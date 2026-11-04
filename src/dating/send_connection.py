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

## ⚠⚠ Когда чата нет, но реплика первая

Приглашение у этого сайта **несёт текст** — он виден собеседнику прямо в
списке запросов. Значит первая реплика и приглашение это одно событие, и
инвайт в закрытый чат уходит не `sendMessage`, а `chatRequest/create`.

Развилку включает довод `may_invite`, и он **не выводится из состояния**:
«чата нет» одинаково для инвайта и для ответа, а приглашать вправе только
первый. Решает вызывающий — `invite_send.py` ставит его явно, прочие дороги
получают прежний `offline`.

⚠ Удача приглашения значит «сайт принял», а не «доставлено»: чат откроется,
когда собеседник нажмёт галочку. Поэтому при удаче в ответе стоит
`offline: True` — реплика **ещё не у него**, и вызывающий, приняв удачу за
доставку, закрыл бы строку пула, а повторить приглашение стало бы нечем.

⚠⚠ **`offline` ставится и когда собеседник вышел.** Сайт отвечает на это
«target», и его собственная страница показывает окно «user offline»
(`chat_request.py`). Уход проходит сам через минуты, и строка возвращается в
пул чистой — человек появится в ленте, и реплика уйдёт ему без вмешательства.

⚠⚠⚠ **А настоящий отказ — `offline: False`.** По этому флагу инвайт
возвращает строку чистой; пропусти под ним отказ — круг возьмёт её снова,
получит то же самое, и очередь встанет вовсе (замер 04.11.2026: 54 попытки к
одному человеку за пять минут, весь сайт стоял).
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
                              proxy: str = '', may_invite: bool = False) -> dict:
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
        may_invite: можно ли, при закрытом чате, звать приглашением. ⚠ Ставит
            **вызывающий**, а не состояние: «чата нет» одинаково у инвайта и у
            ответа, а приглашать вправе только первый.

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

    if not chat_id and may_invite:
        return await _invite(profile, presence, to_uid, text, message_uid, proxy)

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


async def _invite(profile: dict, presence: dict, to_uid: str, text: str,
                  message_uid: str, proxy: str) -> dict:
    """Приглашение с первой репликой — когда разговора ещё нет.

    ⚠⚠ `offline` остаётся **`True`** и при удаче: сайт приглашение принял, но
    собеседник его ещё не открыл. Назови это доставкой — и строка пула
    закроется, а позвать второй раз станет нечем.
    """
    from src.dating.chat_request import chat_request_create

    got = await chat_request_create(profile, presence, to_uid, text, proxy)

    # ⚠⚠⚠ `offline` — это **состояние**, и ставится оно в двух разных случаях.
    #
    # Первый: приглашение ушло. Оно лежит у собеседника в списке запросов и
    # ждёт галочки — реплика ещё не у него, и закрыть строку пула нельзя.
    #
    # Второй: собеседник **вышел** (`chat_request_away`). Сайт отвечает
    # «target», и его собственная страница показывает на это окно «user
    # offline». Уход проходит сам через минуты, и строка обязана вернуться в
    # пул **чистой**: человек появится в ленте, и годная реплика уйдёт ему без
    # вмешательства (то же правило, что у `invite_send.py`).
    #
    # ⚠⚠ А настоящий отказ сайта — `offline: False`, и это не симметрия ради
    # красоты. По `offline` инвайт возвращает строку чистой; пропусти под этим
    # флагом отказ — круг возьмёт её снова, получит то же, и очередь встанет
    # вовсе. Замер 04.11.2026: 54 попытки к одному человеку за пять минут,
    # весь сайт стоял.
    return {'ok': got['ok'],
            'offline': bool(got['ok']) or bool(got.get('away')),
            'response': got['response'], 'message_uid': message_uid,
            'status': 0, 'error': got['error'] or ''}


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
