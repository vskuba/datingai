"""Отправка реплики туда, где вебсокет завёрнут в обычный HTTP.

Так устроен чат Step2Love, и это не самоделка сайта, а запасной путь его же
SDK: страница сперва пробует `new WebSocket(...)`, а если браузер его не знает —
берёт `customWebSocket`, который гоняет те же кадры тремя POST-ами:

    POST {чат}/connection?userID=…&sessionID=…   открыть; ответ несёт `socketID`
    POST {чат}/connection/message                кадры: {"socketID": …, "data": [строка, …]}
    POST {чат}/connection/close                  закрыть

Нам эта дорога дороже сокета ничем, а дешевле — всем: ходит обычный `httpx`,
живёт в нашем же журнале запросов, не держит соединение между репликами и не
требует браузера. Сокета у нас к этому сайту нет вовсе, а сценарий упирается в
панель, которая считает входы (`login_token.py`).

## ⚠⚠⚠ `action: "create"` — то, на чём всё стояло

Кадр без этого поля сайт принимает и **молча выбрасывает**: отвечает `200 []`,
не пишет ни ошибки, ни реплики. Две отправки ушли в никуда именно так, и
отличить это от «сайт лёг» было нечем — ответ тот же самый. Поле ставит SDK
страницы (`sendSocketData`), и оно обязательно.

## ⚠⚠ Ответ приходит **следующим** запросом, а не этим

`/connection/message` — это и отправка, и опрос: ответом приезжает то, что
накопилось для нас, а приговор нашему кадру приходит кругом позже. Поэтому за
отправкой идут опросы пустым телом — ровно то, что делает таймер страницы.

Считать удачей сам `200` нельзя: именно так выглядит молча выброшенный кадр.

## ⚠⚠ Номер чата спрашивается у сокета, а не у панели

В ответ на открытие приезжает `chatObjectList` — живые разговоры анкеты. Номер
берём оттуда: панель знает его задним числом, а сокет отвечает на вопрос «куда
писать **сейчас**». Нет живого чата с этим человеком — отправлять некуда, и это
не поломка: разговор закрыт либо не начинался.
"""

import asyncio
import json
import time
import uuid

import httpx

from http_.http_heartbeat import http_heartbeat_cookies
from http_.http_heartbeat_step import http_heartbeat_step_call
from http_.http_pool import http_pool_transport

from src.dating.json_column import json_column_load
from src.dating.presence import DATING_PRESENCE_TIMEOUT, DATING_PRESENCE_UA

# Пути обёртки. Это не настройка: их задаёт SDK сайта, а не его владелец, и
# сайту с другими путями нужен другой транспорт, а не другие значения.
SEND_CONNECTION_OPEN = '/connection'
SEND_CONNECTION_MESSAGE = '/connection/message'

# Как часто опрашивать ответ и сколько раз. Секунда — шаг самой страницы
# (`setTimeout(g, 1e3)`); пять заходов это пять секунд, после которых молчание
# уже значит не задержку, а то, что кадр не принят.
SEND_CONNECTION_POLL_SEC = 1.0
SEND_CONNECTION_POLL_TRIES = 5

# Состояния кадра в ответе — словами SDK (`SocketDataObject.STATUS_*`).
SEND_CONNECTION_OK = 1
SEND_CONNECTION_FAILED = 2

# Умолчания кадра: имя приложения и метода у этого движка. В настройке сайта их
# можно переопределить (`presence.send.app` / `.method`), но своего значения они
# не меняют — это имена из SDK, а не вкус владельца сайта.
SEND_CONNECTION_APP = 'chat'
SEND_CONNECTION_METHOD = 'sendMessage'

# Сколько кадр считается живым, миллисекунд. Столько же ставит страница.
SEND_CONNECTION_FRAME_LIFE = 30000


async def send_connection_run(profile: dict, presence: dict, send: dict,
                              to_uid: str, text: str, message_uid: str,
                              proxy: str = '') -> dict:
    """Шлёт реплику кадром через HTTP-обёртку сокета.

    Args:
        profile: строка `profile` — нужны номер анкеты на сайте и её сессия.
        presence: настройка сайта целиком (из неё берутся заголовки).
        send: раздел `presence.send` — адрес чата и имена кадра.
        to_uid: номер собеседника на сайте.
        text: что отправляем.
        message_uid: наш идентификатор реплики; он же становится `ID` кадра —
            по нему и узнаётся ответ сайта.
        proxy: выход наружу этой анкеты.

    Returns:
        dict: та же пятёрка, что у прочих путей отправки
        (`ok`/`offline`/`response`/`message_uid`/`error`) плюс `status`.

    ⚠ **Ничего не бросает.** Отправкой правит круг, обходящий все анкеты сайта;
    исключение отсюда унесло бы остальных.

    ⚠⚠ Cookie отбираются **тем же `http_heartbeat_cookies`**, которым их берёт
    присутствие этого сайта: кадр уходит на тот самый домен (`chat.сайт`), по
    которому бьёт `heartbeat`, и под той же `clientSessionID`. Отбор по домену
    из `presence.base` поддомена не покрывает — сессия вышла бы пустой при
    живом входе.
    """
    base = str(send.get('base') or '').rstrip('/')
    if not base:
        return _fail('у сайта не задан адрес чата (presence.send.base)',
                     message_uid)

    me = str(profile.get('profile_id') or '').strip()
    if not me:
        return _fail('у анкеты нет номера на сайте', message_uid)

    cookies = http_heartbeat_cookies(
        json_column_load(profile.get('storage_state')), base)

    if not cookies:
        return _fail('вход не выполнен', message_uid)

    client = _client(cookies, proxy)

    try:
        hello = await _open(client, presence, send, base, cookies, me)
    except Exception as e:
        return _fail(f'{type(e).__name__}: {e}', message_uid)

    if not hello.get('socketID'):
        return _fail('сайт не открыл соединение чата', message_uid,
                     response=str(hello)[:300])

    chat_id = _chat_of(hello, to_uid)
    if not chat_id:
        # ⚠ Это **не ошибка настройки**: живого разговора с этим человеком у
        # анкеты сейчас нет. Отдаём `offline` — тем же словом, каким отвечает
        # сокет, когда собеседника нет в чате: вызывающие уже умеют его читать
        # как состояние, а не как поломку.
        return {'ok': False, 'offline': True, 'response': '',
                'message_uid': message_uid, 'status': 0,
                'error': 'у анкеты нет открытого чата с этим собеседником'}

    frame = _frame(send, me, chat_id, text, message_uid)

    try:
        answer = await client.post(
            base + SEND_CONNECTION_MESSAGE,
            headers=_headers(presence, send),
            content=json.dumps({'socketID': hello['socketID'],
                                'data': [json.dumps(frame)]}))
        said = await _wait(client, base, presence, send, hello['socketID'],
                           frame['ID'], answer.text)
    except Exception as e:
        return _fail(f'{type(e).__name__}: {e}', message_uid)

    return {**said, 'message_uid': message_uid, 'offline': False,
            'status': answer.status_code}


# ── Приватное ────────────────────────────────────────────────────────────────


def _client(cookies: dict, proxy: str) -> httpx.AsyncClient:
    """Клиент на эту отправку: свои cookie, общий транспорт.

    ⚠ **Клиент разовый, а транспорт общий** (`http_pool.py`): cookie у каждой
    анкеты свои, и класть их в общего клиента значило бы слать соседнюю реплику
    под чужим входом. Закрывать его **нельзя** — `aclose()` унёс бы транспорт у
    всех.

    ⚠ Поэтому здесь не `http_heartbeat_client`: тот заводит собственный пул
    соединений и просит закрыть его вызывающего, а у отправки такого места нет —
    она уходит из круга по анкете, и клиент оставался бы незакрытым на каждую
    реплику. Поймано заставой выкладки.
    """
    return httpx.AsyncClient(timeout=DATING_PRESENCE_TIMEOUT, cookies=cookies,
                             follow_redirects=True,
                             headers={'User-Agent': DATING_PRESENCE_UA},
                             transport=http_pool_transport(proxy))


async def _open(client, presence: dict, send: dict, base: str, cookies: dict,
                me: str) -> dict:
    """Открывает соединение и отдаёт разобранный ответ: `socketID` плюс кадры.

    ⚠ Адрес собирает **общий шаг** (`http_heartbeat_step_call`): метки
    `{profile_id}` и `{cookie:clientSessionID}` в нём те же, что у присутствия
    этого сайта, и свой разборщик меток разошёлся бы с ним на первой правке.
    """
    step = {'base': base, 'method': 'POST',
            'path': str(send.get('open') or SEND_CONNECTION_OPEN),
            # Тело открытия у SDK именно такое: сокета ещё нет, кадров тоже.
            'body': json.dumps({'socketID': False, 'data': {}}),
            'headers': _headers(presence, send)}

    answer = await http_heartbeat_step_call(client, presence, step, cookies,
                                            {'profile_id': me})

    try:
        return answer.json()
    except Exception:
        return {}


def _chat_of(hello: dict, to_uid: str) -> int:
    """Номер живого чата с этим собеседником. Ноль — такого разговора нет.

    Список приезжает внутри кадра авторизации (`session`/`auth`), а не
    отдельным полем ответа: сайт отдаёт его теми же кадрами, какие прислал бы в
    сокет.
    """
    want = str(to_uid or '').strip()

    for one in _frames(hello.get('data')):
        data = one.get('data')
        if not isinstance(data, dict):
            continue

        for chat in (data.get('chatObjectList') or []):
            if not isinstance(chat, dict) or chat.get('destroy'):
                continue
            if str(chat.get('userIDMan') or '') == want \
                    or str(chat.get('userIDGirl') or '') == want:
                return int(chat.get('chatID') or 0)

    return 0


def _frame(send: dict, me: str, chat_id: int, text: str, message_uid: str) -> dict:
    """Кадр отправки — ровно тот, что собирает SDK страницы.

    ⚠ `ID` — **наш** `message_uid`, а не свежий случайный: по нему узнаётся
    ответ сайта, и второе имя у той же реплики заставило бы держать их пару.

    ⚠⚠ `action: "create"` обязателен — см. заголовок файла.
    """
    app = str(send.get('app') or SEND_CONNECTION_APP)

    return {'ID': str(message_uid or uuid.uuid4()),
            'appNameCreate': app, 'appNameTarget': app,
            'method': str(send.get('method') or SEND_CONNECTION_METHOD),
            # Номер анкеты числом: сайт сверяет его с тем, под кем открыт сокет,
            # а строку считает чужим значением.
            'userID': int(me),
            'data': {'chatID': int(chat_id), 'message': str(text),
                     'attachID': 0},
            'statusID': 0,
            'createTime': int(time.time() * 1000),
            'destroyTime': SEND_CONNECTION_FRAME_LIFE,
            'action': 'create'}


async def _wait(client, base: str, presence: dict, send: dict, socket_id: str,
                frame_id: str, first: str) -> dict:
    """Ждёт приговора нашему кадру: опрашивает, пока он не придёт.

    ⚠⚠ **`200` удачей не считается.** Молча выброшенный кадр отвечает ровно
    так же, и пока удачу мерили кодом ответа, две реплики «ушли» никуда.

    ⚠ Не дождались — считаем неудачей, а не удачей. Цена ошибки несимметрична:
    лишняя попытка упирается в запрет повтора (`send.py`, `_said_already`), а
    ложная удача закрывает строку пула, и реплика не уйдёт уже никогда.
    """
    seen = _verdict(first, frame_id)
    if seen:
        return seen

    for _ in range(SEND_CONNECTION_POLL_TRIES):
        await asyncio.sleep(SEND_CONNECTION_POLL_SEC)

        answer = await client.post(
            base + SEND_CONNECTION_MESSAGE,
            headers=_headers(presence, send),
            content=json.dumps({'socketID': socket_id, 'data': []}))

        seen = _verdict(answer.text, frame_id)
        if seen:
            return seen

    return {'ok': False, 'response': '',
            'error': 'сайт не подтвердил кадр — реплика могла не уйти'}


def _verdict(raw, frame_id: str) -> dict | None:
    """Ответ сайта про наш кадр. `None` — его в этой пачке нет.

    Узнаём по `ID`, а не по имени метода: подтверждение приезжает кадром
    `action: "update"` без имени вовсе, зато с нашим идентификатором.
    """
    for one in _frames(raw):
        if str(one.get('ID') or '') != str(frame_id):
            continue

        status = one.get('statusID')
        if status == SEND_CONNECTION_OK:
            return {'ok': True, 'response': json.dumps(one.get('resData') or {})[:300],
                    'error': ''}

        if status == SEND_CONNECTION_FAILED:
            why = (one.get('resData') or {}).get('info') or 'сайт отказал'

            return {'ok': False, 'response': json.dumps(one.get('resData') or {})[:300],
                    'error': f'сайт не принял реплику: {why}'}

    return None


def _frames(raw) -> list:
    """Кадры из ответа: сайт отдаёт их списком строк, иногда строкой со списком.

    Служебные (не-JSON) пропускаем молча — это числа протокола, а не кадры.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []

    out = []
    for one in (raw if isinstance(raw, list) else []):
        if isinstance(one, dict):
            out.append(one)

            continue

        try:
            got = json.loads(one)
        except (TypeError, ValueError):
            continue

        if isinstance(got, dict):
            out.append(got)

    return out


def _headers(presence: dict, send: dict) -> dict:
    """Заголовки запроса: общие у сайта плюс свои у отправки."""
    return {**((presence or {}).get('headers') or {}),
            **((send or {}).get('headers') or {})}


def _fail(error: str, message_uid: str, response: str = '') -> dict:
    """Неудача в той же форме, что у прочих путей отправки."""
    return {'ok': False, 'offline': False, 'response': response,
            'message_uid': message_uid, 'status': 0, 'error': error}
