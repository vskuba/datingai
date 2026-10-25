"""Круг отправки: письма каждого занятия уходят сами, по одному, по своему сроку.

Выключатели сайта — `mailing_send_on`, `reply_send_on` и `favorite_mail_send_on`
— только хранят решение; исполнитель у всех троих один, этот круг.

## ⚠ Отправка — самое заметное наружу действие панели

Письмо уходит живому человеку от имени девушки, и отменить его нельзя. Отсюда все
правила ниже; каждое из них — не осторожность вообще, а защита от конкретной беды.

**Выключатели раздельные.** «Готовим» и «шлём» — разные решения, и включённый
набор согласием на отправку не является. Пара своя у каждого из трёх занятий:
рассылки, ответов и переписки с избранными.

**Смены сайта соблюдаются.** Шлём в те же часы, когда набираем
(`mailing_schedule`): письмо в четыре утра выдаёт робота вернее любого текста.

**Каждый пул меряется своим сроком.** Ответы — `reply_every`, рассылку —
`send_every`, письма избранным — их собственное условие выборки (пауза анкеты,
жребий между `favorite_mail_gap` и `favorite_mail_gap_max`, см. `_oldest`).
Полсотни писем, ушедшие залпом, — рассылка
робота, чем она и является; пауза делает поток похожим на человека, который сидит
и пишет. Но чужой темп пул не держит: ответ ждёт свой срок, а не рассылочный —
человек написал и ждёт ответа, пока массовая рассылка копится в своей очереди.

**Норма суток — тоже своя.** У рассылки она сайтная (`send_max_day`, по её же
ушедшим строкам): даже за длинную смену уходит не больше, чем человек физически
написал бы. У писем избранным предел на человека и считается на наборе
(`favorite_mail_per_day`); у ответов потолка нет вовсе — молчание отвечают.

⚠ **Письма избранным — только от анкеты, которая сейчас в сети** (`status =
'online'`). Это возобновление затихшего разговора: человек читает письмо, пока
сидит на сайте, и отвечает сразу — а отвечает он девушке, которой там нет. Набор
пула к тому же отбирал тех, кто онлайн **в момент набора**, и отправка в её
отсутствие рассылала бы вчерашний отбор.

У рассылки и ответов такого условия нет: первое уходит незнакомому, второе — тому,
кто написал сам и ждёт, и ни то, ни другое не про «прямо сейчас».

⚠⚠ **Рассылка не уходит тому, кто ответил письмом.** Холодное обращение кончается
его письмом — дальше с ним работает «приставание» (`favorite_mail`). Проверяется
здесь **второй раз** после набора пула, и это не перестраховка: между сочинением и
отправкой у рассылки проходят часы, а письмо он мог прислать в любую из них.
Правило одно на четыре места — `pool_pair.pool_mail_answered_where`.

⚠ **Неудача не повторяется тут же.** `mail_send.py` оставляет письмо в пуле
с причиной в `error`; круг такое письмо пропускает до конца суток. Иначе один
отказ сайта («слишком часто») превращался бы в бесконечный цикл ровно тогда, когда
сайт просит остановиться.
"""


import asyncio

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_list
from datetime_.datetime_ import datetime_day_minutes
from src.dating.round import (round_mem_due, round_mem_mark, round_sent_day,
                              round_sites)
from src.dating.mail_send import (MAIL_SEND_TALK_TABLES, DatingMailSendError,
                                  DatingMailWaitError, mail_send_fail,
                                  mail_send_one)
from src.dating.mail_wait import mail_wait_uids
from src.dating.pool_pair import pool_mail_answered_where
from src.dating.schedule import schedule_now
from src.dating.site_setting import site_seconds, site_setting

# Как часто круг просыпается. ⚠ Это **не** частота отправки: её задаёт каждый пул
# своим сроком (`_POOLS`), а круг лишь смотрит, кому из них пора.
SEND_ROUND_TICK_SEC = 30

# ⚠⚠ **Пауза между двумя письмами одного захода. Написана по живому отказу.**
#
# Ручки сайта ограничены по частоте **на учётку**: два запроса с разрывом меньше
# ста миллисекунд дают `403 ERROR_TOO_MANY_REQUESTS`, и дальше учётка молчит ещё
# несколько секунд (проверено на проде: разрыв в секунду отказ не вызывает, разрыв
# в 50 мс вызывает всегда). Пулы же обходятся подряд, и `check-send` второго
# уходил сразу за удачной отправкой первого.
#
# Две секунды — заведомо больше измеренного порога и заведомо меньше тика круга
# (30 с), то есть пауза не съедает заход. ⚠ Точного порога сайт не называет, и
# выверять его до миллисекунды незачем: цена ошибки в сторону «подождали дольше» —
# две секунды, в обратную — похороненное письмо.
SEND_ROUND_GAP_SEC = 2.0

# Пулы круга: выключатель, таблица, настройка своего темпа и слово для шапки.
# ⚠ Порядок — порядок обхода, не приоритет: пулы независимы, и каждый, кому пора,
# уходит своим кругом, не дожидаясь соседей. Тем, у кого срок сайтный; у избранных
# `None` — их время живёт в условии выборки (`favorite_mail_girls_busy_sql`
# в `_oldest`), а не в замке на сайт.
_POOLS = (
    ('reply_send_on', 'mail_reply', 'reply_every', 'ответов'),
    ('favorite_mail_send_on', 'favorite_mail', None, 'писем избранным'),
    ('mailing_send_on', 'mailing', 'send_every', 'рассылки'),
)

# Когда пул сайта слал в последний раз: `{(сайт, пул): время}`. В памяти процесса —
# как у круга присутствия: перезапуск смены стоит одного лишнего письма, а не
# потока. Избранных здесь нет: их темп отсчитывается от ушедших писем в базе,
# а не от памяти процесса.
_sent_at: dict[tuple[int, str], float] = {}


async def send_round_run(offset_hours: int = 0) -> dict:
    """Один круг: по письму из каждого пула, которому пора, с каждого готового сайта.

    Args:
        offset_hours: часовое смещение панели — смены заданы местным временем.

    Returns:
        dict: `sent` — сколько ушло, `failed` — сколько не ушло.
    """
    sites = await _sites()
    if not sites:
        return {'sent': 0, 'failed': 0}

    minutes = datetime_day_minutes(offset_hours)
    sent = failed = waited = 0
    # Был ли за этот заход поход к сайту: по нему решается пауза перед следующим
    # пулом (см. `SEND_ROUND_GAP_SEC`).
    touched = False

    for site in sites:
        site_id = int(site['site_id'])

        # ⚠ Смены общие с набором: шлём тогда же, когда готовим. Пустое
        # расписание — «нет», а не «всегда»: правило одно на проект.
        if not schedule_now(json_column_list(site['schedule']),
                            minutes):
            continue

        for flag, table, setting, _said in _POOLS:
            if not int(site[flag]):
                continue

            # ⚠ Темп у пула свой: ответ ждёт `reply_every`, а не рассылочные
            # три минуты; рассылка не смотрит на соседние сроки.
            if setting and not round_mem_due(_sent_at, (site_id, table),
                                              await _every(site_id, setting)):
                continue

            # ⚠ Норма рассылки считает её собственные ушедшие письма: ответы
            # и письма избранным под неё не попадают — у тех пределы свои
            # и стоят на «Фаворитах».
            if table == 'mailing' and \
                    await _today(site_id, offset_hours) >= await _max_day(site_id):
                continue

            # ⚠ Строки с прежней ошибкой `_oldest` не берёт: неудача оставляет
            # письмо в пуле с причиной, и круг к нему не возвращается — такое
            # письмо ждёт человека. Иначе отказ сайта («слишком часто») стал бы
            # бесконечным повтором ровно тогда, когда сайт просит остановиться.
            letter = await _oldest(site_id, table)
            if not letter:
                continue

            if setting:
                round_mem_mark(_sent_at, (site_id, table))

            # ⚠⚠ **Пауза перед вторым пулом захода, и она не вежливость.**
            #
            # Пулы обходятся подряд, и `check-send` следующего уходил через
            # 64–94 мс после удачного `/messages/send` предыдущего. Сайт на такой
            # разрыв отвечает `403 ERROR_TOO_MANY_REQUESTS` — и письмо второго
            # пула умирало ни за что. Четыре таких случая за трое суток в журнале
            # запросов, и все с одинаковой приметой: отказ сразу за удачей.
            if touched:
                await asyncio.sleep(SEND_ROUND_GAP_SEC)

            touched = True

            got = await _send(letter, table)
            sent += got == 'sent'
            waited += got == 'wait'
            failed += got == 'fail'

    if sent or failed or waited:
        logger_info(f'[dating] круг отправки: ушло {sent}'
                    + (f', не ушло {failed}' if failed else '')
                    + (f', ждут {waited}' if waited else ''))

    return {'sent': sent, 'failed': failed, 'waited': waited}


async def send_why(site_id: int, table: str, offset_hours: int = 0) -> str:
    """Почему из этого пула сейчас ничего не уходит. Пусто — уходит, повода нет.

    Args:
        site_id: сайт-закладка.
        table: чей пул спрашивают — `mailing`, `mail_reply` или
            `favorite_mail`. У каждого занятия теперь всё своё: и выключатель,
            и срок, и норма — потому и ответ у каждого свой.
        offset_hours: часовое смещение панели.

    Returns:
        str: одна строка для шапки пула, человеческими словами.

    ⚠⚠ **Молчащий пул неотличим от сломанного, и это уже стоило времени.** «Пул
    50 из 50», анкеты в сети, смена идёт — и ничего не уходит; причина законная,
    но лежит в журнале воркера, а человек смотрит на страницу.

    ⚠ **Норма объясняется своим числом и только у своего пула.** Потолок
    `send_max_day` рассылки — и только рассылку: строка о нём в шапке ответов
    была бы ложью, ответы под ним не стоят.

    ⚠ **Считается теми же вопросами и в том же порядке, что и круг**, а не второй
    их копией.
    """
    site_id = int(site_id or 0)
    pool = next((one for one in _POOLS if one[1] == str(table)), None)
    if not site_id or not pool:
        return ''

    flag, table, _, said = pool

    try:
        site = {int(one['site_id']): one for one in await _sites()}.get(site_id)
        if not site or not int(site.get(flag) or 0):
            return f'отправка {said} выключена'

        if not schedule_now(json_column_list(site['schedule']),
                            datetime_day_minutes(offset_hours)):
            return 'сейчас не смена: письма уходят по часам, заданным у сайта'

        if table == 'mailing':
            today, cap = await _today(site_id, offset_hours), await _max_day(site_id)
            if today >= cap:
                return (f'норма рассылки на сегодня выбрана: ушло {today} из {cap}. '
                        'Поднять — настройка сайта «Не больше писем рассылки '
                        'в сутки»')

        if await _oldest(site_id, str(table)):
            return ''

        return await _nothing_to_send(site_id, str(table))
    except Exception as e:
        logger_info(f'[dating] повод простоя отправки не спросился: {e}')

        return ''


# ── Приватное ────────────────────────────────────────────────────────────────


async def _nothing_to_send(site_id: int, table: str) -> str:
    """Почему очередь пуста: черновиков нет вовсе или все с прежней ошибкой."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f"SELECT COUNT(*) AS total, SUM(error <> '') AS failed"
                f'  FROM {table} WHERE site_id = %s AND status = %s',
                (int(site_id), 'draft'))
            row = dict((await db.fetchone()) or {})
    except Exception as e:
        logger_info(f'[dating] разбор простоя отправки не удался: {e}')

        return ''

    total = int(row.get('total') or 0)
    if not total:
        return 'пул пуст: отправлять нечего'

    if int(row.get('failed') or 0) >= total:
        return ('все письма с ошибкой — круг такие не берёт, '
                'их досылают кнопкой')

    # ⚠ **Четвёртая причина простоя, и она появилась вместе с остудой.** Пул полон,
    # ошибок нет, девушки свободны — а получателям сайт сейчас писать не даёт. Без
    # этой строки человек видел бы полный пул и молчащую отправку, то есть то же
    # самое состояние, которое прежде означало поломку.
    left = await _cooling_wait(site_id, table)
    if left:
        return left

    # ⚠ **Пятая причина простоя: все письма пула — тем, кто уже ответил письмом.**
    # Такие рассылка не уносит вовсе (`_oldest`), а подметает их круг набора — то
    # есть раз в свой срок, не раньше. До уборки пул выглядит полным и живым, и без
    # этой строки молчание читалось бы как поломка.
    if str(table) == 'mailing':
        left = await _answered_wait(site_id)
        if left:
            return left

    # ⚠ **Третья причина простоя, и она появилась вместе с паузой.** Пул полон,
    # ошибок нет, а круг уходит ни с чем: каждая девушка недавно писала. Без
    # этой строки человек видел бы полный пул и молчащую отправку — состояние,
    # которое читается как поломка, а на деле работает ровно так, как настроено.
    if str(table) == 'favorite_mail':
        left = await _girls_wait(site_id)
        if left:
            return left

    return ''


async def _cooling_wait(site_id: int, table: str) -> str:
    """Все ли адресаты пула сейчас остывают. Пусто — не все.

    ⚠ Спрашивает **тем же списком**, которым отбирает круг (`mail_wait_uids`):
    ответ «почему стоим» обязан считаться тем же кодом, что решает за круг.
    """
    cooling = mail_wait_uids(site_id)
    if not cooling or not any(one[1] == str(table) for one in _POOLS):
        return ''

    # ⚠ Список подстановок собирается отдельной строкой, а не `%`-форматированием
    # запроса: в запросе есть и свои `%s`, и формат съел бы их первыми.
    holes = ', '.join(['%s'] * len(cooling))

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'SELECT COUNT(*) AS free FROM {table}'
                f" WHERE site_id = %s AND status = 'draft' AND error = ''"
                f'   AND uid NOT IN ({holes})',
                (int(site_id), *cooling))
            row = dict((await db.fetchone()) or {})
    except Exception as e:
        logger_info(f'[dating] остуда адресатов не спросилась: {e}')

        return ''

    if int(row.get('free') or 0):
        return ''

    return (f'сайт сейчас не даёт писать ни одному адресату пула: у них выбран '
            f'предел входящих ({len(cooling)} чел. остывают). Письма целы — '
            f'уйдут, когда предел отпустит')


async def _answered_wait(site_id: int) -> str:
    """Все ли письма рассылки — тем, кто уже ответил письмом. Пусто — не все.

    ⚠ Спрашивает **тем же условием**, которым отбирает круг
    (`pool_mail_answered_where`): ответ «почему стоим» обязан считаться тем же
    кодом, что решает за круг, иначе он объяснит не тот простой.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS free FROM mailing m'
                "  WHERE m.site_id = %s AND m.status = 'draft' AND m.error = ''"
                + pool_mail_answered_where('m'), (int(site_id),))
            row = dict((await db.fetchone()) or {})
    except Exception as e:
        logger_info(f'[dating] ответившие письмом не спросились: {e}')

        return ''

    if int(row.get('free') or 0):
        return ''

    return ('все письма пула — тем, кто уже ответил письмом: таким рассылка не '
            'уходит, с ними работает «приставание». Черновики уберёт следующий '
            'заход набора')


async def _girls_wait(site_id: int) -> str:
    """Все ли девушки пула избранных сейчас в паузе. Пусто — не все.

    ⚠ Спрашивает **тем же условием**, которым отбирает круг
    (`favorite_mail_girls_busy_sql`): ответ «почему стоим» обязан считаться тем
    же кодом, что решает за круг, иначе он объяснит не тот простой.
    """
    from src.dating.favorite_mail import (favorite_mail_gap_range,
                                          favorite_mail_girls_busy_sql)

    free = await favorite_mail_girls_busy_sql(site_id)
    if not free:
        return ''

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS free FROM favorite_mail m'
                "  WHERE m.site_id = %s AND m.status = 'draft' AND m.error = ''"
                + free, (int(site_id),))
            row = dict((await db.fetchone()) or {})
    except Exception as e:
        logger_info(f'[dating] пауза девушек не спросилась: {e}')

        return ''

    if int(row.get('free') or 0):
        return ''

    from src.dating.favorite_mail import favorite_mail_gap_words

    # ⚠ Называем **промежуток**, а не выпавшую кому-то паузу: анкет здесь много,
    # и срок у каждой свой. «Пауза 4–6 часов» отвечает на вопрос «долго ли
    # стоим», а чужая выпавшая пятёрка только сбила бы с толку.
    low, high = await favorite_mail_gap_range(site_id)

    return (f'все анкеты недавно писали — пауза между письмами '
            f'{favorite_mail_gap_words(low, high)}')


async def _sites() -> list[dict]:
    """Сайты, где включён хоть один выключатель отправки."""
    return await round_sites(
        flags=('mailing_send_on', 'reply_send_on', 'favorite_mail_send_on'),
        any_of=True, schedule='mailing_schedule',
        extra=('mailing_send_on', 'reply_send_on', 'favorite_mail_send_on'),
        what='отправке')


async def _oldest(site_id: int, table: str) -> dict | None:
    """Самое давнее неотправленное письмо таблицы. Порядок — очередь, а не стопка.

    ⚠ Имя таблицы подставляется в запрос, поэтому берётся **только из белого
    списка** `_POOLS`.
    """
    if not any(one[1] == table for one in _POOLS):
        return None

    # ⚠ **Письма избранным уходят только от анкеты, которая сейчас в сети.**
    #
    # Это возобновление затихшего разговора: человек видит письмо, пока сидит на
    # сайте, и отвечает сразу — а отвечает он девушке, которой там нет. Хуже того,
    # набор пула уже отобрал тех, кто **сейчас** онлайн (`favorite_mail`), и
    # отправка в её отсутствие рассылала бы вчерашний отбор случайным людям.
    #
    # ⚠ **У рассылки и ответов этого условия нет, и это не недосмотр.** Письмо
    # рассылки уходит незнакомому, ответ — тому, кто написал сам и ждёт; ни то, ни
    # другое не обещает, что девушка на сайте прямо сейчас. Здесь обещает: письмо
    # написано под сиюминутный разговор.
    online_only = " AND p.status = 'online'" if table == 'favorite_mail' else ''

    # ⚠⚠ **Пауза между письмами одной девушки — здесь, а не только в наборе.**
    #
    # Настройки `favorite_mail_gap` и `favorite_mail_gap_max` говорят: от одной
    # анкеты уходит одно письмо за срок, кому угодно, а сам срок ей бросается
    # жребием между ними. Проверяй его только набор пула — правило осталось бы на
    # бумаге: черновики лежат готовыми, а уносит их этот круг по письму за раз.
    # Так и вышло 21 сентября — четыре письма от Елены четверым за восемь минут.
    #
    # ⚠ Приезжает оно **списком занятых анкет**, а не сроком в условии: жребий
    # бросается в одном месте и на Python, второй его записи на языке MySQL быть
    # не должно (`favorite_mail_girls_busy_sql`).
    #
    # ⚠ **Условием в запросе, а не отсевом после.** Круг берёт из пула **одно**
    # письмо за заход; отсей мы занятую девушку после выборки, заход кончился бы
    # ничем — и сайт молчал бы всю паузу, хотя писать готовы двенадцать других.
    # Условие же просто отдаёт следующее письмо следующей.
    #
    # ⚠ Правило живёт в `favorite_mail.py` и отсюда только спрашивается: там же
    # его читает набор пула, и две копии разошлись бы на первой правке.
    busy = ''
    if table == 'favorite_mail':
        from src.dating.favorite_mail import favorite_mail_girls_busy_sql

        busy = await favorite_mail_girls_busy_sql(site_id)

    # ⚠⚠⚠ **Номер письма, на которое отвечаем, — у ответов обязателен.**
    #
    # Он уезжает в `ReplyForMessageId`, и без него сайт показывает наш ответ
    # **отдельным письмом**: собеседник не понимает, на что ему отвечают. Кнопка
    # человека (`mail_reply_send`) его передавала всегда, а этот запрос поля не
    # выбирал вовсе — то есть **всё, что уходило кругом, шло не ответом**. Найдено
    # 26.09.2026 при разведке панели: она числила неотвеченными письма, на которые
    # ответ ушёл сутки назад.
    #
    # ⚠ Он же нужен и панельному пути: `MessageId` формы — это тот самый номер.
    reply_to = (', i.message_id AS reply_to' if table == 'mail_reply' else '')
    reply_join = ('  LEFT JOIN mail_in i ON i.id = m.mail_in_id'
                  if table == 'mail_reply' else '')

    # ⚠⚠ **Номер обмена разговора — у пулов `dating_talk` обязателен.**
    #
    # `mail_send_mark` отмечает им доставку у соседа (`companion_session`), и
    # читает ровно это поле из строки письма. Не выбери его запрос — грабля та
    # же, что с `reply_to` строкой выше: отметка не уйдёт никогда, и узнать об
    # этом можно только через сутки, случайно. У `mailing` колонки нет вовсе —
    # ей и выбирать нечего.
    message_id = (', m.message_id' if table in MAIL_SEND_TALK_TABLES else '')

    # ⚠⚠ **Остывшие получатели выпадают из очереди, а не отсеиваются после.**
    #
    # Круг берёт из пула **одно** письмо за заход. Отсей мы остывшего после
    # выборки — заход кончился бы ничем, и сайт молчал бы всю минуту, имея
    # сорок девять писем другим людям. Тот же довод и то же решение, что у паузы
    # девушки строкой выше.
    #
    # ⚠ Список берём у `mail_wait`, а не считаем здесь: его же читает набор пула,
    # и две копии правила разошлись бы на первой правке.
    cooling = mail_wait_uids(site_id)
    cool = (' AND m.uid NOT IN (%s)' % ', '.join(['%s'] * len(cooling))
            if cooling else '')

    # ⚠⚠ **Рассылка не уходит тому, кто за это время ответил письмом.**
    #
    # Холодное обращение кончается его письмом: дальше разговор ведёт
    # «приставание» (`favorite_mail`), которое пишет по переписке, а рассылка
    # пишет как незнакомой. Набор пула это правило уже знает — и его одного мало
    # **по устройству**: между сочинением и отправкой у рассылки проходят часы.
    #
    # Живой случай 04.10.2026, Yulia ↔ Riano: письмо сочинено в 13:21:33, его
    # письмо пришло в 13:25:41, ответ на него ушёл в 13:30:54 — а рассылка уехала
    # в 14:10:04, следом за ответом и в ту же нить.
    #
    # ⚠ **Условием в запросе, а не отсевом после.** Круг берёт из пула **одно**
    # письмо за заход; отсей мы такое после выборки — заход кончился бы ничем, и
    # сайт молчал бы весь срок, имея сорок девять писем незнакомым. Тот же довод и
    # то же решение, что у остуды выше и паузы девушки.
    #
    # ⚠ Правило живёт в `pool_pair.py` и отсюда только спрашивается: его же читают
    # набор пула, уборка и кнопка, и четыре копии разошлись бы на первой правке.
    answered = pool_mail_answered_where('m') if table == 'mailing' else ''

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'SELECT m.id, m.site_id, m.profile_id, m.uid, m.body, m.status,'
                f' p.login, p.password, p.password_source{reply_to}{message_id}'
                f'  FROM {table} m'
                f'  JOIN profile p ON p.id = m.profile_id'
                f'{reply_join}'
                f" WHERE m.site_id = %s AND m.status = 'draft' AND m.error = ''"
                f'   AND p.enabled = 1{online_only}{busy}{cool}{answered}'
                f' ORDER BY m.id LIMIT 1',
                (int(site_id), *cooling))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] очередь отправки {table} не спросилась: {e}')

        return None

    return dict(row) if row else None


async def _send(letter: dict, table: str) -> str:
    """Отправляет одно письмо. Не бросает: круг обходит остальные сайты дальше.

    Returns:
        str: `sent` — ушло, `wait` — сайт просит подождать (письмо цело),
        `fail` — не ушло, причина в письме.

    ⚠ **Три исхода, а не два.** Прежде был `bool`, и «подождать» неизбежно
    считалось неудачей — а это разные события: после `fail` письмо ждёт человека,
    после `wait` его унесёт следующий заход.
    """
    site_id = int(letter['site_id'])
    base = str(await site_setting(site_id, 'api_base')).strip()
    password = str(await site_setting(site_id, 'api_password')).strip()

    profile = {'id': letter['profile_id'], 'site_id': site_id,
               'login': letter['login'], 'password': letter['password'],
               'password_source': letter['password_source']}

    # ⚠ `letter` уезжает в отправку как есть, вместе с `reply_to`: он пришёл из
    # запроса выше и нужен обоим путям — `ReplyForMessageId` у ручки API и
    # `MessageId` у формы панели.

    try:
        await mail_send_one(letter, profile, base, password, table)
    except DatingMailSendError as e:
        # ⚠ Причину храним — но не всякую: временную в письмо писать нельзя,
        # круг к такому письму не вернулся бы никогда. Решает это одна дверь
        # `mail_send_fail`, а не здешний разбор рода ошибки.
        await mail_send_fail(letter, e, table=table)
        logger_info(f"[dating] {table} #{letter['id']} не ушло: {e}")

        return 'wait' if isinstance(e, DatingMailWaitError) else 'fail'
    except Exception as e:
        logger_info(f"[dating] {table} #{letter['id']} сорвалось: {e}")

        return 'fail'

    return 'sent'


async def _today(site_id: int, offset_hours: int = 0) -> int:
    """Сколько писем рассылки ушло за сегодня. Правило общее — `round_sent_day`.

    ⚠ Считает **только рассылку**: норма `send_max_day` отныне её собственная,
    и соседние пулы её не едят — у ответов и писем избранным свои пределы
    на «Фаворитах».

    ⚠ Сомнение — в сторону молчания: неизвестный потолок считают выбранным,
    а лишнее письмо отменить нельзя.
    """
    return await round_sent_day('mailing', site_id, offset_hours, 10 ** 6,
                                'отправки')
async def _every(site_id: int, setting: str) -> int:
    """Пауза этого пула, в секундах.

    ⚠ Предела поверх нет, и он не нужен: темп задан **набором** значений, а
    `site_seconds` чужое значение не берёт — возвращает реестровое умолчание.
    """
    return int(await site_seconds(site_id, setting))


async def _max_day(site_id: int) -> int:
    """Сколько писем рассылки в сутки разрешено этому сайту."""
    return int(await site_seconds(site_id, 'send_max_day'))


