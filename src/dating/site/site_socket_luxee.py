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
import json
import random
import re
import time
from contextlib import asynccontextmanager

from socketio_.socketio_v2 import SocketIoV2Error, socketio_v2_open

from src.dating.panel_fetch import (DatingPanelFetchError, panel_fetch_html,
                                   panel_fetch_post)

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

# Вид сообщения «медиа» — снимок с подписью (`MESSAGE_TYPE_MEDIA` в `chat.v2.js`).
SITE_SOCKET_LUXEE_MEDIA = 2

# Куда страница грузит файл перед сообщением. ⚠ Заголовок `token` — токен
# **девушки** из `cm_get_profiles` (`access_token`), а не сессии панели.
SITE_SOCKET_LUXEE_UPLOAD = '/media/agency-chat/upload-media/'


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


# Причина пропуска задания Catch Up — как в форме панели (`chat.v2.js`,
# `SKIP_TASK_REASON_*`): 1 Missmatch, 2 Non-active user, 3 Other (нужен
# комментарий от 10 знаков), 4 Message limit reached. Мы пропускаем того, кто
# молчит на наши повторы, — «Non-active user».
SITE_SOCKET_LUXEE_SKIP_INACTIVE = 2


async def site_socket_luxee_skip(sock, sid, reason: int = SITE_SOCKET_LUXEE_SKIP_INACTIVE,
                                 description: str = '') -> None:
    """Пропустить задание Catch Up — кнопка «Skip» в очереди панели.

    Args:
        sid: номер **задания** (`sid` из `cl_get_tasks`), а не диалога.

    ⚠ Панель не даёт пропускать задания с `priority <= 1` (кнопки там нет) —
    сервер ответит отказом, и он поднимется `DatingPanelFetchError`.

    Raises:
        DatingPanelFetchError: сервер отказал.
    """
    await site_socket_luxee_ask(sock, 'cl_skip_task',
                                {'uid': sid, 'reason': int(reason),
                                 'description': str(description or '')})


async def site_socket_luxee_media_ok(sock, girl: int, identity: str) -> bool:
    """Открыта ли в диалоге отправка снимков (`private` у `cl_m_is_available_media`).

    ⚠ Открывается панелью не сразу — когда между ними уже шёл разговор (замер
    21.11.2026: открыто примерно у половины разговоров). Закрыто — кнопка
    вложения на странице серая, и сервер снимок не примет.
    """
    got = (await site_socket_luxee_ask(sock, 'cl_m_is_available_media',
                                       {'identity': str(identity), 'ownerUid': int(girl)})
           ).get('message') or {}

    return bool(isinstance(got, dict) and got.get('private'))


async def site_socket_luxee_upload(sock, site_id: int, girl: int, key: str,
                                   raw: bytes, kind: str = 'image/jpeg') -> dict:
    """Загрузить снимок к будущему сообщению `key`. Отдаёт запись файла сервера.

    Raises:
        DatingPanelFetchError: токена девушки нет или загрузка не принята.
    """
    cards = (await site_socket_luxee_ask(
        sock, 'cm_get_profiles',
        {'uids': [int(girl)], 'settings': SITE_SOCKET_LUXEE_SETTINGS})).get('message') or {}
    token = next((str(c.get('access_token') or '') for c in cards.values()
                  if isinstance(c, dict) and str(c.get('import_uid')) == str(girl)), '')
    if not token:
        raise DatingPanelFetchError('у девушки нет токена загрузки снимков')

    answer = await panel_fetch_post(
        int(site_id), SITE_SOCKET_LUXEE_UPLOAD, {'type': '1', 'mailUid': str(key)},
        files={'src': ('photo.jpg', raw, kind or 'image/jpeg')},
        headers={'token': token, 'X-Requested-With': 'XMLHttpRequest'})
    try:
        got = json.loads(answer or '{}')
    except ValueError:
        got = {}
    if not got.get('success') or not isinstance(got.get('data'), dict):
        raise DatingPanelFetchError('панель не приняла снимок')

    return got['data']


def site_socket_luxee_media_item(file: dict) -> dict:
    """Элемент `media` сообщения из записи загрузки.

    ⚠⚠ Вид — «из хранилища» (`isStorage`), а имена — **голые**: так сервер
    принимает (вид «новый файл» отвечает `418 Not valid files`), и сам дописывает
    `/src/`, `/thumb/`, `/preview/`. Пришли путь целиком — получишь
    `/thumb//thumb/…`, адрес отвечает `403`, и собеседник видит битую картинку
    (Olena → Donnie, 21.11.2026).
    """
    bare = {k: str(file.get(k) or '').rsplit('/', 1)[-1]
            for k in ('src', 'preview', 'thumbnail')}

    return {'index': str(file.get('entity_id') or ''), 'file': {**file, **bare},
            'type': 1, 'isNew': True, 'status': 7, 'isStorage': True}


async def site_socket_luxee_send(sock, girl: int, identity: str, text: str,
                                 man: str = '', since_ms: int = 0,
                                 photo: dict | None = None,
                                 ours_texts=()) -> dict:
    """Отправить текстовую реплику в диалог и дождаться приговора сервера.

    Args:
        man: его номер — чтобы отличить его реплики от реплик её стороны.
        since_ms: метка повода (его реплики, на которую отвечаем, либо нашей
            последней у «напиши повторно»). ⚠⚠ Есть в диалоге реплика её
            стороны **новее** — не шлём: девушку ведёт и живой человек, и он
            мог ответить, пока агент сочинял (решение владельца 19.11.2026).
            Ноль — не проверять. Проверка по свежему ответу сервера, а не по
            нашей базе: импорт отстаёт на 15–30 секунд.
        ours_texts: тексты **наших** быстрых ответов этой пары (`chat_quick.py`).
            ⚠⚠ Сервер возвращает их в сокете от лица девушки — `author.uid`
            её, и без вычитания шаблонное «hi» читалось бы «её ответ новее
            повода»: параллельный ответ агента отменялся бы нашим же жестом
            вежливости. Сверка скелетом текста (буквы и цифры, регистр не
            важен) — та же мерка, что у близнецов: сайт съедает знаки в пути.

    ⚠⚠ Делаем **ровно как страница**, и оба отличия уже стоили отказа «409 Can't
    save message. Not valid message» на первых двух живых ответах 19.11.2026:
    ключ реплики — в её форме (`cl_m_message-{диалог}-{микровремя}-{случайное}`),
    и диалог перед отправкой **открыт** — страница всегда зовёт `cl_c_messages`,
    прежде чем дать писать (это чтение, «прочитано» оно не ставит).

    Returns:
        dict: `ok` — сервер прислал наше сообщение обратно (`server_m_new` с
        нашим ключом — ключ в форме страницы собирается здесь); `error` — его
        отказ словами (`s_m_error`), `code` — код отказа (485 — «сначала Catch
        Up»), `sid` — номер принятого сообщения, `skipped` — не слали вовсе:
        её сторона уже ответила, и тогда `theirs` — её реплики новее повода
        (сообщения сервера как есть).

    Raises:
        asyncio.TimeoutError: ни подтверждения, ни отказа за срок ожидания.
    """
    key = f'cl_m_message-{identity}-{time.time()}-{random.randint(1, 500)}'
    opened = (await site_socket_luxee_ask(
        sock, 'cl_c_messages', {'ownerUid': int(girl), 'identity': str(identity)})
              ).get('message') or []

    if since_ms and man:
        # Ключи наших быстрых строк — один раз на вызов, не на каждую строку.
        # ⚠⚠ Ключ, а не голый скелет: от «:)» скелет пуст, пустые прежде
        # выбрасывались — и наш же быстрый «:)», приехавший её строкой,
        # отменял каждый ответ агента как «девушка ответила сама» (Irina →
        # Alma Hawker, 25.11.2026: ответ отменялся каждые пять минут).
        ours = {s for s in (_twin_key(t) for t in ours_texts) if s}
        newer = [m for m in opened if isinstance(m, dict)
                 and str((m.get('author') or {}).get('uid') or '') != str(man)
                 # ⚠ Со следующей секунды: повод у нас записан с точностью до
                 # секунды, сервер — до миллисекунд, и наша же реплика той
                 # секунды казалась «новее» себя (19.11.2026, #458 отменён
                 # нашим же #457; «напиши повторно» отменялось бы всегда).
                 and int(m.get('created') or 0) >= int(since_ms) + 1000
                 # ⚠ И это не наш же быстрый ответ, приехавший её строкой.
                 and _twin_key(m.get('body')) not in ours]
        if newer:
            last = max(newer, key=lambda m: int(m.get('created') or 0))
            return {'ok': False, 'skipped': True, 'code': 0, 'sid': '',
                    'theirs': sorted(newer, key=lambda m: int(m.get('created') or 0)),
                    'error': 'девушка уже ответила сама: '
                             f"«{str(last.get('body') or '')[:80]}»"}

    # ⚠⚠ Снимок к реплике: одним сообщением с подписью. Медиа закрыто —
    # ответ `no_media`, и решает вызывающий (обычно — уйти одним текстом).
    media, kind = [], SITE_SOCKET_LUXEE_TEXT
    if photo:
        if not await site_socket_luxee_media_ok(sock, girl, identity):
            return {'ok': False, 'skipped': False, 'no_media': True, 'code': 0,
                    'error': 'отправка снимков в этом диалоге ещё закрыта', 'sid': ''}
        file = await site_socket_luxee_upload(sock, int(photo['site_id']), girl, key,
                                              photo['raw'], photo.get('kind') or '')
        media, kind = [site_socket_luxee_media_item(file)], SITE_SOCKET_LUXEE_MEDIA

    await sock.emit('message', {
        'type': 'cl_m_message', 'key': _key('cl_m_message'),
        'data': {'body': str(text), 'type': kind, 'media': media,
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
        return {'ok': False, 'skipped': False, 'code': int(error.get('code') or 0),
                'error': str(error.get('text') or error.get('message')
                             or 'сервер чата отказал без причины'), 'sid': ''}

    sent = message.get('message') if isinstance(message.get('message'), dict) else {}

    return {'ok': True, 'skipped': False, 'code': 0, 'error': '',
            'sid': str(sent.get('sid') or ''),
            # Снимки, как их запомнил сервер, — полными адресами для окна разговора.
            'media': _media_urls(sent) if media else []}


async def site_socket_luxee_listen(sock, on_change, stop_at: float) -> None:
    """Слушать сокет и звать `on_change()`, когда в чате девушек что-то появилось.

    Сервер сам шлёт оператору события по всем его девушкам (замер 19.11.2026:
    счётчики `sm_mc_new` раз в ~18 с, `server_c_update` и `s_m_update` на каждое
    изменение диалога). Сигнал «забрать» — новая реплика (`server_m_new`), новый
    диалог (`server_jn_ch`, `s_c_newbie`) и изменение диалога, у которого вырос
    номер последнего сообщения (`server_c_update`). Прочитал он нашу реплику —
    номер прежний, и сигнала нет.

    ⚠ Что именно пришло, здесь не разбирается: забор по отметкам сам дочитает
    изменившийся диалог, и правило записи остаётся одно (`site_import_luxee`).

    Args:
        on_change: корутина без доводов — «забери сейчас».
        stop_at: `time.monotonic()`, до которого слушать.

    Raises:
        SocketIoV2Error: соединение закрылось — переоткрывает вызывающий.
    """
    seen: dict = {}
    loop = asyncio.get_running_loop()

    while loop.time() < stop_at:
        try:
            event, args = await sock.next(min(30.0, max(0.1, stop_at - loop.time())))
        except asyncio.TimeoutError:
            continue
        if event != 'message' or not args or not isinstance(args[0], dict):
            continue

        kind = args[0].get('type')
        data = args[0].get('data') if isinstance(args[0].get('data'), dict) else {}

        if kind in ('server_m_new', 'server_jn_ch', 's_c_newbie'):
            await on_change()
        elif kind == 'server_c_update':
            message = data.get('message') if isinstance(data.get('message'), dict) else {}
            channel = message.get('channel') if isinstance(message.get('channel'), dict) else {}
            identity = str(channel.get('identity') or '')
            index = int(channel.get('index') or 0)
            # ⚠ Сигнал — на **смену** номера, а не на рост: удаление уменьшает
            # номер, и новое сообщение после него приходит с прежним
            # (Olena ↔ Donnie, 21.11.2026). Прочтение номер не меняет.
            if identity and index != seen.get(identity, -1):
                if identity in seen:
                    await on_change()
                seen[identity] = index


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


def _media_urls(message: dict) -> list[dict]:
    """Снимки сообщения полными адресами — та же форма, что у импорта."""
    from src.dating.site.site_import_luxee import site_import_luxee_media

    return site_import_luxee_media(message)


def _key(kind: str) -> str:
    """Ключ запроса в форме страницы: тип, микровремя, случайное число."""
    return f'{kind}{time.time()}-{random.randint(1, 500)}'


def _twin_key(text) -> str:
    """Ключ сравнения реплики с нашей: скелет, а при пустом — сам текст.

    ⚠⚠ Python-двойник `message_twin_same` целиком, а не только скелета: там
    реплика из одних знаков («:)», «?») сравнивается **точным текстом** без
    краевых пробелов. Ведущий `=` не даёт такому ключу совпасть со скелетом —
    в скелете знаков нет. Пустая строка — пусто: сравнивать нечего.
    """
    skeleton = _skeleton(text)
    if skeleton:
        return skeleton

    bare = str(text or '').strip()

    return '=' + bare if bare else ''


def _skeleton(text) -> str:
    """Скелет текста: буквы и цифры без регистра — та же мерка, что у близнецов.

    ⚠ Python-двойник `message_twin_clean` (там `REGEXP_REPLACE('[^[:alnum:]]')`):
    сверять приходится со строкой сокета, а не колонкой базы, — общего
    вызова нет. Расходятся на экзотике (SQL знает unicode-классы строже), для
    «hi»/«Hello!» это одно и то же; найдётся разница — править **оба** места
    разом. Пустой скелет близнецом не считается — тот же смысл, что в правиле
    близнецов: по пустоте сравнивать нельзя.
    """

    return ''.join(c for c in str(text or '') if c.isalnum()).casefold()
