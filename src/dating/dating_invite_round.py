"""Круг инвайтов: пул набирается сам, по расписанию сайта.

Раз в минуту смотрит, у каких сайтов идёт смена инвайтов, и добирает им пул. То же
самое, что делает кнопка «Генерировать инвайты», только без человека у экрана.

## ⚠ Круг **готовит реплики, а не отправляет их**

Отправка — отдельное решение и отдельный выключатель (`invite_send_on`, снят по
умолчанию). Инвайт уходит живому человеку в чат, и «расписание включено» не значит
«разрешаю писать»: человек включает смены, чтобы к утру был готов пул, который он
прочтёт.

Разведи мы это одним флагом — включение расписания означало бы согласие на
отправку, которого никто не давал. Тот же довод и та же пара, что у писем.

Уносит готовое **свой круг** — `dating_invite_send_round.py`, по одной реплике в
минуту и не больше сотни в сутки.

⚠ **Прежде отправителя не было, и здесь стояло, что не будет.** Довод звучал так:
реплика в чате застаёт адресата сейчас, и решение писать стоит принимать глазами.
Он верен для отладки, а не для работы: кнопкой отправляли, пока смотрели, что за
тексты сочиняет агент. Пул в полсотни реплик, каждую из которых надо нажать, —
не занятие для человека, и заказчик решил иначе. Кнопка осталась: ею проверяют
текст руками и досылают то, что круг пропустил с ошибкой.

## Два срока, и они про разное

| Что | Чем задан | На какой вопрос отвечает |
|-----|-----------|--------------------------|
| смены суток | `dating_site.invite_schedule` | в какие часы вообще готовим |
| частота внутри смены | настройка `invite_every` | как часто добираем пул за эти часы |

Одного расписания мало. Смена «09:00–18:00» — это девять часов, и без второго срока
круг набирал бы пул каждую минуту: пятьсот сорок заходов вместо восемнадцати, и
каждый стоит вызовов LLM на человека.

## ⚠ Пул — запас, а не поток

Круг добирает пул, **пока в нём не станет `invite_pool_max` неотправленных**
(обычно 20), и на этом встаёт. Инвайты ждут человека, а онлайн меняется: сотня
приглашений успеет устареть раньше, чем до неё дойдёт очередь — люди уйдут с сайта,
а реплика про их занятия останется.

⚠ **Когда набирали — помним в Redis**, а не в памяти процесса: смена держателя
перезапускается (свой срок, деплой, падение), и после каждого перезапуска пул
набирался бы заново, независимо от настройки.

## Сайты обходятся по одному

Набор пула — это вызовы LLM, минуты работы. Два сайта разом удвоили бы очередь к
соседу, ничего не ускорив: очередь у него общая.
"""

import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.dating.dating_feed import dating_feed_write
from src.dating.dating_pool import dating_pool_sweep, dating_pool_sweep_away
from src.dating.dating_invite import (DATING_INVITE_BATCH_DEFAULT,
                                      DATING_INVITE_BATCH_MAX,
                                      DATING_INVITE_POOL_DEFAULT,
                                      DatingInviteError, dating_invite_make,
                                      dating_invite_pool_left)
from src.dating.dating_json import dating_json_load
from src.dating.dating_schedule import dating_schedule_now

# Как часто круг просыпается. ⚠ Не частота набора: её задаёт `invite_every`.
DATING_INVITE_ROUND_TICK_SEC = 60

# Настройки сайта, по которым круг живёт.
DATING_INVITE_EVERY_SETTING = 'invite_every'

# Отметка «этому сайту только что набирали». В Redis — переживает смену держателя.
DATING_INVITE_ROUND_KEY = 'dating:invite:round:{site_id}'


async def dating_invite_round_run(offset: int = 0) -> dict:
    """Один круг: добрать пул тем сайтам, у которых идёт смена и подошёл срок.

    Args:
        offset: сдвиг часового пояса панели, в часах. Смены заданы в местном
            времени, а сервер живёт по `UTC` — без сдвига «09:00–18:00» означало
            бы другие часы. Читается вызывающим на каждый круг: настройку правят
            на ходу.

    Returns:
        dict: `sites` — сайтов обойдено, `made` — реплик готово,
            `skipped` — пар пропущено.

    ⚠ **`dating_schedule_now` принимает минуты суток, а не сдвиг.** Здесь стоял
    именно этот промах: в проверку смен уезжал `offset` (то есть `3`), и она
    сравнивала расписание с 00:03 — в смену `07:45–22:15` это не попадает
    никогда. Круг исправно находил сайт, молча его пропускал и возвращал `sites:
    0`; включённая на странице генерация не давала ни одной реплики, и понять это
    по логу было нечем — он пишет только про сделанное.
    """
    ran = made = skipped = 0
    # Минуты от начала **местных** суток: то, что и ждёт проверка смен. Считаем
    # один раз на круг, а не на сайт: за обход они не меняются, а смены у всех
    # сайтов сверяются с одними часами.
    minutes = _minutes_now(offset)

    for site in await _sites():
        site_id = int(site['site_id'])

        # Смены пусты — расписания нет, и круг молчит: готовят такие сайты руками.
        shifts = dating_json_load(site.get('invite_schedule')) or []
        if not shifts or not dating_schedule_now(shifts, minutes):
            continue

        if not _due(site_id):
            continue

        # ⚠ Отметку ставим **до** набора: он идёт минутами, и следующий тик завёл
        # бы второй набор, пока идёт первый.
        _mark(site_id, await _every(site_id))

        # ⚠ Сперва убираем из пула черновики ушедших анкет, и только потом
        # считаем место. Инвайт от анкеты не в сети отправить некому — отправка
        # ждёт её, — а потолок он занимает: полный пул мёртвых реплик означал бы,
        # что круг не добирает ни одной живой (`dating_pool.py`).
        await dating_pool_sweep(site_id)

        # ⚠ И черновики тех, **кому** писали, если их уже нет в сети. Реплика
        # зовёт сидящего в чате сейчас; ушёл — звать некого, а место в пуле она
        # держит. Без этой строки пул зарастал: 50 черновиков, годных 27.
        await dating_pool_sweep_away(site_id)

        left = await _pool_max(site_id) - await dating_invite_pool_left(site_id)
        if left <= 0:
            continue

        ran += 1
        try:
            got = await dating_invite_make(site_id, min(left, await _batch(site_id)))
        except DatingInviteError as e:
            logger_info(f'[dating] инвайты сайта #{site_id}: {e}')
            continue

        made += int(got.get('made') or 0)

        if got.get('made'):
            dating_feed_write(site_id, 'invite',
                              f"Готовы инвайты в чат: {got['made']}")
        skipped += int(got.get('skipped') or 0)

    if made or skipped:
        logger_info(f'[dating] круг инвайтов: сайтов {ran}, готово {made}'
                    + (f', пропущено {skipped}' if skipped else ''))

    return {'sites': ran, 'made': made, 'skipped': skipped}


# ── Приватное ────────────────────────────────────────────────────────────────

def _minutes_now(offset_hours: int) -> int:
    """Минуты от начала местных суток: смены сверяют с часами на стене.

    ⚠ Своей функцией, а не выражением на месте: ровно её отсутствие и превратило
    сдвиг часового пояса в «минуты суток». Такой же помощник есть у кругов
    рассылки и отправки — эти три места считают одно и то же.
    """
    return int((time.time() + int(offset_hours) * 3600) // 60 % (24 * 60))

async def _sites() -> list[dict]:
    """Сайты с включённым расписанием инвайтов."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id, invite_schedule FROM dating_site'
                             ' WHERE is_gone = 0 AND invite_schedule_on = 1'
                             ' ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для инвайтов не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _seconds(site_id: int, key: str) -> int:
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id, key))


async def _every(site_id: int) -> int:
    """Как часто добирать пул внутри смены, в секундах."""
    return await _seconds(site_id, DATING_INVITE_EVERY_SETTING)


async def _batch(site_id: int) -> int:
    """Сколько пар пробуем за заход."""
    return min(await _seconds(site_id, 'invite_batch') or DATING_INVITE_BATCH_DEFAULT,
               DATING_INVITE_BATCH_MAX)


async def _pool_max(site_id: int) -> int:
    """До какого числа неотправленных добираем пул."""
    return await _seconds(site_id, 'invite_pool_max') or DATING_INVITE_POOL_DEFAULT


def dating_invite_round_next(site_id: int) -> int:
    """Через сколько секунд круг вернётся к этому сайту. `0` — готов идти сейчас.

    ⚠ **Отвечает `TTL` отметки, а не наш расчёт.** Отметку ставит сам круг и
    гасит её Redis; считай мы «последний заход плюс срок», ответ разошёлся бы с
    действительностью в тот же час, когда настройку поправят на ходу.

    Заведено для страницы: в шапке пула висело «по расписанию» — обещание без
    срока. Пул набирается сам, и человек, включивший генерацию, не знал, ждать ему
    минуту или полчаса; выглядело это как «включил, а оно не работает».

    Returns:
        int: секунды до следующего захода. `0` — отметки нет, круг возьмётся за
        сайт на ближайшем тике (в пределах минуты). `-1` — спросить не вышло, и
        врать сроком нельзя.
    """
    try:
        left = redis_conn_get().ttl(DATING_INVITE_ROUND_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] срок инвайтов не спросился: {e}')

        return -1

    # `-2` — ключа нет, `-1` — есть без срока. Первое значит «пора», второе у нас
    # невозможно (ставим только `setex`), но отвечать на него сроком нельзя.
    return 0 if int(left) == -2 else max(0, int(left))


def _due(site_id: int) -> bool:
    """Пора ли набирать. ⚠ Сбой Redis решается в сторону бездействия.

    Не прочиталась отметка — не идём. Лишний заход это вызовы LLM и минуты очереди
    у соседа; пропущенный стоит получаса задержки пула, который всё равно ждёт
    человека. Цена ошибок несимметрична.
    """
    try:
        return not redis_conn_get().get(
            DATING_INVITE_ROUND_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] отметка инвайтов не прочиталась: {e}')

        return False


def _mark(site_id: int, every: int) -> None:
    """Отмечает заход. Срок отметки **и есть** настройка: Redis погасит её сам."""
    try:
        redis_conn_get().setex(
            DATING_INVITE_ROUND_KEY.format(site_id=int(site_id)),
            max(int(every), 1), '1')
    except Exception as e:
        logger_info(f'[dating] отметка инвайтов не поставилась: {e}')
