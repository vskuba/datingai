"""Круг присутствия: анкеты видны в сети, пока идёт их смена.

Раз в `DATING_HEARTBEAT_ROUND_TICK_SEC` обходит сайты с настроенным присутствием и
бьёт за все анкеты, которым сейчас положено быть онлайн. Один удар на анкету, все
удары одним клиентом — устройство и цена описаны в `docs-common/http_heartbeat.md`.

## ⚠ Круг свой, а не внутри держателя сокетов

Прежде присутствие держал `dating_chat_hold`, и это связывало его с чатом: сайт без
вебсокета не мог быть в сети в принципе — держателю нечего было держать, до удара
дело не доходило. Между тем «быть в сети» нужно **всем** сайтам, а сокет — только
тем, у кого чат на нём.

Круги теперь два, и они независимы: этот отвечает за «видно в списке онлайн»,
держатель — за «сообщения приходят живыми».

## Кого берём

Анкета включена, у неё есть расписание, оно включено, минута попадает в смену, и
есть чем ходить — `storage_state` от прошлого входа. Пять условий, и все обязательны:

  **`enabled`** — выключенную не ведут вовсе;
  **`schedule_on`** — галочка и есть решение «водить по часам»;
  **минута в смене** — ради этого расписание и заведено;
  **`storage_state`** — без входа удар слать некуда;
  **настройка сайта** — без неё неизвестно, куда бить.

⚠ **Вход круг не делает.** Это дело `dating_schedule_round`: он заводит сессию
сценарием в начале смены. Здесь только поддержание — иначе два круга гоняли бы
браузер наперегонки, а вход стоит минут живого Chromium.
"""

import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from http_.http_heartbeat import (http_heartbeat_client, http_heartbeat_ready,
                                  http_heartbeat_send_many)

from src.dating.dating_heartbeat import (dating_heartbeat_config,
                                         dating_heartbeat_every,
                                         dating_heartbeat_sessions)
from src.dating.dating_json import dating_json_load
from src.dating.dating_schedule import dating_schedule_now

# Как часто круг просыпается. ⚠ Это **не** период удара: бить чаще, чем задано
# сайту, круг не станет — он смотрит, кому пора, и пропускает остальных. Пятнадцать
# секунд, потому что самый частый период — тридцать, и просыпаться реже значило бы
# бить с опозданием до половины срока.
DATING_HEARTBEAT_ROUND_TICK_SEC = 15

# Когда сайт бился в последний раз: `{сайт: время}`. В памяти процесса, а не в
# Redis, и это осознанно: круг живёт внутри смены держателя, перезапуск смены —
# это лишний удар, а не пропущенный. Цена ошибки в одну сторону копеечная.
_beaten: dict[int, float] = {}


async def dating_heartbeat_round_run(offset_hours: int = 0) -> dict:
    """Один круг: ударить за всех, кому сейчас положено быть в сети.

    Args:
        offset_hours: часовое смещение панели (настройка `timezone`). Расписание
            задано местным временем, а сервер живёт по UTC.

    Returns:
        dict: `sites` — сайтов с настройкой, `beaten` — сколько ударов ушло,
        `failed` — сколько не дошло.
    """
    sites = await _sites()
    if not sites:
        return {'sites': 0, 'beaten': 0, 'failed': 0}

    minutes = _minutes_now(offset_hours)

    # ⚠ Клиент один на весь круг, а не на сайт: соединения к тому же хосту
    # переиспользуются, и тысяча анкет проходит одним пулом.
    client = http_heartbeat_client()
    ran = beaten = failed = 0
    try:
        for site_id in sites:
            config = await dating_heartbeat_config(site_id)
            if not http_heartbeat_ready(config):
                continue

            ran += 1
            if not _due(site_id, await dating_heartbeat_every(site_id)):
                continue

            ids = await _on_shift(site_id, minutes)
            if not ids:
                # ⚠ Отметку ставим и здесь: смена может не начаться до вечера, и
                # без отметки круг ходил бы в базу за анкетами каждые пятнадцать
                # секунд весь день.
                _mark(site_id)
                continue

            sessions = await dating_heartbeat_sessions(site_id, ids)
            _mark(site_id)
            if not sessions:
                continue

            done = await http_heartbeat_send_many(client, sessions, config)
            beaten += sum(1 for ok in done.values() if ok)
            failed += sum(1 for ok in done.values() if not ok)
    finally:
        await client.aclose()

    if beaten or failed:
        logger_info(f'[dating] присутствие: сайтов {ran}, ударов {beaten}'
                    + (f', не дошло {failed}' if failed else ''))

    return {'sites': ran, 'beaten': beaten, 'failed': failed}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _sites() -> list[int]:
    """Сайты, на которых есть кого держать в сети.

    ⚠ Отбор идёт **от анкет**, а не от списка сайтов: сайтов у соседа десятки, а
    работают обычно один-два. Спрашивать настройку у каждого значило бы ходить к
    соседу за описанием сайтов, где нет ни одной нашей анкеты.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT DISTINCT site_id FROM dating_profile'
                ' WHERE enabled = 1 AND schedule_on = 1 AND storage_state IS NOT NULL'
                ' ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для присутствия не спросились: {e}')

        return []

    return [int(row['site_id']) for row in rows or []]


async def _on_shift(site_id: int, minutes: int) -> list[int]:
    """Анкеты сайта, у которых сейчас идёт смена.

    ⚠ Расписание разбирается **здесь, а не запросом**: смены лежат JSON-полем, и
    вопрос «попадает ли минута в интервал» SQL не задать. Правило одно на всех —
    `dating_schedule_now`, тот же, по которому зеленеет полоса на странице.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, schedule FROM dating_profile'
                ' WHERE site_id = %s AND enabled = 1 AND schedule_on = 1'
                '   AND storage_state IS NOT NULL ORDER BY id',
                (int(site_id),))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] анкеты для присутствия не спросились: {e}')

        return []

    return [int(row['id']) for row in rows or []
            if dating_schedule_now(dating_json_load(row['schedule']), minutes)]


def _due(site_id: int, every: int) -> bool:
    """Пора ли бить по этому сайту. Первый заход — всегда пора."""
    return time.time() - _beaten.get(int(site_id), 0.0) >= int(every)


def _mark(site_id: int) -> None:
    """Запомнить, что по сайту только что прошлись."""
    _beaten[int(site_id)] = time.time()


def _minutes_now(offset_hours: int) -> int:
    """Минуты от начала **местных** суток: расписание сверяют с часами на стене.

    Тот же расчёт, что у круга расписания: сервер живёт по UTC, а «в сети с 07:00»
    человек понимает буквально. Разойдись эти два места — анкета входила бы по
    одним часам, а в сети держалась по другим.
    """
    local = time.gmtime(time.time() + int(offset_hours) * 3600)

    return local.tm_hour * 60 + local.tm_min
