"""Платившие нашим девушкам — первыми в очереди рассылки и инвайтов.

Владелец 23.11.2026: «что можно сделать с payers — 1», то есть приоритет.
Платящих **вообще** сайт не показывает ни лентой, ни панелью агентства: в записи
человека нет признака оплаты. Есть только платившие **нашим** анкетам.

## Откуда список

1. **Страница «Payments» админ-панели** (`userpayments`) — платежи всем нашим
   анкетам разом, **одним запросом**: за год 365 платежей от 68 человек, одна
   страница без листания, 2,5 секунды (замер 23.11.2026). За «всё время» панель
   не успевает ответить — отсюда окно `PAYERS_DAYS`.
2. **Запасная дорога — API** `GET /users/payers` под каждой анкетой в сети. ⚠ Он
   отвечает «платившие **этой** анкете», списки у девушек разные (0–6 человек,
   вместе 36), и общего списка API не даёт — поэтому вход под каждой. Берётся,
   только когда панели у сайта нет или она не ответила.

Частота — настройка сайта `payers_every` (часы; ноль — не обновлять).

⚠⚠ **Это порядок, а не флаг `priority`.** Галочка «Приоритет» значит для
инвайтов больше, чем «раньше»: «этому пишут все свободные девушки». Платившего
одной девушке так звать всеми анкетами владелец не просил — поэтому платящий идёт
вперёд, но поле `priority` в записи не ставится.

⚠ **Множество Redis, а не колонка.** Признак пересчитывается целиком, и ушедший
из списка уходит из него сам. Пропал Redis — порядок просто прежний.
"""

import re
import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get, redis_text

PAYERS_KEY = 'dating:payers:{site_id}'

# Страница платежей админ-панели агентства.
PAYERS_PANEL_PATH = 'userpayments'

# За сколько дней берём платежи со страницы панели. ⚠ Не «всё время»: такой
# запрос панель не успевает отдать (таймаут, замер 23.11.2026), а год — 2,5 с.
PAYERS_DAYS = 365

# Сколько живёт множество сверх срока пересбора: одна неудача сайта не должна
# стирать порядок до следующего захода.
PAYERS_KEEP_EXTRA_SEC = 6 * 3600

# Когда сайт пересобирали: `{сайт: время}`. В памяти процесса, как у прочих кругов:
# перезапуск стоит одного лишнего пересбора.
_synced_at: dict[int, float] = {}


def payers_uids(site_id: int) -> set:
    """Номера плативших анкетам сайта. Сбой Redis — пусто: это порядок, не отбор."""
    try:
        got = redis_conn_get().smembers(PAYERS_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] платившие сайта #{site_id} не прочитались: {e}')

        return set()

    return {redis_text(one) for one in got or ()}


async def payers_due(site_id: int) -> bool:
    """Пора ли пересобрать платящих сайта. Ноль в настройке — никогда."""
    every = await _every(site_id)

    return bool(every) and time.time() - _synced_at.get(int(site_id), 0) >= every


async def payers_sync(site_id: int) -> int:
    """Пересобрать платящих сайта. Возвращает, сколько их. Не бросает.

    ⚠ Отметка времени ставится **до** похода: неудача не должна превращаться в
    поход каждую минуту — та же беда стоила блокировки IP (`online_round.py`).

    ⚠ Ни одна дорога не ответила — множество **не трогаем**: пустой ответ от
    упавшего сайта стёр бы порядок, который был верен час назад.
    """
    from src.uvicorn.routers.dating.people import _api_ready

    site_id = int(site_id)
    _synced_at[site_id] = time.time()

    # ⚠ Только сайты на движке Ladadate: страница «Payments» и `/users/payers`
    # есть у него, а панель Luxee или Step2Love получала бы раз в час чужой путь.
    # Признак движка — его API в настройках сайта (`people_source: api`).
    if not await _api_ready(site_id):
        return 0

    found, how = await _from_panel(site_id), 'панель'
    if found is None:
        found, how = await _from_api(site_id), 'API'
    if found is None:
        return 0

    try:
        key = PAYERS_KEY.format(site_id=site_id)
        pipe = redis_conn_get().pipeline()
        pipe.delete(key)
        if found:
            pipe.sadd(key, *found)
            pipe.expire(key, (await _every(site_id)) + PAYERS_KEEP_EXTRA_SEC)
        pipe.execute()
    except Exception as e:
        logger_info(f'[dating] платившие сайта #{site_id} не легли: {e}')

        return 0

    logger_info(f'[dating] платившие сайта #{site_id}: {len(found)} ({how})')

    return len(found)


# ── Приватное ────────────────────────────────────────────────────────────────


async def _every(site_id: int) -> int:
    """Срок пересбора в секундах из настройки сайта (часы)."""
    from src.dating.site_setting import site_seconds

    return int(await site_seconds(site_id, 'payers_every')) * 3600


async def _from_panel(site_id: int) -> set | None:
    """Платившие со страницы «Payments» панели. `None` — панели нет или молчит.

    ⚠ Столбец «From» — плательщик: ссылка ячейки несёт его номер на сайте.
    Шапку сверяем, а не верим месту: сменись вёрстка, номера девушек из «To»
    легли бы в список молча.
    """
    from datetime import date, timedelta

    from src.dating.panel import _url
    from src.dating.panel_fetch import panel_fetch_html, panel_fetch_table

    if not await _url(site_id):
        return None

    today = date.today()
    try:
        html = await panel_fetch_html(site_id, PAYERS_PANEL_PATH, {
            'DateFrom.DateValue': (today - timedelta(days=PAYERS_DAYS)).strftime('%d.%m.%Y'),
            'DateTo.DateValue': today.strftime('%d.%m.%Y')})
    except Exception as e:
        logger_info(f'[dating] платежи панели сайта #{site_id} не прочитались: {e}')

        return None

    head = [re.sub(r'<[^>]+>', '', one).strip()
            for one in re.findall(r'<th[^>]*>(.*?)</th>', html, re.S)]
    if 'From' not in head:
        logger_info(f'[dating] платежи панели сайта #{site_id}: нет столбца «From»')

        return None
    at = head.index('From')

    # ⚠ Номер — последнее число ссылки: таблица отдаёт её то голым номером, то
    # путём `…/userprofileview/808675`.
    found = set()
    for row in panel_fetch_table(html):
        if len(row) > at:
            got = re.search(r'(\d+)\D*$', str(row[at].get('link') or ''))
            if got:
                found.add(got.group(1))

    return found


async def _from_api(site_id: int) -> set | None:
    """Платившие списками API под каждой анкетой в сети. `None` — никто не ответил."""
    from src.dating.people_api import people_api_payers
    from src.dating.ready import password_pick
    from src.dating.site_setting import site_setting

    base = str(await site_setting(site_id, 'api_base')).strip()
    if not base:
        return None
    common = str(await site_setting(site_id, 'api_password')).strip()

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, password, password_source FROM profile'
            " WHERE site_id = %s AND enabled = 1 AND status = 'online'",
            (site_id,))
        girls = [dict(row) for row in await db.fetchall() or []]

    found, answered = set(), 0
    for girl in girls:
        try:
            found |= {str(one['uid']) for one in await people_api_payers(
                girl, base, password_pick(girl, common)) if one.get('uid')}
            answered += 1
        except Exception as e:
            logger_info(f'[dating] платившие анкеты #{girl["id"]} не снялись: {e}')

    return found if answered else None
