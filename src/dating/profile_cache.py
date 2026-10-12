"""Профили: сама учётка и её собеседницы — с сайта в кэш.

Страница показывает фотографию, имя и ID анкеты, а рядом — собеседниц с их
фотографиями и числом сообщений. Тянуть это с сайта на каждую перерисовку нельзя:
список собеседниц приходит ответом в сотни килобайт, и он один на всех.

Поэтому профили кэшируются (`profile_.profile`, `dating_contact`), а сайт
остаётся хозяином данных: обновление зовётся проверкой и опросом, а не показом.

Что откуда брать — настройка сайта (`presence.profile` и `presence.contacts`),
как и остальные ручки: у каждого сайта свои, и зашивать их в Python значило бы
править код ради нового сайта.

**Как называются поля в ответе — знает драйвер сайта** (`site_profile.py`), а не
этот модуль. Здесь остаётся наша механика: сходить по адресу, разложить по
таблицам, не уронить остальное на одной кривой записи.
"""

import json

import time

import httpx

from http_.http_pool import http_pool_transport
from mysql_.mysql_ import mysql_get_db_async

from src.dating.presence import DATING_PRESENCE_TIMEOUT, DATING_PRESENCE_UA
from src.dating.profile_card import profile_card_merge
from src.dating.proxy import proxy_url
from src.dating.site.site_registry import site_registry_profile
from src.dating.people import people_name_case

# Сколько собеседниц держим: список сайта отдаёт сотни, а на странице читаются
# те, с кем есть переписка.
PROFILE_CACHE_CONTACT_LIMIT = 200


async def profile_cache_refresh(profile: dict, presence: dict) -> dict:
    """Обновляет профиль учётки и её собеседниц из ответов сайта."""
    cookies = _cookies(profile, presence)
    if not cookies:
        return {'profile': False, 'contacts': 0, 'error': 'вход не выполнен'}

    saved_profile, contacts = False, 0
    try:
        # Клиент разовый — куки-то на анкету, и в общем клиенте они потекли бы
        # между учётками; соединения общие (`http_pool.py`). Не закрываем:
        # с клиентом ушёл бы и общий транспорт.
        client = httpx.AsyncClient(timeout=DATING_PRESENCE_TIMEOUT, cookies=cookies,
                                   follow_redirects=True,
                                   headers={'User-Agent': DATING_PRESENCE_UA,
                                            # Тело шагов — форма (`mode=…`), и без
                                            # этого заголовка сайт его не читает.
                                            'Content-Type': 'application/x-www-form-urlencoded',
                                            **(presence.get('headers') or {})},
                                   # Выход анкеты, а не общий: сайт считает учётки
                                   # по внешнему адресу (`proxy.py`).
                                   transport=http_pool_transport(await proxy_url(profile)))
        saved_profile = await _profile_load(client, profile, presence)
        contacts = await _contacts_load(client, profile, presence)
    except Exception as e:
        return {'profile': saved_profile, 'contacts': contacts, 'error': f'{type(e).__name__}: {e}'}

    return {'profile': saved_profile, 'contacts': contacts, 'error': ''}


async def _profile_load(client, profile: dict, presence: dict) -> bool:
    """Анкета самой учётки: имя, фотография, идентификатор."""
    step = presence.get('profile')
    if not step:
        return False

    url = str(presence.get('base', '')).rstrip('/') + str(step.get('path') or '')
    method = str(step.get('method', 'GET')).upper()
    started = time.monotonic()
    response = await (client.get(url) if method == 'GET'
                      else client.post(url, content=str(step.get('body') or '')))
    await _note(profile, method, str(step.get('path') or ''), response, started)
    data = _json(response)
    # Поле названо настройкой — значит его отсутствие это «сайт анкету не дал», а
    # не «возьмём что есть». Иначе отказ (`{"error":…,"success":false}`) прошёл бы
    # как анкета и затёр бы ту, что прочитана со страницы кабинета.
    field = str(step.get('field') or '').strip()
    user = data.get(field) if field else data
    if not isinstance(user, dict) or not user:
        return False

    # `card`, а не `profile`: **это разные вещи, и путать их нельзя.** `profile` —
    # учётка в нашей базе, `card` — анкета, прочитанная с сайта; так они зовутся и
    # на страницах панели (`.profile*` против `.card*`), и в `agent_ensure`.
    #
    # Здесь на этом уже обожглись: словарь анкеты присваивался в ту же переменную
    # `profile`, и `WHERE id` получал идентификатор **с сайта** вместо номера нашей
    # строки. Кэш уходил в чужую строку или в никуда, а функция возвращала
    # «сохранено»; когда же сайт не давал `id`, в `int()` попадал логин, и
    # обновление падало целиком. Живых анкет в кэше не было ни одной — только
    # посеянные.
    card = site_registry_profile(presence).card_parse(user, str(profile.get('login') or ''))

    # ⚠⚠ Слиянием, а не заменой. Прежде здесь стоял `UPDATE profile SET profile`
    # целиком: он затирал вписанное человеком и **выбрасывал ключи**, которых сайт
    # не прислал. Правило живёт одной дверью на всех писателей со стороны сайта.
    await profile_card_merge(int(profile['id']), card, 'кэш анкеты не записался')

    return True


async def _contacts_load(client, profile: dict, presence: dict) -> int:
    """Собеседницы: имя, фотография, возраст, город, статус онлайн."""
    step = presence.get('contacts')
    if not step:
        return 0

    url = str(presence.get('base', '')).rstrip('/') + str(step.get('path') or '')
    started = time.monotonic()
    response = await (client.post(url, content=str(step.get('body') or ''))
                      if step.get('body') else client.get(url))
    await _note(profile, 'POST' if step.get('body') else 'GET',
                str(step.get('path') or ''), response, started)
    try:
        data = response.json()
    except Exception:
        return 0

    items = data if isinstance(data, list) else (data.get(str(step.get('field') or 'itemArray')) or [])
    if not isinstance(items, list):
        return 0

    site = site_registry_profile(presence)
    saved = 0
    async with mysql_get_db_async() as db:
        for item in items[:PROFILE_CACHE_CONTACT_LIMIT]:
            if not isinstance(item, dict):
                continue
            values = _contact_values(item, int(profile['id']), site)
            if values is None:
                continue
            await db.execute(
                'INSERT INTO contact (profile_id, uid, name, photo, age, city, is_online) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s) '
                'ON DUPLICATE KEY UPDATE name=VALUES(name), photo=VALUES(photo), '
                '  age=VALUES(age), city=VALUES(city), is_online=VALUES(is_online)',
                values)
            saved += 1
        await db.connection.commit()

    return saved


def _contact_values(item: dict, profile_id: int, site) -> tuple | None:
    """Строка `dating_contact` из ответа сайта. `None` — записывать нечего.

    Разбор у драйвера, здесь — раскладка по колонкам в порядке `INSERT`. Порядок
    и есть смысл этой функции: перепутанные местами имя и город не уронят ничего,
    они просто окажутся не в тех колонках у всех собеседниц разом.
    """
    row = site.contact_parse(item)
    if row is None:
        return None

    return (profile_id, row['uid'], people_name_case(row['name']), row['photo'],
            row['age'], row['city'], row['online'])


def _cookies(profile: dict, presence: dict) -> dict:
    state = profile.get('storage_state')
    if isinstance(state, str):
        try:
            state = json.loads(state or '{}')
        except Exception:
            return {}
    host = str(presence.get('base', '')).split('//')[-1].split('/')[0].replace('www.', '')

    return {str(c['name']): str(c['value']) for c in (state or {}).get('cookies', [])
            if host in str(c.get('domain') or '')}


def _json(response) -> dict:
    try:
        data = response.json()
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


async def _note(profile: dict, method: str, path: str, response, started: float) -> None:
    """Строка в журнал запросов: кабинет — тоже дверь наружу.

    ⚠ Её легко не заметить: страница кабинета читается редко и выглядит «своим»
    запросом. Но идёт она к тому же сайту, под той же cookie и через тот же выход,
    и считается сайтом наравне с прочими.
    """
    from src.dating.request import (REQUEST_CABINET,
                                           request_ms, request_write)

    status = int(getattr(response, 'status_code', 0) or 0)
    # ⚠ Путь со слешем: в настройке сайта его пишут и так, и так, а в журнале
    # `GET userchatmessages` читается как оборванная строка.
    shown = '/' + str(path or '').lstrip('/')
    await request_write(
        door=REQUEST_CABINET, profile_id=int(profile.get('id') or 0),
        site_id=int(profile.get('site_id') or 0), method=method, path=shown,
        status=status, ok=status < 400,
        error='' if status < 400 else f'сайт ответил {status}',
        ms=request_ms(started), proxy_ip=str(profile.get('proxy_ip') or ''))
