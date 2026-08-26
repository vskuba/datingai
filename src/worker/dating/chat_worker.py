"""
Воркер чата знакомств: держит вебсокеты всех учёток, чтобы сообщения приходили живыми.

В сообщении очереди нет ничего — задание одно на всю установку. Это не «работа над
объектом», как у конвейера задач, а смена: один job поднимает сокеты всех готовых
учёток, держит их `CHAT_WORKER_HOLD_SEC` и завершается. Список учёток job перечитывает
на ходу (`dating_chat_hold`), поэтому заведённая в середине смены анкета берётся в
работу через минуту, а не в следующую смену.

Кто ставит: восстановление при старте uvicorn (`chat_worker_recover`) и сама смена
в конце своей работы. Оба зовут `chat_worker_enqueue()` вслепую — от дублей защищает
single-flight, и лишний вызов просто ничего не делает.

Очередь одна (`dating`) и других в проекте нет: работа здесь одна — держать сокеты.

Зачем смена конечна, если работа бесконечна. Перезапуск раз в несколько часов — это
обновление кода без ручного рестарта, сброс накопленных соединений и естественная
точка, где умерший воркер заменяется живым. Бесконечный job не даёт ни одного из трёх.

Общие правила — `.claude/docs/worker.md`, предметная часть — `.claude/docs/dating.md`.
"""
import asyncio

from logging_.logging_ import logger_info, logging_init
from mysql_.mysql_ import mysql_pool_close
from redis_.redis_ import redis_conn_get
from redis_queue.redis_queue import redis_queue_get

from src.dating.dating_chat_hold import dating_chat_hold_run

CHAT_WORKER_QUEUE_NAME = 'dating'

# Длина смены. Меньше часа — и перехваты адресов сокетов начинают стоить дороже
# самой работы; больше суток — и правка кода не доезжает до живых соединений.
CHAT_WORKER_HOLD_SEC = 6 * 3600

# Потолок job'а больше смены: рвать работу должен собственный срок держателя, а не
# rq снаружи. Дефолт rq (180s) сюда не годится на два порядка.
CHAT_WORKER_JOB_TIMEOUT = CHAT_WORKER_HOLD_SEC + 600
CHAT_WORKER_RESULT_TTL = 3600

# Ключ смены живёт минуты и продлевается изнутри job'а, а не выставляется на всю
# смену. Иначе жёстко убитый воркер (SIGKILL, падение контейнера) запирал бы чат на
# шесть часов: `finally` в таком случае не выполняется, и снять ключ некому.
CHAT_WORKER_FLIGHT_TTL = 150
CHAT_WORKER_FLIGHT_BEAT = 60

_FLIGHT_KEY = 'chat_worker:flight'


def chat_worker_enqueue() -> str | None:
    """
    Поставить смену держателя чата в очередь. None — смена уже идёт.

    Безопасно звать откуда угодно и как угодно часто: ключ single-flight делает
    повторный вызов бездействием, а не вторым держателем на те же сокеты. Два
    держателя одной учётки — это два соединения, и сайт вышибает оба.
    """
    if not chat_worker_flight_acquire():
        return None
    try:
        job = redis_queue_get(CHAT_WORKER_QUEUE_NAME).enqueue(
            chat_worker_job_run,
            job_timeout=CHAT_WORKER_JOB_TIMEOUT,
            result_ttl=CHAT_WORKER_RESULT_TTL,
            failure_ttl=CHAT_WORKER_RESULT_TTL,
        )
    except Exception:
        # Ключ занят, а задания нет — чат заперся бы до истечения TTL.
        chat_worker_flight_release()
        raise

    logger_info(f'chat_worker: смена поставлена в очередь [{job.id[:8]}]')

    return job.id


def chat_worker_flight_acquire() -> bool:
    """Занять single-flight смены. False — держатель уже работает или ждёт очереди.

    Срок ключа короткий: пока смена идёт, она его продлевает сама. Умерший воркер
    перестаёт продлевать, и место освобождается через минуты, а не через смену.
    """
    return bool(redis_conn_get().set(_FLIGHT_KEY, '1', nx=True, ex=CHAT_WORKER_FLIGHT_TTL))


def chat_worker_flight_release() -> None:
    """Освободить single-flight. Повторный вызов безвреден."""
    redis_conn_get().delete(_FLIGHT_KEY)


def chat_worker_flight_busy() -> bool:
    """Идёт ли смена прямо сейчас — по ней видно, нужен ли новый job."""
    return bool(redis_conn_get().exists(_FLIGHT_KEY))


def chat_worker_job_run() -> str:
    """
    Точка входа rq: одна смена держателя чата.

    Синхронная намеренно — rq зовёт job как обычную функцию, свой event loop
    поднимаем внутри. Функция обязана быть импортируемой по полному имени модуля:
    воркер живёт в отдельном контейнере и восстанавливает её по ссылке из Redis.
    """
    return asyncio.run(_job_async())


async def chat_worker_recover() -> None:
    """
    Восстановление при старте uvicorn. Звать один раз из lifespan.

    Смена переживает рестарт uvicorn — она идёт в своём контейнере, и трогать её
    нельзя. Поэтому здесь только постановка: занятый ключ означает, что держатель
    уже работает, и вызов ничего не делает.
    """
    try:
        if chat_worker_enqueue():
            logger_info('chat_worker: смена поднята при старте')
    except Exception as e:
        # Недоступный Redis не должен мешать старту сервера: чат — не его условие.
        logger_info(f'chat_worker: смену поставить не вышло: {e}')


async def _job_async() -> str:
    """
    Своя мини-инфраструктура: отдельный процесс rq не получает lifespan uvicorn.

    Движок агентов здесь не нужен — держатель не ходит к LLM, он читает сокеты и
    пишет в базу. Пул MySQL закрываем сами: процесс свой, и утечка соединений на
    каждой смене за сутки упёрлась бы в лимит сервера.
    """
    logging_init()
    beat = asyncio.create_task(_flight_beat())
    try:
        result = await dating_chat_hold_run(CHAT_WORKER_HOLD_SEC)
        done = f"смена окончена: учёток {result['profiles']}, сообщений {result['saved']}"
    finally:
        beat.cancel()
        chat_worker_flight_release()
        await mysql_pool_close()

    # Смена сама ставит следующую: между ними иначе зияет дыра до ближайшего тика,
    # а дыра — это пропущенные сообщения. Тик Cronicle остаётся страховкой на
    # случай, когда смена не дошла до этой строки (падение, убитый контейнер).
    chat_worker_enqueue()

    return done


async def _flight_beat() -> None:
    """Продлевает ключ смены, пока job жив.

    Это и есть признак жизни: перестал продлевать — значит воркера нет, и тик
    Cronicle поднимет смену заново. Сбой Redis не роняет работу — сокеты держатся
    и без ключа, а повторная постановка смены хуже, чем её отсутствие, не будет.
    """
    while True:
        try:
            redis_conn_get().set(_FLIGHT_KEY, '1', ex=CHAT_WORKER_FLIGHT_TTL)
        except Exception as e:
            logger_info(f'chat_worker: ключ смены не продлился: {e}')
        await asyncio.sleep(CHAT_WORKER_FLIGHT_BEAT)
