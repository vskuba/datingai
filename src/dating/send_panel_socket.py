"""Отправка реплики сокетом чата панели агентства — у сайта без кабинета девушки.

У Luxee под девушкой не входят вовсе: одна панель агентства на всех, и оператор
пишет из её страницы чата — socket.io, событие `cl_m_message`. Этим же путём
шлём и мы (транспорт `panel_socket`, настройка сайта «Чем отправляем
сообщения»): протокол — `site/site_socket_luxee.py`, общий с забором переписки.

## ⚠⚠ Писать можно только в существующий диалог

Диалог — это пара «мужчина + **внешняя** анкета девушки» (`2365098_3161888`),
а внешних у девушки по одной на проект агентства. Номер диалога ищем в её
списке: тот, где участвует этот мужчина, а при нескольких — самый свежий
(разговор идёт там, где он писал последним). Диалога нет — отказ словами:
заводить новый от имени девушки ответ не вправе, это холодное обращение.

## ⚠ «Принято» — это наше сообщение, вернувшееся от сервера

Страница считает реплику ушедшей, когда сервер присылает её обратно событием
`server_m_new` с нашим ключом. Отказ приходит `s_m_error` с кодом и словами —
частый у агентства «сначала Catch Up» (код 485): панель держит отправку, пока у
оператора висят задания. Не дождались ни того ни другого — **неудача**:
ложная удача закрыла бы черновик навсегда, а лишняя попытка упрётся в запрет
повтора (`send._said_already`) — привезённая панелью реплика встанет последней.
"""

import asyncio

from src.dating.panel_fetch import DatingPanelFetchError
from src.dating.site.site_socket_luxee import (site_socket_luxee_channels,
                                               site_socket_luxee_open,
                                               site_socket_luxee_outer,
                                               site_socket_luxee_send)


async def send_panel_socket_run(profile: dict, presence: dict, to_uid: str,
                                text: str, message_uid: str) -> dict:
    """Шлёт реплику девушки собеседнику сокетом панели. Ответ — пятёрка `send_message`.

    Args:
        profile: строка `profile`; её внутренний номер — `profile_id` (запасной
            путь — `login`: у Luxee это одно и то же число).
        presence: описание сайта — нужен `base`.
        to_uid: номер собеседника на сайте.
        text: что отправляем.
        message_uid: наш ключ сообщения — он же `key` реплики у сервера.

    ⚠ Ничего не бросает: исход словами, как у прочих транспортов.
    """
    girl = str(profile.get('profile_id') or profile.get('login') or '').strip()
    if not girl.isdigit():
        return _result(False, 'у анкеты нет номера на сайте')

    try:
        async with site_socket_luxee_open(int(profile.get('site_id') or 0),
                                          presence, int(girl)) as sock:
            identity = await _identity(sock, int(girl), str(to_uid))
            if not identity:
                return _result(False, 'диалога с этим собеседником у анкеты нет')

            # ⚠ Молчание ловим **здесь**, внутри соединения: снаружи его
            # перехватит дверь открытия и назовёт «сокет не ответил» — а это
            # другое событие (реплика, может быть, ушла).
            try:
                got = await site_socket_luxee_send(sock, int(girl), identity,
                                                   text, message_uid)
            except asyncio.TimeoutError:
                return _result(False, 'сервер чата не подтвердил отправку')
    except DatingPanelFetchError as e:
        return _result(False, str(e))

    if not got['ok']:
        code = f" (код {got['code']})" if got['code'] else ''
        return _result(False, f"сервер чата отказал{code}: {got['error']}",
                       response=got['error'])

    return {'ok': True, 'error': '', 'offline': False,
            'response': got['sid'], 'message_uid': message_uid}


async def _identity(sock, girl: int, man: str) -> str:
    """Номер диалога девушки с этим мужчиной; несколько — самый свежий."""
    outer = await site_socket_luxee_outer(sock, [girl])
    mine = {uid for uid, inner in outer.items() if inner == str(girl)}

    found = []
    for channel in await site_socket_luxee_channels(sock, girl):
        identity = str(channel.get('identity') or '')
        # ⚠ Порядок сторон в номере не гарантирован (видели только «мужчина
        # первым», но оба номера возрастают, и это может быть сортировка):
        # сверяем множеством, а не позицией.
        sides = set(identity.split('_'))
        if man in sides and sides & mine:
            found.append((int(channel.get('lastActivity') or 0), identity))

    return max(found)[1] if found else ''


def _result(ok: bool, error: str, response: str = '') -> dict:
    """Пятёрка ответа `send_message`: ключи все и всегда."""
    return {'ok': ok, 'error': error, 'offline': False, 'response': response,
            'message_uid': ''}
