"""Сообщения чата из панели агентства: журнал сайта в нашу базу.

У панели есть страница `userchatmessages` — список сообщений анкет за диапазон
дат: номер, время (UTC), кто, кому, текст. Сценарий у соседа (`чат: сообщения из
панели`) заходит туда и снимает таблицу; здесь — разбор и запись.

## Зачем это, если сообщения ловит сокет

`src/dating/chat.py` держит вебсокет и складывает то, что приходит **пока
анкета подключена**. Это дырявый источник по природе: нет держателя — нет и
сообщений, а всё, что было до заведения учётки в панели, отсутствует вовсе.

Панель знает всё. Один прогон за неделю приносит переписку, которой у нас не было,
включая диалоги анкет, которые мы никогда не держали.

## ⚠ Отдельной таблицы под это нет намеренно

Сообщения ложатся в `dating_message` — туда же, куда пишет сокет. Иначе карточка
чата показывала бы половину переписки, а вторую половину пришлось бы подмешивать
вторым запросом и сортировать в Python.

Два различия при этом сохранены, и оба — колонкой `source`:

* **разные пространства номеров.** У сокета `message_uid` это ULID кадра, у панели
  свой числовой номер. Номера панели пишутся с приставкой (`panel:36594474`), и
  столкнуться им негде;
* **импорт не запускает автоответ.** Автоответчик берёт последнее неотвеченное
  входящее по собеседнику, и привезённая история выглядит для него свежей почтой.
  Отбор автоответа исключает `source = 'panel'` — ответы на привезённое отдельная
  задача (`answer.py`).

## Что считается «нашей стороной»

Панель показывает обе стороны переписки строками вида «181685 Semen Gorbunkov».
Своя сторона узнаётся по `site_uid` анкеты (`profile_.profile`): совпал
получатель — сообщение входящее, совпал отправитель — исходящее. Не совпал никто —
строка не про наши анкеты, и мы её пропускаем: панель агентства показывает всех, а
заводим мы у себя не всех.
"""

import re
from datetime import datetime, timedelta

from datetime_.datetime_ import datetime_utc_now
from text_.text_emoji import text_emoji_same
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from src.dating.chat import chat_invites_to_history
from src.dating.feed import feed_write
from src.dating.people import people_name_case

from src.dating.message_twin import message_twin_absent
from src.dating.json_column import json_column_load
from src.dating.scenario import scenario_run
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_scenario_find

# Имя сценария у соседа. По имени, а не по номеру: номера у разных установок свои,
# а имя — то же правило, что у прочих сценариев (`people.py`).
CHAT_PANEL_SCENARIO = 'чат: сообщения из панели'

# Имя шага, под которым сценарий отдаёт строки таблицы.
CHAT_PANEL_FIELD = 'сообщения'

# Приставка к номеру сообщения. ⚠ Не украшение: `message_uid` общий с сокетом, и
# без неё числовой номер панели однажды сойдётся с чужим значением.
CHAT_PANEL_PREFIX = 'panel:'

# Сколько дней забираем по умолчанию. Неделя: панель отдаёт диапазон целиком, и
# запрашивать месяц ради вчерашних сообщений незачем — прогон стоит браузера.
CHAT_PANEL_DAYS = 7

# Формат даты в форме панели — `дд.мм.гггг`. Проверено на живой странице: этот же
# формат она принимает строкой запроса и им же заполняет поля.
CHAT_PANEL_DATE_FMT = '%d.%m.%Y'

# Предел диапазона в днях. Шире незачем: панель отдаёт таблицу целиком, и месяц
# переписки — это минуты прогона и десятки тысяч строк разбора.
CHAT_PANEL_DAYS_MAX = 60

# За сколько дней забирает **круг воркера**. Сутки, а не предел: круг идёт часто
# (по настройке сайта — от пяти минут), и брать неделю каждый раз значило бы
# привозить одно и то же по сотне раз. Историю забирают кнопкой, руками.
#
# ⚠ Сутки, а не «с прошлого раза»: панель отдаёт по датам, а не по времени, и
# сообщение, пришедшее в 00:05, при узком окне потерялось бы навсегда.
CHAT_PANEL_ROUND_DAYS = 1

# Пределы столбцов `message`. Режем при разборе, а не при записи: обрезанное
# молча — то же враньё, только позже.
CHAT_PANEL_UID_MAX = 32
CHAT_PANEL_NAME_MAX = 190
CHAT_PANEL_MSG_UID_MAX = 64

# Страница панели с журналом чата и её поля. ⚠ Путь тот же, на который ходил
# сценарий: разметка читается ручкой, а не браузером (`panel_fetch.py`).
CHAT_PANEL_PATH = 'userchatmessages'

# Ячейки таблицы по порядку. Панель отдаёт пять столбцов: ID, дата UTC, от, кому,
# текст. ⚠ Имена те же, что у сценария, — разбор строк (`chat_panel_rows`)
# общий на оба пути, и расходиться им нельзя.
CHAT_PANEL_CELLS = ('номер', 'когда', 'от', 'кому', 'текст')

# Какое сообщение считаем свежим. Час: круг ходит от пяти минут, и привезённое за
# последний час — это то, что человек и правда мог пропустить, пока не смотрел.
# Всё, что старше, — догон истории, а не событие.
CHAT_PANEL_FRESH_SEC = 3600

# Сколько разговоров отмечаем в ленте за один заход импорта. ⚠ Кольцо ленты
# тридцать строк **на все виды событий**: без потолка один заход вытеснил бы из
# неё и отправку, и ошибки.
CHAT_PANEL_TALKS_MAX = 5

# Номер собеседника из ячейки. Панель кладёт в неё ссылку с номером и имя рядом:
# «181685 Semen Gorbunkov». Номер берётся из отдельного поля сценария, а это —
# запасной путь, если ссылки в ячейке не окажется.
CHAT_PANEL_UID_RE = re.compile(r'\d+')


def chat_panel_rows(extract: dict, profiles: dict) -> list[dict]:
    """Строки панели — в записи `message`.

    Args:
        extract: `extract` прогона сценария.
        profiles: `site_uid` анкеты → её `id` у нас. По нему и определяется, какая
            сторона наша.

    Returns:
        list: записи `{profile_id, sender_uid, sender_name, text, message_uid,
        direction, sent_at}`. `sender_uid` — **собеседник**, а не автор: так же
        устроен сокет, и на этом держится группировка диалогов в карточке.

    ⚠ **`sender_uid` у исходящего — тоже собеседник.** Название столбца обманчиво,
    но менять его нельзя: по нему собран индекс диалога и группировка карточки
    (`profile_view.py`). Здесь это значит «с кем разговор», и исходящее
    сообщение попадает в тот же диалог, что и входящее.
    """
    rows = (extract or {}).get(CHAT_PANEL_FIELD)
    if not isinstance(rows, list):
        return []

    found, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue

        message_uid = _digits(row.get('номер'))
        if not message_uid or message_uid in seen:
            continue

        from_uid = _digits(row.get('от_номер')) or _digits(row.get('от'))
        to_uid = _digits(row.get('кому_номер')) or _digits(row.get('кому'))

        # Чья это переписка и в какую сторону. Обе стороны могут оказаться нашими
        # анкетами — панель показывает и такое; считаем это входящим для
        # получателя: именно ему сообщение и пришло.
        if to_uid in profiles:
            profile_id, direction = profiles[to_uid], 'in'
            partner_uid, partner_cell = from_uid, row.get('от')
        elif from_uid in profiles:
            profile_id, direction = profiles[from_uid], 'out'
            partner_uid, partner_cell = to_uid, row.get('кому')
        else:
            # Строка не про наши анкеты: панель агентства показывает всех, а
            # заводим мы у себя не всех. Это не ошибка разбора.
            continue

        text = _text(row.get('текст'), row.get('текст_подсказка'))
        if not text:
            continue

        seen.add(message_uid)
        found.append({
            'profile_id': profile_id,
            'sender_uid': str(partner_uid)[:CHAT_PANEL_UID_MAX],
            'sender_name': people_name_case(
                _name(partner_cell, partner_uid))[:CHAT_PANEL_NAME_MAX],
            'text': text,
            'message_uid': (CHAT_PANEL_PREFIX
                            + message_uid)[:CHAT_PANEL_MSG_UID_MAX],
            'direction': direction,
            'sent_at': _when(row.get('когда')),
        })

    return found


async def chat_panel_import(site_id: int, days: int) -> dict:
    """Забирает переписку сайта из панели и кладёт в базу.

    Общая на два вызова: ручку панели («Забрать сообщения») и круг воркера, который
    делает это сам по настройке сайта. ⚠ **Одной функцией, а не двумя**: разойдись
    они — и автоматический импорт однажды стал бы разбирать ответ иначе, чем
    ручной, а заметить это было бы нечем.

    Returns:
        dict: `status` (`ok` | `error`), `total` — сколько строк отдала панель,
        `ours` — сколько из них про наши анкеты, `added`/`same`/`contacts` —
        сколько записалось, `from`/`to` — за какой диапазон, `error` — причина
        отказа.

        ⚠ **Отказ здесь — значение, а не исключение.** Кругу воркера нельзя падать
        из-за того, что у сайта не заведён сценарий: он обходит все сайты подряд, и
        первый ненастроенный лишил бы импорта остальные. Ручка превращает эти
        причины в свои коды сама.
    """
    site_id = int(site_id or 0)
    days = int(days)

    empty = {'status': 'error', 'total': 0, 'ours': 0, 'added': 0, 'same': 0,
             'contacts': 0, 'from': '', 'to': ''}

    if not site_id:
        return {**empty, 'error': 'не выбран сайт'}

    if days < 1 or days > CHAT_PANEL_DAYS_MAX:
        return {**empty, 'error': f'диапазон от 1 до {CHAT_PANEL_DAYS_MAX} дней'}

    # Анкеты спрашиваем **до** прогона: без них разбирать нечего, и гонять браузер
    # сорок секунд ради ответа, который некуда положить, незачем.
    profiles = await chat_panel_profiles(site_id)
    if not profiles:
        return {**empty,
                'error': 'у сайта нет анкет с номером на сайте — сперва импортируйте анкеты'}

    today = datetime_utc_now()
    date_from = (today - timedelta(days=days)).strftime(CHAT_PANEL_DATE_FMT)
    date_to = today.strftime(CHAT_PANEL_DATE_FMT)

    # ⚠ **Панель настроена — читаем ручкой, а не сценарием.** Разница замерена:
    # 0,3 секунды против сорока, и главное — без вкладки Chromium у соседа. Круг
    # ходит от пяти минут, и сорок секунд браузера на заход означали бы, что пул
    # вкладок занят нами почти всё время (`panel_fetch.py`).
    #
    # ⚠ Это **не запасной путь, а выбор по настройке**. Сайт без `admin_url`
    # (панель не заведена) идёт сценарием, как и раньше, — и молчаливого перехода
    # с одного на другой нет: иначе сломанная сессия выглядела бы медленной
    # работой, а не поломкой.
    if await _panel_ready(site_id):
        return await _import_by_panel(site_id, profiles, date_from, date_to, empty)

    try:
        scenario = await rhapsody_api_scenario_find(site_id, CHAT_PANEL_SCENARIO)
    except RhapsodyError as e:
        return {**empty, 'error': str(e)}

    if not scenario:
        return {**empty, 'error': f'у сайта нет сценария «{CHAT_PANEL_SCENARIO}»'}

    # Общей дверью (`scenario_run`): она пишет прогон в журнал прогонов.
    # Анкеты у импорта нет — он идёт по сайту целиком, и приписывать прогон одной
    # из привезённых анкет было бы враньём.
    #
    # `account_id = 0` — «все анкеты»: панель так и подписывает первый пункт
    # селекта. Спрашивать по одной значило бы двадцать прогонов вместо одного.
    try:
        run = await scenario_run(
            scenario, site_id, 0, '',
            {'account_id': '0', 'date_from': date_from, 'date_to': date_to}, {},
            keep_session=False)
    except RhapsodyError as e:
        return {**empty, 'from': date_from, 'to': date_to, 'error': str(e)}

    if str(run.get('status')) == 'error':
        return {**empty, 'from': date_from, 'to': date_to,
                'error': f"сценарий встал на шаге {run.get('step_no')}: "
                         f"{run.get('error')}"}

    extract = run.get('extract') or {}
    rows = chat_panel_rows(extract, profiles)
    saved = await chat_panel_save(rows)

    return {'status': 'ok',
            'total': len(extract.get('сообщения') or []),
            'ours': len(rows),
            'from': date_from, 'to': date_to, 'error': '', **saved}


async def _panel_ready(site_id: int) -> bool:
    """Есть ли у сайта заведённая админ-панель. Она и решает, чем забирать."""
    from src.dating.panel import PANEL_URL_SETTING
    from src.uvicorn.routers.dating.site_ import site_setting

    return bool(await site_setting(site_id, PANEL_URL_SETTING))


async def _import_by_panel(site_id: int, profiles: dict, date_from: str, date_to: str,
                           empty: dict) -> dict:
    """Забор журнала чата ручкой панели: один GET и разбор таблицы.

    ⚠ **Своего входа не делает.** Сессию держит круг панели
    (`panel_round.py`), и вход отсюда означал бы два входа наперегонки,
    когда за таблицами придут двое. Протухшая сессия — отказ с причиной: круг
    поднимет её в свой срок, а мы скажем правду вместо тихой пустоты.
    """
    from src.dating.panel_fetch import (DatingPanelFetchError,
                                               panel_fetch_html,
                                               panel_fetch_table)

    # `AccountId=0` — «все анкеты»: панель так и подписывает первый пункт селекта.
    # Спрашивать по одной значило бы тринадцать запросов вместо одного.
    params = {'AccountId': '0',
              'DateFrom.DateValue': date_from,
              'DateTo.DateValue': date_to}

    try:
        html = await panel_fetch_html(site_id, CHAT_PANEL_PATH, params)
    except DatingPanelFetchError as e:
        return {**empty, 'from': date_from, 'to': date_to, 'error': str(e)}

    table = panel_fetch_table(html)
    rows = chat_panel_rows({CHAT_PANEL_FIELD: _cells(table)}, profiles)
    saved = await chat_panel_save(rows)

    return {'status': 'ok', 'total': len(table), 'ours': len(rows),
            'from': date_from, 'to': date_to, 'error': '', **saved}


def _cells(table: list) -> list[dict]:
    """Ячейки таблицы — в записи того же вида, что отдаёт сценарий.

    ⚠ Вид общий намеренно: разбор строк (`chat_panel_rows`) один на оба
    пути, и вторая его копия под ручку разошлась бы с первой на первой же правке.

    Номера сторон берём из **ссылки** ячейки: панель пишет туда «181685 Semen
    Gorbunkov», где номер — ссылка на карточку, а имя стоит рядом. Текст
    сообщения — из подсказки `title`, потому что в самой ячейке он обрезан
    шириной колонки.
    """
    out = []
    for row in table:
        one = {}
        for at, name in enumerate(CHAT_PANEL_CELLS):
            cell = row[at] if at < len(row) else {}
            one[name] = cell.get('текст', '')
            if name in ('от', 'кому'):
                one[f'{name}_номер'] = cell.get('ссылка', '')
            if name == 'текст':
                one['текст_подсказка'] = cell.get('подсказка', '')

        out.append(one)

    return out


async def chat_panel_profiles(site_id: int) -> dict:
    """`site_uid` анкет сайта → их номер у нас.

    Номер на сайте лежит в JSON-столбце `profile`, а не своей колонкой: так его
    кладёт импорт анкет (`import_.py`). Анкеты без него в отборе не
    участвуют — сопоставить их строкам панели нечем.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, profile FROM profile WHERE site_id = %s',
                         (int(site_id),))
        rows = await db.fetchall()

    found = {}
    for row in rows:
        site_uid = str((json_column_load(row['profile']) or {}).get('site_uid') or '').strip()
        if site_uid:
            found[site_uid] = int(row['id'])

    return found


async def chat_panel_save(rows: list) -> dict:
    """Кладёт сообщения и заводит недостающих собеседников.

    Returns:
        dict: `added` — сколько новых, `same` — сколько уже было, `contacts` —
        скольким собеседникам завели карточку.

    ⚠ **Повторы пропускаются молча** (`INSERT IGNORE` по паре анкета + номер).
    Импорт за пересекающиеся диапазоны — обычное дело: человек нажимает кнопку
    дважды, круг забирает неделю поверх вчерашней недели. Ловить это отказом
    значило бы запрещать нормальную работу.

    ⚠ **`is_read = 1` у привезённых.** Импорт это догон журнала, а не доставка:
    пометь мы недельную историю непрочитанной, панель зажглась бы счётчиками по
    всем диалогам разом, и человек не отличил бы новое от привезённого.

    Собеседник заводится, если его ещё нет: без карточки строка в диалогах
    осталась бы без имени и фотографии. Заводим только имя и номер — остальное
    (фото, возраст, город) панель не даёт, и выдумывать его нечем.
    """
    added, contacts, fresh = 0, 0, 0

    # Кладём по времени, от старых к свежим. Порядок показа от этого не зависит —
    # карточка чата сортирует по `created_at` (`profile_view.py`), — но
    # реплики, пришедшиеся на одну секунду, различаются только по `id`, и пусть он
    # идёт туда же, куда время: у панели время с точностью до секунды.
    rows = sorted(rows, key=lambda row: row['sent_at'] or '')

    # Пары «анкета — человек», у которых появилась свежая входящая реплика:
    # по ним поднимется значок у вкладки «Диалоги» (`_talks_feed`).
    talks: dict[tuple, str] = {}

    # ⚠⚠ **Пары, у которых разговора ещё не было** — им перенесём инвайты.
    #
    # Спрашиваем **до** вставки: после неё все пары выглядят говорившими, и
    # отличить начало разговора от продолжения будет нечем.
    fresh_pairs = await _talks_new(rows)

    async with mysql_get_db_async() as db:
        for row in rows:
            if row['sender_uid']:
                await db.execute(
                    'INSERT INTO contact (profile_id, uid, name) VALUES (%s, %s, %s)'
                    ' ON DUPLICATE KEY UPDATE name = IF(name = %s, VALUES(name), name)',
                    (row['profile_id'], row['sender_uid'], row['sender_name'], ''))
                # `rowcount` у вставки — 1, у обновления — 2, у «ничего не
                # изменилось» — 0. Считаем только заведённых.
                contacts += 1 if db.rowcount == 1 else 0

            # ⚠⚠ **`SELECT … WHERE NOT EXISTS`, а не `VALUES`** — и вот зачем.
            #
            # Наше исходящее уже лежит в таблице: его записала отправка
            # (`send.py`), своей строкой с ULID. Журнал сайта возвращает ту же
            # реплику со своим номером — `panel:52447706`, — и `INSERT IGNORE`
            # повтором её не считает: ключ `анкета + message_uid` у них разный.
            # Так в переписке появлялась вторая строка того же сообщения.
            #
            # ⚠ Собеседнику при этом уходило **одно** сообщение: у журнала сайта
            # двойников нет, двоилась только наша таблица.
            #
            # Проверка стоит **внутри вставки**, а не отдельным запросом: между
            # ними успевает пройти другой заход импорта, и дубль появился бы ровно
            # в ту щель, которую мы закрываем.
            #
            # `INSERT IGNORE` остаётся: он ловит **свой** повтор — тот же номер
            # панели из пересекающихся диапазонов, — а это другой случай.
            await db.execute(
                'INSERT IGNORE INTO message'
                ' (profile_id, sender_uid, sender_name, text, message_uid, direction,'
                '  is_read, source, created_at)'
                # Не разобралось время — ставим время импорта: потерять сообщение
                # из-за неожиданного формата даты хуже, чем показать его не тем часом.
                " SELECT %s, %s, %s, %s, %s, %s, 1, 'panel',"
                " COALESCE(NULLIF(%s, ''), NOW())"
                ' FROM DUAL WHERE 1 = 1'
                + message_twin_absent(row['direction']),
                (row['profile_id'], row['sender_uid'], row['sender_name'], row['text'],
                 row['message_uid'], row['direction'], row['sent_at'],
                 # Значения условия: анкета, собеседник, текст и время — дважды,
                 # на обе границы допуска. Направление в условие не идёт: от него
                 # зависит сам допуск, и оно вшито в текст запроса.
                 row['profile_id'], row['sender_uid'], row['text'],
                 row['sent_at'], row['sent_at']))
            if db.rowcount:
                added += 1
                # ⚠⚠ **Первый ответ — момент, когда приглашения становятся
                # диалогом.** Инвайты в переписку не пишутся при отправке: пока
                # человек молчит, разговора нет. Ответил — и последние из них
                # нужны, чтобы понять, **на что** он ответил.
                #
                # То же правило и тот же код, что у сокета (`chat.py`), и сюда его
                # пришлось добавить отдельно: у сайта без сокета — а это LadaDate,
                # весь наш прод — реплики приходят только этой дорогой, и перенос
                # не срабатывал никогда. На проде не нашлось **ни одного**
                # сообщения с `source = 'invite'`.
                pair = (int(row['profile_id']), str(row['sender_uid']))
                if row['direction'] == 'in' and pair in fresh_pairs:
                    fresh_pairs.discard(pair)
                    await chat_invites_to_history(db, pair[0], pair[1])
                # ⚠ Считаем **свежие отдельно**, и это не украшение отчёта.
                # «Привезено 5» значит «пять строк, каких у нас не было», а импорт
                # берёт сутки назад: почти всегда это старая переписка, которую мы
                # просто не видели. Назови её «новыми сообщениями» — и человек
                # пойдёт искать в диалогах то, чего там нет: на это и пожаловались,
                # открыв «Чат → Диалоги» и увидев реплики недельной давности.
                if _fresh(row.get('sent_at')):
                    fresh += 1
                    # ⚠ Пары копим **по свежим входящим**: по ним поднимется
                    # значок у вкладки «Диалоги» (см. ниже).
                    if row['direction'] == 'in' and row['sender_uid']:
                        talks[(int(row['profile_id']), str(row['sender_uid']))] = \
                            str(row['sender_name'] or row['sender_uid'])

        await db.connection.commit()

    await _talks_feed(talks)

    return {'added': added, 'fresh': fresh, 'same': len(rows) - added,
            'contacts': contacts}


async def _talks_new(rows: list) -> set:
    """Пары, у которых входящих реплик у нас ещё нет: им переносятся инвайты.

    ⚠ **Спрашиваем одним запросом на весь заход**, а не по паре: импорт привозит
    сотни строк, и вопрос «говорили ли уже» у них общий.

    ⚠ Сбой чтения отвечает пустым множеством — то есть «все пары уже говорили», и
    перенос не делается. Лишний перенос вклинил бы приглашения в середину давнего
    разговора; пропущенный стоит того, что автоответ увидит реплику без повода, и
    следующий импорт это исправит.
    """
    pairs = {(int(row['profile_id']), str(row['sender_uid']))
             for row in rows
             if row.get('direction') == 'in' and row.get('sender_uid')}
    if not pairs:
        return set()

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT DISTINCT profile_id, sender_uid FROM message'
                " WHERE direction = 'in' AND (%s)"
                % ' OR '.join(['(profile_id = %s AND sender_uid = %s)'] * len(pairs)),
                tuple(one for pair in pairs for one in pair))
            talked = {(int(row['profile_id']), str(row['sender_uid']))
                      for row in await db.fetchall() or []}
    except Exception as e:
        logger_info(f'[dating] начатые разговоры не спросились: {e}')

        return set()

    return pairs - talked


async def _talks_feed(talks: dict) -> None:
    """Отмечает в ленте разговоры, где только что появилась реплика.

    ## ⚠⚠ Зачем это здесь, а не только у сокета

    Значок у вкладки «Диалоги» поднимает событие `talk`, а писал его один лишь
    держатель сокетов (`chat._talk_new`). Сайтам без сокета — а это LadaDate,
    весь наш прод — реплики привозит **импорт из панели**, и значок у них не
    поднимался никогда: механизм был, кормить его было некому.

    Поймано глазами: в «Чат → Диалоги» появился ответ Семёна Горбункова, а полоса
    вкладок молчала.

    ## ⚠⚠ Почему не на всё привезённое

    Импорт берёт сутки назад и привозит сотни строк разом, включая переписку
    месячной давности: отметь мы каждую — лента заполнилась бы «новыми диалогами»
    годичной давности, а кольцо у неё тридцать строк на все виды событий.

    Поэтому отмечаются только **свежие** реплики (`_fresh`, час по времени сайта)
    и **по одной на пару**: пять реплик подряд от одного человека — это один
    разговор, о котором стоит сказать один раз.

    ⚠ **Потолок на заход.** Даже свежих пар бывает много (панель молчала час,
    потом отдала всё разом), а кольцо одно на все события: полтора десятка
    разговоров вытеснили бы из него отправку и ошибки.

    ⚠ Сбой ленты молчит: она подсказка, а не работа. Не записалось — реплики всё
    равно легли, и разговор виден на своей вкладке.
    """
    if not talks:
        return

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, site_id FROM profile WHERE id IN (%s)'
                % ', '.join(['%s'] * len({one[0] for one in talks})),
                tuple({one[0] for one in talks}))
            sites = {int(row['id']): int(row['site_id'])
                     for row in await db.fetchall() or []}

        for (profile_id, _), who in list(talks.items())[:CHAT_PANEL_TALKS_MAX]:
            site_id = sites.get(int(profile_id)) or 0
            if site_id:
                feed_write(site_id, 'talk', f'Новое в диалоге: {who}', profile_id)
    except Exception as e:
        logger_info(f'[dating] разговоры из панели не отметились: {e}')


# ── Приватное ────────────────────────────────────────────────────────────────

def _fresh(sent_at) -> bool:
    """Пришло ли сообщение только что — в пределах `CHAT_PANEL_FRESH_SEC`.

    ⚠ Считаем по времени **сайта** (`sent_at`), а не по времени импорта: повторный
    заход иначе делал бы недельную реплику свежей.

    ⚠⚠ **Это счёт для отчёта и значка, а не запрет отвечать.** Рядом когда-то стоял
    порог свежести у отбора автоответа — его убрали: ответ на реплику **один**
    (`is_answered` закрывает пару), и возраст повода отвечать не мешает. Здесь же
    свежесть нужна затем, чтобы «привезено 5» не читалось как «пять новых
    сообщений», когда это старая переписка, которую мы просто не видели.
    """
    if not sent_at:
        return False

    try:
        when = datetime.strptime(str(sent_at)[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False

    return (datetime_utc_now().replace(tzinfo=None) - when).total_seconds() \
        <= CHAT_PANEL_FRESH_SEC


def _digits(value) -> str:
    """Номер из ячейки: сама ссылка или первое число в тексте."""
    text = str(value or '').strip()
    if text.isdigit():
        return text

    match = CHAT_PANEL_UID_RE.search(text)

    return match.group(0) if match else ''


def _name(cell, uid: str) -> str:
    """Имя из ячейки «181685 Semen Gorbunkov» — без номера в начале.

    Режем по номеру, а не по первому пробелу: у имён пробелы бывают («Semen
    Gorbunkov»), и деление по пробелу оставило бы половину имени.
    """
    text = ' '.join(str(cell or '').split())
    if uid and text.startswith(uid):
        text = text[len(uid):]

    return text.strip()


def _text(body, title) -> str:
    """Текст сообщения: из ячейки или из её подсказки — что длиннее.

    ⚠ Сайт кладёт полную строку в `title`, а в теле она может быть обрезана
    вёрсткой. Какая длиннее, заранее не известно, и брать «правильную» наугад
    значило бы терять хвост длинных сообщений через раз.
    """
    body, title = str(body or '').strip(), str(title or '').strip()

    # ⚠⚠ Эмодзи к одному виду. Панель отдаёт их номером в пять шестнадцатеричных
    # цифр, а её разборщик читает четыре: 😉 приезжает парой «ὠ9». Текст при этом
    # не ломается — он просто становится другим, и наша же реплика, вернувшаяся
    # из журнала сайта, перестаёт совпадать с той, что мы отправили.
    return text_emoji_same(title if len(title) > len(body) else body)


def _when(value) -> str:
    """Время из панели. Она подписывает колонку «Date UTC» — значит перевод не нужен.

    Не разобралось — пусто, и запись ляжет с временем импорта: потерять сообщение
    из-за неожиданного формата даты хуже, чем показать его не тем часом.
    """
    text = ' '.join(str(value or '').split())
    if len(text) == 19 and text[4] == '-' and text[13] == ':':
        return text

    if text:
        logger_info(f'[dating] время сообщения панели не разобралось: {text[:40]!r}')

    return ''
