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

⚠ Отдельной «двери для исполнителя» здесь **нет и заводить её не надо**: открытые
намерения он возьмёт тем же `chat_intention_rows(site_id, status='open')`, что и
вкладка. Такая дверь была написана заранее и три дня простояла без единого
вызывающего, повторяя чужой отбор, — то есть ровно тем, что правила зовут
размазанным знанием.

## ⚠ Пара — это «сайт и человек»

Номер собеседника уникален в пределах сайта, а не вообще, и ключ уникальности —
`(site_id, uid, profile_id, open_intention)`. Анкета в него входит: обещание
даёт она, и обещание другой девушки тому же человеку — своё.
"""

import json

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load

# Предел длины метки, знаков — ровно тот, что у соседа
# (`CHAT_INTENTION_LENGTH_MAX` в его `chat_intention.py`): длиннее он не
# принимает, значит приехать не может.
#
# ⚠ Проверка стоит здесь, а не в двери к соседу: столбец принадлежит этому
# модулю. Полагаться на чужой предел нельзя — он правится у соседа.
CHAT_INTENTION_LENGTH_MAX = 100

# Предел заметки об исполнении, знаков — длина столбца `done_note`.
#
# ⚠⚠ Режем **мы**, а не база. MySQL в строгом режиме отвергает длинную вставку
# ошибкой, и закрытие намерения сорвалось бы из-за того, что исполнитель написал
# в заметке лишнего. Дело уже сделано — терять отметку из-за текста нельзя.
CHAT_INTENTION_NOTE_MAX = 500

# Строк на странице по умолчанию. Столько же, сколько у чёрного списка: списки
# соседние по виду, и разная высота читалась бы как разная важность.
CHAT_INTENTION_PAGE = 50


async def chat_intention_put(profile_id: int, site_id: int, uid: str,
                             intentions: dict) -> int:
    """Заводит открытые намерения пары. Отдаёт, сколько новых легло.

    Args:
        profile_id: чьё намерение — анкета, от чьего имени шёл разговор.
        site_id: сайт собеседника.
        uid: номер собеседника на сайте.
        intentions: намерения объектом «метка → контекст», как приехали от
            соседа. Контекст — с чем исполнять: текст письма, приметы снимка,
            время звонка; пустой законен.

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

    ## ⚠⚠ Контекст у открытой строки обновляется, а метка — нет

    Повтор метки ключ отвергает (`INSERT IGNORE`), но контекст при этом **не
    теряется**: свежий точнее прежнего — он собран по более полной истории
    разговора. Отсюда `ON DUPLICATE KEY UPDATE` на одну колонку: ключ остаётся
    защитой от дублей строк, а контекст догоняет.

    ⚠ Пустой контекст прежний **не затирает**: прогон, который пометил
    намерение, но не собрал контекст, не должен обнулять работу прошлого.
    """
    said = _pairs(intentions)

    if not (int(profile_id or 0) and str(uid or '').strip() and said):
        return 0

    put = 0

    async with mysql_get_db_async() as db:
        for one, context in said:
            await db.execute(
                'INSERT INTO chat_intention'
                '  (profile_id, site_id, uid, intention, context)'
                ' VALUES (%s, %s, %s, %s, %s)'
                # ⚠ `IF(VALUES(context) IS NULL, context, VALUES(context))`, а не
                # просто присваивание: пустой контекст свежего прогона не должен
                # стирать собранный прошлым.
                ' ON DUPLICATE KEY UPDATE'
                '   context = IF(VALUES(context) IS NULL, context, VALUES(context))',
                (int(profile_id), int(site_id or 0), str(uid).strip(), one,
                 json.dumps(context, ensure_ascii=False) if context else None))
            # ⚠ `rowcount` у `ON DUPLICATE KEY UPDATE` равен 2 при обновлении и
            # 1 при вставке: считаем только новые строки.
            put += int(db.rowcount == 1)

        await db.connection.commit()

    if put:
        logger_info(f'[dating] намерений записано: {put} '
                    f'(анкета {int(profile_id)}, собеседник {str(uid).strip()})')

    return put


async def chat_intention_done(intention_id: int, note: str = '') -> bool:
    """Отмечает намерение исполненным.

    Args:
        intention_id: номер строки `chat_intention`.
        note: чем исполнили, словами — «отправлено фото public/a.png». Пусто
            законно: исполнитель может не иметь что сказать.

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

    ⚠ Заметка не нашей отметки никуда не пишется: условие одно на все столбцы, и
    второй исполнитель, опоздавший на миг, не затирает рассказ первого.
    """
    if int(intention_id or 0) <= 0:
        return False

    said = str(note or '').strip()[:CHAT_INTENTION_NOTE_MAX]

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_intention"
            "   SET status = 'done', done_at = NOW(), done_note = %s"
            " WHERE id = %s AND status = 'open'",
            (said, int(intention_id)))
        done = int(db.rowcount or 0)
        await db.connection.commit()

    return bool(done)


async def chat_intention_rows(site_id: int, profile_id: int = 0, uid: str = '',
                              status: str = '', page: int = 1,
                              per: int = 0) -> dict:
    """Намерения страницей, с именами обеих сторон.

    Единственный способ прочитать намерения: им пользуются обе колонки вкладки
    («что сделано» и «что в пуле») и им же возьмёт работу будущий исполнитель —
    `status='open'`.

    Args:
        site_id: сайт. Ноль — пусто, а не намерения всех сайтов.
        profile_id: сузить до анкеты. Ноль — все анкеты сайта.
        uid: сузить до собеседника. Пусто — все.
        status: `open` или `done`. Пусто — и те, и другие.
        page: страница, с единицы. За концом — поправится сама.
        per: строк на странице. Ноль — `CHAT_INTENTION_PAGE`.

    Returns:
        dict: `rows`, `page`, `pages`, `total`.

    ⚠ **Сайт обязателен.** Круги панели обходят сайты поодиночке (`round_sites`),
    и отбор «по всем сразу» не нужен ни одному вызывающему. Ноль молча отдаёт
    пусто, а не всю таблицу: вторая форма однажды уехала бы в круг и унесла
    чужие строки.

    ⚠ **Страницы считает сервер.** Список копится сам и не убывает — намерение не
    истекает. Отданный целиком, он кончается молча, и конец прокрутки читается как
    конец списка.

    ⚠ Порядок зависит от отбора: исполненные — свежие сверху (человек смотрит,
    что сделано последним), открытые — старые сверху (старое обещание исполняют
    раньше нового).
    """
    site_id = int(site_id or 0)

    if not site_id:
        return _empty()

    pid, who = int(profile_id or 0), str(uid or '').strip()
    what = str(status or '').strip()
    where = (_WHERE_SITE + "   AND (%s = '' OR i.uid = %s)"
                           "   AND (%s = '' OR i.status = %s)")
    args = (site_id, pid, pid, who, who, what, what)

    order = (' ORDER BY i.done_at DESC, i.id DESC' if what == 'done'
             else ' ORDER BY i.id')
    per = max(1, int(per or 0) or CHAT_INTENTION_PAGE)

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM chat_intention i' + where,
                             args)
            total = int(((await db.fetchone()) or {}).get('n') or 0)
            page, pages = _page_clamp(total, page, per)

            await db.execute(
                'SELECT i.id, i.profile_id, i.site_id, i.uid, i.intention,'
                '       i.status, i.created_at, i.done_at, i.done_note,'
                '       comp.name, comp.photo,'
                '       p.profile AS profile_card, p.login AS profile_login'
                + _SOURCE + where + order + ' LIMIT %s OFFSET %s',
                (*args, per, (page - 1) * per))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] намерения сайта #{site_id} не прочитались: {e}')

        return _empty()

    return {'rows': [_intention_row(row) for row in (rows or [])],
            'page': page, 'pages': pages, 'total': total}


async def chat_intention_pairs(site_id: int, profile_id: int = 0, page: int = 1,
                               per: int = 0) -> dict:
    """Пары, у которых есть намерения: по строке на пару, свежие сверху.

    Args:
        site_id: сайт. Ноль — пусто.
        profile_id: сузить до анкеты. Ноль — все анкеты сайта.
        page: страница, с единицы. За концом — поправится сама.
        per: строк на странице. Ноль — `CHAT_INTENTION_PAGE`.

    Returns:
        dict: `rows`, `page`, `pages`, `total`. В строке — обе стороны,
        `open_count`, `done_count` и `last_at` (время последнего намерения).

    ⚠ **Пары без намерений сюда не попадают по построению**: группировка идёт по
    самой таблице намерений. Вкладка отвечает на вопрос «что агент наобещал», и
    пары, где он не обещал ничего, — шум; их на живом сайте сотни.

    ⚠⚠ Счётчики приводятся к `int` **здесь**: `SUM()` в MySQL отдаёт `Decimal`,
    и ручка упала бы не на запросе, а на сериализации ответа в JSON — то есть
    жаловалась бы не на то.
    """
    site_id = int(site_id or 0)

    if not site_id:
        return _empty()

    pid = int(profile_id or 0)
    args = (site_id, pid, pid)
    per = max(1, int(per or 0) or CHAT_INTENTION_PAGE)

    try:
        async with mysql_get_db_async() as db:
            # ⚠ Счёт **пар**, а не строк: `COUNT(DISTINCT …)` по двум столбцам
            # считает именно их, и подзапрос ради этого не нужен.
            await db.execute(
                'SELECT COUNT(DISTINCT i.profile_id, i.uid) AS n'
                '  FROM chat_intention i' + _WHERE_SITE, args)
            total = int(((await db.fetchone()) or {}).get('n') or 0)
            page, pages = _page_clamp(total, page, per)

            await db.execute(
                'SELECT i.profile_id, i.site_id, i.uid,'
                "       SUM(i.status = 'open') AS open_count,"
                "       SUM(i.status = 'done') AS done_count,"
                '       MAX(i.created_at) AS last_at,'
                '       comp.name, comp.photo,'
                '       p.profile AS profile_card, p.login AS profile_login'
                + _SOURCE + _WHERE_SITE +
                ' GROUP BY i.profile_id, i.site_id, i.uid,'
                '          comp.name, comp.photo, p.profile, p.login'
                ' ORDER BY last_at DESC, i.profile_id, i.uid'
                ' LIMIT %s OFFSET %s',
                (*args, per, (page - 1) * per))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] пары намерений сайта #{site_id} не прочитались: {e}')

        return _empty()

    out = []
    for row in rows or []:
        got = _intention_row(row)
        got['open_count'] = int(got.get('open_count') or 0)
        got['done_count'] = int(got.get('done_count') or 0)
        got['last_at'] = str(got['last_at']) if got.get('last_at') else ''
        out.append(got)

    return {'rows': out, 'page': page, 'pages': pages, 'total': total}


async def chat_intention_do_allowed(site_id: int) -> bool:
    """Разрешено ли панели исполнять намерения этого сайта.

    Args:
        site_id: сайт-закладка.

    Returns:
        bool: `True` — человек поднял галочку «выполнять действия»; `False` —
        снята, строки сайта нет, или прочитать не вышло.

    ## ⚠⚠ Спрашивают это ПЕРЕД работой, а не вместо работы

    Галочка не трогает набор пула: намерения пишет прогон агента, и запрет
    исполнять не означает запрет замечать. Снятая галочка откладывает работу, а
    не стирает историю обещаний.

    ## ⚠⚠ Сбой чтения — это `False`

    Исполнение уходит живому человеку, и «на всякий случай сделал» отменить
    нельзя. Не смогли узнать — не делаем. Тем же правилом живёт `chat_answer_on`
    в `auto.py`; у настроек-сроков правило обратное (умолчание), и путать их
    дорого: там цена ошибки — лишняя пауза, здесь — необратимое действие.

    ⚠ Исполнителя пока нет вовсе: функция заведена заранее, вместе с галочкой.
    """
    if int(site_id or 0) <= 0:
        return False

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT chat_intention_do_on FROM site WHERE site_id = %s',
                (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] разрешение исполнять намерения сайта #{int(site_id)} '
                    f'не прочиталось: {e}')

        return False

    return bool((row or {}).get('chat_intention_do_on'))


# ── детали реализации ──────────────────────────────────────────────────────

# Источник строк: сама таблица плюс обе стороны пары. ⚠ Один текст на оба
# читателя: разойдись их `JOIN`, список намерений и колонка пар показывали бы
# разные имена для одной и той же строки.
_SOURCE = ('  FROM chat_intention i'
           '  JOIN profile p ON p.id = i.profile_id'
           '  LEFT JOIN companion comp'
           '    ON comp.site_id = i.site_id AND comp.uid = i.uid')

# Начало отбора, общее обоим читателям: сайт обязателен, анкета — фильтр.
# ⚠ Ноль в `profile_id` значит «все анкеты сайта», и выражено это **в запросе**,
# а не ветвлением на стороне Python: одна форма строки на оба случая.
_WHERE_SITE = ' WHERE i.site_id = %s AND (%s = 0 OR i.profile_id = %s)'


def _pairs(intentions) -> list[tuple]:
    """Намерения в пары «метка, контекст», отбросив негодные.

    ⚠ Предел длины метки проверяется здесь, а не у соседа: он принадлежит
    **столбцу**, а длинная вставка в строгом режиме MySQL сорвала бы запись
    всей пачки — вместе с годными метками рядом.

    ⚠ Контекст не той формы становится пустым, а намерение остаётся: метка
    важнее своего контекста, и терять её из-за испорченной половины нельзя.
    """
    if not isinstance(intentions, dict):
        return []

    out = []
    for name, context in intentions.items():
        word = str(name or '').strip()
        if word and len(word) <= CHAT_INTENTION_LENGTH_MAX:
            out.append((word, context if isinstance(context, dict) and context else None))

    return out


def _empty() -> dict:
    """Ответ «читать нечего» в той же форме, что и удачный.

    ⚠ Функцией, а не константой: словарь изменяем, и вызывающий, дописавший в
    него поле, испортил бы ответ всем следующим.
    """
    return {'rows': [], 'page': 1, 'pages': 1, 'total': 0}


def _page_clamp(total: int, page: int, per: int) -> tuple:
    """Номер страницы и сколько их всего.

    ⚠ Страница за концом **правится, а не отвергается**: строку могли закрыть с
    последней страницы, пока человек на неё смотрел, и отказ вместо предыдущей
    читался бы как поломка. То же правило, что у журнала доставки.
    """
    pages = max(1, -(-int(total) // int(per)))

    return min(max(int(page or 1), 1), pages), pages


def _intention_row(row: dict) -> dict:
    """Строка базы → строка для страницы: имена, лица, даты строками.

    ## ⚠ Имя анкеты выводится здесь, а не выражением в SQL

    В списках панели так заведено везде (`block_rows`, `mail_reply`, `mailing`,
    `chat.py`): запрос берёт `p.profile` целиком, а имя, лицо и номер на сайте
    достаёт Python. Выражение `JSON_EXTRACT` в запросе стояло бы пятой копией, и
    ради него пришлось бы ещё и присоединять карточку второй раз.

    ⚠ Собеседник без карточки в `companion` отдаётся **номером**, а не пустотой:
    карточка заводится по требованию, и по пустому месту человека не узнать.
    """
    got = dict(row)
    card = json_column_load(got.pop('profile_card', None)) or {}

    got['profile_name'] = str(card.get('name')
                              or got.pop('profile_login', '') or '')
    got.pop('profile_login', None)
    got['profile_photo'] = str(card.get('photo') or '')
    got['profile_uid'] = str(card.get('id') or '')

    got['name'] = str(got.get('name') or '') or str(got.get('uid') or '')
    got['photo'] = str(got.get('photo') or '')
    got['done_note'] = str(got.get('done_note') or '')

    for key in ('created_at', 'done_at'):
        got[key] = str(got[key]) if got.get(key) else ''

    return got
