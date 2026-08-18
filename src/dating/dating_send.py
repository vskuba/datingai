"""Отправка сообщения в чат — тем же путём, каким его шлёт страница сайта.

Отдельным модулем, а не внутри проверки: отправку зовут и из проверки, и из
будущих цепочек ответов, и правило у неё одно — писать по настройке сайта
(`browser_site.presence.send`), а не по зашитому в код адресу.

**Транспорт задаётся настройкой.** У Goldenbride чат живёт вебсокетом: страница
шлёт кадр `sendPrivateMessage` в то же соединение, откуда читает входящие, — это
снято с живой страницы, HTTP-ручки отправки у сайта нет. Другие сайты шлют
обычным POST, поэтому остаются оба пути: `transport: "ws"` и `transport: "http"`.

Отказ сайта («нет средств», «нужно приглашение») ошибкой **не считается**:
механика цела, просто действие платное. Отличить одно от другого умеет только
вызывающий, поэтому здесь возвращается причина, а решение — на нём.

**Соединение обязано согласовать подпротокол `chat`.** Без него сокет
устанавливается, входящие приходят — а всё, что шлёт клиент, сервер игнорирует
молча: ни ответа, ни ошибки. Ровно из-за этого сообщения месяцами уходили в
никуда, тогда как те же кадры со страницы доходили. Разница нашлась только в
заголовках рукопожатия браузера (`Sec-WebSocket-Protocol: chat`).

Доставку подтверждает сам сервер кадром `confirmFromServer`. Его отсутствие —
не мелочь: это и есть «не доставлено». Отказ сайт называет прямо, например
`limitSecond` с текстом «Message not delivered. Unacceptable words: …» — у него
свой фильтр содержимого, и такой ответ надо показывать человеку, а не прятать.
"""

import asyncio
import json
import secrets
import time

import httpx
import websockets

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_presence import DATING_PRESENCE_TIMEOUT, DATING_PRESENCE_UA

# Метки подстановки в шаблоне кадра или тела: сайты называют поля по-разному, и
# подстановка избавляет от кода под каждый.
DATING_SEND_TO = '{{to}}'
DATING_SEND_TEXT = '{{text}}'
DATING_SEND_UID = '{{uid}}'

# Сколько ждать ответного кадра после отправки. Сайт отвечает за доли секунды,
# а молчание — обычное дело: подтверждения шлют не все.
DATING_SEND_ACK_SEC = 6.0

# Идентификатор сообщения: 26-значный ULID, как у страницы. Первые десять знаков
# — время в миллисекундах, остальные случайны. Алфавит Crockford base32 (без
# I, L, O, U, чтобы не путались с цифрами).
DATING_SEND_UID_LEN = 26
DATING_SEND_ULID_TIME_LEN = 10
DATING_SEND_ULID_ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'

# Подпротокол вебсокета. Браузер согласует `chat` заголовком
# `Sec-WebSocket-Protocol`, и сервер отвечает тем же. Без него соединение
# устанавливается и даже принимает входящие, но **всё присланное клиентом
# игнорируется молча**: ни ответа, ни ошибки. Именно поэтому наши сообщения
# уходили в никуда, а те же кадры со страницы доходили.
DATING_SEND_SUBPROTOCOL = 'chat'

# Отказ сайта. Методов у него несколько (`limit`, `limitSecond`, …), а общая часть
# одна — он прямо пишет «Message not delivered» и объясняет причину: запрещённые
# слова, не чаще одного сообщения в минуту и так далее. По этой фразе и узнаём,
# а не по перечню методов, который назавтра пополнится.
DATING_SEND_REFUSE_MARK = 'not delivered'

# Собеседника нет в чате. Это не поломка и не отказ по правилам: в чате говорят
# с тем, кто в чате. Сайт отвечает `offlineUser`, и такой ответ надо называть
# своим именем — иначе «не подтвердил приём» выглядит как сбой у нас.
DATING_SEND_OFFLINE_MARK = 'offlineUser'

# Подтверждение сервера на принятое сообщение. Его отсутствие — не мелочь: без
# него сообщение не доставлено.
DATING_SEND_ACK_MARK = 'confirmFromServer'

# Приветствие сервера, после которого он начинает слушать клиента. Второе имя —
# для случая, когда соединение по этой учётке уже было.
DATING_SEND_HELLO_MARKS = ('connectFromServer', 'reconnectFromServer')

# Сколько ждать приветствия. Оно приходит за доли секунды; дольше ждать значит,
# что соединение не поднялось.
DATING_SEND_HELLO_SEC = 15.0

# Прелюдия перед сообщением — ровно та, что шлёт страница сайта. Без неё сервер
# принимает кадр и не доставляет его никому.
DATING_SEND_BEFORE = ('getHistoryByContactIds', 'getChatInfo', 'startTyping', 'stopTyping')

# Кадры, которым номер собеседника идёт в `toId`.
DATING_SEND_BEFORE_TO = ('startTyping', 'stopTyping')

# А этим — в `content`, через запятую: так страница открывает диалог.
DATING_SEND_BEFORE_CONTENT = ('getHistoryByContactIds',)

# Пауза между кадрами прелюдии: страница шлёт их не залпом, и сервер успевает
# обработать каждый.
DATING_SEND_BEFORE_PAUSE = 0.4


async def dating_send_message(girl: dict, presence: dict, to_uid: str, text: str) -> dict:
    """Шлёт сообщение собеседнику и записывает его в переписку.

    Args:
        girl: строка `dating_girl`; для вебсокета нужен непустой `ws_url`.
        presence: настройка сайта, раздел `send`.
        to_uid: кому — идентификатор на сайте.
        text: что отправляем.

    Returns:
        dict: `ok` — **принял ли сайт кадр**, а не «дошло до адресата»: это разные
        вещи, и путать их дорого. Проверено на живых учётках: сообщение от женской
        анкеты сайт принимает и показывает в её же переписке, но мужчине оно не
        приходит даже когда тот сидит в чате и получает в это же время письма от
        других. Доставку открывает приглашение с его стороны (`needInvite`).
        Ещё `error` — причина отказа, `response` — ответ сайта, `message_uid` —
        под каким номером ушло.
    """
    send = presence.get('send') or {}
    if not send:
        return _fail('у сайта не настроена отправка (presence.send)')

    message_uid = dating_send_uid()
    transport = str(send.get('transport') or 'http').lower()

    if transport == 'ws':
        result = await _send_ws(girl, send, to_uid, text, message_uid)
    else:
        result = await _send_http(girl, presence, send, to_uid, text, message_uid)

    # Записываем только принятое: строка в переписке означает «это мы сказали», и
    # непринятый сайтом текст показывать в диалоге нельзя.
    if result['ok']:
        await _message_save(int(girl['id']), to_uid, text, message_uid)
        await _answered_mark(int(girl['id']), to_uid)

    return result


def dating_send_uid() -> str:
    """Идентификатор сообщения в том же виде, в каком его делает страница — ULID.

    Это не косметика. ULID несёт внутри время отправки: первые десять знаков —
    миллисекунды, дальше случайность. Наш прежний идентификатор был просто
    шестнадцатеричной строкой, и время в нём читалось как 14554 год.

    Разбор живого обмена: сообщения, отправленные с настоящей страницы, доходят,
    а точно такие же кадры от нашего клиента — нет. Единственное различие —
    идентификатор.
    """
    stamp = int(time.time() * 1000)
    head = ''
    for _ in range(DATING_SEND_ULID_TIME_LEN):
        head = DATING_SEND_ULID_ALPHABET[stamp % 32] + head
        stamp //= 32

    tail = ''.join(secrets.choice(DATING_SEND_ULID_ALPHABET)
                   for _ in range(DATING_SEND_UID_LEN - DATING_SEND_ULID_TIME_LEN))

    return head + tail


async def _send_ws(girl: dict, send: dict, to_uid: str, text: str, message_uid: str) -> dict:
    """Кадр в чат-сокет — так шлёт сама страница сайта."""
    url = str(girl.get('ws_url') or '').strip()
    if not url:
        return _fail('адрес чат-сокета не снят: сначала «Залогиниться»')

    frame = _frame_build(send, to_uid, text, message_uid)
    ack = send.get('ack') or {}

    try:
        async with websockets.connect(url, open_timeout=DATING_PRESENCE_TIMEOUT,
                                      subprotocols=[str(send.get('subprotocol')
                                                        or DATING_SEND_SUBPROTOCOL)]) as socket:
            # Сначала дожидаемся приветствия сервера. До него он присланное молча
            # игнорирует — без ошибки, без отказа, просто в никуда. Именно так
            # уходили все наши сообщения: сайт принимал кадр, показывал его в
            # переписке отправителя, а адресату не доставлял ничего.
            if not await _hello_wait(socket):
                return _fail('чат не поздоровался: сообщение слать некуда')

            await _open_dialog(socket, to_uid, send)
            await socket.send(json.dumps(frame, ensure_ascii=False))
            answer = await _ack_wait(socket, ack, message_uid)
    except Exception as e:
        return _fail(f'{type(e).__name__}: {e}')

    if DATING_SEND_OFFLINE_MARK in answer:
        return {'ok': False, 'error': 'собеседник вышел из чата — сообщение не принимается',
                'offline': True, 'response': answer, 'message_uid': message_uid}

    refuse = str(ack.get('refuse_mark') or DATING_SEND_REFUSE_MARK).strip()
    if refuse and refuse.lower() in answer.lower():
        return {'ok': False, 'error': f'сайт отказал: {_refuse_reason(answer)}',
                'response': answer, 'message_uid': message_uid}

    # Принято сервером — это его собственное подтверждение, а не отсутствие
    # ошибки. Молчание здесь означало ровно то, что сообщение никуда не пошло:
    # ни адресату, ни в очередь. Раньше мы считали такое молчание удачей.
    mark = str(ack.get('ok_mark') or DATING_SEND_ACK_MARK)
    if mark and mark not in answer:
        return {'ok': False, 'error': 'сайт не подтвердил приём сообщения',
                'response': answer, 'message_uid': message_uid}

    return {'ok': True, 'error': '', 'response': answer, 'message_uid': message_uid}


async def _send_http(girl: dict, presence: dict, send: dict, to_uid: str, text: str,
                     message_uid: str) -> dict:
    """Обычный POST — путь сайтов, у которых отправка живёт в HTTP-ручке."""
    if not send.get('path'):
        return _fail('у сайта не задан адрес отправки (presence.send.path)')

    cookies = _cookies(girl, presence)
    if not cookies:
        return _fail('вход не выполнен')

    body = _render(str(send.get('body') or ''), to_uid, text, message_uid)

    try:
        async with httpx.AsyncClient(timeout=DATING_PRESENCE_TIMEOUT, cookies=cookies,
                                     follow_redirects=True,
                                     headers={'User-Agent': DATING_PRESENCE_UA,
                                              'Content-Type': str(send.get('content_type')
                                                                  or 'application/x-www-form-urlencoded'),
                                              **(presence.get('headers') or {})}) as client:
            response = await client.post(str(presence.get('base', '')).rstrip('/') + send['path'],
                                         content=body)
    except Exception as e:
        return _fail(f'{type(e).__name__}: {e}')

    answer = response.text[:500]
    # Признак удачи задаёт сайт: у одних это поле в JSON, у других код ответа.
    mark = str(send.get('ok_mark') or '').strip()
    ok = response.status_code == 200 and (mark in answer if mark else True)

    return {'ok': ok, 'response': answer, 'message_uid': message_uid,
            'error': '' if ok else f'сайт ответил {response.status_code}: {answer[:200]}'}


def _frame_build(send: dict, to_uid: str, text: str, message_uid: str) -> dict:
    """Кадр отправки по шаблону из настройки, с подставленными значениями."""
    template = send.get('frame') or {'toId': DATING_SEND_TO, 'content': DATING_SEND_TEXT,
                                     'method': 'sendPrivateMessage',
                                     'messageIdForConfirm': DATING_SEND_UID}

    return {key: _render(str(value), to_uid, text, message_uid)
            for key, value in template.items()}


async def _open_dialog(socket, to_uid: str, send: dict) -> None:
    """Повторяет то, что страница делает перед отправкой.

    Снято с живой страницы: после приветствия она спрашивает историю и сведения о
    чате, а перед самим сообщением шлёт `startTyping`/`stopTyping`. Наш клиент
    слал только сообщение — сервер принимал кадр и никому его не отдавал.

    Что именно слать, задаётся настройкой (`send.before`): у другого сайта
    прелюдия будет своя, а править ради этого Python не должно.
    """
    steps = send.get('before')
    if steps is None:
        steps = DATING_SEND_BEFORE
    if not steps:
        return

    for method in steps:
        # У каждого кадра прелюдии своё место для номера собеседника, и это не
        # придирка к форме: `getHistoryByContactIds` именно полем `content`
        # открывает диалог. С пустым `content` сервер принимал сообщение и не
        # доставлял его никому — так пропали все наши прежние отправки.
        frame = {'toId': '', 'content': '', 'method': str(method)}
        if method in DATING_SEND_BEFORE_TO:
            frame['toId'] = str(to_uid)
        if method in DATING_SEND_BEFORE_CONTENT:
            frame['content'] = f'{to_uid},'

        try:
            await socket.send(json.dumps(frame))
            await asyncio.sleep(DATING_SEND_BEFORE_PAUSE)
        except Exception as e:
            logger_info(f'[dating] подготовка «{method}» не ушла: {e}')
            return


async def _hello_wait(socket) -> bool:
    """Ждёт приветствие сервера — до него отправлять бесполезно.

    Сервер здоровается кадром `connectFromServer` (после разрыва —
    `reconnectFromServer`) и только после этого начинает слушать клиента. Ровно
    так же ведёт себя страница сайта: сначала приветствие, потом всё остальное.
    """
    deadline = time.monotonic() + DATING_SEND_HELLO_SEC

    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=deadline - time.monotonic())
        except Exception:
            return False

        if any(mark in str(raw) for mark in DATING_SEND_HELLO_MARKS):
            return True

    return False


async def _ack_wait(socket, ack: dict, message_uid: str) -> str:
    """Ждёт ответный кадр про наше сообщение. Молчание — не ошибка.

    Кадры чата идут потоком: входящие сообщения, статусы камер, пинги. Наш —
    тот, где встретился номер отправленного сообщения либо имя метода из
    настройки. Всё остальное пропускаем, а не считаем ответом.
    """
    method = str(ack.get('method') or '').strip()
    mark = str(ack.get('ok_mark') or DATING_SEND_ACK_MARK)
    deadline = time.monotonic() + DATING_SEND_ACK_SEC
    seen = []

    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            break
        except Exception:
            break

        answer = raw if isinstance(raw, str) else str(raw)
        if message_uid not in answer and not (method and f'"{method}"' in answer):
            continue

        seen.append(answer[:300])
        # На принятое сообщение сервер отвечает двумя кадрами: сначала эхо
        # клиенту, потом собственное подтверждение. Ждём именно второе — на
        # первом останавливаться значит принять эхо за доставку.
        if mark in answer:
            break

    return ' | '.join(seen)


def _refuse_reason(answer: str) -> str:
    """Причина отказа человеческими словами, а не кадром целиком."""
    for line in answer.split('\\"'):
        if 'not delivered' in line.lower() or 'unacceptable' in line.lower():
            return line.strip()

    return answer[:200]


def _render(template: str, to_uid: str, text: str, message_uid: str) -> str:
    return (template.replace(DATING_SEND_TO, str(to_uid))
                    .replace(DATING_SEND_TEXT, str(text))
                    .replace(DATING_SEND_UID, str(message_uid)))


def _cookies(girl: dict, presence: dict) -> dict:
    state = girl.get('storage_state')
    if isinstance(state, str):
        try:
            state = json.loads(state or '{}')
        except Exception:
            return {}
    host = str(presence.get('base', '')).split('//')[-1].split('/')[0].replace('www.', '')

    return {str(c['name']): str(c['value']) for c in (state or {}).get('cookies', [])
            if host in str(c.get('domain') or '')}


async def _message_save(girl_id: int, to_uid: str, text: str, message_uid: str) -> None:
    """Кладёт отправленное в переписку — иначе в диалоге видна только их половина."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT IGNORE INTO dating_message '
                '(girl_id, sender_uid, sender_name, text, message_uid, direction, is_answered) '
                "VALUES (%s, %s, %s, %s, %s, 'out', 1)",
                (girl_id, str(to_uid)[:32], '', str(text)[:2000], str(message_uid)[:64]))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] отправленное не записалось: {e}')


async def _answered_mark(girl_id: int, to_uid: str) -> None:
    """Помечает входящие этого собеседника отвеченными: мы только что ответили."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE dating_message SET is_answered = 1 '
                " WHERE girl_id = %s AND sender_uid = %s AND direction = 'in' AND is_answered = 0",
                (girl_id, str(to_uid)[:32]))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] пометка «отвечено» не проставилась: {e}')


def _fail(error: str) -> dict:
    return {'ok': False, 'error': error, 'offline': False,
            'response': '', 'message_uid': ''}
