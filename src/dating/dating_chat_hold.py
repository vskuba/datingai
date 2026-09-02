"""Постоянное присутствие в чате: держим сокет каждой учётки, а не подключаемся на минуту.

Зачем это отдельная работа. Сообщение сайт отдаёт **только тому, кто в этот момент
держит соединение**. Проверено на живых учётках: письмо дошло до сайта, но пока
сокета не было, оно легло в раздел «Missed chat» кабинета, а очередь чата осталась
пустой (`notSendMessages: []`). Пропущенное в чат не возвращается — значит принимать
можно ровно столько, сколько держишь соединение.

HTTP-опрос (`dating_presence`) даёт «на сайте», но не «в чате»: это разные состояния
на стороне сайта, и одно другим не заменяется.

Цена мала: соединение стоит десятки килобайт против ~143 МБ за вкладку Chromium,
поэтому двести учёток укладываются в единицы мегабайт и держатся одним процессом.

**Список учёток перечитывается на ходу.** Держатель живёт часами, а анкеты заводят и
удаляют в это же время; без пересмотра новая учётка ждала бы конца смены. Поэтому
раз в `DATING_CHAT_HOLD_RESCAN_SEC` список сверяется с базой: появившиеся берутся в
работу, исчезнувшие отпускаются.

Обрыв — обычное дело, а не ошибка: сайт рвёт соединение сам, токены в адресе
протухают после нового входа. Поэтому переподключение с нарастающей паузой, а адрес
пересниматься браузером — но не чаще, чем раз в `DATING_CHAT_HOLD_RECAPTURE_SEC`:
каждый перехват это открытая вкладка, и частить ими дороже, чем подождать.
"""

import asyncio
import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.dating.dating_auto import DATING_AUTO_PERIOD_SEC, dating_auto_round
from src.dating.dating_chat_panel import (DATING_CHAT_PANEL_ROUND_DAYS,
                                          dating_chat_panel_import)
from src.dating.dating_ready import (DATING_READY_PASSWORD_SETTING, dating_ready_check,
                                     dating_ready_transport, dating_ready_write)
from src.uvicorn.routers.dating.dating_site import (dating_site_seconds,
                                                    dating_site_settings)
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_error_write,
                                       rhapsody_api_site_get)
from src.dating.dating_chat import dating_chat_listen
from src.dating.dating_json import dating_json_load
from src.dating.dating_presence import dating_presence_beat, dating_presence_ready
from src.dating.dating_revive_round import (DATING_REVIVE_PERIOD_SEC,
                                            dating_revive_round_run)
from src.dating.dating_ws import dating_ws_capture

# Как часто сверять список учёток с базой.
DATING_CHAT_HOLD_RESCAN_SEC = 60

# Как часто круг импорта переписки просыпается и смотрит, кому пора.
#
# ⚠ Это **тик круга**, а не частота импорта: та своя у каждого сайта (настройка
# `chat_check_every`, от пяти минут). Тик мельче самой частой настройки, иначе
# «каждые пять минут» превратилось бы в «каждые пять минут плюс тик».
DATING_CHAT_IMPORT_TICK_SEC = 60

# Настройка сайта с частотой импорта. Ключ — в `DATING_SITE_KNOWN`.
DATING_CHAT_IMPORT_SETTING = 'chat_check_every'

# Отметка «у этого сайта только что забирали». Живёт в Redis ровно столько, сколько
# сказано настройкой, — по её истечению круг и понимает, что пора снова.
DATING_CHAT_IMPORT_KEY = 'dating:chat:import:site:{site_id}'

# Пауза после обрыва: от секунд до минуты. Растёт, пока не выйдет соединение —
# сайт, который рвёт связь, не станет добрее от того, что мы стучимся чаще.
DATING_CHAT_HOLD_BACKOFF_MIN = 5
DATING_CHAT_HOLD_BACKOFF_MAX = 60

# Сколько неудач подряд терпим, прежде чем пересниматьадрес сокета браузером.
DATING_CHAT_HOLD_FAIL_LIMIT = 3

# Не чаще этого перехватываем адрес одной учётки: перехват открывает вкладку.
DATING_CHAT_HOLD_RECAPTURE_SEC = 600

# Как часто подтверждать присутствие в чате. Пятнадцать секунд — ритм самой
# страницы сайта; реже сайт считает учётку ушедшей и перестаёт слать ей что-либо.
DATING_CHAT_HOLD_BEAT_SEC = 15

# Сколько heartbeat'ов разом: запрос идёт ~0.6 с, значит двести учёток за 15
# секунд требуют девяти параллельных — берём с запасом.
DATING_CHAT_HOLD_BEAT_PARALLEL = 16

# Сколько ждём, пока отменённые служебные задачи уберут за собой. Уборка у них —
# запросы к соседу (закрыть вкладку Chromium), и им нужно время; но зависший сосед
# не имеет права держать конец смены вечно: за ней стоит очередь со следующей.
DATING_CHAT_HOLD_SHUTDOWN_SEC = 60


async def dating_chat_hold_run(seconds: float) -> dict:
    """Держит сокеты всех готовых учёток заданное время.

    Args:
        seconds: сколько работать. По исходу соединения закрываются — смену
            перезапустит очередь, и код обновится вместе с ней.

    Returns:
        dict: `profiles` — сколько учёток держали, `saved` — сколько сообщений
        записали, `sec` — сколько отработали.
    """
    deadline = time.monotonic() + float(seconds)
    holders: dict[int, dict] = {}
    counters: dict[int, int] = {}

    logger_info(f'[dating] держатель чата запущен на {int(seconds)} с')

    # Присутствие держим отсюда, а не со страницы админки. Сайт считает учётку в
    # чате не по открытому сокету, а по heartbeat: пока его слала только открытая
    # вкладка `/admin/dating`, сокет молчал и умирал через полторы минуты — то
    # есть чат работал ровно пока человек смотрел на экран.
    beat = asyncio.create_task(_presence_loop(deadline))

    # Автоответ — основной путь, а кнопка на странице запасной: анкету заводят,
    # чтобы она разговаривала сама. Отдельной задачей, потому что её ритм свой:
    # раз в минуту, по ограничению сайта, и он не должен тормозить приём.
    answer = asyncio.create_task(_answer_loop(deadline))

    # Вход на сайт живёт около суток, и до сих пор его протухание не замечал никто:
    # держатель шлёт голый heartbeat, а тот на мёртвых cookie молча отвечает False.
    revive = asyncio.create_task(_revive_loop(deadline))

    # Входящие из панели агентства. ⚠ **Единственный источник сообщений там, где у
    # сайта нет сокета** (Ladadate): без этого круга они приходили только по кнопке
    # «Забрать сообщения», то есть пока человек смотрит на экран.
    chat = asyncio.create_task(_chat_import_loop(deadline))

    try:
        while time.monotonic() < deadline:
            profiles = await _profiles_ready()
            _holders_sync(holders, counters, profiles, deadline)

            await asyncio.sleep(min(DATING_CHAT_HOLD_RESCAN_SEC,
                                    max(1.0, deadline - time.monotonic())))
    finally:
        beat.cancel()
        answer.cancel()
        revive.cancel()
        chat.cancel()
        for held in holders.values():
            held['task'].cancel()
        # Ждём отмены: без этого соединения закрываются уже после выхода, и в
        # логе появляется хвост из «Task was destroyed but it is pending».
        await asyncio.gather(*(held['task'] for held in holders.values()),
                             return_exceptions=True)
        await _service_wait(beat, answer, revive, chat)

    saved = sum(counters.values())
    logger_info(f'[dating] держатель чата закончил: учёток {len(counters)}, сообщений {saved}')

    return {'profiles': len(counters), 'saved': saved, 'sec': int(seconds)}


async def dating_chat_hold_profile(profile: dict, presence: dict, site_url: str,
                                   deadline: float, counters: dict) -> None:
    """Держит сокет одной учётки до срока, переподключаясь после обрывов.

    Обрыв не считается ошибкой: сайт рвёт связь сам, и единственная разумная
    реакция — подождать и подключиться снова. Ошибкой становится череда неудач,
    и лечится она свежим адресом сокета.
    """
    profile_id = int(profile['id'])
    backoff = DATING_CHAT_HOLD_BACKOFF_MIN
    fails = 0
    captured_at = 0.0

    while time.monotonic() < deadline:
        left = deadline - time.monotonic()
        result = await dating_chat_listen(profile, seconds=left, presence=presence)
        counters[profile_id] = counters.get(profile_id, 0) + int(result.get('saved') or 0)

        if not result.get('error'):
            # Соединение закрылось само и без жалоб — обычный обрыв, начинаем заново.
            fails, backoff = 0, DATING_CHAT_HOLD_BACKOFF_MIN
        else:
            fails += 1
            logger_info(f"[dating] чат #{profile_id}: обрыв ({fails}) — {result['error'][:120]}")

        if fails >= DATING_CHAT_HOLD_FAIL_LIMIT and \
                time.monotonic() - captured_at > DATING_CHAT_HOLD_RECAPTURE_SEC:
            captured_at = time.monotonic()
            fails = 0
            profile = await _profile_row(profile_id) or profile
            url = await dating_ws_capture(profile, presence, site_url)
            # Пустой ответ — неудача перехвата, а не «адреса больше нет». Затереть
            # им прежний значило бы обречь учётку на «нужен вход» до конца смены,
            # даже когда старый адрес ещё работал.
            if url:
                profile = {**profile, 'ws_url': url}
            logger_info(f"[dating] чат #{profile_id}: адрес сокета "
                        f"{'обновлён' if url else 'снять не вышло'}")
            # Череда неудач — это уже не рабочий обрыв, а поломка: она уходит во
            # вкладку «Ошибки» страницы браузера, где её ищут вместе с остальными.
            await _error_write(profile_id, profile.get('login', ''),
                               'чат не поднимается: '
                               + ('адрес сокета переснят' if url else 'адрес сокета снять не вышло'),
                               result.get('error', ''))

        await asyncio.sleep(min(backoff, max(0.0, deadline - time.monotonic())))
        backoff = min(backoff * 2, DATING_CHAT_HOLD_BACKOFF_MAX)


async def _service_wait(*tasks) -> None:
    """Дожидается отменённых служебных задач — так же, как выше дожидаются держатели.

    Отменить мало: у круга восстановления после отмены есть работа. Он ловит
    `CancelledError` посреди входа и закрывает за собой вкладку Chromium (~143 МБ),
    а это HTTP-запрос к соседу. Уйди смена отсюда сразу, `asyncio.run` воркера
    своим `_cancel_all_tasks` отменил бы ту же задачу **второй раз** — ровно посреди
    уборки, — и вкладка пережила бы смену, которая её открыла. Пул общий с соседом,
    и такие вкладки копятся от смены к смене.

    Потолок обязателен: уборка стучится к соседу, а он может и не ответить. Ждать
    его бесконечно значило бы не закончить смену, а за ней стоит следующая.
    """
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True),
                               DATING_CHAT_HOLD_SHUTDOWN_SEC)
    except (asyncio.TimeoutError, TimeoutError):
        # Не дождались — уходим всё равно: смена обязана кончиться. Оставшееся
        # доотменит `asyncio.run`, и хвост в логе честнее запертой очереди.
        logger_info(f'[dating] служебные задачи не убрались за '
                    f'{DATING_CHAT_HOLD_SHUTDOWN_SEC} с — заканчиваю смену')


def _holders_sync(holders: dict, counters: dict, profiles: list, deadline: float) -> None:
    """Приводит набор держателей в соответствие списку учёток из базы.

    Сверяется не только состав, но и адрес сокета: новый вход выдаёт новый адрес,
    а держатель, не заметив этого, продолжал бы висеть на прежнем. Соединение при
    этом выглядит живым — сайт его не рвёт, просто перестаёт слать по нему что-либо.
    """
    fresh = {int(item['profile']['id']): item for item in profiles}

    for profile_id, held in list(holders.items()):
        item = fresh.get(profile_id)
        gone = item is None
        moved = not gone and str(item['profile'].get('ws_url') or '') != held['ws_url']
        if gone or moved or held['task'].done():
            held['task'].cancel()
            holders.pop(profile_id, None)
            if moved:
                logger_info(f'[dating] чат #{profile_id}: адрес сокета сменился, переподключаюсь')

    for profile_id, item in fresh.items():
        if profile_id in holders:
            continue
        holders[profile_id] = {
            'ws_url': str(item['profile'].get('ws_url') or ''),
            'task': asyncio.create_task(
                dating_chat_hold_profile(item['profile'], item['presence'], item['site_url'],
                                         deadline, counters)),
        }
        logger_info(f"[dating] чат #{profile_id} ({item['profile']['login']}): взят в работу")


async def _profiles_ready() -> list[dict]:
    """Учётки, с которыми есть смысл работать, и настройка их сайта.

    ⚠ **Отбор больше не по «снят вход».** Прежде стояло `storage_state IS NOT NULL`
    — верно для сокетного сайта (без входа нет адреса сокета), но у Ladadate сокета
    нет вовсе: там пишут сценарием, который входит сам, каждый раз заново. Из 24
    анкет Ladadate воркер видел **ноль**, и понять это по панели было нельзя.

    Теперь берём всех, кого ведут (`enabled`), а годность считаем по транспорту
    сайта (`dating_ready.py`) и **тут же пишем** в `can_work`: круг всё равно
    обходит эти анкеты, и отдельный обход ради пересчёта был бы вторым проходом по
    той же таблице.

    Условие `enabled` — в SQL, а годность — в Python, и это не небрежность:
    `enabled` ставит человек, он в базе всегда свежий; годность зависит от настройки
    сайта, а та живёт у соседа и читается по сайту, а не по анкете.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, login, site_id, storage_state, ws_url, auto_answer, '
            '       auto_answer_only, enabled, can_work, work_reason, role, profile, '
            '       password <> %s AS has_password '
            '  FROM dating_profile WHERE enabled = 1 ORDER BY id', ('',))
        rows = await db.fetchall()

    # Настройка сайта живёт в RhapsodyAi, и join-ом её больше не взять. Спрашиваем
    # по разу на сайт, а не на анкету: сайтов единицы, анкет сотни, и сотня
    # одинаковых запросов на каждый круг — это минута на пустом месте.
    sites: dict[int, dict] = {}
    # Настройки сайта и общий пароль — по разу на сайт, не на анкету: сайтов
    # единицы, анкет сотни.
    settings: dict[int, dict] = {}
    site_passwords: dict[int, str] = {}
    ready = []
    for row in rows or []:
        site_id = int(row['site_id'])
        if site_id not in sites:
            try:
                sites[site_id] = await rhapsody_api_site_get(site_id)
            except RhapsodyError as e:
                logger_info(f'[dating] сайт #{site_id} не спросился: {e}')
                sites[site_id] = {}

            settings[site_id] = await dating_site_settings(site_id)
            site_passwords[site_id] = str(
                (settings[site_id] or {}).get(DATING_READY_PASSWORD_SETTING) or '')

        site = sites[site_id]
        presence = dating_json_load(site.get('presence'))

        # Готовность считаем и записываем всегда — даже для той анкеты, которую
        # дальше не возьмём. ⚠ Иначе причина «нет пароля» не появилась бы в панели
        # никогда: анкета молча выпадала бы из отбора, как это и было с Ladadate.
        state = dating_ready_check(
            {**row, 'password': '1' if row.get('has_password') else ''},
            dating_ready_transport(presence, settings.get(site_id) or {}),
            site_passwords.get(site_id, ''))
        await dating_ready_write(int(row['id']), state)

        if not state['can']:
            continue

        # Опрос присутствия — условие только для сокетного пути: у сценарного сайта
        # его может не быть вовсе, а работать с анкетой это не мешает.
        if not dating_presence_ready(presence) and row.get('storage_state'):
            continue

        # ⚠ Выключенные анкеты **остаются в списке**, и это не недосмотр: держать
        # их сокет надо — входящие принимаются и у выключенной (`dating.md`, §5.10).
        # Отсеивает их круг автоответа, а не этот отбор.
        profile = {'id': row['id'], 'login': row['login'], 'site_id': row['site_id'],
                   'storage_state': row['storage_state'], 'ws_url': row['ws_url'],
                   'auto_answer': row['auto_answer'],
                   'auto_answer_only': row['auto_answer_only'],
                   'enabled': row['enabled'], 'role': row['role'],
                   'profile': row['profile']}
        ready.append({'profile': profile, 'presence': presence, 'site_url': site.get('url', '')})

    return ready


async def _presence_loop(deadline: float) -> None:
    """Держит «я в чате» у всех учёток, пока идёт смена.

    Один запрос на учётку, а не полный опрос: двести анкет раз в 15 секунд — это
    13 запросов в секунду вместо сорока. Статус и счётчики обновляет страница,
    когда её открывают; здесь важен только сам факт присутствия.
    """
    limit = asyncio.Semaphore(DATING_CHAT_HOLD_BEAT_PARALLEL)

    async def beat(item):
        async with limit:
            await dating_presence_beat(item['profile'], item['presence'])

    while time.monotonic() < deadline:
        try:
            profiles = await _profiles_ready()
            if profiles:
                await asyncio.gather(*(beat(item) for item in profiles), return_exceptions=True)
        except Exception as e:
            logger_info(f'[dating] присутствие: {e}')

        await asyncio.sleep(min(DATING_CHAT_HOLD_BEAT_SEC,
                                max(1.0, deadline - time.monotonic())))


async def _answer_loop(deadline: float) -> None:
    """Отвечает за анкеты, у которых включён автоответ.

    Список перечитывается каждый круг: анкеты подключают и отключают на ходу, а
    флаг автоответа переключают прямо в карточке — читать его один раз значило бы
    узнать о выключении только к концу смены.
    """
    while time.monotonic() < deadline:
        await asyncio.sleep(min(DATING_AUTO_PERIOD_SEC,
                                max(1.0, deadline - time.monotonic())))
        try:
            profiles = await _profiles_ready()
            if profiles:
                await dating_auto_round(profiles)
        except Exception as e:
            logger_info(f'[dating] круг автоответа: {e}')


async def _chat_import_loop(deadline: float) -> None:
    """Забирает входящие из панели агентства — по каждому сайту в свой срок.

    ⚠ **Частота своя у каждого сайта** (настройка `chat_check_every`): прогон стоит
    сорока секунд живого браузера и вкладки в общем пуле соседа, и одному сайту
    хватает часа, а другому нужны пять минут.

    ⚠ **Когда ходили — помним в Redis, а не в памяти процесса.** Смена держателя
    перезапускается (свой срок, перезапуск воркера, деплой), и память процесса
    сбрасывалась бы вместе с ней: после каждого перезапуска импорт шёл бы заново,
    независимо от настройки. На живой установке это десятки лишних прогонов в день.

    Сайты обходятся **по одному, последовательно**: каждый прогон открывает вкладку
    Chromium у соседа, и пять сайтов разом уронили бы его пул. Тот же порядок, что
    у круга восстановления входов, и по той же причине.
    """
    while time.monotonic() < deadline:
        await asyncio.sleep(min(DATING_CHAT_IMPORT_TICK_SEC,
                                max(1.0, deadline - time.monotonic())))
        try:
            await _chat_import_round()
        except Exception as e:
            logger_info(f'[dating] круг импорта переписки: {e}')


async def _chat_import_round() -> None:
    """Один обход сайтов: у кого срок вышел — забираем."""
    for site_id in await _chat_sites():
        every = await dating_site_seconds(site_id, DATING_CHAT_IMPORT_SETTING)
        if not every:
            continue

        if not _chat_due(site_id, int(every)):
            continue

        # Отметку ставим **до** прогона, а не после: он длится минуту, и следующий
        # тик круга завёл бы второй, пока идёт первый. Тот же приём, что у сторожа
        # фоновых обновлений списков (`dating_people.py`).
        _chat_mark(site_id, int(every))

        result = await dating_chat_panel_import(site_id, DATING_CHAT_PANEL_ROUND_DAYS)

        if result['status'] == 'error':
            logger_info(f"[dating] переписка сайта #{site_id} не забралась: "
                        f"{result['error']}")

            continue

        # В лог — только когда что-то привезли: круг идёт каждые пять минут по
        # каждому сайту, и «привезено 0» забило бы лог до бесполезности.
        if result['added']:
            logger_info(f"[dating] переписка сайта #{site_id}: "
                        f"новых {result['added']}, собеседников {result['contacts']}")


async def _chat_sites() -> list[int]:
    """Сайты, у которых есть наши анкеты. По ним и ходим — у прочих забирать нечего.

    ⚠ Отбор по анкетам, а не по списку сайтов: снимок `dating_site` держит и те, что
    завели «на будущее», и ушедшие (`is_gone`). Гонять браузер по ним значило бы
    платить сорока секундами за пустой ответ.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT DISTINCT site_id FROM dating_profile '
                             ' WHERE site_id > 0 ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для импорта переписки не спросились: {e}')

        return []

    return [int(row['site_id']) for row in rows or []]


def _chat_due(site_id: int, every: int) -> bool:
    """Пора ли забирать у этого сайта. Сбой Redis — идём: пропустить хуже.

    Пропущенный круг означает, что сообщение полежит до следующего; лишний прогон
    стоит сорока секунд. Оба исхода терпимы, но первый человек замечает («она не
    отвечает»), а второй — нет.
    """
    try:
        return not redis_conn_get().get(DATING_CHAT_IMPORT_KEY.format(site_id=site_id))
    except Exception as e:
        logger_info(f'[dating] отметка импорта не прочиталась: {e}')

        return True


def _chat_mark(site_id: int, every: int) -> None:
    """Отмечает, что у сайта только что забирали.

    Отметкой с истечением, а не временем в значении: срок и есть настройка, и Redis
    сам погасит ключ, когда придёт пора. Считать разницу дат было бы тем же самым,
    только руками — и с риском разойтись, когда настройку поменяют.
    """
    try:
        redis_conn_get().setex(DATING_CHAT_IMPORT_KEY.format(site_id=site_id),
                               max(1, int(every)), '1')
    except Exception as e:
        logger_info(f'[dating] отметка импорта не записалась: {e}')


async def _revive_loop(deadline: float) -> None:
    """Держит вход анкет живым, пока идёт смена.

    Вход на сайт живёт около суток, и его протухание раньше не замечал никто:
    полный опрос делала только открытая страница панели, а держатель шлёт голый
    heartbeat, который на мёртвых cookie молча возвращает False.

    Входы идут по одному: круг обходит анкеты последовательно после того, как
    кончились опросы, и каждый вход дожидается предыдущего. Отдельного семафора не
    нужно — каждый вход открывает вкладку Chromium у соседа, и десяток разом уронил
    бы пул, а последовательный обход этого просто не допускает. Опросы, наоборот,
    идут разом: они по HTTP и стоят единицы килобайт, а подряд не уложились бы в
    минуту круга. Смена в установке одна (single-flight), так что второго круга
    параллельно не бывает.
    """
    while time.monotonic() < deadline:
        await asyncio.sleep(min(DATING_REVIVE_PERIOD_SEC,
                                max(1.0, deadline - time.monotonic())))
        try:
            await dating_revive_round_run()
        except Exception as e:
            logger_info(f'[dating] круг восстановления: {e}')


async def _error_write(profile_id: int, login: str, what: str, detail: str) -> None:
    """Поломка чата — в общий журнал ошибок браузера.

    Импорт внутри функции: журнал живёт в роутерах, а этот модуль зовут и из
    воркера — тянуть половину FastAPI при загрузке модуля незачем.
    """

    await rhapsody_api_error_write('dating', f'{what}: {detail}'.strip(': '),
                              context={'profile_id': profile_id, 'login': login, 'where': 'держатель чата'})


async def _profile_row(profile_id: int) -> dict | None:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, login, site_id, storage_state, ws_url '
                         '  FROM dating_profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    return dict(row) if row else None
