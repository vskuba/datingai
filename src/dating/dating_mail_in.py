"""Входящие письма анкеты — через API сайта, без браузера и без соседа.

У сайта две ручки, и они делят работу пополам:

| Что | Ручка | Что отдаёт |
|-----|-------|-----------|
| список | `GET /messages?startDate=…` | номера, отправители, даты — **без текста** |
| одно письмо | `GET /messages/{id}` | то же плюс `Body` и вложение |

## ⚠ Два запроса, и второй — на каждое новое письмо

Текста в списке нет. Сорок писем — сорок запросов за телами; спрашивай мы их при
каждом открытии вкладки, страница открывалась бы полминуты. Поэтому забираем один
раз и храним у себя (`dating_mail_in`), а тела берём **только у новых**: письмо,
которое уже лежит, второй раз не спрашиваем.

## ⚠ «Прочитано» — наш признак, а не сайта

У сайта его нет вовсе. Есть `IsReplied` — «ответили ли», и это другой вопрос: письмо
можно прочесть и не ответить, и таким оно для нас уже не новое. Поэтому `seen` живёт
в нашей таблице и ставится, когда человек открыл письмо в панели.

## С какого числа спрашиваем

`startDate` по умолчанию у сайта — **сегодняшняя полночь**, то есть вчерашние письма
он не отдаст вовсе. Спрашиваем от последнего, что у нас есть, минус запас: письмо
могло прийти в ту же секунду, что и прошлый заход, и без запаса оно потерялось бы
навсегда. Ничего нет — берём за неделю: столько живёт интерес к первому письму.
"""

from datetime import datetime, timedelta

from datetime_.datetime_ import datetime_utc_now
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_people import DATING_PEOPLE_NAME_MAX, DATING_PEOPLE_UID_MAX
from src.dating.dating_feed import dating_feed_write
from src.dating.dating_favorite import (DATING_FAVORITE_STATES,
                                        DATING_FAVORITE_STATES_SQL)
from src.dating.dating_request import DATING_REQUEST_MAIL
from src.dating.dating_site_api import DatingSiteApiError, dating_site_api_call

# Что зовём. Пути из документации сайта.
DATING_MAIL_IN_LIST_PATH = '/messages'
DATING_MAIL_IN_ONE_PATH = '/messages/{message_id}'

# Имена полей в ответе сайта.
DATING_MAIL_IN_ROWS = 'Messages'
DATING_MAIL_IN_ID = 'MessageId'
DATING_MAIL_IN_DATE = 'DatePost'
DATING_MAIL_IN_USER = 'User'
DATING_MAIL_IN_BODY = 'Body'
DATING_MAIL_IN_REPLIED = 'IsReplied'

# Поля отправителя — те же имена, что в списках собеседников.
DATING_MAIL_IN_USER_FIELDS = {'uid': 'AccountId', 'name': 'Name', 'age': 'Age',
                              'photo': 'ProfilePhoto', 'city': 'City',
                              'country': 'Country'}

# Сколько знаков текста храним. Письма на сайте короткие; предел здесь затем, чтобы
# чужой сбой не положил в базу мегабайт.
DATING_MAIL_IN_BODY_MAX = 20000

# Какое письмо считаем свежим. Час: круг ходит от двух минут, и пришедшее за
# последний час — то, что человек и правда мог пропустить. Старше — догон истории,
# и назвать его новым значит послать человека искать во входящих позавчерашнее.
DATING_MAIL_IN_FRESH_SEC = 3600

# Запас назад от последнего известного письма. Минута: письмо могло прийти в ту же
# секунду, что и прошлый заход, и без запаса оно не попало бы ни в тот, ни в этот.
DATING_MAIL_IN_OVERLAP_MIN = 1

# За сколько дней спрашиваем, когда у анкеты нет ни одного письма. Неделя: столько
# живёт интерес к первому письму, а глубже сайт всё равно отдаёт неохотно.
DATING_MAIL_IN_FIRST_DAYS = 7

# Сколько тел забираем за один заход. Каждое — отдельный запрос; полсотни писем это
# полсотни запросов, и на плохой день сайта заход растянулся бы на минуты.
DATING_MAIL_IN_BODY_LIMIT = 40


class DatingMailInError(RuntimeError):
    """Письма не забрались. Текст годен для страницы и журнала анкеты."""


async def dating_mail_in_fetch(profile: dict, base: str, password: str) -> dict:
    """Забирает новые входящие письма анкеты и кладёт их к нам.

    Args:
        profile: строка `dating_profile`: нужны `id`, `site_id`, `login`, `password`,
            `password_source`.
        base: корень API сайта (настройка `api_base`).
        password: общий пароль анкет сайта (настройка `api_password`).

    Returns:
        dict: `found` — сколько писем отдал сайт, `added` — сколько легло впервые,
        `fresh` — сколько из них пришли за последний час, `bodies` — скольким
        забрали текст.

        ⚠ `added` и `fresh` — **разные числа**: первое про нашу базу («таких у нас
        не было»), второе про время сайта («пришло только что»). Забор берёт от
        последнего известного минус запас, а первый заход — за неделю, и почти
        всегда `added` больше.

    Raises:
        DatingMailInError: сайт не ответил или отказал.

    ⚠ **Тела берём только у новых.** Письмо, которое уже лежит, второй раз не
    спрашиваем: его текст не меняется, а запрос стоит похода к сайту.
    """
    from src.dating.dating_ready import dating_password_pick

    profile_id = int(profile.get('id') or 0)
    site_id = int(profile.get('site_id') or 0)

    if not str(base or '').strip():
        raise DatingMailInError('у сайта не задан адрес API '
                                '(настройка сайта «Адрес API сайта»)')

    secret = dating_password_pick(profile, password)
    since = await _since(profile_id)

    try:
        body = await dating_site_api_call(
            profile, base, secret,
            f'{DATING_MAIL_IN_LIST_PATH}?startDate={since}', method='GET',
            door=DATING_REQUEST_MAIL)
    except DatingSiteApiError as e:
        raise DatingMailInError(f'письма не забрались: {e}')

    rows = body.get(DATING_MAIL_IN_ROWS)
    if not isinstance(rows, list):
        return {'found': 0, 'added': 0, 'fresh': 0, 'bodies': 0}

    known = await _known(profile_id)
    added = bodies = fresh = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        message_id = _int(row.get(DATING_MAIL_IN_ID))
        if not message_id:
            continue

        # ⚠ Уже лежит — обновляем только `IsReplied`: он меняется на сайте, когда
        # там ответили, а прочее у письма неизменно.
        if message_id in known:
            await _touch_replied(profile_id, message_id,
                                 bool(row.get(DATING_MAIL_IN_REPLIED)))
            continue

        text = ''
        if bodies < DATING_MAIL_IN_BODY_LIMIT:
            text = await _body(profile, base, secret, message_id)
            bodies += 1

        await _put(site_id, profile_id, message_id, row, text)
        await _talk_new(site_id, profile_id, row)
        added += 1
        # ⚠ **«Привезено» и «свежих» — разные числа.** Забор берёт письма от
        # последнего известного минус запас, а первый заход — за несколько суток:
        # «новых 5» почти всегда значит пять писем, каких у нас не было, а не пять
        # только что пришедших. Разницу человек видит сразу — открывает входящие и
        # находит там позавчерашнее.
        if _fresh(row.get(DATING_MAIL_IN_DATE)):
            fresh += 1

    if added:
        logger_info(f'[dating] входящие анкеты #{profile_id}: новых {added} '
                    f'из {len(rows)}')

    return {'found': len(rows), 'added': added, 'fresh': fresh, 'bodies': bodies}


async def dating_mail_in_unread(site_id: int) -> dict:
    """Сколько непросмотренных писем у каждой анкеты сайта и **где они лежат**.

    Returns:
        dict: `{profile_id: {'all': сколько, 'fav': сколько из них от избранных}}`.
        Анкет без новых писем в нём нет вовсе — страница рисует значок только тем,
        у кого он есть.

    ⚠ **`fav` нужен не для счёта, а для перехода.** Письмо от избранного
    показывается только на «Фавориты → Диалоги», от прочих — только на «Письма →
    Входящие» (`api.md`, §3.7.4). Значок в списке анкет вёл всегда во «Входящие», и
    для письма от избранного это был переход в пустоту: человек щёлкал по конверту
    с числом и попадал на «писем пока нет».

    Отдельной функцией и одним запросом: значок стоит в списке анкет, и спрашивать
    по анкете значило бы двадцать запросов на каждую отрисовку списка.
    """
    from src.dating.dating_favorite import DATING_FAVORITE_STATES, DATING_FAVORITE_STATES_SQL

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.profile_id, COUNT(*) AS n,'
            '       SUM(EXISTS (SELECT 1 FROM favorite f'
            '                    WHERE f.profile_id = m.profile_id AND f.uid = m.uid'
            f'                     AND f.state IN ({DATING_FAVORITE_STATES_SQL}))) AS fav'
            '  FROM mail_in m'
            ' WHERE m.site_id = %s AND m.seen = 0 GROUP BY m.profile_id',
            (*DATING_FAVORITE_STATES, int(site_id)))
        rows = await db.fetchall()

    return {int(row['profile_id']): {'all': int(row['n']),
                                     'fav': int(row['fav'] or 0)}
            for row in rows or []}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _since(profile_id: int) -> str:
    """С какого времени спрашивать письма — в виде, который принимает сайт.

    ⚠ **От последнего известного минус запас**, а не «от сегодня»: у сайта
    `startDate` по умолчанию — сегодняшняя полночь, и вчерашние письма он не отдаст
    вовсе. Запас нужен на письмо, пришедшее в ту же секунду, что и прошлый заход.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT MAX(date_post) AS last FROM mail_in'
                         ' WHERE profile_id = %s', (int(profile_id),))
        row = await db.fetchone()

    last = (row or {}).get('last')
    since = (last - timedelta(minutes=DATING_MAIL_IN_OVERLAP_MIN)) if last \
        else (datetime_utc_now() - timedelta(days=DATING_MAIL_IN_FIRST_DAYS))

    return since.strftime('%Y-%m-%dT%H:%M:%S')


async def _known(profile_id: int) -> set:
    """Номера писем, которые уже лежат у нас."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT message_id FROM mail_in'
                         ' WHERE profile_id = %s', (int(profile_id),))
        rows = await db.fetchall()

    return {int(row['message_id']) for row in rows or []}


async def _body(profile: dict, base: str, secret: str, message_id: int) -> str:
    """Текст одного письма. Не вышло — пустая строка, а не отказ всему заходу.

    ⚠ **Одно неудачное тело не должно ронять весь забор.** Письмо ляжет без текста
    и будет видно в списке; текст доедет следующим заходом, а до тех пор человек
    хотя бы знает, что письмо есть.
    """
    try:
        answer = await dating_site_api_call(
            profile, base, secret,
            DATING_MAIL_IN_ONE_PATH.format(message_id=int(message_id)), method='GET',
            door=DATING_REQUEST_MAIL)
    except DatingSiteApiError as e:
        logger_info(f'[dating] тело письма #{message_id} не забралось: {e}')

        return ''

    return str(answer.get(DATING_MAIL_IN_BODY) or '')[:DATING_MAIL_IN_BODY_MAX]


async def _put(site_id: int, profile_id: int, message_id: int, row: dict,
               text: str) -> None:
    """Кладёт письмо. `INSERT IGNORE` ловит гонку двух заходов разом."""
    user = row.get(DATING_MAIL_IN_USER)
    user = user if isinstance(user, dict) else {}
    f = DATING_MAIL_IN_USER_FIELDS

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO mail_in'
            ' (site_id, profile_id, message_id, uid, name, photo, age, city,'
            '  country, body, date_post, is_replied)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (int(site_id), int(profile_id), int(message_id),
             str(user.get(f['uid']) or '')[:DATING_PEOPLE_UID_MAX],
             str(user.get(f['name']) or '')[:DATING_PEOPLE_NAME_MAX],
             str(user.get(f['photo']) or '')[:500],
             _int(user.get(f['age'])),
             str(user.get(f['city']) or '')[:DATING_PEOPLE_NAME_MAX],
             str(user.get(f['country']) or '')[:DATING_PEOPLE_NAME_MAX],
             str(text or ''),
             _stamp(row.get(DATING_MAIL_IN_DATE)),
             1 if row.get(DATING_MAIL_IN_REPLIED) else 0))
        await db.connection.commit()


async def _talk_new(site_id: int, profile_id: int, row: dict) -> None:
    """Отмечает в ленте начало переписки с избранным: он написал впервые.

    ⚠ **Только избранные, и только первое письмо.** Вкладка «Фавориты → Диалоги»
    показывает переписку с ними, и счётчик у её названия отвечает на один вопрос:
    «кто-то из них заговорил?». Письмо от незнакомца — работа «Писем → Входящих»,
    и там свой вид события.

    ⚠ **Первым считается письмо, после которого писем стало одно.** Спросить «есть
    ли прежние» **до** записи здесь нельзя: `_put` уже отработал, а вставлял он
    `INSERT IGNORE` — повтор мы бы приняли за первое слово. Поэтому вопрос задаётся
    после и считает строки.

    Сбой ленты молчит: она подсказка, а не работа.
    """
    user = row.get(DATING_MAIL_IN_USER)
    user = user if isinstance(user, dict) else {}
    uid = str(user.get(DATING_MAIL_IN_USER_FIELDS['uid']) or '')
    if not uid:
        return

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM mail_in'
                             ' WHERE profile_id = %s AND uid = %s',
                             (int(profile_id), uid))
            if int(((await db.fetchone()) or {}).get('n') or 0) != 1:
                return

            # ⚠ Тем же набором состояний, каким их отбирает сама вкладка: возьми
            # мы шире, счётчик считал бы разговор, которого в списке нет.
            await db.execute(
                'SELECT 1 FROM favorite WHERE profile_id = %s AND uid = %s'
                f'   AND state IN ({DATING_FAVORITE_STATES_SQL}) LIMIT 1',
                (int(profile_id), uid, *DATING_FAVORITE_STATES))
            if not await db.fetchone():
                return

        who = str(user.get(DATING_MAIL_IN_USER_FIELDS['name']) or '').strip() or uid
        dating_feed_write(int(site_id), 'favtalk', f'Новый диалог: {who}',
                          int(profile_id))
    except Exception as e:
        logger_info(f'[dating] начало переписки с избранным не отметилось: {e}')


async def _touch_replied(profile_id: int, message_id: int, replied: bool) -> None:
    """Обновляет «ответили ли» у письма, которое уже лежит.

    ⚠ Только это поле: прочее у письма неизменно, а `seen` — наш признак, и сайт про
    него ничего не знает. Затри мы его ответом сайта — прочитанное письмо снова
    считалось бы новым.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE mail_in SET is_replied = %s'
            ' WHERE profile_id = %s AND message_id = %s AND is_replied <> %s',
            (1 if replied else 0, int(profile_id), int(message_id),
             1 if replied else 0))
        await db.connection.commit()


def _fresh(value) -> bool:
    """Пришло ли письмо только что — в пределах `DATING_MAIL_IN_FRESH_SEC`.

    ⚠ По времени **сайта** (`DatePost`), а не по времени забора: письмо, привезённое
    сейчас, могло быть написано позавчера, и считать его свежим значило бы обещать
    во входящих то, чего человек там не увидит.
    """
    raw = _stamp(value)
    if not raw:
        return False

    try:
        when = datetime.strptime(raw, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False

    return (datetime_utc_now().replace(tzinfo=None) - when).total_seconds() \
        <= DATING_MAIL_IN_FRESH_SEC


def _stamp(value) -> str:
    """Время сайта в вид MySQL. Не разобралось — `None`: пустая дата честнее
    выдуманной, а «сейчас» подставило бы письму чужой возраст."""
    raw = str(value or '').strip().replace('T', ' ')
    raw = raw.rstrip('Z').split('.')[0].split('+')[0].strip()

    return raw[:19] if len(raw) >= 19 else None


def _int(value) -> int:
    """Число из чужого ответа. Мусор — ноль: сайт вправе прислать что угодно."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
