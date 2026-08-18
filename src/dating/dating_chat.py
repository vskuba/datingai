"""Живой чат: держим вебсокет учётки и складываем входящие сообщения.

Сообщения от собеседниц приходят кадрами `receivePrivateMessage` — отправитель,
текст и идентификатор для подтверждения. HTTP-опросом их не достать: сайт шлёт их
сокетом и только сокетом.

**Сокет держит этот модуль, а не браузер.** Адрес сокета собирает JS страницы, и в
разметке его нет, поэтому снимается он браузером один раз (`browser_ws`) и живёт
в `dating_girl.ws_url`. Дальше подключение стоит десятки килобайт вместо ~143 МБ
на вкладку — двести учёток укладываются в единицы мегабайт.

Каждое сообщение подтверждается обратным кадром `confirmFromClient`: без него
сайт считает его недоставленным и шлёт заново при следующем подключении. Повторы
всё равно ловятся уникальным ключом по `message_uid` — подтверждение может не
дойти, и дважды показать одно письмо человеку хуже, чем промолчать.
"""

import asyncio
import json

import websockets

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_log import dating_log_message_in

# Кадры, которые нас касаются. Остальное (billings, статусы камер) чат шлёт для
# своей вёрстки, и разбирать его здесь нечего.
DATING_CHAT_METHOD_MESSAGE = 'receivePrivateMessage'
DATING_CHAT_METHOD_CONFIRM = 'confirmFromClient'

# Сервер здоровается первым, и до этого кадра он присланное клиентом игнорирует:
# проверено — отправленные сразу после подключения запросы остаются без ответа.
DATING_CHAT_METHOD_HELLO = 'connectFromServer'

# Что спросить после приветствия — ровно это шлёт настоящая страница чата.
DATING_CHAT_HELLO_ASK = ('getHistoryByContactIds', 'getChatInfo')

# Кадр с накопившимся: сервер отдаёт то, что пришло, пока соединения не было.
DATING_CHAT_METHOD_PENDING = 'notSendMessages'

# **Номера собеседников обязательны в запросе истории.** С пустым `content`
# сервер отвечает `notSendMessages: []` — как будто ничего и не было; те же
# сообщения он отдаёт, стоит перечислить собеседников через запятую. Именно так
# терялось всё, что приходило между подключениями: очередь на сервере была, а мы
# считали её пустой.
DATING_CHAT_ASK_WITH_IDS = ('getHistoryByContactIds',)

# Текст режем: в чат кидают и простыни, а в карточке всё равно видно первые строки.
DATING_CHAT_TEXT_MAX = 2000

# Сколько собеседников перечисляем в запросе. Сервер принимает список, но у
# анкеты с сотней диалогов строка вырастет до килобайта — берём свежие.
DATING_CHAT_ASK_CONTACTS_MAX = 50

# Сколько ждать первого кадра, прежде чем считать подключение неудачным.
DATING_CHAT_HELLO_TIMEOUT = 20.0

# Подпротокол вебсокета — тот же, что согласует браузер (`Sec-WebSocket-Protocol`).
# Без него сервер шлёт входящие, но не отвечает ни на один наш запрос: молча, без
# ошибки. Приём от этого не страдал, а вот отправка не работала вовсе.
DATING_CHAT_SUBPROTOCOL = 'chat'

# Сколько терпим полную тишину. Сайт сам шлёт `ping` каждые 15-20 секунд, поэтому
# полторы минуты без единого кадра означают мёртвое соединение, а не затишье в чате.
#
# Это не придирка: живое наблюдение — соединение проработало час, приняв 711
# сообщений, потом сайт перестал слать что-либо, а разрыва не случилось. Молчание
# длилось восемь часов, и учётка всё это время выглядела работающей.
DATING_CHAT_IDLE_MAX = 90.0


async def dating_chat_listen(girl: dict, seconds: float = 0) -> dict:
    """Подключается к чат-сокету учётки и складывает входящие сообщения.

    Args:
        girl: строка `dating_girl` с непустым `ws_url`.
        seconds: сколько слушать; 0 — до обрыва соединения.

    Returns:
        dict: `saved` — сколько сообщений записано, `frames` — сколько кадров
        пришло всего, `error` — причина обрыва, если он был.
    """
    url = str(girl.get('ws_url') or '').strip()
    if not url:
        return {'saved': 0, 'frames': 0, 'error': 'адрес сокета не снят: нужен вход'}

    saved = frames = 0
    deadline = asyncio.get_event_loop().time() + seconds if seconds else None

    try:
        async with websockets.connect(url, open_timeout=DATING_CHAT_HELLO_TIMEOUT,
                                      subprotocols=[DATING_CHAT_SUBPROTOCOL],
                                      ping_interval=20) as socket:
            while True:
                left = DATING_CHAT_IDLE_MAX
                if deadline:
                    left = deadline - asyncio.get_event_loop().time()
                    if left <= 0:
                        break
                    left = min(left, DATING_CHAT_IDLE_MAX)

                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=left)
                except asyncio.TimeoutError:
                    # Дошли до собственного срока — работа окончена, всё в порядке.
                    if deadline and deadline - asyncio.get_event_loop().time() <= 0:
                        break
                    # Иначе это тишина при живом с виду сокете: сайт молчит даже
                    # своими `ping`. Такое соединение мёртвое, и держать его —
                    # значит пропускать сообщения, считая, что всё хорошо.
                    return {'saved': saved, 'frames': frames,
                            'error': f'молчит {int(DATING_CHAT_IDLE_MAX)} с — соединение мертво'}

                frames += 1

                # Приветствие сервера — сигнал, что нас слушают. Только теперь
                # есть смысл спрашивать накопившееся.
                if _method_of(raw) == DATING_CHAT_METHOD_HELLO:
                    await _hello_ask(socket, int(girl['id']))
                    continue

                # Накопившееся за время без соединения: сервер отдаёт его пачкой
                # в ответ на запрос истории, а не отдельными сообщениями.
                if _method_of(raw) == DATING_CHAT_METHOD_PENDING:
                    saved += await _pending_take(socket, int(girl['id']), raw)
                    continue

                message = _message_parse(raw, str(girl.get('login') or ''))
                if not message:
                    continue

                if await _message_save(int(girl['id']), message):
                    saved += 1
                    # Строка журнала пишется здесь, а не у вызывающего: сообщение
                    # ловит и проверка, и держатель сокетов, а видеть его в ленте
                    # человек должен в обоих случаях.
                    await dating_log_message_in(int(girl['id']), message['sender_uid'],
                                                message['sender_name'], message['text'])
                # Подтверждаем доставку: без этого сайт пришлёт то же самое снова.
                await _confirm(socket, message)
    except Exception as e:
        return {'saved': saved, 'frames': frames, 'error': f'{type(e).__name__}: {e}'}

    return {'saved': saved, 'frames': frames, 'error': ''}


def _method_of(raw) -> str:
    """Имя метода кадра. Не разобрался — пустая строка, а не исключение."""
    try:
        frame = json.loads(raw)
    except Exception:
        return ''

    return str(frame.get('method') or '') if isinstance(frame, dict) else ''


async def _hello_ask(socket, girl_id: int) -> None:
    """Спрашивает накопившееся — теми же кадрами, что шлёт страница чата.

    Не роняет слушание: живые сообщения придут и без ответа на эти запросы, а
    оборвать чтение из-за неудачного вопроса значило бы потерять и их.
    """
    contacts = await _contacts_of(girl_id)

    for method in DATING_CHAT_HELLO_ASK:
        content = contacts if method in DATING_CHAT_ASK_WITH_IDS else ''
        try:
            await socket.send(json.dumps({'toId': '', 'content': content, 'method': method}))
        except Exception as e:
            logger_info(f'[dating] запрос «{method}» не ушёл: {e}')
            return


async def _contacts_of(girl_id: int) -> str:
    """Собеседники учётки строкой «uid,uid,» — в таком виде их ждёт сервер.

    Берём и тех, кто уже писал (`dating_message`), и список с сайта
    (`dating_contact`). Одних написавших мало: у свежей анкеты их ноль, строка
    выходит пустой, а на пустой сервер отвечает `notSendMessages: []` — как будто
    накопившегося нет. Первое же письмо, пришедшее до подключения, так и терялось:
    в очереди на сервере оно лежало, а спросить про него было некем.
    """
    uids: list[str] = []
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT DISTINCT sender_uid AS uid FROM dating_message '
                             ' WHERE girl_id = %s ORDER BY sender_uid DESC LIMIT %s',
                             (int(girl_id), DATING_CHAT_ASK_CONTACTS_MAX))
            uids += [str(row['uid']) for row in (await db.fetchall() or [])]

            await db.execute('SELECT uid FROM dating_contact WHERE girl_id = %s '
                             ' ORDER BY is_online DESC, id DESC LIMIT %s',
                             (int(girl_id), DATING_CHAT_ASK_CONTACTS_MAX))
            uids += [str(row['uid']) for row in (await db.fetchall() or [])]
    except Exception as e:
        logger_info(f'[dating] список собеседников не прочитался: {e}')
        return ''

    # Порядок сохраняем, повторы убираем: написавшие идут первыми — про них
    # спросить важнее, чем про случайного онлайн-мужчину из общего списка.
    seen, ordered = set(), []
    for uid in uids:
        if uid and uid not in seen:
            seen.add(uid)
            ordered.append(uid)

    return ''.join(f'{uid},' for uid in ordered[:DATING_CHAT_ASK_CONTACTS_MAX])


async def _pending_take(socket, girl_id: int, raw) -> int:
    """Разбирает накопившееся из `notSendMessages` и складывает как обычные сообщения.

    Оно приходит другой формой, чем живое: без вложенного JSON, с полями `id` и
    `text`. Приводим к общему виду, чтобы дальше путь был один.
    """
    try:
        frame = json.loads(raw)
        items = json.loads(frame.get('message') or '[]')
    except Exception:
        return 0

    if not isinstance(items, list) or not items:
        return 0

    saved = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        message = {'sender_uid': str(item.get('id') or '')[:32],
                   'sender_name': '',
                   'text': str(item.get('text') or '').strip()[:DATING_CHAT_TEXT_MAX],
                   'message_uid': str(item.get('messageIdForConfirm') or '')[:64]}
        if not message['sender_uid'] or not message['text']:
            continue

        if await _message_save(girl_id, message):
            saved += 1
            await dating_log_message_in(girl_id, message['sender_uid'], '', message['text'])
        await _confirm(socket, message)

    return saved


def _message_parse(raw, girl_login: str = '') -> dict | None:
    """Кадр сокета → сообщение. Чужие кадры и мусор дают None.

    Тело приходит строкой с JSON внутри JSON — так его шлёт сам сайт, поэтому
    разбор двухступенчатый.
    """
    try:
        frame = json.loads(raw)
    except Exception:
        return None

    if not isinstance(frame, dict) or frame.get('method') != DATING_CHAT_METHOD_MESSAGE:
        return None

    body = frame.get('message')
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            return None

    if not isinstance(body, dict):
        return None

    text = str(body.get('message') or '').strip()
    sender = str(body.get('senderUid') or '').strip()
    if not text or not sender:
        return None

    # Своё же сообщение, вернувшееся эхом, входящим не считаем: показать человеку
    # его собственный текст как новый — хуже, чем не показать ничего.
    #
    # Отличаем по отправителю, а не по `isMan`. Это поле говорит, мужчина ли
    # написал, а вовсе не «мы ли»: у женской анкеты входящие от мужчин помечены
    # `isMan: true`, и проверка на него глушила ровно те сообщения, ради которых
    # всё и затевалось.
    if sender and sender == str(girl_login or ''):
        return None

    return {'sender_uid': sender[:32],
            'sender_name': str(body.get('senderName') or '')[:190],
            'text': text[:DATING_CHAT_TEXT_MAX],
            'message_uid': str(body.get('messageIdForConfirm') or '')[:64]}


async def _message_save(girl_id: int, message: dict) -> bool:
    """Кладёт сообщение. Повтор (тот же `message_uid`) молча пропускается."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT IGNORE INTO dating_message '
                '(girl_id, sender_uid, sender_name, text, message_uid) VALUES (%s, %s, %s, %s, %s)',
                (girl_id, message['sender_uid'], message['sender_name'],
                 message['text'], message['message_uid']))
            added = bool(db.rowcount)
            await db.connection.commit()

        return added
    except Exception as e:
        logger_info(f'[dating] сообщение не записалось: {e}')
        return False


async def _confirm(socket, message: dict) -> None:
    """Подтверждает доставку. Не вышло — не беда: повтор поймает уникальный ключ."""
    if not message['message_uid']:
        return

    try:
        await socket.send(json.dumps({'toId': message['sender_uid'], 'content': '',
                                      'method': DATING_CHAT_METHOD_CONFIRM,
                                      'messageIdForConfirm': message['message_uid']}))
    except Exception as e:
        logger_info(f'[dating] подтверждение не ушло: {e}')
