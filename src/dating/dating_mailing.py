"""Набор пула писем: кто кому пишет и чем подставляются переменные.

⚠ **Предметный модуль, а не роутер** — и это главное. Набор зовут двое: человек
кнопкой «Генерировать письма» и круг рассылки по расписанию сайта
(`dating_mailing_round.py`). Живи он в ручке, кругу пришлось бы ходить к своему же
приложению по HTTP — со своим токеном, своими таймаутами и своим молчанием, когда
приложение перезапускают.

## Как набирается

Обходим **по очереди**: первая анкета — первому адресату, вторая — второму, и по
кругу. Не перебором всех пар: анкет два десятка, людей в сети под две сотни, и
декартово произведение дало бы тысячи писем за один заход.

⚠ **Не больше одного письма от анкеты одному адресату.** Проверяется по паре
«анкета + адресат» **любым** шаблоном — правило про пул, а не про историю. В базе
уникальный ключ включает шаблон (`profile_id, uid, mail_id`), и такого запрета он
выразить не может: там речь о том, что допустимо в журнале, здесь — о том, что
осмысленно готовить.

## ⚠ Письмо с нехваткой в пул не кладётся

Шаблон ссылается на переменные, и подставить их бывает нечем: у анкеты не
заполнено `girl_name`, у адресата нет имени в карточке. Такое письмо не уйдёт —
значит и лежать в пуле ему незачем. Пропущенные считаются и называются: молчание
человек прочитал бы как «письма не сделались вовсе».
"""

import random

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_json import dating_json_load
from src.dating.dating_mail import (dating_mail_missing, dating_mail_render,
                                    dating_mail_vars_found)
from src.dating.dating_mail_agent import DatingMailAgentError, dating_mail_question
from src.dating.dating_people import dating_people_name_parts

# Сколько писем набирает один заход, если настройки сайта нет. Тот же порядок, что
# у `mailing_batch` в реестре (`dating_site.py`).
DATING_MAILING_BATCH_DEFAULT = 50

# Жёсткий предел поверх настройки. ⚠ Не дублирует её, а страхует: значение в базе
# правят руками, и «5000» превратило бы один заход в часы работы. Тот же приём, что
# у выдержки списков (`api.md`, §3.3).
DATING_MAILING_BATCH_MAX = 200

# Сколько анкет собеседников идём смотреть на сайт за один заход, если настройки
# нет. Пять: поход стоит ~50 секунд, и пять это четыре минуты — предел терпения у
# нажатой кнопки.
DATING_MAILING_PROBE_DEFAULT = 5

# Жёсткий потолок: двадцать походов это четверть часа, и дольше держать нажатую
# кнопку нельзя ни при какой настройке.
DATING_MAILING_PROBE_MAX = 20

# Настройка сайта, задающая это число.
DATING_MAILING_PROBE_SETTING = 'mailing_probe'


# Начало причины у **бесплатного** пропуска: незаполненная переменная видна до
# всякого похода — ни LLM, ни сайта на неё не потрачено.
#
# ⚠ **Строка вынесена не ради опрятности.** По ней круг рассылки решает, держать
# ли получасовой слот после захода (`dating_mailing_round.py`): бесплатный
# пропуск чинится за минуту, и заставлять человека ждать полчаса после починки
# незачем. Разойдись строка с той, что круг ищет, — и он молча вернулся бы к
# ожиданию, а понять это можно было бы только по секундомеру.
DATING_MAILING_SKIP_FREE = 'не хватает переменных'


# Сколько анкет собеседников разрешено собрать за один заход. ⚠ **Считается на
# заход, а не на сутки**: заход это нажатие кнопки или один тик круга, и человек у
# экрана вправе знать, сколько он ждёт. Поход стоит ~50 секунд живого браузера.
#
# Живёт в памяти процесса, а не в Redis: предел про длительность **этого** захода,
# и переживать его незачем.
_probe_budget: dict[int, int] = {}


def _probe_left(site_id: int) -> bool:
    """Остался ли запас походов на сайт в этом заходе."""
    return int(_probe_budget.get(int(site_id), 0)) > 0


def _probe_take(site_id: int) -> None:
    """Тратит один поход."""
    _probe_budget[int(site_id)] = max(0, int(_probe_budget.get(int(site_id), 0)) - 1)


async def dating_mailing_make(site_id: int, templates: list, limit: int,
                              profile_id: int = 0) -> dict:
    """Набирает пул: письма по заданным шаблонам, не больше `limit` попыток.

    Args:
        site_id: сайт-закладка.
        templates: шаблоны, из которых каждому письму достаётся случайный.
        limit: сколько пар пробуем за заход.
        profile_id: писать только от этой анкеты. Ноль — от всех, у кого включена
            рассылка. ⚠ Сужение, а не отбор заново: анкета всё равно должна быть с
            рассылкой и с этого сайта, иначе писем не будет вовсе.

    Returns:
        dict: `made` — сколько писем готово, `skipped` — сколько пар пропущено из-за
        нехватки переменных, `reasons` — чего именно не хватило (первые несколько),
        `left` — сколько пар осталось на следующий заход, `stopped` — почему заход
        оборвался раньше времени (пусто — дошёл до конца).

    ⚠ **Повторный заход продолжает с того же места**, а не делает то же самое: пары,
    для которых письмо уже лежит в пуле или ушло, пропускаются. Поэтому кнопку можно
    жать, пока пул не наберётся, и не бояться дублей — а круг может будить набор
    каждые полчаса, не заваливая пул повторами.
    """
    girls = await _girls(site_id)
    people = await _people(site_id)

    # ⚠ Сужаем **после** отбора, а не запросом: правила «включена» и «с рассылкой»
    # должны быть одни на оба случая. Спроси мы анкету отдельным запросом, письма
    # от выключенной уходили бы там, где от неё же в общем наборе не уходят.
    if profile_id:
        girls = [girl for girl in girls if girl['id'] == int(profile_id)]

    if not girls or not people or not templates:
        return {'made': 0, 'skipped': 0, 'reasons': [], 'left': 0, 'stopped': ''}

    # ⚠ Реестр и значения берём **у роутера переменных, лениво**. Импорт наверху
    # завёл бы предметный модуль в зависимость от ручек: обход сайтов в воркере
    # тянул бы за собой FastAPI и всю обвязку прав, которой в круге нет.
    from src.uvicorn.routers.dating.dating_mail_var import (
        dating_mail_var_rows, dating_profile_var_values)

    known = await dating_mail_var_rows(site_id)
    taken = await _taken(site_id)

    # ⚠ Запас походов на сайт — **на этот заход**. Настройка сайта `mailing_probe`
    # (§3.7.2), ноль — не ходить вовсе и писать тем, о ком уже знаем.
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    _probe_budget[int(site_id)] = min(
        int(await dating_site_seconds(site_id, DATING_MAILING_PROBE_SETTING)
            or DATING_MAILING_PROBE_DEFAULT), DATING_MAILING_PROBE_MAX)

    # ⚠ Считаем **попытки**, а не только удачи. Прежде предел проверялся по готовым
    # письмам, и проход, где всё пропускается, шёл по всем парам до конца: 176 пар,
    # 8 минут и ноль писем на выходе. Поймано живым прогоном.
    made, tried, skipped, reasons, left, stopped = 0, 0, 0, [], 0, ''
    # ⚠ Значения анкеты читаются **один раз на анкету**, а не на письмо: они не
    # зависят от адресата, а запрос на каждое письмо превратил бы полсотни писем в
    # полсотни лишних походов в базу.
    values_by_girl = {}

    for girl, template, person in _pairs(girls, people, templates, taken):
        if tried >= limit:
            left += 1
            continue

        tried += 1

        if girl['id'] not in values_by_girl:
            values_by_girl[girl['id']] = await dating_profile_var_values(girl['id'])

        try:
            body, why = await _body(template, girl, person, known,
                                    values_by_girl[girl['id']], site_id)
        except DatingMailAgentError as e:
            # Агент недоступен — это не «письмо не годится», а «сейчас не выйдет
            # вовсе». Останавливаемся: следующие пары упрутся в то же самое.
            logger_info(f'[dating] рассылка остановлена: {e}')
            stopped = str(e)
            break

        if why:
            skipped += 1
            if len(reasons) < 5 and why not in reasons:
                reasons.append(why)
            continue

        await _put(site_id, girl, person, template, body)
        made += 1
        taken.add((girl['id'], person['uid'], template['id']))

    return {'made': made, 'skipped': skipped, 'reasons': reasons, 'left': left,
            'stopped': stopped}


async def dating_mailing_templates(site_id: int, mail_ids: list = None) -> list[dict]:
    """Шаблоны сайта: заданные номерами или все включённые.

    Args:
        mail_ids: какие брать. ⚠ **Пусто — берём все включённые**, и это не
            вольность: так набирает круг по расписанию, где выбирать некому.
            Человек же выбирает сам, и пустой выбор для него — отказ, а не «все».
    """
    sql = 'SELECT id, name, body FROM dating_mail WHERE site_id = %s AND enabled = 1'
    args = [int(site_id)]

    ids = [int(one) for one in (mail_ids or []) if int(one) > 0]
    if ids:
        sql += ' AND id IN (%s)' % ', '.join(['%s'] * len(ids))
        args.extend(ids)

    sql += ' ORDER BY id'

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    return [{'id': int(row['id']), 'name': str(row['name']), 'body': str(row['body'])}
            for row in rows]


async def dating_mailing_girls(site_id: int) -> list[dict]:
    """Анкеты сайта, участвующие в рассылке. Наружу — ради проверок вызывающего."""
    return await _girls(site_id)


async def dating_mailing_people(site_id: int) -> list[dict]:
    """Кто в сети по снимку ленты. Наружу — ради проверок вызывающего."""
    return await _people(site_id)


async def dating_mailing_gaps(site_id: int) -> dict[int, list[str]]:
    """Каким анкетам сайта нечем заполнить письмо: анкета → чего не хватает.

    ⚠ **Заведено по живому случаю.** Рассылка была включена у четырёх анкет, а
    письма шли от одной: у трёх не заполнен `girl_name`, и каждая выпавшая на них
    пара молча пропускалась. В журнале это читалось как «готово 25, пропущено 25»,
    на странице — как «включено, а писем нет». Ответ лежал в базе, и найти его
    можно было только запросом.

    ⚠ **Считаем только `profile`-переменные.** `companion` берётся из карточки
    адресата, `live` спрашивается у агента — и то и другое зависит от пары, а не
    от анкеты, и «не хватает» про них было бы неправдой ровно так же, как в
    `_body`. Здесь отвечаем на вопрос про **анкету**: она-то готова?

    ⚠ **Нехватка хотя бы в одном включённом шаблоне** — это уже повод сказать. Не
    «во всех»: шаблоны выбираются наугад, и анкета, годная для двух из шести,
    четыре раза из шести пропустит письмо. Показать её здоровой значило бы
    объяснять потом, почему писем вчетверо меньше ожидаемого.

    Returns:
        dict: только про анкеты с нехваткой; у кого всё на месте — ключа нет.
        Пустой словарь — либо всё заполнено, либо считать не по чему (нет анкет
        или включённых шаблонов), и это одно и то же для вызывающего: сказать
        нечего.
    """
    girls = await _girls(site_id)
    templates = await dating_mailing_templates(site_id)
    if not girls or not templates:
        return {}

    from src.uvicorn.routers.dating.dating_mail_var import (
        dating_mail_var_rows, dating_profile_var_values)

    known = await dating_mail_var_rows(site_id)

    # Всё, что не `profile`, подставится позже и не по анкете. Заполняем заглушкой,
    # чтобы `dating_mail_missing` их не считал: он не знает про источники.
    later = {str(var['name']): 'x' for var in known if var['source'] != 'profile'}

    gaps = {}
    for girl in girls:
        values = dict(later, **await dating_profile_var_values(girl['id']))

        missing = set()
        for template in templates:
            missing.update(dating_mail_missing(template['body'], values))

        if missing:
            gaps[girl['id']] = sorted(missing)

    return gaps


def dating_mailing_person_fields(person: dict) -> dict:
    """Карточка собеседника плюс выводимые поля — то, из чего берутся значения.

    Сейчас выводимых два: `firstname` и `lastname`, разобранные из `name`
    (`dating_people_name_parts`).

    ⚠ **Выводим, а не храним.** Части имени — это `name`, посмотренный под другим
    углом; заведи мы под них колонки, они разошлись бы с именем в первую же правку
    карточки, и письмо ушло бы с прежним обращением. Тот же довод, по которому у
    `companion`-переменных нет своего хранилища вовсе.

    ⚠ **Своё поле карточки сильнее выводимого.** Появись у сайта однажды
    настоящее `firstname`, в дело пойдёт оно: разбор по пробелу — догадка, а
    присланное сайтом — факт.
    """
    parts = dating_people_name_parts((person or {}).get('name'))

    return {**parts, **{key: value for key, value in (person or {}).items()
                        if value not in (None, '')}}


def dating_mailing_person_key(name: str, fields: dict) -> str:
    """Какое поле карточки стоит за именем переменной.

    Args:
        name: имя переменной — `companion_first_name`, `his_city`, `name`.
        fields: поля карточки вместе с выводимыми (`dating_mailing_person_fields`).

    Returns:
        str: имя поля. Не нашлось — последний кусок имени, как раньше: пустое
        значение честнее выдуманного, и решает его `default_value` переменной.

    ⚠ **Хвосты пробуем от длинного к короткому, и порядок здесь — всё.**
    `companion_first_name` должен взять поле `firstname`, а не `name`: возьми он
    короткий хвост, в обращение молча уехало бы полное имя. Раньше разбор умел один
    кусок после последнего подчёркивания, и ради этого переменные приходилось звать
    `companion_firstName` — camelCase посреди snake_case, помесь, которую человек
    видит в реестре и не может объяснить.

    ⚠ **Подчёркивания в хвосте склеиваем**: поля карточки зовутся `firstname`,
    `lastname` — одним словом, потому что так их отдаёт разбор имени. Совпадение
    ищется и по хвосту как есть, и по склеенному: `first_name` → `firstname`.
    """
    куски = str(name or '').lower().split('_')

    for от in range(len(куски)):
        хвост = '_'.join(куски[от:])
        for ключ in (хвост, хвост.replace('_', '')):
            if ключ in fields:
                return ключ

    return куски[-1] if куски else ''


def dating_mailing_companion_vars(person: dict, known: list) -> list[dict]:
    """Чем подставятся `companion`-переменные для этого человека.

    Args:
        person: карточка адресата — её поля и есть источник значений.
        known: реестр переменных сайта; берутся только `companion`.

    Returns:
        list: по записи на переменную — `name`, `title`, `field` (поле карточки,
        откуда берётся), `value`, `from_default` (взято ли умолчание) и `filled`.

    ⚠ **Своего хранилища у этих значений нет, и заводить его незачем.** Значение
    `companion`-переменной выводится из карточки: `companion_name` — это `name`
    адресата. Храни мы их отдельно, они разошлись бы с карточкой в первую же
    правку — и письмо ушло бы со старым именем.

    Отсюда и назначение: страница показывает **чем подставится**, а правят при
    этом саму карточку.
    """
    rows = []
    for var in known or []:
        if var.get('source') != 'companion':
            continue

        name = str(var['name'])
        fields = dating_mailing_person_fields(person)
        field = dating_mailing_person_key(name, fields)
        own = fields.get(field)
        value = _from_person(name, person, var)

        rows.append({'name': name,
                     'title': str(var.get('title') or ''),
                     'field': field,
                     'value': value,
                     # ⚠ Различаем «взято из карточки» и «взято из умолчания»:
                     # второе значит, что письмо уйдёт, но обратятся в нём общими
                     # словами — и человек вправе это увидеть до отправки.
                     'from_default': bool(value) and own in (None, '', 0),
                     'filled': bool(value)})

    return rows


# ── Приватное ────────────────────────────────────────────────────────────────

def _pairs(girls: list, people: list, templates: list, taken: set):
    """Тройки «анкета — адресат — шаблон»: кому, от кого и чем писать.

    ⚠ **По адресатам, а не перебором всех сочетаний.** Анкет два десятка, людей в
    сети под две сотни, шаблонов пяток: перебор дал бы тысячи троек за заход. Здесь
    каждый адресат получает одно письмо за проход, и вопрос только в том, от кого и
    каким текстом.

    ⚠ **Анкета и шаблон выбираются наугад, а не по кругу.** Круг давал правильное
    чередование — первая, вторая, третья, снова первая, — и по журналу сайта такая
    рассылка читается как рассылка.

    ⚠ **Выбираем из свободных троек.** Иначе случай раз за разом выпадал бы на
    занятую пару, и человек оставался без письма, хотя другая анкета или другой
    шаблон были доступны.

    Args:
        taken: тройки, которым письмо уже готовили или отправляли.
    """
    for person in people:
        свободные = [(girl, template) for girl in girls for template in templates
                     if (girl['id'], person['uid'], template['id']) not in taken]
        if not свободные:
            continue

        yield (*random.choice(свободные), person)


async def _body(template: dict, girl: dict, person: dict, known: list,
                girl_values: dict, site_id: int) -> tuple:
    """Готовый текст письма. Вторым — причина, по которой письма не будет.

    Собирает значения из трёх источников по `source` переменной:

      `profile`   — из значений анкеты;
      `companion` — из карточки адресата;
      `live`      — спрашивается у агента, под эту самую пару.

    ⚠ **Дешёвое считается первым, `live` — последним.** Это не порядок ради
    порядка: `live` стоит вызова LLM, а `profile` и `companion` лежат в памяти.
    Спроси мы агента раньше проверки — платили бы за письмо, которое всё равно не
    уйдёт из-за незаполненного `girl_name`.

    Поймано живым прогоном: 157 пар, восемь с половиной минут и ноль писем.

    ⚠ **`live` спрашивается только если шаблон на него ссылается** — по той же
    причине: вызов ради переменной, которой в тексте нет, это плата за воздух.
    """
    used = set(dating_mail_vars_found(template['body']))
    values = dict(girl_values)

    live = []
    for var in known:
        name = var['name']
        if name not in used or var['source'] == 'profile':
            continue

        if var['source'] == 'companion':
            values[name] = _from_person(name, person, var)
        elif var['source'] == 'live':
            live.append(name)

    # Проверяем то, что уже известно: `live` в этот счёт не идёт — его ещё не
    # спрашивали, и «не хватает» про него было бы неправдой.
    missing = [name for name in dating_mail_missing(template['body'], values)
               if name not in live]
    if missing:
        return '', f"{DATING_MAILING_SKIP_FREE}: {', '.join(missing)}"

    if not live:
        return dating_mail_render(template['body'], values), ''

    # ── Дальше платное: сначала сайт, потом агент ────────────────────────────
    #
    # ⚠ **Агенту нужна карточка, а не запись ленты.** Лента даёт имя, фото и
    # номер — по ним вопроса про интересы не задашь. Описание живёт в
    # `dating_companion`, и раньше оно до агента не доезжало вовсе: сюда
    # передавался `person` из ленты, у которого поля `about` нет и не бывает.
    # Собранные описания при этом лежали в базе и никуда не шли.
    card = await _card(site_id, person, girl)

    for name in live:
        values[name] = await dating_mail_question(girl, site_id, card)

    missing = dating_mail_missing(template['body'], values)
    if missing:
        return '', f"агент не ответил: {', '.join(missing)}"

    return dating_mail_render(template['body'], values), ''


async def _card(site_id: int, person: dict, girl: dict) -> dict:
    """Что известно об адресате: запись ленты плюс карточка, а если пусто — сходим.

    ⚠ **Сходить на сайт готовы не всегда**, и решает это настройка `mailing_probe`
    (§3.7.2): каждый заход стоит ~50 секунд живого браузера, и десяток незнакомых
    адресатов превратил бы нажатие кнопки в восемь минут ожидания.

    ⚠ **Ходим ровно один раз на человека.** Что известно — то известно: описание
    в карточке не устаревает так, чтобы за него стоило платить второй раз. Когда
    его взяли, помнит `about_at`, а чем кончился поход — `about_state`:

    | Состояние | Что было | Пойдём ли снова |
    |-----------|----------|-----------------|
    | `none` | не спрашивали | да |
    | `done` | описание есть | нет |
    | `empty` | сходили, рассказывать нечего | нет |
    | `fail` | не вышло — вход, сеть, чужая анкета | нет |

    Даже `fail` не повторяем сами: причина обычно не рассосётся к следующему
    заходу, а платить за неё по 50 секунд каждый раз — верный способ превратить
    рассылку в ожидание. Захочет человек — попросит заново, руками.

    ⚠ **Карточка при этом заводится каждому адресату, а не только тем, к кому
    сходили.** Это два разных дела: описание стоит похода браузером и потому
    нормируется, а карточка — строка из ленты, которая уже в памяти.

    Пока их путали, карточку заводил сам сбор описания, и она была у одного
    адресата из пяти: на живом пуле в 50 писем — у 13. В карточках писем имя
    адресата ссылается на его страницу у нас, и ссылка вела в никуда чаще, чем
    куда-нибудь. А главное — «кому мы пишем» это вопрос обо всех, кому пишем, а не
    о тех пятерых, на кого хватило походов.
    """
    from src.uvicorn.routers.dating.dating_companion import dating_companion_get

    uid = str(person.get('uid') or '')
    card = await dating_companion_get(site_id, uid)

    # ⚠ **Сперва кладём то, что знает лента** — и делаем это всегда, до всякого
    # решения о походе. Имя, фото и возраст уже пришли со списком онлайна, писать
    # их бесплатно, а без них карточка выходит безымянной и без лица: имя со
    # страницы анкеты снимается, фотография — нет (она есть в ленте, и грузить её
    # второй раз незачем).
    #
    # Так и вышло на живом прогоне: пять собеседников, у всех описание, и ни у
    # одного аватарки — в списке они читались как мусор.
    await _from_feed(site_id, person)

    # Описание есть — дальше ходить незачем.
    if not str((card or {}).get('about') or '').strip():
        if str((card or {}).get('about_state') or 'none') == 'none' and _probe_left(site_id):
            _probe_take(site_id)

            from src.dating.dating_companion_about import dating_companion_about_collect

            await dating_companion_about_collect(girl, uid)
            card = await dating_companion_get(site_id, uid)

    # ⚠ Лента старше карточки в том, что знает обе: имя и фото она снимает каждые
    # две минуты, а карточка их помнит с первого раза. Поэтому сверху кладём ленту.
    return {**(card or {}), **{k: v for k, v in (person or {}).items() if v}}


async def _from_feed(site_id: int, person: dict) -> None:
    """Заводит карточку адресата и переносит в неё то, что знает лента.

    ⚠ **Пустое не пишем.** `dating_companion_touch` правит только названное, и
    пустая строка из ленты затёрла бы то, что уже сняли со страницы анкеты.

    ⚠ **А вот саму карточку заводим и когда лента не дала ничего.** Строка нужна не
    ради полей: письмо ссылается на страницу адресата у нас, и адресат без строки —
    ссылка в никуда. Пустая карточка честнее её отсутствия: «этот человек есть, о
    нём пока ничего не знаем» — законный ответ, а «такой страницы нет» про того,
    кому мы прямо сейчас пишем письмо, выглядит поломкой.

    Тем же занимается щелчок по ленте на «Профилях» — но он бывает не всегда:
    адресата берут из пула, а не из ленты под рукой.
    """
    from src.uvicorn.routers.dating.dating_companion import dating_companion_touch

    ещё = {name: value for name, value in
           (('name', person.get('name')), ('photo', person.get('photo')),
            ('age', person.get('age')))
           if value}

    await dating_companion_touch(int(site_id), str(person.get('uid') or ''), **ещё)


def _from_person(name: str, person: dict, var: dict) -> str:
    """Значение `companion`-переменной из карточки адресата.

    ⚠ **По суффиксу имени, а не по точному совпадению.** Переменные зовут
    `companion_name`, `his_city`, `name` — как решил человек. Гадать по всему имени
    бессмысленно, а вот хвост после последнего подчёркивания совпадает с полем
    карточки почти всегда.

    Не нашлось — умолчание реестра; нет и его — пусто, и письмо не уйдёт. Это
    правильный исход: подставить наугад значило бы отправить чужие данные.
    """
    fields = dating_mailing_person_fields(person)
    key = dating_mailing_person_key(name, fields)
    value = fields.get(key)

    # ⚠ **Обращение по имени, а не по имени с фамилией.** Сайт отдаёт `name`
    # целиком — «Semen Gorbunkov», — и письмо, начинающееся «Hi Semen Gorbunkov»,
    # читается как рассылка из базы, чем оно и является. Поэтому у `…_name` в дело
    # идёт разобранное первое слово, если оно есть.
    #
    # ⚠ **Правим подстановку, а не тексты шаблонов**, и это не лень. Замени мы
    # `[[companion_name]]` на `[[companion_firstName]]` в девяти готовых письмах —
    # адресатам с именем одним словом («Kevin») подставлять стало бы нечего:
    # `firstname` у них пуст намеренно (`dating_people_name_parts`), нехватка
    # переменной останавливает отправку, и письмо не ушло бы вовсе. Здесь же пустой
    # разбор просто оставляет прежнее поведение.
    if key == 'name' and str(fields.get('firstname') or '').strip():
        value = fields['firstname']

    if value in (None, '', 0):
        return str(var.get('default_value') or '')

    return str(value)


async def _girls(site_id: int) -> list[dict]:
    """Анкеты, участвующие в рассылке: включённые, с `mailing_on` и **в сети**.

    ⚠ **Только те, кто сейчас в сети** (`status = 'online'`). Письмо уходит от её
    имени, и уносит его круг отправки — в те же часы, но от анкеты, которой на
    сайте может уже не быть. Хуже другое: сайт показывает получателю отправителя,
    и письмо от отсутствующей девушки он открывает, не имея кому ответить.

    Прежде отбор смотрел только на `mailing_on`, и пул набивался от всех учёток с
    галочкой — включая спящие. Условие добавлено вслед за тем же правилом у писем
    избранным (`dating_favorite_mail`), и довод здесь тот же.

    ⚠ Тот же отбор, что у вкладки «Письма рассылка» на «Профилях»: список слева и
    те, кому генерируются письма, обязаны совпадать — иначе человек видит одно, а
    получает другое.

    ⚠ **Строка нужна целиком, а не тремя полями.** Анкета уходит не только в письмо:
    под ней **заходят на сайт** за анкетой собеседника (`dating_companion_about`), а
    для входа нужны `site_id`, `password` и `password_source`.

    Их тут и не было — запрос брал `id`, `login`, `profile`, — и первое же нажатие
    «Генерировать письма» падало с `KeyError: 'site_id'`. Ошибка ждала ровно того
    случая, когда среди адресатов попадётся незнакомый: пока все были известны, на
    сайт никто не ходил.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, password, password_source, profile'
            ' FROM dating_profile'
            ' WHERE site_id = %s AND mailing_on = 1 AND enabled = 1'
            "   AND status = 'online' ORDER BY id",
            (int(site_id),))
        rows = await db.fetchall()

    return [{'id': int(row['id']), 'site_id': int(row['site_id']),
             'login': str(row['login']),
             'password': str(row['password'] or ''),
             'password_source': str(row['password_source'] or 'site'),
             'profile': dating_json_load(row['profile'])} for row in rows]


async def _people(site_id: int) -> list[dict]:
    """Кто сейчас в сети — из того же снимка, что показывает лента «Все онлайн».

    ⚠ **Из кэша, а не походом на сайт.** Лента снимается своим кругом и живёт в
    Redis; сходи мы за ней заново, нажатие кнопки стоило бы сорока секунд браузера
    вдобавок к письмам. Пустой кэш означает, что ленту ещё не открывали, — и это
    честный ответ «в сети никого не знаем», а не повод лезть на сайт.

    ⚠ Ключ кэша и его чтение берём **у ленты** (`dating_people.py`), а не собираем
    свои: разойдись они, рассылка писала бы не тем, кого человек видит на экране.

    ⚠ **Отмеченные приоритетом идут первыми.** Порядок в ленте — тот, в каком людей
    отдал сайт, и для панели он случаен: пул набирается до потолка и обрывается на
    полпути по списку, так что «кому досталось письмо» решал случай. Отметка
    (`dating_companion.priority`) — способ сказать «этим в первую очередь».
    """
    from src.uvicorn.routers.dating.dating_people import _cache_get, _key

    cached = _cache_get(_key('online', site_id=site_id))
    people = [row for row in ((cached or {}).get('people') or []) if row.get('uid')]

    return await _by_priority(site_id, people) if people else people


async def _by_priority(site_id: int, people: list[dict]) -> list[dict]:
    """Отмеченные приоритетом — вперёд, остальные — следом, порядок внутри прежний.

    ⚠ **Это перестановка, а не отбор.** Неотмеченные никуда не деваются: сделай мы
    из отметки фильтр, включённая галочка у одного человека молча выключила бы
    рассылку всем прочим — а человек отмечал одного, а не отменял остальных.

    ⚠ **Порядок внутри каждой половины сохраняется.** `sorted` в Python устойчива, и
    это здесь по существу: лента приходит от сайта в своём порядке, а перетасуй мы её
    сверх нужного — два набора подряд давали бы разных адресатов без всякой причины.

    ⚠ **Сбой базы не роняет набор**: возвращаем ленту как есть. Приоритет — это
    удобство, и падать из-за него значило бы менять письма на порядок писем.
    """
    try:
        marked = await _marked(site_id)
    except Exception as e:
        logger_info(f'[dating] приоритет собеседников не спросился: {e}')

        return people

    if not marked:
        return people

    return sorted(people, key=lambda row: 0 if str(row.get('uid')) in marked else 1)


async def _marked(site_id: int) -> set:
    """Номера собеседников сайта, отмеченных приоритетом."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT uid FROM dating_companion'
                         ' WHERE site_id = %s AND priority = 1', (int(site_id),))
        rows = await db.fetchall()

    return {str(row['uid']) for row in rows or []}


async def _taken(site_id: int) -> set:
    """Тройки «анкета — адресат — шаблон», которые уже готовили или отправляли.

    ⚠ **Шаблон входит в ключ**, и это то же правило, что стоит в базе
    (`uq_dating_mailing_once`): не больше одного письма от анкеты адресату **тем же
    шаблоном**. Разными — можно: у них разный текст, и второе письмо читается как
    продолжение разговора, а не как повтор.

    Прежде здесь была пара без шаблона — правило строже базы. Оно и работало, пока
    пул набирали по нажатию: адресатов сотня, писем полсотни, до вторых заходов
    дело не доходило. С наполнением по расписанию дошло: круг добирает пул до
    предела, и без вторых писем ему быстро становится нечего делать.

    ⚠ Берём **все состояния**, а не только пул: ушедшее письмо тоже значит, что
    этим шаблоном этому человеку уже писали.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT profile_id, uid, mail_id FROM dating_mailing WHERE site_id = %s',
            (int(site_id),))
        rows = await db.fetchall()

    return {(int(row['profile_id']), str(row['uid']), int(row['mail_id'] or 0))
            for row in rows}


async def _put(site_id: int, girl: dict, person: dict, template: dict,
               body: str) -> None:
    """Кладёт письмо в пул.

    `INSERT IGNORE`: уникальный ключ ловит гонку, если кнопку нажали дважды разом.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO dating_mailing'
            ' (site_id, profile_id, mail_id, uid, name, body, status)'
            " VALUES (%s, %s, %s, %s, %s, %s, 'draft')",
            (int(site_id), int(girl['id']), int(template['id']),
             str(person['uid']), str(person.get('name') or ''), body))
        await db.connection.commit()
