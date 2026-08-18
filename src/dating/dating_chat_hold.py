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

from src.dating.dating_auto import DATING_AUTO_PERIOD_SEC, dating_auto_round
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_error_write,
                                       rhapsody_api_site_get)
from src.dating.dating_chat import dating_chat_listen
from src.dating.dating_json import dating_json_load
from src.dating.dating_presence import dating_presence_beat, dating_presence_ready
from src.dating.dating_ws import dating_ws_capture

# Как часто сверять список учёток с базой.
DATING_CHAT_HOLD_RESCAN_SEC = 60

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


async def dating_chat_hold_run(seconds: float) -> dict:
    """Держит сокеты всех готовых учёток заданное время.

    Args:
        seconds: сколько работать. По исходу соединения закрываются — смену
            перезапустит очередь, и код обновится вместе с ней.

    Returns:
        dict: `girls` — сколько учёток держали, `saved` — сколько сообщений
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

    try:
        while time.monotonic() < deadline:
            girls = await _girls_ready()
            _holders_sync(holders, counters, girls, deadline)

            await asyncio.sleep(min(DATING_CHAT_HOLD_RESCAN_SEC,
                                    max(1.0, deadline - time.monotonic())))
    finally:
        beat.cancel()
        answer.cancel()
        for held in holders.values():
            held['task'].cancel()
        # Ждём отмены: без этого соединения закрываются уже после выхода, и в
        # логе появляется хвост из «Task was destroyed but it is pending».
        await asyncio.gather(*(held['task'] for held in holders.values()),
                             return_exceptions=True)

    saved = sum(counters.values())
    logger_info(f'[dating] держатель чата закончил: учёток {len(counters)}, сообщений {saved}')

    return {'girls': len(counters), 'saved': saved, 'sec': int(seconds)}


async def dating_chat_hold_girl(girl: dict, presence: dict, site_url: str,
                                deadline: float, counters: dict) -> None:
    """Держит сокет одной учётки до срока, переподключаясь после обрывов.

    Обрыв не считается ошибкой: сайт рвёт связь сам, и единственная разумная
    реакция — подождать и подключиться снова. Ошибкой становится череда неудач,
    и лечится она свежим адресом сокета.
    """
    girl_id = int(girl['id'])
    backoff = DATING_CHAT_HOLD_BACKOFF_MIN
    fails = 0
    captured_at = 0.0

    while time.monotonic() < deadline:
        left = deadline - time.monotonic()
        result = await dating_chat_listen(girl, seconds=left)
        counters[girl_id] = counters.get(girl_id, 0) + int(result.get('saved') or 0)

        if not result.get('error'):
            # Соединение закрылось само и без жалоб — обычный обрыв, начинаем заново.
            fails, backoff = 0, DATING_CHAT_HOLD_BACKOFF_MIN
        else:
            fails += 1
            logger_info(f"[dating] чат #{girl_id}: обрыв ({fails}) — {result['error'][:120]}")

        if fails >= DATING_CHAT_HOLD_FAIL_LIMIT and \
                time.monotonic() - captured_at > DATING_CHAT_HOLD_RECAPTURE_SEC:
            captured_at = time.monotonic()
            fails = 0
            girl = await _girl_row(girl_id) or girl
            url = await dating_ws_capture(girl, presence, site_url)
            # Пустой ответ — неудача перехвата, а не «адреса больше нет». Затереть
            # им прежний значило бы обречь учётку на «нужен вход» до конца смены,
            # даже когда старый адрес ещё работал.
            if url:
                girl = {**girl, 'ws_url': url}
            logger_info(f"[dating] чат #{girl_id}: адрес сокета "
                        f"{'обновлён' if url else 'снять не вышло'}")
            # Череда неудач — это уже не рабочий обрыв, а поломка: она уходит во
            # вкладку «Ошибки» страницы браузера, где её ищут вместе с остальными.
            await _error_write(girl_id, girl.get('login', ''),
                               'чат не поднимается: '
                               + ('адрес сокета переснят' if url else 'адрес сокета снять не вышло'),
                               result.get('error', ''))

        await asyncio.sleep(min(backoff, max(0.0, deadline - time.monotonic())))
        backoff = min(backoff * 2, DATING_CHAT_HOLD_BACKOFF_MAX)


def _holders_sync(holders: dict, counters: dict, girls: list, deadline: float) -> None:
    """Приводит набор держателей в соответствие списку учёток из базы.

    Сверяется не только состав, но и адрес сокета: новый вход выдаёт новый адрес,
    а держатель, не заметив этого, продолжал бы висеть на прежнем. Соединение при
    этом выглядит живым — сайт его не рвёт, просто перестаёт слать по нему что-либо.
    """
    fresh = {int(item['girl']['id']): item for item in girls}

    for girl_id, held in list(holders.items()):
        item = fresh.get(girl_id)
        gone = item is None
        moved = not gone and str(item['girl'].get('ws_url') or '') != held['ws_url']
        if gone or moved or held['task'].done():
            held['task'].cancel()
            holders.pop(girl_id, None)
            if moved:
                logger_info(f'[dating] чат #{girl_id}: адрес сокета сменился, переподключаюсь')

    for girl_id, item in fresh.items():
        if girl_id in holders:
            continue
        holders[girl_id] = {
            'ws_url': str(item['girl'].get('ws_url') or ''),
            'task': asyncio.create_task(
                dating_chat_hold_girl(item['girl'], item['presence'], item['site_url'],
                                      deadline, counters)),
        }
        logger_info(f"[dating] чат #{girl_id} ({item['girl']['login']}): взят в работу")


async def _girls_ready() -> list[dict]:
    """Учётки, которые есть смысл держать: со снятым входом и настроенным сайтом.

    Учётка без `storage_state` — это анкета, под которой ещё не входили: адрес
    сокета для неё снять не из чего, и держать нечего.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, login, site_id, storage_state, ws_url, auto_answer, '
            '       auto_answer_only, role, profile '
            '  FROM dating_girl WHERE storage_state IS NOT NULL ORDER BY id')
        rows = await db.fetchall()

    # Настройка сайта живёт в RhapsodyAi, и join-ом её больше не взять. Спрашиваем
    # по разу на сайт, а не на анкету: сайтов единицы, анкет сотни, и сотня
    # одинаковых запросов на каждый круг — это минута на пустом месте.
    sites: dict[int, dict] = {}
    ready = []
    for row in rows or []:
        site_id = int(row['site_id'])
        if site_id not in sites:
            try:
                sites[site_id] = await rhapsody_api_site_get(site_id)
            except RhapsodyError as e:
                logger_info(f'[dating] сайт #{site_id} не спросился: {e}')
                sites[site_id] = {}

        site = sites[site_id]
        presence = dating_json_load(site.get('presence'))
        if not dating_presence_ready(presence):
            continue

        girl = {'id': row['id'], 'login': row['login'], 'site_id': row['site_id'],
                'storage_state': row['storage_state'], 'ws_url': row['ws_url'],
                'auto_answer': row['auto_answer'],
                'auto_answer_only': row['auto_answer_only'], 'role': row['role'],
                'profile': row['profile']}
        ready.append({'girl': girl, 'presence': presence, 'site_url': site.get('url', '')})

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
            await dating_presence_beat(item['girl'], item['presence'])

    while time.monotonic() < deadline:
        try:
            girls = await _girls_ready()
            if girls:
                await asyncio.gather(*(beat(item) for item in girls), return_exceptions=True)
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
            girls = await _girls_ready()
            if girls:
                await dating_auto_round(girls)
        except Exception as e:
            logger_info(f'[dating] круг автоответа: {e}')


async def _error_write(girl_id: int, login: str, what: str, detail: str) -> None:
    """Поломка чата — в общий журнал ошибок браузера.

    Импорт внутри функции: журнал живёт в роутерах, а этот модуль зовут и из
    воркера — тянуть половину FastAPI при загрузке модуля незачем.
    """

    await rhapsody_api_error_write('dating', f'{what}: {detail}'.strip(': '),
                              context={'girl_id': girl_id, 'login': login, 'where': 'держатель чата'})


async def _girl_row(girl_id: int) -> dict | None:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, login, site_id, storage_state, ws_url '
                         '  FROM dating_girl WHERE id = %s', (int(girl_id),))
        row = await db.fetchone()

    return dict(row) if row else None
