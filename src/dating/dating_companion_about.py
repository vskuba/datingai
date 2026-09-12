"""Анкета собеседника с сайта: сбор сценарием и разбор текста.

Письма рассылки спрашивают адресата про его интересы, а интересы берутся из его
анкеты. Списки собеседников (`dating_people.py`) дают имя, фото и номер — этого
хватает, чтобы обратиться, но не хватает, чтобы спросить.

## Как устроено

Сценарий у соседа («анкета мужчины: карточка») входит **под той девушкой, от
которой пойдёт письмо**, открывает страницу мужчины по его номеру и снимает текст
блока анкеты. Разбирает этот текст модуль здесь, а результат ложится в
`dating_companion.about`.

⚠ **Гостю анкета не показывается вовсе** — сайт отдаёт 404. Отсюда и вход: без
него собирать нечего.

## ⚠ Разбираем по подписям, а не по вёрстке

Сценарий снимает **весь текст** блока одной строкой:

    Id: 352443 Location London, United Kingdom Occupation Musician Marital Status
    Single Children None … Education University … About Myself Hi, I'm a Composer
    Musician living in London … Hobby music, Composing, walking … About Partner

Разбор идёт по подписям («Occupation», «Marital Status»), потому что классы у
сайта для этого не годятся: вся анкета лежит в двух `div.user_row`, и поля внутри
слиты. Подписи же переживут перевёрстку — а если сайт переименует их, это видно
сразу и правится здесь, где есть чем проверить.

## Что попадает в описание

Не всё подряд: рост, вес и цвет глаз вопроса не рождают. Берётся то, о чём можно
спросить, — занятие, «About Myself», увлечения, образование, семейное положение.
"""

import re

from logging_.logging_ import logger_info

from src.dating.dating_ready import dating_password_pick
from src.dating.dating_scenario import dating_scenario_run
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_site_get)

# Имя сценария у соседа. ⚠ Чужое имя: так он записан в его базе, и переименование
# здесь ничего не изменит — менять надо вместе с ним.
DATING_COMPANION_SCENARIO = 'анкета мужчины: карточка'

# Подписи полей анкеты — **в том порядке, в каком сайт их выводит**. Порядок важен:
# значение поля это всё, что стоит между его подписью и следующей.
#
# ⚠ «Id:» с двоеточием, остальные без — так на странице. Расхождение не описка.
DATING_COMPANION_FIELDS = (
    'Id:', 'Location', 'Occupation', 'Marital Status', 'Children', 'Height',
    'Weight', 'Hair Color', 'Hair Style', 'Eyes Color', 'Body Type', 'Birthday',
    'Religion', 'Ethnicity', 'Education', 'Smoke', 'Drink', 'Level of English',
    'About Myself', 'Hobby', 'About Partner',
)

# Что идёт в описание и как это назвать по-русски. ⚠ Порядок — от важного к
# частному: описание читает агент, и первые строки весят больше.
DATING_COMPANION_ABOUT_FIELDS = (
    ('About Myself', 'О себе'),
    ('Hobby', 'Увлечения'),
    ('Occupation', 'Занятие'),
    ('Education', 'Образование'),
    ('Marital Status', 'Семейное положение'),
    ('Children', 'Дети'),
    ('Religion', 'Религия'),
)

# Предел описания — тот же, что у колонки `dating_companion.about`.
DATING_COMPANION_ABOUT_MAX = 4000

# Предел имени — тот же, что у колонки `dating_companion.name`.
DATING_COMPANION_NAME_MAX = 190

# Значения, которые сайт пишет вместо пустоты. ⚠ «Not Specified» в описании хуже
# отсутствия строки: агент примет это за факт и спросит про «не указано».
DATING_COMPANION_EMPTY = ('', '-', '—', 'not specified', 'none specified',
                          'no answer', 'n/a')


class DatingCompanionAboutError(Exception):
    """Собрать не вышло. Причина — словами, для журнала и страницы."""


def dating_companion_about_parse(text: str) -> dict:
    """Разбирает текст анкеты на поля.

    Args:
        text: то, что снял сценарий, — одна строка со всеми полями подряд.

    Returns:
        dict: «подпись → значение», только непустые. Ключи — как на сайте
        (`Occupation`, `About Myself`): переводить их здесь незачем, а сверять с
        сайтом по одинаковым словам проще.

    ⚠ **Значение — всё между своей подписью и следующей.** Иначе пришлось бы знать,
    из скольких слов состоит каждое, а «Location» это и «London», и «Stuttgart,
    Germany», и «New York, United States».
    """
    flat = ' '.join(str(text or '').split())
    if not flat:
        return {}

    # Где какая подпись стоит. ⚠ Ищем **по границе слова**: без неё «Children»
    # нашлось бы внутри «Childrens», а «Drink» — внутри «Drinking».
    found = []
    for label in DATING_COMPANION_FIELDS:
        match = re.search(r'(?:^|\s)' + re.escape(label) + r'(?=\s|$)', flat)
        if match:
            found.append((match.end(), label))

    found.sort()

    got = {}
    for index, (start, label) in enumerate(found):
        end = found[index + 1][0] - len(found[index + 1][1]) if index + 1 < len(found) \
            else len(flat)
        value = flat[start:end].strip(' ,;·')

        if value.lower() not in DATING_COMPANION_EMPTY:
            got[label] = value

    return got


def dating_companion_about_text(fields: dict) -> str:
    """Собирает описание для карточки из разобранных полей.

    Returns:
        str: строки «Подпись: значение», по одной на поле. Пусто — собирать было
        не из чего, и это законный исход: анкеты бывают пустыми.

    ⚠ **Берём не всё.** Рост, вес и цвет глаз вопроса не рождают, а описание
    читает агент — лишнее в нём соревнуется за внимание с полезным.

    ⚠ **«Id» и «Location» тоже не берём**: номер у карточки свой, а город и страна
    лежат отдельными колонками. Дублируй мы их в описании, правка одного места
    расходилась бы с другим.
    """
    lines = []
    for label, title in DATING_COMPANION_ABOUT_FIELDS:
        value = _tail(str(fields.get(label) or '').strip())
        if value:
            lines.append(f'{title}: {value}')

    return '\n'.join(lines)[:DATING_COMPANION_ABOUT_MAX]


def _tail(value: str) -> str:
    """Приводит значение поля в вид, годный для чтения человеком и агентом.

    Две чистки, и обе — про мусор самого сайта:

    ⚠ **Разметка.** Текст «о себе» сайт иногда отдаёт с тегами внутри — это следы
    его встроенного переводчика:

        <font style="vertical-align: inherit;">Mein Leben ist eine Achterbahn…

    В описание они уходят как есть, и агент читает их как слова: вопрос про
    интересы выходит про «vertical-align». Теги снимаем, текст оставляем.

    ⚠ **Хвост «… More»** — это кнопка, а не слова человека. В описании она читается
    как часть рассказа: «Looking for a girl to enjoy my nice life... More».
    Раскрыть текст сценарий пока не умеет, но показывать обрубок с чужим словом
    хуже, чем обрубок без него.
    """
    # Теги вырезаем, а не экранируем: содержимое между ними — тот самый текст.
    value = re.sub(r'<[^>]{0,200}>', '', str(value or ''))

    # ⚠ И **обрывок тега в конце**: сайт режет длинный текст по числу знаков, не
    # разбирая разметку, и на срезе остаётся «…dem Tod zu stellen. </fo». Целые
    # теги уже сняты, так что уцелевшая `<` в хвосте — всегда обрывок.
    value = re.sub(r'<[^<>]{0,200}$', '', value)

    # ⚠ Пробелы схлопываем **после** тегов: на их месте остаются пустые места, и
    # без этого в описании зияли бы дыры по два-три пробела.
    value = ' '.join(value.split())

    return re.sub(r'\s*\.{2,}\s*More\s*$', '…', value).strip()


def dating_companion_about_head(head: str) -> tuple:
    """Имя и возраст из шапки анкеты. Пусто — сайт их не показал.

    Args:
        head: текст блока `.top__profle-wr` — «Kevin , 58 SEND MESSAGE START LIVE
            CHAT ADD TO FAVORITES SEND FIRST LETTER Id: 352443 Location…».

    Returns:
        tuple: `(имя, возраст)`. Возраст числом; ноль — не назван.

    ⚠ **Режем по первой кнопке**, а не по запятой: имя бывает с запятой внутри
    («Kevin , 58»), а вот кнопки стоят всегда и в одном порядке. Заодно это
    отсекает хвост анкеты, который в тот же блок и попадает.

    ⚠ Имя и фото снимаем **со страницы**, а не берём из ленты: анкету открывают и
    для тех, кого в лентах не было — адресата берут из пула, а пул живёт дольше
    снимка ленты. Без этого карточка выходила безымянной, и в списке её было не
    отличить от мусора.
    """
    flat = ' '.join(str(head or '').split())
    if not flat:
        return '', 0

    # Всё до первой кнопки: их подписи сайт пишет заглавными и не переводит.
    cut = re.split(r'\s(?=SEND\b|START\b|ADD TO\b|Id:)', flat, maxsplit=1)[0].strip()

    # ⚠ Шапка без имени — не повод класть в карточку что попало. Если резать было
    # нечего и остался текст анкеты, имени на странице нет: лучше пусто, чем
    # «Id: 12345 Location Berlin» в списке собеседников.
    if cut.startswith('Id:') or 'Location' in cut:
        return '', 0

    # «Kevin , 58» → имя и возраст. Возраст всегда последним числом.
    match = re.match(r'^(.*?)[\s,]*(\d{2})\s*$', cut.strip())
    if match:
        return match.group(1).strip(' ,'), int(match.group(2))

    return cut.strip(' ,')[:DATING_COMPANION_NAME_MAX], 0


def dating_companion_about_place(fields: dict) -> tuple:
    """Город и страна из строки `Location`. Пусто — сайт их не назвал.

    ⚠ Отдельно от описания: у карточки под них свои колонки, и лежать им надо
    там — по ним ищут и их показывает список.
    """
    place = str(fields.get('Location') or '').strip()
    if not place:
        return '', ''

    parts = [part.strip() for part in place.split(',') if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[-1]

    # Одно слово — это страна: сайт пишет «Location Germany», когда города нет.
    return '', parts[0] if parts else ''


async def dating_companion_about_fetch(profile: dict, uid: str) -> dict:
    """Идёт на сайт и снимает анкету собеседника.

    Args:
        profile: анкета девушки, **под которой входим**. Её же логин и пароль.
        uid: номер собеседника на сайте — им и открывается страница.

    Returns:
        dict: `fields` — разобранные поля, `about` — готовое описание, `city`,
        `country`, `name`, `age`.

    ⚠ **Фотографию отсюда не берём.** Аватарка приходит из ленты «Все онлайн» и
    ложится в карточку при первом щелчке по человеку; снимать её второй раз со
    страницы анкеты значило бы грузить то же самое дважды — и разбираться с ленивой
    загрузкой, из-за которой в `src` попадает заглушка.

    Имя при этом снимаем: карточку заводят и под сбор описания, а тогда ленты за
    спиной нет — и собеседник остался бы безымянным.

    Raises:
        DatingCompanionAboutError: сценария нет, вход не удался, страница не
            открылась. Причина словами — её видно в журнале анкеты.

    ⚠ **Входим под той девушкой, от которой пойдёт письмо.** Не под любой: сайт
    считает просмотры анкет, и заходы «ниоткуда» выглядят иначе, чем интерес той,
    кто потом напишет.

    ⚠ **Пароль выбираем мы, а не сосед.** У анкеты есть настройка, каким паролем
    ходить — своим или общим агентским (`password_source`). Подставь сосед свою
    переменную сайта, анкета с личным паролем ходила бы под чужим.
    """
    from src.uvicorn.routers.dating.dating_site import dating_site_setting

    site_id = int(profile['site_id'])
    uid = str(uid or '').strip()
    if not uid:
        raise DatingCompanionAboutError('не назван номер собеседника')

    password = dating_password_pick(
        profile, str(await dating_site_setting(site_id, 'api_password') or ''))
    if not password:
        raise DatingCompanionAboutError(
            'нечем войти: у анкеты не задан пароль — проверьте блок «Пароль»')

    try:
        site = await rhapsody_api_site_get(site_id)
        scenario = await rhapsody_api_scenario_find(site_id, DATING_COMPANION_SCENARIO)
    except RhapsodyError as e:
        raise DatingCompanionAboutError(str(e)) from e

    if not scenario:
        raise DatingCompanionAboutError(
            f'у сайта нет сценария «{DATING_COMPANION_SCENARIO}» — '
            f'заведите его в RhapsodyAi на вкладке «Сценарии»')

    login_url = str((site or {}).get('url') or '').rstrip('/')

    try:
        # ⚠ `keep_session=False`: вкладка нужна на один прогон. Она стоит ~143 МБ в
        # пуле, общем с соседом, а анкет мы смотрим десятками.
        run = await dating_scenario_run(
            scenario, site_id, int(profile['id']), '',
            {'login_url': login_url,
             'profile_login': str(profile.get('login') or ''),
             'companion_uid': uid},
            {'girl_password': password}, keep_session=False)
    except RhapsodyError as e:
        raise DatingCompanionAboutError(str(e)) from e

    if str(run.get('status')) == 'error':
        raise DatingCompanionAboutError(
            f"сценарий встал на шаге {run.get('step_no')}: {run.get('error')}")

    extract = run.get('extract') or {}
    fields = dating_companion_about_parse(extract.get('анкета'))
    city, country = dating_companion_about_place(fields)
    name, age = dating_companion_about_head(extract.get('шапка'))

    # ⚠ Номер сверяем с тем, что просили. Сайт мог увести на чужую анкету — по
    # опечатке в номере или редиректом, — и записать чужие сведения человеку было
    # бы хуже, чем не записать ничего.
    got_uid = str(fields.get('Id:') or '').strip()
    if got_uid and got_uid != uid:
        raise DatingCompanionAboutError(
            f'открылась чужая анкета: просили {uid}, а на странице {got_uid}')

    # ⚠ Заглушку ленивой загрузки отбрасываем — то же правило, что у списков
    # (`dating_people.py`). Сайт грузит картинки лениво, и в `src` успевает попасть
    # `/images/placeholder-loading.svg`; запиши мы её в карточку, она заслонила бы
    # настоящее фото, снятое лентой, и человек увидел бы пустое место.
    return {'fields': fields,
            'about': dating_companion_about_text(fields),
            'city': city,
            'country': country,
            'name': name,
            'age': age}


async def dating_companion_about_collect(profile: dict, uid: str) -> dict:
    """Собирает анкету и кладёт описание в карточку собеседника.

    Returns:
        dict: `ok`, `about` и `error` — то, что покажет страница или запишет журнал.

    ⚠ **Пустая анкета — тоже ответ.** Сайт отдал страницу, а рассказывать человеку
    нечего: описание останется пустым, а состояние станет `empty`. Без этого
    различия круг ходил бы за одним и тем же молчуном каждый раз.
    """
    from src.uvicorn.routers.dating.dating_companion import dating_companion_touch

    try:
        got = await dating_companion_about_fetch(profile, uid)
    except DatingCompanionAboutError as e:
        logger_info(f'[dating] анкета собеседника {uid}: {e}')
        await dating_companion_touch(int(profile['site_id']), uid,
                                     about_state='fail', about_error=str(e))

        return {'ok': False, 'about': '', 'error': str(e)}

    about = str(got.get('about') or '')

    # ⚠ Пустое не пишем: `dating_companion_touch` правит только названное, и
    # передай мы пустое имя — оно затёрло бы снятое лентой.
    extra = {name: value for name, value in
             (('name', got.get('name')), ('age', got.get('age')),
              ('city', got.get('city')), ('country', got.get('country')))
             if value}

    await dating_companion_touch(
        int(profile['site_id']), uid, about=about,
        about_state='done' if about else 'empty', about_error='', **extra)

    return {'ok': True, 'about': about, 'error': ''}
