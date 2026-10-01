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
`send_every`, письма избранным — их собственное условие выборки
(`favorite_mail_gap`, см. `_oldest`). Полсотни писем, ушедшие залпом, — рассылка
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

⚠ **Неудача не повторяется тут же.** `mail_send.py` оставляет письмо в пуле
с причиной в `error`; круг такое письмо пропускает до конца суток. Иначе один
отказ сайта («слишком часто») превращался бы в бесконечный цикл ровно тогда, когда
сайт просит остановиться.
"""


from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load
from src.dating.round import (round_day_count, round_mem_due,
                            round_mem_mark, round_minutes)
from src.dating.mail_send import (MAIL_SEND_TALK_TABLES, DatingMailSendError,
                                  mail_send_mark, mail_send_one)
from src.dating.schedule import schedule_now

# Как часто круг просыпается. ⚠ Это **не** частота отправки: её задаёт каждый пул
# своим сроком (`_POOLS`), а круг лишь смотрит, кому из них пора.
SEND_ROUND_TICK_SEC = 30

# Пулы круга: выключатель, таблица, настройка своего темпа и слово для шапки.
# ⚠ Порядок — порядок обхода, не приоритет: пулы независимы, и каждый, кому пора,
# уходит своим кругом, не дожидаясь соседей. Тем, у кого срок сайтный; у избранных
# `None` — их время живёт в условии выборки (`favorite_mail_girl_free_sql`
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

    minutes = round_minutes(offset_hours)
    sent = failed = 0

    for site in sites:
        site_id = int(site['site_id'])

        # ⚠ Смены общие с набором: шлём тогда же, когда готовим. Пустое
        # расписание — «нет», а не «всегда»: правило одно на проект.
        if not schedule_now(json_column_load(site['mailing_schedule']),
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

            if await _send(letter, table):
                sent += 1
            else:
                failed += 1

    if sent or failed:
        logger_info(f'[dating] круг отправки: ушло {sent}'
                    + (f', не ушло {failed}' if failed else ''))

    return {'sent': sent, 'failed': failed}


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

        if not schedule_now(json_column_load(site['mailing_schedule']),
                            round_minutes(offset_hours)):
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

    # ⚠ **Третья причина простоя, и она появилась вместе с паузой.** Пул полон,
    # ошибок нет, а круг уходит ни с чем: каждая девушка недавно писала. Без
    # этой строки человек видел бы полный пул и молчащую отправку — состояние,
    # которое читается как поломка, а на деле работает ровно так, как настроено.
    if str(table) == 'favorite_mail':
        left = await _girls_wait(site_id)
        if left:
            return left

    return ''


async def _girls_wait(site_id: int) -> str:
    """Все ли девушки пула избранных сейчас в паузе. Пусто — не все.

    ⚠ Спрашивает **тем же условием**, которым отбирает круг
    (`favorite_mail_girl_free_sql`): ответ «почему стоим» обязан считаться тем
    же кодом, что решает за круг, иначе он объяснит не тот простой.
    """
    from src.dating.favorite_mail import (favorite_mail_gap_sec,
                                          favorite_mail_girl_free_sql)

    gap = await favorite_mail_gap_sec(site_id)
    free = favorite_mail_girl_free_sql(gap)
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

    from src.dating.favorite_mail import _hours_word

    return (f'все анкеты недавно писали — пауза между письмами '
            f'{_hours_word(gap)}')


async def _sites() -> list[dict]:
    """Сайты, где включён хоть один выключатель отправки."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT site_id, mailing_schedule, mailing_send_on, reply_send_on,'
                ' favorite_mail_send_on'
                ' FROM site'
                ' WHERE is_gone = 0 AND (mailing_send_on = 1 OR reply_send_on = 1'
                '                        OR favorite_mail_send_on = 1)'
                ' ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для отправки не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


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
    # Настройка `favorite_mail_gap` говорит: от одной анкеты уходит одно письмо за
    # срок, кому угодно. Проверяй её только набор пула — правило осталось бы на
    # бумаге: черновики лежат готовыми, а уносит их этот круг по письму за раз.
    # Так и вышло 21 сентября — четыре письма от Елены четверым за восемь минут.
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
        from src.dating.favorite_mail import (favorite_mail_gap_sec,
                                              favorite_mail_girl_free_sql)

        busy = favorite_mail_girl_free_sql(await favorite_mail_gap_sec(site_id))

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

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'SELECT m.id, m.site_id, m.profile_id, m.uid, m.body, m.status,'
                f' p.login, p.password, p.password_source{reply_to}{message_id}'
                f'  FROM {table} m'
                f'  JOIN profile p ON p.id = m.profile_id'
                f'{reply_join}'
                f" WHERE m.site_id = %s AND m.status = 'draft' AND m.error = ''"
                f'   AND p.enabled = 1{online_only}{busy}'
                f' ORDER BY m.id LIMIT 1', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] очередь отправки {table} не спросилась: {e}')

        return None

    return dict(row) if row else None


async def _send(letter: dict, table: str) -> bool:
    """Отправляет одно письмо. Не бросает: круг обходит остальные сайты дальше."""
    from src.uvicorn.routers.dating.site_ import site_setting

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
        # ⚠ Причину храним: она ляжет на карточку, и человек увидит, почему письмо
        # осталось в пуле. Круг к нему больше не вернётся — см. `_oldest`.
        await mail_send_mark(letter, sent=False, error=str(e), table=table)
        logger_info(f"[dating] {table} #{letter['id']} не ушло: {e}")

        return False
    except Exception as e:
        logger_info(f"[dating] {table} #{letter['id']} сорвалось: {e}")

        return False

    return True


async def _today(site_id: int, offset_hours: int = 0) -> int:
    """Сколько писем рассылки ушло за сегодня. Правило общее — `round_day_count`.

    ⚠ Считает **только рассылку**: норма `send_max_day` отныне её собственная,
    и соседние пулы её не едят — у ответов и писем избранным свои пределы
    на «Фаворитах».

    ⚠ Сомнение — в сторону молчания: неизвестный потолок считают выбранным,
    а лишнее письмо отменить нельзя.
    """
    return await round_day_count('mailing', site_id, offset_hours, 10 ** 6,
                                 'отправки')
async def _every(site_id: int, setting: str) -> int:
    """Пауза этого пула, в секундах.

    ⚠ Предела поверх нет, и он не нужен: темп задан **набором** значений, а
    `site_seconds` чужое значение не берёт — возвращает реестровое умолчание.
    """
    from src.uvicorn.routers.dating.site_ import site_seconds

    return int(await site_seconds(site_id, setting))


async def _max_day(site_id: int) -> int:
    """Сколько писем рассылки в сутки разрешено этому сайту."""
    from src.uvicorn.routers.dating.site_ import site_seconds

    return int(await site_seconds(site_id, 'send_max_day'))


