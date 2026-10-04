"""Отправка сообщения в чат — тем же путём, каким его шлёт страница сайта.

Отдельным модулем, а не внутри проверки: отправку зовут и из проверки, и из
цепочек ответов, и правило у неё одно — писать по настройке сайта
(`browser_site.presence.send`), а не по зашитому в код адресу.

**Транспорт задаётся настройкой.** У одних сайтов чат живёт вебсокетом: страница
шлёт кадр в то же соединение, откуда читает входящие, и HTTP-ручки отправки у
сайта нет вовсе. Другие шлют обычным POST. Третьи не дают ни того ни другого — там
отправляет **сценарий у соседа**, живым браузером, как это сделал бы человек.
Отсюда три пути: `transport: "ws"`, `"http"` и `"scenario"`.

⚠ **Сценарий — самый дорогой из трёх и выбирается не от хорошей жизни.** Он стоит
десятков секунд и занимает вкладку в общем пуле соседа, тогда как кадр в сокет
уходит за миллисекунды. Но у Ladadate отправка живёт внутри страницы
(`onclick="SendMessage()"`), своей ручки нет, а сокет чата поднимается скриптом —
и повторить это HTTP-запросом не выходит. Браузер там единственный способ.

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

from http_.http_pool import http_pool_transport
from http_.http_proxy import http_proxy_ws_url
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.online import online_check
from src.dating.ready import password_pick
from src.dating.scenario import scenario_run
from src.dating.site_setting import site_setting
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_site_variable)

from src.dating.presence import DATING_PRESENCE_TIMEOUT, DATING_PRESENCE_UA
from src.dating.proxy import proxy_url
from src.dating.site.site_base import site_base_encode, site_base_render
from src.dating.site.site_registry import site_registry_chat

# Сколько ждать ответного кадра после отправки. Сайт отвечает за доли секунды,
# а молчание — обычное дело: подтверждения шлют не все.
SEND_ACK_SEC = 6.0

# Сколько ждать приветствия. Оно приходит за доли секунды; дольше ждать значит,
# что соединение не поднялось.
SEND_HELLO_SEC = 15.0

# Пауза между кадрами прелюдии: страница шлёт их не залпом, и сервер успевает
# обработать каждый.
SEND_BEFORE_PAUSE = 0.4


# Имя сценария отправки у соседа. По имени, а не по номеру: номера у разных
# установок свои, а имя — то же правило, что у прочих сценариев.
SEND_SCENARIO = 'чат: отправить сообщение'

# Переменные сайта у соседа, из которых собирается прогон.
#
# ⚠ Они **у него**, а не у нас, и это осознанно: это свойства самого сайта (как
# выглядит его чат, где страница входа), а не нашей работы с ним. Своя копия
# разошлась бы с оригиналом в день, когда сайт переверстает адреса.
SEND_CHAT_URL_VAR = 'chat_companion_url_pattern'
SEND_LOGIN_URL_VAR = 'login_url'

# Метка номера собеседника в шаблоне адреса: `https://site/chat#{ID}`.
SEND_UID_MARK = '{ID}'

# Настройка сайта с общим паролем анкет — та же, что у списков собеседников.
SEND_PASSWORD_SETTING = 'api_password'

# Наша настройка сайта, которой выбирают транспорт. Ключ — в `SITE_KNOWN`.
SEND_TRANSPORT_SETTING = 'send_transport'

# Транспорт → что записать в `message.source`.
#
# ⚠ **Не украшение, а первый вопрос разбора**: сообщение не дошло — смотрят, чем
# его отправляли. Сокет молчит иначе, чем отказывает ручка, а сценарий падает на
# шаге вёрстки: три разных места, куда идти смотреть.
#
# Раньше здесь стояло `'scenario' if transport == 'scenario' else 'socket'`, и это
# было верно, пока HTTP-путь никем не был настроен. С переводом LadaDate на
# `/chat-send` каждое исходящее стало писаться как `socket` — у сайта, у которого
# сокета нет вовсе, то есть ровно та путаница, от которой столбец и заводился.
SEND_SOURCE = {'scenario': 'scenario', 'http': 'http', 'ws': 'socket'}


async def send_message(profile: dict, presence: dict, to_uid: str, text: str,
                              to_history: bool = True) -> dict:
    """Шлёт сообщение собеседнику и записывает его в переписку.

    Args:
        profile: строка `profile`; для вебсокета нужен непустой `ws_url`.
        presence: настройка сайта, раздел `send`.
        to_uid: кому — идентификатор на сайте.
        text: что отправляем.
        to_history: писать ли ушедшее в переписку. ⚠ `False` у инвайтов:
            приглашение не разговор, и до первого ответа человека его в
            диалоге быть не должно.

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
    # ⚠ **Выключенная анкета не пишет никому и ничем.**
    #
    # Проверка стоит первой — раньше транспорта, раньше онлайна, раньше всего: она
    # самая дешёвая и самая безусловная. «Анкету не ведём» значит не ведём совсем, а
    # не «ведём, но осторожно».
    #
    # Запрет здесь один на все пути сразу — автоответ, круг воркера, кнопка в окне
    # переписки, — потому что все они идут через эту функцию. Расставь его по
    # вызывающим, и первый забытый писал бы от выключенной анкеты молча.
    #
    # ⚠ **Входящие при этом принимаются.** Держатель сокета выключенную анкету не
    # бросает: переписка копится, и включив её обратно, человек видит, что писали
    # без него. Молчание наружу — не то же, что слепота.
    if not int(profile.get('enabled', 1) or 0):
        return {'ok': False, 'error': 'анкета выключена', 'offline': False,
                'response': '', 'message_uid': ''}

    # ⚠ **Транспорт спрашиваем у себя, а `presence` — у соседа, и порядок такой.**
    #
    # `presence.send` описывает сайт: какие у него ручки, какие кадры. Но правит
    # его только сосед (ручки для этого у него нет вовсе — правят прямо в базе), а
    # «чем отправлять» — решение **нашей** стороны: у одного и того же сайта путь
    # может смениться, когда он переверстает чат.
    #
    # Поэтому наша настройка (`send_transport`) старше: сказали «сценарием» —
    # шлём сценарием, даже если `presence.send` пуст. Умолчание — `presence`,
    # то есть всё, что настроено у соседа, работает как работало.
    site_id = int(profile.get('site_id') or 0)
    chosen = str(await site_setting(site_id, SEND_TRANSPORT_SETTING)).strip() \
        if site_id else ''

    send = presence.get('send') or {}
    transport = chosen if chosen and chosen != 'presence' \
        else str(send.get('transport') or '').lower()

    if not transport:
        return _fail('у сайта не настроена отправка: ни `presence.send` у соседа, '
                     'ни «Чем отправляем сообщения» на странице «Сайты»')

    # ⚠ **Тот же текст тому же собеседнику подряд не отправляется.**
    #
    # Смотрим последнюю реплику диалога: если она наша и с этим же текстом, значит
    # мы это уже сказали, и собеседник ещё не ответил. Повтор ему ничего не
    # добавит, а в диалоге выглядит поломкой — так и вышло: человек увидел десять
    # «привет» подряд и решил, что автоответ сломался.
    #
    # Сравнение по **последней** реплике, а не по всей переписке: «привет» через
    # неделю разговора — законное сообщение, а «привет» сразу после «привет» —
    # нет. Историю целиком проверять и не надо, и нельзя: люди повторяются.
    #
    # Стоит после запрета выключенной анкеты (тот дешевле — не ходит в базу) и
    # **до** проверки онлайна: незачем спрашивать снимок ради сообщения, которое
    # мы всё равно не отправим.
    if await _said_already(int(profile['id']), to_uid, text):
        return {'ok': False, 'error': 'это сообщение уже отправлено последним',
                'offline': False, 'response': '', 'message_uid': ''}

    # ⚠ **Собеседника нет в сети — не шлём вовсе.**
    #
    # У сценария это особенно дорого: прогон стоит десятков секунд живого браузера
    # и вкладки в общем пуле соседа — платить их за сообщение, которое уйдёт в
    # пустоту, незачем. У прочих путей дешевле, но исход тот же.
    #
    # `offline: True` — **состояние, а не ошибка**: собеседник вернётся, и тогда
    # сообщение уйдёт. Вызывающий поступает с этим по-своему: автоответ оставляет
    # сообщение неотвеченным, страница гасит поле ввода.
    away = online_check(site_id, to_uid)
    if not away['online']:
        return {'ok': False, 'error': 'собеседника нет в сети', 'offline': True,
                'response': '', 'message_uid': ''}

    site = site_registry_chat(presence)
    message_uid = site.message_uid()

    # ⚠⚠ **Засекаем время до развилки, а строку журнала пишем после.**
    #
    # Здесь, а не внутри каждого пути: иначе занятие «отправка» оставалось бы
    # без журнала ровно там, где его забыли дописать. Так и было — до этой
    # правки в журнал попадала только отправка сокетом, а ручкой
    # и сценарием не попадала вовсе. Человек смотрел вкладку «Запросы», видел
    # присутствие, письма и списки — и ни одной реплики чата.
    started = time.monotonic()

    if transport == 'scenario':
        result = await _send_scenario(profile, to_uid, text, message_uid)
    elif transport == 'ws':
        result = await _send_ws(profile, site, to_uid, text, message_uid)
    else:
        result = await _send_http(profile, presence, send, to_uid, text, message_uid)

    await _note_send(profile, presence, send, transport, to_uid, text, result, started)

    # Записываем только принятое: строка в переписке означает «это мы сказали», и
    # непринятый сайтом текст показывать в диалоге нельзя.
    if result['ok']:
        # ⚠⚠ **Инвайт в переписку не идёт** (`to_history=False`), и это не мелочь.
        # Приглашение — не разговор: пока человек не ответил, разговора нет
        # вовсе, а приглашений ему уходит до двадцати за разные дни. Записанные
        # сразу, они давали диалог из двадцати наших реплик и ни одной его — и
        # портили ровно то, ради чего их хранят: понять, **на что** он ответил.
        #
        # Историю держит своя таблица (`invite`), а в переписку последние из
        # приглашений переносятся в момент **первого ответа** (`chat.py`).
        if to_history:
            await _message_save(int(profile['id']), to_uid, text, message_uid,
                                SEND_SOURCE.get(transport, 'socket'))

        await _answered_mark(int(profile['id']), to_uid)

    return result


async def _send_scenario(profile: dict, to_uid: str, text: str, message_uid: str) -> dict:
    """Отправка сценарием у соседа: живой браузер входит под анкетой и пишет в чат.

    Дороже прочих путей — десятки секунд и вкладка в общем пуле, — но там, где
    отправка живёт внутри страницы, другого способа нет.

    ⚠ **Адрес чата собирается здесь, а не в сценарии.** Шаблон лежит переменной
    сайта у соседа (`chat_companion_url_pattern`, например
    `https://ladadate.com/chat#{ID}`), и подставить в него номер сценарий не может:
    подстановки у соседа только двойными скобками, одинарные `{ID}` он не трогает.

    Пароль уходит **секретом**: в журнал прогонов он не попадёт, там останется
    `{{girl_password}}`. Логин анкеты — обычной подстановкой: он и так виден в
    панели.
    """
    site_id = int(profile.get('site_id') or 0)
    if not site_id:
        return _fail('у анкеты нет сайта')

    pattern = await rhapsody_api_site_variable(site_id, SEND_CHAT_URL_VAR)
    if not pattern or SEND_UID_MARK not in pattern:
        return _fail(f'у сайта нет переменной «{SEND_CHAT_URL_VAR}» '
                     f'с меткой {SEND_UID_MARK}')

    login_url = await rhapsody_api_site_variable(site_id, SEND_LOGIN_URL_VAR)
    if not login_url:
        return _fail(f'у сайта нет переменной «{SEND_LOGIN_URL_VAR}»')

    # Откуда пароль — решает сама анкета (`password_source`). ⚠ Правило одно на
    # всех путях: `password_pick`. Две копии выбора разошлись бы в день,
    # когда правило поменяют, и половина системы ходила бы под другим паролем.
    password = password_pick(
        profile, str(await site_setting(site_id, SEND_PASSWORD_SETTING)))
    if not password:
        return _fail('нечем войти на сайт: пароль не задан — проверьте блок «Пароль» '
                     'у анкеты')

    try:
        scenario = await rhapsody_api_scenario_find(site_id, SEND_SCENARIO)
    except RhapsodyError as e:
        return _fail(str(e))

    if not scenario:
        return _fail(f'у сайта нет сценария «{SEND_SCENARIO}» — '
                     f'заведите его в RhapsodyAi на вкладке «Сценарии»')

    try:
        run = await scenario_run(
            scenario, site_id, int(profile['id']), '',
            {'login_url': login_url,
             'profile_login': str(profile.get('login') or ''),
             'chat_url': pattern.replace(SEND_UID_MARK, str(to_uid)),
             'message': text},
            {'girl_password': password},
            keep_session=False,
            proxy_id=int(profile.get('proxy_id') or 0))
    except RhapsodyError as e:
        return _fail(str(e))

    if str(run.get('status')) == 'error':
        # ⚠ Сценарий падает и тогда, когда сообщение **не появилось в ленте** —
        # сайт принимает нажатие, но реплики нет. Считаем это неудачей: показать
        # в панели то, чего у собеседника нет, хуже, чем сказать «не ушло».
        return _fail(f"сценарий встал на шаге {run.get('step_no')}: {run.get('error')}")

    return {'ok': True, 'error': '', 'offline': False,
            'response': str((run.get('extract') or {}).get('отправлено') or ''),
            'message_uid': message_uid}


async def _send_ws(profile: dict, site, to_uid: str, text: str, message_uid: str) -> dict:
    """Кадр в чат-сокет — так шлёт сама страница сайта.

    Порядок разговора здесь, форма кадров — у драйвера сайта (`site`).

    ⚠ **В журнал запросов пишется весь разговор целиком, одной строкой.** Сокет —
    не запрос: открылся, поздоровался, отдал кадр, дождался подтверждения. Строка
    на каждый кадр отвечала бы на вопрос, которого никто не задаёт, а строка на
    отправку отвечает на тот, который задают всегда: ушло ли сообщение и за
    сколько.
    """
    # ⚠⚠ **Наружу не пошли — строки в журнале нет.** Адрес сокета не снят: до
    # сайта мы не дотянулись ни разу, и запись об этом означала бы, что журнал
    # запросов врёт о походе, которого не было. Такую беду показывает журнал
    # анкеты — она про неё, а не про сайт.
    url = str(profile.get('ws_url') or '').strip()
    if not url:
        return _fail('адрес чат-сокета не снят: сначала «Залогиниться»')

    started = time.monotonic()
    got = await _send_ws_run(profile, site, to_uid, text, message_uid)

    return got


async def _send_ws_run(profile: dict, site, to_uid: str, text: str,
                       message_uid: str) -> dict:
    """Сам разговор по сокету. Вынесен, чтобы исход писался в журнал одним местом.

    Адрес сокета проверен вызывающим: без него сюда не заходят вовсе.
    """
    url = str(profile.get('ws_url') or '').strip()
    frame = site.send_frame(to_uid, text, message_uid)

    try:
        # ⚠ **Сокет идёт тем же выходом, что и вход.** Сайт считает учётки по
        # внешнему адресу, и сокет с другого IP для него — вторая учётка на ту же
        # анкету. `proxy=None` (выхода нет) значит прямой выход: разбираться, можно
        # ли так, — дело вызывающего, здесь мы лишь не подменяем адрес молча.
        async with websockets.connect(url, open_timeout=DATING_PRESENCE_TIMEOUT,
                                      proxy=http_proxy_ws_url(await proxy_url(profile)) or None,
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

    # ⚠ **Текст кодируем до подстановки.** `site_base_render` — простая замена
    # метки: пробел, `&` или `=` в сообщении порвали бы тело `id={{to}}&body={{text}}`,
    # и оно ушло бы обрезанным по первому пробелу. Как кодировать, знает сайт
    # (`presence.send.encode`) — у LadaDate это двойное кодирование, потому что
    # страница шлёт `escape(mes)` внутри form-urlencode (`site_base_encode`).
    body = site_base_render(str(send.get('body') or ''), to_uid,
                            site_base_encode(text, str(send.get('encode') or '')),
                            message_uid)

    try:
        # Клиент разовый — куки на анкету, — а соединение из общего транспорта
        # (`http_pool.py`): отправка идёт по каждой анкете, и заводить TLS
        # на каждое письмо незачем. Не закрываем: ушёл бы и общий пул.
        client = httpx.AsyncClient(timeout=DATING_PRESENCE_TIMEOUT, cookies=cookies,
                                   follow_redirects=True,
                                   headers={'User-Agent': DATING_PRESENCE_UA,
                                            'Content-Type': str(send.get('content_type')
                                                                or 'application/x-www-form-urlencoded'),
                                            **(presence.get('headers') or {})},
                                   # Выход анкеты, а не общий: отправка идёт под
                                   # той же учёткой, что и вход (`proxy.py`).
                                   transport=http_pool_transport(await proxy_url(profile)))
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
    # `status` — для журнала запросов: по коду отличают «сайт лёг» от «сайт
    # ответил отказом», и без него во вкладке «Запросы» стоял бы ноль.
    return {'ok': ok, 'offline': False, 'response': answer, 'message_uid': message_uid,
            'status': response.status_code,
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
            await asyncio.sleep(SEND_BEFORE_PAUSE)
        except Exception as e:
            logger_info(f"[dating] подготовка «{frame.get('method')}» не ушла: {e}")
            return


async def _hello_wait(socket, site) -> bool:
    """Ждёт приветствие сервера — до него отправлять бесполезно.

    Сервер здоровается и только после этого начинает слушать клиента; ровно так
    же ведёт себя страница сайта. Чем именно он здоровается, знает драйвер.
    """
    deadline = time.monotonic() + SEND_HELLO_SEC

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
    deadline = time.monotonic() + SEND_ACK_SEC
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


async def _said_already(profile_id: int, to_uid: str, text: str) -> bool:
    """Последняя реплика диалога — наша и с этим же текстом?

    Returns:
        bool: `True` — отправлять не надо. Сбой базы даёт `False`: молча не
        отправить хуже, чем отправить второй раз, — потерянный ответ человек не
        увидит вовсе, а дубль хотя бы заметен.

    ⚠ **Порядок по времени, а не по `id`.** Импорт переписки из панели агентства
    кладёт историю за неделю сегодня, то есть с наибольшими `id`: по `id`
    «последней» оказалась бы недельной давности реплика (`dating.md`, §5.8). Тот же
    разбор, что у списка диалогов.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT text, direction FROM message '
                             ' WHERE profile_id = %s AND sender_uid = %s '
                             ' ORDER BY created_at DESC, id DESC LIMIT 1',
                             (profile_id, str(to_uid)[:32]))
            last = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] последняя реплика не прочиталась: {e}')

        return False

    if not last or str(last.get('direction')) != 'out':
        return False

    # Сравниваем по сути, а не побуквенно: агент может вернуть тот же текст с
    # другим пробелом или регистром, и для собеседника это одно и то же сообщение.
    return str(last.get('text') or '').strip().casefold() == str(text or '').strip().casefold()


async def _message_save(profile_id: int, to_uid: str, text: str, message_uid: str,
                        source: str = 'socket') -> None:
    """Кладёт отправленное в переписку — иначе в диалоге видна только их половина.

    `source` — каким путём ушло (`SEND_SOURCE`). ⚠ Не украшение: у Ladadate
    сокета нет вовсе, и запись со `socket` заставила бы искать поломку там, где её
    нет. Это уже случалось дважды — сперва со сценарием, потом с HTTP-ручкой, — и
    оба раза столбец приходилось учить новому значению, а не подгонять под старое.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT IGNORE INTO message '
                '(profile_id, sender_uid, sender_name, text, message_uid, direction,'
                ' is_answered, source) '
                "VALUES (%s, %s, %s, %s, %s, 'out', 1, %s)",
                (profile_id, str(to_uid)[:32], '', str(text)[:2000], str(message_uid)[:64],
                 str(source)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] отправленное не записалось: {e}')


async def _answered_mark(profile_id: int, to_uid: str) -> None:
    """Помечает входящие этого собеседника отвеченными: мы только что ответили."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE message SET is_answered = 1 '
                " WHERE profile_id = %s AND sender_uid = %s AND direction = 'in' AND is_answered = 0",
                (profile_id, str(to_uid)[:32]))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] пометка «отвечено» не проставилась: {e}')


def _fail(error: str) -> dict:
    return {'ok': False, 'error': error, 'offline': False,
            'response': '', 'message_uid': ''}


async def _note_send(profile: dict, presence: dict, send: dict, transport: str,
                     to_uid: str, text: str, got: dict, started: float) -> None:
    """Строка журнала об отправке реплики — любым путём.

    ⚠ **Одна строка на одну отправку, включая неудачную.** Журнал отвечает на
    вопрос «сколько раз мы постучались к сайту», и отказ — такой же стук, как
    удача. Неотправленное сообщение в переписку не ложится (`result['ok']`), и
    без этой строки его след пропадал бы совсем.

    ⚠ **Текста реплики здесь нет, только длина.** Журнал читают в консоли и
    копируют в переписку; сам разговор живёт в `message`, и второй его копии в
    таблице на 52 тысячи строк в сутки не нужно. Длина же отвечает на живой
    вопрос «не обрезал ли сайт сообщение».

    ⚠ `ok` берётся из разбора ответа, **а не из кода HTTP**: у LadaDate признак
    удачи это поле `IsSuccess` в теле, и `200` с отказом внутри — обычное дело.
    """
    from src.dating.request import (REQUEST_SEND, request_ms, request_write)

    if transport == 'ws':
        # Адрес сокета без параметров: в строке у некоторых сайтов едет токен
        # сессии, а журнал читают глазами и копируют в переписку.
        where = str(profile.get('ws_url') or '').split('?')[0]
        method = 'WS'
    elif transport == 'scenario':
        where = 'сценарий у соседа'
        method = 'RUN'
    else:
        where = str(send.get('path') or '')
        method = 'POST'

    await request_write(
        door=REQUEST_SEND, profile_id=int(profile.get('id') or 0),
        site_id=int(profile.get('site_id') or 0), method=method,
        path=f'{where} → {to_uid} ({len(str(text or ""))} зн.)',
        status=int(got.get('status') or 0), ok=bool(got.get('ok')),
        error=str(got.get('error') or ''), ms=request_ms(started),
        proxy_ip=str(profile.get('proxy_ip') or ''),
        body=str(got.get('response') or ''))


