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
(`dating_invite._talked`) и по той же причине: разговор — свойство двоих.

## ⚠ Примета отказа — настройкой, а не константой

Коды отказа придумывает сайт. `ERROR_MESSAGE_USER_DENY` — код того движка, на
котором работают оба наших сайта, и он же стоит умолчанием; сайт на другом движке
скажет это иначе, и тогда примета правится на странице «Сайты», а не в коде.

Примет может быть несколько — через запятую: у одного и того же сайта чат и почта
вправе называть одно и то же разными словами.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_feed import dating_feed_write
from src.dating.dating_log import dating_log_write

# Настройка сайта: по каким словам в ответе узнаём «он закрылся».
DATING_BLOCK_DENY_SETTING = 'block_deny_mark'

# Умолчание — код движка, на котором работают оба наших сайта. ⚠ Это умолчание, а
# не правило: другой движок скажет то же самое иначе, и примета правится настройкой.
DATING_BLOCK_DENY_DEFAULT = 'ERROR_MESSAGE_USER_DENY'

# Пулы, из которых запрет выметает готовое. ⚠ `dating_mail_reply` здесь нет: ответ
# на его же письмо уходит и после запрета (см. заголовок модуля).
DATING_BLOCK_POOLS = ('dating_invite', 'dating_mailing', 'dating_favorite_mail')


async def dating_block_deny(site_id: int, answer: str) -> bool:
    """Значит ли ответ сайта «он закрылся от этой девушки».

    Args:
        site_id: сайт — у него и спрашиваем примету.
        answer: что ответил сайт. Годится и тело JSON, и собранная причина
            словами: ищем вхождение, а не разбираем ответ.

    Returns:
        bool: `True` — это стойкий отказ, писать больше нельзя.

    ⚠ **Вхождение подстроки, а не разбор JSON.** Один канал отдаёт нам тело ответа,
    другой — уже собранную фразу с кодом в скобках; разбирать пришлось бы оба, и
    каждый по-своему. Код отказа — слово, которого в обычном ответе нет.
    """
    from src.uvicorn.routers.dating.dating_site import dating_site_setting

    answer = str(answer or '')
    if not answer:
        return False

    marks = await dating_site_setting(int(site_id or 0), DATING_BLOCK_DENY_SETTING,
                                      DATING_BLOCK_DENY_DEFAULT)

    return any(one.strip() and one.strip() in answer for one in str(marks).split(','))


async def dating_block_add(site_id: int, profile_id: int, uid: str, name: str = '',
                           reason: str = '', source: str = 'chat') -> bool:
    """Заносит пару в чёрный список и выметает всё готовое для неё из пулов.

    Args:
        site_id: сайт-закладка.
        profile_id: девушка, от которой закрылись.
        uid: номер собеседника на сайте.
        name: как его зовут — для списка, который читает человек.
        reason: что ответил сайт, целиком.
        source: откуда узнали — `chat`, `mail` или `hand`.

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
                'INSERT IGNORE INTO dating_block'
                ' (site_id, profile_id, uid, name, reason, source)'
                ' VALUES (%s, %s, %s, %s, %s, %s)',
                (site_id, profile_id, uid, who[:190], str(reason or '')[:500],
                 source if source in ('chat', 'mail', 'hand') else 'chat'))
            new = int(db.rowcount or 0) > 0

            for table in DATING_BLOCK_POOLS:
                await db.execute(
                    f"DELETE FROM {table} WHERE profile_id = %s AND uid = %s"
                    "   AND status = 'draft'", (profile_id, uid))

            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] запрет писать #{profile_id} → {uid} не записался: {e}')

        return False

    if not new:
        return False

    # ⚠ Уровень `fail`, а не `warn`: `dating_log.level` знает пять слов, и `warn`
    # среди них нет — MySQL обрезал бы его до пустого молча. У ленты словарь свой,
    # и там `warn` как раз есть; одинаковыми они не были и не должны быть.
    await dating_log_write(profile_id, 'fail',
                           f'Больше не пишем: {who or uid} закрылся от этой анкеты')
    dating_feed_write(site_id, 'warn', f'Закрылся от анкеты: {who or uid}', profile_id)
    logger_info(f'[dating] запрет писать: анкета #{profile_id} → {uid} ({reason[:120]})')

    return True


async def dating_block_pairs(site_id: int) -> set:
    """Пары «анкета — собеседник», которым на этом сайте писать нельзя.

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
            await db.execute('SELECT profile_id, uid FROM dating_block'
                             ' WHERE site_id = %s', (site_id,))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] чёрный список сайта #{site_id} не спросился: {e}')

        return set()

    return {(int(row['profile_id']), str(row['uid'])) for row in rows or []}


async def dating_block_rows(site_id: int, profile_id: int = 0) -> list[dict]:
    """Чёрный список сайта для страницы, свежие сверху.

    Args:
        site_id: сайт-закладка.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.

            ⚠ **Фильтр, а не условие показа**, как у диалогов и доставки: без
            выбора список полон и отвечает на «от кого вообще закрылись».
    """
    site_id = int(site_id or 0)
    if not site_id:
        return []

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT b.id, b.profile_id, b.uid, b.reason, b.source, b.created_at,'
                '       COALESCE(NULLIF(b.name, %s), comp.name, %s) AS name,'
                '       comp.photo, p.profile AS profile_card, p.login AS profile_login'
                '  FROM dating_block b'
                '  JOIN dating_profile p ON p.id = b.profile_id'
                '  LEFT JOIN dating_companion comp'
                '    ON comp.site_id = b.site_id AND comp.uid = b.uid'
                ' WHERE b.site_id = %s AND (%s = 0 OR b.profile_id = %s)'
                ' ORDER BY b.created_at DESC, b.id DESC',
                ('', '', site_id, int(profile_id or 0), int(profile_id or 0)))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] чёрный список сайта #{site_id} не прочитался: {e}')

        return []

    from src.dating.dating_json import dating_json_load

    out = []
    for row in rows or []:
        got = dict(row)
        card = dating_json_load(got.pop('profile_card', None)) or {}
        got['profile_name'] = str(card.get('name')
                                  or got.pop('profile_login', '') or '')
        got.pop('profile_login', None)
        # ⚠ **И лицо с номером анкеты.** Строка рисует обе стороны тем же видом,
        # что «Доставка» и «Диалоги» (`talkSide`), а тот ждёт три поля. Без них
        # девушка оставалась буквой в кружке, хотя её фотография лежала в той же
        # строке запроса — карточка анкеты уже была в руках.
        got['profile_photo'] = str(card.get('photo') or '')
        got['profile_uid'] = str(card.get('site_uid') or '')
        got['photo'] = str(got.get('photo') or '')
        got['created_at'] = str(got['created_at']) if got.get('created_at') else ''
        out.append(got)

    return out


async def dating_block_drop(block_id: int) -> bool:
    """Снимает запрет. `False` — такой записи уже нет.

    ⚠ **Снять его может только человек.** Сайт не сообщает, когда собеседник
    передумал, и срок жизни записи был бы выдумкой: «через месяц попробуем снова» —
    это решение написать тому, кто просил не писать.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM dating_block WHERE id = %s', (int(block_id),))
            gone = int(db.rowcount or 0) > 0
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] запрет #{block_id} не снялся: {e}')

        return False

    return gone
