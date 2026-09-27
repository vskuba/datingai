"""Круг присутствия: анкеты видны в сети, пока идёт их смена.

Раз в `HEARTBEAT_ROUND_TICK_SEC` обходит сайты с настроенным присутствием и
бьёт за все анкеты, которым сейчас положено быть онлайн. Один удар на анкету, все
удары одним клиентом — устройство и цена описаны в `docs-common/http_heartbeat.md`.

## ⚠ Круг свой, а не внутри держателя сокетов

Прежде присутствие держал `chat_hold`, и это связывало его с чатом: сайт без
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

⚠ **Вход круг не делает.** Это дело `schedule_round`: он заводит сессию
сценарием в начале смены. Здесь только поддержание — иначе два круга гоняли бы
браузер наперегонки, а вход стоит минут живого Chromium.
"""

import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from http_.http_heartbeat import (http_heartbeat_client, http_heartbeat_ready,
                                  http_heartbeat_send_many)

from src.dating.heartbeat import (heartbeat_config,
                                         heartbeat_every,
                                         heartbeat_proxies,
                                         heartbeat_sessions)
from src.dating.json_column import json_column_load
from src.dating.request import request_ms
from src.dating.proxy import (proxy_enabled,
                                     proxy_required)
from src.dating.schedule import schedule_now
from src.dating.round import (round_mem_due, round_mem_mark,
                            round_minutes)

# Как часто круг просыпается. ⚠ Это **не** период удара: бить чаще, чем задано
# сайту, круг не станет — он смотрит, кому пора, и пропускает остальных. Пятнадцать
# секунд, потому что самый частый период — тридцать, и просыпаться реже значило бы
# бить с опозданием до половины срока.
HEARTBEAT_ROUND_TICK_SEC = 15

# Когда сайт бился в последний раз: `{сайт: время}`. В памяти процесса, а не в
# Redis, и это осознанно: круг живёт внутри смены держателя, перезапуск смены —
# это лишний удар, а не пропущенный. Цена ошибки в одну сторону копеечная.
_beaten: dict[int, float] = {}


async def heartbeat_round_run(offset_hours: int = 0) -> dict:
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

    minutes = round_minutes(offset_hours)

    # ⚠ **Клиент на выход, а не один на круг.** Раньше он был один: соединения к
    # тому же хосту переиспользовались, и тысяча анкет проходила одним пулом. Но
    # сайт считает учётки по внешнему адресу, и удары всех анкет с одного IP — это
    # ровно то, за что он закрывает вход. Теперь клиент заводится на каждый выход
    # и живёт до конца круга; переиспользование соединений при этом не теряется —
    # оно и имело смысл только внутри одного выхода.
    clients: dict[str, object] = {}
    ran = beaten = failed = 0
    skipped = 0
    try:
        for site_id in sites:
            config = await heartbeat_config(site_id)
            if not http_heartbeat_ready(config):
                continue

            ran += 1
            if not _due(site_id, await heartbeat_every(site_id)):
                continue

            ids = await _on_shift(site_id, minutes)
            if not ids:
                # ⚠ Отметку ставим и здесь: смена может не начаться до вечера, и
                # без отметки круг ходил бы в базу за анкетами каждые пятнадцать
                # секунд весь день.
                _mark(site_id)
                continue

            sessions = await heartbeat_sessions(site_id, ids)
            _mark(site_id)
            if not sessions:
                continue

            # Выходы читаем кэшем и разбиваем сессии по ним: у каждой анкеты
            # свой адрес, а бить надо пачками — по клиенту на адрес.
            proxies = await heartbeat_proxies(site_id, list(sessions))
            # ⚠ Требование выхода — только у сайта, который выходами пользуется.
            # Иначе снятая галочка «использовать выходы» гасила бы присутствие
            # всех его анкет разом: выхода нет ни у кого, а требование включено
            # панелью — оно общее на установку.
            required = (await proxy_required()
                        and await proxy_enabled(site_id))

            groups: dict[str, dict] = {}
            for profile_id, cookies in sessions.items():
                url = proxies.get(profile_id) or ''
                if not url and required:
                    # ⚠ Молча ударить с общего адреса нельзя: именно так лимит
                    # сайта и складывается на одном IP. Пропускаем — «в сети» у
                    # этой анкеты погаснет, и это честный признак беды.
                    skipped += 1
                    continue
                groups.setdefault(url, {})[profile_id] = cookies

            for url, group in groups.items():
                client = clients.get(url)
                if client is None:
                    client = clients[url] = http_heartbeat_client(url)

                started = time.monotonic()
                done = await http_heartbeat_send_many(client, group, config)
                beaten += sum(1 for ok in done.values() if ok)
                failed += sum(1 for ok in done.values() if not ok)

                # ⚠ **Строка на каждый удар, а не на пачку.** Присутствие — самая
                # частая дверь наружу (раз в полминуты на анкету), и именно
                # поэтому его надо видеть поимённо: пропавшая анкета отличается
                # от пропавшего сайта только тем, чьи удары перестали доходить.
                #
                # Длительность у всех в пачке общая: они шли одним залпом, и
                # делить её по одному нечем — `http_heartbeat_send_many` отдаёт
                # исход, а не время каждого.
                await _note(site_id, group, done, url, proxies,
                            request_ms(started), config)
    finally:
        for client in clients.values():
            await client.aclose()

    if beaten or failed or skipped:
        logger_info(f'[dating] присутствие: сайтов {ran}, ударов {beaten}'
                    + (f', не дошло {failed}' if failed else '')
                    + (f', без выхода пропущено {skipped}' if skipped else ''))

    return {'sites': ran, 'beaten': beaten, 'failed': failed, 'skipped': skipped}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _note(site_id: int, group: dict, done: dict, url: str, proxies: dict,
                ms: int, config: dict) -> None:
    """Пишет удары присутствия в журнал запросов — по строке на анкету.

    ⚠ **Адрес выхода берём из карты, а не из строки подключения** (`url` здесь —
    именно она, с паролем канала). В журнал едет увиденный IP, и берётся он у
    анкеты: `heartbeat_proxies` отдаёт строки, а адрес лежит в
    `profile_.proxy_ip`.
    """
    from src.dating.request import (REQUEST_HEARTBEAT,
                                           request_write)

    path = str((config.get('beat') or {}).get('path') or '')
    method = str((config.get('beat') or {}).get('method') or 'POST').upper()
    seen = await _proxy_ips(list(group)) if url else {}

    for profile_id in group:
        ok = bool(done.get(profile_id))
        await request_write(
            door=REQUEST_HEARTBEAT, profile_id=int(profile_id),
            site_id=int(site_id), method=method, path=path,
            # ⚠ Кода ответа у пачки нет: движок ударов отдаёт «дошло или нет», а
            # не сам ответ. Ноль здесь честнее выдуманного `200`.
            status=0, ok=ok, error='' if ok else 'удар не дошёл',
            ms=ms, proxy_ip=str(seen.get(int(profile_id)) or ''))


async def _proxy_ips(profile_ids: list) -> dict:
    """Увиденные адреса анкет: `{номер: IP}`. Пусто — ходят общим адресом."""
    if not profile_ids:
        return {}

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, proxy_ip FROM profile WHERE id IN (%s)'
                % ', '.join(['%s'] * len(profile_ids)),
                tuple(int(one) for one in profile_ids))
            rows = await db.fetchall()
    except Exception:
        return {}

    return {int(row['id']): str(row['proxy_ip'] or '') for row in rows or []}

async def _sites() -> list[int]:
    """Сайты, на которых есть кого держать в сети.

    ⚠ Отбор идёт **от анкет**, а не от списка сайтов: сайтов у соседа десятки, а
    работают обычно один-два. Спрашивать настройку у каждого значило бы ходить к
    соседу за описанием сайтов, где нет ни одной нашей анкеты.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT DISTINCT site_id FROM profile'
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
    `schedule_now`, тот же, по которому зеленеет полоса на странице.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, schedule FROM profile'
                ' WHERE site_id = %s AND enabled = 1 AND schedule_on = 1'
                '   AND storage_state IS NOT NULL ORDER BY id',
                (int(site_id),))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] анкеты для присутствия не спросились: {e}')

        return []

    return [int(row['id']) for row in rows or []
            if schedule_now(json_column_load(row['schedule']), minutes)]


def _due(site_id: int, every: int) -> bool:
    """Пора ли бить по этому сайту. Правило общее — `round_mem_due`."""
    return round_mem_due(_beaten, int(site_id), every)


def _mark(site_id: int) -> None:
    """Запомнить, что по сайту только что прошлись."""
    round_mem_mark(_beaten, int(site_id))


