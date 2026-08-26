"""Профили: сама учётка и её собеседницы — с сайта в кэш.

Страница показывает фотографию, имя и ID анкеты, а рядом — собеседниц с их
фотографиями и числом сообщений. Тянуть это с сайта на каждую перерисовку нельзя:
список собеседниц приходит ответом в сотни килобайт, и он один на всех.

Поэтому профили кэшируются (`dating_profile.profile`, `dating_contact`), а сайт
остаётся хозяином данных: обновление зовётся проверкой и опросом, а не показом.

Что откуда брать — настройка сайта (`presence.profile` и `presence.contacts`),
как и остальные ручки: у каждого сайта свои, и зашивать их в Python значило бы
править код ради нового сайта.
"""

import json

import httpx

from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_presence import DATING_PRESENCE_TIMEOUT, DATING_PRESENCE_UA

# Сколько собеседниц держим: список сайта отдаёт сотни, а на странице читаются
# те, с кем есть переписка.
DATING_PROFILE_CACHE_CONTACT_LIMIT = 200


async def dating_profile_cache_refresh(profile: dict, presence: dict) -> dict:
    """Обновляет профиль учётки и её собеседниц из ответов сайта."""
    cookies = _cookies(profile, presence)
    if not cookies:
        return {'profile': False, 'contacts': 0, 'error': 'вход не выполнен'}

    saved_profile, contacts = False, 0
    try:
        async with httpx.AsyncClient(timeout=DATING_PRESENCE_TIMEOUT, cookies=cookies,
                                     follow_redirects=True,
                                     headers={'User-Agent': DATING_PRESENCE_UA,
                                              # Тело шагов — форма (`mode=…`), и без
                                              # этого заголовка сайт его не читает.
                                              'Content-Type': 'application/x-www-form-urlencoded',
                                              **(presence.get('headers') or {})}) as client:
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
    response = await (client.get(url) if str(step.get('method', 'GET')).upper() == 'GET'
                      else client.post(url, content=str(step.get('body') or '')))
    data = _json(response)
    # Поле названо настройкой — значит его отсутствие это «сайт анкету не дал», а
    # не «возьмём что есть». Иначе отказ (`{"error":…,"success":false}`) прошёл бы
    # как анкета и затёр бы ту, что прочитана со страницы кабинета.
    field = str(step.get('field') or '').strip()
    user = data.get(field) if field else data
    if not isinstance(user, dict) or not user:
        return False

    profile = {'id': user.get('id') or profile.get('login'),
               'name': user.get('firstName') or user.get('name') or '',
               'photo': _photo(user, presence),
               'age': user.get('age') or 0,
               'city': user.get('city') or user.get('from') or '',
               'about': user.get('about') or user.get('description') or ''}

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET profile = %s WHERE id = %s',
                         (json.dumps(profile, ensure_ascii=False), int(profile['id'])))
        await db.connection.commit()

    return True


async def _contacts_load(client, profile: dict, presence: dict) -> int:
    """Собеседницы: имя, фотография, возраст, город, статус онлайн."""
    step = presence.get('contacts')
    if not step:
        return 0

    url = str(presence.get('base', '')).rstrip('/') + str(step.get('path') or '')
    response = await (client.post(url, content=str(step.get('body') or ''))
                      if step.get('body') else client.get(url))
    try:
        data = response.json()
    except Exception:
        return 0

    items = data if isinstance(data, list) else (data.get(str(step.get('field') or 'itemArray')) or [])
    if not isinstance(items, list):
        return 0

    saved = 0
    async with mysql_get_db_async() as db:
        for item in items[:DATING_PROFILE_CACHE_CONTACT_LIMIT]:
            if not isinstance(item, dict):
                continue
            values = _contact_values(item, int(profile['id']), presence)
            if values is None:
                continue
            await db.execute(
                'INSERT INTO dating_contact (profile_id, uid, name, photo, age, city, is_online) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s) '
                'ON DUPLICATE KEY UPDATE name=VALUES(name), photo=VALUES(photo), '
                '  age=VALUES(age), city=VALUES(city), is_online=VALUES(is_online)',
                values)
            saved += 1
        await db.connection.commit()

    return saved


def _contact_values(item: dict, profile_id: int, presence: dict) -> tuple | None:
    """Строка `dating_contact` из ответа сайта. `None` — записывать нечего.

    Вынесено из запроса отдельно, чтобы правила разбора проверялись без сети и
    базы: цена ошибки здесь — молча битые данные у всех собеседниц разом.
    """
    # Ключ таблицы — пара `profile_id, uid`, и пустой `uid` склеил бы всех
    # безымянных в одну строку.
    uid = str(item.get('id') or item.get('ladyId') or '').strip()
    if not uid:
        return None

    # Через `_photo`, а не сырьём: сайт отдаёт фотографию то полным адресом на
    # CDN, то путём от своего корня, и второй вариант страница подставляет в
    # `<img src>` как есть — браузер достроил бы его до адреса панели.
    return (profile_id, uid[:32], str(item.get('firstName') or '')[:190],
            _photo(item, presence)[:500], int(item.get('age') or 0),
            str(item.get('from') or '')[:190], 1 if item.get('isOnline') else 0)


def _photo(user: dict, presence: dict) -> str:
    """Адрес фотографии: сайт отдаёт то полный, то относительный."""
    photo = str(user.get('url') or user.get('photo') or '').strip()
    if photo.startswith('http') or not photo:
        return photo

    return str(presence.get('base', '')).rstrip('/') + '/' + photo.lstrip('/')


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
