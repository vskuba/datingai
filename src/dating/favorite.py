"""Избранные: звёздочка на анкете мужчины у той девушки, которой он написал.

Ответил человек на письмо — его надо добавить девушке в избранные. Смысл в
последующем: избранные это короткий список, по которому ведут переписку, и человек,
однажды ответивший, не должен теряться среди тысяч анкет сайта.

Разворот тот же рукояткой: «убрать из фаворитов» на красной строке «Фаворитов →
Доставки» гасит звезду **и на сайте** — у сайта своей ручки снимать избранное нет,
а погаси мы только свою строку, `favorite_sync` прочитает список сайта и вернёт
её обратно, ровно к следующему обходу.

## Две двери, и обе нужны

| Что | Чем | Сколько стоит |
|-----|-----|---------------|
| «он уже в избранных?» | API сайта, `GET /users/my-favorites` | ~70 мс |
| «добавить в избранные» и «убрать» | сценарий у соседа, живой браузер | ~40 секунд |

Ручки «добавить» у сайта **нет** — только кнопка на странице анкеты. Отсюда
неравенство: спросить дёшево, сделать дорого. На нём и держится порядок работы —
сперва спрашиваем список, и ходим браузером только за теми, кого в нём не нашли.

## ⚠ Кнопка — переключатель, а не «добавить»

Разведка на живом сайте: звёздочка это элемент без текста, а состояние лежит в его
классе.

    class="favorite__status  profile_btn-friend"                  title="Add to Favorites"
    class="favorite__status favorite__status-ok profile_btn-friend" title="Remove from Favorites"

То есть **второе нажатие снимает избранное**. Отсюда два сценария с одним шагом
различия: «в избранные» жмёт только незажжённую звезду
(`.favorite__status:not(.favorite__status-ok)`), «из избранных» — только зажжённую,
шаг помечен `optional`, а этот модуль читает класс до и после и по ним говорит,
что случилось и в какую сторону. Порядок «сперва список сайта» — тоже про это: он
избавляет от лишних заходов, но не он делает прогон безопасным. Безопасным его
делает селектор, совпадающий с одним состоянием переключателя.

⚠ **Снимаем не на анкете, а на своей странице избранных сайта.** Прогон «из
избранных» по анкете не гасил звезду никогда, и вина не наша: обработчик сайта в
`user.min.js` берёт номер человека из строки списка (`closest(".people__item")`),
на анкете такой строки нет, и обработчик падает на `undefined.split("-")` — без
запроса и без видимого отказа, молча. Снятие избранного с анкеты сайтом просто
не работает. Работает оно на `/favorites`: там человек — строка
`id="userid-<номер>"`, нажатие по горящей звёздочке строки открывает окно
подтверждения самого сайта, а кнопка подтверждения шлёт `POST /contact-remove` и
снимает строку со страницы вместе со звездой. Отсюда второе отличие «после»:
**пустое «после» при горящем «до» — это успех** («строку сняли»), а не «звезду не
показали»; контракт записан у `favorite_state`, которая на то и один разборщик
на оба направления.

## Три исхода, и они разные

Исходы одни в обе сторону, только цель разная: в одну — чтобы звезда горела,
в другую — чтобы погасла.

| Исход | Что было | Пойдём ли снова |
|-------|----------|-----------------|
| `done` | звезда была не в нужном состоянии, мы нажали — стала нужной | нет |
| `already` | была в нём до нас | нет |
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

# Имена сценариев у соседа — двух, туда и обратно. ⚠ Чужие имена: так они записаны
# в его базе, и переименование здесь ничего не изменит — менять надо вместе с ним.
# Первый жмёт незажжённую звезду (`:not`) на анкете; второй — горящую, но уже на
# своей странице избранных сайта и с подтверждением в его окне (почему — выше).
FAVORITE_ADD_SCENARIO = 'анкета мужчины: в избранные'
FAVORITE_FORGET_SCENARIO = 'анкета мужчины: из избранных'

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

# Звезда погасла на сайте: пара была избранной, а в списке её больше нет.
#
# ⚠⚠ **Состояние, а не удаление строки.** `DELETE` унёс бы и счётчик заходов, и
# повод (`reason`), и историю: журнал доставки делит ответы между «Фаворитами» и
# «Письмами» по наличию строки в этой таблице.
#
# ⚠ **В `FAVORITE_STATES` намеренно не входит.** В этом весь приём: по набору
# отбирают шесть мест — пул писем, лента «Избранные онлайн», счётчики вкладок, —
# и все они перестают видеть пару сами, без единой правки в них.
FAVORITE_GONE = 'gone'


class DatingFavoriteError(RuntimeError):
    """Добавить не вышло. Текст годен для журнала анкеты и для страницы."""


def favorite_state(before: str, after: str, on: bool = True) -> tuple:
    """Что случилось со звёздочкой — по её классу до и после нажатия.

    Args:
        before: класс `.favorite__status` до нажатия (шаг «до» сценария).
        after: он же после (шаг «после»).
        on: чего мы ждали от звезды — загореться (`True`, «в избранные»)
            или погаснуть (`False`, «из избранных»).

    Returns:
        tuple: `(исход, причина)`. Причина непуста только у `fail`.

    ⚠ **Была в целевом состоянии до нас — это `already`, а не `done`.** У круга
    разницы нет, оба означают «больше не ходить», но событие разное: `done`
    сделала панель, `already` было и так. Свести их в одно значило бы потерять
    ответ на «а мы-то что сделали».

    ⚠ **Направлений два, а разбор один.** Сценарии «в избранные» и «из избранных»
    различаются и страницей (анкета против `/favorites`), и тем, что жмут (`:not`
    против горящей), но состояние снимают одинаково: класс звезды до и после.
    Свой разбор на каждое направление разошёлся бы с первым на первой же правке,
    поэтому здесь просто сравнение с тем, каким класс звезда **стать должна**:
    `was == on` — пришла целевой, мы не при чём, `now == on` — стало нашей
    работой.

    ⚠ **Класс читаем построчно.** `extract` отдаёт все совпадения селектора через
    перевод строки, и хотя звёздочка на анкете одна, второй элемент с тем же
    классом (всплывшая карточка, скрытый шаблон) превратил бы строку в две. Ищем
    признак в любой из них: зажжённая звезда где-то на странице означает, что он в
    избранных, а какой из узлов её показал — вопрос вёрстки.

    ⚠ **Пустое «после» — по-разному на каждый конец, и это не путаница, а разные
    страницы.** На анкете звезда висит всегда, и пусто там значит «шага не было или
    не нашли» — отвечать на это удачей нельзя, мы ничего не видели. На `/favorites`
    сайт при подтверждении снятия убирает **строку вместе со звездой**, так что
    пустое «после» при горевшем «до» и есть наш успех: строки нет — звезды нет.
    Отличает одно от другого «до»: без увиденной горящей звезды пустота — слепота,
    а не работа.
    """
    was = _on(before)
    now = _on(after)

    if not str(after or '').strip():
        # На странице избранных пустое «после» при горящем «до» — сайт снял
        # строку вместе со звездой. Без горящего «до» это та же слепота, что и
        # на анкете: мы ничего не видели и утверждать нечего.
        if not on and was:
            return FAVORITE_DONE, ''
        return FAVORITE_FAIL, 'сайт не показал звёздочку после нажатия'

    if was == on:
        return FAVORITE_ALREADY, ''

    if now == on:
        return FAVORITE_DONE, ''

    return FAVORITE_FAIL, ('звёздочка не зажглась — нажатие не дошло до сайта'
                           if on else
                           'звёздочка не погасла — нажатие не дошло до сайта')


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
        list: `{uid, name, photo, age, city, country}`. Пустой список значит ровно
        «сайт говорит: избранных нет» — живой случай, анкета #50 отвечает
        двенадцатью байтами.

    Raises:
        DatingFavoriteError: сайт отказал **или ответил без списка**.

    ## ⚠⚠ Пустой список и отсутствие списка — разное, и разведены они здесь

    Прежде оба отвечали `[]`, и от этого `favorite_sync` не мог убирать ничего:
    «снятая на сайте звезда и не пришедший список выглядят отсюда одинаково» —
    так и было записано доводом. Цену узнали 28 сентября: у тринадцати анкет
    прода накопилось 22 пары, которых в списках сайта давно нет, и пул
    «Приставания» исправно писал им письма.

    Теперь ответ без `Users` — отказ. Оба зовущих его уже ловят и ведут себя
    верно: круг звёздочек уходит к следующей анкете (жать звезду наугад значило бы
    гасить чужую — кнопка на сайте переключатель), сверка молчит и работает по
    тому, что уже знает.

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
    from src.dating.people import people_name_case
    from src.dating.people_api import PEOPLE_API_FIELDS

    try:
        body = await site_api_call(profile, base, password,
                                          FAVORITE_LIST_PATH, method='GET',
                                          door=REQUEST_PEOPLE)
    except DatingSiteApiError as e:
        raise DatingFavoriteError(f'список избранных не пришёл: {e}')

    rows = body.get(FAVORITE_LIST_ROWS)
    if not isinstance(rows, list):
        raise DatingFavoriteError(
            f'сайт ответил без списка избранных (нет поля «{FAVORITE_LIST_ROWS}»)')

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
            'name': people_name_case(
                str(row.get(PEOPLE_API_FIELDS['name']) or ''))[:PEOPLE_NAME_MAX],
            'photo': str(row.get(PEOPLE_API_FIELDS['photo'])
                         or '').strip()[:500],
            'age': int(age) if isinstance(age, int) else 0,
            'city': str(row.get('City') or '').strip()[:PEOPLE_NAME_MAX],
            'country': str(row.get('Country') or '').strip()[:PEOPLE_NAME_MAX]})

    return out


async def favorite_uids(profile: dict, base: str, password: str) -> set:
    """Номера тех, кто у этой девушки в избранных, — списком с сайта.

    Returns:
        set: номера. Пустое множество значит «избранных нет» — и только это:
        случай «спросить не удалось» отсюда уходит отказом.

    Raises:
        DatingFavoriteError: сайт отказал **или ответил без списка**. Круг ловит и
        идёт к следующей анкете: без списка добавлять наугад значило бы жать
        звёздочку тем, кто и так там, — а кнопка на сайте переключатель, и такое
        нажатие звезду **снимет**.

    ⚠ Разбор общий с `favorite_rows`, там же и довод: пустой список и отсутствие
    списка разведены, потому что на этом различии держится уборка погасших.
    """
    try:
        body = await site_api_call(profile, base, password,
                                          FAVORITE_LIST_PATH, method='GET',
                                          door=REQUEST_PEOPLE)
    except DatingSiteApiError as e:
        raise DatingFavoriteError(f'список избранных не пришёл: {e}')

    rows = body.get(FAVORITE_LIST_ROWS)
    if not isinstance(rows, list):
        raise DatingFavoriteError(
            f'сайт ответил без списка избранных (нет поля «{FAVORITE_LIST_ROWS}»)')

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

    ## ⚠⚠ Пропавших гасим — и это правка живой беды

    Прежде здесь было «только добавляем, ничего не убираем», доводом — «снятая на
    сайте звезда и не пришедший список выглядят отсюда одинаково». Довод был
    честный ровно до тех пор, пока `favorite_rows` отвечал `[]` на оба случая;
    теперь отсутствие списка приходит отказом, и `[]` значит «избранных нет».

    Цена прежнего порядка измерена 28 сентября на проде: в пуле «Приставания»
    висело письмо `Oksana413636 → John849892`, которого у Оксаны в избранных нет.
    Строка от 13 сентября с `reason = site_list` — тогда он там был. Сверка живых
    списков: **22 лишние пары** у тринадцати анкет.

    ⚠ **Гасим состоянием (`gone`), а не `DELETE`.** Удаление унесло бы счётчик
    заходов, повод и историю: журнал доставки делит ответы между «Фаворитами» и
    «Письмами» по наличию строки. `gone` же не входит в `FAVORITE_STATES`, и все
    шесть мест, которые им отбирают, перестают видеть пару сами.

    ⚠ **Гаснут только те, кто считался избранным.** `fail` — это «нажать не
    вышло», у него свой счётчик заходов, и переписывать его нечем.

    ⚠⚠ **Вернувшийся в список зажигается обратно.** Сайт может моргнуть и отдать
    пустой список; без возврата одна такая осечка стоила бы нам всех избранных
    навсегда — погашенных сверка больше не сочла бы новыми. С возвратом цена
    осечки — один пропущенный заход приставания.

    ⚠ **Состояние `already`**, а не `done`: звезда уже стояла, мы её не нажимали.
    Отличить это важно в статистике — «добавили сами» против «были там до нас».
    """
    try:
        rows = await favorite_rows(profile, base, password)
    except DatingFavoriteError as e:
        logger_info(f'[dating] список избранных анкеты #{profile.get("id")}: {e}')

        return 0

    # ⚠ **Карточки заводим всем из списка, а не только новым.** Имя и лицо нужны
    # пулу и «Доставке» — без них человек подписан номером, — и приходят они в том
    # же ответе. Заведение дешёвое (один `INSERT … ON DUPLICATE`), а «только
    # новым» означало бы, что у знакомых пар имя не появится никогда.
    await _cards_put(int(profile['site_id']), rows)

    listed = {one['uid'] for one in rows}
    known = await favorite_settled(int(profile['id']))

    # ⚠ «Новый» это и тот, кого мы не знаем, и тот, кто числится у нас не
    # избранным: список сайта — источник истины, и раз он в нём есть, звезда горит.
    # Сюда же попадает возврат погашенного.
    fresh = [uid for uid in listed
             if str((known.get(uid) or {}).get('state') or '')
             not in FAVORITE_STATES]

    for uid in fresh:
        await favorite_mark(int(profile['site_id']), int(profile['id']), uid,
                                   FAVORITE_ALREADY, '',
                                   FAVORITE_REASON_SITE)

    gone = [uid for uid, was in known.items()
            if uid not in listed and str(was.get('state') or '') in FAVORITE_STATES]
    await _gone_mark(int(profile['id']), gone)

    if fresh or gone:
        logger_info(f'[dating] избранные анкеты #{profile["id"]}: '
                    f'переписано с сайта {len(fresh)}, погашено {len(gone)}')

    return len(fresh)


async def favorite_site_uids(site_id: int) -> set:
    """Кого хоть одна анкета сайта держит в избранных.

    Args:
        site_id: сайт-закладка.

    Returns:
        set: номера собеседников на сайте. Пусто — избранных нет либо база
        не ответила.

    ## ⚠⚠ Единственное место, которое отвечает на этот вопрос

    Спрашивают его из разных углов: лента «Избранные онлайн» на «Приставании»,
    отбор пар для писем, счётчики. Своя копия запроса в каждом расходится с
    первой правкой — так уже случилось с долями процентов и с правилом
    близнеца. Здесь ответ один, и его значение — «избранный **у сайта**», а не
    у конкретной девушки.

    ⚠ **По сайту, а не по паре, и это осознанно.** Лента отвечает на вопрос
    «с кем сейчас можно поработать», а работать может любая девушка сайта:
    человек, избранный у Дианы, для Елены такой же собеседник. Нужна пара —
    её знает `favorite_settled`, и это другой вопрос.

    ⚠ **Считаются только удавшиеся звёздочки** (`FAVORITE_STATES`): `fail`
    означает, что нажать не вышло, — человек избранным не стал. Тем же набором
    состояний отбирает письма `favorite_mail._quiet`; разойдись перечисления,
    лента показывала бы тех, кому пул писать отказывается.

    ⚠ **Сбой базы — пустое множество, а не отказ.** Лента без фильтра показала
    бы всех подряд и соврала бы названием; пустая же честно говорит «никого не
    нашли». Страница от этого не ломается.
    """
    site_id = int(site_id or 0)
    if not site_id:
        return set()

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT DISTINCT uid FROM favorite'
                f' WHERE site_id = %s AND state IN ({FAVORITE_STATES_SQL})',
                (site_id, *FAVORITE_STATES))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] избранные сайта #{site_id} не спросились: {e}')

        return set()

    return {str(row['uid']) for row in rows or [] if row.get('uid')}


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
        run = await _run(profile, uid, site_password, FAVORITE_ADD_SCENARIO)
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


async def favorite_forget(profile: dict, uid: str,
                                 site_password: str = '') -> dict:
    """Гасит звезду сайта под анкетой девушки и убирает пару из нашей таблицы.

    Args:
        profile: анкета девушки, **под которой входим**: избранные её.
        uid: номер мужчины на сайте.
        site_password: общий пароль анкет сайта (настройка `api_password`) —
            прогону сценария входить тем же входом, что и добавление.

    Returns:
        dict: `state` (`done` — звезда погасла нашей работой, `already` — её там
            и так не было), `error` словами при отказе и `dropped` — была ли
            строка у нас. `already` и `dropped: False` — не ошибки: нажатие
            значит «не считать избранным», а он и без нас не считался.

    ⚠ **Гасит не на анкете, а на `/favorites` — и через окно сайта.** Нажатая
    звезда на анкету ничего не делает: обработчик сайта читает номер человека из
    строки списка, вне его падает молча, и запроса не случается (разбор — в
    докстринге модуля). Прогон открывает страницу избранных, находит строку
    `#userid-<uid>`, жмёт на ней горящую звезду и подтверждает снятие кнопкой в
    окне самого сайта; сайт шлёт `POST /contact-remove` и убирает строку со
    страницы. Удача для нас — когда строки нет вовсе (пустое «после» при горящем
    «до»), поэтому разбор у `favorite_state`, а не здесь.

    ⚠ **Сперва сайт, потом своё.** Сценарий идёт первым: не погаснет звезда там
    — строку при следующей сверке вернёт `favorite_sync`, и «удаление» держится
    ровно до `favorite_sync_every`. Не погасла на сайте — значит не убрано
    нигде: строка ждёт человека с причиной в отказе.

    ⚠ **Черновик приставания уходит вместе со строкой.** Письмо избранному
    пишется потому, что он избранный; с погасшей звездой слать его нечего. Тот
    же жест, что у запрета (`block_add` — он тоже выметает пулы). Ушедшие письма
    не трогаем: это журнал, а журнал отвечает за уже случившееся.

    ⚠ **Ошибок не глотит** — в отличие от `favorite_mark`. Там потерянная строка
    дешевле потерянного прогона, за который уже заплачено сорока секундами; здесь
    нажал человек, и человек обязан увидеть отказ. Ответит ручка-дверь словом из
    `error`, страница покажет.
    """
    profile_id = int(profile.get('id') or 0)
    uid = str(uid or '').strip()[:PEOPLE_UID_MAX]

    try:
        run = await _run(profile, uid, site_password, FAVORITE_FORGET_SCENARIO)
    except DatingFavoriteError as e:
        await log_write(profile_id, 'fail', f'Снятие избранного {uid}: {e}')

        return {'state': FAVORITE_FAIL, 'error': str(e)}

    extract = run.get('extract') or {}
    state, error = favorite_state(extract.get('до'), extract.get('после'), on=False)

    if state == FAVORITE_FAIL:
        await log_write(profile_id, 'fail', f'Снятие избранного {uid}: {error}')

        return {'state': state, 'error': error}

    # ⚠ Звезда на сайте погашена (или её там и не было) — теперь наша очередь.
    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM favorite WHERE profile_id = %s AND uid = %s',
                         (profile_id, uid))
        dropped = int(db.rowcount or 0)
        await db.execute("DELETE FROM favorite_mail"
                         " WHERE profile_id = %s AND uid = %s AND status = 'draft'",
                         (profile_id, uid))
        await db.connection.commit()

    # ⚠ Уровень `info`, а не `fail`: работа не сломалась, список изменил человек.
    # Завтра спросят, почему этой паре ничего не уходит, — ответ должен лежать там.
    await log_write(profile_id, 'info', f'Из избранного убран: {uid}')
    logger_info(f'[dating] избранные: анкета #{profile_id} забыла {uid}'
                f' ({state}, строка {"была" if dropped else "не нужна"})')

    return {'state': state, 'dropped': bool(dropped)}


# ── Приватное ────────────────────────────────────────────────────────────────

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


async def _gone_mark(profile_id: int, uids: list) -> None:
    """Гасит пары, пропавшие из списка сайта, и уносит их черновики.

    Args:
        profile_id: анкета, чей список сверяли. ⚠ Гаснет **пара**, а не человек:
            у соседки он может гореть законно — список избранных у каждой свой.
        uids: номера, которых в списке сайта больше нет. Пусто — делать нечего.

    ⚠ **Черновик приставания уходит вместе со звездой.** Письмо избранному пишется
    потому, что он избранный; с погасшей звездой слать его нечего. Тот же жест,
    что у кнопки «убрать из фаворитов» (`favorite_forget`) и у запрета
    (`block_add`). Ушедшие письма не трогаем: это журнал, а журнал отвечает за уже
    случившееся.

    ⚠ **Ошибку глотаем**, как и `favorite_mark`: сверка — фоновая работа, и
    единичный сбой базы она переживёт, а следующий заход попробует снова. Цена
    молчания невелика и ровно та же, что была до правки: пара лишний час считается
    избранной.

    ⚠ `tries` и `error` не трогаем. Они про заходы круга звёздочек — «сколько раз
    падало и на чём», — а звезда погасла не от нашей неудачи.
    """
    uids = [str(one) for one in uids or []]
    if not uids:
        return

    holes = ', '.join(['%s'] * len(uids))
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'UPDATE favorite SET state = %s'
                f' WHERE profile_id = %s AND uid IN ({holes})',
                (FAVORITE_GONE, int(profile_id), *uids))
            await db.execute(
                f'DELETE FROM favorite_mail'
                f" WHERE profile_id = %s AND status = 'draft'"
                f'   AND uid IN ({holes})',
                (int(profile_id), *uids))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] погасшие избранные анкеты #{profile_id} '
                    f'не записались: {e}')


def _on(value) -> bool:
    """Горит ли звёздочка: есть ли класс-признак хоть в одной снятой строке."""
    return any(FAVORITE_ON_CLASS in line.split()
               for line in str(value or '').splitlines())


async def _run(profile: dict, uid: str, site_password: str,
               scenario_name: str) -> dict:
    """Прогон сценария у соседа — в любую из сторон. Любая неудача —
    `DatingFavoriteError` словами."""
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
        scenario = await rhapsody_api_scenario_find(site_id, scenario_name)
    except RhapsodyError as e:
        raise DatingFavoriteError(str(e)) from e

    if not scenario:
        raise DatingFavoriteError(
            f'у сайта нет сценария «{scenario_name}» — '
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
