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

from logging_.logging_ import logger_exception, logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.dating.dating_auto import DATING_AUTO_PERIOD_SEC, dating_auto_round
from src.dating.dating_feed import dating_feed_done, dating_feed_write
from src.dating.dating_round import dating_round_loop, dating_round_offset_loop
from src.dating.dating_favorite_mail_round import (
    DATING_FAVORITE_MAIL_ROUND_TICK_SEC, dating_favorite_mail_round_run)
from src.dating.dating_panel_round import (DATING_PANEL_ROUND_TICK_SEC,
                                           dating_panel_round_run)
from src.dating.dating_chat_panel import (DATING_CHAT_PANEL_ROUND_DAYS,
                                          dating_chat_panel_import)
from src.dating.dating_ready import (DATING_READY_PASSWORD_SETTING, dating_ready_check,
                                     dating_ready_transport, dating_ready_write)
from src.dating.dating_mail_round import (DATING_MAIL_ROUND_TICK_SEC,
                                          dating_mail_round_run)
from src.dating.dating_mailing_round import (DATING_MAILING_ROUND_TICK_SEC,
                                             dating_mailing_round_run)
from src.dating.dating_heartbeat_round import (DATING_HEARTBEAT_ROUND_TICK_SEC,
                                               dating_heartbeat_round_run)
from src.dating.dating_online_round import (DATING_ONLINE_ROUND_TICK_SEC,
                                            dating_online_round_run)
from src.dating.dating_mail_in_round import (DATING_MAIL_IN_ROUND_TICK_SEC,
                                             dating_mail_in_round_run)
from src.dating.dating_favorite_round import (DATING_FAVORITE_ROUND_TICK_SEC,
                                              dating_favorite_round_run)
from src.dating.dating_invite_round import (DATING_INVITE_ROUND_TICK_SEC,
                                            dating_invite_round_run)
from src.dating.dating_invite_send_round import (DATING_INVITE_SEND_ROUND_TICK_SEC,
                                                 dating_invite_send_round_run)
from src.dating.dating_proxy_round import (DATING_PROXY_ROUND_TICK_SEC,
                                           dating_proxy_round_run)
from src.dating.dating_request_round import (DATING_REQUEST_ROUND_TICK_SEC,
                                             dating_request_round_run)
from src.dating.dating_status_round import (DATING_STATUS_ROUND_TICK_SEC,
                                            dating_status_round_run)
from src.dating.dating_reply_round import (DATING_REPLY_ROUND_TICK_SEC,
                                           dating_reply_round_run)
from src.dating.dating_send_round import (DATING_SEND_ROUND_TICK_SEC,
                                          dating_send_round_run)
from src.dating.dating_schedule_round import (DATING_SCHEDULE_ROUND_TICK_SEC,
                                              dating_schedule_round_run)
from src.dating.dating_socket import dating_socket_write
from src.uvicorn.routers.dating.dating_site import (dating_site_seconds,
                                                    dating_site_settings)
from src.rhapsody.rhapsody_api import rhapsody_api_error_write
from src.dating.dating_site_presence import dating_site_presence
from src.dating.dating_chat import dating_chat_listen
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

    # Расписание: вход и выход по сменам. ⚠ Своей задачей, а не внутри круга
    # автоответа: вход идёт минутами живого браузера, и ответы не должны его ждать.
    sched = asyncio.create_task(_schedule_loop(deadline))

    # Входящие из панели агентства. ⚠ **Единственный источник сообщений там, где у
    # сайта нет сокета** (Ladadate): без этого круга они приходили только по кнопке
    # «Забрать сообщения», то есть пока человек смотрит на экран.
    chat = asyncio.create_task(_chat_import_loop(deadline))

    # Рассылка: пул набирается сам по расписанию сайта. ⚠ **Готовит, но не
    # отправляет** — отправка это отдельный выключатель, и включённое расписание
    # согласием на неё не является (`dating_mailing_round.py`).
    mailing = asyncio.create_task(_mailing_loop(deadline))

    # Лента «Все онлайн». ⚠ Прежде её обновляла **только открытая страница**: закрыл
    # вкладку — и снимок стареет. От него зависят рассылка, точка «в сети» у
    # собеседника и решение «писать ли сейчас», и все трое молча теряли опору.
    online = asyncio.create_task(_online_loop(deadline))

    # Шаблоны писем. ⚠ Заготовки пишет агент, и без круга их писали бы, когда
    # вспомнят: через неделю у сайта те же пять текстов, которыми он писал в
    # понедельник, — а один и тот же текст на сотню людей читается как рассылка
    # робота, чем он и является (`dating_mail_round.py`).
    tmpl = asyncio.create_task(_mail_round_loop(deadline))

    # Присутствие на сайте. ⚠ **Не то же, что `beat` выше, и это разные вещи.**
    # Тот держит «в чате» у сайтов с сокетом и ходит по анкетам держателя; этот
    # держит «в сети» по расписанию и работает там, где сокета нет вовсе
    # (`dating_heartbeat_round.py`). Пока присутствие было только внутри `beat`,
    # сайт без сокета не мог показать анкету онлайн в принципе.
    live = asyncio.create_task(_heartbeat_loop(deadline))

    # Отправка писем и ответов. ⚠ **Самое заметное наружу действие панели**: письмо
    # уходит живому человеку и отменить его нельзя. Поэтому за двумя выключателями
    # (`mailing_send_on`, `reply_send_on`), по одному письму с паузой и с потолком
    # в сутки (`dating_send_round.py`).
    post = asyncio.create_task(_send_loop(deadline))

    # Забор входящих писем. ⚠ **Первый в цепочке почты**, и до него письма
    # приносила только кнопка — по одной анкете за нажатие. Круги ответов и
    # отправки при этом работали сами и молча простаивали: отвечать было не на
    # что, потому что за письмами никто не сходил (`dating_mail_in_round.py`).
    letters = asyncio.create_task(_mail_in_loop(deadline))

    # Набор ответов. ⚠ До него ответы писала **только кнопка**: выключатель
    # «генерация ответов» решал, чьи письма брать, но запускал набор человек — и
    # без нажатия пул стоял пустым, сколько бы писем ни пришло
    # (`dating_reply_round.py`).
    answers = asyncio.create_task(_reply_loop(deadline))

    # Звёздочки написавшим. ⚠ Своим кругом, а не внутри забора писем: забор
    # дешёвый (HTTP, секунды на все анкеты), а звёздочка стоит входа браузером и
    # ~40 секунд на человека. Повесь её на приход письма — и опоздали бы сами
    # письма, то есть первое звено цепочки (`dating_favorite_round.py`).
    stars = asyncio.create_task(_favorite_loop(deadline))

    # Пул инвайтов. ⚠ **Готовит и не отправляет** — как рассылка, и по той же
    # причине: «набрать» и «послать наружу» это два решения и два выключателя.
    invites = asyncio.create_task(_invite_loop(deadline))

    # Отправка инвайтов. ⚠ Своим кругом, а не внутри почтового: там письма, здесь
    # чат — другой транспорт, другая свежесть (реплика зовёт того, кто **сейчас** в
    # чате) и свои пауза с потолком (`dating_invite_send_round.py`).
    invites_out = asyncio.create_task(_invite_send_loop(deadline))

    # Опрос: жив ли вход у анкеты. ⚠ **Не присутствие и не восстановление.** Тот
    # бьёт, чтобы держать в сети, второй чинит упавший вход; этот только
    # спрашивает и записывает — иначе колонка «В сети» показывает то, что записала
    # последняя открытая страница, и у анкеты без смен это может быть неделя
    # назад (`dating_status_round.py`).
    asking = asyncio.create_task(_status_loop(deadline))

    # Переписка с избранными: письма тем, кто отвечал, но замолчал. ⚠ **Готовит и
    # не отправляет**, как рассылка; уносит письма общий круг отправки, в часы
    # сайта. Без этого круга цепочка почты обрывалась на молчании собеседника:
    # всё, что панель умела, было реакцией на его письмо
    # (`dating_favorite_mail_round.py`).
    # ⚠ Имя своё, не `revive`: так уже зовут задачу восстановления входа, и
    # второе присваивание оставило бы ту без отмены — она пережила бы смену
    # держателя, продолжая ходить на сайт от имени законченного процесса.
    revive_talk = asyncio.create_task(_favorite_mail_loop(deadline))

    # Админ-панель агентства: сессия сайта держится живой круглые сутки. ⚠ Круг
    # **не про анкеты** — учётка у панели одна на сайт, и расписания у неё нет:
    # панель нужна живой и в три часа ночи, когда придёт круг импорта переписки.
    panel = asyncio.create_task(_panel_loop(deadline))

    # Проверка выходов: прокси умирают молча, и узнать об этом до того, как анкета
    # не сможет войти, можно только спрашивая (`dating_proxy_round.py`). ⚠ Круг
    # один на установку, срок берёт наименьший из настроенных сайтами: выходы
    # общие, и два расписания означали бы двойной стук к провайдеру без пользы.
    proxies = asyncio.create_task(_proxy_loop(deadline))

    # Чистка журнала запросов: он растёт быстрее всего в базе — около 52 тысяч
    # строк в сутки, — и без уборки за месяц перерос бы всё остальное вместе
    # взятое (`dating_request_round.py`).
    swept = asyncio.create_task(_request_loop(deadline))

    try:
        while time.monotonic() < deadline:
            profiles = await _profiles_ready()
            _holders_sync(holders, counters, profiles, deadline)
            # Снимок для страницы «Сокеты»: держатели живут в памяти этого
            # процесса, а панель — отдельный, и другого канала между ними нет.
            dating_socket_write(holders, counters, deadline - time.monotonic())

            await asyncio.sleep(min(DATING_CHAT_HOLD_RESCAN_SEC,
                                    max(1.0, deadline - time.monotonic())))
    finally:
        beat.cancel()
        answer.cancel()
        revive.cancel()
        chat.cancel()
        sched.cancel()
        mailing.cancel()
        online.cancel()
        tmpl.cancel()
        live.cancel()
        post.cancel()
        answers.cancel()
        letters.cancel()
        stars.cancel()
        revive_talk.cancel()
        invites.cancel()
        invites_out.cancel()
        asking.cancel()
        panel.cancel()
        proxies.cancel()
        swept.cancel()
        for held in holders.values():
            held['task'].cancel()
        # Ждём отмены: без этого соединения закрываются уже после выхода, и в
        # логе появляется хвост из «Task was destroyed but it is pending».
        await asyncio.gather(*(held['task'] for held in holders.values()),
                             return_exceptions=True)
        # ⚠ Ждать надо **все** отменённые задачи, а не часть: не дождавшаяся
        # закрывается уже после выхода, и в логе остаётся хвост из «Task was
        # destroyed but it is pending». `revive_talk` сюда прежде не попадал.
        await _service_wait(beat, answer, revive, chat, sched, mailing, online,
                            tmpl, live, post, answers, letters, stars, invites,
                            revive_talk, panel)

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

    ⚠ **Держим только тех, у кого есть адрес сокета.** «Готова к работе» и «есть что
    держать» — разные вещи, и с появлением сценарного транспорта они разошлись:
    анкета Ladadate готова работать (сценарий войдёт сам), но сокета у её сайта нет
    вовсе, и держать нечего.

    Поймано по логу: отбор стал отдавать все готовые анкеты, держатель честно полез
    подключаться к каждой и выдал **264 жалобы «адрес сокета не снят» за десять
    минут**. Работе это не мешало, но такой лог нельзя читать — а читают его именно
    тогда, когда что-то сломалось.
    """
    fresh = {int(item['profile']['id']): item
             for item in profiles
             if str(item['profile'].get('ws_url') or '').strip()}

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
            # Логин кладём сюда же: снимок читает страница, а лезть за именем в
            # базу по каждой строке значило бы сотню запросов на один показ.
            'login': str(item['profile'].get('login') or ''),
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
        # ⚠ Адрес сайта — **левым** join-ом к снимку (`dating_site`), а не
        # обычным: строка снимка появляется сверкой с соседом, и анкета сайта,
        # который ещё не сверяли, иначе выпала бы из работы целиком. Прежде
        # адрес брался из карточки соседа, и для этого её и спрашивали.
        await db.execute(
            'SELECT p.id, p.login, p.site_id, p.storage_state, p.ws_url, '
            '       p.auto_answer, p.auto_answer_only, p.enabled, p.can_work, '
            '       p.work_reason, p.role, p.profile, '
            '       p.password <> %s AS has_password, '
            '       COALESCE(s.url, %s) AS site_url '
            '  FROM dating_profile p '
            '  LEFT JOIN dating_site s ON s.site_id = p.site_id '
            ' WHERE p.enabled = 1 ORDER BY p.id', ('', ''))
        rows = await db.fetchall()

    # Описание сайта — по разу на сайт, а не на анкету: сайтов единицы, анкет
    # сотни, и сотня одинаковых чтений на каждый круг была бы работой впустую.
    sites: dict[int, dict] = {}
    # Настройки сайта и общий пароль — по разу на сайт, не на анкету: сайтов
    # единицы, анкет сотни.
    settings: dict[int, dict] = {}
    site_passwords: dict[int, str] = {}
    ready = []
    for row in rows or []:
        site_id = int(row['site_id'])
        if site_id not in sites:
            # ⚠⚠ **Описание берём у себя, а не у соседа.** Этот отбор зовут три
            # петли держателя — присутствие раз в 15 секунд, главный цикл и
            # автоответ раз в минуту, — и каждая спрашивала карточку сайта
            # ручкой `GET /api/browser/site/{id}`. По журналу прода: 412
            # обращений в час на сайт ради трёхсот байт, не менявшихся пять дней.
            #
            # Хуже цены зависимость: сосед лежит — встаёт чат, хотя отправка
            # идёт нашей ручкой по нашим cookie и браузера не касается вовсе.
            sites[site_id] = await dating_site_presence(site_id)
            settings[site_id] = await dating_site_settings(site_id)
            site_passwords[site_id] = str(
                (settings[site_id] or {}).get(DATING_READY_PASSWORD_SETTING) or '')

        presence = sites[site_id]

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
        #
        # ⚠ Спрашиваем **и свою настройку сайта** (`site_id`), а не только описание
        # соседа: сайт, присутствие которого настроено у нас, иначе выпадал бы из
        # держателя — при том что в сети он держится исправно.
        if row.get('storage_state') and not await dating_presence_ready(
                presence, int(row['site_id'])):
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
        ready.append({'profile': profile, 'presence': presence,
                      # ⚠ Адрес — из нашего снимка списка сайтов, а не из карточки
                      # соседа: снимок и заведён затем, чтобы раздел не зависел от
                      # него в мелочах (`api.md`, §3.5). Пустой снимок — не беда:
                      # `base` описания говорит то же самое.
                      'site_url': str(row.get('site_url')
                                      or presence.get('base') or '')})

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
            # Своя обёртка, не `dating_round_loop`: присутствие спит **после**
            # работы — оно нужно с первой секунды смены, а не через тик.
            logger_exception(f'[dating] присутствие: {e}', exc=e)

        await asyncio.sleep(min(DATING_CHAT_HOLD_BEAT_SEC,
                                max(1.0, deadline - time.monotonic())))


async def _answer_loop(deadline: float) -> None:
    """Отвечает за анкеты, у которых включён автоответ.

    Список перечитывается каждый круг: анкеты подключают и отключают на ходу, а
    флаг автоответа переключают прямо в карточке — читать его один раз значило бы
    узнать о выключении только к концу смены.
    """

    async def body() -> None:
        profiles = await _profiles_ready()
        if profiles:
            await dating_auto_round(profiles)

    await dating_round_loop(deadline, DATING_AUTO_PERIOD_SEC, 'автоответа', body)


async def _schedule_loop(deadline: float) -> None:
    """Водит анкеты по расписанию, пока идёт смена.

    Тик — минута: смены заданы с точностью до четверти часа, чаще смотреть нечего,
    а реже значило бы входить с опозданием до пяти минут.
    """
    await dating_round_offset_loop(deadline, DATING_SCHEDULE_ROUND_TICK_SEC,
                                   'расписания', dating_schedule_round_run)


async def _mail_in_loop(deadline: float) -> None:
    """Забирает входящие письма, пока идёт смена.

    ⚠ **Расписание не спрашивается.** Письмо приходит, когда его написали, а не
    когда у девушки смена; отвечать на него всё равно будут по часам отправки.
    """
    await dating_round_loop(deadline, DATING_MAIL_IN_ROUND_TICK_SEC,
                            'писем', dating_mail_in_round_run)


async def _invite_loop(deadline: float) -> None:
    """Набирает пул инвайтов по расписанию сайта, пока идёт смена.

    Тик — минута, как у рассылки: смены заданы с точностью до четверти часа. Сам
    набор идёт куда реже — у каждого сайта свой срок (`invite_every`), и
    большинство тиков внутри смены только читают отметку.
    """
    await dating_round_offset_loop(deadline, DATING_INVITE_ROUND_TICK_SEC,
                                   'инвайтов', dating_invite_round_run)


async def _invite_send_loop(deadline: float) -> None:
    """Круг отправки инвайтов: реплики уходят в чат сами, по одной.

    Тик — пятнадцать секунд, а шлёт круг по своему сроку (`invite_send_every`,
    обычно минута): большинство тиков только смотрят, не пора ли.

    ⚠ Смещение пояса нужно: смены отправки те же, что у набора
    (`invite_schedule`), и заданы они местным временем.
    """
    await dating_round_offset_loop(deadline, DATING_INVITE_SEND_ROUND_TICK_SEC,
                                   'отправки инвайтов', dating_invite_send_round_run)


async def _status_loop(deadline: float) -> None:
    """Круг опроса: колонка «В сети» свежая без открытой страницы.

    Тик — пятнадцать секунд, а спрашивает круг по сроку сайта (`probe_every`,
    обычно минута): большинство тиков только смотрят, кому пора.

    ⚠ Расписания **не спрашивает**, поэтому и смещения пояса ему не нужно: «жив
    ли вход» — вопрос круглосуточный, по нему смотрят и ночью. Тот же довод, что
    у круга админ-панели.
    """
    await dating_round_loop(deadline, DATING_STATUS_ROUND_TICK_SEC,
                            'опроса', dating_status_round_run)


async def _proxy_loop(deadline: float) -> None:
    """Просит реестр проверять выходы, пока идёт смена.

    Тик — минута, а сам заход идёт по сроку из настроек сайтов (обычно десять
    минут): большинство тиков только смотрят, не пора ли.

    ⚠ Расписания анкет **не спрашивает**: выход нужен живым и ночью — утром по
    нему пойдёт первая же смена, и узнать, что канал умер, лучше заранее.
    """
    await dating_round_loop(deadline, DATING_PROXY_ROUND_TICK_SEC,
                            'проверки выходов', dating_proxy_round_run)


async def _request_loop(deadline: float) -> None:
    """Убирает старые записи журнала запросов, пока идёт смена.

    Тик — минута, сама чистка раз в час: таблица терпит, а блокировка на
    миллионе строк — нет.

    ⚠ Расписания **не спрашивает**: мусор копится круглосуточно, и ночью быстрее
    всего — присутствие бьёт и в три часа.
    """
    await dating_round_loop(deadline, DATING_REQUEST_ROUND_TICK_SEC,
                            'чистки журнала запросов', dating_request_round_run)


async def _favorite_mail_loop(deadline: float) -> None:
    """Набирает пул писем избранным, пока идёт смена.

    Тик — минута, а сам заход идёт по сроку сайта (`favorite_mail_every`, обычно
    полчаса): большинство тиков только смотрят, не пора ли.

    ⚠ **Расписание не спрашивается**, как у круга ответов. Здесь готовится
    черновик, который никуда не уходит без второго выключателя; уносит его круг
    отправки — в свои часы. Второе расписание отвечало бы на тот же вопрос вторым
    голосом.
    """
    await dating_round_loop(deadline, DATING_FAVORITE_MAIL_ROUND_TICK_SEC,
                            'писем избранным', dating_favorite_mail_round_run)


async def _panel_loop(deadline: float) -> None:
    """Держит живой сессию админ-панели каждого сайта, пока идёт смена.

    Тик — минута, а сам заход идёт по сроку сайта (`admin_every`, обычно пять
    минут): большинство тиков только смотрят, не пора ли.

    ⚠ **Расписания нет и не будет.** Панель — наша собственная страница агентства,
    и жива она должна быть всегда: в неё входит круг импорта переписки, и в три
    часа ночи тоже. Смены сайта — про письма живым людям, а не про это.
    """
    await dating_round_loop(deadline, DATING_PANEL_ROUND_TICK_SEC,
                            'админ-панели', dating_panel_round_run)


async def _favorite_loop(deadline: float) -> None:
    """Жмёт звёздочки написавшим, пока идёт смена.

    Тик — минута, а сам заход идёт по сроку сайта (`favorite_every`, обычно десять
    минут): большинство тиков только смотрят, не пора ли.

    ⚠ **Расписание не спрашивается**, как и у забора писем. Человек написал, когда
    написал; звёздочка при этом никому не показывается в ленте и не требует, чтобы
    девушка была «на смене».
    """
    await dating_round_loop(deadline, DATING_FAVORITE_ROUND_TICK_SEC,
                            'избранных', dating_favorite_round_run)


async def _reply_loop(deadline: float) -> None:
    """Набирает пул ответов, пока идёт смена.

    ⚠ **Расписание не спрашивается**, в отличие от рассылки. Здесь готовится
    черновик, который никуда не уходит без второго выключателя: набирать его
    можно когда угодно, а отправит его круг отправки — в свои часы.
    """
    await dating_round_loop(deadline, DATING_REPLY_ROUND_TICK_SEC,
                            'ответов', dating_reply_round_run)


async def _send_loop(deadline: float) -> None:
    """Отправляет письма и ответы, пока идёт смена.

    Тик — полминуты, а шлёт круг по своему сроку (`send_every`, обычно три
    минуты): большинство тиков только смотрят, не пора ли.
    """
    await dating_round_offset_loop(deadline, DATING_SEND_ROUND_TICK_SEC,
                                   'отправки', dating_send_round_run)


async def _heartbeat_loop(deadline: float) -> None:
    """Держит анкеты в сети по их расписанию, пока идёт смена.

    ⚠ **Это про «видно в списке онлайн», а не про чат.** Соседний `_presence_loop`
    держит присутствие **в чате** у сайтов с сокетом и обходит анкеты держателя;
    этот работает по расписанию и там, где сокета нет вовсе. Разделение стоило
    разбирательства: пока присутствие жило только в держателе, сайт без сокета не
    мог показать анкету онлайн в принципе — держать было нечего, и до удара дело
    не доходило.

    Тик — пятнадцать секунд, а бьёт круг по сроку сайта (`heartbeat_every`):
    большинство тиков только смотрят, кому пора.
    """
    await dating_round_offset_loop(deadline, DATING_HEARTBEAT_ROUND_TICK_SEC,
                                   'присутствия', dating_heartbeat_round_run)


async def _online_loop(deadline: float) -> None:
    """Держит ленту «Все онлайн» свежей, пока идёт смена.

    Тик — минута; сам съём идёт куда реже, по выдержке сайта
    (`online_check_every`, обычно две минуты). Большинство тиков только читают
    время снимка.

    ⚠ Круг **заводит** съём и не ждёт его: это ~40 секунд живого браузера, и
    держать на них смену незачем — снимок появится в кэше сам.
    """
    await dating_round_loop(deadline, DATING_ONLINE_ROUND_TICK_SEC,
                            'ленты', dating_online_round_run)


async def _mailing_loop(deadline: float) -> None:
    """Набирает пул писем по расписанию сайта, пока идёт смена.

    Тик — минута, как у расписания анкет: смены заданы с точностью до четверти
    часа. Сам набор при этом идёт куда реже — у каждого сайта свой срок
    (`mailing_every`), и большинство тиков внутри смены только читают отметку.
    """
    await dating_round_offset_loop(deadline, DATING_MAILING_ROUND_TICK_SEC,
                                   'рассылки', dating_mailing_round_run)


async def _mail_round_loop(deadline: float) -> None:
    """Круг шаблонов: новые заготовки писем по расписанию сайта."""
    await dating_round_offset_loop(deadline, DATING_MAIL_ROUND_TICK_SEC,
                                   'шаблонов', dating_mail_round_run)


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
    await dating_round_loop(deadline, DATING_CHAT_IMPORT_TICK_SEC,
                            'импорта переписки', _chat_import_round)


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

        # ⚠ В ленту пишем **до** захода и **всегда**, а не только при добыче.
        # Прежде строка появлялась, лишь когда что-то привезли, — а привозить чаще
        # всего нечего, и чат в ленте не показывался вовсе: человек видел там одну
        # почту и решил, что переписка не забирается. Теперь заход виден так же,
        # как заход за письмами: «иду» → тот же ряд заменяется итогом.
        # ⚠ Без многоточия: оно читается как обрезанный текст, а не как «идёт».
        started = dating_feed_write(site_id, 'chat', 'Забираю переписку из панели')

        result = await dating_chat_panel_import(site_id, DATING_CHAT_PANEL_ROUND_DAYS)

        if result['status'] == 'error':
            logger_info(f"[dating] переписка сайта #{site_id} не забралась: "
                        f"{result['error']}")
            dating_feed_done(site_id, started, 'warn',
                             f"Переписка не забралась: {result['error']}")

            continue

        # В лог — только когда что-то привезли: он растёт бесконечно, и «привезено
        # 0» забило бы его до бесполезности. Лента живёт иначе — она кольцо на
        # тридцать записей, и её задача как раз показать, что круг ходил.
        if result['added']:
            logger_info(f"[dating] переписка сайта #{site_id}: "
                        f"новых {result['added']}, собеседников {result['contacts']}")

        # ⚠ **«Привезено» и «свежих» — разные числа, и путать их дорого.** Импорт
        # берёт сутки назад, и «привезено 5» почти всегда значит пять старых
        # реплик, которых у нас не было. Назови их новыми — человек откроет «Чат →
        # Диалоги» и не найдёт там ничего свежего; на это и пожаловались.
        fresh = int(result.get('fresh') or 0)
        if not result['added']:
            summary = 'Новой переписки нет'
        elif fresh:
            summary = f"Переписка: сообщений {result['added']}, свежих {fresh}"
        else:
            summary = f"Переписка: привезено {result['added']} — все старые"

        dating_feed_done(site_id, started, 'chat', summary)


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
    await dating_round_loop(deadline, DATING_REVIVE_PERIOD_SEC,
                            'восстановления', dating_revive_round_run)


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
