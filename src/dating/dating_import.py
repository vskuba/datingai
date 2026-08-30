"""Импорт анкет с сайта: что считать той же анкетой и что у неё обновлять.

Данные добывает **RhapsodyAi** — у него браузер и сценарии, и вся работа со
страницей лежит на нём. Здесь только разбор того, что он вернул, и решение, что с
этим делать в нашей базе.

## Два сценария, а не один

| Сценарий | Что даёт | Сколько прогонов |
|----------|----------|------------------|
| «импорт анкет» | список: имя, город, номер, ссылка, фото | один на сайт |
| «анкета: карточка» | логин-почта, год рождения, занятие, описание | один на анкету |

Разделение не наше — так устроена панель сайта: в списке карточек нет ни почты,
ни описания, они лежат на странице правки каждой анкеты. Отсюда и цена: список
снимается за секунды, а карточки идут по одной, и сотня анкет — это сотня
прогонов браузера. Поэтому карточки читаются **не всегда** (см. `dating_import_plan`).

## Ключ — пара «сайт и логин»

Совпал логин на том же сайте — та же анкета: ровно так устроен уникальный ключ
`dating_profile`, и второго мнения тут быть не может. Логин берётся из карточки
(почта), а когда карточку не читали — из номера анкеты на сайте.

⚠ **Из-за этого номер и почта не должны разъезжаться.** Анкета, заведённая
импортом без карточки (логин = номер), при следующем импорте с карточкой
получила бы почту и выглядела бы новой — то есть завелась бы второй раз. Поэтому
номер сайта всегда пишется в `profile.site_uid`, и поиск идёт сперва по нему
(`dating_import_match`), а логин остаётся ключом только для новых.

## Что обновляем, а что не трогаем никогда

Обновляем то, что **пришло с сайта**: имя, фото, возраст, город, описание. Не
трогаем то, что **завёл человек**: пароль, пометку, автоответ, снятый вход. Импорт
повторяют, и затирать им ручную работу — верный способ отучить его нажимать.
"""

from datetime import datetime

# Год, раньше которого рождения не бывает: страница отдаёт год строкой, и мусор
# вроде «—» или «0» превратился бы в возраст в две тысячи лет.
DATING_IMPORT_YEAR_MIN = 1930

# Длины колонок и полей карточки, под которые режется пришедшее.
DATING_IMPORT_LOGIN_MAX = 190
DATING_IMPORT_NAME_MAX = 190
DATING_IMPORT_CITY_MAX = 190
DATING_IMPORT_PHOTO_MAX = 500
DATING_IMPORT_ABOUT_MAX = 2000

# Поля анкеты, которые импорт имеет право переписать. Всё, чего здесь нет,
# принадлежит человеку и переживает любой импорт.
DATING_IMPORT_CARD_FIELDS = ('name', 'photo', 'age', 'city', 'about')


def dating_import_rows(extract: dict, field: str = 'анкеты') -> list[dict]:
    """Строки списка из ответа сценария — в наш вид.

    Args:
        extract: `extract` прогона; под `field` лежит список записей
            (`extract_rows` у соседа отдаёт именно список, а не склейку строк).
        field: имя шага, под которым сосед вернул записи.

    Returns:
        list: записи `{site_uid, name, city, photo_path}`. Без номера запись
        пропускается — по нему ищут анкету, и пустой склеил бы разные в одну.
    """
    rows = (extract or {}).get(field)
    if not isinstance(rows, list):
        return []

    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        site_uid = _text(row.get('номер'))
        if not site_uid:
            continue

        found.append({'site_uid': site_uid[:DATING_IMPORT_LOGIN_MAX],
                      'name': _text(row.get('имя'))[:DATING_IMPORT_NAME_MAX],
                      'city': _text(row.get('город'))[:DATING_IMPORT_CITY_MAX],
                      'photo_path': _text(row.get('фото'))})

    return found


def dating_import_card(extract: dict, now: datetime) -> dict:
    """Карточка одной анкеты из ответа сценария — в наш вид.

    Args:
        extract: `extract` прогона карточки: `id`, `login`, `name`, `city`,
            `birth_year`, `occupation`, `about`, `photo`.
        now: текущее время — из него считается возраст по году рождения.

    Returns:
        dict: `site_uid`, `login`, `name`, `city`, `photo_path`, `age`, `about`.
        Пустой словарь — карточка не разобралась.
    """
    if not isinstance(extract, dict) or not extract:
        return {}

    site_uid = _text(extract.get('id'))
    login = _text(extract.get('login'))
    if not site_uid and not login:
        return {}

    # Занятие дописываем к описанию, а не заводим своё поле: в карточке панели это
    # одна строка про человека, а колонки под неё в `dating_profile.profile` нет —
    # заводить её ради одного сайта значило бы менять форму кэша всем остальным.
    about = _text(extract.get('about'))
    occupation = _text(extract.get('occupation'))
    if occupation and occupation.lower() not in about.lower():
        about = f'{occupation}. {about}'.strip('. ').strip() if about else occupation

    return {'site_uid': site_uid[:DATING_IMPORT_LOGIN_MAX],
            'login': login[:DATING_IMPORT_LOGIN_MAX],
            'name': _text(extract.get('name'))[:DATING_IMPORT_NAME_MAX],
            'city': _text(extract.get('city'))[:DATING_IMPORT_CITY_MAX],
            'photo_path': _text(extract.get('photo')),
            'age': _age(extract.get('birth_year'), now),
            'about': about[:DATING_IMPORT_ABOUT_MAX]}


def dating_import_match(row: dict, by_uid: dict, by_login: dict) -> dict | None:
    """Наша учётка, которой соответствует пришедшая запись. `None` — новая.

    Ищем **сперва по номеру сайта**, и только потом по логину. Порядок тут не
    вкусовщина: номер у анкеты один навсегда, а логин у неё меняется — импорт без
    карточки кладёт в него номер, импорт с карточкой почту. Ищи мы по логину
    первым, такая анкета завелась бы второй раз, и человек получил бы две карточки
    одной девушки с разной перепиской.
    """
    found = by_uid.get(str(row.get('site_uid') or ''))
    if found:
        return found

    login = str(row.get('login') or row.get('site_uid') or '')

    return by_login.get(login) or None


def dating_import_plan(row: dict, found: dict | None, base: str) -> dict:
    """Что сделать с одной записью: завести, обновить или оставить как есть.

    Args:
        row: разобранная запись — из списка либо дополненная карточкой.
        found: наша учётка, если нашлась (`dating_import_match`).
        base: адрес сайта — им достраиваются относительные пути фотографий.

    Returns:
        dict: `action` (`create` | `update` | `same`), `login`, `card` — анкета в
        том виде, в каком она ложится в `dating_profile.profile`, и `changed` —
        имена полей, которые действительно поменялись.

    Решение и запись разведены намеренно: что менять — правило, и его видно на
    чистых данных; а `UPDATE` без сети и базы не проверишь.
    """
    card = _card_build(row, base)
    login = str(row.get('login') or row.get('site_uid') or '')

    if not found:
        return {'action': 'create', 'login': login, 'card': card, 'changed': []}

    # Сравниваем с тем, что уже лежит в кэше анкеты, а не с колонками учётки:
    # именно `profile` импорт и переписывает.
    было = dict(found.get('card') or {})
    changed = [name for name in DATING_IMPORT_CARD_FIELDS
               if _changed(было.get(name), card.get(name))]

    # **Поверх кладём только непустое.** Иначе импорт без карточек стирал бы город
    # и описание, дочитанные импортом с карточками: список их не отдаёт вовсе, и
    # в `card` они приходят пустыми. Заметно это не сразу — пустое не считается
    # изменением, и `same` такую анкету не тронет; но стоит смениться имени, и тот
    # же прогон унёс бы вместе с ним всё, чего не было в списке.
    свежее = {name: value for name, value in card.items() if value not in (None, '', 0)}

    return {'action': 'update' if changed else 'same',
            'login': str(found.get('login') or login),
            'card': {**было, **свежее},
            'changed': changed}


def dating_import_photo(path: str, base: str) -> str:
    """Адрес фотографии: сайт отдаёт путь от своего корня, странице нужен полный.

    То же правило и та же причина, что у собеседниц (`site_profile.photo_url`):
    страница подставляет значение в `<img src>` как есть, и путь без хоста браузер
    достроит до адреса панели, где такой картинки нет.
    """
    photo = str(path or '').strip()
    if not photo or photo.startswith('http'):
        return photo

    return (str(base or '').rstrip('/') + '/' + photo.lstrip('/'))[:DATING_IMPORT_PHOTO_MAX]


# ── Приватное ────────────────────────────────────────────────────────────────

def _card_build(row: dict, base: str) -> dict:
    """Анкета в том виде, в каком её держит `dating_profile.profile`.

    Форма общая с кэшем анкеты, который пишет вход (`site_profile.card_parse`):
    страница читает `profile.name` и `profile.photo` независимо от того, откуда
    они взялись, и второй формы у неё быть не должно.
    """
    card = {'id': str(row.get('site_uid') or ''),
            'site_uid': str(row.get('site_uid') or ''),
            'name': str(row.get('name') or ''),
            'photo': dating_import_photo(row.get('photo_path'), base),
            'age': int(row.get('age') or 0),
            'city': str(row.get('city') or ''),
            'about': str(row.get('about') or '')}

    return card


def _changed(было, стало) -> bool:
    """Поменялось ли поле. Пустое «стало» изменением не считается.

    Сайт отдаёт не всё и не всегда: у части анкет не заполнен город, карточку могли
    не читать вовсе. Считай мы пустоту изменением — импорт стирал бы то, что уже
    знает, и каждый прогон «обновлял» бы анкеты в никуда.
    """
    if стало in (None, '', 0):
        return False

    return str(было or '') != str(стало)


def _age(birth_year, now: datetime) -> int:
    """Возраст из года рождения. Мусор и будущее дают ноль, а не отрицательное."""
    try:
        year = int(str(birth_year or '').strip())
    except (TypeError, ValueError):
        return 0

    if year < DATING_IMPORT_YEAR_MIN or year > now.year:
        return 0

    return now.year - year


def _text(value) -> str:
    """Значение со страницы — строкой без краёв. `None` и числа тоже."""
    return str(value if value is not None else '').strip()
