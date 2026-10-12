"""Анкета собеседника с сайта: сбор сценарием и разбор текста.

Письма рассылки спрашивают адресата про его интересы, а интересы берутся из его
анкеты. Списки собеседников (`people.py`) дают имя, фото и номер — этого
хватает, чтобы обратиться, но не хватает, чтобы спросить.

## Как устроено

Сценарий у соседа («анкета мужчины: карточка») входит **под той девушкой, от
которой пойдёт письмо**, открывает страницу мужчины по его номеру и снимает текст
блока анкеты. Разбирает этот текст модуль здесь, а результат ложится в
`companion.about`.

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

import httpx

from http_.http_heartbeat import http_heartbeat_cookies
from http_.http_pool import http_pool_transport
from datetime_.datetime_ import datetime_utc_now
from logging_.logging_ import logger_info

from src.dating.json_column import json_column_load
from src.dating.ready import password_pick
from src.dating.scenario import scenario_run
from src.dating.scenario_field import scenario_field
from src.dating.site.site_registry import site_registry_about
from src.dating.site_setting import site_seconds, site_setting
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_site_get)

# ── Ход по HTTP: то же самое, но без браузера ────────────────────────────────
#
# ⚠⚠ **Страница анкеты отдаётся обычным GET, и это проверено живьём.** Сайт
# присылает готовый HTML: блок `.user__profile-info` с полями и `.top__profle-wr` с
# шапкой — те же селекторы, по которым сценарий делал `extract`. Кнопка «More» в
# разметке есть, но текст под ней **уже в ответе** — она только раскрывает, никакой
# подгрузки скриптом.
#
# ⚠⚠ **Чем это лучше сценария, и цифры не наши, а с прода.** Сценарий 16 шагов, из
# них девять — вход заново: открыть сайт, закрыть баннер, форма, логин, пароль,
# кнопка и `wait_ms 12000` после неё. Отсюда 12,4 секунды в среднем и **143 неудачи
# из 286** — половина прогонов сгорала впустую.
#
# Сессия же у анкеты **уже есть**: cookie лежат в `storage_state`, присутствие
# ходит с ними каждую минуту. Один GET с ними — доли секунды.
#
# ⚠ **Сценарий остаётся запасным.** Сессия протухает, разметка у сайта меняется, и
# путь через браузер умеет то, чего не умеет GET: войти заново. Потерять сбор
# описаний из-за одной смены класса в вёрстке было бы дорого.
# ⚠ Путь и классы блоков живут **у драйвера сайта** (`site/site_about.py`):
# вёрстка у каждого своя, и лестница условий здесь превратилась бы в место, где
# правка под один сайт молча ломает другой.

# Сколько ждём ответа. ⚠ Тридцать секунд, а не пять: страница тяжёлая (37 КБ), и
# сайт под нагрузкой отвечает не сразу. Дороже подождать, чем считать живого
# человека недоступным.
COMPANION_HTTP_TIMEOUT = 30.0

# ⚠ Представляемся браузером: сайт отдаёт страницу и без этого, но «python-httpx» в
# заголовке — примета, по которой нас отличают от людей первым же правилом.
COMPANION_HTTP_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/125.0.0.0 Safari/537.36')

# Как часто освежать анкету собеседника. Настройка сайта; ноль — снять один раз и
# больше не возвращаться.
COMPANION_ABOUT_EVERY_SETTING = 'companion_about_every'
COMPANION_ABOUT_EVERY_DEFAULT = 1209600

# Имя сценария у соседа. ⚠ Чужое имя: так он записан в его базе, и переименование
# здесь ничего не изменит — менять надо вместе с ним.
COMPANION_SCENARIO = 'анкета мужчины: карточка'

# Подписи полей анкеты — **в том порядке, в каком сайт их выводит**. Порядок важен:
# значение поля это всё, что стоит между его подписью и следующей.
#
# ⚠ «Id:» с двоеточием, остальные без — так на странице. Расхождение не описка.
COMPANION_FIELDS = (
    'Id:', 'Location', 'Occupation', 'Marital Status', 'Children', 'Height',
    'Weight', 'Hair Color', 'Hair Style', 'Eyes Color', 'Body Type', 'Birthday',
    'Religion', 'Ethnicity', 'Education', 'Smoke', 'Drink', 'Level of English',
    'About Myself', 'Hobby', 'About Partner',
)

# Что идёт в описание и как это назвать по-русски. ⚠ Порядок — от важного к
# частному: описание читает агент, и первые строки весят больше.
COMPANION_ABOUT_FIELDS = (
    ('About Myself', 'О себе'),
    ('Hobby', 'Увлечения'),
    ('Occupation', 'Занятие'),
    ('Education', 'Образование'),
    ('Marital Status', 'Семейное положение'),
    ('Children', 'Дети'),
    ('Religion', 'Религия'),
)

# Предел описания — тот же, что у колонки `companion.about`.
COMPANION_ABOUT_MAX = 4000

# Предел имени — тот же, что у колонки `companion.name`.
COMPANION_NAME_MAX = 190

# Значения, которые сайт пишет вместо пустоты. ⚠ «Not Specified» в описании хуже
# отсутствия строки: агент примет это за факт и спросит про «не указано».
COMPANION_EMPTY = ('', '-', '—', 'not specified', 'none specified',
                          'no answer', 'n/a')


class DatingCompanionAboutError(Exception):
    """Собрать не вышло. Причина — словами, для журнала и страницы."""


def companion_about_parse(text: str) -> dict:
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
    for label in COMPANION_FIELDS:
        match = re.search(r'(?:^|\s)' + re.escape(label) + r'(?=\s|$)', flat)
        if match:
            found.append((match.end(), label))

    found.sort()

    got = {}
    for index, (start, label) in enumerate(found):
        end = found[index + 1][0] - len(found[index + 1][1]) if index + 1 < len(found) \
            else len(flat)
        value = flat[start:end].strip(' ,;·')

        if value.lower() not in COMPANION_EMPTY:
            got[label] = value

    return got


def companion_about_text(fields: dict) -> str:
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
    for label, title in COMPANION_ABOUT_FIELDS:
        value = _tail(str(fields.get(label) or '').strip())
        if value:
            lines.append(f'{title}: {value}')

    return '\n'.join(lines)[:COMPANION_ABOUT_MAX]


def companion_about_head(head: str) -> tuple:
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

    # ⚠⚠ **Регистр любой, и это не придирка.** Браузер отдаёт подписи кнопок
    # заглавными — «SEND MESSAGE», — потому что их так рисует CSS
    # (`text-transform`). В самом HTML они написаны как есть: «Send message». Пока
    # резали только по заглавным, имя со страницы приходило с хвостом
    # «, 59 new Live 3 Send message Start live chat Add to Favorites».
    #
    # ⚠ `new` и `Live` — тоже не часть имени: это значки «новенький» и «в эфире»,
    # они стоят между именем и кнопками.
    cut = re.split(r'\s(?=send\b|start\b|add to\b|new\b|live\b|Id:)',
                   flat, maxsplit=1, flags=re.IGNORECASE)[0].strip()

    # ⚠ Шапка без имени — не повод класть в карточку что попало. Если резать было
    # нечего и остался текст анкеты, имени на странице нет: лучше пусто, чем
    # «Id: 12345 Location Berlin» в списке собеседников.
    if cut.startswith('Id:') or 'Location' in cut:
        return '', 0

    # «Kevin , 58» → имя и возраст. Возраст всегда последним числом.
    match = re.match(r'^(.*?)[\s,]*(\d{2})\s*$', cut.strip())
    if match:
        return match.group(1).strip(' ,'), int(match.group(2))

    return cut.strip(' ,')[:COMPANION_NAME_MAX], 0


def companion_about_place(fields: dict) -> tuple:
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


async def companion_about_http(profile: dict, uid: str) -> dict:
    """Снимает анкету собеседника **обычным GET**, под cookie анкеты.

    Args:
        profile: анкета девушки — её cookie и её выход наружу.
        uid: номер собеседника на сайте.

    Returns:
        dict: то же, что у сценария — `fields`, `about`, `city`, `country`,
        `name`, `age`.

    Raises:
        DatingCompanionAboutError: нет cookie, сайт ответил не тем, страница не
            разобралась. Причину видно в журнале анкеты.

    ⚠⚠ **Быстрее сценария на два порядка.** Тот тратил 12,4 секунды в среднем, из
    них двенадцать — ожидание после входа, который делался заново на каждого
    человека. Здесь сессия уже есть: cookie лежат в `storage_state`, и присутствие
    ходит с ними каждую минуту.

    ⚠ **Выход наружу — тот же, что у всех дверей анкеты** (`proxy.py`): сайт
    считает адреса, и поход за анкетой с чужого IP выдал бы нас вернее любого
    другого признака.

    ⚠ **Чужая анкета — отказ, а не тихая подмена.** Сайт уводит переадресацией на
    форму входа, когда сессия протухла; без проверки номера мы записали бы человеку
    чужое описание. Тот же довод, что у сценария.
    """
    from src.dating.proxy import proxy_url

    site_id = int(profile['site_id'])
    uid = str(uid or '').strip()
    if not uid:
        raise DatingCompanionAboutError('не назван номер собеседника')

    # ⚠ Адрес берём **из снимка сайта**, а не из настройки: сценарий берёт его
    # оттуда же (`login_url` в его переменных), и второй источник разошёлся бы с
    # первым. Настройки `login_url` у сайта нет вовсе — на этом уже спотыкались.
    try:
        site = await rhapsody_api_site_get(site_id)
    except RhapsodyError as e:
        raise DatingCompanionAboutError(f'адрес сайта не спросился: {e}') from e

    base = str((site or {}).get('url') or '').rstrip('/')
    if not base:
        raise DatingCompanionAboutError('у сайта не записан адрес')

    cookies = http_heartbeat_cookies(
        json_column_load(profile.get('storage_state')), base)
    if not cookies:
        raise DatingCompanionAboutError(
            'у анкеты нет живой сессии — войдите под ней')

    # ⚠ Чем разбирать эту страницу, знает драйвер сайта: путь, классы блоков и
    # способ достать из них текст. Настройка та же, что у чата и присутствия —
    # `presence` сайта (`site_about.py`).
    driver = site_registry_about(
        json_column_load(await site_setting(site_id, 'presence')))
    url = driver.url(base, uid)

    # ⚠ Клиент разовый, транспорт общий: cookie у каждой анкеты свои, а пул
    # соединений один на всех (`http_pool.py`). Закрывать клиент нельзя — унесёт
    # транспорт у всех.
    client = httpx.AsyncClient(transport=http_pool_transport(), cookies=cookies,
                               follow_redirects=True,
                               timeout=COMPANION_HTTP_TIMEOUT,
                               proxy=await proxy_url(profile) or None)

    try:
        answer = await client.get(url, headers={'user-agent': COMPANION_HTTP_UA})
    except Exception as e:
        raise DatingCompanionAboutError(f'страница не открылась: {e}') from e

    if answer.status_code != 200:
        raise DatingCompanionAboutError(
            f'сайт ответил {answer.status_code} на анкету {uid}')

    blocks = driver.parse(answer.text)

    if not scenario_field(blocks, 'profile'):
        # ⚠ Пустой блок — это не «у человека ничего не написано», а «страница не
        # та»: сессия протухла и нас увели на вход, либо сайт поменял вёрстку.
        # Отличить важно: первое лечится входом, второе — правкой класса.
        raise DatingCompanionAboutError(
            'на странице нет блока анкеты — сессия протухла или сайт сменил вёрстку')

    return _from_blocks(blocks, uid)


async def companion_about_fetch(profile: dict, uid: str) -> dict:
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
    site_id = int(profile['site_id'])
    uid = str(uid or '').strip()
    if not uid:
        raise DatingCompanionAboutError('не назван номер собеседника')

    password = password_pick(
        profile, str(await site_setting(site_id, 'api_password') or ''))
    if not password:
        raise DatingCompanionAboutError(
            'нечем войти: у анкеты не задан пароль — проверьте блок «Пароль»')

    try:
        site = await rhapsody_api_site_get(site_id)
        scenario = await rhapsody_api_scenario_find(site_id, COMPANION_SCENARIO)
    except RhapsodyError as e:
        raise DatingCompanionAboutError(str(e)) from e

    if not scenario:
        raise DatingCompanionAboutError(
            f'у сайта нет сценария «{COMPANION_SCENARIO}» — '
            f'заведите его в RhapsodyAi на вкладке «Сценарии»')

    login_url = str((site or {}).get('url') or '').rstrip('/')

    try:
        # ⚠ `keep_session=False`: вкладка нужна на один прогон. Она стоит ~143 МБ в
        # пуле, общем с соседом, а анкет мы смотрим десятками.
        run = await scenario_run(
            scenario, site_id, int(profile['id']), '',
            {'login_url': login_url,
             'profile_login': str(profile.get('login') or ''),
             'companion_uid': uid},
            {'girl_password': password}, keep_session=False,
            proxy_id=int(profile.get('proxy_id') or 0))
    except RhapsodyError as e:
        raise DatingCompanionAboutError(str(e)) from e

    if str(run.get('status')) == 'error':
        raise DatingCompanionAboutError(
            f"сценарий встал на шаге {run.get('step_no')}: {run.get('error')}")

    extract = run.get('extract') or {}
    fields = companion_about_parse(scenario_field(extract, 'profile'))
    city, country = companion_about_place(fields)
    name, age = companion_about_head(scenario_field(extract, 'head'))

    # ⚠ Номер сверяем с тем, что просили. Сайт мог увести на чужую анкету — по
    # опечатке в номере или редиректом, — и записать чужие сведения человеку было
    # бы хуже, чем не записать ничего.
    got_uid = str(fields.get('Id:') or '').strip()
    if got_uid and got_uid != uid:
        raise DatingCompanionAboutError(
            f'открылась чужая анкета: просили {uid}, а на странице {got_uid}')

    # ⚠ Заглушку ленивой загрузки отбрасываем — то же правило, что у списков
    # (`people.py`). Сайт грузит картинки лениво, и в `src` успевает попасть
    # `/images/placeholder-loading.svg`; запиши мы её в карточку, она заслонила бы
    # настоящее фото, снятое лентой, и человек увидел бы пустое место.
    return {'fields': fields,
            'about': companion_about_text(fields),
            'city': city,
            'country': country,
            'name': name,
            'age': age}


async def companion_about_due(site_id: int, card: dict) -> bool:
    """Пора ли снимать анкету этого человека.

    Args:
        site_id: сайт-закладка — по нему берётся срок.
        card: карточка собеседника из базы (или пустая, если её ещё нет).

    Returns:
        bool: `True` — идти на сайт.

    ## ⚠⚠ Здесь и решается, когда мы ходим

    Отдельного круга обновления нет и не заводится. Вопрос задаётся там, где
    карточку **и так** заводят или подтверждают, — то есть ровно тогда, когда
    человек попал в работу. Не попал никто — и ходить незачем.

    | Что в карточке | Идём? |
    |----------------|-------|
    | карточки нет вовсе | да — это новый человек |
    | `about_state = none` | да — анкету ещё не снимали |
    | снято, и срок вышел | да — освежаем |
    | снято недавно | нет |
    | `fail` | нет: причина обычно не рассосётся сама |

    ⚠ **`empty` освежаем наравне с `done`.** «Мужчина ничего о себе не написал» —
    состояние сегодняшнего дня, а не приговор: через две недели он заполнит анкету,
    и это ровно тот случай, ради которого срок и заведён.

    ⚠ **Ноль в настройке значит «снять один раз и не возвращаться»**, а не «не
    ходить вовсе»: новый человек всё равно будет снят — иначе описаний не появится
    ни у кого.

    ⚠ Сбой чтения настройки отвечает «не пора»: лишний поход дешевле
    пропущенного, но не настолько, чтобы ходить на каждой ошибке.
    """
    state = str((card or {}).get('about_state') or 'none')

    if not card or state == 'none':
        return True

    if state == 'fail':
        return False

    when = (card or {}).get('about_at')
    if not when:
        return True

    try:
        every = int(await site_seconds(int(site_id), COMPANION_ABOUT_EVERY_SETTING)
                    or COMPANION_ABOUT_EVERY_DEFAULT)
    except Exception as e:
        logger_info(f'[dating] срок обновления анкет сайта #{site_id} не прочитался: {e}')

        return False

    if every <= 0:
        return False

    return (datetime_utc_now() - when).total_seconds() >= every


async def companion_about_collect(profile: dict, uid: str) -> dict:
    """Собирает анкету и кладёт описание в карточку собеседника.

    Returns:
        dict: `ok`, `about` и `error` — то, что покажет страница или запишет журнал.

    ⚠ **Пустая анкета — тоже ответ.** Сайт отдал страницу, а рассказывать человеку
    нечего: описание останется пустым, а состояние станет `empty`. Без этого
    различия круг ходил бы за одним и тем же молчуном каждый раз.
    """
    from src.uvicorn.routers.dating.companion import companion_touch

    # ⚠⚠ **Сперва GET, сценарий — запасным.** Страница отдаётся обычным запросом
    # под cookie анкеты: доли секунды против 12,4 секунды у браузера, и без
    # половины неудач, которые давал вход заново (143 из 286 на проде).
    #
    # ⚠ Запасной путь нужен по-настоящему: сессия протухает, и войти заново умеет
    # только браузер. Потерять сбор описаний из-за этого дороже минуты, которую он
    # стоит.
    try:
        got = await companion_about_http(profile, uid)
    except DatingCompanionAboutError as first:
        logger_info(f'[dating] анкета {uid} по HTTP не снялась ({first}) — '
                    f'идём сценарием')

        try:
            got = await companion_about_fetch(profile, uid)
        except DatingCompanionAboutError as e:
            logger_info(f'[dating] анкета собеседника {uid}: {e}')
            await companion_touch(int(profile['site_id']), uid,
                                  about_state='fail', about_error=str(e))

            return {'ok': False, 'about': '', 'error': str(e)}

    about = str(got.get('about') or '')

    # ⚠ Пустое не пишем: `companion_touch` правит только названное, и
    # передай мы пустое имя — оно затёрло бы снятое лентой.
    extra = {name: value for name, value in
             (('name', got.get('name')), ('age', got.get('age')),
              ('city', got.get('city')), ('country', got.get('country')))
             if value}

    await companion_touch(
        int(profile['site_id']), uid, about=about,
        about_state='done' if about else 'empty', about_error='', **extra)

    return {'ok': True, 'about': about, 'error': ''}


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


def _from_blocks(blocks: dict, uid: str) -> dict:
    """Разбирает снятые блоки в поля. ⚠ Общий для GET и сценария.

    Разбор один на оба пути намеренно: страница одна и та же, и второй его вид
    разошёлся бы с первым на первой же правке подписей.
    """
    fields = companion_about_parse(scenario_field(blocks, 'profile'))
    city, country = companion_about_place(fields)
    name, age = companion_about_head(scenario_field(blocks, 'head'))

    got_uid = str(fields.get('Id:') or '').strip()
    if got_uid and got_uid != str(uid):
        raise DatingCompanionAboutError(
            f'открылась чужая анкета: просили {uid}, а на странице {got_uid}')

    return {'fields': fields, 'about': companion_about_text(fields),
            'city': city, 'country': country, 'name': name, 'age': age}
