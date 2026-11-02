"""Вход под анкетой **обменом токена**, без браузера.

Панель агентства умеет входить под анкетой сама: у каждой строки списка стоит
кнопка «Логин», и она не ссылка и не форма, а вызов JS-функции. Внутри — два
обычных GET:

    GET  {панель}/autologinsettoken.php?id={номер}   → {"token": "…"}
    GET  {сайт}/autologin.php?token={токен}          → сессия анкеты

## ⚠⚠ Зачем это вместо сценария

Сценарий входа через браузер работает — он написан и проверен, — но каждый его
заход начинается **входом в панель**, а панель считает попытки и на лишней молча
перестаёт пускать. Замер 02.11.2026: за час проб накопился 61 вход, после чего
панель перестала отвечать на `billing.php`, и прогон вставал, не дойдя до кнопки.
Той же бедой 28.10 легла лента Step2Love на несколько часов.

Здесь входа в панель нет вовсе: её сессию держит круг (`panel_round.py`), а мы
только ходим по ней. Секунды против минуты и ни одной лишней попытки.

## ⚠ Что это даёт

`clientSessionID` — тот самый cookie, который присутствие подставляет в
`{cookie:clientSessionID}`. Без него heartbeat не бьёт вовсе: на проде
02.11.2026 состояние было у одной анкеты из десяти, и в чате сидела ровно она.
"""

import json
import time

import httpx

from http_.http_pool import http_pool_transport
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.panel_fetch import DatingPanelFetchError, panel_fetch_html

# Где панель выдаёт токен и где сайт меняет его на сессию.
#
# ⚠ Пути — из самой страницы панели (`autologinsettoken.js`), а не придуманы:
# функция кнопки лежит отдельным файлом и читается целиком.
LOGIN_TOKEN_PATH = '/autologinsettoken.php'
LOGIN_TOKEN_ENTER = '{base}/autologin.php?token={token}'

# Сколько ждём каждый из двух запросов. Оба — обычный HTTP, и минута здесь
# заведомо больше правды: измеренный обмен укладывается в секунду.
LOGIN_TOKEN_TIMEOUT = 30.0

# Сколько живёт записанное состояние, если сайт не сказал срока. Сутки: cookie
# сессии сайт гасит раньше, а запись — лишь наша копия, и её переживший срок
# ничего не ломает (удар просто не пройдёт, и анкета пойдёт на повторный вход).
LOGIN_TOKEN_KEEP_SEC = 86400

# Сайты, чья панель входит под анкетой этой парой запросов.
#
# ⚠ Списком, а не одним именем: второй такой сайт допишется сюда строкой,
# и ни одного `if` в коде это не потребует.
LOGIN_TOKEN_SITES = ('step2love',)


class DatingLoginTokenError(Exception):
    """Войти обменом токена не вышло. Текст — то, что покажут человеку."""


async def login_token_run(profile: dict, base: str) -> dict:
    """Входит под анкетой обменом токена и кладёт ей `storage_state`.

    Args:
        profile: строка `profile`; нужны `id`, `site_id` и номер на сайте.
        base: корень сайта знакомств (`https://mystep2love.com`).

    Returns:
        dict: `status` — `success` либо `error`, `error` — чем кончилось,
        `cookies` — сколько cookie получено.

    Raises:
        DatingLoginTokenError: панель не выдала токен либо сайт не признал его.

    ⚠ **Номер анкеты на сайте обязателен**, и берётся он из карточки (ключ `id`),
    а не из логина: кнопка панели ищется именно по нему, и без него входить не
    под кого.
    """
    site_id = int(profile.get('site_id') or 0)
    uid = _uid_of(profile)
    if not uid:
        raise DatingLoginTokenError('у анкеты нет номера на сайте: панель входит '
                                    'по нему, и найти её строку нечем')

    token = await _token(site_id, uid)
    jar = await _enter(base, token)
    if not jar:
        raise DatingLoginTokenError('сайт не отдал ни одного cookie: токен не принят')

    await _state_save(int(profile.get('id') or 0), jar)

    return {'status': 'success', 'error': '', 'cookies': len(jar)}


def login_token_ready(presence: dict | None) -> bool:
    """Умеет ли этот сайт вход обменом токена.

    ⚠ Признак — **драйвер сайта**, а не наличие панели: панель есть у всех
    агентских сайтов, а кнопка «Логин» с этой функцией — особенность Step2Love.
    Появится второй такой сайт — он назовётся тем же драйвером либо добавит сюда
    своё имя; чего делать не надо, так это пробовать вслепую у всех.
    """
    return str((presence or {}).get('driver') or '').lower() in LOGIN_TOKEN_SITES



# ── Приватное ────────────────────────────────────────────────────────────────

def _uid_of(profile: dict) -> str:
    """Номер анкеты на сайте — из карточки. Пусто — номера не знаем."""
    from src.dating.json_column import json_column_load

    card = json_column_load(profile.get('profile')) or {}

    return str(card.get('id') or '').strip()


async def _token(site_id: int, uid: str) -> str:
    """Токен разового входа у панели. Пусто не возвращает — бросает.

    ⚠ Идём **по удерживаемой сессии панели** (`panel_fetch`), своего входа не
    делаем: он здесь и был бы той самой лишней попыткой, за которую панель
    перестаёт пускать.
    """
    try:
        raw = await panel_fetch_html(site_id, LOGIN_TOKEN_PATH, {'id': uid})
    except DatingPanelFetchError as e:
        raise DatingLoginTokenError(f'панель не ответила: {e}') from e

    try:
        token = str((json.loads(raw) or {}).get('token') or '').strip()
    except ValueError as e:
        raise DatingLoginTokenError(
            f'панель ответила не JSON: {str(raw)[:120]}') from e

    if not token:
        raise DatingLoginTokenError('панель не выдала токен — возможно, анкету '
                                    'сняли с ведения')

    return token


async def _enter(base: str, token: str) -> list:
    """Меняет токен на сессию сайта. Возвращает cookie списком.

    ⚠ Клиент разовый, транспорт общий (`http_pool`): cookie у каждой анкеты свои,
    а `aclose()` унёс бы транспорт у всех — то же правило, что у `site_session`.
    """
    url = LOGIN_TOKEN_ENTER.format(base=str(base or '').rstrip('/'), token=token)
    client = httpx.AsyncClient(timeout=LOGIN_TOKEN_TIMEOUT, follow_redirects=True,
                               transport=http_pool_transport())
    try:
        await client.get(url)
    except Exception as e:
        raise DatingLoginTokenError(f'сайт не принял токен: {e}') from e

    return list(client.cookies.jar)


async def _state_save(profile_id: int, jar: list) -> None:
    """Кладёт состояние анкете в том же виде, что и браузерный вход.

    ⚠⚠ Форма — `storage_state` Playwright, и это не прихоть: её читает
    `http_heartbeat_cookies`, отбирая cookie по домену. Запиши мы свой формат —
    присутствие молча не нашло бы ни одного cookie, то есть анкета осталась бы
    вне чата при успешном с виду входе.
    """
    state = {'cookies': [{'name': one.name, 'value': one.value,
                          'domain': one.domain, 'path': one.path or '/',
                          'expires': float(one.expires
                                           or time.time() + LOGIN_TOKEN_KEEP_SEC),
                          'httpOnly': False, 'secure': bool(one.secure),
                          'sameSite': 'Lax'} for one in jar],
             'origins': []}

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET storage_state = %s, status = %s,'
                         '       last_seen_at = NOW(), last_error = %s'
                         ' WHERE id = %s',
                         (json.dumps(state, ensure_ascii=False), 'online', '',
                          int(profile_id)))
        await db.connection.commit()

    logger_info(f'[dating] анкета #{profile_id}: вход обменом токена, '
                f'cookie {len(jar)}')
