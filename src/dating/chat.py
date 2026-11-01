"""Живой чат: держим вебсокет учётки и складываем входящие сообщения.

Сообщения от собеседниц приходят кадрами сокета — отправитель, текст и
идентификатор для подтверждения. HTTP-опросом их не достать: сайт шлёт их сокетом
и только сокетом.

**Сокет держит этот модуль, а не браузер.** Адрес сокета собирает JS страницы, и в
разметке его нет, поэтому снимается он браузером один раз (`browser_ws`) и живёт
в `profile_.ws_url`. Дальше подключение стоит десятки килобайт вместо ~143 МБ
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

from src.dating.feed import feed_write
from src.dating.log import log_message_in
from src.dating.proxy import proxy_url
from src.dating.site.site_registry import site_registry_chat
from src.dating.site_setting import site_seconds

# Сколько собеседников перечисляем в запросе истории. Сервер принимает список, но
# у анкеты с сотней диалогов строка вырастет до килобайта — берём свежие.
CHAT_ASK_CONTACTS_MAX = 50

# Сколько ждать первого кадра, прежде чем считать подключение неудачным.
CHAT_HELLO_TIMEOUT = 20.0

# Сколько терпим полную тишину. Сайт сам шлёт `ping` каждые 15-20 секунд, поэтому
# полторы минуты без единого кадра означают мёртвое соединение, а не затишье в чате.
#
# Это не придирка: живое наблюдение — соединение проработало час, приняв 711
# сообщений, потом сайт перестал слать что-либо, а разрыва не случилось. Молчание
# длилось восемь часов, и учётка всё это время выглядела работающей.
CHAT_IDLE_MAX = 90.0


async def chat_listen(profile: dict, seconds: float = 0,
                             presence: dict | None = None) -> dict:
    """Подключается к чат-сокету учётки и складывает входящие сообщения.

    Args:
        profile: строка `profile` с непустым `ws_url`.
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
        async with websockets.connect(url, open_timeout=CHAT_HELLO_TIMEOUT,
                                      subprotocols=[site.subprotocol()],
                                      proxy=http_proxy_ws_url(await proxy_url(profile)) or None,
                                      ping_interval=20) as socket:
            while True:
                left = CHAT_IDLE_MAX
                if deadline:
                    left = deadline - asyncio.get_event_loop().time()
                    if left <= 0:
                        break
                    left = min(left, CHAT_IDLE_MAX)

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
                            'error': f'молчит {int(CHAT_IDLE_MAX)} с — соединение мертво'}

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
                    await log_message_in(int(profile['id']), message['sender_uid'],
                                                message['sender_name'], message['text'])
                # Подтверждаем доставку: без этого сайт пришлёт то же самое снова.
                await _confirm(socket, message, site)
    except Exception as e:
        return {'saved': saved, 'frames': frames, 'error': f'{type(e).__name__}: {e}'}

    return {'saved': saved, 'frames': frames, 'error': ''}


async def chat_invites_to_history(db, profile_id: int, uid: str) -> int:
    """Кладёт в переписку последние инвайты этой пары — их столько, сколько
    просит настройка сайта `invite_keep`.

    Args:
        db: открытая сделка вызывающего. ⚠ **Своей не заводим**: перенос и запись
            ответа обязаны случиться вместе, иначе сбой между ними оставил бы
            диалог без повода или повод без диалога.
        profile_id: девушка.
        uid: собеседник на сайте.

    Returns:
        int: сколько приглашений перенесено. Ноль — обычный случай: ответ пришёл
        человеку, которому мы не писали (он сам начал разговор).

    ## ⚠⚠ Время берём от инвайта, а не текущее

    `created_at` ставится равным `sent_at` приглашения. Поставь мы `NOW()` —
    приглашение оказалось бы **новее** ответа на него, и диалог читался бы задом
    наперёд: сначала «привет, как ты?», потом наше «спорим, ты улыбнулся».
    Разбор истории у нас идёт по времени, а не по `id` (`send._said_already`),
    так что это не косметика.

    ## ⚠ Берём только отправленные

    Черновик в пуле человеку не уходил — в переписке ему места нет. `status` у
    инвайта как раз это и различает: `sent` — ушло, `draft` — лежит, `failed` —
    не дошло.
    """
    from src.dating.invite import INVITE_KEEP_DEFAULT

    keep = await _invite_keep(profile_id)

    await db.execute(
        'SELECT uid, body, sent_at FROM invite'
        "  WHERE profile_id = %s AND uid = %s AND status = 'sent'"
        '    AND sent_at IS NOT NULL'
        '  ORDER BY sent_at DESC LIMIT %s',
        (int(profile_id), str(uid), max(1, int(keep or INVITE_KEEP_DEFAULT))))
    invites = await db.fetchall() or []

    for one in reversed(invites):
        # ⚠ `message_uid` собираем из номера строки инвайта: у приглашения нет
        # номера сообщения на сайте (его даёт сокет, а инвайт уходит ручкой), а
        # `INSERT IGNORE` без уникального ключа пустит одно и то же дважды.
        await db.execute(
            'INSERT IGNORE INTO message'
            ' (profile_id, sender_uid, sender_name, text, message_uid, direction,'
            "  is_answered, source, created_at)"
            " VALUES (%s, %s, '', %s, %s, 'out', 1, 'invite', %s)",
            (int(profile_id), str(uid), str(one['body'] or ''),
             f"invite-{profile_id}-{uid}-{one['sent_at']:%Y%m%d%H%M%S}",
             one['sent_at']))

    return len(invites)


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
            await db.execute('SELECT DISTINCT sender_uid AS uid FROM message '
                             ' WHERE profile_id = %s ORDER BY sender_uid DESC LIMIT %s',
                             (int(profile_id), CHAT_ASK_CONTACTS_MAX))
            uids += [str(row['uid']) for row in (await db.fetchall() or [])]

            await db.execute('SELECT uid FROM contact WHERE profile_id = %s '
                             ' ORDER BY is_online DESC, id DESC LIMIT %s',
                             (int(profile_id), CHAT_ASK_CONTACTS_MAX))
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

    return ''.join(f'{uid},' for uid in ordered[:CHAT_ASK_CONTACTS_MAX])


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
            await log_message_in(profile_id, message['sender_uid'], '', message['text'])
        await _confirm(socket, message, site)

    return saved


async def _invite_keep(profile_id: int) -> int:
    """Сколько приглашений переносить — настройка **сайта** этой анкеты.

    Сайт спрашиваем по анкете: у пары его в руках нет, а настройка живёт у сайта.
    Сбой чтения даёт умолчание — перенести два приглашения безопаснее, чем не
    перенести ни одного и оставить автоответ без повода.
    """
    from src.dating.invite import (INVITE_KEEP_DEFAULT,
                                          INVITE_KEEP_SETTING)
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id FROM profile WHERE id = %s',
                             (int(profile_id),))
            row = await db.fetchone()

        site_id = int((row or {}).get('site_id') or 0)
        if not site_id:
            return INVITE_KEEP_DEFAULT

        return int(await site_seconds(site_id, INVITE_KEEP_SETTING)
                   or INVITE_KEEP_DEFAULT)
    except Exception as e:
        logger_info(f'[dating] сколько инвайтов переносить не спросилось: {e}')

        return INVITE_KEEP_DEFAULT


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
                'SELECT 1 FROM message'
                " WHERE profile_id = %s AND sender_uid = %s AND direction = 'in'"
                ' LIMIT 1', (profile_id, message['sender_uid']))
            first = not await db.fetchone()

            # ⚠⚠ **Первый ответ — момент, когда приглашения становятся диалогом.**
            # Инвайты в переписку не пишутся: пока человек молчит, разговора нет,
            # а приглашений ему уходит до двадцати за разные дни
            # (`send.to_history`). Ответил — и последние из них нужны, чтобы
            # понять, **на что** он ответил: без них автоответ видит одну реплику
            # без повода.
            #
            # ⚠ Переносим **до** вставки его слов, в той же сделке: инвайт с
            # временем отправки обязан лечь раньше ответа, иначе диалог читается
            # задом наперёд.
            if first:
                await chat_invites_to_history(db, profile_id, message['sender_uid'])

            await db.execute(
                'INSERT IGNORE INTO message '
                '(profile_id, sender_uid, sender_name, text, message_uid) VALUES (%s, %s, %s, %s, %s)',
                (profile_id, message['sender_uid'], message['sender_name'],
                 message['text'], message['message_uid']))
            added = bool(db.rowcount)
            await db.connection.commit()

        if added:
            # ⚠⚠ Реплика едет к соседу СРАЗУ, а не когда агент решит ответить.
            # Прежде путь был один — прогон workflow из `answer.py`, — и он
            # проходим только при ответе: сказанное, на что мы смолчали, у
            # соседа не появлялось вовсе. Двенадцать таких за восемь дней,
            # замерено 02.10.2026.
            #
            # ⚠ После записи и только для живого сокета. Импорт из панели
            # (`chat_panel.py`) возит историю сотнями строк за месяц —
            # перенос накопленного делается миграцией, а не этим вызовом.
            await _history_push(profile_id, message)

        if added and first:
            await _talk_new(profile_id, message)

        return added
    except Exception as e:
        logger_info(f'[dating] сообщение не записалось: {e}')
        return False


async def _history_push(profile_id: int, message: dict) -> None:
    """Отдаёт входящую реплику в историю разговора у соседа.

    ⚠⚠ Своей бедой не роняет приём сообщения: оно к этому моменту уже лежит в
    `message`, и исключение отсюда отменило бы запись. Не ушло — строка в
    журнале, история у соседа на реплику короче.

    ⚠ Ключи собираются теми же функциями, что и для вызова агента
    (`agent_vars`, `companion_vars_known`): склейка `{номер}_{имя}` у соседа одна
    на все дороги, и второе правило разошлось бы с первым молча.
    """
    try:
        from src.dating.agent import agent_vars
        from src.dating.companion_card import companion_vars_known
        from src.rhapsody.rhapsody_api import rhapsody_api_chat_incoming

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s LIMIT 1',
                             (int(profile_id),))
            profile = await db.fetchone()

        if not profile:
            return

        card = profile.get('profile')

        if isinstance(card, str):
            try:
                card = json.loads(card or '{}')
            except ValueError:
                card = {}

        girl = agent_vars(profile, card or {})
        # ⚠⚠ Имя спрашиваем **у карточки**, а не берём из реплики: `sender_name`
        # приходит пустым почти всегда (99% входящих на проде), и ключ выходил
        # вырожденным — `663532_663532` рядом с живым `663532_youpikeke`. Довод
        # целиком — в докстринге `companion_vars_known`.
        companion = await companion_vars_known(profile.get('site_id'),
                                               message['sender_uid'],
                                               message.get('sender_name'))

        # ⚠ Проверяется только собеседник: у анкеты ключ есть всегда —
        # `agent_site_uid` падает до нашего внутреннего номера и пусто не
        # возвращает никогда (так же, как при вызове агента, и ключ у обеих
        # дорог обязан быть один). А вот номер собеседника бывает пуст, и
        # тогда класть реплику некуда.
        if not companion.get('companion_id'):
            return

        await rhapsody_api_chat_incoming(
            f"{girl['girl_id']}_{girl['girl_name']}",
            f"{companion['companion_id']}_{companion['companion_name']}",
            message['text'], kind='chat',
            source_uid=str(message.get('message_uid') or ''))
    except Exception as e:
        logger_info(f'[dating] входящее не уехало в историю: {e}')


async def _talk_new(profile_id: int, message: dict) -> None:
    """Отмечает в ленте начало разговора: человек ответил впервые.

    ⚠ **Только живой сокет.** Ту же первую реплику привозит импорт переписки из
    панели агентства (`chat_panel.py`) — но там их сотни разом, за месяц
    назад, и лента заполнилась бы «новыми диалогами» годичной давности. Событие
    здесь про **сейчас**.

    Сбой ленты молчит: она подсказка, а не работа. Не записалось событие — реплика
    всё равно легла, и разговор виден на своей вкладке.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id FROM profile WHERE id = %s',
                             (profile_id,))
            row = await db.fetchone()

        site_id = int((row or {}).get('site_id') or 0)
        if not site_id:
            return

        who = str(message.get('sender_name') or '').strip() \
            or str(message.get('sender_uid') or '')
        feed_write(site_id, 'talk', f'Новый диалог: {who}', profile_id)
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
