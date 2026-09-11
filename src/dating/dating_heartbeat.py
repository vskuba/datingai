"""Присутствие анкеты на сайте: кого держать в сети и чем.

Связка между общим движком (`http_/http_heartbeat.py`) и нашими данными. Движок
знает, **как** ударить; здесь решается, **кого** и **по какой настройке**.

## ⚠ Это не чат и от чата не зависит

Разделение стоило разбирательства, и путать их дорого:

| | Чем держится | Зачем |
|--|--------------|-------|
| «в сети» | HTTP-удар по таймеру | собеседники видят анкету в списке онлайн |
| «в чате» | вебсокет (`dating_chat_hold`) | сообщения приходят живыми, а не в «пропущенные» |

Первое нужно **всем** сайтам, второе — только тем, у кого чат на вебсокете. У
Ladadate его нет вовсе: сайт на jQuery, ни одной сокетной библиотеки (проверено
перехватом и разбором его скриптов). Пока присутствие жило внутри держателя
сокетов, такие сайты не могли быть в сети в принципе — держателю нечего было
держать, и до удара дело не доходило.

Отсюда: свой круг, своя настройка, свои ручки. Сокет остаётся частным случаем
одного сайта.

## Откуда настройка

Своя, в `dating_site.settings` под ключом `heartbeat` — JSON в формате движка.
Наша, а не соседская, потому что присутствие должно работать у сайта, о котором
сосед не знает ничего: браузер нужен один раз, для входа.

⚠ **Старую настройку соседа читаем как запасную** и переводим на ходу. У
Goldenbride описание присутствия живёт в `browser_site.presence` вперемешку с
кадрами чата (`heartbeat`, `status`), и переносить его руками ради переименования
ключей значило бы ронять работающий сайт ради опрятности.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from http_.http_heartbeat import http_heartbeat_cookies, http_heartbeat_ready

from src.dating.dating_json import dating_json_load

# Наша настройка сайта: JSON в формате движка.
DATING_HEARTBEAT_SETTING = 'heartbeat'

# Как часто бить, если у сайта не задано. Полминуты: сайты гасят присутствие через
# одну-две минуты тишины, и минутный период оставлял бы анкету мигающей.
DATING_HEARTBEAT_EVERY_DEFAULT = 30

# Настройка сайта, задающая период.
DATING_HEARTBEAT_EVERY_SETTING = 'heartbeat_every'

# Жёсткий потолок частоты. ⚠ Страхует не нас, а сайт: настройку правят руками, и
# «5» превратило бы двести анкет в сорок запросов в секунду с одного адреса.
DATING_HEARTBEAT_EVERY_MIN = 15


async def dating_heartbeat_config(site_id: int) -> dict:
    """Настройка присутствия сайта в формате движка. Пустой словарь — не настроено.

    Порядок один и тот же везде: **своя настройка старше соседской**. Так же
    решается транспорт отправки (`dating_ready_transport`) — сосед описывает сайт,
    но как с ним работать, решает наша сторона.
    """
    from src.uvicorn.routers.dating.dating_site import dating_site_setting

    own = dating_json_load(await dating_site_setting(site_id,
                                                     DATING_HEARTBEAT_SETTING))
    if own:
        return own

    return _from_neighbour(await _presence_of(site_id))


async def dating_heartbeat_every(site_id: int) -> int:
    """Период удара в секундах — не чаще потолка."""
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    every = int(await dating_site_seconds(site_id, DATING_HEARTBEAT_EVERY_SETTING)
                or DATING_HEARTBEAT_EVERY_DEFAULT)

    return max(every, DATING_HEARTBEAT_EVERY_MIN)


async def dating_heartbeat_sessions(site_id: int, profile_ids: list = None) -> dict:
    """Сессии анкет сайта: `{номер анкеты: cookie}`.

    Отдаются только те, у кого есть чем ходить: без `storage_state` анкета в сеть
    не выйдет, и слать за неё удар некуда.

    ⚠ **Cookie отбираются по домену сайта** (это делает движок). В состоянии лежат
    и чужие — аналитика, CDN, — а уехав скопом, они выдают клиент, который слишком
    много знает.

    Args:
        profile_ids: сузить до этих анкет. Пусто — все включённые с расписанием.
    """
    config = await dating_heartbeat_config(site_id)
    if not http_heartbeat_ready(config):
        return {}

    sql = ('SELECT id, storage_state FROM dating_profile'
           ' WHERE site_id = %s AND enabled = 1 AND storage_state IS NOT NULL')
    args = [int(site_id)]

    if profile_ids:
        sql += ' AND id IN (%s)' % ', '.join(['%s'] * len(profile_ids))
        args.extend(int(one) for one in profile_ids)

    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, tuple(args))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сессии для присутствия не спросились: {e}')

        return {}

    base = str(config.get('base') or '')
    found = {}
    for row in rows or []:
        cookies = http_heartbeat_cookies(dating_json_load(row['storage_state']), base)
        if cookies:
            found[int(row['id'])] = cookies

    return found


# ── Приватное ────────────────────────────────────────────────────────────────

async def _presence_of(site_id: int) -> dict:
    """Описание сайта у соседа. Молчание соседа — пустой словарь, а не отказ.

    Присутствие не должно ложиться оттого, что сосед перезапускается: у сайта со
    своей настройкой он вообще ни при чём, а у старого — круг просто пропустит
    заход и вернётся через полминуты.
    """
    from src.rhapsody.rhapsody_api import rhapsody_api_site_get

    try:
        site = await rhapsody_api_site_get(int(site_id))
    except Exception as e:
        logger_info(f'[dating] описание сайта #{site_id} не пришло: {e}')

        return {}

    return dating_json_load((site or {}).get('presence'))


def _from_neighbour(presence: dict) -> dict:
    """Старое описание соседа → формат движка. Пустое на входе — пустое на выходе.

    ⚠ Переводим **только присутствие**: `base`, `headers` и два шага. Всё
    остальное в этом словаре — про чат (кадры сокета, адрес чата, драйвер), и
    движку оно не нужно ни в каком виде. Тащить его целиком значило бы вернуть ту
    самую связку с чатом, ради разрыва которой модуль и написан.
    """
    if not presence:
        return {}

    config = {'base': presence.get('base') or ''}
    if presence.get('headers'):
        config['headers'] = presence['headers']
    if presence.get('heartbeat'):
        config['beat'] = presence['heartbeat']
    if presence.get('status'):
        config['alive'] = presence['status']
    if presence.get('unread'):
        config['unread'] = presence['unread']

    return config if http_heartbeat_ready(config) else {}
