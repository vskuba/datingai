"""Намерения разговора: что агент пообещал и исполнено ли это.

Агент `dating_talk` по ходу разговора принимает решения, которых нет в тексте
ответа: «она обещала прислать фото», «он просил не писать до вечера». Сосед
помечает их инструментом `chat_intention_add` и отдаёт списком в ключе
`intentions` ответа `/chat/ask`. Этот модуль — **единственная дверь** к таблице,
где такое обещание ждёт исполнения.

## ⚠ Это не копия `companion_intention` у соседа

Та решает другую задачу: отметкой времени режет историю надвое, чтобы одно
обещание не приезжало на каждом следующем ответе. Там дедупликация внутри
разговора, здесь очередь работы.

## ⚠⚠ Исполнителя пока нет, и дверь заведена раньше него

Круга, который берёт открытые намерения и делает дело, не существует. Память
нужна в тот же миг, когда исполнение появится: заводить её потом значило бы
иметь первые сотни обещаний без следа. Тем же доводом живёт
`companion_gallery.py`.

## ⚠ Пара — это «сайт и человек»

Номер собеседника уникален в пределах сайта, а не вообще, и ключ уникальности —
`(site_id, uid, profile_id, open_intention)`. Анкета в него входит: обещание
даёт она, и обещание другой девушки тому же человеку — своё.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Предел длины метки, знаков — ровно тот, что у соседа
# (`CHAT_INTENTION_LENGTH_MAX` в его `chat_intention.py`): длиннее он не
# принимает, значит приехать не может.
#
# ⚠ Проверка стоит здесь, а не в двери к соседу: столбец принадлежит этому
# модулю. Полагаться на чужой предел нельзя — он правится у соседа.
CHAT_INTENTION_LENGTH_MAX = 100


async def chat_intention_put(profile_id: int, site_id: int, uid: str,
                             intentions: list) -> int:
    """Заводит открытые намерения пары. Отдаёт, сколько новых легло.

    Args:
        profile_id: чьё намерение — анкета, от чьего имени шёл разговор.
        site_id: сайт собеседника.
        uid: номер собеседника на сайте.
        intentions: метки словом, как приехали от соседа.

    Returns:
        int: сколько строк вправду завелось. Ноль — всё уже лежало открытым
        либо писать было нечего, и это не ошибка.

    ## ⚠⚠ Повтор открытой строки — обычный исход, а не поломка

    Одно обещание держится в истории разговора много реплик, и агент вправе
    пометить его снова. Ключ базы такую вставку отвергает сам (`INSERT
    IGNORE`), и вызывающему довольно знать число новых. Выполненная строка ключ
    отпускает — следующее такое же обещание ляжет своей строкой.

    ⚠ Повторы внутри одной пачки тоже схлопывает ключ, а не разбор: одно
    правило вместо двух.

    ⚠ Пустой `uid` или пустая анкета — не пишем: намерению не к кому
    принадлежать.
    """
    said = [str(one or '').strip() for one in (intentions or [])]
    said = [one for one in said if one and len(one) <= CHAT_INTENTION_LENGTH_MAX]

    if not (int(profile_id or 0) and str(uid or '').strip() and said):
        return 0

    put = 0

    async with mysql_get_db_async() as db:
        for one in said:
            await db.execute(
                'INSERT IGNORE INTO chat_intention'
                '  (profile_id, site_id, uid, intention)'
                ' VALUES (%s, %s, %s, %s)',
                (int(profile_id), int(site_id or 0), str(uid).strip(), one))
            put += int(db.rowcount or 0)

        await db.connection.commit()

    if put:
        logger_info(f'[dating] намерений записано: {put} '
                    f'(анкета {int(profile_id)}, собеседник {str(uid).strip()})')

    return put


async def chat_intention_open(site_id: int, profile_id: int = 0, uid: str = '',
                              limit: int = 0) -> list[dict]:
    """Намерения, которые ещё не исполнены. Пусто — законный исход.

    Args:
        site_id: сайт. Ноль — отдаём пусто (см. ⚠ ниже).
        profile_id: отбор по анкете. Ноль — все анкеты сайта.
        uid: отбор по собеседнику. Пусто — все.
        limit: сколько строк вернуть. Ноль — все.

    Returns:
        list[dict]: `id`, `profile_id`, `site_id`, `uid`, `intention`,
        `created_at`; старые первыми.

    ⚠ **Сайт обязателен.** Круги панели обходят сайты поодиночке
    (`round_sites`), и отбор «по всем сразу» здесь не нужен ни одному
    вызывающему. Ноль молча отдаёт пусто, а не всю таблицу: вторая форма
    однажды уехала бы в круг и унесла чужие строки.

    ⚠ Порядок — по номеру строки, то есть по времени: старое обещание исполняют
    раньше нового.
    """
    if not int(site_id or 0):
        return []

    sql = ('SELECT id, profile_id, site_id, uid, intention, created_at'
           '  FROM chat_intention'
           " WHERE status = 'open' AND site_id = %s")
    args = [int(site_id)]

    if int(profile_id or 0):
        sql += ' AND profile_id = %s'
        args.append(int(profile_id))

    if str(uid or '').strip():
        sql += ' AND uid = %s'
        args.append(str(uid).strip())

    sql += ' ORDER BY id'

    if int(limit or 0) > 0:
        sql += ' LIMIT %s'
        args.append(int(limit))

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))

        return [dict(row) for row in (await db.fetchall() or [])]


async def chat_intention_done(intention_id: int) -> bool:
    """Отмечает намерение исполненным.

    Args:
        intention_id: номер строки `chat_intention`.

    Returns:
        bool: `True` — отметка наша; `False` — строки нет либо её уже закрыли,
        и это не ошибка.

    ## ⚠⚠ Условный `UPDATE`, а не вопрос «ещё открыта?» отдельным запросом

    Исполнителей может быть несколько — круг и кнопка, два воркера, — и между
    вопросом и записью они разошлись бы: оба увидели бы открытую строку и оба
    сделали бы дело. Условие стоит **внутри** записи, а `rowcount` отвечает,
    чья это отметка. Тем же приёмом живёт `invite_send`.

    ⚠ Отметка **отпускает ключ**: `open_intention` гаснет в `NULL`, и следующее
    такое же обещание той же паре ложится своей строкой.
    """
    if int(intention_id or 0) <= 0:
        return False

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_intention SET status = 'done', done_at = NOW()"
            " WHERE id = %s AND status = 'open'",
            (int(intention_id),))
        done = int(db.rowcount or 0)
        await db.connection.commit()

    return bool(done)
