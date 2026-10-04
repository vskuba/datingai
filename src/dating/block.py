"""Чёрный список: кому эта анкета больше не пишет.

Сайт умеет отвечать «нет» **навсегда**, и до сих пор мы этого не слышали. Живой
ответ на инвайт:

    200 {"IsSuccess":false,"Reason":"ERROR_MESSAGE_USER_DENY",
         "Message":"User ignore messages from you"}

Человек закрылся от этой девушки. Для панели это был обычный отказ: реплика
оставалась в пуле с причиной, круг через сутки готовил следующую, и каждая стоила
вызова LLM. Столько же стоила бы рассылка — закрылся он от **девушки**, а не от
способа доставки.

## Что запрет останавливает и чего не трогает

| Занятие | Идёт ли к нему | Почему |
|---------|----------------|--------|
| инвайты | **нет** | зовём незнакомого, а он уже сказал «не пишите» |
| рассылка | **нет** | то же письмо от той же девушки, просто другим каналом |
| письма избранным | **нет** | «ты замолчал» тому, кто закрылся, — худшее из писем |
| **ответы на его письма** | **да** | он написал сам: закрылся он не сейчас, а раньше передумал |

⚠ **Ответы намеренно остаются.** Запрет отвечает на вопрос «писать ли **первыми**»,
а входящее письмо этот вопрос снимает: человек обратился к нам сам. Заодно это и
есть способ снять запрет жизнью, а не руками, — но снимает его всё-таки человек:
молчать в ответ на прямое обращение хуже, чем один раз написать тому, кто уже не
против.

## ⚠ Пара, а не человек

Он закрылся от **этой** девушки; для остальных наших анкет он незнакомец, которого
они не отвергали. Ровно так же устроено «с кем уже говорили» у инвайтов
(`invite._talked`) и по той же причине: разговор — свойство двоих.

## ⚠ Примета отказа — настройкой, а не константой

Коды отказа придумывает сайт. `ERROR_MESSAGE_USER_DENY` — код того движка, на
котором работают оба наших сайта, и он же стоит умолчанием; сайт на другом движке
скажет это иначе, и тогда примета правится на странице «Сайты», а не в коде.

Примет может быть несколько — через запятую: у одного и того же сайта чат и почта
вправе называть одно и то же разными словами.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.feed import feed_write
from src.dating.log import log_write
from src.dating.site_setting import site_setting

# Настройка сайта: по каким словам в ответе узнаём «он закрылся».
BLOCK_DENY_SETTING = 'block_deny_mark'

# Умолчание — код движка, на котором работают оба наших сайта. ⚠ Это умолчание, а
# не правило: другой движок скажет то же самое иначе, и примета правится настройкой.
BLOCK_DENY_DEFAULT = 'ERROR_MESSAGE_USER_DENY'

# ⚠⚠ **Вторая настройка — приметы, которые касаются ТОЛЬКО ПИСЕМ.**
#
#     403 "You do not match the criteria of age to send messages to this user
#          (ERROR_MESSAGE_AGE_CRITERIA)"
#
# Человек выставил, от женщин какого возраста готов получать **письма**. Пробовать
# снова бессмысленно — возраст девушки не изменится, — но к чату это отношения не
# имеет, и гасить им инвайты значило бы запрещать разговор, которого никто не
# запрещал.
#
# ⚠ **Разделение подтверждено журналом, а не выведено из смысла слов.** За всё
# время отказы ни разу не пересеклись по каналам: «по возрасту» приходил только из
# почты, «он закрылся» — только из чата.
BLOCK_DENY_MAIL_SETTING = 'block_deny_mail_mark'
BLOCK_DENY_MAIL_DEFAULT = 'ERROR_MESSAGE_AGE_CRITERIA'

# Чего касается запрет. ⚠ Значения те же, что в столбце `block.channel`.
BLOCK_CHANNEL_ALL = 'all'
BLOCK_CHANNEL_MAIL = 'mail'

# Пулы, из которых запрет выметает готовое. ⚠ `mail_reply` здесь нет: ответ
# на его же письмо уходит и после запрета (см. заголовок модуля).
BLOCK_POOLS = ('invite', 'mailing', 'favorite_mail')

# ⚠ Те же пулы без инвайтов: запрет, который касается только писем, чат не трогает.
BLOCK_POOLS_MAIL = ('mailing', 'favorite_mail')


async def block_deny(site_id: int, answer: str) -> str:
    """Стойкий ли это отказ и какого канала он касается.

    Args:
        site_id: сайт — у него и спрашиваем приметы.
        answer: что ответил сайт. Годится и тело JSON, и собранная причина
            словами: ищем вхождение, а не разбираем ответ.

    Returns:
        str: `'all'` — писать нельзя ни в чат, ни письмом; `'mail'` — нельзя
        только письмом; **пустая строка** — обычный отказ, пробовать можно.

    ⚠ **Пустая строка вместо `False`, и вызывающим это ничего не ломает:**
    `if not await block_deny(...)` читает её так же. Зато появился третий ответ —
    «нельзя, но не везде», которого у `bool` быть не могло.

    ⚠ **Вхождение подстроки, а не разбор JSON.** Один канал отдаёт нам тело ответа,
    другой — уже собранную фразу с кодом в скобках; разбирать пришлось бы оба, и
    каждый по-своему. Код отказа — слово, которого в обычном ответе нет.

    ⚠ **Сперва спрашиваем про «всё», потом про «только письма».** Совпади ответ с
    обеими приметами разом, шире тот запрет, что гасит оба канала: ошибиться в
    сторону молчания дешевле, чем писать тому, кто просил не писать.
    """
    answer = str(answer or '')
    if not answer:
        return ''

    for setting, fallback, channel in (
            (BLOCK_DENY_SETTING, BLOCK_DENY_DEFAULT, BLOCK_CHANNEL_ALL),
            (BLOCK_DENY_MAIL_SETTING, BLOCK_DENY_MAIL_DEFAULT,
             BLOCK_CHANNEL_MAIL)):
        marks = await site_setting(int(site_id or 0), setting, fallback)

        if any(one.strip() and one.strip() in answer
               for one in str(marks).split(',')):
            return channel

    return ''


async def block_add(site_id: int, profile_id: int, uid: str, name: str = '',
                           reason: str = '', source: str = 'chat',
                           channel: str = BLOCK_CHANNEL_ALL) -> bool:
    """Заносит пару в чёрный список и выметает всё готовое для неё из пулов.

    Args:
        site_id: сайт-закладка.
        profile_id: девушка, от которой закрылись.
        uid: номер собеседника на сайте.
        name: как его зовут — для списка, который читает человек.
        reason: что ответил сайт, целиком.
        source: откуда узнали — `chat`, `mail` или `hand`.
        channel: чего касается — `all` (оба канала) или `mail` (только письма).
            ⚠ Это **не то же, что `source`**: `source` говорит, где мы услышали
            отказ, а `channel` — на что он распространяется. «Он закрылся»,
            услышанное в почте, гасит и чат тоже.

    Returns:
        bool: `True` — запись новая. `False` — такая уже была либо база отказала.

    ⚠ **Черновики убираем сразу.** Оставь мы их, круг отправки честно попробовал бы
    каждый и получил тот же отказ — то есть запрет откладывал бы работу вместо того,
    чтобы её отменить. Ушедшее не трогаем: это история, и она правдива.

    ⚠ **Пишем и в журнал анкеты, и в ленту.** Запрет меняет работу молча, а человек
    завтра спросит, почему этой паре ничего не уходит; ответ должен лежать там, где
    он смотрит, — а не только в таблице, которую надо знать.
    """
    site_id, profile_id = int(site_id or 0), int(profile_id or 0)
    uid, who = str(uid or '').strip(), str(name or '').strip()
    if not site_id or not profile_id or not uid:
        return False

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT IGNORE INTO block'
                ' (site_id, profile_id, uid, name, reason, source, channel)'
                ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (site_id, profile_id, uid, who[:190], str(reason or '')[:500],
                 source if source in ('chat', 'mail', 'hand') else 'chat',
                 channel if channel in (BLOCK_CHANNEL_ALL, BLOCK_CHANNEL_MAIL)
                 else BLOCK_CHANNEL_ALL))
            new = int(db.rowcount or 0) > 0

            # ⚠ Выметаем черновики **только тех пулов, которых запрет
            # касается**. Отказ по возрасту — про письма: снеси он заодно готовый
            # инвайт, мы потеряли бы реплику, которую сайт принял бы.
            pools = (BLOCK_POOLS if channel == BLOCK_CHANNEL_ALL
                     else BLOCK_POOLS_MAIL)

            for table in pools:
                await db.execute(
                    f"DELETE FROM {table} WHERE profile_id = %s AND uid = %s"
                    "   AND status = 'draft'", (profile_id, uid))

            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] запрет писать #{profile_id} → {uid} не записался: {e}')

        return False

    if not new:
        return False

    # ⚠ Уровень `fail`, а не `warn`: `log.level` знает пять слов, и `warn`
    # среди них нет — MySQL обрезал бы его до пустого молча. У ленты словарь свой,
    # и там `warn` как раз есть; одинаковыми они не были и не должны быть.
    await log_write(profile_id, 'fail',
                           f'Больше не пишем: {who or uid} закрылся от этой анкеты')
    feed_write(site_id, 'warn', f'Закрылся от анкеты: {who or uid}', profile_id)
    logger_info(f'[dating] запрет писать: анкета #{profile_id} → {uid} ({reason[:120]})')

    return True


async def block_pairs(site_id: int, channel: str = '') -> set:
    """Пары «анкета — собеседник», которым на этом сайте писать нельзя.

    Args:
        site_id: сайт-закладка.
        channel: чей набор спрашиваем. `chat` — только запреты, которые касаются
            обоих каналов; пусто или `mail` — все, включая почтовые.

            ⚠⚠ **Чату нужен не весь список.** Отказ «вы не подходите ему по
            возрасту» — про письма: погаси он инвайты, мы запретили бы разговор,
            которого никто не запрещал. Подтверждено журналом: этот отказ ни разу
            не приходил из чата.

    Returns:
        set: `{(profile_id, uid)}`. Пусто — запретов нет.

    ⚠ **Одним запросом на сайт, а не по анкете.** Спрашивает это набор пула перед
    перебором, анкет у сайта десятки, и запрос на каждую превратил бы набор в
    десятки походов в базу до первой готовой строки. Тот же приём, что у `_talked`.

    ⚠ **Сбой базы — пустой набор, и это верная сторона ошибки здесь.** Не спросив
    запреты, мы рискуем написать закрывшемуся ещё раз — неприятно, но обратимо;
    остановить же всю работу сайта из-за недоступной таблицы куда хуже. У «кого уже
    звали» решено наоборот, и намеренно: там незнание ведёт к повтору **всем**, а
    здесь — к одному лишнему письму тем немногим, кто в списке.
    """
    site_id = int(site_id or 0)
    if not site_id:
        return set()

    try:
        async with mysql_get_db_async() as db:
            # ⚠ Чату отдаём только запреты «про оба канала»: почтовые его не
            # касаются (довод — у параметра `channel` выше).
            await db.execute('SELECT profile_id, uid FROM block'
                             ' WHERE site_id = %s'
                             + (" AND channel = 'all'"
                                if str(channel) == 'chat' else ''),
                             (site_id,))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] чёрный список сайта #{site_id} не спросился: {e}')

        return set()

    return {(int(row['profile_id']), str(row['uid'])) for row in rows or []}


# Сколько запретов на странице. ⚠ Столько же, сколько у журнала доставки: списки
# соседние, строки одного вида, и разная длина страницы читалась бы как разница по
# существу, которой нет.
BLOCK_PAGE = 25


async def block_rows(site_id: int, profile_id: int = 0, page: int = 1,
                            per: int = 0, channel: str = '') -> dict:
    """Чёрный список сайта для страницы, свежие сверху.

    Args:
        site_id: сайт-закладка.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.

            ⚠ **Фильтр, а не условие показа**, как у диалогов и доставки: без
            выбора список полон и отвечает на «от кого вообще закрылись».
        page: страница, с единицы.
        per: строк на странице. Ноль — `BLOCK_PAGE`.
        channel: чей список показываем. `mail` — только те запреты, что касаются
            писем и пришли из почты; пусто — все.

            ⚠⚠ **Это про показ, а не про работу.** «Фавориты → Не пишем» отвечает
            на вопрос «кому не уходят письма избранным», и шесть сотен пар,
            закрывшихся в чате, там только мешают: человек пришёл разбираться с
            почтой. Работу же по-прежнему определяет `block_pairs`, и она шире —
            закрывшийся в чате писем тоже не получит.

    Returns:
        dict: `rows` — записи страницы, `page`, `pages`, `total`.

    ⚠ **Страницы на сервере, а не в браузере.** Список копится сам и не убывает:
    запрет не истекает, и каждый отказ сайта добавляет строку. Отдавать его целиком
    значит однажды отдать всё подряд — и тем медленнее, чем дольше панель работает.

    ⚠ **Страница за концом правится, а не отвергается**: человек мог снять запрет с
    последней страницы, и отказ вместо предыдущей читался бы как поломка. То же
    правило, что у журнала доставки.
    """
    site_id = int(site_id or 0)
    if not site_id:
        return {'rows': [], 'page': 1, 'pages': 1, 'total': 0}

    where = ' WHERE b.site_id = %s AND (%s = 0 OR b.profile_id = %s)'
    args = (site_id, int(profile_id or 0), int(profile_id or 0))

    # ⚠ Отбор по **источнику**, а не по `channel`: вкладка показывает то, что
    # случилось в почте, — и «он закрылся», услышанное при отправке письма, сюда
    # входит, хотя касается обоих каналов. Человек, пришедший на «Фавориты»,
    # ищет именно отказы своих писем.
    if str(channel) == BLOCK_CHANNEL_MAIL:
        where += " AND b.source = 'mail'"

    per = max(1, int(per or 0) or BLOCK_PAGE)

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM block b' + where, args)
            total = int(((await db.fetchone()) or {}).get('n') or 0)

            pages = max(1, -(-total // per))
            page = min(max(int(page or 1), 1), pages)

            await db.execute(
                'SELECT b.id, b.profile_id, b.uid, b.reason, b.source, b.created_at,'
                '       COALESCE(NULLIF(b.name, %s), comp.name, %s) AS name,'
                '       comp.photo, p.profile AS profile_card, p.login AS profile_login'
                '  FROM block b'
                '  JOIN profile p ON p.id = b.profile_id'
                '  LEFT JOIN companion comp'
                '    ON comp.site_id = b.site_id AND comp.uid = b.uid'
                + where +
                ' ORDER BY b.created_at DESC, b.id DESC LIMIT %s OFFSET %s',
                ('', '', *args, per, (page - 1) * per))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] чёрный список сайта #{site_id} не прочитался: {e}')

        return {'rows': [], 'page': 1, 'pages': 1, 'total': 0}

    from src.dating.json_column import json_column_load

    out = []
    for row in rows or []:
        got = dict(row)
        card = json_column_load(got.pop('profile_card', None)) or {}
        got['profile_name'] = str(card.get('name')
                                  or got.pop('profile_login', '') or '')
        got.pop('profile_login', None)
        # ⚠ **И лицо с номером анкеты.** Строка рисует обе стороны тем же видом,
        # что «Доставка» и «Диалоги» (`talkSide`), а тот ждёт три поля. Без них
        # девушка оставалась буквой в кружке, хотя её фотография лежала в той же
        # строке запроса — карточка анкеты уже была в руках.
        got['profile_photo'] = str(card.get('photo') or '')
        got['profile_uid'] = str(card.get('id') or '')
        got['photo'] = str(got.get('photo') or '')
        got['created_at'] = str(got['created_at']) if got.get('created_at') else ''
        out.append(got)

    return {'rows': out, 'page': page, 'pages': pages, 'total': total}


async def block_drop(block_id: int) -> bool:
    """Снимает запрет. `False` — такой записи уже нет.

    ⚠ **Снять его может только человек.** Сайт не сообщает, когда собеседник
    передумал, и срок жизни записи был бы выдумкой: «через месяц попробуем снова» —
    это решение написать тому, кто просил не писать.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM block WHERE id = %s', (int(block_id),))
            gone = int(db.rowcount or 0) > 0
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] запрет #{block_id} не снялся: {e}')

        return False

    return gone
