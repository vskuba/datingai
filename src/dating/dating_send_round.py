"""Круг отправки: письма и ответы уходят сами, по одному.

Выключатели сайта — `mailing_send_on`, `reply_send_on` и `favorite_mail_send_on`
— только хранят решение; исполнитель у всех троих один, этот круг.

## ⚠ Отправка — самое заметное наружу действие панели

Письмо уходит живому человеку от имени девушки, и отменить его нельзя. Отсюда все
правила ниже; каждое из них — не осторожность вообще, а защита от конкретной беды.

**Выключатели раздельные.** «Готовим» и «шлём» — разные решения, и включённый
набор согласием на отправку не является. Пара своя у каждого из трёх занятий:
рассылки, ответов и переписки с избранными.

**Смены сайта соблюдаются.** Шлём в те же часы, когда набираем
(`mailing_schedule`): письмо в четыре утра выдаёт робота вернее любого текста.

**По одному письму за заход, с паузой.** Пул в полсотни писем, ушедший залпом, —
это рассылка робота, чем он и является. Пауза `send_every` (по умолчанию три
минуты) делает поток похожим на человека, который сидит и пишет.

**Потолок в сутки.** `send_max_day`: даже за длинную смену уходит не больше, чем
человек физически написал бы. Считается по факту отправки, а не по попыткам.

⚠ **Письма избранным — только от анкеты, которая сейчас в сети** (`status =
'online'`). Это возобновление затихшего разговора: человек читает письмо, пока
сидит на сайте, и отвечает сразу — а отвечает он девушке, которой там нет. Набор
пула к тому же отбирал тех, кто онлайн **в момент набора**, и отправка в её
отсутствие рассылала бы вчерашний отбор.

У рассылки и ответов такого условия нет: первое уходит незнакомому, второе — тому,
кто написал сам и ждёт, и ни то, ни другое не про «прямо сейчас».

⚠ **Неудача не повторяется тут же.** `dating_mail_send.py` оставляет письмо в пуле
с причиной в `error`; круг такое письмо пропускает до конца суток. Иначе один
отказ сайта («слишком часто») превращался бы в бесконечный цикл ровно тогда, когда
сайт просит остановиться.
"""

import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_json import dating_json_load
from src.dating.dating_mail_send import (DatingMailSendError, dating_mail_send_mark,
                                         dating_mail_send_one)
from src.dating.dating_schedule import dating_schedule_now

# Как часто круг просыпается. ⚠ Это **не** частота отправки: ту задаёт
# `send_every`, а круг лишь смотрит, не пора ли.
DATING_SEND_ROUND_TICK_SEC = 30

# Пауза между письмами, если у сайта не задана. Три минуты: полсотни писем уходят
# за два с половиной часа — примерно столько человек и потратил бы.
DATING_SEND_EVERY_DEFAULT = 180

# ⚠ Нижний предел поверх настройки. Настройку правят руками, и «5» превратило бы
# смену в очередь из сотни писем в час — то самое, от чего пауза и заведена.
DATING_SEND_EVERY_MIN = 30

# Сколько писем в сутки, если настройки нет. Столько человек и пишет за день.
DATING_SEND_MAX_DAY_DEFAULT = 40

# Настройки сайта.
DATING_SEND_EVERY_SETTING = 'send_every'
DATING_SEND_MAX_DAY_SETTING = 'send_max_day'

# Когда сайт слал в последний раз: `{сайт: время}`. В памяти процесса — как у
# круга присутствия: перезапуск смены стоит одного лишнего письма, а не потока.
_sent_at: dict[int, float] = {}


async def dating_send_round_run(offset_hours: int = 0) -> dict:
    """Один круг: отправить по одному письму и ответу с каждого готового сайта.

    Args:
        offset_hours: часовое смещение панели — смены заданы местным временем.

    Returns:
        dict: `sent` — сколько ушло, `failed` — сколько не ушло.
    """
    sites = await _sites()
    if not sites:
        return {'sent': 0, 'failed': 0}

    minutes = _minutes_now(offset_hours)
    sent = failed = 0

    for site in sites:
        site_id = int(site['site_id'])

        # ⚠ Смены общие с набором: шлём тогда же, когда готовим. Пустое
        # расписание — «нет», а не «всегда»: правило одно на проект.
        if not dating_schedule_now(dating_json_load(site['mailing_schedule']),
                                   minutes):
            continue

        every = await _every(site_id)
        if time.time() - _sent_at.get(site_id, 0.0) < every:
            continue

        if await _today(site_id) >= await _max_day(site_id):
            continue

        # ⚠ Ответы вперёд прочего. Человек ждёт ответа на своё письмо, а
        # рассылка уходит незнакомым: при общем потолке первым уходит то, чего
        # ждут. Возобновление — между ними: собеседник знакомый, но не ждёт.
        letter, table = await _next(site_id, bool(site['reply_send_on']),
                                    bool(site['mailing_send_on']),
                                    bool(site['favorite_mail_send_on']))
        if not letter:
            continue

        _sent_at[site_id] = time.time()

        if await _send(letter, table):
            sent += 1
        else:
            failed += 1

    if sent or failed:
        logger_info(f'[dating] круг отправки: ушло {sent}'
                    + (f', не ушло {failed}' if failed else ''))

    return {'sent': sent, 'failed': failed}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _sites() -> list[dict]:
    """Сайты, где включён хоть один выключатель отправки."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT site_id, mailing_schedule, mailing_send_on, reply_send_on,'
                ' favorite_mail_send_on'
                ' FROM dating_site'
                ' WHERE is_gone = 0 AND (mailing_send_on = 1 OR reply_send_on = 1'
                '                        OR favorite_mail_send_on = 1)'
                ' ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для отправки не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _next(site_id: int, reply_on: bool, mailing_on: bool,
                favorite_on: bool = False) -> tuple:
    """Что отправить следующим: `(письмо, таблица)` либо `(None, '')`.

    ⚠ **Только строки без прежней ошибки.** Неудача оставляет письмо в пуле с
    причиной в `error`; берись круг за него снова, один отказ сайта («слишком
    часто») превратился бы в бесконечный повтор ровно тогда, когда сайт просит
    остановиться. Такое письмо ждёт человека — тот прочтёт причину и нажмёт
    отправку сам.

    ⚠ **Порядок пулов — порядок ожидания.** Ответа ждёт живой человек, написавший
    первым; возобновления не ждёт никто, но собеседник знакомый и однажды отвечал;
    рассылка уходит незнакомым. При общем дневном потолке очередь решает, что
    достанется человеку, а что подождёт до завтра.
    """
    for on, table in ((reply_on, 'dating_mail_reply'),
                      (favorite_on, 'dating_favorite_mail'),
                      (mailing_on, 'dating_mailing')):
        if not on:
            continue

        row = await _oldest(site_id, table)
        if row:
            return row, table

    return None, ''


async def _oldest(site_id: int, table: str) -> dict | None:
    """Самое давнее неотправленное письмо таблицы. Порядок — очередь, а не стопка.

    ⚠ Имя таблицы подставляется в запрос, поэтому берётся **только из белого
    списка** `DATING_MAIL_SEND_TABLES` — того же, что у отправки.
    """
    from src.dating.dating_mail_send import DATING_MAIL_SEND_TABLES

    if table not in DATING_MAIL_SEND_TABLES:
        return None

    # ⚠ **Письма избранным уходят только от анкеты, которая сейчас в сети.**
    #
    # Это возобновление затихшего разговора: человек видит письмо, пока сидит на
    # сайте, и отвечает сразу — а отвечает он девушке, которой там нет. Хуже того,
    # набор пула уже отобрал тех, кто **сейчас** онлайн (`dating_favorite_mail`), и
    # отправка в её отсутствие рассылала бы вчерашний отбор случайным людям.
    #
    # ⚠ **У рассылки и ответов этого условия нет, и это не недосмотр.** Письмо
    # рассылки уходит незнакомому, ответ — тому, кто написал сам и ждёт; ни то, ни
    # другое не обещает, что девушка на сайте прямо сейчас. Здесь обещает: письмо
    # написано под сиюминутный разговор.
    сеть = " AND p.status = 'online'" if table == 'dating_favorite_mail' else ''

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'SELECT m.id, m.site_id, m.profile_id, m.uid, m.body, m.status,'
                f' p.login, p.password, p.password_source'
                f'  FROM {table} m'
                f'  JOIN dating_profile p ON p.id = m.profile_id'
                f" WHERE m.site_id = %s AND m.status = 'draft' AND m.error = ''"
                f'   AND p.enabled = 1{сеть}'
                f' ORDER BY m.id LIMIT 1', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] очередь отправки {table} не спросилась: {e}')

        return None

    return dict(row) if row else None


async def _send(letter: dict, table: str) -> bool:
    """Отправляет одно письмо. Не бросает: круг обходит остальные сайты дальше."""
    from src.uvicorn.routers.dating.dating_site import dating_site_setting

    site_id = int(letter['site_id'])
    base = str(await dating_site_setting(site_id, 'api_base')).strip()
    password = str(await dating_site_setting(site_id, 'api_password')).strip()

    profile = {'id': letter['profile_id'], 'site_id': site_id,
               'login': letter['login'], 'password': letter['password'],
               'password_source': letter['password_source']}

    try:
        await dating_mail_send_one(letter, profile, base, password, table)
    except DatingMailSendError as e:
        # ⚠ Причину храним: она ляжет на карточку, и человек увидит, почему письмо
        # осталось в пуле. Круг к нему больше не вернётся — см. `_next`.
        await dating_mail_send_mark(letter, sent=False, error=str(e), table=table)
        logger_info(f"[dating] {table} #{letter['id']} не ушло: {e}")

        return False
    except Exception as e:
        logger_info(f"[dating] {table} #{letter['id']} сорвалось: {e}")

        return False

    return True


async def _today(site_id: int) -> int:
    """Сколько писем сайта ушло за сегодня — всеми тремя пулами.

    ⚠ Потолок **общий**: письма считает получатель, а не наши таблицы. Раздельные
    потолки означали бы втрое больше писем от одной девушки — при том, что в почту
    они приходят вместе.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                "SELECT (SELECT COUNT(*) FROM dating_mailing"
                "         WHERE site_id = %s AND status = 'sent'"
                '           AND DATE(sent_at) = CURDATE())'
                " + (SELECT COUNT(*) FROM dating_mail_reply"
                "     WHERE site_id = %s AND status = 'sent'"
                '       AND DATE(sent_at) = CURDATE())'
                " + (SELECT COUNT(*) FROM dating_favorite_mail"
                "     WHERE site_id = %s AND status = 'sent'"
                '       AND DATE(sent_at) = CURDATE()) AS ушло',
                (int(site_id), int(site_id), int(site_id)))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] дневной счёт отправки не спросился: {e}')

        # ⚠ Сомнение — в сторону молчания: непрочитанный счётчик означает
        # неизвестный потолок, а лишнее письмо отменить нельзя.
        return 10 ** 6

    return int((row or {}).get('ушло') or 0)


async def _every(site_id: int) -> int:
    """Пауза между письмами, в секундах, — не короче предела."""
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    every = int(await dating_site_seconds(site_id, DATING_SEND_EVERY_SETTING)
                or DATING_SEND_EVERY_DEFAULT)

    return max(every, DATING_SEND_EVERY_MIN)


async def _max_day(site_id: int) -> int:
    """Сколько писем в сутки разрешено этому сайту."""
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id, DATING_SEND_MAX_DAY_SETTING)
               or DATING_SEND_MAX_DAY_DEFAULT)


def _minutes_now(offset_hours: int) -> int:
    """Минуты от начала местных суток: смены сверяют с часами на стене."""
    local = time.gmtime(time.time() + int(offset_hours) * 3600)

    return local.tm_hour * 60 + local.tm_min
