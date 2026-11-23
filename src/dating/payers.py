"""Платившие нашим девушкам — первыми в очереди рассылки и инвайтов.

Владелец 23.11.2026: «что можно сделать с payers — 1», то есть приоритет.
Платящих **вообще** сайт не показывает ни лентой, ни панелью агентства: в записи
человека нет признака оплаты, а у панели Ladadate нет раздела «мужчины в сети».
Есть только список `GET /users/payers` под анкетой — «у кого был хоть один платёж
с этой девушкой». Замер на проде: 36 человек на двадцать анкет, из них в сети 3 при
187 в ленте «Все онлайн».

## Что делается

Раз в час (`PAYERS_EVERY_SEC`) круг ленты спрашивает список у каждой анкеты сайта,
которая сейчас в сети (вход API под спящей стоил бы лишних входов), складывает
номера в одно множество Redis на сайт — и набор пула ставит этих людей **сразу
после** отмеченных человеком (`mailing._by_priority`).

⚠⚠ **Это порядок, а не флаг `priority`.** Галочка «Приоритет» значит для
инвайтов больше, чем «раньше»: «этому пишут все свободные девушки». Платившего
одной девушке так звать всеми анкетами владелец не просил — поэтому платящий идёт
вперёд, но поле `priority` в записи не ставится.

⚠ **Множество, а не колонка.** Признак пересчитывается целиком каждый час, и
ушедший из списка уходит из него сам — колонка в `companion` требовала бы снимать
отметку отдельным проходом. Пропал Redis — порядок просто прежний.
"""

import time

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get, redis_text

PAYERS_KEY = 'dating:payers:{site_id}'

# Как часто пересобираем. Платёж — событие редкое (174 за месяц на всё агентство),
# а каждый пересбор — вход API под каждой анкетой в сети.
PAYERS_EVERY_SEC = 3600

# Сколько живёт множество. ⚠ Дольше срока пересбора: одна неудача сайта не должна
# стирать порядок до следующего часа.
PAYERS_KEEP_SEC = 6 * 3600

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
    """Пора ли пересобрать платящих сайта."""
    return time.time() - _synced_at.get(int(site_id), 0) >= PAYERS_EVERY_SEC


async def payers_sync(site_id: int) -> int:
    """Пересобрать платящих сайта. Возвращает, сколько их. Не бросает.

    ⚠ Отметка времени ставится **до** похода: неудача не должна превращаться в
    поход каждую минуту — та же беда стоила блокировки IP (`online_round.py`).

    ⚠ Ни одна анкета не ответила — множество **не трогаем**: пустой ответ от
    упавшего сайта стёр бы порядок, который был верен час назад.
    """
    from src.dating.people_api import people_api_payers
    from src.dating.site_setting import site_setting
    from src.dating.ready import password_pick

    site_id = int(site_id)
    _synced_at[site_id] = time.time()

    base = str(await site_setting(site_id, 'api_base')).strip()
    if not base:
        return 0
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

    if not answered:
        return 0

    try:
        key = PAYERS_KEY.format(site_id=site_id)
        pipe = redis_conn_get().pipeline()
        pipe.delete(key)
        if found:
            pipe.sadd(key, *found)
            pipe.expire(key, PAYERS_KEEP_SEC)
        pipe.execute()
    except Exception as e:
        logger_info(f'[dating] платившие сайта #{site_id} не легли: {e}')

        return 0

    logger_info(f'[dating] платившие сайта #{site_id}: {len(found)} '
                f'(ответили {answered} из {len(girls)} анкет)')

    return len(found)
