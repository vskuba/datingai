"""Присутствие учётки на сайте знакомств: держим онлайн и читаем счётчики.

**Без браузера.** Замер на живом сайте: вкладка Chromium со страницей стоит ~143 МБ,
и сотня учёток в таком виде — это 14 ГБ, то есть невозможно. Тот же сайт отвечает
обычному HTTP-клиенту с сохранёнными cookie за доли секунды, а двести таких
клиентов занимают 2,3 МБ. Опрос раз в 15 секунд для двухсот учёток — 13 запросов
в секунду и 0,7 КБ/с трафика.

Браузер остаётся для действий, которым нужен настоящий DOM: вход (раз в сутки,
дальше живёт `storage_state`), открытие диалога, ответ в чат. Их делают сценарии
и цепочки — этот модуль их не заменяет и в них не лезет.

**Что дёргать — настройка сайта, а не код.** У каждого сайта свои ручки, и
вписывать их сюда значило бы править Python ради нового сайта. Формат `presence`
в `browser_site`:

    {
      "base": "https://goldenbride.net",
      "headers": {"Referer": "https://goldenbride.net/chat"},
      "heartbeat": {"path": "/goldenbride/services/chat/v5", "body": "mode=setAuthUserOnline"},
      "status":    [{"path": "/goldenbride/services/chat/v26", "body": "mode=update_v2",
                     "auth_field": "auth"},
                    {"path": "/ladymodule/services/rpc", "body": "7|0|10|…",
                     "ok_mark": "//OK", "headers": {"Content-Type": "text/x-gwt-rpc; charset=UTF-8"}}],
      "unread":    {"path": "/goldenbride/services/userApiV2?requestType=LetterStats",
                    "method": "GET", "field": "unreadMail"}
    }

`auth_field` — поле ответа, по которому видно, жив ли вход: сайт отвечает `200` и
гостю тоже, и без этой проверки протухшая учётка выглядела бы онлайн. Где ответ
не JSON — `ok_mark`, метка в тексте.

`status` может быть списком: на одном сайте мужская анкета отвечает JSON-ручкой,
а женская живёт в другом модуле с другим форматом. Подошла любая проверка — вход
жив; перебор идёт до первой удачной.
"""

import json
import time

import httpx

from logging_.logging_ import logger_info

# Браузерный `User-Agent`: сайт отдаёт API тому, кто похож на его же страницу.
DATING_PRESENCE_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')

# Ответ приходит за 0.4-2 секунды; больше десяти ждать нечего — опрос повторится.
DATING_PRESENCE_TIMEOUT = 10.0

# Ошибка сайта в карточку идёт строкой, а не стеком: человек читает её глазами.
DATING_PRESENCE_ERROR_MAX = 500


async def dating_presence_probe(girl: dict, presence: dict) -> dict:
    """Один опрос учётки: держим онлайн, снимаем статус и счётчик.

    Args:
        girl: строка `dating_girl` со `storage_state`.
        presence: настройка сайта (`browser_site.presence`).

    Returns:
        dict: Что записать в карточку:
            status (str): online | offline | error.
            unread (int): непрочитанных на момент опроса; -1 — не спрашивали.
            error (str): причина, если status = error.
            ms (int): сколько занял опрос.
    """
    cookies = _cookies_of(girl.get('storage_state'), presence.get('base', ''))
    if not cookies:
        return _result('offline', error='вход не выполнен: нет cookie')

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=DATING_PRESENCE_TIMEOUT, cookies=cookies,
                                     follow_redirects=True,
                                     headers=_headers(presence)) as client:
            # Heartbeat первым: он и есть «я на сайте». Ответ у него обычно пустой,
            # поэтому смотрим только на то, что запрос состоялся.
            await _call(client, presence.get('base', ''), presence.get('heartbeat'))

            alive, data = await _status_check(client, presence)
            if not alive:
                return _result('offline', error='сайт не признал вход: нужен повторный логин',
                               ms=_ms(started))

            unread = await _unread_get(client, presence)
    except Exception as e:
        return _result('error', error=f'{type(e).__name__}: {e}', ms=_ms(started))

    return _result('online', unread=unread, ms=_ms(started))


async def dating_presence_beat(girl: dict, presence: dict) -> bool:
    """Только heartbeat — «я на сайте», без статуса и счётчиков.

    Нужен держателю сокетов: присутствие в чате сайт считает не по открытому
    соединению, а по этому запросу. Пока его никто не слал, сокет учётки молчал и
    умирал через полторы минуты, а сообщения уходили в «пропущенные».

    Отдельно от `dating_presence_probe`, потому что цена разная: здесь один запрос
    на учётку, там три. Двести учёток раз в 15 секунд — это 13 запросов в секунду
    против сорока.
    """
    step = presence.get('heartbeat')
    if not step:
        return False

    cookies = _cookies_of(girl.get('storage_state'), presence.get('base', ''))
    if not cookies:
        return False

    try:
        async with httpx.AsyncClient(timeout=DATING_PRESENCE_TIMEOUT, cookies=cookies,
                                     follow_redirects=True, headers=_headers(presence)) as client:
            await _call(client, presence.get('base', ''), step)
    except Exception as e:
        logger_info(f'[dating] heartbeat не прошёл: {e}')
        return False

    return True


def dating_presence_ready(presence) -> bool:
    """Настроен ли у сайта опрос: без него карточка честно молчит, а не врёт нулём."""
    data = _json_load(presence)

    return bool(data.get('base') and (data.get('heartbeat') or data.get('status')))


async def _status_check(client: httpx.AsyncClient, presence: dict) -> tuple[bool, dict]:
    """Жив ли вход. Нет настройки статуса — верим heartbeat и считаем, что жив.

    Проверок может быть несколько: у одного сайта мужская анкета отвечает JSON, а
    женская — другим модулем и другим форматом, и одна ручка на всех не годится.
    Подошла любая — вход жив; отказ у остальных при этом ничего не значит.
    """
    steps = presence.get('status')
    if not steps:
        return True, {}

    last = {}
    for step in steps if isinstance(steps, list) else [steps]:
        response = await _call(client, presence.get('base', ''), step)
        data = _json_body(response)
        last = data or last
        if _status_ok(step, response, data):
            return True, data

    return False, last


async def _unread_get(client: httpx.AsyncClient, presence: dict) -> int:
    """Счётчик непрочитанных. Не настроен — -1: «не спрашивали» и «ноль» разные вещи."""
    step = presence.get('unread')
    if not step:
        return -1

    response = await _call(client, presence.get('base', ''), step)
    data = _json_body(response)
    value = data.get(str(step.get('field') or 'unread'))

    return int(value) if str(value).isdigit() else -1


def _status_ok(step: dict, response, data: dict) -> bool:
    """Признал ли сайт вход в этом ответе.

    Три способа, потому что три вида ответов: поле в JSON, метка в тексте (у
    GWT-модулей ответ вообще не JSON) и «раз ответил — значит жив», когда сайт
    гостю просто не отвечает.
    """
    if response is None:
        return False

    mark = str(step.get('ok_mark') or '').strip()
    if mark:
        return mark in (response.text or '')

    field = str(step.get('auth_field') or '').strip()
    if field:
        return bool(data.get(field))

    return response.status_code == 200


async def _call(client: httpx.AsyncClient, base: str, step) -> httpx.Response | None:
    """Один запрос по настройке шага. Нет шага — нечего и звать."""
    if not step:
        return None

    url = str(base).rstrip('/') + str(step.get('path') or '')
    method = str(step.get('method') or ('POST' if step.get('body') else 'GET')).upper()
    # Свои заголовки шага: GWT-модуль отвечает только на свой тип содержимого и
    # свою пермутацию, и общие заголовки сайта тут не подходят.
    extra = step.get('headers') if isinstance(step.get('headers'), dict) else None
    headers = {str(k): str(v) for k, v in (extra or {}).items()} or None

    if method == 'GET':
        return await client.get(url, headers=headers)

    return await client.post(url, content=str(step.get('body') or ''), headers=headers)


def _headers(presence: dict) -> dict:
    """Заголовки запроса: свой `User-Agent` плюс то, что задал сайт."""
    headers = {'User-Agent': DATING_PRESENCE_UA,
               'Content-Type': 'application/x-www-form-urlencoded'}
    extra = presence.get('headers')
    if isinstance(extra, dict):
        headers.update({str(k): str(v) for k, v in extra.items()})

    return headers


def _cookies_of(storage_state, base: str) -> dict:
    """Cookie сайта из снятого состояния входа.

    Берём только его домен: в состоянии лежат и чужие (аналитика, платёжки), а
    слать их на сайт незачем — и лишний повод для отказа.
    """
    state = _json_load(storage_state)
    host = str(base).split('//')[-1].split('/')[0].replace('www.', '')

    return {str(c.get('name')): str(c.get('value'))
            for c in (state.get('cookies') or [])
            if host in str(c.get('domain') or '')}


def _json_body(response) -> dict:
    """Тело ответа словарём. Не JSON — пустой словарь, а не исключение."""
    if response is None:
        return {}

    try:
        data = response.json()
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _json_load(value) -> dict:
    """JSON-столбец приезжает то структурой, то строкой — зависит от драйвера."""
    if isinstance(value, str):
        try:
            return json.loads(value or '{}')
        except Exception:
            return {}

    return value if isinstance(value, dict) else {}


def _result(status: str, unread: int = -1, error: str = '', ms: int = 0) -> dict:
    if error:
        logger_info(f'[dating] опрос: {status} — {error[:120]}')

    return {'status': status, 'unread': unread,
            'error': error[:DATING_PRESENCE_ERROR_MAX], 'ms': ms}


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
