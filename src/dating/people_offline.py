"""Месячные активные — второй круг адресатов рассылки после ленты «Все онлайн».

Владелец 23.11.2026: «как ещё ускорить рассылку» → «делай». Замер того же дня на
Ladadate: в пуле 126 писем 91 человеку, и **ни одному** писать было нельзя — 94
адресата остывали после отказа «ящик полон», ещё 7 только что получили наше
письмо. Лента «Все онлайн» — 187 человек, и почти всех заваливают письмами все
агентства сразу: удачные наши — около одного в час, а сайт отвечает «полон».

У API есть список `GET /users/offline` — «активные, заходившие за месяц»: 5000
человек (потолок ответа), 0,3 секунды. Письмо — не чат, его читают при следующем
входе, поэтому для рассылки такой человек законный адресат.

⚠⚠ **Список один на сайт.** Под двумя разными анкетами совпали 4995 номеров из
5000 — сайт отдаёт общую выборку, а не «её» людей. Поэтому снимается под одной
анкетой в сети, раз в срок настройки `mailing_offline_every` (часы; ноль —
список не берётся вовсе), и лежит в Redis.

⚠ Только сайты на движке Ladadate: признак — их API в настройках
(`people_source: api`), как у плативших (`payers.py`).
"""

import json
import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get, redis_text

PEOPLE_OFFLINE_KEY = 'dating:people_offline:{site_id}'

# Сколько живёт снимок сверх срока пересбора: одна неудача сайта не должна
# оставлять рассылку без второго круга до следующего захода.
PEOPLE_OFFLINE_KEEP_EXTRA_SEC = 6 * 3600

# Когда сайт пересобирали: `{сайт: время}`.
_synced_at: dict[int, float] = {}


async def people_offline_rows(site_id: int) -> list[dict]:
    """Месячные активные сайта в виде записей ленты. Выключено или сбой — пусто."""
    if not await _every(site_id):
        return []

    try:
        raw = redis_conn_get().get(PEOPLE_OFFLINE_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] месячные активные сайта #{site_id} не прочитались: {e}')

        return []

    try:
        rows = json.loads(redis_text(raw) or '[]')
    except ValueError:
        return []

    return [row for row in rows if isinstance(row, dict) and row.get('uid')]


async def people_offline_due(site_id: int) -> bool:
    """Пора ли пересобрать. Ноль в настройке — никогда."""
    every = await _every(site_id)

    return bool(every) and time.time() - _synced_at.get(int(site_id), 0) >= every


async def people_offline_sync(site_id: int) -> int:
    """Снять список под одной анкетой в сети. Сколько людей. Не бросает.

    ⚠ Отметка ставится **до** похода: неудача не превращается в поход каждую
    минуту. Не ответила ни одна анкета — прежний снимок не трогаем.
    """
    from src.dating.people_api import people_api_offline
    from src.dating.ready import password_pick
    from src.dating.site_setting import site_setting
    from src.uvicorn.routers.dating.people import _api_ready

    site_id = int(site_id)
    _synced_at[site_id] = time.time()

    if not await _api_ready(site_id):
        return 0

    base = str(await site_setting(site_id, 'api_base')).strip()
    common = str(await site_setting(site_id, 'api_password')).strip()

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, password, password_source FROM profile'
            " WHERE site_id = %s AND enabled = 1 AND status = 'online'"
            ' ORDER BY RAND() LIMIT 3', (site_id,))
        girls = [dict(row) for row in await db.fetchall() or []]

    # ⚠ До трёх попыток разными анкетами: у одной может протухнуть вход.
    for girl in girls:
        try:
            rows = await people_api_offline(girl, base, password_pick(girl, common))
        except Exception as e:
            logger_info(f'[dating] месячные активные под анкетой #{girl["id"]}: {e}')
            continue

        try:
            redis_conn_get().set(
                PEOPLE_OFFLINE_KEY.format(site_id=site_id),
                json.dumps(rows, ensure_ascii=False),
                ex=(await _every(site_id)) + PEOPLE_OFFLINE_KEEP_EXTRA_SEC)
        except Exception as e:
            logger_info(f'[dating] месячные активные сайта #{site_id} не легли: {e}')

            return 0

        logger_info(f'[dating] месячные активные сайта #{site_id}: {len(rows)}')

        return len(rows)

    return 0


# ── Приватное ────────────────────────────────────────────────────────────────


async def _every(site_id: int) -> int:
    """Срок пересбора в секундах из настройки сайта (часы)."""
    from src.dating.site_setting import site_seconds

    return int(await site_seconds(site_id, 'mailing_offline_every')) * 3600
