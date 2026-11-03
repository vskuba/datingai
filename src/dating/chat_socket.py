"""Вебсокет чата, завёрнутый в обычные POST: транспорт и кадры.

Так устроен чат Step2Love, и это не самоделка сайта, а запасной путь его же
SDK: страница сперва пробует `new WebSocket(...)`, а если браузер его не знает
— берёт `customWebSocket`, который гоняет те же кадры тремя POST-ами:

    POST {чат}/connection?userID=…&sessionID=…   открыть; ответ несёт `socketID`
    POST {чат}/connection/message                кадры: {"socketID": …, "data": […]}
    POST {чат}/connection/close                  закрыть

## ⚠⚠ Зачем отдельным файлом

Дел через этот транспорт уже два: **отправка реплики** (`send_connection.py`)
и **приём запросов чата** (`chat_request.py`). Общего у них всё, кроме
собственно кадра: открытие соединения, разбор пачки кадров, ожидание
приговора, клиент с cookie анкеты.

Оставь это в отправке — приём стал бы её копией, и первая же правка
(заголовок, срок, формат ответа) разошлась бы молча.

Граница такая: **здесь — как разговаривать, там — о чём**. Этот файл не знает
ни про реплики, ни про запросы; те не знают про `socketID` и опросы.

## ⚠⚠⚠ `action: "create"` — то, на чём всё стояло

Кадр без этого поля сайт принимает и **молча выбрасывает**: отвечает
`200 []`, не пишет ни ошибки, ни реплики. Две отправки ушли в никуда именно
так, и отличить это от «сайт лёг» было нечем. Поле ставит SDK страницы
(`sendSocketData`), и оно обязательно — потому и прописано здесь, в сборке
кадра, а не у вызывающего.

## ⚠⚠ Ответ приходит **следующим** запросом, а не этим

`/connection/message` — это и отправка, и опрос: ответом приезжает то, что
накопилось для нас, а приговор нашему кадру приходит кругом позже. Поэтому за
отправкой идут опросы пустым телом — ровно то, что делает таймер страницы.

Считать удачей сам `200` нельзя: именно так выглядит молча выброшенный кадр.
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
#
# ⚠⚠⚠ **Открытие несёт метки, и без них сайт отвечает `401`.** SDK собирает
# адрес сокета как `{хост}/?userID=…&sessionID=…` и приклеивает эту же строку
# запроса к `/connection` (`"/connection"===a&&(s+=u.search)`). Метки те же,
# что у присутствия этого сайта, и разворачивает их общий шаг
# (`http_heartbeat_step_call`) — потому он здесь и взят.
#
# ⚠⚠ Поймано **живой пробой**, а не сюитой: поддельный клиент сторожей
# отвечает на любой адрес, и путь без параметров проходил все пятнадцать
# проверок. Отсюда и сторож на сами метки ниже по файлу.
CHAT_SOCKET_OPEN = ('/connection?userID={profile_id}'
                    '&sessionID={cookie:clientSessionID}')
CHAT_SOCKET_MESSAGE = '/connection/message'

# Как часто опрашивать ответ и сколько раз. Секунда — шаг самой страницы
# (`setTimeout(g, 1e3)`); пять заходов это пять секунд, после которых молчание
# уже значит не задержку, а то, что кадр не принят.
CHAT_SOCKET_POLL_SEC = 1.0
CHAT_SOCKET_POLL_TRIES = 5

# Состояния кадра в ответе — словами SDK (`SocketDataObject.STATUS_*`).
CHAT_SOCKET_OK = 1
CHAT_SOCKET_FAILED = 2

# Сколько кадр считается живым, миллисекунд. Столько же ставит страница.
CHAT_SOCKET_FRAME_LIFE = 30000


class ChatSocketError(RuntimeError):
    """Соединение не открылось или сайт ответил непонятным. Текст — для журнала."""


async def chat_socket_open(profile: dict, presence: dict, send: dict,
                           base: str, proxy: str = '') -> dict:
    """Открывает соединение чата. Отдаёт клиента, `socketID` и кадры привета.

    Args:
        profile: строка `profile` — нужны номер анкеты на сайте и её сессия.
        presence: настройка сайта (из неё берутся заголовки).
        send: раздел `presence.send` — адрес чата и свои заголовки.
        base: адрес чат-сервера.
        proxy: выход наружу этой анкеты.

    Returns:
        dict: `client`, `socket_id`, `frames` — кадры, пришедшие при открытии
        (среди них `session`/`auth` со списками живых чатов и запросов).

    Raises:
        ChatSocketError: нет номера, нет сессии, сайт не открыл соединение.

    ⚠⚠ Cookie отбираются **тем же `http_heartbeat_cookies`**, которым их берёт
    присутствие этого сайта: кадр уходит на тот самый домен (`chat.сайт`), по
    которому бьёт `heartbeat`, и под той же `clientSessionID`. Отбор по домену
    из `presence.base` поддомена не покрывает — сессия вышла бы пустой при
    живом входе.
    """
    me = str(profile.get('profile_id') or '').strip()
    if not me:
        raise ChatSocketError('у анкеты нет номера на сайте')

    cookies = http_heartbeat_cookies(
        json_column_load(profile.get('storage_state')), base)

    if not cookies:
        raise ChatSocketError('вход не выполнен')

    client = chat_socket_client(cookies, proxy)

    # ⚠ Адрес собирает **общий шаг** (`http_heartbeat_step_call`): метки
    # `{profile_id}` и `{cookie:clientSessionID}` в нём те же, что у
    # присутствия этого сайта, и свой разборщик меток разошёлся бы с ним на
    # первой правке.
    step = {'base': base, 'method': 'POST',
            'path': str(send.get('open') or CHAT_SOCKET_OPEN),
            # Тело открытия у SDK именно такое: сокета ещё нет, кадров тоже.
            'body': json.dumps({'socketID': False, 'data': {}}),
            'headers': chat_socket_headers(presence, send)}

    answer = await http_heartbeat_step_call(client, presence, step, cookies,
                                            {'profile_id': me})

    try:
        hello = answer.json()
    except Exception:
        hello = {}

    socket_id = str(hello.get('socketID') or '')
    if not socket_id:
        raise ChatSocketError('сайт не открыл соединение чата')

    return {'client': client, 'socket_id': socket_id,
            'frames': chat_socket_frames(hello.get('data'))}


def chat_socket_frame(app: str, method: str, me: str, data: dict,
                      frame_id: str = '') -> dict:
    """Кадр — ровно тот, что собирает SDK страницы.

    Args:
        app: имя приложения (`chat`, `chatRequest`).
        method: имя метода (`sendMessage`, `confirm`, `cancel`).
        me: номер нашей анкеты на сайте.
        data: тело кадра — что именно делаем.
        frame_id: наш ключ кадра; пусто — случайный.

    ⚠⚠ `action: "create"` обязателен — см. заголовок файла.
    """
    return {'ID': str(frame_id or uuid.uuid4()),
            'appNameCreate': str(app), 'appNameTarget': str(app),
            'method': str(method),
            # Номер анкеты числом: сайт сверяет его с тем, под кем открыт
            # сокет, а строку считает чужим значением.
            'userID': int(me),
            'data': dict(data or {}),
            'statusID': 0,
            'createTime': int(time.time() * 1000),
            'destroyTime': CHAT_SOCKET_FRAME_LIFE,
            'action': 'create'}


async def chat_socket_send(opened: dict, base: str, presence: dict, send: dict,
                           frame: dict) -> dict:
    """Шлёт кадр и ждёт приговора сайта.

    Returns:
        dict: `ok`, `response`, `error`, `status` — код ответа на сам POST.

    ⚠⚠ **`200` удачей не считается.** Молча выброшенный кадр отвечает ровно
    так же, и пока удачу мерили кодом ответа, две реплики «ушли» никуда.

    ⚠ Не дождались — считаем неудачей, а не удачей. Цена ошибки
    несимметрична: лишняя попытка упирается в запрет повтора у вызывающего, а
    ложная удача закрывает дело навсегда.
    """
    client, socket_id = opened['client'], opened['socket_id']

    answer = await client.post(
        base + CHAT_SOCKET_MESSAGE,
        headers=chat_socket_headers(presence, send),
        content=json.dumps({'socketID': socket_id,
                            'data': [json.dumps(frame)]}))

    said = await _wait(client, base, presence, send, socket_id,
                       frame['ID'], answer.text)

    return {**said, 'status': answer.status_code}


def chat_socket_hello(frames: list, key: str) -> list:
    """Список из кадра авторизации: живые чаты, запросы чата, избранные.

    Args:
        frames: кадры, пришедшие при открытии.
        key: имя списка (`chatObjectList`, `chatRequestObjectList`).

    ⚠ Списки приезжают **внутри** кадра `session`/`auth`, а не отдельными
    полями ответа: сайт отдаёт их теми же кадрами, какие прислал бы в сокет.
    """
    for one in frames or []:
        data = one.get('data')
        if not isinstance(data, dict):
            continue

        found = data.get(str(key))
        if isinstance(found, list):
            return found

    return []


def chat_socket_frames(raw) -> list:
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


def chat_socket_client(cookies: dict, proxy: str = '') -> httpx.AsyncClient:
    """Клиент на этот заход: свои cookie, общий транспорт.

    ⚠ **Клиент разовый, а транспорт общий** (`http_pool.py`): cookie у каждой
    анкеты свои, и класть их в общего клиента значило бы слать соседний кадр
    под чужим входом. Закрывать его **нельзя** — `aclose()` унёс бы транспорт
    у всех.

    ⚠ Поэтому здесь не `http_heartbeat_client`: тот заводит собственный пул
    соединений и просит закрыть его вызывающего, а у кругов такого места нет —
    они ходят по анкете, и клиент оставался бы незакрытым на каждый заход.
    Поймано заставой выкладки на прошлой правке этого же транспорта.
    """
    return httpx.AsyncClient(timeout=DATING_PRESENCE_TIMEOUT, cookies=cookies,
                             follow_redirects=True,
                             headers={'User-Agent': DATING_PRESENCE_UA},
                             transport=http_pool_transport(proxy))


def chat_socket_headers(presence: dict, send: dict) -> dict:
    """Заголовки запроса: общие у сайта плюс свои у этой дороги."""
    return {**((presence or {}).get('headers') or {}),
            **((send or {}).get('headers') or {})}


# ── Приватное ────────────────────────────────────────────────────────────────


async def _wait(client, base: str, presence: dict, send: dict, socket_id: str,
                frame_id: str, first: str) -> dict:
    """Ждёт приговора нашему кадру: опрашивает, пока он не придёт."""
    seen = _verdict(first, frame_id)
    if seen:
        return seen

    for _ in range(CHAT_SOCKET_POLL_TRIES):
        await asyncio.sleep(CHAT_SOCKET_POLL_SEC)

        answer = await client.post(
            base + CHAT_SOCKET_MESSAGE,
            headers=chat_socket_headers(presence, send),
            content=json.dumps({'socketID': socket_id, 'data': []}))

        seen = _verdict(answer.text, frame_id)
        if seen:
            return seen

    return {'ok': False, 'response': '',
            'error': 'сайт не подтвердил кадр — дело могло не сделаться'}


def _verdict(raw, frame_id: str) -> dict | None:
    """Ответ сайта про наш кадр. `None` — его в этой пачке нет.

    Узнаём по `ID`, а не по имени метода: подтверждение приезжает кадром
    `action: "update"` без имени вовсе, зато с нашим идентификатором.
    """
    for one in chat_socket_frames(raw):
        if str(one.get('ID') or '') != str(frame_id):
            continue

        status = one.get('statusID')
        if status == CHAT_SOCKET_OK:
            return {'ok': True,
                    'response': json.dumps(one.get('resData') or {})[:300],
                    'error': ''}

        if status == CHAT_SOCKET_FAILED:
            why = (one.get('resData') or {}).get('info') or 'сайт отказал'

            return {'ok': False,
                    'response': json.dumps(one.get('resData') or {})[:300],
                    'error': f'сайт не принял кадр: {why}'}

    return None
