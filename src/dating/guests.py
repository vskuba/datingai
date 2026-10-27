"""Гости: кто заходил на анкету — наполнение ленты «Гости».

Лента собрана из таблицы `companion_guest`: строка — факт «человек N заходил к
анкете M сайта S». Карточка человека при этом живёт в `companion` (одна на сайт),
здесь только визиты. Круг снятия — `guests_round.py`; этот файл — дверь: записать
визиты, собрать ленту, сказать, включена ли она у сайта.

## ⚠⚠ Анкеты берутся **все**, включая выключенные

Витрина выключенной анкеты стоит на сайте, и гости у неё бывают; круг снимает
гостей и у спящих анкет — автовход кабинета агентства работает под любой
учёткой, своей сессии выключенной анкете не требуется.

**Съём — про сайт, показ — про выбранную анкету.** На странице, где выбрана
анкета, лента показывает гостей **её** витрины: «ГОСТИ» рядом с Данкой — это те,
кто заходил к Данке, а не к агентству вообще. Без выбранной анкеты (и у круга,
и у ручки по умолчанию) лента общая — все визиты сайта. Порядок и
смысл строк от фильтра не меняются: меняется только отбор.

Это же правило, почему у ленты нет отказа «сайт не в сети»: данные лежат в нашей
базе, и отдаём их мы независимо от того, горит ли сейчас хоть одна анкета.

## ⚠ Имени и фото здесь нет

Держит их `companion`, куда круг кладёт карточку дверью `companion_save`.
Дублировать в ленту значило бы иметь два ответа на «как его зовут».
"""

import re
from datetime import date, timedelta

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.companion_card import companion_save
from src.dating.people import people_row_card, people_rows
from src.dating.scenario_field import scenario_field
from src.dating.site_setting import site_boxes

# Имя сценария у соседа и имя шага съёма. `scenario_find` допустимо и «имя», и
# «имя (Метка)» — второе заведено конвенцией, когда голое имя занято; здесь
# голое, сосед сам разберётся, под каким именем он его прячет.
GUESTS_SCENARIO = 'гости: список'

# Имя шага-съёма в прогоне — латиницей, как требуют правила переезда имён
# (`scenario_field.py`); кириллический вариант подставит маппинг.
GUESTS_FIELD = 'guests'

# Потолок ленты на руки: карусель онлайна держала по две сотни карточек, и
# больше в прокручиваемый ряд не влезает по-человечески.
GUESTS_FEED_MAX = 200

# Строка настройки-выключателя: галочка «Гости» в «Активных лентах» сайта.
GUESTS_FEED_TOKEN = 'guests'

# ── Разбор даты со страницы гостей ───────────────────────────────────────────

# Даты страница подписывает словами и числами по-разному: «17 окт 2026 19:42»,
# «Вчера», «3 дн. назад», «07.10.2026». Разбираем и русское, и английское
# написание: сайты отдают что угодно обоими.
#
# ⚠ Списком пар, а не словарём: ключи словаря — имена по правилу именования
# (§1.2), а эти слова — **данные страницы**, они и в тексте кода остаются
# словами-строками.
_GUESTS_MONTHS = (('янв', 1), ('фев', 2), ('мар', 3), ('апр', 4),
                  ('мая', 5), ('май', 5), ('июн', 6), ('июл', 7),
                  ('авг', 8), ('сен', 9), ('окт', 10), ('ноя', 11),
                  ('дек', 12),
                  ('jan', 1), ('feb', 2), ('mar', 3), ('apr', 4), ('may', 5),
                  ('jun', 6), ('jul', 7), ('aug', 8), ('sep', 9), ('oct', 10),
                  ('nov', 11), ('dec', 12))

# «17 окт 2026 19:42» — и месяц точкой («окт.»), и без.
_GUESTS_MONTH_RE = re.compile(r'(\d{1,2})\s+([а-яёa-z]{3,4})\.?\s+(\d{4})')

# «07.10.2026», «07.10.26» — год может быть и двузначным. У Step2Love та же
# дата пишется тире («27-10-2026») — формат живёт в тексте карточки.
_GUESTS_NUM_RE = re.compile(r'^(\d{1,2})[.](\d{1,2})[.](\d{2,4})')
_GUESTS_DASH_RE = re.compile(r'^(\d{1,2})-(\d{1,2})-(\d{2,4})')

# «3 дн. назад», «5 days ago» — срок, а не дата.
_GUESTS_DAYS_RE = re.compile(r'(\d{1,4})\s*(?:дн|дней|день|day|days)',
                             re.IGNORECASE)


def guests_visit_day(text: str) -> date | None:
    """Дата визита словами со страницы — в дату. Не понял — `None`, а не догадка.

    ⚠ **Пустота и догадка — разные ответы.** Возврат — дата или `None`: столбец
    `visit_at` для непонятного стоит NULL, то есть «не знаем», и врать нулевой
    датой («1 января 1970» в ленте) незачем.

    ⚠ Относительные сроки («3 дн.») считаются от **сегодня**. Они и сняты
    сегодня: прогон живёт минуты, и расхождение на сутки означало бы лишь то,
    что круг ошибся порядком, а не тем, что гость был три дня назад.
    """
    said = ' '.join(str(text or '').split()).lower()
    if not said:
        return None

    today = date.today()

    if 'позавчера' in said:
        return today - timedelta(days=2)

    if 'вчера' in said or 'yesterday' in said:
        return today - timedelta(days=1)

    if said in ('сегодня', 'today', 'now', 'сейчас'):
        return today

    match = _GUESTS_MONTH_RE.search(said)
    if match:
        month = next((number for word, number in _GUESTS_MONTHS
                      if match.group(2).rstrip('.') == word), 0)
        if month:
            return _safe_date(int(match.group(3)), month, int(match.group(1)))

    match = _GUESTS_NUM_RE.match(said) or _GUESTS_DASH_RE.match(said)
    if match:
        year = int(match.group(3))
        return _safe_date(year + 2000 if year < 100 else year,
                         int(match.group(2)), int(match.group(1)))

    match = _GUESTS_DAYS_RE.search(said)
    if match:
        return today - timedelta(days=int(match.group(1)))

    return None


def guests_rows(extract: dict, field: str = GUESTS_FIELD) -> list[dict]:
    """Строки съёма — карточками с датами визита.

    Args:
        extract: `extract` прогона сценария «гости: список».
        field: имя шага-съёма в прогоне; сайту с другим именем его задаёт
            драйвер (`site/site_guests.py`), а не правка здесь.

    Returns:
        list: те же карточки, что отдаёт `people_rows` (`uid`, `name`, `age`,
        `photo`, …), и у каждой `visit` — дата или `None`. Порядок — как на
        странице: гости отсортированы по свежести, и порядок этот мы храним.

    ⚠ Разбор карточек **не свой**, а общий со всеми лентами: и `people_rows`, и
    наша таблица дат строятся одним разбором строки (`people_row_card`) — иначе
    номер, по которому дата притягивается к карточке, считался бы в двух местах
    по-разному, а это ровно та грабля, что уже стоила нам потерянных строк.

    ⚠ Дату берёт **первая** строка с этим номером: `people_rows` из близнецов
    оставляет первую, и дата обязана быть её же, а не последней копии.
    """
    visits = {}
    for row in scenario_field(extract, field) or []:
        card = people_row_card(row)
        if card and card['uid'] not in visits:
            visits[card['uid']] = guests_visit_day(scenario_field(row, 'when'))

    return [{**one, 'visit': visits.get(one['uid'])}
            for one in people_rows(extract, field)]


async def guests_feed_on(site_id: int) -> bool:
    """Включена ли лента «Гости» у сайта — галочка в «Активных лентах»."""
    return GUESTS_FEED_TOKEN in await site_boxes(int(site_id), 'mailing_feeds')


async def guests_site_profiles(site_id: int) -> list[dict]:
    """Анкеты сайта, чьих гостей снимаем: **все**, включая выключенные.

    ⚠ Отбора по `enabled` здесь нет нарочно — см. заголовок файла. Смысл ленты в
    том, чтобы показать гостей всего агентства, а выключенная анкета гостей не
    отменяет.

    ⚠ Роль — женская: гостиная стоит в женском кабинете, и автовход агентства
    (`my_profiles.php`) ведёт в него; мужские учётки — другое приложение и
    другой сценарий.

    ⚠ Анкета без логина не обходится вовсе: автовход ищет её по логину, и
    сценарий встал бы на первом же шаге.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, login, proxy_id FROM profile'
                " WHERE site_id = %s AND login <> '' AND role = 'lady'"
                ' ORDER BY id',
                (int(site_id),))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] анкеты для гостей сайта #{site_id} '
                    f'не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def guests_seen(site_id: int, profile_id: int,
                      rows: list[dict]) -> int:
    """Записывает визиты одной анкеты. Возвращает, сколько строк легло.

    Args:
        rows: карточки `guests_rows` — с `uid`, `name`, `photo`, `age`, `visit`.

    ## Что делает и почему так

    Заводит карточки (`companion_save`) и кладёт строки визитов одним запросом:
    повторный визит обновляет дату, а не заводит второго близнеца — гонку двух
    заходов отпускает ключ `uq_companion_guest`.

    ⚠ **Карточке отдаём только непустое.** `companion_save` перезаписывает
    названные поля, и пустое имя с голой страницы стёрло бы настоящее, снятое
    раньше с другой ленты.

    ⚠ **Дату не выдумываем:** `COALESCE(VALUES(visit_at), visit_at)` — новая
    пустота не затирает известную прежнюю дату. Гость без даты — гость без даты,
    а не гость «никогда».

    ⚠ **Время подтверждения двигаем всегда.** `created_at` — «когда мы это
    узнали», и по нему лента отвечает возрастом. Гость, которого сегодня снова
    видно на странице, подтверждён сегодня — даже если сам визит не нов: те же
    лица, подтверждаемые кругом раз за разом, не обязаны выглядеть гостями
    недельной давности. Пропавший из выборки — не касался ключа, его строка и
    дальше стареет честно.

    ⚠ Здесь `CURRENT_TIMESTAMP`, а не `DEFAULT(created_at)`: у колонки стоит
    именно такой default, но в части `ON DUPLICATE KEY UPDATE` MySQL 8 так
    его и не вычисляет — probe по живой базе отвечает нулевой датой
    «0000-00-00», то есть в ленте поселился бы гость из начала времён.
    """
    rows = [one for one in rows if str(one.get('uid') or '').strip()]
    if not rows:
        return 0

    for one in rows:
        fields = {name: one[name] for name in ('name', 'photo')
                  if str(one.get(name) or '').strip()}
        if int(one.get('age') or 0) > 0:
            fields['age'] = int(one['age'])

        await companion_save(site_id, one['uid'], **fields)

        # ⚠ **Карточка завелась — человека стоит прочесть сразу.** Страница
        # уходит в `companion.params` тем же кругом, а не когда о ней вспомнит
        # рассылка: `companion_about_new` вызывается сам, при отказе молчит и на
        # знакомых строках тратит один `SELECT` — пора ему или нет, знает
        # состояние `none`. Дверь зовём **местным импортом**: наверху вышла бы
        # кольцо `companion_about → site_registry → site_guests → guests`.
        from src.dating.companion_about import companion_about_new

        await companion_about_new(site_id, profile_id, str(one['uid']))

    holes = ', '.join(['(%s, %s, %s, %s)'] * len(rows))
    args = [value for one in rows for value in
            (int(site_id), int(profile_id), str(one['uid'])[:32],
             one.get('visit'))]

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO companion_guest (site_id, profile_id, uid, visit_at)'
                f' VALUES {holes}'
                ' ON DUPLICATE KEY UPDATE'
                '   visit_at = COALESCE(VALUES(visit_at), visit_at),'
                '   created_at = CURRENT_TIMESTAMP',
                args)
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] гости анкеты #{profile_id} не легли: {e}')

        return 0

    return len(rows)


async def guests_feed(site_id: int, profile_id: int = 0) -> dict:
    """Лента гостей сайта: один человек — одна карточка, свежие впереди.

    Args:
        site_id: сайт-закладка у соседа.
        profile_id: 0 — все гости сайта; номер анкеты — только те, кто заходил
            к ней. Фильтр про показ, не про хранение: строки лежат по анкетам,
            сняты кругом за все анкеты разом.

    Returns:
        dict: `people` — карточки (`uid`, `name`, `age`, `photo`, `visit` —
        дата словами или '', `profiles` — скольким анкетам он заходил) и
        `taken_at` — время самого свежего пополнения ленты.

    ## ⚠⚠ Один человек — одна карточка

    Он мог заходить к трём анкетам: в ленте он стоит **один раз** с самой свежей
    из дат. Держать по строке на анкету значило бы показывать одного человека
    тремя карточками — на скриншоте ленты «Гости» так и было, пока наполнять
    было нечем, а с наполнением это стало бы обманом: «восьмеро заходили», хотя
    трое — один и тот же.

    ⚠ Под выбором анкеты тем же правилом держится и **дата**: `MAX(visit_at)`
    считается по строкам выбранной анкеты — человек видит, когда он заходил
    **к ней**, а не когда светился у соседней девушки.

    ⚠⚠ А вот `profiles` под фильтром — **не то, что `COUNT(*)` от отобранных
    строк**: внутри отбора он всегда единица, а значок «заходил нескольким»
    человек смотрит именно в ленте одной анкеты — это и есть примета гуляки.
    Поэтому вторым запросом счёт берётся по всему сайту.

    ⚠ **Карточка человека — из `companion`, а не отсюда.** Имя, фото и возраст
    живут там; сюда они доезжают связкой по «сайту + номеру». У гостя, чью
    карточку так и не завели (круг заводит — но гость мог прийти раньше), поля
    пустые: пустая карточка честнее выдуманной.

    ⚠ **Даты нет — в конец.** `ORDER BY visit_at DESC` ставит NULL последними:
    гость без даты — не «давний», он «неизвестно когда», и показывать его первым
    на том лишь основании, что мы не посмотрели, — врать свежестью.
    """
    picked = int(profile_id or 0)
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT g.uid,'
                '       MAX(g.visit_at)                    AS visit_at,'
                '       COUNT(*)                           AS profiles,'
                '       MAX(COALESCE(c.name, \'\'))        AS name,'
                '       MAX(COALESCE(c.photo, \'\'))       AS photo,'
                '       MAX(COALESCE(c.age, 0))            AS age,'
                '       MAX(g.created_at)                  AS seen_at'
                '  FROM companion_guest g'
                '  LEFT JOIN companion c'
                '         ON c.site_id = g.site_id AND c.uid = g.uid'
                ' WHERE g.site_id = %s'
                '   AND (%s = 0 OR g.profile_id = %s)'
                ' GROUP BY g.uid'
                ' ORDER BY visit_at DESC, name'
                ' LIMIT %s',
                (int(site_id), picked, picked, GUESTS_FEED_MAX))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] лента гостей сайта #{site_id} не спросилась: {e}')

        return {'people': [], 'taken_at': ''}

    people = [{'uid': str(row['uid']),
               'name': str(row['name'] or ''),
               'age': int(row['age'] or 0),
               'photo': str(row['photo'] or ''),
               'visit': str(row['visit_at'] or ''),
               'profiles': int(row['profiles'] or 0)}
              for row in rows or []]

    if picked and people:
        people = await _guests_profiles(site_id, people)

    seen = [str(row['seen_at']) for row in rows or [] if row.get('seen_at')]

    return {'people': people, 'taken_at': max(seen) if seen else ''}


async def _guests_profiles(site_id: int, people: list) -> list:
    """Значок «скольким анкетам заходил» — по всему сайту, не по отбору."""
    marks = ', '.join(['%s'] * len(people))
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT uid, COUNT(DISTINCT profile_id) AS profiles'
                '  FROM companion_guest'
                f' WHERE site_id = %s AND uid IN ({marks})'
                ' GROUP BY uid',
                (int(site_id), *[one['uid'] for one in people]))
            rows = await db.fetchall()
    except Exception as e:
        # Отказ не портит ленту: без значка она читается как «по одной анкете»,
        # а не как ошибка — лучше врать скромностью, чем терять ряд.
        logger_info(f'[dating] анкеты гостей сайта #{site_id} не сошлись: {e}')

        return people

    counts = {str(row['uid']): int(row['profiles'] or 0) for row in rows or []}
    for one in people:
        one['profiles'] = counts.get(one['uid'], one['profiles'])

    return people


# ── Приватное ────────────────────────────────────────────────────────────────

def _safe_date(year: int, month: int, day: int) -> date | None:
    """Дату из разобранных частей — либо `None`, если календарь её не знает.

    ⚠ Сайт показал «31.02», строчка уехала в кривой разбор, а разбор не должен
    уносить с собой весь прогон: ради одной строки ленты терять остальные
    незачем.
    """
    try:
        return date(year, month, day)
    except ValueError:
        return None
