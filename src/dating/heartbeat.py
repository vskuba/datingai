"""Присутствие анкеты на сайте: кого держать в сети и чем.

Связка между общим движком (`http_/http_heartbeat.py`) и нашими данными. Движок
знает, **как** ударить; здесь решается, **кого** и **по какой настройке**.

## ⚠ Это не чат и от чата не зависит

Разделение стоило разбирательства, и путать их дорого:

| | Чем держится | Зачем |
|--|--------------|-------|
| «в сети» | HTTP-удар по таймеру | собеседники видят анкету в списке онлайн |
| «в чате» | вебсокет (`chat_hold`) | сообщения приходят живыми, а не в «пропущенные» |

Первое нужно **всем** сайтам, второе — только тем, у кого чат на вебсокете. У
Ladadate его нет вовсе: сайт на jQuery, ни одной сокетной библиотеки (проверено
перехватом и разбором его скриптов). Пока присутствие жило внутри держателя
сокетов, такие сайты не могли быть в сети в принципе — держателю нечего было
держать, и до удара дело не доходило.

Отсюда: свой круг, своя настройка, свои ручки. Сокет остаётся частным случаем
одного сайта.

## Откуда настройка

Своя, в `site_.settings` под ключом `heartbeat` — JSON в формате движка.
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

from src.dating.json_column import json_column_load
from src.dating.proxy import proxy_enabled
from src.dating.site_setting import site_seconds, site_setting

# Наша настройка сайта: JSON в формате движка.
HEARTBEAT_SETTING = 'heartbeat'

# Как часто бить, если у сайта не задано. Полминуты: сайты гасят присутствие через
# одну-две минуты тишины, и минутный период оставлял бы анкету мигающей.
HEARTBEAT_EVERY_DEFAULT = 30

# Настройка сайта, задающая период.
HEARTBEAT_EVERY_SETTING = 'heartbeat_every'

# Жёсткий потолок частоты. ⚠ Страхует не нас, а сайт: настройку правят руками, и
# «5» превратило бы двести анкет в сорок запросов в секунду с одного адреса.
HEARTBEAT_EVERY_MIN = 15


async def heartbeat_config(site_id: int) -> dict:
    """Настройка присутствия сайта в формате движка. Пустой словарь — не настроено.

    Порядок один и тот же везде: **своя настройка старше соседской**. Так же
    решается транспорт отправки (`ready_transport`) — сосед описывает сайт,
    но как с ним работать, решает наша сторона.
    """
    own = json_column_load(await site_setting(site_id,
                                                     HEARTBEAT_SETTING))
    if own:
        return own

    return _from_neighbour(await _presence_of(site_id))


async def heartbeat_every(site_id: int) -> int:
    """Период удара в секундах — не чаще потолка."""
    every = int(await site_seconds(site_id, HEARTBEAT_EVERY_SETTING)
                or HEARTBEAT_EVERY_DEFAULT)

    return max(every, HEARTBEAT_EVERY_MIN)


async def heartbeat_sessions(site_id: int, profile_ids: list = None) -> dict:
    """Сессии анкет сайта: `{номер анкеты: cookie}`.

    Отдаются только те, у кого есть чем ходить: без `storage_state` анкета в сеть
    не выйдет, и слать за неё удар некуда.

    ⚠ **Cookie отбираются по домену сайта** (это делает движок). В состоянии лежат
    и чужие — аналитика, CDN, — а уехав скопом, они выдают клиент, который слишком
    много знает.

    Args:
        profile_ids: сузить до этих анкет. Пусто — все включённые с расписанием.
    """
    config = await heartbeat_config(site_id)
    if not http_heartbeat_ready(config):
        return {}

    sql = ('SELECT id, storage_state, proxy_url FROM profile'
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
        cookies = http_heartbeat_cookies(json_column_load(row['storage_state']), base)
        if cookies:
            found[int(row['id'])] = cookies

    return found


async def heartbeat_proxies(site_id: int, profile_ids: list = None) -> dict:
    """Выходы анкет сайта: `{номер анкеты: адрес прокси}`.

    ⚠ **Отдельным вопросом, а не полем в сессиях.** Форму `{id: cookie}` читает
    общий модуль ударов, и приделывать к ней второе значение значило бы менять
    сигнатуру в общей библиотеке ради одного потребителя. Круг спрашивает оба
    списка и группирует анкеты по выходу сам.

    Анкеты без выхода приходят с пустой строкой — они пойдут прямым адресом
    сервера, и решает, допустимо ли это, круг (`proxy_required`).

    ⚠ Читается **кэш** (`profile_.proxy_url`), а не реестр соседа: удар
    идёт каждые полминуты по всем анкетам, и поход к соседу на каждую означал бы,
    что его перезапуск гасит присутствие всей установки.

    ⚠⚠ **Сайт выходами не пользуется — карта пустая, и в базу мы не идём.** Кэш в
    колонке переживает выключение (её чистят на выключателе, но строка могла
    остаться от прежней жизни сайта), а удары эту колонку читают напрямую, минуя
    `proxy_url`. Не спроси мы выключатель здесь — присутствие продолжало бы
    ходить через канал, которого для сайта уже нет.
    """
    if not await proxy_enabled(int(site_id)):
        return {}

    sql = 'SELECT id, proxy_url FROM profile WHERE site_id = %s'
    args = [int(site_id)]

    if profile_ids:
        sql += ' AND id IN (%s)' % ', '.join(['%s'] * len(profile_ids))
        args.extend(int(one) for one in profile_ids)

    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, tuple(args))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] выходы для присутствия не спросились: {e}')

        return {}

    return {int(row['id']): str(row['proxy_url'] or '') for row in rows or []}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _presence_of(site_id: int) -> dict:
    """Описание сайта: своё, а соседское — запасное. Пусто — работать нечем.

    ⚠ **Ходить к соседу здесь больше не обязательно**: описание переехало в нашу
    настройку сайта (`site_presence.py`). Сайт со своей настройкой его не
    спрашивает вовсе; у сайта без настройки всё как было.
    """
    from src.dating.site_presence import site_presence

    return await site_presence(int(site_id))


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
