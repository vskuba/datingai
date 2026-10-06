"""Что из галереи уже ушло собеседнику: память об отправленных снимках.

## Зачем

Снимок, посланный человеку дважды, выдаёт агентство вернее любого текста: живая
девушка помнит, что показывала. Помнить за неё должен код — отправок бывают
сотни, и держать это в голове оператора нельзя.

## ⚠⚠ Отправки фото ещё нет, и дверь заведена раньше неё

Галерея сегодня — картотека: обход Диска, индекс, описание моделью, показ в
панели. Отправить снимок в чат пока нечем. Память заведена заранее намеренно:
она нужна в тот же миг, когда отправка появится, а заводить её потом значило бы
иметь первые сотни отправок без памяти — то есть ровно те дубли, ради которых
всё и затеяно.

## ⚠ Пара — это «сайт и человек», а не «анкета и человек»

Номер собеседника уникален в пределах сайта, и ключ уникальности таблицы —
`(site_id, uid, gallery_file_id)`. Анкета в ключ не входит: снимок принадлежит
её галерее, то есть отправить его может только она.

⚠ Вопрос «что **эта девушка** показывала этому человеку» всё равно задаётся
часто, поэтому `profile_id` в строке хранится — но для чтения, не для ключа.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.gallery import gallery_group_of


async def companion_gallery_sent(profile_id: int, site_id: int, uid: str,
                                 gallery_file_id: int) -> bool:
    """Отмечает, что снимок ушёл этому собеседнику.

    Args:
        profile_id: от чьего имени ушло.
        site_id: сайт собеседника.
        uid: номер собеседника на сайте.
        gallery_file_id: строка `gallery_file`, та самая, что ушла.

    Returns:
        bool: `True` — отметка легла; `False` — она уже была, и это не ошибка.

    ⚠⚠ **Повтор не ошибка, а обычный исход.** Отправку могли отметить дважды:
    круг и кнопка, две попытки подряд. Ключ базы такую вставку отвергает сам
    (`INSERT IGNORE`), и вызывающему достаточно знать, новая это отметка или нет.

    ⚠ Отметка ставится **после** удачной отправки, а не до: снимок, не ушедший
    из-за отказа сайта, не должен считаться показанным — иначе человек не увидит
    его никогда.
    """
    if not (int(profile_id or 0) and str(uid or '').strip()
            and int(gallery_file_id or 0)):
        return False

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO companion_gallery_file'
            '  (profile_id, site_id, uid, gallery_file_id)'
            ' VALUES (%s, %s, %s, %s)',
            (int(profile_id), int(site_id or 0), str(uid),
             int(gallery_file_id)))
        put = int(db.rowcount or 0)
        await db.connection.commit()

    return bool(put)


async def companion_gallery_fresh(profile_id: int, site_id: int, uid: str,
                                  limit: int = 0,
                                  group: str = '') -> list[dict]:
    """Снимки анкеты, которых этот собеседник ещё не видел.

    Args:
        profile_id: чья галерея.
        site_id: сайт собеседника.
        uid: номер собеседника на сайте.
        limit: сколько строк вернуть. Ноль — все.
        group: папка внутри галереи (`public`, `intim`); пусто — любая.

    Returns:
        list[dict]: строки `gallery_file` — номер, имя, путь, описание, теги.

    ## ⚠⚠ Пропавшие и видео не отдаются

    `is_gone` значит «файла на Диске не застали» — послать его нечем. Видео
    отсекается здесь же: оно живёт на Диске целиком, и отправка его в чат — не
    та работа, что отправка снимка.

    ⚠ Порядок — по номеру строки, то есть по времени появления в индексе:
    свежие снимки показываются последними. Жребий здесь был бы хуже — человек,
    глядя на переписку, не поймёт, почему выбрано именно это.
    """
    if not (int(profile_id or 0) and str(uid or '').strip()):
        return []

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT g.id, g.file_id, g.name, g.path, g.description, g.tags'
            '  FROM gallery_file g'
            ' WHERE g.profile_id = %s AND g.is_gone = 0 AND g.kind = %s'
            # ⚠ `NOT EXISTS`, а не `LEFT JOIN … IS NULL`: читается как вопрос,
            # на который отвечает, и не множит строки, если отметок вдруг две.
            '   AND NOT EXISTS (SELECT 1 FROM companion_gallery_file c'
            '                    WHERE c.gallery_file_id = g.id'
            '                      AND c.site_id = %s AND c.uid = %s)'
            ' ORDER BY g.id',
            (int(profile_id), 'image', int(site_id or 0), str(uid)))
        rows = [dict(row) for row in (await db.fetchall() or [])]

    # ⚠⚠ Группа отбирается **здесь, общим правилом** (`gallery_group_of`), а не
    # выражением в SQL. Имя группы — последняя папка над файлом, и повторить это
    # в запросе значило бы завести второе правило о том же:
    # `SUBSTRING_INDEX(path, '/', 1)` дало бы приставку `photo`, а не `public`,
    # и отбор молча расходился бы с тем, что человек видит на вкладке.
    if str(group or '').strip():
        want = str(group).strip()
        rows = [row for row in rows if gallery_group_of(row['path']) == want]

    # ⚠ Предел — после отбора по группе: примени его запрос, в окно попали бы
    # чужие группы, а своих не осталось бы вовсе.
    return rows[:int(limit)] if int(limit or 0) > 0 else rows


async def companion_gallery_seen(profile_id: int, uid: str) -> list[dict]:
    """Что эта девушка уже показывала этому человеку — свежее первым.

    Args:
        profile_id: чья галерея.
        uid: номер собеседника на сайте.

    Returns:
        list[dict]: строки с именем файла и временем отправки.

    ⚠ Для глаза человека: он открывает переписку и хочет знать, что уже видел
    собеседник. Отбором занят `companion_gallery_fresh`.
    """
    if not (int(profile_id or 0) and str(uid or '').strip()):
        return []

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT c.gallery_file_id, c.sent_at, g.file_id, g.name, g.path'
                '  FROM companion_gallery_file c'
                '  JOIN gallery_file g ON g.id = c.gallery_file_id'
                ' WHERE c.profile_id = %s AND c.uid = %s'
                ' ORDER BY c.sent_at DESC, c.id DESC',
                (int(profile_id), str(uid)))
            rows = await db.fetchall()

        return [dict(row) for row in (rows or [])]
    except Exception as e:
        logger_info(f'[dating] показанное этому человеку не прочиталось: {e}')

        return []
