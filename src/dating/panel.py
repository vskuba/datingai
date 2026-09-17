"""Админ-панель агентства: вход сценарием один раз, дальше — cookie и таймер.

У каждого сайта знакомств есть панель агентства: там разом видны все наши анкеты,
их переписка и письма. Панель — **не сайт знакомств**: заходят в неё не анкетой, а
учёткой агентства, и сессия у неё одна на сайт, а не на анкету.

## ⚠ Зачем держать её живой, если сценарии входят сами

Входят — и в этом цена. Каждый панельный сценарий у соседа начинается одинаково:
`goto` → `fill login` → `fill password` → `click` → `wait_url`. Это половина из
сорока секунд прогона, и платится она заново на каждый заход круга импорта
переписки, то есть раз в `chat_check_every` по каждому сайту.

Второе, и оно важнее: **о состоянии панели мы не знали ничего**. Сменили пароль
агентства — узнаём по упавшему сценарию через час, из ленты прогонов, где отказ
выглядит как поломка сценария. Красная точка в шапке отвечает на этот вопрос до
того, как он задан.

## Как устроено

```
сценарий «вход в панель» (браузер у соседа, ~20 секунд)
        │
        ├─ storage_state → site_.panel_state        ← cookie у нас
        └─ вкладка закрывается
                │
        круг панели, раз в admin_every
                └─ GET admin_url с этими cookie          ← сотые доли секунды
                        │
                не признала → снова сценарий входа
```

Тот же приём, что у присутствия анкет (`heartbeat.py`), и тем же движком
(`py_modules/http_/http_heartbeat.py`): браузер нужен один раз, для входа.

## ⚠ Пароля панели здесь нет

Логин и пароль лежат переменными сайта **у соседа** (`panel_login`,
`panel_password`) — их подставляет его же сценарий. Мы зовём сценарий по имени и
пароля не видим. Копия здесь означала бы второе место, где он протухает.

## ⚠ Примета входа обязательна

Панель отдаёт форму входа тем же кодом `200`, что и кабинет: у Ladadate форма
живёт по тому же адресу, `#login` появляется прямо на нём. Без приметы
(`admin_mark`) проверка сводилась бы к «ответила — значит пустила», то есть
показывала бы зелёную точку и с протухшей сессией.
"""

import json

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from http_.http_heartbeat import (http_heartbeat_check, http_heartbeat_client,
                                  http_heartbeat_cookies)

from src.dating.json_column import json_column_load
from src.dating.scenario import scenario_run
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_session_close,
                                       rhapsody_api_session_state)

# Имя сценария входа у соседа. Одно на все сайты: сценарии у него разведены по
# `site_id`, и «вход в панель» у Ladadate — не тот же, что у Goldenbride.
PANEL_SCENARIO = 'вход в панель'

# Настройки сайта. ⚠ Адрес — он же выключатель: пусто значит «панели у сайта нет».
PANEL_URL_SETTING = 'admin_url'
PANEL_MARK_SETTING = 'admin_mark'
PANEL_EVERY_SETTING = 'admin_every'

# Как часто проверять, если настройки нет.
PANEL_EVERY_DEFAULT = 300

# Длина строки отказа: столько же, сколько в колонке.
PANEL_ERROR_MAX = 500


async def panel_show(site_id: int) -> dict:
    """Что знать шапке страницы про панель этого сайта.

    Returns:
        dict: `on` — настроена ли панель вообще (есть адрес), `url` — куда вести
        ссылку, `name` — чем её подписать, `status` — `online` | `offline` |
        `error` | пусто, `checked_at` и `login_at` — метки UTC строкой, `error` —
        чем кончилась последняя неудача.

    ⚠ **Пустой `status` — не `offline`.** Первое значит «ещё не спрашивали»
    (панель настроили минуту назад, круг до неё не дошёл), второе — «спросили, и
    панель нас не признала». Страница рисует их разным: серым и красным.
    """
    url = await _url(site_id)
    if not url:
        return {'on': False, 'url': '', 'name': '', 'status': '',
                'checked_at': '', 'login_at': '', 'error': ''}

    row = await _row(site_id)

    return {'on': True,
            'url': url,
            'name': panel_name(url),
            'status': str(row.get('panel_status') or ''),
            'checked_at': _at(row.get('panel_checked_at')),
            'login_at': _at(row.get('panel_login_at')),
            'error': str(row.get('panel_error') or '')}


def panel_name(url: str) -> str:
    """Чем подписать ссылку: **хост** панели, без схемы и без пути.

    ⚠ Своей настройки на имя нет намеренно. Имя отвечает на вопрос «куда я
    попаду», а на него уже отвечает адрес; отдельное поле означало бы, что подпись
    и ссылка расходятся — и первым это заметит тот, кто по ней не туда уйдёт.

    ⚠ Путь при этом отбрасываем, хотя в адресе он есть: `admin_url` ведёт на
    страницу **после входа** (`/partner/agency/profile`), и подпись из неё
    заняла бы пол-шапки, не сказав ничего сверх хоста. Целый адрес виден в
    ссылке и в подсказке.
    """
    host = str(url or '').split('://')[-1].split('/')[0].strip()

    return host or str(url or '')


async def panel_check(site_id: int) -> dict:
    """Спрашивает панель по сохранённым cookie. Ответ пишет сайту.

    Returns:
        dict: `status` — `online` | `offline` | `error`, `error` — причина,
        `ms` — сколько занял заход.

    ⚠ `offline` и `error` — разные исходы, и разводятся они не для красоты:
    первый значит «панель нас не признала, нужен вход заново», второй — «до панели
    не достучались». Слив их в один, мы гоняли бы браузер соседа из-за чужой
    сетевой аварии.
    """
    url = await _url(site_id)
    if not url:
        return {'status': '', 'error': 'у сайта не задан адрес админ-панели', 'ms': 0}

    row = await _row(site_id)
    cookies = http_heartbeat_cookies(json_column_load(row.get('panel_state')), url)
    if not cookies:
        answer = {'status': 'offline', 'ms': 0,
                  'error': 'в панель ещё не входили: нет cookie'}
        await _save(site_id, answer)

        return answer

    client = http_heartbeat_client()
    try:
        answer = await http_heartbeat_check(client, cookies, await _config(site_id))
    finally:
        await client.aclose()

    await _save(site_id, answer)

    return answer


async def panel_login(site_id: int) -> dict:
    """Входит в панель сценарием соседа и снимает cookie сессии.

    Returns:
        dict: `status` — `ok` | `error`, `error` — чем кончилось.

    ⚠ **Вкладку гасим всегда**, и удача тут ничего не меняет: состояние снято, а
    живой Chromium у соседа стоит полутораста мегабайт. Оставленная вкладка — то
    же самое, от чего этот модуль и уходит.
    """
    try:
        scenario = await rhapsody_api_scenario_find(site_id, PANEL_SCENARIO)
    except RhapsodyError as e:
        await _save(site_id, {'status': 'error', 'error': str(e)})

        return {'status': 'error', 'error': str(e)}

    if not scenario:
        error = f'у сайта нет сценария «{PANEL_SCENARIO}»'
        await _save(site_id, {'status': 'error', 'error': error})

        return {'status': 'error', 'error': error}

    # Общей дверью (`scenario_run`): она пишет прогон в журнал прогонов.
    # Анкеты у входа в панель нет и быть не может — учётка агентства одна на сайт.
    try:
        run = await scenario_run(scenario, site_id, 0, '', {}, {},
                                        keep_session=True)
    except RhapsodyError as e:
        await _save(site_id, {'status': 'error', 'error': str(e)})

        return {'status': 'error', 'error': str(e)}

    session_id = str(run.get('session_id') or '')

    if str(run.get('status')) == 'error':
        error = (f"сценарий встал на шаге {run.get('step_no')}: "
                 f"{str(run.get('error') or '')[:300]}")
        await _save(site_id, {'status': 'error', 'error': error})
        await _session_close(session_id)

        return {'status': 'error', 'error': error}

    try:
        state = await rhapsody_api_session_state(session_id)
    except RhapsodyError as e:
        await _save(site_id, {'status': 'error', 'error': f'состояние не снялось: {e}'})
        await _session_close(session_id)

        return {'status': 'error', 'error': f'состояние не снялось: {e}'}
    finally:
        await _session_close(session_id)

    await _state_save(site_id, state)

    return {'status': 'ok', 'error': ''}


async def panel_keep(site_id: int) -> dict:
    """Держит панель живой: спросить, а не признала — войти заново и спросить ещё.

    Returns:
        dict: `status` — чем кончилось (`online` | `offline` | `error`),
        `logged_in` — заводили ли вход сценарием на этом заходе.

    ⚠ **Вход заводим только на `offline`.** `error` значит, что до панели не
    достучались, и гонять из-за этого браузер соседа — платить минутой Chromium за
    чужую сетевую икоту. Круг придёт снова через `admin_every`.
    """
    answer = await panel_check(site_id)
    if answer['status'] != 'offline':
        return {**answer, 'logged_in': False}

    logger_info(f'[dating] панель сайта #{site_id} не признала вход — захожу сценарием')

    login = await panel_login(site_id)
    if login['status'] != 'ok':
        return {'status': 'error', 'error': login['error'], 'ms': 0, 'logged_in': True}

    # Спрашиваем ещё раз, и это не перестраховка: сценарий отчитывается о **своих**
    # шагах, а не о том, признала ли панель сессию. У анкет это уже ловило неверный
    # пароль — сценарий проходил, состояние приходило пустым, а карточка выглядела
    # рабочей до первой попытки что-нибудь сделать.
    return {**await panel_check(site_id), 'logged_in': True}


async def panel_sites() -> list[int]:
    """Сайты с заданным адресом панели. По ним и ходит круг.

    ⚠ Ушедшие у соседа (`is_gone`) не берём: закладки нет, а настройки её пережили
    — ходить в панель сайта, которого у нас больше нет, незачем.
    """
    from src.uvicorn.routers.dating.site_ import site_setting

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id FROM site WHERE is_gone = 0'
                             ' ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для панели не спросились: {e}')

        return []

    out = []
    for row in rows or []:
        site_id = int(row['site_id'])
        if await site_setting(site_id, PANEL_URL_SETTING):
            out.append(site_id)

    return out


async def panel_every(site_id: int) -> int:
    """Частота проверки этого сайта в секундах."""
    from src.uvicorn.routers.dating.site_ import site_seconds

    return int(await site_seconds(site_id, PANEL_EVERY_SETTING)
               or PANEL_EVERY_DEFAULT)


# ── Приватное ────────────────────────────────────────────────────────────────

async def _url(site_id: int) -> str:
    from src.uvicorn.routers.dating.site_ import site_setting

    return str(await site_setting(site_id, PANEL_URL_SETTING) or '').strip()


async def _config(site_id: int) -> dict:
    """Описание проверки в формате движка присутствия.

    ⚠ Удара (`beat`) здесь нет вовсе, и это разница с анкетами. Анкету сайт
    показывает в сети, **пока** она стучится; сессия панели живёт сама, и нам
    нужно лишь знать, жива ли она. Лишний POST на чужую панель раз в пять минут —
    это действие от имени агентства, которого никто не просил.
    """
    from src.uvicorn.routers.dating.site_ import site_setting

    base, path = _split(await _url(site_id))
    mark = str(await site_setting(site_id, PANEL_MARK_SETTING) or '').strip()

    # ⚠ `no_redirect` — вторая примета, и работает она там, где первой нет: панель,
    # уводящая гостя на форму, даёт `302`, а вошедшего пускает с `200`.
    return {'base': base,
            'alive': [{'path': path, 'method': 'GET', 'ok_mark': mark,
                       'no_redirect': True}]}


def panel_root(url: str) -> str:
    """Корень панели: от него строятся адреса её страниц.

    `admin_url` ведёт на страницу **внутри** панели (`/partner/agency/profile`), а
    её таблицы лежат рядом (`/partner/agency/userchatmessages`). Корень — то, что
    у них общее.

    ⚠ **Черта в конце различает страницу и папку**, и другого признака здесь нет:
    `…/partner/agency/profile` — страница, её корень `…/partner/agency`;
    `…/partner/agency/` — сама папка, и корень равен ей же без черты. Считай мы
    последний кусок страницей всегда, второй адрес дал бы корнем `…/partner`, и
    все таблицы искались бы этажом выше.
    """
    clean = str(url or '').strip()
    if not clean:
        return ''

    if clean.endswith('/'):
        return clean.rstrip('/')

    return clean.rsplit('/', 1)[0] if clean.count('/') > 2 else clean


def _split(url: str) -> tuple[str, str]:
    """Адрес панели на корень и путь — так, как его ждёт движок присутствия.

    ⚠ **Косая черта в конце решает всё, и это не придирка.** Движок склеивает
    адрес как `base + '/' + path`, поэтому целый адрес в `base` давал бы лишнюю
    черту в конце — а Ladadate отвечает на неё `301` на адрес без неё. С
    `no_redirect` это «панель не признала вход», хотя сессия жива: так и вышло на
    первом же заходе, вход проходил, а точка горела красным.

    ⚠ Черту в конце **снимаем и у пути** по той же причине.
    """
    clean = str(url or '').strip().rstrip('/')
    if '://' not in clean:
        return clean, ''

    scheme, rest = clean.split('://', 1)
    host, _, path = rest.partition('/')

    return f'{scheme}://{host}', path


async def _row(site_id: int) -> dict:
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT panel_state, panel_status, panel_checked_at,'
                             '       panel_login_at, panel_error FROM site'
                             ' WHERE site_id = %s', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] состояние панели сайта #{site_id} не прочиталось: {e}')

        return {}

    return dict(row) if row else {}


async def _save(site_id: int, answer: dict) -> None:
    """Пишет исход проверки сайту. Удача стирает прежний отказ.

    ⚠ Метку `panel_checked_at` ставим **на любом** исходе, включая отказ: она
    отвечает на вопрос «свежий ли перед вами ответ», а не «хороший ли он». Без
    этого красная точка из вчерашней проверки выглядела бы сегодняшней.
    """
    status = str(answer.get('status') or '')
    error = str(answer.get('error') or '')[:PANEL_ERROR_MAX]

    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE site SET panel_status = %s, panel_error = %s,'
                             '       panel_checked_at = NOW() WHERE site_id = %s',
                             (status, '' if status == 'online' else error, int(site_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] состояние панели сайта #{site_id} не записалось: {e}')


async def _state_save(site_id: int, state: dict) -> None:
    """Кладёт снятые cookie сайту и отмечает время входа."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE site SET panel_state = %s, panel_login_at = NOW(),'
                             '       panel_error = %s WHERE site_id = %s',
                             (json.dumps(state or {}, ensure_ascii=False), '', int(site_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] cookie панели сайта #{site_id} не записались: {e}')


async def _session_close(session_id: str) -> None:
    """Гасит вкладку. Неудача здесь ничего не решает — пишем и идём дальше."""
    if not session_id:
        return

    try:
        await rhapsody_api_session_close(session_id)
    except Exception as e:
        logger_info(f'[dating] вкладка панели не закрылась: {e}')


def _at(value) -> str:
    """Метка времени строкой UTC. Пусто — не было."""
    return value.strftime('%Y-%m-%d %H:%M:%S') if value else ''
