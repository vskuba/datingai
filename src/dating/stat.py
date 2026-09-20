"""Счётчик событий: история, не зависящая от чистки рабочих таблиц.

## ⚠⚠ Зачем он есть

Статистика считала «сколько инвайтов ушло» прямо по таблице `invite`. Пока та
хранила всё, это работало; как только у неё появилась чистка по сроку, счёт
обвалился — страница показывала 38 889 ушедших там, где за трое суток ушло 280
тысяч.

Правило общее, а не про инвайты. **Рабочая таблица отвечает на вопрос «что сейчас
в работе»** и вправе забывать старое — иначе она растёт без предела. **Статистика
отвечает на «сколько сделано»** и забывать не вправе. Считать второе по первой
значит привязать историю к сроку уборки, и первая же ротация стирает её молча.

## ⚠ Агрегат по суткам

Событий девяносто тысяч в сутки; строка на каждое дала бы таблицу, которую
пришлось бы чистить — и мы вернулись бы туда, откуда ушли.

## ⚠ Счётчик не роняет работу

Ни одна запись сюда не стоит того, чтобы из-за неё не ушёл инвайт. Все отказы
гасятся и уходят в лог: пропущенная единица в статистике — потеря, а несостоявшаяся
отправка — беда.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Виды событий. ⚠ Строками, а не числами: строку видно в базе глазами, и разбор
# «что это за kind = 7» не нужен. Список закрытый — чтобы опечатка не заводила
# третий вид того же события.
STAT_INVITE_SENT = 'invite_sent'
STAT_CHAT_OUT = 'chat_out'
STAT_CHAT_IN = 'chat_in'
STAT_MAIL_OUT = 'mail_out'
STAT_FAVORITE_MAIL_SENT = 'favorite_mail_sent'
STAT_INITIATIVE_SENT = 'initiative_sent'

STAT_KINDS = (STAT_INVITE_SENT, STAT_CHAT_OUT, STAT_CHAT_IN, STAT_MAIL_OUT,
              STAT_FAVORITE_MAIL_SENT, STAT_INITIATIVE_SENT)


async def stat_add(site_id, profile_id, kind: str, n: int = 1) -> None:
    """Прибавляет событие к счётчику суток.

    Args:
        site_id: сайт-закладка.
        profile_id: анкета. Ноль — событие сайта целиком.
        kind: вид из `STAT_KINDS`.
        n: сколько прибавить. Обычно единица; пачка отправки зовёт с числом.

    ⚠ **Сутки считает база** (`CURDATE()`), а не Python: время у них разное, и
    событие, записанное в полночь, разъехалось бы по двум строкам.

    ⚠ **Ничего не бросает.** Счётчик — побочная запись: из-за неё не должна
    срываться отправка, ради которой она ведётся.
    """
    if not int(site_id or 0) or str(kind) not in STAT_KINDS or int(n or 0) <= 0:
        return

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO stat_day (site_id, profile_id, kind, day, n)'
                ' VALUES (%s, %s, %s, CURDATE(), %s)'
                ' ON DUPLICATE KEY UPDATE n = n + VALUES(n)',
                # ⚠ Ноль, а не `None`: в ключе NULL не равен NULL, и строки
                # «по сайту целиком» не складывались бы, а плодились.
                (int(site_id), int(profile_id or 0), str(kind), int(n)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] счётчик «{kind}» не прибавился: {e}')


async def stat_sum(site_id: int, kind: str, profile_id: int = 0,
                   hours: int = 0) -> int:
    """Сколько событий за окно. Ноль часов — за всё время.

    ⚠ **Окно считается по суткам, а не по часам.** Счётчик хранит агрегат дня, и
    «за последние 6 часов» по нему не ответить — вернём сутки, в которые эти часы
    попадают. Для статистики этого довольно: там окна от суток и больше.
    """
    where = ''
    params = [int(site_id), str(kind)]

    if int(profile_id or 0):
        where += ' AND profile_id = %s'
        params.append(int(profile_id))

    if int(hours or 0) > 0:
        # ⚠ Округляем **вверх**: окно «24 часа» обязано захватить вчерашний день
        # целиком, иначе половина суток потеряется на границе.
        days = max(1, (int(hours) + 23) // 24)
        where += ' AND day > CURDATE() - INTERVAL %s DAY'
        params.append(days)

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COALESCE(SUM(n), 0) AS n FROM stat_day'
                ' WHERE site_id = %s AND kind = %s' + where, tuple(params))
            row = await db.fetchone()

        return int((row or {}).get('n') or 0)
    except Exception as e:
        logger_info(f'[dating] счётчик «{kind}» не прочитался: {e}')

        return 0


async def stat_reset(site_id: int, kind: str = '', profile_id: int = 0) -> int:
    """Обнуляет счётчик. Возвращает, сколько строк убрано.

    Зовётся кнопкой на вкладке статистики: человек решает, с какого момента
    считать заново — например, после смены настроек или начала нового захода.

    ⚠ **Удаляем строки, а не ставим нули.** Нулевая строка означала бы «в этот
    день ничего не было», и следующий отчёт за месяц честно показал бы её в ряду
    суток. Отсутствие строки означает «не считали», и это правда.
    """
    where = ''
    params = [int(site_id)]

    if str(kind or ''):
        where += ' AND kind = %s'
        params.append(str(kind))

    if int(profile_id or 0):
        where += ' AND profile_id = %s'
        params.append(int(profile_id))

    try:
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM stat_day WHERE site_id = %s' + where,
                             tuple(params))
            gone = int(db.rowcount or 0)
            await db.connection.commit()

        return gone
    except Exception as e:
        logger_info(f'[dating] счётчик не обнулился: {e}')

        return 0
