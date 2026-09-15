"""Живой чат: держим вебсокет учётки и складываем входящие сообщения.

Сообщения от собеседниц приходят кадрами сокета — отправитель, текст и
идентификатор для подтверждения. HTTP-опросом их не достать: сайт шлёт их сокетом
и только сокетом.

**Сокет держит этот модуль, а не браузер.** Адрес сокета собирает JS страницы, и в
разметке его нет, поэтому снимается он браузером один раз (`browser_ws`) и живёт
в `dating_profile.ws_url`. Дальше подключение стоит десятки килобайт вместо ~143 МБ
на вкладку — двести учёток укладываются в единицы мегабайт.

Каждое сообщение подтверждается обратным кадром: без него сайт считает его
недоставленным и шлёт заново при следующем подключении. Повторы всё равно ловятся
уникальным ключом по `message_uid` — подтверждение может не дойти, и дважды
показать одно письмо человеку хуже, чем промолчать.

**Как выглядят кадры — знает драйвер сайта** (`src/dating/site/`), а не этот
модуль. Здесь остаётся наша механика: цикл слушания, срок тишины, запись в базу,
журнал. Имена методов и форма кадра у каждого сайта свои, и держать их здесь
значило бы ветвить весь файл на втором же сайте.
"""

import asyncio
import json

import websockets

from http_.http_proxy import http_proxy_ws_url
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_feed import dating_feed_write
from src.dating.dating_log import dating_log_message_in
from src.dating.dating_proxy import dating_proxy_url
from src.dating.site.site_registry import site_registry_chat

# Сколько собеседников перечисляем в запросе истории. Сервер принимает список, но
# у анкеты с сотней диалогов строка вырастет до килобайта — берём свежие.
DATING_CHAT_ASK_CONTACTS_MAX = 50

# Сколько ждать первого кадра, прежде чем считать подключение неудачным.
DATING_CHAT_HELLO_TIMEOUT = 20.0

# Сколько терпим полную тишину. Сайт сам шлёт `ping` каждые 15-20 секунд, поэтому
# полторы минуты без единого кадра означают мёртвое соединение, а не затишье в чате.
#
# Это не придирка: живое наблюдение — соединение проработало час, приняв 711
# сообщений, потом сайт перестал слать что-либо, а разрыва не случилось. Молчание
# длилось восемь часов, и учётка всё это время выглядела работающей.
DATING_CHAT_IDLE_MAX = 90.0


async def dating_chat_listen(profile: dict, seconds: float = 0,
                             presence: dict | None = None) -> dict:
    """Подключается к чат-сокету учётки и складывает входящие сообщения.

    Args:
        profile: строка `dating_profile` с непустым `ws_url`.
        seconds: сколько слушать; 0 — до обрыва соединения.
        presence: настройка сайта — по ней выбирается драйвер протокола. Пусто —
            берётся драйвер по умолчанию (см. `site_registry.py`).

    Returns:
        dict: `saved` — сколько сообщений записано, `frames` — сколько кадров
        пришло всего, `error` — причина обрыва, если он был.
    """
    url = str(profile.get('ws_url') or '').strip()
    if not url:
        return {'saved': 0, 'frames': 0, 'error': 'адрес сокета не снят: нужен вход'}

    site = site_registry_chat(presence)
    saved = frames = 0
    deadline = asyncio.get_event_loop().time() + seconds if seconds else None

    try:
        # ⚠ **Сокет идёт тем же выходом, что и вход.** Сайт считает учётки по
        # внешнему адресу, и сокет с другого IP для него — вторая учётка на ту же
        # анкету. `proxy=None` (выхода нет) значит прямой выход: разбираться, можно
        # ли так, — дело вызывающего, здесь мы лишь не подменяем адрес молча.
        async with websockets.connect(url, open_timeout=DATING_CHAT_HELLO_TIMEOUT,
                                      subprotocols=[site.subprotocol()],
                                      proxy=http_proxy_ws_url(await dating_proxy_url(profile)) or None,
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
                if site.is_hello(raw):
                    await _hello_ask(socket, int(profile['id']), site)
                    continue

                # Накопившееся за время без соединения: сервер отдаёт его пачкой
                # в ответ на запрос истории, а не отдельными сообщениями.
                if site.is_pending(raw):
                    saved += await _pending_take(socket, int(profile['id']), raw, site)
                    continue

                message = site.incoming_parse(raw, str(profile.get('login') or ''))
                if not message:
                    continue

                if await _message_save(int(profile['id']), message):
                    saved += 1
                    # Строка журнала пишется здесь, а не у вызывающего: сообщение
                    # ловит и проверка, и держатель сокетов, а видеть его в ленте
                    # человек должен в обоих случаях.
                    await dating_log_message_in(int(profile['id']), message['sender_uid'],
                                                message['sender_name'], message['text'])
                # Подтверждаем доставку: без этого сайт пришлёт то же самое снова.
                await _confirm(socket, message, site)
    except Exception as e:
        return {'saved': saved, 'frames': frames, 'error': f'{type(e).__name__}: {e}'}

    return {'saved': saved, 'frames': frames, 'error': ''}


async def _hello_ask(socket, profile_id: int, site) -> None:
    """Спрашивает накопившееся — теми же кадрами, что шлёт страница чата.

    Какими именно, знает драйвер сайта. Не роняет слушание: живые сообщения
    придут и без ответа на эти запросы, а оборвать чтение из-за неудачного
    вопроса значило бы потерять и их.
    """
    contacts = await _contacts_of(profile_id)

    for frame in site.ask_frames(contacts):
        try:
            await socket.send(json.dumps(frame))
        except Exception as e:
            logger_info(f"[dating] запрос «{frame.get('method')}» не ушёл: {e}")
            return


async def _contacts_of(profile_id: int) -> str:
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
                             ' WHERE profile_id = %s ORDER BY sender_uid DESC LIMIT %s',
                             (int(profile_id), DATING_CHAT_ASK_CONTACTS_MAX))
            uids += [str(row['uid']) for row in (await db.fetchall() or [])]

            await db.execute('SELECT uid FROM dating_contact WHERE profile_id = %s '
                             ' ORDER BY is_online DESC, id DESC LIMIT %s',
                             (int(profile_id), DATING_CHAT_ASK_CONTACTS_MAX))
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


async def _pending_take(socket, profile_id: int, raw, site) -> int:
    """Складывает накопившееся, разобранное драйвером сайта.

    Пачка приходит целиком, и негодная запись в ней пропускается, а не утаскивает
    за собой остальные: отбор делает драйвер, здесь остаётся запись и
    подтверждение.
    """
    saved = 0
    for message in site.pending_parse(raw):
        if await _message_save(profile_id, message):
            saved += 1
            await dating_log_message_in(profile_id, message['sender_uid'], '', message['text'])
        await _confirm(socket, message, site)

    return saved


async def _message_save(profile_id: int, message: dict) -> bool:
    """Кладёт сообщение. Повтор (тот же `message_uid`) молча пропускается.

    ⚠ Здесь же рождается событие «новый диалог»: первое слово от человека —
    единственное место, где его видно. Спрашиваем **до** записи, иначе своя же
    строка и окажется тем «прежним разговором», которого мы ищем.
    """
    try:
        async with mysql_get_db_async() as db:
            # Существование, а не счёт: у разговора бывают сотни реплик, а вопрос
            # тут двоичный. Индекс тот же — `idx_dating_message_dialog`.
            await db.execute(
                'SELECT 1 FROM dating_message'
                " WHERE profile_id = %s AND sender_uid = %s AND direction = 'in'"
                ' LIMIT 1', (profile_id, message['sender_uid']))
            first = not await db.fetchone()

            await db.execute(
                'INSERT IGNORE INTO dating_message '
                '(profile_id, sender_uid, sender_name, text, message_uid) VALUES (%s, %s, %s, %s, %s)',
                (profile_id, message['sender_uid'], message['sender_name'],
                 message['text'], message['message_uid']))
            added = bool(db.rowcount)
            await db.connection.commit()

        if added and first:
            await _talk_new(profile_id, message)

        return added
    except Exception as e:
        logger_info(f'[dating] сообщение не записалось: {e}')
        return False


async def _talk_new(profile_id: int, message: dict) -> None:
    """Отмечает в ленте начало разговора: человек ответил впервые.

    ⚠ **Только живой сокет.** Ту же первую реплику привозит импорт переписки из
    панели агентства (`dating_chat_panel.py`) — но там их сотни разом, за месяц
    назад, и лента заполнилась бы «новыми диалогами» годичной давности. Событие
    здесь про **сейчас**.

    Сбой ленты молчит: она подсказка, а не работа. Не записалось событие — реплика
    всё равно легла, и разговор виден на своей вкладке.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id FROM dating_profile WHERE id = %s',
                             (profile_id,))
            row = await db.fetchone()

        site_id = int((row or {}).get('site_id') or 0)
        if not site_id:
            return

        who = str(message.get('sender_name') or '').strip() \
            or str(message.get('sender_uid') or '')
        dating_feed_write(site_id, 'talk', f'Новый диалог: {who}', profile_id)
    except Exception as e:
        logger_info(f'[dating] начало разговора не отметилось: {e}')


async def _confirm(socket, message: dict, site) -> None:
    """Подтверждает доставку. Не вышло — не беда: повтор поймает уникальный ключ.

    Чем именно подтверждать, знает драйвер сайта; `None` от него означает, что
    подтверждать нечем — у сообщения не было номера.
    """
    frame = site.confirm_frame(message)
    if not frame:
        return

    try:
        await socket.send(json.dumps(frame))
    except Exception as e:
        logger_info(f'[dating] подтверждение не ушло: {e}')
