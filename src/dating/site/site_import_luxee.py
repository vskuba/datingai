"""Luxee: переписка чата сокетом панели агентства — все диалоги, все сообщения.

У Luxee кабинета под девушкой нет: одна панель агентства («Models admin») на всех,
и чат в ней — страница `/chats/`, которая сама ничего не несёт, а грузит диалоги
событиями socket.io 2 (`/chat/socket.io`). Ходим тем же сокетом, без браузера:
GET страницы под сессией круга панели даёт токен, дальше — текстовые кадры
(клиент — `py_modules/socketio_/socketio_v2.py`).

    cm_get_profiles {uids}             → внешние анкеты девушек (по одной на проект)
    cl_ch_all / cl_ch_more {ownerUid}  → диалоги девушки страницами
    cl_c_messages {ownerUid, identity} → последние 20 сообщений диалога
    cl_cm_messages {…, token: index}   → двадцать раньше указанного номера

## ⚠⚠ Номер девушки — внутренний, автор сообщения — внешний

У девушки один номер панели (`622180`, он же `id` карточки у нас) и по анкете на
каждый проект агентства (`3161872`, `3161888`, …). Диалог зовётся
`{мужчина}_{внешняя анкета}`, и автор её реплики — **внешняя** анкета. Карту
«внешняя → внутренняя» отдаёт `cm_get_profiles` (`import_uid`); без этого запроса
сервер вдобавок отвечает пустым списком диалогов — замер 19.11.2026.

## ⚠⚠ «Прочитано» на выдачу не влияет — и мы его не ставим

Прочитанность живёт в сообщении отдельно (`memberStatus`: `status` 1/2,
`readAt`), и прочитанное девушкой руками приезжает так же, как непрочитанное.
Отметку ставит событие `cl_consume` — его мы **не шлём никогда**: чтение панели
не должно гасить счётчик, по которому оператор видит новое.

## ⚠⚠ Пропусков не бывает: номера в диалоге сквозные

У каждого диалога номер последнего сообщения (`index`), и сообщения нумерованы
подряд с единицы (замер: 437 из 437 без дыр). Помним по диалогу последний
**сохранённый** номер (Redis, `LUXEE_CHAT_MARKS`) и, когда он вырос, листаем
назад до него — сколько бы ни проспал круг. Диалог без отметки (первый заход,
пустой Redis) берётся окном дат круга. Отметка ставится **после записи**
(`journal_saved`): упади запись — следующий заход перечитает то же самое.
"""

import asyncio
import random
import re
import time
from datetime import datetime, timezone

from redis_.redis_ import redis_conn_get
from socketio_.socketio_v2 import SocketIoV2Error, socketio_v2_open

from src.dating.panel_fetch import DatingPanelFetchError, panel_fetch_html
from src.dating.site.site_import import DatingSiteImport

# Страница чата: несёт токен сокета (третий довод `ModelChatInstance`).
LUXEE_CHAT_PAGE = '/chats/'

# Путь socket.io на том же хосте, что и сайт.
LUXEE_CHAT_SOCKET = '/chat/socket.io/'

# Токен стоит сразу за объектом шаблонов, последний ключ которого — этот.
# ⚠ Рядом на странице ещё восемь JWT других служб (подарки, звонки, уведомления):
# брать «первый попавшийся» значило бы ловить «Permission denied» от сокета.
LUXEE_CHAT_TOKEN_RE = re.compile(
    r"skipTaskFormTemplate[^}]*\},\s*'(eyJ[A-Za-z0-9_.-]+)'", re.S)

# Что шлёт страница доводом `settings`; без него сервер отвечает тем же, но мы
# повторяем страницу, чтобы не зависеть от его умолчаний.
LUXEE_CHAT_SETTINGS = {'winkExclude': []}

# Сколько ждём ответа на один запрос. Сервер отвечает за десятые доли секунды.
LUXEE_CHAT_ASK_SEC = 20

# Предохранители от петли: сервер, вернувший ту же метку, крутил бы нас вечно.
# 50 страниц диалогов — тысяча собеседников у девушки; 500 страниц сообщений —
# десять тысяч реплик в одном диалоге.
LUXEE_CHAT_CHANNEL_PAGES = 50
LUXEE_CHAT_MESSAGE_PAGES = 500

# Отметки «последний сохранённый номер» по диалогам: хэш на сайт.
LUXEE_CHAT_MARKS = 'dating:luxee:chat:{site_id}'

# Сообщение без текста — тоже событие разговора, и показать его надо словом:
# пустой текст общий разбор выбрасывает. Вид — по `type` сообщения.
LUXEE_CHAT_KINDS = {3: '[wink]', 5: '[gift]', 6: '[gift request]', 8: '[sticker]'}


class DatingSiteImportLuxee(DatingSiteImport):
    """Чат панели Luxee сокетом (см. заголовок файла)."""

    name = 'luxee'

    def __init__(self, presence: dict | None = None):
        super().__init__(presence)
        self._pending = {}
        self._site_id = 0

    def date_fmt(self) -> str:
        return '%Y-%m-%d'

    def has_journal(self) -> bool:
        return True

    async def journal_rows(self, site_id: int, profiles: dict, date_from: str,
                           date_to: str) -> tuple[int, list[dict], str]:
        """Все новые сообщения всех диалогов наших девушек.

        Raises:
            DatingPanelFetchError: страница чата не отдала токен, сокет не
                открылся или не пустил. Беда одной девушки — строка в `why`.
        """
        self._site_id = int(site_id)
        self._pending = {}

        girls = sorted(int(uid) for uid in profiles if str(uid).isdigit())
        if not girls:
            return 0, [], ''

        base = str(self.presence.get('base') or '').rstrip('/')
        if not base.startswith('http'):
            raise DatingPanelFetchError('у сайта нет адреса `presence.base`')

        html = await panel_fetch_html(site_id, LUXEE_CHAT_PAGE,
                                      {'ownerUid': str(girls[0])})
        found = LUXEE_CHAT_TOKEN_RE.search(html or '')
        if not found:
            raise DatingPanelFetchError(
                'на странице чатов нет токена сокета — разметка сменилась?')

        since_ms = _ms(date_from)
        marks = _marks_get(site_id)

        try:
            async with socketio_v2_open(
                    'wss://' + base.split('://', 1)[1] + LUXEE_CHAT_SOCKET,
                    query={'token': found.group(1), 'type': 'model'},
                    headers={'Origin': base}) as sock:
                return await self._read(sock, girls, marks, since_ms)
        except SocketIoV2Error as e:
            raise DatingPanelFetchError(f'сокет чата: {e}')
        except asyncio.TimeoutError:
            raise DatingPanelFetchError('сокет чата не ответил на запрос')

    async def journal_saved(self, site_id: int) -> None:
        """Строки записаны — запоминаем, докуда прочитан каждый диалог."""
        if self._pending and int(site_id) == self._site_id:
            redis_conn_get().hset(LUXEE_CHAT_MARKS.format(site_id=int(site_id)),
                                  mapping=self._pending)
        self._pending = {}

    async def _read(self, sock, girls: list, marks: dict,
                    since_ms: int) -> tuple[int, list[dict], str]:
        """Обход по девушкам: диалоги, затем новые сообщения в каждом."""
        hello = await _wait(sock, lambda kind, data: kind == 's_ua_success')
        if not hello.get('status'):
            raise DatingPanelFetchError('сокет чата не принял токен')

        outer = (await _ask(sock, 'cm_get_profiles',
                            {'uids': girls, 'settings': LUXEE_CHAT_SETTINGS})
                 ).get('message')
        inner = {str(uid): str(card.get('import_uid') or '')
                 for uid, card in (outer or {}).items() if isinstance(card, dict)}

        total, rows, why = 0, [], []
        for girl in girls:
            try:
                channels = await _channels(sock, girl)
            except DatingPanelFetchError as e:
                why.append(f'{girl}: {e}')
                continue

            for channel in channels:
                identity = str(channel.get('identity') or '')
                index = int(channel.get('index') or 0)
                mark = marks.get(identity)

                if not identity or (mark is not None and index <= mark):
                    continue
                if mark is None and int(channel.get('lastActivity') or 0) < since_ms:
                    continue

                try:
                    messages = await _messages(sock, girl, identity, mark,
                                               since_ms if mark is None else 0)
                except DatingPanelFetchError as e:
                    why.append(f'{girl}/{identity}: {e}')
                    continue

                total += len(messages)
                rows += [row for row in (_row(m, inner) for m in messages) if row]
                self._pending[identity] = index

        return total, rows, '; '.join(why)


async def _ask(sock, kind: str, data: dict):
    """Запрос серверу чата и его ответ целиком (`message`, `token`).

    Сопоставление по `key` — так делает и страница: события приходят вперемешку
    со служебными (счётчики, «в сети»), и порядок ответов не гарантирован.
    """
    key = f'{kind}{time.time()}-{random.randint(1, 500)}'
    await sock.emit('message', {'type': kind, 'data': data, 'key': key})

    answer = await _wait(sock, lambda _kind, got: got.get('key') == key)
    if not answer.get('status'):
        raise DatingPanelFetchError(
            f"сокет отказал в {kind}: {answer.get('error') or 'status ложен'}")

    return answer


async def _wait(sock, match) -> dict:
    """Первое событие `message`, которое подходит под `match(type, data)`."""
    loop = asyncio.get_running_loop()
    end = loop.time() + LUXEE_CHAT_ASK_SEC

    while True:
        event, args = await sock.next(max(0.1, end - loop.time()))
        if event != 'message' or not args or not isinstance(args[0], dict):
            continue

        data = args[0].get('data')
        if isinstance(data, dict) and match(args[0].get('type'), data):
            return data


async def _channels(sock, girl: int) -> list[dict]:
    """Все диалоги девушки, страница за страницей по метке."""
    data = {'ownerUid': girl, 'settings': LUXEE_CHAT_SETTINGS}
    answer = await _ask(sock, 'cl_ch_all', data)

    channels = list(answer.get('message') or [])
    token = answer.get('token')
    for _ in range(LUXEE_CHAT_CHANNEL_PAGES):
        if not token:
            break

        answer = await _ask(sock, 'cl_ch_more', {**data, 'token': token})
        channels += list(answer.get('message') or [])
        token = answer.get('token') if answer.get('token') != token else None

    return [c for c in channels if isinstance(c, dict)]


async def _messages(sock, girl: int, identity: str, mark: int | None,
                    since_ms: int) -> list[dict]:
    """Сообщения диалога новее отметки (или окна дат, когда отметки нет)."""
    got = list((await _ask(sock, 'cl_c_messages',
                           {'ownerUid': girl, 'identity': identity})
                ).get('message') or [])

    for _ in range(LUXEE_CHAT_MESSAGE_PAGES):
        if not got:
            break

        oldest = min(got, key=lambda m: int(m.get('index') or 0))
        first = int(oldest.get('index') or 0)
        if first <= 1 or (mark is not None and first <= mark + 1):
            break
        if mark is None and int(oldest.get('created') or 0) < since_ms:
            break

        more = list((await _ask(sock, 'cl_cm_messages',
                                {'ownerUid': girl, 'identity': identity,
                                 'token': first})).get('message') or [])
        if not more:
            break
        got = more + got

    return [m for m in got if isinstance(m, dict)
            and (mark is None or int(m.get('index') or 0) > mark)]


def _row(message: dict, inner: dict) -> dict | None:
    """Сообщение сокета — строкой общего разбора (`chat_panel_rows`)."""
    author = str((message.get('author') or {}).get('uid') or '')
    members = (message.get('channel') or {}).get('members') or []
    names = {str(m.get('uid')): str(m.get('username') or m.get('first_name') or '')
             for m in members if isinstance(m, dict)}

    girl_outer = next((uid for uid in names if uid in inner), '')
    man = next((uid for uid in names if uid not in inner), '')
    girl = inner.get(girl_outer, '')
    if not girl or not man or not author:
        return None

    man_cell = f'{man} {names.get(man, "").strip()}'
    if author == man:
        side = {'from_uid': man, 'from': man_cell, 'to_uid': girl, 'to': girl}
    else:
        side = {'from_uid': girl, 'from': girl, 'to_uid': man, 'to': man_cell}

    return {'uid': str(message.get('sid') or ''), **side,
            'text': _text(message),
            'when': _when(message.get('created'))}


def _text(message: dict) -> str:
    """Текст сообщения; без текста — словом о том, что пришло."""
    body = str(message.get('body') or message.get('bodyOrigin') or '').strip()
    extra = []
    if message.get('media'):
        extra.append('[photo]')
    if message.get('sticker') and not body:
        extra.append('[sticker]')

    kind = LUXEE_CHAT_KINDS.get(int(message.get('type') or 0))
    if not body and not extra and kind:
        extra.append(kind)

    return ' '.join([body, *extra]).strip()


def _when(created) -> str:
    """Метка сервера в миллисекундах — `гггг-мм-дд чч:мм:сс` UTC."""
    try:
        moment = datetime.fromtimestamp(int(created) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return ''

    return moment.strftime('%Y-%m-%d %H:%M:%S')


def _ms(day: str) -> int:
    """Начало дня окна (`гггг-мм-дд`, UTC) — в миллисекундах."""
    try:
        moment = datetime.strptime(str(day), '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return 0

    return int(moment.timestamp() * 1000)


def _marks_get(site_id: int) -> dict:
    """Отметки диалогов сайта: `identity → последний сохранённый номер`."""
    raw = redis_conn_get().hgetall(LUXEE_CHAT_MARKS.format(site_id=int(site_id))) or {}

    found = {}
    for key, value in raw.items():
        key = key.decode() if isinstance(key, bytes) else str(key)
        try:
            found[key] = int(value)
        except (TypeError, ValueError):
            continue

    return found
