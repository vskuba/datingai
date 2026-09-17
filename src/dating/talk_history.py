"""История переписки для агента: диалог пары строками, старое сверху.

Агент чата отвечает на реплику собеседника — и до этого модуля видел **только
её одну**. Ни своего инвайта, которым разговор начался, ни того, что человек
отвечал вчера: каждый ответ рождался с чистого листа.

## ⚠⚠ Почему истории не было

Шаг `session_messages` у соседа умеет отдавать историю, но берёт её по
`session_uuid`, а мы его не передавали: сосед на каждый вызов заводил новую
сессию, и история в ней была пуста. В его базе у чат-агента лежало три сессии за
всё время — следы ручных проверок из админки, не работа.

**Сессию у него держать и не нужно.** Запрос к LLM не помнит прошлого сам, а
история у нас уже есть — в `message`, вместе с перенесёнными инвайтами
(`chat._invites_to_history`). Переменной она уезжает туда, где нужна, и остаётся
там, где ей место: в нашей базе, под нашими правилами чистки.

## Что видит агент

    [16.09 21:04] она: Спорим, ты сейчас улыбнулся?
    [17.09 08:12] он: Улыбнулся. Как тебя зовут?

⚠ **Время в строке обязательно.** Без него агент не отличит «ответил через
минуту» от «ответил через неделю», а это разные разговоры: во втором случае
продолжать с полуслова — значит выдать робота. Сутки и часы, без года и секунд:
год в переписке недельной давности не значит ничего, а секунды крадут место.

⚠ **«Она» и «он», а не имена.** Имя девушки у агента и так есть переменной, а
имя собеседника приходит не всегда (у трети людей в ленте оно нелатинское или
пустое). Роль же не пустеет никогда, и агенту нужна именно она — кто говорит.

⚠ **Старое сверху.** Так читают диалог глаза и так его понимает модель: в обратном
порядке она отвечает на самую верхнюю строку, то есть на последнюю по времени.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Сколько реплик отдавать агенту, если у сайта не настроено своё.
#
# ⚠ **Двадцать — не «побольше для верности».** Длинная история стоит токенов на
# каждом ответе, а чат идёт репликами по десять слов: двадцать строк — это уже
# полноценный разговор. Больше тянет старое, к которому никто не возвращался.
TALK_HISTORY_MAX_DEFAULT = 20

# Настройка сайта. Ключ — в `SITE_KNOWN`.
TALK_HISTORY_MAX_SETTING = 'chat_history_max'

# Сколько знаков оставляем от реплики. Длинные письма в чат не пишут, а обрезок
# в тысячу знаков защищает промпт от одной случайной простыни.
TALK_HISTORY_TEXT_MAX = 1000


async def talk_history_text(profile_id: int, uid: str, limit: int = 0) -> str:
    """Диалог пары строками для промпта. Пусто — истории нет, и это законно.

    Args:
        profile_id: девушка.
        uid: собеседник на сайте.
        limit: сколько последних реплик взять. Ноль — спросить настройку сайта.

    Returns:
        str: строки «[дд.мм чч:мм] она|он: текст», старое сверху. Пустая строка,
        если переписки нет вовсе — первый разговор без инвайта.

    ⚠ **Берём последние, а показываем по возрастанию.** `ORDER BY ... DESC LIMIT`
    отбирает хвост разговора, а `reversed` возвращает ему естественный порядок:
    без первого мы взяли бы начало переписки вместо конца, без второго — модель
    отвечала бы на верхнюю строку, то есть на самую свежую.

    ⚠ **Не бросает.** Автоответ без истории хуже, чем с историей, но лучше, чем
    молчание: сбой чтения отдаёт пустую строку, и агент отвечает как прежде — по
    одной реплике.
    """
    rows = await talk_history_rows(profile_id, uid, limit)

    return '\n'.join(talk_history_line(row) for row in rows)


async def talk_history_rows(profile_id: int, uid: str, limit: int = 0) -> list[dict]:
    """Те же реплики, но строками базы — для страниц и проверок.

    Отдельно от текста, потому что вопросы разные: промпту нужна одна строка,
    а тому, кто считает или показывает, — поля.
    """
    if not str(uid or ''):
        return []

    limit = int(limit or 0) or await talk_history_max(int(profile_id))

    try:
        async with mysql_get_db_async() as db:
            # ⚠ Порядок по времени, а не по `id`: перенесённые инвайты получают
            # время **отправки**, то есть ложатся в таблицу позже, а в разговоре
            # стоят раньше. Тот же довод, что у `send._said_already`.
            await db.execute(
                'SELECT direction, text, created_at FROM message'
                '  WHERE profile_id = %s AND sender_uid = %s'
                '  ORDER BY created_at DESC, id DESC LIMIT %s',
                (int(profile_id), str(uid), max(1, int(limit))))
            rows = await db.fetchall() or []
    except Exception as e:
        logger_info(f'[dating] история пары #{profile_id}/{uid} не спросилась: {e}')

        return []

    return [dict(row) for row in reversed(rows)]


def talk_history_line(row: dict) -> str:
    """Одна реплика строкой промпта: время, кто сказал, что сказал."""
    when = row.get('created_at')
    stamp = f'[{when:%d.%m %H:%M}] ' if when else ''
    who = 'она' if str(row.get('direction')) == 'out' else 'он'
    text = ' '.join(str(row.get('text') or '').split())[:TALK_HISTORY_TEXT_MAX]

    return f'{stamp}{who}: {text}'


async def talk_history_max(site_or_profile: int, by_profile: bool = True) -> int:
    """Сколько реплик отдавать агенту — настройка сайта этой анкеты.

    Args:
        site_or_profile: номер анкеты (по умолчанию) либо сайта.
        by_profile: `True` — номер анкеты, и сайт найдём по ней.

    ⚠ Сбой чтения даёт умолчание, а не ноль: история из двадцати реплик лучше
    отсутствия истории, а «ноль» означал бы тихую потерю всего разговора.
    """
    from src.uvicorn.routers.dating.site_ import site_seconds

    try:
        site_id = int(site_or_profile)
        if by_profile:
            async with mysql_get_db_async() as db:
                await db.execute('SELECT site_id FROM profile WHERE id = %s',
                                 (int(site_or_profile),))
                row = await db.fetchone()

            site_id = int((row or {}).get('site_id') or 0)

        if not site_id:
            return TALK_HISTORY_MAX_DEFAULT

        return int(await site_seconds(site_id, TALK_HISTORY_MAX_SETTING)
                   or TALK_HISTORY_MAX_DEFAULT)
    except Exception as e:
        logger_info(f'[dating] длина истории не спросилась: {e}')

        return TALK_HISTORY_MAX_DEFAULT
