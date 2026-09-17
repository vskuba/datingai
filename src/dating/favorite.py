"""Избранные: звёздочка на анкете мужчины у той девушки, которой он написал.

Ответил человек на письмо — его надо добавить девушке в избранные. Смысл в
последующем: избранные это короткий список, по которому ведут переписку, и человек,
однажды ответивший, не должен теряться среди тысяч анкет сайта.

## Две двери, и обе нужны

| Что | Чем | Сколько стоит |
|-----|-----|---------------|
| «он уже в избранных?» | API сайта, `GET /users/my-favorites` | ~70 мс |
| «добавить в избранные» | сценарий у соседа, живой браузер | ~40 секунд |

Ручки «добавить» у сайта **нет** — только кнопка на странице анкеты. Отсюда
неравенство: спросить дёшево, сделать дорого. На нём и держится порядок работы —
сперва спрашиваем список, и ходим браузером только за теми, кого в нём не нашли.

## ⚠ Кнопка — переключатель, а не «добавить»

Разведка на живом сайте: звёздочка это элемент без текста, а состояние лежит в его
классе.

    class="favorite__status  profile_btn-friend"                  title="Add to Favorites"
    class="favorite__status favorite__status-ok profile_btn-friend" title="Remove from Favorites"

То есть **второе нажатие снимает избранное**. Сценарий поэтому нажимает только
незажжённую звезду (`.favorite__status:not(.favorite__status-ok)`, шаг помечен
`optional`), а этот модуль читает класс до и после и по ним говорит, что случилось.
Порядок «сперва список сайта» — тоже про это: он избавляет от лишних заходов, но не
он делает прогон безопасным. Безопасным его делает `:not` в селекторе.

## Три исхода, и они разные

| Исход | Что было | Пойдём ли снова |
|-------|----------|-----------------|
| `done` | звёздочка не горела, мы нажали — загорелась | нет |
| `already` | горела до нас | нет |
| `fail` | вход, сеть, занятый браузер, не та анкета | да, до потолка `tries` |

⚠ **`fail` повторяем** — в отличие от сбора описаний (`companion_about`, где
неудача не повторяется вовсе). Разница в природе причины: описание, которого на
странице нет, не появится и завтра, а занятая вкладка у соседа освободится через
минуту. Цель при этом не исчезает: он всё ещё должен оказаться в избранных.
Потолок держит это в рамках — три захода, дальше строка ждёт человека.

⚠ **Кому и почему добавили — помним** (`favorite`). Без этого круг ходил бы
за одними и теми же каждые пять минут по сорок секунд. Источником истины таблица при
этом не становится: «есть ли он в избранных» знает сайт, а таблица знает, что
**мы пробовали** и чем это кончилось.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.log import log_write
from src.dating.people import PEOPLE_NAME_MAX, PEOPLE_UID_MAX
from src.dating.ready import password_pick
from src.dating.scenario import scenario_run
from src.dating.request import REQUEST_PEOPLE
from src.dating.site_api import DatingSiteApiError, site_api_call
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_site_get)

# Имя сценария у соседа. ⚠ Чужое имя: так он записан в его базе, и переименование
# здесь ничего не изменит — менять надо вместе с ним.
FAVORITE_SCENARIO = 'анкета мужчины: в избранные'

# Класс зажжённой звёздочки. ⚠ **Признак состояния — он, а не подпись.** Подпись
# («Remove from Favorites») сайт переводит на язык кабинета, а класс — нет.
FAVORITE_ON_CLASS = 'favorite__status-ok'

# Ручка списка избранных у API сайта. Тот же вид, что читает колонка «My favorites»
# на «Профилях» (`people_api.py`, `my-favorites`).
FAVORITE_LIST_PATH = '/users/my-favorites'
FAVORITE_LIST_ROWS = 'Users'
FAVORITE_LIST_UID = 'AccountId'

# Состояния, при которых человек считается избранным. ⚠ **`fail` сюда не входит**:
# звёздочку нажать не вышло, на сайте он не в избранных, и считать его таковым
# значило бы врать про то, что видит он сам.
#
# ⚠ **По этому набору разговоры делятся между двумя вкладками**: переписка с
# избранным живёт на «Фаворитах → Диалоги», со всеми прочими — на «Письмах →
# Входящие». Одна и та же пара не должна попасть в оба списка: дважды показанный
# разговор это дважды написанный ответ.
#
# Отсюда и общая константа: два места сравнивают одно и то же, и разойдись
# перечисления — пара либо задвоилась бы, либо исчезла из обоих.
FAVORITE_STATES = ('done', 'already')

# Место для набора в `IN (...)`: «%s, %s». ⚠ Своей константой, а не выражением по
# месту: `%` внутри склейки строковых литералов SQL **разрывает** её, и запрос
# ломается на сборке модуля (`SyntaxError: Perhaps you forgot a comma?`). Так уже
# случилось — правка валила приложение целиком, пока не вынесли.
FAVORITE_STATES_SQL = ', '.join(['%s'] * len(FAVORITE_STATES))

# Сколько раз ходим за одним человеком, прежде чем оставить его в покое. Три: две
# неудачи подряд бывают от занятого браузера, три — уже про причину, которая сама
# не рассосётся.
FAVORITE_TRIES_MAX = 3

# Почему добавляли. Строка ложится в `favorite.reason` и отвечает на вопрос
# «откуда взялся этот человек в списке», когда его смотрят через месяц.
#
# ⚠ Значение пока одно, и столбец всё равно нужен: поводов добавить в избранные
# больше одного (кнопка у человека, ответ в чате, а не в письме), и появись второй
# при одном значении в базе — старые строки нечем было бы отличить от новых.
FAVORITE_REASON_MAIL = 'mail_in'

# Повод записи: «так сказал список сайта». ⚠ Не `mail_in`: тот означает «человек
# написал нам письмо, и мы решили нажать звезду», а здесь звезда уже стояла — мы
# лишь переписали к себе то, что у сайта и так есть.
FAVORITE_REASON_SITE = 'site_list'

# Исходы. Отдельными константами, потому что по ним ветвится и круг, и запись.
FAVORITE_DONE = 'done'
FAVORITE_ALREADY = 'already'
FAVORITE_FAIL = 'fail'


class DatingFavoriteError(RuntimeError):
    """Добавить не вышло. Текст годен для журнала анкеты и для страницы."""


def favorite_state(before: str, after: str) -> tuple:
    """Что случилось со звёздочкой — по её классу до и после нажатия.

    Args:
        before: класс `.favorite__status` до нажатия (шаг «до» сценария).
        after: он же после (шаг «после»).

    Returns:
        tuple: `(исход, причина)`. Причина непуста только у `fail`.

    ⚠ **Горела до нас — это `already`, а не `done`.** Для круга разницы нет, оба
    означают «больше не ходить», но событие разное: `done` сделала панель, `already`
    было и так. Свести их в одно значило бы потерять ответ на «а мы-то что сделали».

    ⚠ **Класс читаем построчно.** `extract` отдаёт все совпадения селектора через
    перевод строки, и хотя звёздочка на анкете одна, второй элемент с тем же
    классом (всплывшая карточка, скрытый шаблон) превратил бы строку в две. Ищем
    признак в любой из них: зажжённая звезда где-то на странице означает, что он в
    избранных, а какой из узлов её показал — вопрос вёрстки.

    ⚠ **Пустое «после» — отказ, а не «не изменилось».** Пусто значит, что шага не
    было или звёздочку не нашли; отвечать на это «уже в избранных» было бы
    выдумыванием удачи там, где мы ничего не видели.
    """
    was = _on(before)
    now = _on(after)

    if not str(after or '').strip():
        return FAVORITE_FAIL, 'сайт не показал звёздочку после нажатия'

    if was:
        return FAVORITE_ALREADY, ''

    if now:
        return FAVORITE_DONE, ''

    return FAVORITE_FAIL, 'звёздочка не зажглась — нажатие не дошло до сайта'


def favorite_pick(letters: list, favorites: set, settled: dict,
                         limit: int) -> list:
    """Кого добавлять в этот заход: отправители писем минус те, кто уже решён.

    Args:
        letters: номера мужчин, писавших этой анкете, — свежие первыми.
        favorites: номера из списка избранных сайта. Кто в нём есть, того трогать
            не надо вовсе.
        settled: что мы уже пробовали: `{uid: {'state': …, 'tries': …}}`.
        limit: потолок на заход. Каждый заход — сорок секунд браузера.

    Returns:
        list: номера в том порядке, в каком пришли письма, не длиннее `limit`.

    ⚠ **Список сайта сильнее нашей таблицы.** Он про «есть ли он там сейчас», а
    таблица — про «что мы делали». Разойдись они (человек снял звёздочку руками), в
    избранные надо возвращаться, а не верить своей записи.

    ⚠ **Удачу помним всё равно.** Уже решённого (`done`/`already`) пропускаем даже
    без списка сайта: список мог не прийти вовсе (сайт молчит, API не настроен), и
    тогда пустое множество не должно означать «добавить всех заново».

    ⚠ **Отказ повторяем до потолка.** Причина обычно временная, и один отказ не
    повод бросить человека; три — уже про причину, которая сама не пройдёт.
    """
    # ⚠ Потолок проверяем **до** отбора, а не после добавления. Проверяй мы после,
    # потолок в ноль отдавал бы одного человека: строка успевала лечь в список
    # прежде, чем счёт дойдёт до предела. Поймано тестом.
    limit = max(int(limit), 0)
    if not limit:
        return []

    pick, seen = [], set()
    for uid in letters:
        uid = str(uid or '').strip()
        if not uid or uid in seen or uid in favorites:
            continue
        seen.add(uid)

        was = settled.get(uid) or {}
        state = str(was.get('state') or '')
        if state in (FAVORITE_DONE, FAVORITE_ALREADY):
            continue
        if state == FAVORITE_FAIL \
                and int(was.get('tries') or 0) >= FAVORITE_TRIES_MAX:
            continue

        pick.append(uid)
        if len(pick) >= limit:
            break

    return pick


async def favorite_rows(profile: dict, base: str, password: str) -> list[dict]:
    """Избранные этой девушки **записями**, а не номерами: имя, лицо, возраст, город.

    Returns:
        list: `{uid, name, photo, age, city, country}`. Пусто — избранных нет либо
        список не пришёл.

    Raises:
        DatingFavoriteError: сайт отказал.

    ⚠ **Заведено потому, что имён брать было неоткуда.** Прежде список читался
    ради одних номеров (`favorite_uids`), и в пуле «Приставания» карточки
    подписывались номером: `Диана → 803656`. У инвайтов имя есть — их ленту сайт
    отдаёт с именами, — а сюда оно не попадало ни от кого: в `dating_contact`
    человека нет (в чате не говорили), в `companion` тоже (карточку заводят
    по требованию).

    А сайт отдаёт всё сразу — `Name`, `ProfilePhoto`, `Age`, `City`, `Country` — в
    том же ответе, за который мы уже сходили.

    ⚠ **Имена полей — общие с `people_api`**: это один и тот же движок и
    одна и та же запись пользователя. Второй словарь имён разошёлся бы с первым.
    """
    from src.dating.people_api import PEOPLE_API_FIELDS

    try:
        body = await site_api_call(profile, base, password,
                                          FAVORITE_LIST_PATH, method='GET',
                                          door=REQUEST_PEOPLE)
    except DatingSiteApiError as e:
        raise DatingFavoriteError(f'список избранных не пришёл: {e}')

    rows = body.get(FAVORITE_LIST_ROWS)
    if not isinstance(rows, list):
        return []

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        uid = str(row.get(FAVORITE_LIST_UID) or '').strip()
        if not uid:
            continue

        age = row.get(PEOPLE_API_FIELDS['age'])
        out.append({
            'uid': uid[:PEOPLE_UID_MAX],
            'name': str(row.get(PEOPLE_API_FIELDS['name'])
                        or '').strip()[:PEOPLE_NAME_MAX],
            'photo': str(row.get(PEOPLE_API_FIELDS['photo'])
                         or '').strip()[:500],
            'age': int(age) if isinstance(age, int) else 0,
            'city': str(row.get('City') or '').strip()[:PEOPLE_NAME_MAX],
            'country': str(row.get('Country') or '').strip()[:PEOPLE_NAME_MAX]})

    return out


async def favorite_uids(profile: dict, base: str, password: str) -> set:
    """Номера тех, кто у этой девушки в избранных, — списком с сайта.

    Returns:
        set: номера. Пустое множество — либо избранных нет, либо спросить не
        удалось; отличить их здесь незачем, потому что решение одно и то же:
        дальше отбирает `favorite_pick`, а он на пустой список опирается
        только в сторону осторожности.

    Raises:
        DatingFavoriteError: сайт отказал. Круг ловит и идёт к следующей анкете:
        без списка добавлять наугад значило бы жать звёздочку тем, кто и так там.
    """
    try:
        body = await site_api_call(profile, base, password,
                                          FAVORITE_LIST_PATH, method='GET',
                                          door=REQUEST_PEOPLE)
    except DatingSiteApiError as e:
        raise DatingFavoriteError(f'список избранных не пришёл: {e}')

    rows = body.get(FAVORITE_LIST_ROWS)
    if not isinstance(rows, list):
        return set()

    return {str(row.get(FAVORITE_LIST_UID) or '').strip()
            for row in rows if isinstance(row, dict)
            and str(row.get(FAVORITE_LIST_UID) or '').strip()}


async def favorite_sync(profile: dict, base: str, password: str) -> int:
    """Переписывает список избранных с сайта в нашу таблицу. Возвращает — сколько
    записей добавилось.

    ⚠⚠ **Ради этого и написано: наша таблица знала не всех.** Заполнял её круг
    звёздочек, и только теми, кто **писал письма**: «у девушки в избранных бывают
    десятки людей, заведённых руками, и таскать их всех незачем — вопрос к ней
    задают лишь про тех, кто написал».

    Довод был верен, пока «Приставание» требовало переписки в нашей базе. Теперь
    хватает звезды (`favorite_mail.py`), и наша таблица стала узким местом:
    в виджете «Мои фавориты» у анкеты восемнадцать человек, в таблице — один, и пул
    честно отвечал «писать некому».

    ⚠ **Только добавляем, ничего не убираем.** Снятая на сайте звезда и не
    пришедший список выглядят отсюда одинаково, а удаление унесло бы и состояние
    (`state`, `tries`, `error`), и историю. Кому мы больше не пишем — знает чёрный
    список, и это другой вопрос.

    ⚠ **Состояние `already`**, а не `done`: звезда уже стояла, мы её не нажимали.
    Отличить это важно в статистике — «добавили сами» против «были там до нас».
    """
    try:
        rows = await favorite_rows(profile, base, password)
    except DatingFavoriteError as e:
        logger_info(f'[dating] список избранных анкеты #{profile.get("id")}: {e}')

        return 0

    if not rows:
        return 0

    # ⚠ **Карточки заводим всем из списка, а не только новым.** Имя и лицо нужны
    # пулу и «Доставке» — без них человек подписан номером, — и приходят они в том
    # же ответе. Заведение дешёвое (один `INSERT … ON DUPLICATE`), а «только
    # новым» означало бы, что у знакомых пар имя не появится никогда.
    await _cards_put(int(profile['site_id']), rows)

    known = set(await favorite_settled(int(profile['id'])))
    fresh = [one['uid'] for one in rows if one['uid'] not in known]

    for uid in fresh:
        await favorite_mark(int(profile['site_id']), int(profile['id']), uid,
                                   FAVORITE_ALREADY, '',
                                   FAVORITE_REASON_SITE)

    if fresh:
        logger_info(f'[dating] избранные анкеты #{profile["id"]}: '
                    f'переписано с сайта {len(fresh)}')

    return len(fresh)


async def _cards_put(site_id: int, rows: list[dict]) -> None:
    """Кладёт карточки собеседников из списка избранных.

    ⚠ **Пустое значение не стирает прежнее** (`IF(VALUES(x) = '', x, VALUES(x))`):
    сайт иногда отдаёт запись без фотографии, и затирать ею снятое сценарием лицо
    значило бы терять то, за чем ходили в браузер. Тот же приём, что у ручки
    карточки собеседника.

    ⚠ **`priority` и `about` не трогаем вовсе** — их ставит человек и агент, а
    список сайта про них ничего не знает.
    """
    if not rows:
        return

    try:
        async with mysql_get_db_async() as db:
            for one in rows:
                await db.execute(
                    'INSERT INTO companion'
                    ' (site_id, uid, name, photo, age, city, country)'
                    ' VALUES (%s, %s, %s, %s, %s, %s, %s)'
                    ' ON DUPLICATE KEY UPDATE'
                    "  name = IF(VALUES(name) = '', name, VALUES(name)),"
                    "  photo = IF(VALUES(photo) = '', photo, VALUES(photo)),"
                    '  age = IF(VALUES(age) = 0, age, VALUES(age)),'
                    "  city = IF(VALUES(city) = '', city, VALUES(city)),"
                    "  country = IF(VALUES(country) = '', country, VALUES(country))",
                    (int(site_id), one['uid'], one['name'], one['photo'],
                     int(one['age'] or 0), one['city'], one['country']))

            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] карточки избранных сайта #{site_id} не легли: {e}')


async def favorite_settled(profile_id: int) -> dict:
    """Что мы уже пробовали по этой анкете: `{uid: {'state': …, 'tries': …}}`."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT uid, state, tries FROM favorite'
                             ' WHERE profile_id = %s', (int(profile_id),))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] избранные анкеты #{profile_id} не спросились: {e}')

        return {}

    return {str(row['uid']): {'state': str(row['state']),
                              'tries': int(row['tries'])}
            for row in rows or []}


async def favorite_mark(site_id: int, profile_id: int, uid: str, state: str,
                               error: str = '',
                               reason: str = FAVORITE_REASON_MAIL) -> None:
    """Записывает исход захода. Не бросает: потерянная строка дешевле потерянного
    прогона, за который уже заплачено сорока секундами браузера.

    ⚠ **`tries` растёт только на отказе.** Удача заходов не копит: считать их у
    `done` значило бы мешать «сколько раз падало» с «сколько раз работало», а
    потолок читает именно первое.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO favorite'
                ' (site_id, profile_id, uid, state, error, tries, reason)'
                ' VALUES (%s, %s, %s, %s, %s, %s, %s)'
                ' ON DUPLICATE KEY UPDATE state = VALUES(state),'
                '  error = VALUES(error), reason = VALUES(reason),'
                '  tries = tries + VALUES(tries)',
                (int(site_id), int(profile_id), str(uid)[:PEOPLE_UID_MAX],
                 str(state), str(error or '')[:500],
                 1 if state == FAVORITE_FAIL else 0, str(reason)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] избранное {uid} у анкеты #{profile_id} '
                    f'не записалось: {e}')


async def favorite_add(profile: dict, uid: str, site_password: str = '',
                              reason: str = FAVORITE_REASON_MAIL) -> dict:
    """Идёт на сайт и нажимает звёздочку на анкете мужчины.

    Args:
        profile: анкета девушки, **под которой входим**: избранные её.
        uid: номер мужчины на сайте.
        site_password: общий пароль анкет сайта (настройка `api_password`).
        reason: почему добавляем — ложится в строку и читается через месяц.

    Returns:
        dict: `state` (`done`/`already`/`fail`) и `error` словами.

    Исход записывается в `favorite` и в журнал анкеты — оба раза здесь, а не
    у вызывающего: заходов бывает много и из разных мест (круг, кнопка), и след
    должен получаться одинаковым.

    ⚠ **Входим под самой девушкой.** Избранные — её список, и добавить в него
    из-под другой учётки нельзя в принципе, а не только «нежелательно».

    ⚠ **Пароль выбираем мы, а не сосед.** У анкеты есть настройка, каким паролем
    ходить — своим или общим агентским (`password_source`). Подставь сосед свою
    переменную сайта, анкета с личным паролем ходила бы под чужим. То же правило,
    что у сбора анкеты собеседника.
    """
    site_id = int(profile.get('site_id') or 0)
    profile_id = int(profile.get('id') or 0)
    uid = str(uid or '').strip()

    try:
        run = await _run(profile, uid, site_password)
    except DatingFavoriteError as e:
        await favorite_mark(site_id, profile_id, uid, FAVORITE_FAIL,
                                   str(e), reason)
        await log_write(profile_id, 'fail',
                               f'В избранные {uid}: {e}')

        return {'state': FAVORITE_FAIL, 'error': str(e)}

    extract = run.get('extract') or {}
    state, error = favorite_state(extract.get('до'), extract.get('после'))

    await favorite_mark(site_id, profile_id, uid, state, error, reason)

    # ⚠ В журнал анкеты пишем **все три исхода**, и `already` тоже. Он отвечает на
    # вопрос «почему круг сходил и ничего не сделал»: без строки поход выглядел бы
    # потерянными сорока секундами.
    if state == FAVORITE_DONE:
        await log_write(profile_id, 'ok', f'В избранные добавлен {uid}')
    elif state == FAVORITE_ALREADY:
        await log_write(profile_id, 'info', f'{uid} уже в избранных')
    else:
        await log_write(profile_id, 'fail', f'В избранные {uid}: {error}')

    return {'state': state, 'error': error}


# ── Приватное ────────────────────────────────────────────────────────────────

def _on(value) -> bool:
    """Горит ли звёздочка: есть ли класс-признак хоть в одной снятой строке."""
    return any(FAVORITE_ON_CLASS in line.split()
               for line in str(value or '').splitlines())


async def _run(profile: dict, uid: str, site_password: str) -> dict:
    """Прогон сценария у соседа. Любая неудача — `DatingFavoriteError` словами."""
    from src.uvicorn.routers.dating.site_ import site_setting

    site_id = int(profile['site_id'])
    if not uid:
        raise DatingFavoriteError('не назван номер собеседника')

    password = password_pick(
        profile, site_password or str(await site_setting(site_id,
                                                               'api_password') or ''))
    if not password:
        raise DatingFavoriteError(
            'нечем войти: у анкеты не задан пароль — проверьте блок «Пароль»')

    try:
        site = await rhapsody_api_site_get(site_id)
        scenario = await rhapsody_api_scenario_find(site_id, FAVORITE_SCENARIO)
    except RhapsodyError as e:
        raise DatingFavoriteError(str(e)) from e

    if not scenario:
        raise DatingFavoriteError(
            f'у сайта нет сценария «{FAVORITE_SCENARIO}» — '
            f'заведите его в RhapsodyAi на вкладке «Сценарии»')

    try:
        # ⚠ `keep_session=False`: вкладка нужна на один прогон. Она стоит ~143 МБ в
        # пуле, общем с соседом, а звёздочек мы жмём десятками.
        run = await scenario_run(
            scenario, site_id, int(profile['id']), '',
            {'login_url': str((site or {}).get('url') or '').rstrip('/'),
             'profile_login': str(profile.get('login') or ''),
             'companion_uid': uid},
            {'girl_password': password}, keep_session=False,
            proxy_id=int(profile.get('proxy_id') or 0))
    except RhapsodyError as e:
        raise DatingFavoriteError(str(e)) from e

    if str(run.get('status')) == 'error':
        raise DatingFavoriteError(
            f"сценарий встал на шаге {run.get('step_no')}: {run.get('error')}")

    return run
