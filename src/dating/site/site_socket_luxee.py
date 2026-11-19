"""Сокет чата панели Luxee: открыть, спросить, прочитать диалоги, отправить реплику.

Чат панели агентства Luxee — socket.io 2 страницы `/chats/`. Протокол один на
двоих читателей: забор переписки (`site_import_luxee.py`) и отправку ответа
(`send_panel_socket.py`); жил бы двумя копиями — разошёлся бы на первой правке
сайта. Клиент протокола — общий, `py_modules/socketio_/socketio_v2.py`.

    cm_get_profiles {uids}             → внешние анкеты девушек (по одной на проект)
    cl_ch_all / cl_ch_more {ownerUid}  → диалоги девушки страницами
    cl_c_messages {ownerUid, identity} → последние 20 сообщений диалога
    cl_cm_messages {…, token: index}   → двадцать раньше указанного номера
    cl_m_message {ownerUid, identity, body, type: 1, key}
                                       → server_m_new (принято) | s_m_error

## ⚠⚠ Номер девушки — внутренний, автор и диалог — по внешней анкете

У девушки один номер панели (`622180`, он же наш `profile_id`) и по анкете на
каждый проект агентства (`3161872`, `3161888`, …). Диалог зовётся
`{мужчина}_{внешняя анкета}`, и автор её реплики — внешняя анкета. Карту
«внешняя → внутренняя» отдаёт `cm_get_profiles` (`import_uid`); без этого
запроса сервер вдобавок отвечает пустым списком диалогов — замер 19.11.2026.

## ⚠ `cl_consume` здесь нет и не будет

Это отметка «прочитано»: чтение панели не должно гасить оператору счётчик новых.
"""

import asyncio
import random
import re
import time
from contextlib import asynccontextmanager

from socketio_.socketio_v2 import SocketIoV2Error, socketio_v2_open

from src.dating.panel_fetch import DatingPanelFetchError, panel_fetch_html

# Страница чата: несёт токен сокета (третий довод `ModelChatInstance`).
SITE_SOCKET_LUXEE_PAGE = '/chats/'

# Путь socket.io на том же хосте, что и сайт.
SITE_SOCKET_LUXEE_PATH = '/chat/socket.io/'

# Токен стоит сразу за объектом шаблонов, последний ключ которого — этот.
# ⚠ Рядом на странице ещё восемь JWT других служб (подарки, звонки, уведомления):
# брать «первый попавшийся» значило бы ловить «Permission denied» от сокета.
SITE_SOCKET_LUXEE_TOKEN_RE = re.compile(
    r"skipTaskFormTemplate[^}]*\},\s*'(eyJ[A-Za-z0-9_.-]+)'", re.S)

# Что шлёт страница доводом `settings`.
SITE_SOCKET_LUXEE_SETTINGS = {'winkExclude': []}

# Сколько ждём ответа на один запрос. Сервер отвечает за десятые доли секунды.
SITE_SOCKET_LUXEE_ASK_SEC = 20

# Предохранители от петли: сервер, вернувший ту же метку, крутил бы нас вечно.
# 50 страниц диалогов — тысяча собеседников у девушки; 500 страниц сообщений —
# десять тысяч реплик в одном диалоге.
SITE_SOCKET_LUXEE_CHANNEL_PAGES = 50
SITE_SOCKET_LUXEE_MESSAGE_PAGES = 500

# Вид сообщения «текст» — так шлёт страница обычную реплику.
SITE_SOCKET_LUXEE_TEXT = 1


@asynccontextmanager
async def site_socket_luxee_open(site_id: int, presence: dict, owner: int):
    """Открыть сокет чата панели Luxee под сессией круга панели.

    Args:
        site_id: сайт; сессию его панели держит `panel_round`.
        presence: описание сайта — нужен `base`.
        owner: внутренний номер любой нашей девушки — страница чата без него
            не открывается.

    Yields:
        открытое и авторизованное соединение (`SocketIoV2`).

    Raises:
        DatingPanelFetchError: панель не отдала страницу или токен, сокет не
            открылся, не принял токен или замолчал.
    """
    base = str((presence or {}).get('base') or '').rstrip('/')
    if not base.startswith('http'):
        raise DatingPanelFetchError('у сайта нет адреса `presence.base`')

    html = await panel_fetch_html(site_id, SITE_SOCKET_LUXEE_PAGE,
                                  {'ownerUid': str(owner)})
    found = SITE_SOCKET_LUXEE_TOKEN_RE.search(html or '')
    if not found:
        raise DatingPanelFetchError(
            'на странице чатов нет токена сокета — разметка сменилась?')

    try:
        async with socketio_v2_open(
                'wss://' + base.split('://', 1)[1] + SITE_SOCKET_LUXEE_PATH,
                query={'token': found.group(1), 'type': 'model'},
                headers={'Origin': base}) as sock:
            hello = await _wait(sock, lambda kind, data: kind == 's_ua_success')
            if not hello.get('status'):
                raise DatingPanelFetchError('сокет чата не принял токен')

            yield sock
    except SocketIoV2Error as e:
        raise DatingPanelFetchError(f'сокет чата: {e}')
    except asyncio.TimeoutError:
        raise DatingPanelFetchError('сокет чата не ответил на запрос')


async def site_socket_luxee_ask(sock, kind: str, data: dict) -> dict:
    """Запрос серверу чата и его ответ целиком (`message`, `token`).

    Сопоставление по `key` — так делает и страница: события приходят вперемешку
    со служебными (счётчики, «в сети»), и порядок ответов не гарантирован.

    Raises:
        DatingPanelFetchError: сервер ответил `status: false`.
        asyncio.TimeoutError: не ответил за `SITE_SOCKET_LUXEE_ASK_SEC`.
    """
    key = _key(kind)
    await sock.emit('message', {'type': kind, 'data': data, 'key': key})

    answer = await _wait(sock, lambda _kind, got: got.get('key') == key)
    if not answer.get('status'):
        raise DatingPanelFetchError(
            f"сокет отказал в {kind}: {answer.get('error') or 'status ложен'}")

    return answer


async def site_socket_luxee_outer(sock, girls: list) -> dict:
    """Внешние анкеты наших девушек: `внешний номер → внутренний` (строками).

    ⚠ Звать **до** списка диалогов: без него сервер отвечает пустым списком.
    """
    outer = (await site_socket_luxee_ask(
        sock, 'cm_get_profiles',
        {'uids': [int(g) for g in girls], 'settings': SITE_SOCKET_LUXEE_SETTINGS})
             ).get('message') or {}

    return {str(uid): str(card.get('import_uid') or '')
            for uid, card in outer.items() if isinstance(card, dict)}


async def site_socket_luxee_channels(sock, girl: int) -> list[dict]:
    """Все диалоги девушки, страница за страницей по метке."""
    data = {'ownerUid': int(girl), 'settings': SITE_SOCKET_LUXEE_SETTINGS}
    answer = await site_socket_luxee_ask(sock, 'cl_ch_all', data)

    channels = list(answer.get('message') or [])
    token = answer.get('token')
    for _ in range(SITE_SOCKET_LUXEE_CHANNEL_PAGES):
        if not token:
            break

        answer = await site_socket_luxee_ask(sock, 'cl_ch_more',
                                             {**data, 'token': token})
        channels += list(answer.get('message') or [])
        token = answer.get('token') if answer.get('token') != token else None

    return [c for c in channels if isinstance(c, dict)]


async def site_socket_luxee_messages(sock, girl: int, identity: str,
                                     mark: int | None = None,
                                     since_ms: int = 0) -> list[dict]:
    """Сообщения диалога новее номера `mark`; без него — новее `since_ms`.

    Ни того ни другого — диалог целиком, с первого сообщения.
    """
    ask = {'ownerUid': int(girl), 'identity': str(identity)}
    got = list((await site_socket_luxee_ask(sock, 'cl_c_messages', ask)
                ).get('message') or [])

    for _ in range(SITE_SOCKET_LUXEE_MESSAGE_PAGES):
        if not got:
            break

        oldest = min(got, key=lambda m: int(m.get('index') or 0))
        first = int(oldest.get('index') or 0)
        if first <= 1 or (mark is not None and first <= mark + 1):
            break
        if mark is None and since_ms and int(oldest.get('created') or 0) < since_ms:
            break

        more = list((await site_socket_luxee_ask(
            sock, 'cl_cm_messages', {**ask, 'token': first})).get('message') or [])
        if not more:
            break
        got = more + got

    return [m for m in got if isinstance(m, dict)
            and (mark is None or int(m.get('index') or 0) > mark)]


async def site_socket_luxee_tasks(sock) -> list[dict]:
    """Задания очереди Catch Up: диалоги, где он написал, а ответа нет.

    ⚠⚠ Пока хоть одно висит, панель отвечает на любую реплику в **другой**
    диалог отказом 485 «You have unfinished tasks in Catch Up» — замок на всю
    учётку агентства (19.11.2026: Kateryna не пустили из-за задания Olena ↔
    Ricardo). Закрывает задание ответ в его диалоге либо пропуск с причиной.

    Returns:
        list: задания как их отдаёт сервер — `importUid` (наша девушка),
        `userUid` (он), `channelIdentity`, `priority`, `createdAt`.
    """
    tasks, page = [], None
    for _ in range(SITE_SOCKET_LUXEE_CHANNEL_PAGES):
        got = (await site_socket_luxee_ask(sock, 'cl_get_tasks', {'page': page})
               ).get('message') or {}
        tasks += [t for t in got.get('tasks') or [] if isinstance(t, dict)]
        page = got.get('page') if got.get('page') != page else None
        if not page:
            break

    return tasks


async def site_socket_luxee_send(sock, girl: int, identity: str, text: str) -> dict:
    """Отправить текстовую реплику в диалог и дождаться приговора сервера.

    ⚠⚠ Делаем **ровно как страница**, и оба отличия уже стоили отказа «409 Can't
    save message. Not valid message» на первых двух живых ответах 19.11.2026:
    ключ реплики — в её форме (`cl_m_message-{диалог}-{микровремя}-{случайное}`),
    и диалог перед отправкой **открыт** — страница всегда зовёт `cl_c_messages`,
    прежде чем дать писать (это чтение, «прочитано» оно не ставит).

    Returns:
        dict: `ok` — сервер прислал наше сообщение обратно (`server_m_new` с
        нашим ключом — ключ в форме страницы собирается здесь); `error` — его отказ словами (`s_m_error`), `code` — код
        отказа (485 — «сначала Catch Up»), `sid` — номер принятого сообщения.

    Raises:
        asyncio.TimeoutError: ни подтверждения, ни отказа за срок ожидания.
    """
    key = f'cl_m_message-{identity}-{time.time()}-{random.randint(1, 500)}'
    await site_socket_luxee_ask(sock, 'cl_c_messages',
                                {'ownerUid': int(girl), 'identity': str(identity)})

    await sock.emit('message', {
        'type': 'cl_m_message', 'key': _key('cl_m_message'),
        'data': {'body': str(text), 'type': SITE_SOCKET_LUXEE_TEXT, 'media': [],
                 'key': str(key), 'identity': str(identity),
                 'ownerUid': int(girl), 'gift': None, 'sticker': None}})

    def ours(kind, data):
        message = data.get('message') if isinstance(data.get('message'), dict) else {}
        if kind == 'server_m_new':
            request = (message.get('request') or {}).get('data') or {}
            return request.get('key') == key
        if kind == 's_m_error':
            return message.get('key') == key or message.get('identity') == identity

        return False

    answer = await _wait(sock, ours)
    message = answer.get('message') if isinstance(answer.get('message'), dict) else {}
    error = answer.get('error') if isinstance(answer.get('error'), dict) else {}
    if error or 'request' not in message:
        return {'ok': False, 'code': int(error.get('code') or 0),
                'error': str(error.get('text') or error.get('message')
                             or 'сервер чата отказал без причины'), 'sid': ''}

    sent = message.get('message') if isinstance(message.get('message'), dict) else {}

    return {'ok': True, 'code': 0, 'error': '', 'sid': str(sent.get('sid') or '')}


async def _wait(sock, match) -> dict:
    """Первое событие `message`, которое подходит под `match(type, data)`."""
    loop = asyncio.get_running_loop()
    end = loop.time() + SITE_SOCKET_LUXEE_ASK_SEC

    while True:
        event, args = await sock.next(max(0.1, end - loop.time()))
        if event != 'message' or not args or not isinstance(args[0], dict):
            continue

        data = args[0].get('data')
        if isinstance(data, dict) and match(args[0].get('type'), data):
            return data


def _key(kind: str) -> str:
    """Ключ запроса в форме страницы: тип, микровремя, случайное число."""
    return f'{kind}{time.time()}-{random.randint(1, 500)}'
