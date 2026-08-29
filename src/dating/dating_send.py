"""Отправка сообщения в чат — тем же путём, каким его шлёт страница сайта.

Отдельным модулем, а не внутри проверки: отправку зовут и из проверки, и из
цепочек ответов, и правило у неё одно — писать по настройке сайта
(`browser_site.presence.send`), а не по зашитому в код адресу.

**Транспорт задаётся настройкой.** У одних сайтов чат живёт вебсокетом: страница
шлёт кадр в то же соединение, откуда читает входящие, и HTTP-ручки отправки у
сайта нет вовсе. Другие шлют обычным POST, поэтому остаются оба пути:
`transport: "ws"` и `transport: "http"`.

Отказ сайта («нет средств», «нужно приглашение») ошибкой **не считается**:
механика цела, просто действие платное. Отличить одно от другого умеет только
вызывающий, поэтому здесь возвращается причина, а решение — на нём.

**Как выглядят кадры и что считать доставкой — знает драйвер сайта**
(`src/dating/site/`). Здесь остаётся порядок разговора: дождаться приветствия,
открыть диалог, послать, дождаться ответа. Порядок один на все сайты, а имена
методов и форма кадра — свои у каждого.
"""

import asyncio
import json
import time

import httpx
import websockets

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_presence import DATING_PRESENCE_TIMEOUT, DATING_PRESENCE_UA
from src.dating.site.site_base import dating_site_render
from src.dating.site.site_registry import dating_site_chat

# Сколько ждать ответного кадра после отправки. Сайт отвечает за доли секунды,
# а молчание — обычное дело: подтверждения шлют не все.
DATING_SEND_ACK_SEC = 6.0

# Сколько ждать приветствия. Оно приходит за доли секунды; дольше ждать значит,
# что соединение не поднялось.
DATING_SEND_HELLO_SEC = 15.0

# Пауза между кадрами прелюдии: страница шлёт их не залпом, и сервер успевает
# обработать каждый.
DATING_SEND_BEFORE_PAUSE = 0.4


async def dating_send_message(profile: dict, presence: dict, to_uid: str, text: str) -> dict:
    """Шлёт сообщение собеседнику и записывает его в переписку.

    Args:
        profile: строка `dating_profile`; для вебсокета нужен непустой `ws_url`.
        presence: настройка сайта, раздел `send`.
        to_uid: кому — идентификатор на сайте.
        text: что отправляем.

    Returns:
        dict: `ok` — **принял ли сайт кадр**, а не «дошло до адресата»: это разные
        вещи, и путать их дорого. Проверено на живых учётках: сообщение от женской
        анкеты сайт принимает и показывает в её же переписке, но мужчине оно не
        приходит даже когда тот сидит в чате и получает в это же время письма от
        других. Доставку открывает приглашение с его стороны (`needInvite`).
        Ещё `error` — причина отказа, `offline` — собеседника нет в чате (это
        состояние, а не поломка), `response` — ответ сайта, `message_uid` — под
        каким номером ушло.

        **Ключи все пять и всегда**, каким бы путём ни шла отправка и чем бы ни
        кончилась. Читателю не приходится помнить, какая ветка что кладёт, а
        `offline` со значением `False` отличается от `offline`, которого нет.
    """
    send = presence.get('send') or {}
    if not send:
        return _fail('у сайта не настроена отправка (presence.send)')

    site = dating_site_chat(presence)
    message_uid = site.message_uid()
    transport = str(send.get('transport') or 'http').lower()

    if transport == 'ws':
        result = await _send_ws(profile, site, to_uid, text, message_uid)
    else:
        result = await _send_http(profile, presence, send, to_uid, text, message_uid)

    # Записываем только принятое: строка в переписке означает «это мы сказали», и
    # непринятый сайтом текст показывать в диалоге нельзя.
    if result['ok']:
        await _message_save(int(profile['id']), to_uid, text, message_uid)
        await _answered_mark(int(profile['id']), to_uid)

    return result


async def _send_ws(profile: dict, site, to_uid: str, text: str, message_uid: str) -> dict:
    """Кадр в чат-сокет — так шлёт сама страница сайта.

    Порядок разговора здесь, форма кадров — у драйвера сайта (`site`).
    """
    url = str(profile.get('ws_url') or '').strip()
    if not url:
        return _fail('адрес чат-сокета не снят: сначала «Залогиниться»')

    frame = site.send_frame(to_uid, text, message_uid)

    try:
        async with websockets.connect(url, open_timeout=DATING_PRESENCE_TIMEOUT,
                                      subprotocols=[site.subprotocol()]) as socket:
            # Сначала дожидаемся приветствия сервера. До него он присланное молча
            # игнорирует — без ошибки, без отказа, просто в никуда. Именно так
            # уходили все наши сообщения: сайт принимал кадр, показывал его в
            # переписке отправителя, а адресату не доставлял ничего.
            if not await _hello_wait(socket, site):
                return _fail('чат не поздоровался: сообщение слать некуда')

            await _open_dialog(socket, to_uid, site)
            await socket.send(json.dumps(frame, ensure_ascii=False))
            answer = await _ack_wait(socket, site, message_uid)
    except Exception as e:
        return _fail(f'{type(e).__name__}: {e}')

    # Что означает ответ — решает драйвер: у каждого сайта свои метки отказа,
    # свой признак «нет в чате» и своё подтверждение доставки.
    read = site.ack_read(answer, message_uid)

    return {**read, 'response': answer, 'message_uid': message_uid}


async def _send_http(profile: dict, presence: dict, send: dict, to_uid: str, text: str,
                     message_uid: str) -> dict:
    """Обычный POST — путь сайтов, у которых отправка живёт в HTTP-ручке."""
    if not send.get('path'):
        return _fail('у сайта не задан адрес отправки (presence.send.path)')

    cookies = _cookies(profile, presence)
    if not cookies:
        return _fail('вход не выполнен')

    body = dating_site_render(str(send.get('body') or ''), to_uid, text, message_uid)

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

    # `offline` здесь всегда `False`: «нет в чате» — понятие живого сокета, а по
    # HTTP такого ответа не бывает. Ключ всё равно на месте — форма ответа у всех
    # путей отправки одна, и читателю не надо помнить, каким она пришла.
    return {'ok': ok, 'offline': False, 'response': answer, 'message_uid': message_uid,
            'error': '' if ok else f'сайт ответил {response.status_code}: {answer[:200]}'}


async def _open_dialog(socket, to_uid: str, site) -> None:
    """Повторяет то, что страница делает перед отправкой.

    Снято с живых страниц: сайт может принять кадр сообщения и не доставить его
    никому, если перед этим не открыть диалог. Какими кадрами это делается и куда
    в каждом кладётся номер собеседника — знает драйвер сайта.

    Пауза между кадрами здесь, а не у драйвера: она про то, как мы себя ведём —
    страница шлёт их не залпом, и сервер успевает обработать каждый.
    """
    for frame in site.prelude_frames(to_uid):
        try:
            await socket.send(json.dumps(frame))
            await asyncio.sleep(DATING_SEND_BEFORE_PAUSE)
        except Exception as e:
            logger_info(f"[dating] подготовка «{frame.get('method')}» не ушла: {e}")
            return


async def _hello_wait(socket, site) -> bool:
    """Ждёт приветствие сервера — до него отправлять бесполезно.

    Сервер здоровается и только после этого начинает слушать клиента; ровно так
    же ведёт себя страница сайта. Чем именно он здоровается, знает драйвер.
    """
    deadline = time.monotonic() + DATING_SEND_HELLO_SEC

    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=deadline - time.monotonic())
        except Exception:
            return False

        if site.is_hello(raw):
            return True

    return False


async def _ack_wait(socket, site, message_uid: str) -> str:
    """Ждёт ответный кадр про наше сообщение. Молчание — не ошибка.

    Кадры чата идут потоком: входящие сообщения, статусы камер, пинги. Наш —
    тот, где встретился номер отправленного сообщения либо имя метода ответа,
    если сайт его называет. Всё остальное пропускаем, а не считаем ответом.
    """
    method = site.ack_method()
    mark = site.ack_ok_mark()
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


async def _message_save(profile_id: int, to_uid: str, text: str, message_uid: str) -> None:
    """Кладёт отправленное в переписку — иначе в диалоге видна только их половина."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT IGNORE INTO dating_message '
                '(profile_id, sender_uid, sender_name, text, message_uid, direction, is_answered) '
                "VALUES (%s, %s, %s, %s, %s, 'out', 1)",
                (profile_id, str(to_uid)[:32], '', str(text)[:2000], str(message_uid)[:64]))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] отправленное не записалось: {e}')


async def _answered_mark(profile_id: int, to_uid: str) -> None:
    """Помечает входящие этого собеседника отвеченными: мы только что ответили."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE dating_message SET is_answered = 1 '
                " WHERE profile_id = %s AND sender_uid = %s AND direction = 'in' AND is_answered = 0",
                (profile_id, str(to_uid)[:32]))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] пометка «отвечено» не проставилась: {e}')


def _fail(error: str) -> dict:
    return {'ok': False, 'error': error, 'offline': False,
            'response': '', 'message_uid': ''}
