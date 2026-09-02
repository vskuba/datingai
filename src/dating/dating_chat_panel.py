"""Сообщения чата из панели агентства: журнал сайта в нашу базу.

У панели есть страница `userchatmessages` — список сообщений анкет за диапазон
дат: номер, время (UTC), кто, кому, текст. Сценарий у соседа (`чат: сообщения из
панели`) заходит туда и снимает таблицу; здесь — разбор и запись.

## Зачем это, если сообщения ловит сокет

`src/dating/dating_chat.py` держит вебсокет и складывает то, что приходит **пока
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
  задача (`dating_answer.py`).

## Что считается «нашей стороной»

Панель показывает обе стороны переписки строками вида «181685 Semen Gorbunkov».
Своя сторона узнаётся по `site_uid` анкеты (`dating_profile.profile`): совпал
получатель — сообщение входящее, совпал отправитель — исходящее. Не совпал никто —
строка не про наши анкеты, и мы её пропускаем: панель агентства показывает всех, а
заводим мы у себя не всех.
"""

import re
from datetime import timedelta

from datetime_.datetime_ import datetime_utc_now
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_json import dating_json_load
from src.dating.dating_scenario import dating_scenario_run
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_scenario_find

# Имя сценария у соседа. По имени, а не по номеру: номера у разных установок свои,
# а имя — то же правило, что у прочих сценариев (`dating_people.py`).
DATING_CHAT_PANEL_SCENARIO = 'чат: сообщения из панели'

# Имя шага, под которым сценарий отдаёт строки таблицы.
DATING_CHAT_PANEL_FIELD = 'сообщения'

# Приставка к номеру сообщения. ⚠ Не украшение: `message_uid` общий с сокетом, и
# без неё числовой номер панели однажды сойдётся с чужим значением.
DATING_CHAT_PANEL_PREFIX = 'panel:'

# Сколько дней забираем по умолчанию. Неделя: панель отдаёт диапазон целиком, и
# запрашивать месяц ради вчерашних сообщений незачем — прогон стоит браузера.
DATING_CHAT_PANEL_DAYS = 7

# Формат даты в форме панели — `дд.мм.гггг`. Проверено на живой странице: этот же
# формат она принимает строкой запроса и им же заполняет поля.
DATING_CHAT_PANEL_DATE_FMT = '%d.%m.%Y'

# Предел диапазона в днях. Шире незачем: панель отдаёт таблицу целиком, и месяц
# переписки — это минуты прогона и десятки тысяч строк разбора.
DATING_CHAT_PANEL_DAYS_MAX = 60

# За сколько дней забирает **круг воркера**. Сутки, а не предел: круг идёт часто
# (по настройке сайта — от пяти минут), и брать неделю каждый раз значило бы
# привозить одно и то же по сотне раз. Историю забирают кнопкой, руками.
#
# ⚠ Сутки, а не «с прошлого раза»: панель отдаёт по датам, а не по времени, и
# сообщение, пришедшее в 00:05, при узком окне потерялось бы навсегда.
DATING_CHAT_PANEL_ROUND_DAYS = 1

# Пределы столбцов `dating_message`. Режем при разборе, а не при записи: обрезанное
# молча — то же враньё, только позже.
DATING_CHAT_PANEL_UID_MAX = 32
DATING_CHAT_PANEL_NAME_MAX = 190
DATING_CHAT_PANEL_MSG_UID_MAX = 64

# Номер собеседника из ячейки. Панель кладёт в неё ссылку с номером и имя рядом:
# «181685 Semen Gorbunkov». Номер берётся из отдельного поля сценария, а это —
# запасной путь, если ссылки в ячейке не окажется.
DATING_CHAT_PANEL_UID_RE = re.compile(r'\d+')


def dating_chat_panel_rows(extract: dict, profiles: dict) -> list[dict]:
    """Строки панели — в записи `dating_message`.

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
    (`dating_profile_view.py`). Здесь это значит «с кем разговор», и исходящее
    сообщение попадает в тот же диалог, что и входящее.
    """
    rows = (extract or {}).get(DATING_CHAT_PANEL_FIELD)
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
            'sender_uid': str(partner_uid)[:DATING_CHAT_PANEL_UID_MAX],
            'sender_name': _name(partner_cell, partner_uid)[:DATING_CHAT_PANEL_NAME_MAX],
            'text': text,
            'message_uid': (DATING_CHAT_PANEL_PREFIX
                            + message_uid)[:DATING_CHAT_PANEL_MSG_UID_MAX],
            'direction': direction,
            'sent_at': _when(row.get('когда')),
        })

    return found


async def dating_chat_panel_import(site_id: int, days: int) -> dict:
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

    if days < 1 or days > DATING_CHAT_PANEL_DAYS_MAX:
        return {**empty, 'error': f'диапазон от 1 до {DATING_CHAT_PANEL_DAYS_MAX} дней'}

    # Анкеты спрашиваем **до** прогона: без них разбирать нечего, и гонять браузер
    # сорок секунд ради ответа, который некуда положить, незачем.
    profiles = await dating_chat_panel_profiles(site_id)
    if not profiles:
        return {**empty,
                'error': 'у сайта нет анкет с номером на сайте — сперва импортируйте анкеты'}

    try:
        scenario = await rhapsody_api_scenario_find(site_id, DATING_CHAT_PANEL_SCENARIO)
    except RhapsodyError as e:
        return {**empty, 'error': str(e)}

    if not scenario:
        return {**empty, 'error': f'у сайта нет сценария «{DATING_CHAT_PANEL_SCENARIO}»'}

    today = datetime_utc_now()
    date_from = (today - timedelta(days=days)).strftime(DATING_CHAT_PANEL_DATE_FMT)
    date_to = today.strftime(DATING_CHAT_PANEL_DATE_FMT)

    # Общей дверью (`dating_scenario_run`): она пишет прогон в журнал прогонов.
    # Анкеты у импорта нет — он идёт по сайту целиком, и приписывать прогон одной
    # из привезённых анкет было бы враньём.
    #
    # `account_id = 0` — «все анкеты»: панель так и подписывает первый пункт
    # селекта. Спрашивать по одной значило бы двадцать прогонов вместо одного.
    try:
        run = await dating_scenario_run(
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
    rows = dating_chat_panel_rows(extract, profiles)
    saved = await dating_chat_panel_save(rows)

    return {'status': 'ok',
            'total': len(extract.get('сообщения') or []),
            'ours': len(rows),
            'from': date_from, 'to': date_to, 'error': '', **saved}


async def dating_chat_panel_profiles(site_id: int) -> dict:
    """`site_uid` анкет сайта → их номер у нас.

    Номер на сайте лежит в JSON-столбце `profile`, а не своей колонкой: так его
    кладёт импорт анкет (`dating_import.py`). Анкеты без него в отборе не
    участвуют — сопоставить их строкам панели нечем.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, profile FROM dating_profile WHERE site_id = %s',
                         (int(site_id),))
        rows = await db.fetchall()

    found = {}
    for row in rows:
        site_uid = str((dating_json_load(row['profile']) or {}).get('site_uid') or '').strip()
        if site_uid:
            found[site_uid] = int(row['id'])

    return found


async def dating_chat_panel_save(rows: list) -> dict:
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
    added, contacts = 0, 0

    # Кладём по времени, от старых к свежим. Порядок показа от этого не зависит —
    # карточка чата сортирует по `created_at` (`dating_profile_view.py`), — но
    # реплики, пришедшиеся на одну секунду, различаются только по `id`, и пусть он
    # идёт туда же, куда время: у панели время с точностью до секунды.
    rows = sorted(rows, key=lambda row: row['sent_at'] or '')

    async with mysql_get_db_async() as db:
        for row in rows:
            if row['sender_uid']:
                await db.execute(
                    'INSERT INTO dating_contact (profile_id, uid, name) VALUES (%s, %s, %s)'
                    ' ON DUPLICATE KEY UPDATE name = IF(name = %s, VALUES(name), name)',
                    (row['profile_id'], row['sender_uid'], row['sender_name'], ''))
                # `rowcount` у вставки — 1, у обновления — 2, у «ничего не
                # изменилось» — 0. Считаем только заведённых.
                contacts += 1 if db.rowcount == 1 else 0

            await db.execute(
                'INSERT IGNORE INTO dating_message'
                ' (profile_id, sender_uid, sender_name, text, message_uid, direction,'
                '  is_read, source, created_at)'
                # Не разобралось время — ставим время импорта: потерять сообщение
                # из-за неожиданного формата даты хуже, чем показать его не тем часом.
                " VALUES (%s, %s, %s, %s, %s, %s, 1, 'panel',"
                " COALESCE(NULLIF(%s, ''), NOW()))",
                (row['profile_id'], row['sender_uid'], row['sender_name'], row['text'],
                 row['message_uid'], row['direction'], row['sent_at']))
            added += 1 if db.rowcount else 0

        await db.connection.commit()

    return {'added': added, 'same': len(rows) - added, 'contacts': contacts}


# ── Приватное ────────────────────────────────────────────────────────────────

def _digits(value) -> str:
    """Номер из ячейки: сама ссылка или первое число в тексте."""
    text = str(value or '').strip()
    if text.isdigit():
        return text

    match = DATING_CHAT_PANEL_UID_RE.search(text)

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

    return title if len(title) > len(body) else body


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
