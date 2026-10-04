"""Общие куски ручек пулов: убрать строку, очистить пул, взять отправителя.

Пулов пять — рассылка (`mailing`), инвайты (`invite`), письма избранным
(`favorite_mail`), инициатива в чате (`chat_initiative`) и ответы на письма
(`mail_reply`), — и три вопроса у них общие до строчки: «убери вот эту»,
«очисти весь пул сайта», «от какой анкеты отправляем».

## ⚠⚠ Что это стоило, пока лежало пятью копиями

Ответы разъехались. На одно и то же действие — убрать одну строку — ручки
отдавали `{'dropped': 1}`, `{'deleted': 1}` и `{'id': 42}`: три формы, которые
страница должна была бы помнить поимённо. Не помнила: ответ `drop` **никто не
читает**, и потому расхождение не всплывало — оно просто ждало того, кто
однажды решит его прочесть.

У очистки разъехались и тексты отказа: «не назван сайт — очищать нечего» у
троих, «сайт не выбран» у четвёртого. Человек видит разные слова в одинаковой
ситуации и ищет между ними разницу, которой нет.

Теперь форма одна: `{'dropped': N}` — столько строк убрано. Слова, которыми
ручка объясняет отказ, остаются своими: «инвайт уже ушёл» и «письмо уже ушло» —
разные предложения, и склеить их в «строка уже ушла» значило бы заговорить с
человеком языком схемы.

## ⚠ Почему здесь, а не в домене

Эти функции отвечают `HTTPException` — то есть их контракт **код ответа HTTP**,
и место им в слое дверей. То же правило и тот же довод, что у `_number_check`,
оставшегося в `site_.py`, когда реестр настроек уезжал в домен.

Доменная половина лежит отдельно и отвечает на другие вопросы: `pool.py` —
«чьи черновики стереть, когда анкета ушла из сети», `pool_pair.py` — «кто кому
пишет» в запросах.
"""

from fastapi import HTTPException, status

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Таблицы, из которых человек вправе убрать строку руками.
#
# ⚠ Имя таблицы уезжает в запрос **текстом** — параметром его не передать, —
# поэтому список закрытый. Проверка стоит даже при том, что имена приходят из
# кода ручек, а не снаружи: она дешевле разбирательства, если однажды придут
# не оттуда.
#
# ⚠⚠ Это **не** `pool.POOL_TABLES`, и совпадение четырёх имён из пяти
# обманчиво. Тот список отвечает на вопрос «чьи черновики стереть, когда анкета
# ушла из сети», и `mail_reply` в нём нет намеренно: ответ ждёт живого человека,
# написавшего первым. Здесь вопрос другой — «что человек может убрать
# кнопкой», — и ответ убрать он вправе: «не такой» значит «напиши заново».
POOL_HANDLE_TABLES = ('mailing', 'favorite_mail', 'invite', 'chat_initiative',
                      'mail_reply')


async def pool_handle_drop(table: str, row_id: int, *, missing: str,
                           sent: str) -> dict:
    """Убирает одну строку пула — ту, на крестик которой нажали.

    Args:
        table: таблица пула, из `POOL_HANDLE_TABLES`.
        row_id: номер строки.
        missing: что сказать, если строки нет, — словами этого пула
            («инвайта нет», «письма #42 нет»).
        sent: что сказать, если она уже ушла («инвайт уже ушёл — его не
            убрать»).

    Returns:
        dict: готовое тело ручки, `{'result': {'dropped': 1}}`.

    Raises:
        HTTPException: 404 — строки нет; 409 — она уже ушла.

    ⚠ **Спрашиваем только `status`, а не всю строку.** Пулы для показа тянут её
    джойнами к контакту, карточке собеседника и анкете — здесь из всего этого
    нужно одно поле, и платить за остальное незачем.

    ⚠ **Ушедшее не удаляется, и это не предосторожность.** Оно уже случилось:
    человек его прочитал. По этим же строкам считаются пределы частоты и
    правило «одного человека зовём один раз» — сотри их, и вернувшаяся анкета
    напишет тому же человеку второй раз подряд.
    """
    assert table in POOL_HANDLE_TABLES, f'не таблица пула: {table!r}'

    async with mysql_get_db_async() as db:
        await db.execute(f'SELECT status FROM {table} WHERE id = %s',
                         (int(row_id),))
        row = await db.fetchone()

        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=missing)

        if str(row['status']) != 'draft':
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail=sent)

        await db.execute(f'DELETE FROM {table} WHERE id = %s', (int(row_id),))
        await db.connection.commit()

    return {'result': {'dropped': 1}}


async def pool_handle_clear(table: str, site_id: int, *, what: str,
                            profile_id: int = 0) -> dict:
    """Очищает пул сайта — разом всё, что ещё не ушло.

    Args:
        table: таблица пула, из `POOL_HANDLE_TABLES`.
        site_id: сайт. Ноль — отказ: очищать «вообще всё» никто не просил.
        what: чей пул — словом, для строки журнала.
        profile_id: сузить до одной анкеты. Ноль — весь сайт.

    Returns:
        dict: готовое тело ручки, `{'result': {'dropped': N}}`.

    Raises:
        HTTPException: 422 — сайт не назван.

    ⚠ **Ушедшее остаётся** — по тому же доводу, что и у `pool_handle_drop`.

    ⚠⚠ `db.rowcount`, а не `db.cursor.rowcount`: у обёртки курсора своего
    `cursor` нет, и второе роняло ручку `AttributeError`. Поймано живым
    нажатием — очистка отвечала 500 там, где всё удалилось.
    """
    assert table in POOL_HANDLE_TABLES, f'не таблица пула: {table!r}'

    if not int(site_id or 0):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не назван сайт — очищать нечего')

    async with mysql_get_db_async() as db:
        await db.execute(
            f'DELETE FROM {table}'
            f" WHERE site_id = %s AND status = 'draft'"
            f' AND (%s = 0 OR profile_id = %s)',
            (int(site_id), int(profile_id), int(profile_id)))
        dropped = int(db.rowcount or 0)
        await db.connection.commit()

    if dropped:
        logger_info(f'[dating] пул {what} сайта #{site_id} очищен: '
                    f'убрано {dropped}')

    return {'result': {'dropped': dropped}}


async def pool_handle_sender(profile_id: int, what: str) -> dict:
    """Анкета, от которой уходит письмо или реплика, — со всеми полями входа.

    Args:
        profile_id: номер анкеты.
        what: чего она отправитель («письма», «ответа»), — для слов отказа.

    Returns:
        dict: строка `profile` целиком.

    Raises:
        HTTPException: 404 — анкеты нет; 409 — она выключена.

    ⚠ **Нужны все поля входа**, а не одно имя: под этой анкетой заходят в API
    сайта, и для входа берутся `login`, `password` и `password_source`. Ровно на
    этом однажды падал набор пула (`KeyError: 'site_id'`), когда полей взяли
    меньше, чем нужно вызываемому.

    ⚠ Выключенную анкету не ведут вовсе (`dating.md`, §5.10), и отправка от
    неё — действие наружу, самое заметное из всех.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, password, password_source, enabled'
            ' FROM profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'анкета {what} удалена — отправлять не от кого')

    if not int(row.get('enabled') or 0):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='анкета выключена — от неё не отправляем')

    return dict(row)
