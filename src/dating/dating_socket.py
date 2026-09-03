"""Кто сейчас держит сокет: снимок состояния держателя для панели.

## Зачем

Держатели живут **в памяти воркера**, а панель — отдельный процесс. Спросить его
напрямую нельзя: между ними нет ничего, кроме базы и Redis. Поэтому воркер кладёт
снимок раз в круг, а панель его читает.

Вопрос, на который отвечает страница, — «почему анкета молчит». Ответов у него
несколько, и по базе их не различить: адреса сокета нет вовсе, адрес есть но
соединение рвётся, соединение живо но сообщений нет. Первое видно в
`dating_profile.ws_url`, остальные — только здесь.

## ⚠ Снимок, а не живое состояние

Между записями проходит до `DATING_CHAT_HOLD_RESCAN_SEC` (минута): страница
показывает то, что было на последнем круге, и говорит, сколько снимку секунд.
Живое состояние потребовало бы канала «панель ↔ воркер», которого нет и заводить
ради показа не стоит.

⚠ **Нет снимка — не значит «сокетов нет».** Смена могла только что подняться, а
воркер — перезапуститься. Пустой список и «воркер молчит» это разные ответы, и
страница их различает: у ключа стоит срок жизни, и его истечение само по себе
сведение.
"""

import json
import time

from logging_.logging_ import logger_info
from redis_.redis_ import redis_conn_get

# Где лежит снимок. Один ключ на установку: смена в ней одна (single-flight), и
# делить снимок по сайтам значило бы собирать его из кусков ради того же списка.
DATING_SOCKET_KEY = 'dating:socket:holders'

# Сколько снимок живёт. Вдвое дольше круга смены: одного пропущенного круга (сеть
# моргнула, база задумалась) мало, чтобы объявлять держатель мёртвым, а двух уже
# достаточно — значит смена и правда встала.
DATING_SOCKET_TTL_SEC = 150


def dating_socket_write(holders: dict, counters: dict, shift_left: float) -> None:
    """Кладёт снимок держателей. Зовётся из круга смены, после сверки состава.

    Args:
        holders: что держит смена — `{profile_id: {'ws_url', 'task', 'login'}}`.
        counters: сколько сообщений поймано за смену по каждой анкете.
        shift_left: сколько секунд до конца смены.

    ⚠ **Не бросает.** Снимок — сведение для показа, и сбой Redis не повод ронять
    круг, который держит сокеты: страница переживёт пустой список, анкеты без
    держателя — нет.
    """
    rows = []
    for profile_id, held in holders.items():
        task = held.get('task')
        rows.append({
            'profile_id': int(profile_id),
            'login': str(held.get('login') or ''),
            # ⚠ Адрес режем: в нём токен сессии, а снимок читает страница. Хвоста
            # хватает, чтобы отличить один адрес от другого при переподключении.
            'ws_url': _url_short(str(held.get('ws_url') or '')),
            'alive': bool(task is not None and not task.done()),
            'messages': int(counters.get(profile_id, 0)),
        })

    snapshot = {'at': time.time(), 'shift_left': int(max(0, shift_left)),
                'holders': sorted(rows, key=lambda r: r['profile_id'])}

    try:
        redis_conn_get().setex(DATING_SOCKET_KEY, DATING_SOCKET_TTL_SEC,
                               json.dumps(snapshot, ensure_ascii=False))
    except Exception as e:
        logger_info(f'[dating] снимок сокетов не записался: {e}')


def dating_socket_read() -> dict:
    """Снимок для страницы.

    Returns:
        dict: `holders` — список держателей, `at` — когда снят (unix), `age_sec` —
        сколько ему секунд, `alive` — жив ли воркер (снимок вообще есть).

        ⚠ `alive = False` означает «воркер молчит», а не «сокетов нет». Разница
        важна: во втором случае искать надо анкеты, в первом — смену.
    """
    try:
        raw = redis_conn_get().get(DATING_SOCKET_KEY)
    except Exception as e:
        logger_info(f'[dating] снимок сокетов не прочитался: {e}')

        return {'holders': [], 'at': 0, 'age_sec': -1, 'alive': False}

    if not raw:
        return {'holders': [], 'at': 0, 'age_sec': -1, 'alive': False}

    try:
        snapshot = json.loads(raw)
    except ValueError:
        return {'holders': [], 'at': 0, 'age_sec': -1, 'alive': False}

    return {'holders': snapshot.get('holders') or [],
            'at': snapshot.get('at') or 0,
            'age_sec': int(time.time() - (snapshot.get('at') or 0)),
            'shift_left': int(snapshot.get('shift_left') or 0),
            'alive': True}


# ── Приватное ────────────────────────────────────────────────────────────────

def _url_short(url: str) -> str:
    """Адрес без токена: `wss://host/path…abc123`.

    ⚠ **Целиком отдавать нельзя.** В адресе токен сессии сайта: по нему можно
    подключиться к чату анкеты со стороны, а снимок уезжает на страницу и оседает
    в журналах запросов. Хвоста хватает, чтобы отличить один адрес от другого —
    ровно за этим на него и смотрят.
    """
    if not url:
        return ''

    head = url.split('?')[0]

    return f'{head}…{url[-6:]}' if len(url) > len(head) + 6 else head
