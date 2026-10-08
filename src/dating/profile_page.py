"""Анкета девушки — со страницы кабинета, а не из HTTP-ручки.

У мужской учётки на этом сайте есть JSON (`userApiV2?requestType=ManStats`), у
женской — нет: кабинет девушки собран на GWT-RPC и отдаёт бинарный поток вместо
данных. Проверено: `LadyStats` и соседние имена возвращают `{"success":true}` без
единого поля, а `ManStats` под женской учёткой отвечает 401.

Поэтому анкета читается со **страницы** — тем же сценарием (`browser_scenario`),
которым делается всё остальное на сайте. Шаги `extract` складывают значения по
именам, а имена совпадают с полями карточки: `name`, `city`, `about`, `photo`.
Новое поле в карточке — новый шаг в сценарии, а не правка Python.

Читается это **редко**: анкета меняется раз в месяцы, а браузер стоит ~143 МБ на
вкладку. Поэтому чтение прицеплено к входу и к проверке — там сессия уже открыта,
и отдельный заход не нужен. Опрос присутствия (раз в 15 секунд) сюда не заходит.
"""

import json

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load
from src.dating.profile_card import profile_card_keep
from src.dating.scenario import scenario_find

from src.dating.role import ROLE_DEFAULT, role_scenario

# Что кладём в анкету. Ключ — имя шага `extract` в сценарии, значение — поле
# карточки. Лишние имена сценария не мешают: они просто не попадут в анкету.
PROFILE_PAGE_FIELDS = ('name', 'city', 'about', 'photo', 'age', 'id')

# Из многострочного `extract` берём первую строку: селектор фотографии ловит всю
# карусель, а в карточке нужна одна — главная.
PROFILE_PAGE_FIRST = ('photo', 'name', 'city', 'age', 'id')

# Заглушка ленивой загрузки: пока карусель не долистали, в `src` лежит прозрачный
# пиксель `data:image/gif;base64,…`. Взять его как фотографию — показать пустоту.
PROFILE_PAGE_STUB = 'data:'


async def profile_page_read(profile: dict, presence: dict, session_id: str,
                                   runner) -> dict:
    """Читает анкету учётки со страницы кабинета и кладёт её в `profile_.profile`.

    Args:
        profile: строка `profile`.
        presence: настройка сайта; имя сценария берётся из `profile_scenario`.
        session_id: живая сессия браузера — та, в которой только что вошли.
        runner: чем гонять сценарий, передаётся снаружи: модуль знает про анкету,
            но не про роутеры. Принимает **запись сценария**, а не номер: журнал
            прогонов пишет имя рядом с номером (номер у соседа могут
            переименовать), и добывать его второй раз было бы незачем.

    Returns:
        dict: `ok` — записали ли, `profile` — что получилось, `error` — причина.
    """
    role = str(profile.get('role') or ROLE_DEFAULT)
    name = role_scenario(presence, 'profile_scenario', role)
    if not name:
        return {'ok': False, 'profile': {},
                'error': f'у сайта нет сценария анкеты для роли «{role}»'}

    scenario = await scenario_find(int(profile['site_id']), name)
    if not scenario:
        return {'ok': False, 'profile': {},
                'error': f'сценарий анкеты «{name}» у сайта не заведён'}

    # `login` подставляется в адрес: страница анкеты открывается по номеру учётки,
    # и одним сценарием читаются все анкеты сайта.
    result = await runner(scenario, session_id, {'login': str(profile['login'])})
    if result.get('status') == 'error':
        return {'ok': False, 'profile': {},
                'error': f"сценарий анкеты встал на шаге {result.get('step_no', 0)}: "
                         f"{str(result.get('error') or '')[:200]}"}

    card = _profile_build(result.get('extract') or {}, profile, presence)
    if not card.get('name'):
        return {'ok': False, 'profile': card, 'error': 'сценарий анкеты не вернул имя'}

    # ⚠ Наверх уходит карточка, **как она легла**, а не как её собрал сценарий:
    # правленые руками поля в ней остались прежними, и журнал обязан назвать то,
    # что теперь лежит в базе, а не то, что отдал сайт.
    stored = await _profile_save(int(profile['id']), card)

    return {'ok': True, 'profile': stored or card, 'error': ''}


def _profile_build(extract: dict, profile: dict, presence: dict) -> dict:
    """Собирает анкету из того, что вернули шаги `extract`."""
    card = {}
    for field in PROFILE_PAGE_FIELDS:
        value = str(extract.get(field) or '').strip()
        if field in PROFILE_PAGE_FIRST:
            value = _first_real(value)
        card[field] = value

    # Номер анкеты сайт печатает как «ID: 1430119» — в карточке нужен номер.
    card['id'] = ''.join(ch for ch in card['id'] if ch.isdigit()) or str(profile['login'])
    card['age'] = int(''.join(ch for ch in card['age'] if ch.isdigit()) or 0)
    card['photo'] = _photo_full(card['photo'], presence)

    return card


def _first_real(value: str) -> str:
    """Первая строка многострочного `extract`, пропуская заглушки.

    Селектор карусели ловит и ещё не подгруженные кадры — у них в `src` лежит
    прозрачный пиксель. Он не пустой, поэтому «первая непустая строка» дала бы
    именно его, и в карточке была бы дырка вместо лица.
    """
    for line in value.split('\n'):
        line = line.strip()
        if line and not line.startswith(PROFILE_PAGE_STUB):
            return line

    return ''


def _photo_full(photo: str, presence: dict) -> str:
    """Относительный адрес фотографии → полный: страница отдаёт и такой, и такой."""
    if not photo or photo.startswith('http') or photo.startswith(PROFILE_PAGE_STUB):
        return photo

    return str(presence.get('base', '')).rstrip('/') + '/' + photo.lstrip('/')


async def _profile_save(profile_id: int, profile: dict) -> dict:
    """Кладёт анкету, не затирая правленого руками. Не роняет вход.

    Args:
        profile_id: наш номер анкеты.
        profile: карточка, собранная из `extract` сценария.

    Returns:
        dict: карточка, как она **легла** — она же идёт в журнал и наверх. Пустой
        словарь значит «записать не удалось»; вход из-за этого не падает: сессия
        уже открыта, и терять её незачем.

    ## ⚠⚠ Почему слияние, а не замена

    Прежде здесь стоял один `UPDATE profile SET profile = %s` — то есть замена
    карточки целиком. С появлением правки руками это стало затиранием: человек
    вписывает описание анкете, у которой сайт его не отдал, и следующий вход
    уносит вписанное.

    ⚠ Случай не редкий, а ровно тот, ради которого правка и заводилась: чтение
    карточки заходит **только при пустой карточке**, а признак пустоты — **имя**
    (`login._card_empty`). Анкета, где впишут описание, но не впишут имя, попадает
    сюда при каждом входе.

    ## ⚠ Отметки читаются здесь, а не приезжают доводом

    Словарь анкеты собирают шесть разных запросов, и половина перечисляет колонки
    поимённо (`agent_site_uid` объясняет, чем это уже стоило). Спроси мы отметки у
    вызывающего — забытая колонка в одном из запросов тихо вернула бы затирание.
    Лишний `SELECT` случается не чаще раза на вход.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT profile, profile_edited FROM profile '
                             ' WHERE id = %s', (int(profile_id),))
            row = dict(await db.fetchone() or {})
            before = json_column_load(row.get('profile'))
            card = {**before, **profile_card_keep(profile, row.get('profile_edited'))}

            await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                             (json.dumps(card, ensure_ascii=False), int(profile_id)))
            await db.connection.commit()

        return card
    except Exception as e:
        logger_info(f'[dating] анкета не записалась: {e}')

        return {}
