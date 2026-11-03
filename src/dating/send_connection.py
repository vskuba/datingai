"""Отправка реплики туда, где вебсокет завёрнут в обычный HTTP.

Транспорт — общий (`chat_socket.py`): открытие соединения, кадры, ожидание
приговора. Здесь остаётся то, что про **реплику**: где взять номер чата и из
чего собрать кадр `sendMessage`.

⚠⚠ Разделение не красоты ради. Через тот же транспорт ходит приём запросов
чата (`chat_request.py`), и пока всё лежало здесь, второе дело стало бы
копией первого — а первая же правка (заголовок, срок, формат ответа)
разошлась бы молча.

## ⚠⚠ Номер чата спрашивается у сокета, а не у панели

В ответ на открытие приезжает `chatObjectList` — живые разговоры анкеты.
Номер берём оттуда: панель знает его задним числом, а сокет отвечает на
вопрос «куда писать **сейчас**». Нет живого чата с этим человеком —
отправлять некуда, и это не поломка.

⚠⚠⚠ **Чата может не быть по существу, а не по ошибке.** У этого сайта
разговор открывается только принятым запросом (`chat_request.py`): пока он
висит, писать некуда, сколько ни пробуй. Отсюда и `offline` в ответе —
состояние, а не сбой.
"""

from src.dating.chat_socket import (ChatSocketError, chat_socket_frame,
                                    chat_socket_hello, chat_socket_open,
                                    chat_socket_send)

# Имя приложения и метода у этого движка. В настройке сайта их можно
# переопределить (`presence.send.app` / `.method`), но своего значения они не
# меняют — это имена из SDK, а не вкус владельца сайта.
SEND_CONNECTION_APP = 'chat'
SEND_CONNECTION_METHOD = 'sendMessage'

# Где в кадре авторизации лежат живые чаты анкеты.
SEND_CONNECTION_CHATS = 'chatObjectList'


async def send_connection_run(profile: dict, presence: dict, send: dict,
                              to_uid: str, text: str, message_uid: str,
                              proxy: str = '') -> dict:
    """Шлёт реплику кадром через HTTP-обёртку сокета.

    Args:
        profile: строка `profile` — нужны номер анкеты на сайте и её сессия.
        presence: настройка сайта целиком (из неё берутся заголовки).
        send: раздел `presence.send` — адрес чата и имена кадра.
        to_uid: номер собеседника на сайте.
        text: что отправляем.
        message_uid: наш идентификатор реплики; он же становится `ID` кадра —
            по нему и узнаётся ответ сайта.
        proxy: выход наружу этой анкеты.

    Returns:
        dict: та же пятёрка, что у прочих путей отправки
        (`ok`/`offline`/`response`/`message_uid`/`error`) плюс `status`.

    ⚠ **Ничего не бросает.** Отправкой правит круг, обходящий все анкеты сайта;
    исключение отсюда унесло бы остальных.
    """
    base = str(send.get('base') or '').rstrip('/')
    if not base:
        return _fail('у сайта не задан адрес чата (presence.send.base)',
                     message_uid)

    try:
        opened = await chat_socket_open(profile, presence, send, base, proxy)
    except ChatSocketError as e:
        return _fail(str(e), message_uid)
    except Exception as e:
        return _fail(f'{type(e).__name__}: {e}', message_uid)

    chat_id = _chat_of(opened['frames'], to_uid)
    if not chat_id:
        # ⚠ Это **не ошибка настройки**: живого разговора с этим человеком у
        # анкеты сейчас нет — чаще всего потому, что запрос чата ещё не принят
        # (`chat_request.py`). Отдаём `offline` — тем же словом, каким
        # отвечает сокет, когда собеседника нет в чате: вызывающие уже умеют
        # читать его как состояние, а не как поломку.
        return {'ok': False, 'offline': True, 'response': '',
                'message_uid': message_uid, 'status': 0,
                'error': 'у анкеты нет открытого чата с этим собеседником'}

    frame = chat_socket_frame(
        str(send.get('app') or SEND_CONNECTION_APP),
        str(send.get('method') or SEND_CONNECTION_METHOD),
        str(profile.get('profile_id') or ''),
        {'chatID': int(chat_id), 'message': str(text), 'attachID': 0},
        message_uid)

    try:
        said = await chat_socket_send(opened, base, presence, send, frame)
    except Exception as e:
        return _fail(f'{type(e).__name__}: {e}', message_uid)

    return {**said, 'message_uid': message_uid, 'offline': False}


# ── Приватное ────────────────────────────────────────────────────────────────


def _chat_of(frames: list, to_uid: str) -> int:
    """Номер живого чата с этим собеседником. Ноль — такого разговора нет."""
    want = str(to_uid or '').strip()

    for chat in chat_socket_hello(frames, SEND_CONNECTION_CHATS):
        if not isinstance(chat, dict) or chat.get('destroy'):
            continue
        if str(chat.get('userIDMan') or '') == want \
                or str(chat.get('userIDGirl') or '') == want:
            return int(chat.get('chatID') or 0)

    return 0


def _fail(error: str, message_uid: str, response: str = '') -> dict:
    """Неудача в той же форме, что у прочих путей отправки."""
    return {'ok': False, 'offline': False, 'response': response,
            'message_uid': message_uid, 'status': 0, 'error': error}
