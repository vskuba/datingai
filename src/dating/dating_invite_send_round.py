"""Круг отправки инвайтов: реплики уходят в чат сами, по одной.

Выключатель сайта `invite_send_on` хранит решение, исполнитель — этот круг.
Кнопка на карточке собеседника остаётся и работает как работала: ею проверяют
текст руками и досылают то, что круг пропустил с ошибкой.

## ⚠ Прежде круга не было, и это было решением

`dating_invite_round.py` до сих пор писал: «кругового отправителя у инвайтов нет
вовсе, отправляет человек кнопкой» — с доводом, что реплика в чате застаёт
адресата сейчас, и решение писать стоит принимать глазами. Довод верен для
проверки, а не для работы: кнопкой отправляли, пока смотрели, что за тексты
сочиняет агент. Пул в полсотни реплик, каждую из которых надо нажать, —
это не занятие для человека, и заказчик решил иначе.

Что от прежнего осталось: **пара выключателей** («готовим» и «шлём» — разные
решения) и кнопка. Что появилось — правила ниже; каждое не осторожность вообще,
а защита от конкретной беды.

## Правила, и почему каждое

**Смены сайта соблюдаются** (`invite_schedule`) — те же часы, в которые круг
набирает пул. Реплика в четыре утра выдаёт робота вернее любого текста.

**По одной реплике за заход, с паузой** (`invite_send_every`, по умолчанию
минуту). Пул в двадцать реплик, ушедший залпом, — это робот, чем он и является.

⚠ Пауза здесь **короче почтовой** (там три минуты), и это не небрежность:
инвайт зовёт того, кто **сейчас** в чате, а заходят люди на десять минут.
Трёхминутная пауза не застаёт их, и половина пула уходила бы впустую — в чат,
который уже закрыли.

**Потолок в сутки** (`invite_send_max_day`, по умолчанию сто). Считается по
факту отправки, а не по попыткам: отказ сайта не должен съедать дневную норму.

⚠ **Потолок свой, а не общий с письмами.** У писем он тоже есть
(`send_max_day`), но канал другой: письмо человек читает в почте и через день,
реплика приходит в чат сейчас. Считай мы их вместе, полсотни писем закрывали бы
инвайты на сутки — и наоборот.

**Пишет только анкета, которая сейчас в сети** (`status = 'online'`). Реплика
уходит её сессией, и у вышедшей девушки отправлять нечем; а придёт ответ — его
некому будет прочитать живым. То же условие, что у писем избранным, и по той же
причине.

⚠ Это правило опирается на свежесть `status`, а её держит круг опроса
(`dating_status_round.py`). Пока его не было, статус у анкеты без смен не
двигался неделями — и условие «в сети» либо не пускало никого, либо пускало
всех, смотря что записали в тот единственный раз.

**Неудача не повторяется тут же.** `dating_invite_send.py` оставляет реплику в
пуле с причиной в `error`; круг такую строку пропускает. Иначе один отказ сайта
(«слишком часто») превращался бы в бесконечный цикл ровно тогда, когда сайт
просит остановиться. Ждёт она человека: тот прочтёт причину и нажмёт сам.

## Сайты обходятся по одному

Отправка — это живой запрос к сайту, а у сайта на нас свои пределы. Два сайта
разом означали бы две реплики в одну секунду; порядок обхода — по номеру, и
пауза считается **каждому сайту своя**.
"""

import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_invite_send import dating_invite_send_one
from src.dating.dating_json import dating_json_load
from src.dating.dating_schedule import dating_schedule_now

# Как часто круг просыпается. ⚠ Это **не** частота отправки: ту задаёт
# `invite_send_every`, а круг лишь смотрит, не пора ли. Пятнадцать секунд, потому
# что минутная пауза при тике в полминуты давала бы разброс в полтора раза.
DATING_INVITE_SEND_ROUND_TICK_SEC = 15

# Настройки сайта: пауза между репликами и потолок за сутки.
DATING_INVITE_SEND_EVERY_SETTING = 'invite_send_every'
DATING_INVITE_SEND_MAX_DAY_SETTING = 'invite_send_max_day'

# Умолчания на случай сайта без настроек. Минута и сотня — с них решили начать.
DATING_INVITE_SEND_EVERY_DEFAULT = 60
DATING_INVITE_SEND_MAX_DAY_DEFAULT = 100

# ⚠ Нижний предел поверх настройки. Её правят руками, и «5» превратило бы смену в
# очередь из сотен реплик в час — то самое, от чего пауза и заведена.
DATING_INVITE_SEND_EVERY_MIN = 20

# Когда сайту уходила последняя реплика: `{сайт: время}`. В памяти процесса, как у
# круга отправки писем: перезапуск смены — это лишняя пауза, а не лишняя реплика,
# и ошибаться сюда безопасно.
_sent_at: dict[int, float] = {}


async def dating_invite_send_round_run(offset_hours: int = 0) -> dict:
    """Один заход: каждому сайту, которому пора, — по одной реплике.

    Args:
        offset_hours: часовое смещение панели (настройка `timezone`). Смены
            заданы местным временем, а сервер живёт по UTC.

    Returns:
        dict: `sent` и `failed` — сколько ушло и сколько не ушло за этот заход.
    """
    minutes = _minutes_now(offset_hours)
    sent = failed = 0

    for site in await _sites():
        site_id = int(site['site_id'])

        if not dating_schedule_now(dating_json_load(site['invite_schedule']), minutes):
            continue

        if time.time() - _sent_at.get(site_id, 0.0) < await _every(site_id):
            continue

        if await _today(site_id) >= await _max_day(site_id):
            continue

        invite = await _next(site_id)
        if not invite:
            continue

        # ⚠ Отметку о заходе ставим **до** отправки, а не после. Отправка идёт
        # секунды, а тик — пятнадцать: не отметь мы сразу, второй тик успел бы
        # взять ту же строку, пока первый ждёт ответа сайта.
        _sent_at[site_id] = time.time()

        if (await dating_invite_send_one(invite))['ok']:
            sent += 1
        else:
            failed += 1

    if sent or failed:
        logger_info(f'[dating] круг инвайтов: ушло {sent}'
                    + (f', не ушло {failed}' if failed else ''))

    return {'sent': sent, 'failed': failed}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _sites() -> list[dict]:
    """Сайты, где отправка инвайтов разрешена."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT site_id, invite_schedule FROM dating_site'
                ' WHERE is_gone = 0 AND invite_send_on = 1 ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для отправки инвайтов не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _next(site_id: int) -> dict:
    """Следующая реплика сайта: самая старая годная. Пусто — отправлять нечего.

    ⚠ **Только строки без прежней ошибки** (`error = ''`): взявшись за них снова,
    круг зациклился бы на отказе сайта. Такая реплика ждёт человека с кнопкой.

    ⚠ **Только от анкеты, которая в сети** — условие в запросе, а не после
    выборки: отбери мы старейшую и отбрось её по статусу, круг стоял бы, пока
    первая в очереди девушка не вернётся, хотя писать было кому.

    Порядок — по возрасту строки: реплика сочинялась под человека, который был в
    чате тогда, и чем дольше она лежит, тем меньше от неё толку.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT i.id, i.site_id, i.profile_id, i.uid, i.name, i.body'
                '  FROM dating_invite i'
                '  JOIN dating_profile p ON p.id = i.profile_id'
                " WHERE i.site_id = %s AND i.status = 'draft' AND i.error = %s"
                "   AND p.enabled = 1 AND p.status = 'online'"
                ' ORDER BY i.created_at, i.id LIMIT 1', (int(site_id), ''))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] очередь инвайтов сайта #{site_id} не спросилась: {e}')

        return {}

    return dict(row) if row else {}


async def _today(site_id: int) -> int:
    """Сколько реплик сайта ушло за сегодня. Считаем по факту, а не по попыткам."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM dating_invite'
                " WHERE site_id = %s AND status = 'sent' AND DATE(sent_at) = CURDATE()",
                (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] дневной счёт инвайтов #{site_id} не спросился: {e}')

        # ⚠ Не сосчитали — считаем, что потолок выбран. Верная сторона ошибки:
        # молчание дешевле сотни реплик сверх нормы.
        return DATING_INVITE_SEND_MAX_DAY_DEFAULT

    return int((row or {}).get('n') or 0)


async def _every(site_id: int) -> int:
    """Пауза между репликами у этого сайта, не короче жёсткого предела."""
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    every = int(await dating_site_seconds(site_id, DATING_INVITE_SEND_EVERY_SETTING)
                or DATING_INVITE_SEND_EVERY_DEFAULT)

    return max(DATING_INVITE_SEND_EVERY_MIN, every)


async def _max_day(site_id: int) -> int:
    """Потолок реплик в сутки у этого сайта."""
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id, DATING_INVITE_SEND_MAX_DAY_SETTING)
               or DATING_INVITE_SEND_MAX_DAY_DEFAULT)


def _minutes_now(offset_hours: int) -> int:
    """Минуты от начала местных суток: смены сверяют с часами на стене.

    Тот же расчёт, что у круга отправки писем, — и намеренно тот же: два способа
    посчитать «который час» разошлись бы на границе суток, и одна из отправок
    ушла бы вне смены.
    """
    local = time.gmtime(time.time() + int(offset_hours or 0) * 3600)

    return local.tm_hour * 60 + local.tm_min
