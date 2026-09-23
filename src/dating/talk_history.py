"""История общения с человеком для агента: чат и письма, каждый своим списком.

Агент чата отвечает на реплику собеседника — и до этого модуля видел **только
её одну**. Ни своего инвайта, которым разговор начался, ни того, что человек
отвечал вчера: каждый ответ рождался с чистого листа.

## ⚠⚠ Каналов два, и они не сливаются в один список

С собеседником говорят **двумя** способами: репликами в чате и письмами. Это
разные разговоры — разной длины, разного тона и часто разных дней, — и агенту
они уезжают **двумя переменными**: `talk_history_chat` и `talk_history_mail`.

Слить их в одну ленту было бы неверно дважды. Во-первых, предел общий: чат идёт
десятками коротких реплик, письма — единицами длинных, и «последние двадцать»
вытолкнули бы письма целиком. Во-вторых, register: ответ на письмо пишут не так,
как реплику в мессенджер, и агент должен видеть, что на что похоже.

⚠ **Обе истории отдаются всегда** — и когда отвечаем в чат, и когда пишем
письмо. Человек один, отношения одни; знать половину их значит писать письмо,
не помня вчерашнего разговора в чате.

## ⚠ Почему один код на оба канала

Занятие у них буквально одно: «взять последние N обменов пары, отдать строками
промпта». Разного — только откуда брать и сколько. Поэтому механизм здесь
**один**, а различия лежат таблицей `TALK_HISTORY_CHANNELS`: запрос, настройка
предела, умолчание, обрезок текста. Две копии функции разошлись бы на первой же
правке формата — так уже было с `mail_reply._history`, который знал про письма
своё и не знал про избранных вовсе.

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

⚠⚠ **В письмах считается только ушедшее.** Черновик из пула человек не читал, и
показывать его как сказанное значило бы отвечать на разговор, которого не было:
агент решил бы, что уже ответил, и написал бы продолжение вместо ответа.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Имена каналов. Они же — хвосты переменных агента (`talk_history_chat`,
# `talk_history_mail`), и это не совпадение: одно имя на канал во всех местах.
TALK_HISTORY_CHAT = 'chat'
TALK_HISTORY_MAIL = 'mail'

# Чат: реплики пары из `message`, вместе с перенесёнными инвайтами
# (`chat.chat_invites_to_history`).
#
# ⚠ Порядок по времени, а не по `id`: перенесённые инвайты получают время
# **отправки**, то есть ложатся в таблицу позже, а в разговоре стоят раньше. Тот
# же довод, что у `send._said_already`.
_TALK_HISTORY_SQL_CHAT = (
    'SELECT direction, text, created_at FROM message'
    '  WHERE profile_id = %s AND sender_uid = %s'
    '  ORDER BY created_at DESC, id DESC LIMIT %s')

# Письма: четыре источника, и четвёртый — письма избранным.
#
# ⚠ **`mailing` нужен потому, что разговор чаще всего начинали мы**: ушло письмо
# рассылки, он на него ответил. Без него агент видит переписку с середины — «Moto
# is my life» без вопроса, на который это ответ, — и пишет, будто человек
# заговорил сам собой.
#
# ⚠⚠ **`favorite_mail` здесь появился впервые.** `mail_reply._history` про него
# не знал вовсе, хотя письма избранным уходят каждый день: отвечая на входящее,
# агент не видел собственного письма, которым этот разговор и начался.
#
# ⚠ Время ушедшего письма — `sent_at`, а не `created_at`: между сочинением
# черновика и отправкой проходят часы, и по времени сочинения письмо встало бы в
# ленту раньше, чем человек его прочитал.
_TALK_HISTORY_SQL_MAIL = (
    "SELECT 'in' AS direction, body AS text, date_post AS created_at, id"
    '  FROM mail_in WHERE profile_id = %s AND uid = %s'
    ' UNION ALL'
    " SELECT 'out', body, COALESCE(sent_at, created_at), id"
    "  FROM mail_reply WHERE profile_id = %s AND uid = %s AND status = 'sent'"
    ' UNION ALL'
    " SELECT 'out', body, COALESCE(sent_at, created_at), id"
    "  FROM mailing WHERE profile_id = %s AND uid = %s AND status = 'sent'"
    ' UNION ALL'
    " SELECT 'out', body, COALESCE(sent_at, created_at), id"
    "  FROM favorite_mail WHERE profile_id = %s AND uid = %s AND status = 'sent'"
    ' ORDER BY created_at DESC, id DESC LIMIT %s')

# Всё, чем каналы отличаются, — одной таблицей. Дальше код общий.
#
# `pairs` — сколько раз запрос просит «анкета + собеседник»: у письма четыре
# источника, у чата один.
#
# ⚠ Предел чата — двадцать, и это не «побольше для верности»: длинная история
# стоит токенов на каждом ответе, а чат идёт репликами по десять слов. Предел
# писем меньше (их пишут единицами), а обрезок текста больше: письмо это три
# абзаца, и тысяча знаков отрезала бы у него конец.
#
# ⚠ Настройка писем — та же `reply_history`, которой жил `mail_reply`: заводить
# вторую значило бы спрашивать у человека дважды одно и то же.
# ⚠⚠ `cap` — **жёсткий потолок поверх настройки**, и он не украшение: в реестре
# сайта у обеих настроек стоит только `min: 1`, то есть человек вправе вписать
# 500. Промпт превратился бы в простыню, на которую модель отвечает первым, что
# увидит, — и платили бы мы за это на каждом вызове.
#
# У писем такой потолок был (`mail_reply.MAIL_REPLY_HISTORY_MAX`), у чата не было;
# сегодня оба сайта живут на умолчаниях, поэтому возврат ничего не меняет.
TALK_HISTORY_CHANNELS = {
    TALK_HISTORY_CHAT: {
        'sql': _TALK_HISTORY_SQL_CHAT,
        'pairs': 1,
        'setting': 'chat_history_max',
        'default': 20,
        'cap': 100,
        'text_max': 1000,
    },
    TALK_HISTORY_MAIL: {
        'sql': _TALK_HISTORY_SQL_MAIL,
        'pairs': 4,
        'setting': 'reply_history',
        'default': 10,
        'cap': 50,
        'text_max': 2000,
    },
}


async def talk_history_text(profile_id: int, uid: str,
                            channel: str = TALK_HISTORY_CHAT,
                            limit: int = 0) -> str:
    """Общение пары строками для промпта. Пусто — истории нет, и это законно.

    Args:
        profile_id: девушка.
        uid: собеседник на сайте.
        channel: `TALK_HISTORY_CHAT` или `TALK_HISTORY_MAIL`.
        limit: сколько последних обменов взять. Ноль — спросить настройку сайта.

    Returns:
        str: строки «[дд.мм чч:мм] она|он: текст», старое сверху. Пустая строка,
        если общения этим каналом не было — законный исход: у пары бывает чат без
        писем и письма без чата.
    """
    rows = await talk_history_rows(profile_id, uid, channel, limit)
    text_max = int(TALK_HISTORY_CHANNELS[channel]['text_max'])

    return '\n'.join(talk_history_line(row, text_max) for row in rows)


async def talk_history_rows(profile_id: int, uid: str,
                            channel: str = TALK_HISTORY_CHAT,
                            limit: int = 0) -> list[dict]:
    """Те же обмены, но строками базы — для страниц и проверок.

    Отдельно от текста, потому что вопросы разные: промпту нужна одна строка,
    а тому, кто считает или показывает, — поля.

    ⚠ **Берём последние, а показываем по возрастанию.** `ORDER BY ... DESC LIMIT`
    отбирает хвост разговора, а `reversed` возвращает ему естественный порядок:
    без первого мы взяли бы начало переписки вместо конца, без второго — модель
    отвечала бы на верхнюю строку, то есть на самую свежую.

    ⚠ **Не бросает.** Ответ без истории хуже, чем с историей, но лучше, чем
    молчание: сбой чтения отдаёт пустой список, и агент отвечает как прежде — по
    одной реплике.
    """
    if not str(uid or ''):
        return []

    known = TALK_HISTORY_CHANNELS.get(channel)
    if not known:
        logger_info(f'[dating] неизвестный канал истории: {channel}')

        return []

    limit = int(limit or 0) or await talk_history_max(int(profile_id), channel)
    args = [int(profile_id), str(uid)] * int(known['pairs'])
    args.append(max(1, int(limit)))

    try:
        async with mysql_get_db_async() as db:
            await db.execute(str(known['sql']), tuple(args))
            rows = await db.fetchall() or []
    except Exception as e:
        logger_info(f'[dating] история {channel} пары #{profile_id}/{uid}'
                    f' не спросилась: {e}')

        return []

    return [dict(row) for row in reversed(rows)]


def talk_history_line(row: dict, text_max: int = 0) -> str:
    """Один обмен строкой промпта: время, кто сказал, что сказал."""
    when = row.get('created_at')
    stamp = f'[{when:%d.%m %H:%M}] ' if when else ''
    who = 'она' if str(row.get('direction')) == 'out' else 'он'
    cut = int(text_max or TALK_HISTORY_CHANNELS[TALK_HISTORY_CHAT]['text_max'])
    text = ' '.join(str(row.get('text') or '').split())[:cut]

    return f'{stamp}{who}: {text}'


async def talk_history_max(site_or_profile: int,
                           channel: str = TALK_HISTORY_CHAT,
                           by_profile: bool = True) -> int:
    """Сколько обменов отдавать агенту — настройка сайта этой анкеты.

    Args:
        site_or_profile: номер анкеты (по умолчанию) либо сайта.
        channel: у каждого канала своя настройка и своё умолчание.
        by_profile: `True` — номер анкеты, и сайт найдём по ней.

    ⚠ Сбой чтения даёт умолчание, а не ноль: история из двадцати реплик лучше
    отсутствия истории, а «ноль» означал бы тихую потерю всего разговора.
    """
    from src.uvicorn.routers.dating.site_ import site_seconds

    known = TALK_HISTORY_CHANNELS.get(channel) \
        or TALK_HISTORY_CHANNELS[TALK_HISTORY_CHAT]
    fallback = int(known['default'])

    try:
        site_id = int(site_or_profile)
        if by_profile:
            async with mysql_get_db_async() as db:
                await db.execute('SELECT site_id FROM profile WHERE id = %s',
                                 (int(site_or_profile),))
                row = await db.fetchone()

            site_id = int((row or {}).get('site_id') or 0)

        if not site_id:
            return fallback

        want = int(await site_seconds(site_id, str(known['setting'])) or fallback)

        return min(want, int(known['cap']))
    except Exception as e:
        logger_info(f'[dating] длина истории {channel} не спросилась: {e}')

        return fallback
