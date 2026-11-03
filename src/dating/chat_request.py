"""Запросы чата: вторая дорога к разговору, и без неё первой не бывает.

## ⚠⚠⚠ Разговор начинается не с реплики

У Step2Love написать человеку в чат нельзя, пока между вами нет **открытого
чата**. А открывается он только одним способом — запросом (`chatRequest`),
который одна сторона посылает, а другая принимает:

    он прислал запрос  →  мы приняли   →  чат открыт, реплики ходят
    мы прислали запрос →  он принял    →  то же самое
    запрос висит       →  чата нет, и реплику слать некуда

Пока запрос не принят, `chatObjectList` пуст, отправка отвечает `offline` («у
анкеты нет открытого чата с этим собеседником»), а переписки в панели не
заводится вовсе. Со стороны это выглядит так, будто собеседник молчит, — а он
ждёт, когда мы нажмём галочку.

Видно это на странице чата сайта: справа **DIALOG LIST** (открытые разговоры)
и под ним **REQUEST LIST** — те, кто постучался. У панели агентства те же два
числа названы «Активные» и «Непринятые приглашения».

## Что здесь

Чтение списка запросов и их принятие — тем же транспортом, которым ходит
отправка (`chat_socket.py`). Браузер не нужен: запросы приезжают в кадре
авторизации, а принятие — один кадр `chatRequest/confirm`.

⚠ **Отклонять не умеем намеренно.** Отказ — решение человека, а не круга:
промолчать можно всегда, а отклонённый запрос собеседник увидит отказом.
Метод у сайта есть (`cancel`), и добавить его недолго, — но пусть это будет
осознанная правка, а не побочная возможность.

## ⚠⚠⚠ Приглашение несёт текст, и это делает его нашим инвайтом

Запрос у этого сайта — не пустой стук: в кадре создания едет `message`, и
страница показывает его прямо в списке («do you like sport girls?»). То есть
**первая реплика и приглашение — одно и то же событие**, и слать инвайт в
закрытый чат обычным `sendMessage` нельзя: писать некуда, пока чата нет.

Вид кадра взят из SDK сайта дословно (`onlineSDK.min.js`, `createRequest`):

    appNameCreate / appNameTarget = 'chatRequest'
    method = 'create'
    data = {userID: кому, message: текст, attachID: 0}

⚠⚠ **`attachID` обязателен**, пусть и нулём: у SDK он `a || 0`, то есть поле
всегда в кадре. Пропуск полей у этого сайта уже стоил двух реплик, ушедших в
никуда (`chat_socket.py`, про `action`).
"""

from logging_.logging_ import logger_info

from src.dating.chat_socket import (ChatSocketError, chat_socket_frame,
                                    chat_socket_hello, chat_socket_open,
                                    chat_socket_send)

# Имя приложения и методы — словами SDK сайта (`ChatRequestObject`).
CHAT_REQUEST_APP = 'chatRequest'
CHAT_REQUEST_CONFIRM = 'confirm'
CHAT_REQUEST_CREATE = 'create'

# Где в кадре авторизации лежит список запросов.
CHAT_REQUEST_LIST = 'chatRequestObjectList'

# Имена полей в кадре. ⚠⚠ Не `idCreate`/`idTarget`, как зовут их **геттеры**
# SDK (`getIDCreate`): в самом кадре они приезжают полными —
# `userIDCreate`/`userIDTarget`. Первая редакция читала по геттерам и находила
# ноль запросов при живом, видном на странице сайта; поймано живой пробой, а
# не сюитой — поддельный кадр был собран с теми же неверными именами.
CHAT_REQUEST_FROM = 'userIDCreate'
CHAT_REQUEST_TO = 'userIDTarget'

# Состояния запроса — числа самого сайта (`ChatRequestObject.STATUSID_*`).
#
# ⚠ Принимать можно только **ждущий** (`CREATE`). Прочие — уже решённые:
# принят, отозван, просрочен, отклонён одной из сторон, не хватило денег.
# Шли мы `confirm` на такой, сайт ответил бы отказом, а мы записали бы это
# поломкой.
CHAT_REQUEST_WAITING = 1

# Сколько запросов принимаем за один заход. ⚠ Потолок не про нагрузку, а про
# неожиданность: каждый принятый открывает чат, то есть заводит разговор,
# который кто-то должен вести.
CHAT_REQUEST_MAX = 10


async def chat_request_rows(profile: dict, presence: dict) -> list[dict]:
    """Запросы чата, которые ждут решения. Пусто — таких нет.

    Args:
        profile: строка `profile` — нужны номер анкеты на сайте и её сессия.
        presence: настройка сайта; из неё берётся адрес чат-сервера.

    Returns:
        list: записи `{uid, name, text, created_at}` — кто постучался и с чем.

    Raises:
        ChatSocketError: соединение не открылось (нет входа, сайт молчит).

    ⚠ Берём только те, где **мы адресат**: в списке лежат и наши собственные
    запросы, отправленные инвайтами. Принять свой же нельзя — сайт отвечает
    «This user not target», и попытка выглядела бы поломкой.
    """
    base = str((presence.get('send') or {}).get('base') or '').rstrip('/')
    if not base:
        raise ChatSocketError('у сайта не задан адрес чата (presence.send.base)')

    opened = await chat_socket_open(profile, presence,
                                    presence.get('send') or {}, base)

    me = str(profile.get('profile_id') or '').strip()

    out = []
    for one in chat_socket_hello(opened['frames'], CHAT_REQUEST_LIST):
        if not isinstance(one, dict):
            continue

        data = one.get('data') if isinstance(one.get('data'), dict) else one

        if int(data.get('statusID') or 0) != CHAT_REQUEST_WAITING:
            continue

        # ⚠ Адресат — мы: `userIDTarget` это тот, кому постучались.
        if str(data.get(CHAT_REQUEST_TO) or '') != me:
            continue

        him = str(data.get(CHAT_REQUEST_FROM) or '')
        if not him:
            continue

        out.append({'uid': him,
                    'name': _name_of(data),
                    'text': str(data.get('message') or ''),
                    'created_at': data.get('createTime')})

    return out


async def chat_request_accept(profile: dict, presence: dict, uid: str,
                              proxy: str = '') -> dict:
    """Принимает запрос чата от этого человека. Разговор после этого открыт.

    Args:
        profile: строка `profile`.
        presence: настройка сайта.
        uid: номер того, кто прислал запрос.
        proxy: выход наружу этой анкеты.

    Returns:
        dict: `ok`, `error`, `response` — как у отправки реплики.

    ⚠ **Ничего не бросает.** Принимает круг, обходящий все анкеты сайта;
    исключение отсюда унесло бы остальных.

    ⚠⚠ В кадре **два разных номера**, и перепутать их легко: `userID` самого
    кадра — это **мы** (под кем открыт сокет), а `userID` внутри `data` — тот,
    **кто прислал** запрос. У SDK это видно по проверке `getID() !==
    getIDTarget()`: подтверждает всегда адресат.
    """
    base = str((presence.get('send') or {}).get('base') or '').rstrip('/')
    if not base:
        return {'ok': False, 'response': '',
                'error': 'у сайта не задан адрес чата (presence.send.base)'}

    send = presence.get('send') or {}
    me = str(profile.get('profile_id') or '').strip()

    try:
        opened = await chat_socket_open(profile, presence, send, base, proxy)
        frame = chat_socket_frame(CHAT_REQUEST_APP, CHAT_REQUEST_CONFIRM, me,
                                  {'userID': int(uid)})

        return await chat_socket_send(opened, base, presence, send, frame)
    except ChatSocketError as e:
        return {'ok': False, 'response': '', 'error': str(e)}
    except Exception as e:
        return {'ok': False, 'response': '', 'error': f'{type(e).__name__}: {e}'}


async def chat_request_create(profile: dict, presence: dict, uid: str,
                              text: str, proxy: str = '') -> dict:
    """Шлёт приглашение в чат с первой репликой. Разговор откроет его согласие.

    Args:
        profile: строка `profile`.
        presence: настройка сайта.
        uid: кому — номер собеседника на сайте.
        text: первая реплика; она же видна ему в списке приглашений.
        proxy: выход наружу этой анкеты.

    Returns:
        dict: `ok`, `error`, `response` — как у отправки реплики.

    ⚠⚠ **Удача здесь значит «приглашение принято сайтом», а не «доставлено».**
    Чата после неё по-прежнему нет: он откроется, когда собеседник нажмёт
    галочку. Путать эти две вещи дорого — вызывающий, принявший `ok` за
    доставку, закрыл бы строку пула, и повторить приглашение стало бы нечем.

    ⚠ **Ничего не бросает**, как и приём: шлёт круг, обходящий анкеты сайта.
    """
    base = str((presence.get('send') or {}).get('base') or '').rstrip('/')
    if not base:
        return {'ok': False, 'response': '',
                'error': 'у сайта не задан адрес чата (presence.send.base)'}

    send = presence.get('send') or {}
    me = str(profile.get('profile_id') or '').strip()

    try:
        opened = await chat_socket_open(profile, presence, send, base, proxy)
        frame = chat_socket_frame(CHAT_REQUEST_APP, CHAT_REQUEST_CREATE, me,
                                  # ⚠ `attachID` нулём, но в кадре: у SDK поле
                                  # стоит всегда (`a || 0`).
                                  {'userID': int(uid), 'message': str(text),
                                   'attachID': 0})

        return await chat_socket_send(opened, base, presence, send, frame)
    except ChatSocketError as e:
        return {'ok': False, 'response': '', 'error': str(e)}
    except Exception as e:
        return {'ok': False, 'response': '', 'error': f'{type(e).__name__}: {e}'}


async def chat_request_accept_all(profile: dict, presence: dict,
                                  take: int = 0) -> dict:
    """Принимает все ждущие запросы этой анкеты.

    Returns:
        dict: `found` — сколько ждало, `taken` — сколько приняли, `error` —
        причина, если список не прочитался вовсе.

    ⚠ Отказ **одного** запроса не уносит остальные: каждый сам по себе, и
    неудача на первом не повод бросать второго.
    """
    try:
        rows = await chat_request_rows(profile, presence)
    except ChatSocketError as e:
        return {'found': 0, 'taken': 0, 'error': str(e)}
    except Exception as e:
        return {'found': 0, 'taken': 0, 'error': f'{type(e).__name__}: {e}'}

    rows = rows[:int(take or CHAT_REQUEST_MAX)]

    taken = 0
    for one in rows:
        got = await chat_request_accept(profile, presence, one['uid'],
                                        str(profile.get('proxy_url') or ''))
        if got['ok']:
            taken += 1
            logger_info(f"[dating] анкета #{profile.get('id')}: принят запрос "
                        f"чата от {one['uid']}")
        else:
            logger_info(f"[dating] анкета #{profile.get('id')}: запрос чата от "
                        f"{one['uid']} не принят — {got['error']}")

    return {'found': len(rows), 'taken': taken, 'error': ''}


# ── Приватное ────────────────────────────────────────────────────────────────


def _name_of(data: dict) -> str:
    """Имя приславшего — из вложенной карточки, если сайт её положил."""
    for key in ('userObjectCreate', 'userObject'):
        one = data.get(key)
        if isinstance(one, dict) and one.get('name'):
            return str(one['name'])

    return ''
