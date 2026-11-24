"""Быстрый ответ: чем закрыть паузу, пока агент сочиняет.

## ⚠⚠ Зачем занятие существует

Собеседник написал — и видит пустой чат, пока судья решает, отвечать ли, а LLM
пишет ответ: минута-две для живого разговора — вечность. Живая девушка на такую
паузу отвечает хотя бы «hi»; наше молчание собеседник читает как безразличие, и
часть разговоров умирает в этой паузе, не дожив до ответа.

Через `chat_quick_after` секунд тишины (настройка сайта, ноль — выключено) круг
`chat_quick_round.py` шлёт **случайный шаблон** из `chat_quick` — смайлик,
«hi», что угодно одной строкой. Случайный: одна и та же «привет» в тридцатый раз
выдаёт робота, разброс — нет.

## ⚠⚠ Быстрый — НЕ ответ, и это главное правило занятия

Настоящий ответ агента пауза **не отменяет**: реплика в диалоге появится, а
неотвеченное входящее останется неотвеченным (`_answered_mark` быстрый не
ставит). Два сообщения на одно входящее — штатный исход. Из этого правила
растут все исключения `source = 'quick'` по коду:

* `send.py` — строка в `message` пишется `source='quick'`, `by_agent=0`, и
  пометка «отвечено» не ставится: круг ответов обязан увидеть это же входящее.
* `answer._waiting_find`, `_waiting_tail`, `_said_already`, `_anyway_here` —
  «последняя реплика пары» считается **без** быстрых строк: с quick в ней
  диалог выглядел бы так, будто мы уже ответили, и автоответ прошёл бы мимо.
* `initiative._quiet` / `_pair_state` — таймеры тишины и «последнее слово»
  тоже не видят quick: с ним затихший разговор считался бы оживлённым нашим
  же «hi».
* `site_socket_luxee_send` — перед отправкой сокет смотрит живое окно диалога
  и отменяет ответ, если её сторона писала новее повода. Эхо нашего же быстрого
  ответа прилетает оттуда **как её реплика** — текст быстрая строка узнаёт по
  нашей базе (`send_panel_socket._quick_texts`), живое решение человека такое
  эхо не отменит.
* Близнецы (`message_twin`) — правку не требуют вовсе: правило прячет
  панельное отражение по условию `twin.source <> 'panel'`, и строка `quick`
  подходит под него сама; `chat_history_said` берёт только `source='panel'`.

Соседу (RhapsodyAi) быстрый ответ **не сообщается никогда**: ни `/chat/ask`,
ни доставками. Агент узнаёт о реплике из своего круга, как будто быстрого не
было, — иначе он отвечал бы на наш же смайлик.

## ⚠ Одна попытка на входящее

Отметка — ключ Redis `dating:chat_quick:tried:{номер входящего}` с TTL, большим
окна отбора: пара, которой разок не повезло (собеседник вышел из сети, сайт
отказал), не получает второй стук в ту же дверь. Redis лёг — быстрая строка
**не уходит**: это удобствие, а не ответ; без отметки стучаться каждые пять
секунд хуже, чем молчать. Ответит агент — как отвечал всегда.
"""

import random

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.dating.auto import answer_on
from src.dating.block import block_pairs
from src.dating.feed import feed_write
from src.dating.send import send_message
from src.dating.site_presence import site_presence
from src.dating.site_setting import site_seconds

# Настройка сайта: через сколько секунд тишины уходит быстрый ответ.
# Ноль — выключено (реестр и границы — в `site_setting.SITE_KNOWN`).
QUICK_AFTER_SETTING = 'chat_quick_after'

# Источник строки в `message`. ⚠ Значение enum — его заводит миграция
# `20261122120000_chat_quick.sql`; без правки миграции вставка падает.
QUICK_SOURCE = 'quick'

# Сколько ещё секунд после срока пара годна: окно `[N, N + 300]`. Нижняя
# граница — сам интервал, верхняя — страховка от круга, который проспал
# (воркер перезапускался, сайт молчал): реплике час — «hi» вчерашнему вопросу
# не нужен, это уже не быстрый ответ.
QUICK_WINDOW_SEC = 300

# Сколько пар обслуживаем за один заход сайта. Запас сознательный: тик — пять
# секунд, и пара, не попавшая в десятку, попадёт в следующий тик, пока сидит
# в окне.
QUICK_LIMIT = 10

# TTL отметки «пробовали». ⚠ Больше всего окна (`N` до часа + `QUICK_WINDOW_SEC`):
# ключ обязан пережить срок, в течение которого пара может быть отобрана, —
# иначе он истечёт раньше окна, и вторая попытка всё-таки случится.
QUICK_TRIED_TTL_SEC = 7200

# Ключ отметки. ⚠ Метка `{id}` — номер **входящего**, породившего паузу: ровно
# одно быстрое сообщение на одно входящее, чем бы попытка ни кончилась.
QUICK_TRIED_KEY = 'dating:chat_quick:tried:{id}'


# ── Шаблоны ──────────────────────────────────────────────────────────────────


async def chat_quick_list(site_id: int) -> list[dict]:
    """Шаблоны сайта: `id`, `body`, `created_at`, свежие снизу — как заводились."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, body, created_at FROM chat_quick'
                ' WHERE site_id = %s ORDER BY id', (int(site_id),))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] шаблоны быстрого ответа не прочитались: {e}')

        return []

    return [dict(row) for row in rows or []]


async def chat_quick_add(site_id: int, body: str) -> dict:
    """Заводит шаблон. Пустой (или из одних пробелов) — `ValueError`.

    ⚠ Переводы строк сворачиваются в пробелы: шаблон — **одна** реплика, а
    многострочная выглядела бы в чате как спам, и жребий между ними сравнивать
    нечего.
    """
    text = ' '.join(str(body or '').split())
    if not text:
        raise ValueError('шаблон пустой')

    try:
        async with mysql_get_db_async() as db:
            await db.execute('INSERT INTO chat_quick (site_id, body) VALUES (%s, %s)',
                             (int(site_id), text[:2000]))
            await db.connection.commit()
            new_id = int(db.lastrowid or 0)
    except Exception as e:
        logger_info(f'[dating] шаблон быстрого ответа не записался: {e}')

        raise ValueError(f'шаблон не записался: {e}') from e

    return {'id': new_id, 'body': text}


async def chat_quick_drop(quick_id: int) -> bool:
    """Убирает шаблон. `False` — его уже нет (вторая рука успела)."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM chat_quick WHERE id = %s', (int(quick_id),))
            await db.connection.commit()

            return bool(int(db.rowcount or 0))
    except Exception as e:
        logger_info(f'[dating] шаблон быстрого ответа не удалился: {e}')

        return False


# ── Круг ─────────────────────────────────────────────────────────────────────


async def chat_quick_run(site_id: int) -> dict:
    """Один заход по сайту: кому тишина — тому случайный шаблон.

    Returns:
        dict: `checked` — пар нашли, `sent` — сколько ушло.

    ⚠ Заход **не бросает**: отказ одной отправки — строка в журнал, остальные
    идут. Возвращает всегда, не кидает: круг ходит по всем сайтам подряд, и
    первый кривой не имеет права лишать остальных.
    """
    after = await site_seconds(int(site_id or 0), QUICK_AFTER_SETTING)
    if after <= 0:
        return {'checked': 0, 'sent': 0}

    # ⚠ Тот же выключатель, что у автоответа (`auto.answer_on`), и по тому же
    # доводу: быстрый ответ — тень ответа. Site, которому агент не отвечает,
    # смайликами засыпать нельзя — человек снял галочку «отправка ответов»,
    # чтобы его анкета молчала.
    if not await answer_on(int(site_id or 0)):
        return {'checked': 0, 'sent': 0}

    templates = await chat_quick_list(int(site_id or 0))
    if not templates:
        # Пустой список — выключено: слать нечего, а отказ слова ради значил бы
        # строку в журнал каждые пять секунд по каждому голому сайту.
        return {'checked': 0, 'sent': 0}

    rows = await _waiting_pairs(int(site_id or 0), after)
    if not rows:
        return {'checked': 0, 'sent': 0}

    blocked = {(int(profile_id), str(uid))
               for profile_id, uid in await block_pairs(int(site_id or 0), 'chat')}
    presence = await site_presence(int(site_id or 0))
    bodies = [str(row.get('body') or '') for row in templates if str(row.get('body') or '').strip()]

    sent = 0
    for row in rows:
        pair = (int(row['id']), str(row['sender_uid']))
        if pair in blocked:
            # ⚠ Он закрылся от этой анкеты — и «hi» тому, кто просил не
            # писать, дороже обычного «hi»: сайт считает такие попытки
            # жалобой. Правило общее, `block.py`.
            continue

        if not _tried(int(row['message_id'])):
            continue

        got = await send_message(dict(row), presence, str(row['sender_uid']),
                                 random.choice(bodies),
                                 since=row['trigger_at'], quick=True)
        if got['ok']:
            sent += 1
            continue

        # ⚠ Отказ — только журнал: повторной попытки у этого входящего всё
        # равно не будет (отметка снята до отправки), а долбить сайт
        # нечем — то же правило, что у автоответа.
        logger_info(f"[dating] быстрый ответ #{row['message_id']}: {got['error']}")

    if sent:
        feed_write(int(site_id or 0), 'chat', f"Быстрый ответ: {sent}")

    return {'checked': len(rows), 'sent': sent}


# ── Приватное ────────────────────────────────────────────────────────────────


async def _waiting_pairs(site_id: int, after_sec: int) -> list[dict]:
    """Пары сайта, у которых тишина ровно в окне `[N, N + 300]` секунд.

    Отбор **одним запросом** — и в нём ровно те условия, что у занятия:

    * анкета сайта, включена и `status = 'online'` — мёртвый вход не то что
      не шлёт, он и тишину считать не должен;
    * **последняя** реплика пары — его входящее без ответа и без решения
      молчать (`is_answered = 0`, `answer_silent_at IS NULL`): судья уже
      промолчал — быстрый лезть не вправе, и ждущее ответа слово — тоже не
      пауза;
    * старше `N` секунд, но не старше `N + 300` — окно выше;
    * **никакой** нашей исходящей строки новее неё — в том числе быстрой:
    * «последняя» считается по **всем** строкам, и quick в этом отборе участвует
      (в отличие от автоответа, который его не видит): ушли «hi» пять секунд
      назад — последняя реплика наша, тишины нет.

    ⚠ Порядок по времени, `id` лишь при совпадении — та же мерка, что у
    `answer._waiting_find` и списка диалогов: импорт кладёт историю сегодня.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT m.id AS message_id, m.sender_uid, m.sender_name,'
                '       m.created_at AS trigger_at, p.*'
                '  FROM profile p'
                '  JOIN (SELECT m2.profile_id, m2.sender_uid,'
                '               MAX(m2.created_at) AS last_at'
                '          FROM message m2'
                "         WHERE m2.profile_id IN (SELECT id FROM profile"
                '                                   WHERE site_id = %s'
                "                                     AND enabled = 1"
                "                                     AND status = 'online')"
                '         GROUP BY m2.profile_id, m2.sender_uid) t'
                '    ON t.profile_id = p.id'
                '  JOIN message m'
                '    ON m.profile_id = t.profile_id AND m.sender_uid = t.sender_uid'
                '   AND m.created_at = t.last_at'
                '   AND m.id = (SELECT MAX(x.id) FROM message x'
                '                 WHERE x.profile_id = t.profile_id'
                '                   AND x.sender_uid = t.sender_uid'
                '                   AND x.created_at = t.last_at)'
                " WHERE p.site_id = %s AND p.enabled = 1 AND p.status = 'online'"
                "   AND m.direction = 'in' AND m.is_answered = 0"
                '   AND m.answer_silent_at IS NULL'
                '   AND m.created_at < NOW() - INTERVAL %s SECOND'
                '   AND m.created_at >= NOW() - INTERVAL %s SECOND'
                ' ORDER BY m.created_at DESC, m.id DESC LIMIT %s',
                (int(site_id), int(site_id), int(after_sec),
                 int(after_sec) + QUICK_WINDOW_SEC, QUICK_LIMIT))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] пары для быстрого ответа не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


def _tried(message_id: int) -> bool:
    """Снимаем отметку «пробовали». `True` — можно слать, проба наша.

    ⚠⚠ `SET NX EX` — проверка и запись **одним** атомарным ударом: два круга
    (или два воркера) у одного входящего иначе послали бы по шаблону.

    ⚠ Сбой Redis — **не** слать: без отметки занятие стучалось бы по каждой
    зависшей паре каждые пять секунд, а это ровно то «долбить», чего правило
    бережёт. Агент не пострадает: его путь от Redis не зависит.
    """
    try:
        return bool(redis_conn_get().set(QUICK_TRIED_KEY.format(id=int(message_id)),
                                         '1', nx=True, ex=QUICK_TRIED_TTL_SEC))
    except Exception as e:
        logger_info(f'[dating] отметка быстрого ответа не снялась: {e}')

        return False
