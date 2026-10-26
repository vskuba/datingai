"""Круг переписки с избранными: пул набирается по сменам сайта.

Занятие устроено как круг инвайтов (`invite_round.py`): ходит по сайтам с
поднятым выключателем, смотрит, идёт ли смена, и добирает пул до потолка.

## ⚠ Расписание у набора своё, и это не спор с отправкой

Смены здесь отвечают на вопрос «когда **сочиняем**», а `mailing_schedule` — на
«когда **отправляем**». Вопроса два, потому что дорого и рискованно тоже разное:
сочинение стоит вызовов LLM и ничего не шлёт наружу, отправка ничего не стоит и
уходит живому человеку.

Прежде расписания здесь не было вовсе — круг ходил круглые сутки, — и довод был
такой: «набор ничего не отправляет, второе расписание отвечало бы на тот же
вопрос вторым голосом». Довод оказался неверным в одном месте: **пул набирается
под тех, кто в сети сейчас**. Написанное ночью к утру протухает — человек ушёл, а
письмо лежит и займёт его место в пуле.

## ⚠⚠ Пустые смены значат «не работаем»

Правило одно на проект (`mailing_round`, `invite_round`): пустое расписание — это
«нет», а не «всегда». Второе прочтение здесь означало бы, что человек, стерев все
смены, получает круглосуточную работу.

Своего выключателя у смен нет: работает занятие или нет, говорит
`favorite_mail_make_on` — та же галочка, что и раньше.

## ⚠ Частоту держит не круг, а пределы на человека

Сколько бы круг ни ходил, письмо конкретному человеку не появится чаще, чем
позволяют `favorite_mail_per_day` и `favorite_mail_gap` (`favorite_mail`).
Собственный срок круга (`favorite_mail_every`) — это про нагрузку: заход стоит
вызовов LLM, и будить его каждую минуту незачем, когда работа появляется от
молчания собеседника.

⚠ **Когда ходили — помним в Redis**, а не в памяти процесса: смена держателя
перезапускается (свой срок, деплой), и после каждого перезапуска круг заходил бы
заново. Тот же приём, что у забора писем и звёздочек.
"""

from logging_.logging_ import logger_info

from src.dating.feed import feed_write
from src.dating.round import (round_sites, round_sites_run, round_slot_due,
                              round_slot_left)
from src.dating.pool import pool_sweep
from src.dating.favorite_mail import (FAVORITE_MAIL_BATCH_DEFAULT,
                                             FAVORITE_MAIL_POOL_SETTING,
                                             favorite_mail_make,
                                             favorite_mail_pool_left,
                                             favorite_mail_shift_now,
                                             favorite_mail_stars_sync)
from src.dating.site_setting import site_seconds

# Как часто круг просыпается. ⚠ Не частота захода: её задаёт `favorite_mail_every`.
FAVORITE_MAIL_ROUND_TICK_SEC = 60

# Как часто заходить на сайт, если настройки нет.
FAVORITE_MAIL_ROUND_EVERY_DEFAULT = 1800

# Настройка срока.
FAVORITE_MAIL_ROUND_EVERY_SETTING = 'favorite_mail_every'

# Сколько писем держать в пуле, если настройки нет.
FAVORITE_MAIL_ROUND_POOL_DEFAULT = 20

# Отметка «на этот сайт только что ходили». В Redis — переживает смену держателя.
FAVORITE_MAIL_ROUND_KEY = 'dating:favorite:mail:round:{id}'


async def favorite_mail_round_run(offset: int = 0) -> dict:
    """Один круг: добрать пул тем сайтам, где идёт смена и вышел срок.

    Args:
        offset: сдвиг часового пояса панели, в часах. Смены заданы местным
            временем, а сервер живёт по `UTC`; читает его обвязка на каждый
            круг — настройку правят на ходу.

    Returns:
        dict: `sites` — сколько сайтов обошли, `made` — сколько писем написали.

    ⚠ **`schedule_now` ждёт минуты суток, а не сдвиг.** У инвайтов на этом уже
    спотыкались: в проверку уезжал `offset` (то есть `3`), она сравнивала смены с
    00:03, и круг молча пропускал каждый сайт. Считаем минуты **один раз на
    круг**: за обход они не меняются, а часы у всех сайтов одни.
    """
    sites = await _sites()
    ran = made = 0
    for _, c in await round_sites_run(sites, offset_hours=offset, gate=_gate,
                                    every=_every, work=_work,
                                    key=FAVORITE_MAIL_ROUND_KEY,
                                    what='письмам избранным'):
        if c is not None:
            ran += c['sites']
            made += c['made']

    if made:
        logger_info(f'[dating] круг писем избранным: сайтов {ran}, '
                    f'написано {made}')

    return {'sites': ran, 'made': made}


def favorite_mail_round_next(site_id: int) -> int:
    """Через сколько секунд круг вернётся к этому сайту. `0` — готов идти сейчас.

    ⚠ **Отвечает `TTL` отметки, а не наш расчёт.** Отметку ставит сам круг, гасит
    её Redis; считай мы «последний заход плюс срок», ответ разошёлся бы с
    действительностью в тот же час, когда настройку поправят на ходу. Тот же приём
    и то же обоснование, что у инвайтов (`invite_round.py`).

    Returns:
        int: секунды до следующего захода. `0` — отметки нет, круг возьмётся за
        сайт на ближайшем тике (в пределах минуты). `-1` — спросить не вышло, и
        врать сроком нельзя.

    ⚠⚠ **Ключ собирает слой** (`round_slot_left` → `round_slot_key`), а не эта
    функция. Прежде она подставляла единицу сама — `.format(site_id=…)` на
    шаблоне с меткой `{id}`, — и ответ был `-1` **всегда**: `KeyError` глотал
    `except`. Счётчик на странице молчал месяцами, и выглядело это как «данных
    нет», а не как поломка (`tests/test_round_next.py`).
    """
    return round_slot_left(FAVORITE_MAIL_ROUND_KEY, int(site_id),
                           'письмам избранным')


async def _gate(site: dict, minutes: int) -> bool:
    """Смена идёт (общим предикатом) и круг пора: свежая лента или вышел срок.

    ⚠ Смена спрашивается общим предикатом (`favorite_mail_shift_now`), а не
    своей проверкой: по нему же отвечает шапка пула. Разойдись они — панель
    писала бы «идёт смена» там, где круг уходит ни с чем.

    ⚠ Новый снимок ленты будит круг сразу, не дожидаясь срока: писать можно
    только тому, кто сейчас на сайте, а заходят люди на десять минут —
    получасовой круг их попросту не застаёт. Частить это не даёт: заход по
    новому снимку дёшев, пока писать некому (SQL и Redis, без вызовов LLM),
    а частоту одному человеку держат его же пределы. Сбой — молчим.

    ⚠ Отметку обвязка ставит до захода и ставит, когда пришла свежая лента:
    два снимка подряд дали бы два захода, а второму писать уже некому.
    """
    site_id = int(site['site_id'])
    if not favorite_mail_shift_now(site['schedule'], minutes):
        return False

    return _fresh_feed(site_id) or round_slot_due(
        FAVORITE_MAIL_ROUND_KEY, site_id, 'письмам избранным',
        every=await _every(site_id))


async def _work(site: dict, minutes: int) -> dict | None:
    """Добирает пул писем избранным; черновики ушедших уходят первыми.
    """
    site_id = int(site['site_id'])

    # ⚠ Сперва убираем черновики ушедших анкет: отправить их некому, а место
    # в пуле они держат (`pool.py`).
    await pool_sweep(site_id)

    # ⚠⚠ **Сверка звёзд — до счёта места, и это не порядок ради порядка.**
    #
    # Она гасит пары, пропавшие из списков сайта, и уносит их черновики
    # (`favorite_sync`). Стояла она внутри набора — то есть **за** проверкой
    # `room <= 0`, — и дверь запиралась изнутри: стухший черновик держит место в
    # пуле, места нет, набор не зовётся, сверка не идёт, черновик не убирается.
    # Единственный, кто из этого круга выходил, — круг отправки: полный пул он как
    # раз разбирает, то есть слал письмо тому, кто давно не избранный.
    #
    # ⚠ Второй вызов из набора ничего не стоит: срок отмечен, `_sync_due` ответит
    # «рано».
    await favorite_mail_stars_sync(site_id)

    # ⚠ Место считаем по **живым** черновикам: строка с причиной отказа ждёт
    # человека, а не отправку, и держать ею потолок значило бы молчать при
    # пустой очереди (`favorite_mail_pool_left`).
    pool = await favorite_mail_pool_left(site_id, ready_only=True)
    room = await _pool_max(site_id) - pool
    if room <= 0:
        return None

    got = await favorite_mail_make(
        site_id, min(room, FAVORITE_MAIL_BATCH_DEFAULT))
    made = int(got.get('made') or 0)

    if got.get('made'):
        feed_write(site_id, 'fav',
                          f"Готовы письма избранным: {got['made']}")

    return {'sites': 1, 'made': made}

# ── Приватное ────────────────────────────────────────────────────────────────

async def _sites() -> list[dict]:
    """Сайты с включённой генерацией писем избранным — вместе со сменами."""
    return await round_sites(flags=('favorite_mail_make_on',),
                             schedule='favorite_mail_schedule',
                             what='письмам избранным')


async def _every(site_id: int) -> int:
    return int(await site_seconds(site_id,
                                         FAVORITE_MAIL_ROUND_EVERY_SETTING)
               or FAVORITE_MAIL_ROUND_EVERY_DEFAULT)


async def _pool_max(site_id: int) -> int:
    return int(await site_seconds(site_id,
                                         FAVORITE_MAIL_POOL_SETTING)
               or FAVORITE_MAIL_ROUND_POOL_DEFAULT)


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
    from src.dating.online import online_taken_at

    at = online_taken_at(site_id)
    if not at:
        return False

    seen = _feed_seen.get(int(site_id))
    _feed_seen[int(site_id)] = at

    return seen is not None and at > seen


