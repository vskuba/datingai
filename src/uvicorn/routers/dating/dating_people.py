"""Собеседники анкеты с сайта: избранные и кто в сети.

Браузер водит RhapsodyAi — он входит под анкетой в её кабинет и снимает карточки
со страницы. Отсюда идут только вызовы сценариев; разбор ответа — в
`src/dating/dating_people.py`.

## ⚠ Сайт нельзя дёргать часто

Каждый список — это **вход в живой кабинет и прогон сценария**: секунды работы
браузера и запрос к сайту от имени анкеты. Частые заходы сайт считает подозрением
и отвечает блокировкой, а блокировка стоит анкеты, а не запроса.

Поэтому здесь **кэш с выдержкой** (`DATING_PEOPLE_FRESH_SEC`, пять минут), а не
троттлинг отказом. Разница принципиальная: страница спрашивает список сколько
угодно — хоть каждую секунду перерисовки, — и получает последний снятый, пока он
свежий. Сценарий при этом гоняется не чаще выдержки.

Под выдержкой лежит второй порог — **жёсткий пол** (`DATING_PEOPLE_FLOOR_SEC`,
пятнадцать секунд): его не обходит даже кнопка «обновить». Выдержка про то, когда
список стоит освежить; пол — про то, чего не выдержит сайт.

Отказом (429) отвечать было бы хуже: страница не должна знать про лимит сайта и
уж точно не должна показывать человеку «слишком часто» вместо списка, который у
нас уже есть.

Кэш живёт **в памяти процесса** и не переживает перезапуск. Своей таблицы у этих
списков нет намеренно: они про «кто на сайте прямо сейчас», и вчерашний снимок не
нужен никому. Цена — после перезапуска первый заход снова гонит сценарий.
"""

import time

from fastapi import APIRouter, Depends, HTTPException

from logging_.logging_ import logger_info

from src.auth.auth import auth_required
from src.dating.dating_people import dating_people_rows
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_scenario_run)
from src.uvicorn.routers.dating.dating_profile import dating_profile_row, dating_profile_site

router = APIRouter(prefix='/api/dating/people', tags=['dating'])

# Сколько снятый список считается свежим. Пять минут, а не пятнадцать секунд:
# прогон стоит **40 секунд живого браузера**, а состав избранных и тех, кто в
# сети, за пять минут меняется мало. Держать выдержку у самого предела значило бы
# платить сорока секундами за обновление, которого никто не просил.
DATING_PEOPLE_FRESH_SEC = 300

# ⚠ **Жёсткий пол: чаще этого к сайту не ходим ни при каких условиях.** Отдельно
# от выдержки, потому что закрывает другой случай — кнопку «обновить», которая
# кэш обходит намеренно. Нажать её десять раз подряд легко, и без пола это были бы
# десять входов в кабинет за минуту: сайт считает такое подозрением и отвечает
# блокировкой, а блокировка стоит анкеты, а не запроса.
#
# По достижении пола отдаём прежний снимок, а не отказ: у нас он есть, и показать
# его полезнее, чем сообщить «слишком часто».
DATING_PEOPLE_FLOOR_SEC = 15

# Виды списков: имя вида → (сценарий у соседа, имя шага, подстановки).
#
# Таблицей, а не двумя ручками: у списков одна механика и разное лишь то, какой
# сценарий гнать. Третий вид («кто добавил её в избранные», `favorites_side`)
# заводится строкой здесь, без нового кода.
DATING_PEOPLE_KINDS = {
    'favorite': {'scenario': 'избранные собеседники: список',
                 'field': 'избранные',
                 'variables': {'favorites_side': 'favorites'}},
    'online': {'scenario': 'анкеты мужчин: список',
               'field': 'мужчины',
               'variables': {'status_filter': 'online'}},
    # Диалоги снимаются со страницы чата, а не из кабинета: там список тех, с кем
    # переписка уже есть. Отдельного класса у строк на той странице нет вовсе —
    # сценарий отбирает их по наличию фотографии внутри (`li:has(img)`), чем и
    # отсекает вкладки «ONLINE MEN» и «FAVORITES».
    'dialog': {'scenario': 'чат: список диалогов',
               'field': 'диалоги',
               'variables': {}},
}

# Снятые списки: ключ `(profile_id, вид)`, значение `(момент, записи)`. Момент —
# монотонные часы, а не календарные: меряется «сколько прошло внутри процесса», и
# рывок NTP назад не должен объявлять кэш свежим на лишние часы. Тем же меряет
# сроки держатель сокетов.
_people_cache: dict[tuple[int, str], tuple[float, list]] = {}


@router.get('/{profile_id}/{kind}', include_in_schema=False)
async def dating_people_list(profile_id: int, kind: str, fresh: bool = False,
                             user=Depends(auth_required)):
    """Список собеседников анкеты с сайта.

    Args:
        profile_id: наша учётка.
        kind: `favorite` — избранные анкеты, `online` — кто сейчас в сети.
        fresh: снять заново, не дожидаясь конца выдержки. Ставит страница по
            кнопке «обновить»; сам по себе круг перерисовки его не ставит.
            Жёсткий пол (`DATING_PEOPLE_FLOOR_SEC`) этот флаг **не обходит**.

    Returns:
        dict: `people` — записи, `cached` — отдан ли снимок из кэша, `age_sec` —
        сколько ему секунд, `error` — если сценарий не отработал.

    Raises:
        HTTPException: 404 — нет учётки; 422 — у сайта нет нужного сценария;
            502 — RhapsodyAi не ответил.

    ⚠ **Отказ сценария приходит с кодом 200** и текстом в `error`. Правило то же,
    что у импорта: код ответа говорит про запрос, а не про то, что вышло на живом
    сайте. Сайт сменил вёрстку или не пустил анкету — это ответ, а не поломка
    панели, и страница должна показать причину, а не «что-то пошло не так».
    """
    if kind not in DATING_PEOPLE_KINDS:
        raise HTTPException(status_code=422, detail=f'неизвестный список «{kind}»')

    profile = await dating_profile_row(profile_id)
    site = await dating_profile_site(int(profile['site_id']))

    # Свежий снимок отдаём не спрашивая сайт — ради этого кэш и существует.
    # Кнопка «обновить» (`fresh`) выдержку обходит, но пол — нет: он про предел
    # самого сайта, а не про то, чего хочет страница.
    cached = _people_cache.get((profile_id, kind))
    if cached:
        age = time.monotonic() - cached[0]
        limit = DATING_PEOPLE_FLOOR_SEC if fresh else DATING_PEOPLE_FRESH_SEC
        if age < limit:
            return {'result': {'people': cached[1], 'cached': True,
                               'age_sec': int(age), 'error': ''}}

    spec = DATING_PEOPLE_KINDS[kind]
    try:
        scenario = await rhapsody_api_scenario_find(int(site['id']), spec['scenario'])
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not scenario:
        raise HTTPException(
            status_code=422,
            detail=f"у сайта нет сценария «{spec['scenario']}»: "
                   f'заведите его в RhapsodyAi на вкладке «Сценарии»')

    # Логин анкеты передаём мы, пароль — переменная сайта у соседа (`girl_password`):
    # у агентства он общий на все анкеты, а импортированные анкеты своего пароля и
    # не имеют — панель его не показывает.
    #
    # `keep_session=False`: вкладка нужна на один прогон, и держать её незачем —
    # она стоит ~143 МБ в пуле, общем с соседом.
    try:
        run = await rhapsody_api_scenario_run(
            int(scenario['id']), '',
            {**spec['variables'], 'profile_login': str(profile['login'])}, {},
            keep_session=False)
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if str(run.get('status')) == 'error':
        # Прежний снимок при отказе не выбрасываем: список, снятый минуту назад,
        # человеку полезнее пустоты. Но говорим, что он старый.
        stale = cached[1] if cached else []
        logger_info(f"[dating] список «{kind}» #{profile_id} не снялся: {run.get('error')}")

        return {'result': {'people': stale, 'cached': bool(stale), 'age_sec': -1,
                           'error': f"сценарий встал на шаге {run.get('step_no')}: "
                                    f"{run.get('error')}"}}

    people = dating_people_rows(run.get('extract') or {}, spec['field'])
    _people_cache[(profile_id, kind)] = (time.monotonic(), people)

    return {'result': {'people': people, 'cached': False, 'age_sec': 0, 'error': ''}}
