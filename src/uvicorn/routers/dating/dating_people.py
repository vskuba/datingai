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

## ⚠ Отказ соседа не выбрасывает снимок

То же правило и на другом краю: **не вышло снять заново — отдаём прежнее**, а не
502. Поводов не получить список три (занят браузер соседа, оборвалась связь, встал
сценарий), и все они про «сейчас не вышло», а не про «того, что мы знали, больше
нет». Пятиминутной давности список полезнее пустого места с красной надписью.

Самый частый повод — **409 от соседа**: пул Chromium у него на 25 вкладок и общий
с его собственными задачами, и свободной может не оказаться. Панель на это влиять
не может и падать от этого не должна.

Причина при этом не проглатывается: она едет в ответе (`error`), в журнале анкеты
и в логе процесса. Молчаливо показывать старое — худший вид вранья: человек
смотрит на список и не знает, что тот с прошлого часа.

## Кэш живёт в Redis и переживает перезапуск

Снимок хранится **неделю** (`DATING_PEOPLE_KEEP_SEC`) и показывается всё это время,
пока сценарий не принесёт новый. Не «пока не протухнет»: протухший снимок нечем
заменить, и выбросив его, мы поменяли бы список на слово «гружу…» — а под ним
сорок секунд ожидания и, может быть, отказ занятого браузера.

Раньше кэш лежал в памяти процесса, и это было ошибкой: перезапуск панели —
дело обычное, а после него все колонки заново гнали сценарии, то есть заново
входили на сайт под каждой анкетой. Ровно то, чего кэш и должен избегать.

Redis, а не своя таблица: это кэш со сроком годности, а не данные. Срок Redis
считает сам, чистить некому и нечего, а потерять его при сбросе Redis не жалко —
следующий заход снимет заново.

⚠ **Показанное из кэша всегда помечено.** Иначе человек смотрит на список и не
знает, сегодняшний он или недельной давности; пометка со временем снимка — цена
за то, чтобы старое можно было показывать спокойно.
"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException

from datetime_.datetime_ import datetime_utc_now
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.auth.auth import auth_required
from src.dating.dating_log import dating_log_write
from src.dating.dating_people import dating_people_rows
from src.dating.dating_scenario import dating_scenario_run
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_scenario_forget)
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

# Как список зовётся в журнале анкеты. Отдельно от ключа: в ленте рядом со строками
# «Вход подтверждён» и «Автоответ включён» слово `favorite` выглядит чужим.
#
# Название — подлежащее целой фразы («Избранные с сайта: 18»), поэтому и звучать
# должно как подлежащее: «Диалоги», а не «Список диалогов».
DATING_PEOPLE_TITLES = {'favorite': 'Избранные', 'online': 'Кто в сети',
                        'dialog': 'Диалоги'}

# Сколько снимок хранится и показывается. Неделя — не «сколько он годен», а
# «сколько его есть чем заменить»: пока сценарий не принёс новый список, старый
# остаётся единственным, что мы знаем о собеседниках анкеты.
#
# Выбросив его по сроку, мы поменяли бы список на слово «гружу…», под которым
# сорок секунд ожидания и, может быть, отказ занятого браузера. Помеченный
# недельный список честнее пустоты.
DATING_PEOPLE_KEEP_SEC = 7 * 24 * 3600

# Ключ снимка в Redis. С именем проекта в начале: Redis общий с очередью `dating`
# и с однопоточным замком воркера, и ключи разных хозяйств не должны сталкиваться.
DATING_PEOPLE_KEY = 'dating:people:{profile_id}:{kind}'

# Ключ снимка по сайту. Отдельный, а не тот же с чужим номером: списки по анкете и
# по сайту живут рядом, и общий вид ключа однажды свёл бы их в один.
DATING_PEOPLE_SITE_KEY = 'dating:people:site:{site_id}:{kind}'

# Идущие фоновые обновления: `(profile_id, вид)` → задача. Сторож от того, чтобы
# круг перерисовки завёл десять прогонов подряд; подробности — в `_refresh_start`.
_refreshing: dict[tuple[int, str], object] = {}

# Когда в последний раз **пробовали** обновить — удачно или нет. Второй сторож:
# без него сорвавшийся прогон запускался бы заново на каждом опросе страницы.
_refresh_at: dict[tuple[int, str], float] = {}

# Чем кончилась последняя попытка обновить, если неудачно. Ответ ручки уходит
# раньше, чем фоновый прогон добирается до конца, — и без этой памяти колонка
# показывала бы «кеш от 29.08» бесконечно, ни словом не обмолвившись, что новый
# список не снимается уже которую попытку.
_refresh_error: dict[tuple[int, str], str] = {}


@router.get('/site/{site_id}/{kind}', include_in_schema=False)
async def dating_people_site_list(site_id: int, kind: str, fresh: bool = False,
                                  user=Depends(auth_required)):
    """Список собеседников **по сайту**, а не по анкете.

    Args:
        site_id: сайт-закладка у соседа.
        kind, fresh: то же, что у списка по анкете.

    Returns:
        dict: та же форма, что у `dating_people_list`.

    Raises:
        HTTPException: 422 — неизвестный вид списка или у сайта нет ни одной
            анкеты, под которой можно войти.

    ⚠ **«Кто в сети» — свойство сайта, а не анкеты.** Список мужчин, зашедших на
    сайт, один и тот же, под какой учёткой на него ни смотри. Держать его отдельно
    для каждой анкеты значило бы снимать один и тот же список по разу на анкету —
    десять входов на сайт вместо одного, ради одинакового ответа.

    Анкета всё равно нужна: сценарий заходит в кабинет, а кабинет нужен чей-то.
    Берём любую (`_site_profile`) — какая именно, на результат не влияет.
    """
    if kind not in DATING_PEOPLE_KINDS:
        raise HTTPException(status_code=422, detail=f'неизвестный список «{kind}»')

    key = _key(kind, site_id=site_id)
    cached = _cache_get(key)

    # Анкету ищем только когда собираемся ходить на сайт: свежий снимок отдаётся
    # и без неё, а лишний запрос в базу на каждую перерисовку страницы не нужен.
    if cached and time.time() - cached['at'] < (DATING_PEOPLE_FLOOR_SEC if fresh
                                                else DATING_PEOPLE_FRESH_SEC):
        return _answer(cached, cached=True, error='')

    profile = await _site_profile(site_id)
    if not profile:
        raise HTTPException(status_code=422,
                            detail='у сайта нет ни одной анкеты — под кем заходить?')

    return await _list(kind, fresh, profile, key, log_id=0, cached=cached)


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
        сколько ему секунд (`-1`, если снимка нет вовсе), `taken_at` — когда снят,
        `error` — если снять заново не вышло.

        `cached: true` — не пометка «данные похуже», а факт, который страница
        обязана показать: список мог быть снят неделю назад.

    Raises:
        HTTPException: 404 — нет учётки; 422 — неизвестный вид списка.

    ⚠ **Неудача приходит с кодом 200** и текстом в `error`. Правило то же, что у
    импорта: код ответа говорит про запрос, а не про то, что вышло на живом сайте.
    Сайт сменил вёрстку, не пустил анкету, у соседа не нашлось свободной вкладки —
    это ответы, а не поломка панели, и страница должна показать причину рядом с
    прежним списком, а не заменить его словами «шлюз не отвечает».
    """
    if kind not in DATING_PEOPLE_KINDS:
        raise HTTPException(status_code=422, detail=f'неизвестный список «{kind}»')

    # ⚠ **Сайт спрашиваем не здесь, а внутри прогона.** Он живёт у соседа, и
    # `dating_profile_site` при его недоступности отвечает 502 — то есть недоступный
    # сосед гасил колонку целиком, хотя снимок лежал в кэше и был готов к показу.
    # Ровно то, от чего кэш и заведён; поймано сверкой этого файла с документацией.
    #
    # Кэшу сайт не нужен вовсе: он нужен прогону, чтобы найти сценарий.
    profile = await dating_profile_row(profile_id)

    return await _list(kind, fresh, profile, _key(kind, profile_id=profile_id),
                       log_id=profile_id)


# ── Приватное ────────────────────────────────────────────────────────────────

async def _list(kind: str, fresh: bool, profile: dict, key: str, log_id: int,
                cached: dict | None = None) -> dict:
    """Общее тело обоих списков: кэш, фоновое обновление, запись в журнал.

    Args:
        kind: вид списка.
        fresh: обходить выдержку (но не жёсткий пол).
        profile: под какой анкетой заходить на сайт.
        key: ключ снимка в Redis — по анкете или по сайту.
        log_id: чей журнал ведём. Ноль — списка по сайту: он не про одну анкету, и
            писать его в журнал случайно выбранной было бы враньём. След прогона
            при этом остаётся в журнале прогонов (`dating_scenario.py`).
        cached: уже прочитанный снимок, если вызывающий его читал.
    """
    cached = cached if cached is not None else _cache_get(key)
    if cached:
        age = time.time() - cached['at']

        # Свежий снимок отдаём не спрашивая сайт — ради этого кэш и существует.
        # Кнопка «обновить» (`fresh`) выдержку обходит, но пол — нет: он про предел
        # самого сайта, а не про то, чего хочет страница.
        if age < (DATING_PEOPLE_FLOOR_SEC if fresh else DATING_PEOPLE_FRESH_SEC):
            return _answer(cached, cached=True, error='')

        # ⚠ **Устаревший снимок отдаём сразу, а сценарий гоним в фоне.**
        #
        # Дожидаться прогона здесь значило бы держать страницу сорок секунд (а с
        # очередью — и дольше) на слове «гружу…», имея на руках готовый список.
        # Человеку нужен ответ на «кто там на сайте», и вчерашний ответ он
        # прочтёт сейчас, а сегодняшний — через минуту, сам.
        #
        # `refreshing` говорит странице, что стоит вернуться за свежим, а `error` —
        # чем кончилась прошлая попытка, если она сорвалась.
        return _answer(cached, cached=True,
                       error=_refresh_error.get(key, ''),
                       refreshing=_refresh_start(profile, kind, key, log_id))

    # Снимка нет вовсе — вот тут ждём: показать нечего, и «гружу…» честно.
    try:
        people = await _snapshot(profile, kind)
    except _PeopleFailure as e:
        # ⚠ **Отказ не выбрасывает прежний снимок.** Занятый браузер соседа (409),
        # оборванная связь, упавший сценарий — всё это про «сейчас снять не
        # вышло», а не про «того, что мы знали, больше нет». Список, снятый пять
        # минут назад, человеку полезнее пустого места с красной надписью.
        #
        # Поэтому здесь 200 с прежними записями и причиной рядом, а не 502.
        if log_id:
            await dating_log_write(
                log_id, 'fail',
                f'{DATING_PEOPLE_TITLES.get(kind, kind)} с сайта не снялись: {e}')
        logger_info(f'[dating] список «{key}» не снялся: {e}')

        return _answer(cached, cached=bool(cached), error=str(e))

    snapshot = {'at': time.time(),
                'when': datetime_utc_now().strftime('%Y-%m-%d %H:%M:%S'),
                'people': people}
    _cache_put(key, snapshot)

    if log_id:
        await dating_log_write(log_id, 'ok',
                               f'{DATING_PEOPLE_TITLES.get(kind, kind)} с сайта: '
                               f'{len(people)} собеседников')

    return _answer(snapshot, cached=False, error='')


class _PeopleFailure(RuntimeError):
    """Снять список не вышло. Текст пригоден для журнала анкеты и для страницы."""


def _refresh_start(profile: dict, kind: str, key: str, log_id: int) -> bool:
    """Заводит фоновое обновление снимка, если его пора и можно заводить.

    Returns:
        bool: идёт ли обновление прямо сейчас — своё или начатое раньше.

    ⚠ **Два сторожа, и оба обязательны.**

    Первый — «уже идёт»: страница спрашивает список, пока ждёт результата, и без
    него каждый опрос плодил бы ещё один прогон.

    Второй — **выдержка между попытками**. Он закрывает случай, который первый
    пропускает: прогон не удался, снимок остался старым, и следующий же опрос
    завёл бы новую попытку. Раз в пятнадцать секунд, бесконечно — то есть ровно
    то, от чего бережёт кэш. Поэтому попытка считается попыткой независимо от
    того, чем кончилась, и следующая — не раньше конца выдержки.
    """
    task = _refreshing.get(key)
    if task and not task.done():
        return True

    if time.time() - _refresh_at.get(key, 0.0) < DATING_PEOPLE_FRESH_SEC:
        return False

    _refresh_at[key] = time.time()
    _refreshing[key] = asyncio.ensure_future(_refresh_run(profile, kind, key, log_id))

    return True


async def _refresh_run(profile: dict, kind: str, key: str, log_id: int) -> None:
    """Снимает список в фоне и переписывает кэш. Не бросает: ждать его некому.

    Ошибку пишем в журнал анкеты — там её и ищут. В ответ ручки она не попадёт:
    тот запрос давно ушёл со старым снимком, а следующий увидит либо новый
    снимок, либо прежний, если прогон не удался.
    """
    title = DATING_PEOPLE_TITLES.get(kind, kind)

    try:
        people = await _snapshot(profile, kind)
    except _PeopleFailure as e:
        _refresh_error[key] = str(e)
        if log_id:
            await dating_log_write(log_id, 'fail', f'{title} с сайта не снялись: {e}')
        logger_info(f'[dating] фоновый список «{key}» не снялся: {e}')

        return
    except Exception as e:
        # Сюда падает то, чего мы не предусмотрели. Ждать эту задачу некому, и без
        # перехвата исключение утонуло бы в логе `asyncio` без следа на странице.
        _refresh_error[key] = f'обновление сорвалось: {e}'
        logger_info(f'[dating] фоновый список «{key}» упал: {e}')

        return

    _cache_put(key, {'at': time.time(),
                     'when': datetime_utc_now().strftime('%Y-%m-%d %H:%M:%S'),
                     'people': people})
    _refresh_error.pop(key, None)
    if log_id:
        await dating_log_write(log_id, 'ok', f'{title} с сайта: {len(people)} собеседников')


def _key(kind: str, profile_id: int = 0, site_id: int = 0) -> str:
    """Ключ снимка: по анкете или по сайту.

    Он же служит ключом сторожей фонового обновления — одна строка на один список,
    и разъехаться им негде.
    """
    return (DATING_PEOPLE_KEY.format(profile_id=profile_id, kind=kind) if profile_id
            else DATING_PEOPLE_SITE_KEY.format(site_id=site_id, kind=kind))


async def _site_profile(site_id: int) -> dict:
    """Любая анкета сайта — под ней сценарий зайдёт в кабинет.

    Какая именно, на результат не влияет: «кто в сети» одинаков под любой учёткой.
    Но порядок задан явно (сначала те, у кого снят вход, дальше по номеру), чтобы
    список не снимался то одной анкетой, то другой: в журнале прогонов это
    выглядело бы как случайная чехарда.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login FROM dating_profile '
            ' WHERE site_id = %s AND login <> %s'
            ' ORDER BY storage_state IS NULL, id LIMIT 1', (int(site_id), ''))
        row = await db.fetchone()

    return dict(row) if row else {}


def _cache_get(key: str) -> dict | None:
    """Снимок из Redis или None.

    ⚠ **Сбой Redis не должен ронять ручку.** Кэш — ускорение, а не источник
    правды: не прочитался — сходим к соседу, как в первый раз. Ронять из-за этого
    страницу значило бы менять «медленно» на «никак».
    """
    try:
        raw = redis_conn_get().get(key)
    except Exception as e:
        logger_info(f'[dating] кэш собеседников не прочитался: {e}')

        return None

    if not raw:
        return None

    try:
        snapshot = json.loads(raw)
    except ValueError:
        return None

    return snapshot if isinstance(snapshot, dict) and 'people' in snapshot else None


def _cache_put(key: str, snapshot: dict) -> None:
    """Кладёт снимок в Redis на неделю. Не бросает — по той же причине, что и чтение."""
    try:
        redis_conn_get().set(key, json.dumps(snapshot, ensure_ascii=False),
                             ex=DATING_PEOPLE_KEEP_SEC)
    except Exception as e:
        logger_info(f'[dating] кэш собеседников не записался: {e}')


async def _snapshot(profile: dict, kind: str) -> list:
    """Гонит сценарий у соседа и разбирает ответ. Любая неудача — `_PeopleFailure`.

    Три разных повода не получить список сведены в одно исключение намеренно:
    страница поступает с ними одинаково — показывает прежний снимок и причину, — а
    разбирать, чем «сосед занят» отличается от «сценарий встал на шаге 7», по
    тексту причины проще, чем по трём веткам кода.
    """
    spec = DATING_PEOPLE_KINDS[kind]

    # Сайт спрашиваем здесь, а не в ручке: он живёт у соседа, и его недоступность
    # — такая же неудача прогона, как занятый браузер, а не повод погасить колонку
    # с готовым снимком. `dating_profile_site` отвечает `HTTPException` (502 или
    # 422) — переводим её в общую неудачу, чтобы кэш отработал по своим правилам.
    try:
        site = await dating_profile_site(int(profile['site_id']))
    except HTTPException as e:
        raise _PeopleFailure(str(e.detail))

    site_id = int(site['id'])

    try:
        scenario = await rhapsody_api_scenario_find(site_id, spec['scenario'])
    except RhapsodyError as e:
        raise _PeopleFailure(str(e))

    if not scenario:
        raise _PeopleFailure(f"у сайта нет сценария «{spec['scenario']}» — "
                             f'заведите его в RhapsodyAi на вкладке «Сценарии»')

    # Логин анкеты передаём мы, пароль — переменная сайта у соседа (`girl_password`):
    # у агентства он общий на все анкеты, а импортированные анкеты своего пароля и
    # не имеют — панель его не показывает.
    #
    # `keep_session=False`: вкладка нужна на один прогон, и держать её незачем —
    # она стоит ~143 МБ в пуле, общем с соседом.
    # Общей дверью (`dating_scenario_run`): она пишет прогон в журнал прогонов и в
    # журнал самой анкеты — список снимается под ней, и след должен быть у неё.
    try:
        run = await dating_scenario_run(
            scenario, site_id, int(profile['id']), '',
            {**spec['variables'], 'profile_login': str(profile['login'])}, {},
            keep_session=False)
    except RhapsodyError as e:
        # «Нет такого сценария» у соседа — повод забыть его номер: сценарий
        # переименовали или удалили, а мы держим номер до конца выдержки и стучимся
        # по нему каждый раз. Прочие отказы номера не порочат.
        if ' → 404' in str(e):
            rhapsody_api_scenario_forget(site_id, spec['scenario'])
        raise _PeopleFailure(str(e))

    if str(run.get('status')) == 'error':
        raise _PeopleFailure(f"сценарий встал на шаге {run.get('step_no')}: {run.get('error')}")

    return dating_people_rows(run.get('extract') or {}, spec['field'])


def _answer(snapshot: dict | None, cached: bool, error: str, refreshing: bool = False) -> dict:
    """Ответ ручки из снимка. Нет снимка — пустой список и возраст `-1`.

    `-1` вместо нуля: ноль значит «снят только что», и показать его там, где
    снимка нет вовсе, значило бы соврать свежестью.

    `refreshing` — «прямо сейчас снимаем новый»: страница по нему знает, что стоит
    вернуться за результатом, и показывает, что список обновляется.
    """
    if not snapshot:
        return {'result': {'people': [], 'cached': False, 'age_sec': -1,
                           'taken_at': '', 'refreshing': refreshing, 'error': error}}

    # Возраст меряется календарными часами, а не монотонными: снимок переживает
    # перезапуск процесса, а монотонные часы после него начинаются заново — и
    # недельный снимок объявили бы свежим.
    return {'result': {'people': snapshot['people'],
                       'cached': cached,
                       'age_sec': max(0, int(time.time() - snapshot['at'])),
                       'taken_at': snapshot['when'],
                       'refreshing': refreshing,
                       'error': error}}
