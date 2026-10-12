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
прогонов браузера. Поэтому карточки читаются **не всегда** (см. `import_plan`).

## Ключ — пара «сайт и логин»

Совпал логин на том же сайте — та же анкета: ровно так устроен уникальный ключ
`profile_`, и второго мнения тут быть не может. Логин берётся из карточки
(почта), а когда карточку не читали — из номера анкеты на сайте.

⚠ **Из-за этого номер и почта не должны разъезжаться.** Анкета, заведённая
импортом без карточки (логин = номер), при следующем импорте с карточкой
получила бы почту и выглядела бы новой — то есть завелась бы второй раз. Поэтому
номер сайта всегда пишется в `profile.site_uid`, и поиск идёт сперва по нему
(`import_match`), а логин остаётся ключом только для новых.

## Что обновляем, а что не трогаем никогда

Обновляем то, что **пришло с сайта**: имя, фото, возраст, город, описание. Не
трогаем то, что **завёл человек**: пароль, пометку, автоответ, снятый вход. Импорт
повторяют, и затирать им ручную работу — верный способ отучить его нажимать.
"""

from datetime import datetime

from src.dating.profile_card import profile_card_edited, profile_card_keep

# Год, раньше которого рождения не бывает: страница отдаёт год строкой, и мусор
# вроде «—» или «0» превратился бы в возраст в две тысячи лет.
IMPORT_YEAR_MIN = 1930

# Длины колонок и полей карточки, под которые режется пришедшее.
IMPORT_LOGIN_MAX = 190
IMPORT_NAME_MAX = 190
IMPORT_CITY_MAX = 190
IMPORT_PHOTO_MAX = 500
IMPORT_ABOUT_MAX = 2000

# Поля анкеты, которые импорт имеет право переписать. Всё, чего здесь нет,
# принадлежит человеку и переживает любой импорт.
IMPORT_CARD_FIELDS = ('name', 'photo', 'age', 'city', 'about')


def import_rows(extract: dict, field: str = 'анкеты') -> list[dict]:
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

        found.append({'site_uid': site_uid[:IMPORT_LOGIN_MAX],
                      'name': _text(row.get('имя'))[:IMPORT_NAME_MAX],
                      'city': _text(row.get('город'))[:IMPORT_CITY_MAX],
                      'photo_path': _text(row.get('фото'))})

    return found


def import_card(extract: dict, now: datetime) -> dict:
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
    # одна строка про человека, а колонки под неё в `profile_.profile` нет —
    # заводить её ради одного сайта значило бы менять форму кэша всем остальным.
    about = _text(extract.get('about'))
    occupation = _text(extract.get('occupation'))
    if occupation and occupation.lower() not in about.lower():
        about = f'{occupation}. {about}'.strip('. ').strip() if about else occupation

    return {'site_uid': site_uid[:IMPORT_LOGIN_MAX],
            'login': login[:IMPORT_LOGIN_MAX],
            'name': _text(extract.get('name'))[:IMPORT_NAME_MAX],
            'city': _text(extract.get('city'))[:IMPORT_CITY_MAX],
            'photo_path': _text(extract.get('photo')),
            'age': _age(extract.get('birth_year'), now),
            'about': about[:IMPORT_ABOUT_MAX]}


def import_match(row: dict, by_uid: dict, by_login: dict) -> dict | None:
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


def import_plan(row: dict, found: dict | None, base: str) -> dict:
    """Что сделать с одной записью: завести, обновить или оставить как есть.

    Args:
        row: разобранная запись — из списка либо дополненная карточкой.
        found: наша учётка, если нашлась: `id`, `login`, `card` и `edited` —
            значение колонки `profile_edited`. ⚠ Ключа нет — отметок нет,
            прежнее поведение.
        base: адрес сайта — им достраиваются относительные пути фотографий.

    Returns:
        dict: `action` (`create` | `update` | `same`), `login`, `card` — анкета в
        том виде, в каком она ложится в `profile_.profile`, и `changed` —
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
    before = dict(found.get('card') or {})
    # ⚠⚠ Правленое руками импорт не трогает — ни в карточке, ни в отчёте. Правило
    # одно на всех писателей со стороны сайта (`profile_card.profile_card_keep`),
    # и своя копия здесь разошлась бы с ними на первой правке.
    #
    # ⚠ Импорт зовёт правило **сам**, а не через общую дверь записи
    # (`profile_card_merge`), и это не упущение: у него решение и запись разведены
    # намеренно — здесь чистая функция планирует карточку, а `UPDATE` делает
    # роутер импорта. Прочие писатели (чтение страницы кабинета, кэш анкеты,
    # чтение панели) решают и пишут одним заходом, и дверь — их.
    edited = set(profile_card_edited(found.get('edited')))
    changed = [name for name in IMPORT_CARD_FIELDS
               if name not in edited and _changed(before.get(name), card.get(name))]

    # **Поверх кладём только непустое.** Иначе импорт без карточек стирал бы город
    # и описание, дочитанные импортом с карточками: список их не отдаёт вовсе, и
    # в `card` они приходят пустыми. Заметно это не сразу — пустое не считается
    # изменением, и `same` такую анкету не тронет; но стоит смениться имени, и тот
    # же прогон унёс бы вместе с ним всё, чего не было в списке.
    fresh = {name: value for name, value in card.items() if value not in (None, '', 0)}
    # ⚠ Номер (`id`) в `IMPORT_CARD_FIELDS` не входит и в `changed` не попадал
    # никогда — а в карточку импорт его всё равно кладёт. Отметка защищает и его:
    # отсев идёт по `fresh`, а не по `changed`.
    fresh = profile_card_keep(fresh, sorted(edited))

    return {'action': 'update' if changed else 'same',
            'login': str(found.get('login') or login),
            'card': {**before, **fresh},
            'changed': changed}


def import_photo(path: str, base: str) -> str:
    """Адрес фотографии: сайт отдаёт путь от своего корня, странице нужен полный.

    То же правило и та же причина, что у собеседниц (`site_profile.photo_url`):
    страница подставляет значение в `<img src>` как есть, и путь без хоста браузер
    достроит до адреса панели, где такой картинки нет.
    """
    photo = str(path or '').strip()
    if not photo or photo.startswith('http'):
        return photo

    return (str(base or '').rstrip('/') + '/' + photo.lstrip('/'))[:IMPORT_PHOTO_MAX]


# ── Приватное ────────────────────────────────────────────────────────────────

def _card_build(row: dict, base: str) -> dict:
    """Анкета в том виде, в каком её держит `profile_.profile`.

    Форма общая с кэшем анкеты, который пишет вход (`site_profile.card_parse`):
    страница читает `profile.name` и `profile.photo` независимо от того, откуда
    они взялись, и второй формы у неё быть не должно.
    """
    # ⚠⚠ Номер кладётся **одним** ключом `id`. Прежде рядом лежал `site_uid` с
    # тем же значением: вход (`site_profile.card_parse`) писал `id`, импорт писал
    # оба, — и читатели разбрелись, кто во что. Два имени одной величины значат,
    # что однажды их разведут по смыслу, не заметив.
    card = {'id': str(row.get('site_uid') or ''),
            'name': str(row.get('name') or ''),
            'photo': import_photo(row.get('photo_path'), base),
            'age': int(row.get('age') or 0),
            'city': str(row.get('city') or ''),
            'about': str(row.get('about') or '')}

    return card


def _changed(before, after) -> bool:
    """Поменялось ли поле. Пустое «стало» изменением не считается.

    Сайт отдаёт не всё и не всегда: у части анкет не заполнен город, карточку могли
    не читать вовсе. Считай мы пустоту изменением — импорт стирал бы то, что уже
    знает, и каждый прогон «обновлял» бы анкеты в никуда.
    """
    if after in (None, '', 0):
        return False

    return str(before or '') != str(after)


def _age(birth_year, now: datetime) -> int:
    """Возраст из года рождения. Мусор и будущее дают ноль, а не отрицательное."""
    try:
        year = int(str(birth_year or '').strip())
    except (TypeError, ValueError):
        return 0

    if year < IMPORT_YEAR_MIN or year > now.year:
        return 0

    return now.year - year


def _text(value) -> str:
    """Значение со страницы — строкой без краёв. `None` и числа тоже."""
    return str(value if value is not None else '').strip()
