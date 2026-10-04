"""Пул инициативы: строки, которыми возвращаются к затихшему разговору.

Здесь только работа с таблицей: положить карточку, найти ждущую, отметить исход.
Кого и чем звать — решает `initiative.py`, отправкой занят круг
(`initiative_round.py`).

⚠ **Устроено как три соседних пула** (`chat_reply`, `invite`, `mailing`): те же
три состояния, та же неудача, оставляющая строку в пуле с причиной. Четвёртый
способ хранить «текст, ждущий отправки» разошёлся бы с ними на первой правке.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async


async def chat_initiative_put(site_id: int, profile_id: int, uid: str,
                              name: str, body: str, try_no: int = 1,
                              talk_message_id: int = 0) -> int:
    """Кладёт карточку в пул. Возвращает номер строки, ноль — не легло.

    Args:
        site_id: сайт-закладка.
        profile_id: наша анкета.
        uid: собеседник на сайте.
        name: его имя на момент набора.
        body: чем заговорить.
        try_no: который это заход **подряд** в один промежуток тишины (предел —
            настройка `chat_initiative_repeat`). ⚠ Его ответ счёт обнуляет, так
            что «заход 1» бывает и у пары, которой уже писали сегодня; дневной
            потолок считается отдельно (`chat_initiative_max_day`).
        talk_message_id: номер обмена разговора **у соседа** — тот, которым
            отмечается доставка (`rhapsody_api_chat_delivered`). Приходит из
            `_agent_ask` в момент сочинения, а отправка идёт другим кругом
            позже; без своего места в строке он до неё не дожил бы. Ноль
            значит «агент промолчал о номере» и ложится `NULL`, а не самим
            нулём — отметка на нуле ушла бы в никуда.

    ⚠ **Повтор не ошибка.** Ключ базы держит одну ждущую карточку на пару, и
    второй заход круга на ту же пару превращается в `INSERT IGNORE`, который
    ничего не делает.
    """
    if not (int(profile_id or 0) and str(uid or '').strip()
            and str(body or '').strip()):
        return 0

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO chat_initiative'
            '  (site_id, profile_id, uid, name, body, try_no, talk_message_id)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (int(site_id or 0), int(profile_id), str(uid), str(name or ''),
             str(body), max(1, int(try_no or 1)),
             int(talk_message_id or 0) or None))
        await db.connection.commit()

        return int(db.lastrowid or 0)


async def chat_initiative_waiting(profile_id: int, uid: str) -> dict:
    """Ждущая карточка пары. Пусто — её нет."""
    if not (int(profile_id or 0) and str(uid or '').strip()):
        return {}

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, body, try_no, error FROM chat_initiative'
            " WHERE profile_id = %s AND uid = %s AND status = 'draft'",
            (int(profile_id), str(uid)))
        row = await db.fetchone()

    return dict(row) if row else {}


async def chat_initiative_ready(site_id: int) -> list[dict]:
    """Карточки сайта, ждущие отправки, — свежие последними.

    ⚠ **Старые первыми**: карточка тем бесполезнее, чем дольше лежит. Разговор,
    затихший час назад, ещё живой; затихший вчера — уже нет, и его очередь должна
    прийти раньше, пока он не протух окончательно.

    ⚠ Берём **только у анкет в сети**: чат это вход под анкетой, и спящей девушке
    отправить нечем. Проверка стоит в запросе, а не после — иначе круг брал бы
    карточки спящих и каждый раз откладывал их, не доходя до тех, что можно
    отправить.

    ⚠ Отдаём и `talk_message_id`: круг отправки берёт номер отметки доставки
    отсюда — он сочинял карточку другим кругом и другим заходом, и другого
    источника у номера нет.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT i.id, i.site_id, i.profile_id, i.uid, i.name, i.body,'
            '       i.try_no, i.talk_message_id'
            '  FROM chat_initiative i'
            '  JOIN profile p ON p.id = i.profile_id'
            " WHERE i.site_id = %s AND i.status = 'draft' AND i.error = ''"
            "   AND p.enabled = 1 AND p.status = 'online'"
            ' ORDER BY i.id',
            (int(site_id),))
        rows = await db.fetchall()

    return [dict(row) for row in (rows or [])]


async def chat_initiative_sent(initiative_id: int) -> None:
    """Отмечает, что карточка ушла."""
    if not int(initiative_id or 0):
        return

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_initiative SET status = 'sent', error = '',"
            ' sent_at = NOW() WHERE id = %s', (int(initiative_id),))
        await db.connection.commit()


async def chat_initiative_failed(initiative_id: int, error: str) -> None:
    """Запоминает причину неудачи, оставляя карточку в пуле.

    ⚠ Не `failed`, а `draft` с причиной: разговор всё так же затих, и повторить
    стоит. ⚠⚠ Но круг такую карточку **больше не возьмёт** (`error = ''` в
    отборе) — её досылает человек кнопкой. Иначе круг бился бы об одну и ту же
    ошибку каждую минуту, сжигая попытки и журнал.
    """
    if not int(initiative_id or 0):
        return

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_initiative SET status = 'draft', error = %s,"
            ' sent_at = NULL WHERE id = %s',
            (str(error or '')[:500], int(initiative_id)))
        await db.connection.commit()


async def chat_initiative_drop(profile_id: int, uid: str) -> int:
    """Убирает ждущие карточки пары. Возвращает, сколько убрал.

    Зовётся, когда инициатива стала не нужна: человек ответил сам, пару закрыли.
    Ушедшие не трогаем — они часть переписки.
    """
    if not int(profile_id or 0):
        return 0

    async with mysql_get_db_async() as db:
        await db.execute(
            "DELETE FROM chat_initiative WHERE profile_id = %s AND uid = %s"
            " AND status = 'draft'", (int(profile_id), str(uid or '')))
        await db.connection.commit()

        return int(db.rowcount or 0)


async def chat_initiative_stale(profile_id: int, uid: str) -> int:
    """Снимает ждущую карточку, если собеседник заговорил сам.

    ## ⚠⚠ Зачем это отдельно от `drop`

    Он ответил — инициатива стала не просто не нужна, а **вредна**: её текст
    сочинялся под тишину («заговори сама»), и уйдя после его реплики, он прозвучит
    мимо разговора. Отвечать теперь будет автоответ, и ответит он на сказанное.

    Возвращает, сколько сняли, — для журнала круга.
    """
    try:
        return await chat_initiative_drop(profile_id, uid)
    except Exception as e:
        logger_info(f'[dating] протухшая инициатива не снялась: {e}')

        return 0
