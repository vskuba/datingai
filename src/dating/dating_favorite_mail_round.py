"""Круг переписки с избранными: пул набирается сам, пока включена генерация.

Занятие устроено как круг ответов (`dating_reply_round.py`): расписания не
спрашивает, ходит по сайтам с поднятым выключателем и добирает пул до потолка.

## ⚠ Почему без расписания

Набор ничего не отправляет — письмо ложится в пул черновиком. Уходит оно кругом
отправки, а тот уже соблюдает смены сайта (`mailing_schedule`) и паузу между
письмами. Заведи мы здесь второе расписание, оно отвечало бы на тот же вопрос
вторым голосом: человек правил бы смены в двух местах и гадал, какие из них
сработали.

## ⚠ Частоту держит не круг, а пределы на человека

Сколько бы круг ни ходил, письмо конкретному человеку не появится чаще, чем
позволяют `favorite_mail_per_day` и `favorite_mail_gap` (`dating_favorite_mail`).
Собственный срок круга (`favorite_mail_every`) — это про нагрузку: заход стоит
вызовов LLM, и будить его каждую минуту незачем, когда работа появляется от
молчания собеседника.

⚠ **Когда ходили — помним в Redis**, а не в памяти процесса: смена держателя
перезапускается (свой срок, деплой), и после каждого перезапуска круг заходил бы
заново. Тот же приём, что у забора писем и звёздочек.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.dating.dating_feed import dating_feed_write
from src.dating.dating_pool import dating_pool_sweep
from src.dating.dating_favorite_mail import (DATING_FAVORITE_MAIL_BATCH_DEFAULT,
                                             DATING_FAVORITE_MAIL_POOL_SETTING,
                                             dating_favorite_mail_make,
                                             dating_favorite_mail_pool_left)

# Как часто круг просыпается. ⚠ Не частота захода: её задаёт `favorite_mail_every`.
DATING_FAVORITE_MAIL_ROUND_TICK_SEC = 60

# Как часто заходить на сайт, если настройки нет.
DATING_FAVORITE_MAIL_ROUND_EVERY_DEFAULT = 1800

# Настройка срока.
DATING_FAVORITE_MAIL_ROUND_EVERY_SETTING = 'favorite_mail_every'

# Сколько писем держать в пуле, если настройки нет.
DATING_FAVORITE_MAIL_ROUND_POOL_DEFAULT = 20

# Отметка «на этот сайт только что ходили». В Redis — переживает смену держателя.
DATING_FAVORITE_MAIL_ROUND_KEY = 'dating:favorite:mail:round:{site_id}'


async def dating_favorite_mail_round_run() -> dict:
    """Один круг: добрать пул тем сайтам, где генерация включена и вышел срок.

    Returns:
        dict: `sites` — сколько сайтов обошли, `made` — сколько писем написали.
    """
    sites = await _sites()
    ran = made = 0

    for site in sites:
        site_id = int(site['site_id'])

        # ⚠ **Новый снимок ленты будит круг сразу**, не дожидаясь срока. Это и есть
        # связка «обновили ленту → проверили, фаворит ли он → пристаём»: писать
        # можно только тому, кто **сейчас** на сайте, а заходят люди на десять
        # минут — получасовой круг их попросту не застаёт.
        #
        # ⚠ Частить это не даёт: заход по новому снимку дёшев, пока писать некому
        # (SQL и Redis, без единого вызова LLM), а частоту писем **одному
        # человеку** держат его же пределы — два в сутки и четыре часа паузы. Срок
        # круга остаётся вторым сроком: он ограничивает заходы, когда лента стоит.
        if not _fresh_feed(site_id) and not _due(site_id):
            continue

        # ⚠ Отметку ставим **до** захода: он идёт минутами (каждое письмо — вызов
        # LLM), и следующий тик завёл бы второй, пока идёт первый.
        #
        # Ставим её и когда пришли по свежей ленте: два снимка подряд (лента
        # снимается раз в две минуты) иначе дали бы два захода подряд, а второму
        # писать уже некому — пределы на человека те же.
        _mark(site_id, await _every(site_id))

        # ⚠ Сперва убираем черновики ушедших анкет: отправить их некому, а место
        # в пуле они держат (`dating_pool.py`).
        await dating_pool_sweep(site_id)

        pool = await dating_favorite_mail_pool_left(site_id)
        room = await _pool_max(site_id) - pool
        if room <= 0:
            continue

        ran += 1
        got = await dating_favorite_mail_make(
            site_id, min(room, DATING_FAVORITE_MAIL_BATCH_DEFAULT))
        made += int(got.get('made') or 0)

        if got.get('made'):
            dating_feed_write(site_id, 'fav',
                              f"Готовы письма избранным: {got['made']}")

    if made:
        logger_info(f'[dating] круг писем избранным: сайтов {ran}, написано {made}')

    return {'sites': ran, 'made': made}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _sites() -> list[dict]:
    """Сайты с включённой генерацией писем избранным."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT site_id FROM site'
                ' WHERE is_gone = 0 AND favorite_mail_make_on = 1 ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для писем избранным не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _every(site_id: int) -> int:
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id,
                                         DATING_FAVORITE_MAIL_ROUND_EVERY_SETTING)
               or DATING_FAVORITE_MAIL_ROUND_EVERY_DEFAULT)


async def _pool_max(site_id: int) -> int:
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id,
                                         DATING_FAVORITE_MAIL_POOL_SETTING)
               or DATING_FAVORITE_MAIL_ROUND_POOL_DEFAULT)


# На какой снимок ленты круг уже отзывался: `{сайт: метка снимка}`. В памяти
# процесса, а не в Redis, и это осознанно: перезапуск смены стоит **одного**
# лишнего захода по свежей ленте, а он дёшев — пределы на человека всё равно на
# месте. Redis же пришлось бы чистить вместе с отметкой срока.
_feed_seen: dict[int, float] = {}


def _fresh_feed(site_id: int) -> bool:
    """Появился ли новый снимок ленты с прошлого захода.

    ⚠ **Сравниваем метку снимка, а не «свежее ли он N секунд».** Лента снимается
    своим кругом и своей частотой (`online_check_every`), и вопрос здесь один:
    тот же это список людей или уже другой.

    ⚠ **Первый заход после старта — не «свежий»**: метку запоминаем и ждём
    следующего снимка. Иначе каждый перезапуск смены давал бы внеочередной круг по
    всем сайтам разом.
    """
    from src.dating.dating_online import dating_online_taken_at

    at = dating_online_taken_at(site_id)
    if not at:
        return False

    seen = _feed_seen.get(int(site_id))
    _feed_seen[int(site_id)] = at

    return seen is not None and at > seen


def _due(site_id: int) -> bool:
    """Пора ли заходить. ⚠ Сбой Redis решается в сторону бездействия.

    Лишний заход это вызовы LLM и письма в пуле; пропущенный — полчаса задержки.
    Цена несимметрична, и сомнение решается в сторону молчания — то же правило,
    что у прочих кругов.
    """
    try:
        return not redis_conn_get().get(
            DATING_FAVORITE_MAIL_ROUND_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] отметка писем избранным не прочиталась: {e}')

        return False


def _mark(site_id: int, every: int) -> None:
    """Отмечает заход. Срок отметки **и есть** настройка: Redis погасит её сам."""
    try:
        redis_conn_get().setex(
            DATING_FAVORITE_MAIL_ROUND_KEY.format(site_id=int(site_id)),
            max(int(every), 1), '1')
    except Exception as e:
        logger_info(f'[dating] отметка писем избранным не поставилась: {e}')


def dating_favorite_mail_round_next(site_id: int) -> int:
    """Через сколько секунд круг вернётся к этому сайту. `0` — готов идти сейчас.

    ⚠ **Отвечает `TTL` отметки, а не наш расчёт.** Отметку ставит сам круг, гасит
    её Redis; считай мы «последний заход плюс срок», ответ разошёлся бы с
    действительностью в тот же час, когда настройку поправят на ходу. Тот же приём
    и то же обоснование, что у инвайтов (`dating_invite_round.py`).

    Returns:
        int: секунды до следующего захода. `0` — отметки нет, круг возьмётся за
        сайт на ближайшем тике (в пределах минуты). `-1` — спросить не вышло, и
        врать сроком нельзя.
    """
    try:
        left = redis_conn_get().ttl(
            DATING_FAVORITE_MAIL_ROUND_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] срок писем избранным не спросился: {e}')

        return -1

    # `-2` — ключа нет, `-1` — есть без срока. Первое значит «пора», второе у нас
    # невозможно (ставим только `setex`), но отвечать на него сроком нельзя.
    return 0 if int(left) == -2 else max(0, int(left))
