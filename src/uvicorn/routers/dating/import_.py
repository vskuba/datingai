"""Импорт анкет с сайта: кнопка «Импортировать профили» на странице «Профили».

**Браузер здесь не водят.** Вся работа со страницей — вход в партнёрскую панель,
переход к списку, чтение карточек — лежит на RhapsodyAi и описана его сценариями.
Отсюда идут только вызовы: погнать сценарий, взять то, что он вернул, разложить по
своей базе. Разбор и правила слияния — в `src/dating/import.py`, здесь
порядок вызовов и запись.

Учётка партнёрской панели тоже не наша забота: `{{panel_url}}`, `{{panel_login}}`
и `{{panel_password}}` лежат переменными сайта у соседа (`browser_site_variable`),
и он подставляет их в сценарий сам. Мы не знаем и не передаём этот пароль —
что правильно: он от чужой панели, а не от анкеты.

## Два сценария и одна сессия

Список даёт имя, город, номер и фото — одним прогоном. Почта, год рождения и
описание лежат на странице правки каждой анкеты, и за ними идёт второй сценарий,
по одному прогону на анкету. Оба гоняются **в одной сессии**: вход в панель стоит
секунд, и повторять его на каждую анкету значило бы платить за него сотню раз.

Отсюда же предел `IMPORT_CARD_LIMIT`: карточки идут по одной, и на большом
агентстве полный обход занял бы десятки минут, всё это время держа вкладку в пуле
соседа. Анкеты сверх предела заводятся по списку — без почты и описания, но с
номером; следующий импорт дочитает их.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.dating.import_ import (import_card, import_match,
                                      import_plan, import_rows)
from src.dating.json_column import json_column_load
from src.dating.log import log_write
from src.dating.scenario import scenario_run
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_session_close,
                                       rhapsody_api_site_get)

router = APIRouter(prefix='/api/dating/import', tags=['dating'])

# Как сценарии называются у соседа. Ищем по именам, а не по подстановкам (так
# ищется вход, `login`): у списка подстановок нет вовсе, а у карточки одна
# и та же — по ней сценарий не узнать.
IMPORT_LIST_SCENARIO = 'импорт анкет'
IMPORT_CARD_SCENARIO = 'анкета: карточка'

# Сколько карточек дочитываем за один импорт. Каждая — отдельный прогон в живом
# браузере, то есть секунды; сотня анкет без предела заняла бы десятки минут, и
# всё это время человек смотрел бы на крутящуюся кнопку.
IMPORT_CARD_LIMIT = 60

# Роль импортированной анкеты. Партнёрская панель агентства — женская по
# устройству: в ней заводят анкеты девушек, раздел так и называется «Ladies
# profiles». Мужская учётка появляется здесь только руками.
IMPORT_ROLE = 'lady'

# Ширина колонки `profile.profile_id` — номер на сайте. Срезаем по ней, как
# логин по своей: обрезок в адресе удара лучше вставки, которую база отвергнет.
IMPORT_UID_MAX = 32


@router.get('/ready', include_in_schema=False)
async def import_ready(site_id: int = 0, user=Depends(auth_required)):
    """Настроен ли импорт для сайта — тем ли, чем его настраивают у соседа.

    Признак один: **есть ли у сайта сценарий списка**. Не отдельная галочка в
    настройке: галочка разъехалась бы со сценарием в тот день, когда его
    переименуют или удалят, и кнопка обещала бы работу, которой нет.

    Отказ соседа — не «не настроено», а `ready: false` с причиной: страница гасит
    кнопку в обоих случаях, но подсказка у них разная, и человеку важно, чинить
    ему настройку или ждать соседа.
    """
    if not int(site_id or 0):
        return {'result': {'ready': False, 'why': 'сайт не выбран'}}

    try:
        scenario = await rhapsody_api_scenario_find(int(site_id), IMPORT_LIST_SCENARIO)
    except RhapsodyError as e:
        return {'result': {'ready': False, 'why': f'RhapsodyAi не ответил: {e}'}}

    if not scenario:
        return {'result': {'ready': False,
                           'why': f'у сайта нет сценария «{IMPORT_LIST_SCENARIO}»'}}

    return {'result': {'ready': True, 'why': '', 'scenario': scenario.get('name') or ''}}


@router.post('/profile', include_in_schema=False)
async def import_profile(site_id: int = 0, cards: bool = True,
                                user=Depends(auth_required)):
    """Импортирует анкеты сайта: заводит новые, обновляет изменившиеся.

    Args:
        site_id: сайт-закладка, с которого импортируем.
        cards: дочитывать ли карточку каждой анкеты. Без неё импорт идёт секунды
            и даёт имя, город и фото; с ней — минуты, зато с почтой, возрастом и
            описанием.

    Returns:
        dict: `created`, `updated`, `same` — сколько анкет заведено, обновлено и
        оставлено как есть; `total` — сколько пришло с сайта; `cards` — сколько
        карточек дочитано; `log` — строки хода работы для показа на странице.

    Raises:
        HTTPException: 422 — не выбран сайт или у сайта нет сценария импорта;
            502 — RhapsodyAi не ответил.
    """
    if not int(site_id or 0):
        raise HTTPException(status_code=422, detail='не выбран сайт')

    site, scenario, card_scenario = await _scenarios_find(int(site_id))
    base = str((json_column_load(site.get('presence')) or {}).get('base')
               or site.get('url') or '')

    # `keep_session=True` — вкладка остаётся жить: в ней уже выполнен вход, и
    # карточки читаются в ней же. Закрываем сами, в `finally`: брошенная вкладка
    # стоит ~143 МБ в пуле, общем с соседом.
    # Прогоны идут общей дверью (`scenario_run`): она пишет их в журнал
    # прогонов. Анкеты у импорта нет — он идёт по сайту целиком, и приписывать
    # прогон одной из привезённых анкет было бы враньём.
    try:
        run = await scenario_run(scenario, int(site_id), keep_session=True)
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    session_id = str(run.get('session_id') or '')
    trail = []
    try:
        if str(run.get('status')) == 'error':
            # Упавший сценарий — это ответ, а не поломка панели: сайт сменил
            # вёрстку, панель просит подтверждение, пароль устарел. Правило то же,
            # что у выгрузки: код ответа говорит про запрос, а не про сценарий.
            return {'result': {'status': 'error', 'created': 0, 'updated': 0, 'same': 0,
                               'total': 0, 'cards': 0,
                               'error': f"сценарий «{scenario['name']}» встал на шаге "
                                        f"{run.get('step_no')}: {run.get('error')}",
                               'log': []}}

        rows = import_rows(run.get('extract') or {})
        trail.append(f'Список анкет прочитан: {len(rows)}')

        read = 0
        if cards and card_scenario:
            read = await _cards_read(rows, card_scenario, int(site_id), session_id, trail)
    finally:
        await _session_drop(session_id)

    counts = await _rows_save(int(site_id), rows, base, trail)

    return {'result': {'status': 'ok', 'error': '', 'total': len(rows), 'cards': read,
                       **counts, 'log': trail}}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _scenarios_find(site_id: int) -> tuple[dict, dict, dict]:
    """Сайт и два его сценария. Нет списка — 422 с тем, что делать человеку."""
    try:
        site = await rhapsody_api_site_get(site_id)
        scenario = await rhapsody_api_scenario_find(site_id, IMPORT_LIST_SCENARIO)
        card = await rhapsody_api_scenario_find(site_id, IMPORT_CARD_SCENARIO)
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not scenario:
        raise HTTPException(
            status_code=422,
            detail=f'для сайта нет сценария «{IMPORT_LIST_SCENARIO}»: '
                   f'заведите его в RhapsodyAi на вкладке «Сценарии»')

    return site or {}, scenario, card or {}


async def _cards_read(rows: list, scenario: dict, site_id: int, session_id: str,
                      trail: list) -> int:
    """Дочитывает карточки анкет в той же вкладке и дополняет ими записи списка.

    Упавшая карточка не роняет импорт: у анкеты остаётся то, что дал список, и
    следующий импорт попробует снова. Терять из-за одной кривой страницы весь
    прогон, который уже стоил входа в панель, нельзя.
    """
    now = datetime.now()
    read = 0
    for row in rows[:IMPORT_CARD_LIMIT]:
        try:
            run = await scenario_run(
                scenario, site_id, session_id=session_id,
                variables={'profile_id': row['site_uid']}, keep_session=True)
        except RhapsodyError as e:
            trail.append(f"Карточка #{row['site_uid']} не прочиталась: {e}")
            continue

        if str(run.get('status')) == 'error':
            trail.append(f"Карточка #{row['site_uid']} не прочиталась: {run.get('error')}")
            continue

        card = import_card(run.get('extract') or {}, now)
        if card:
            # Список отдаёт номер, карточка — почту и остальное. Пустые поля
            # карточки не затирают список: у части анкет город стоит только там.
            row.update({name: value for name, value in card.items() if value})
            read += 1

    if len(rows) > IMPORT_CARD_LIMIT:
        trail.append(f'Карточек прочитано {read} из {len(rows)} — '
                     f'предел за один импорт; остальные дочитает следующий')

    return read


async def _rows_save(site_id: int, rows: list, base: str, trail: list) -> dict:
    """Заводит новые анкеты и обновляет изменившиеся. Возвращает счётчики."""
    by_uid, by_login = await _profiles_index(site_id)
    counts = {'created': 0, 'updated': 0, 'same': 0}

    for row in rows:
        found = import_match(row, by_uid, by_login)
        plan = import_plan(row, found, base)

        if plan['action'] == 'create':
            profile_id = await _profile_create(site_id, plan)
            if profile_id:
                counts['created'] += 1
                by_uid[str(row.get('site_uid') or '')] = {'id': profile_id,
                                                          'login': plan['login'],
                                                          'card': plan['card']}
                await log_write(profile_id, 'ok',
                                       f"Заведена импортом с сайта: {plan['login']}")
                trail.append(f"Заведена «{plan['card'].get('name') or plan['login']}»")
            continue

        if plan['action'] == 'update':
            await _profile_update(int(found['id']), plan)
            counts['updated'] += 1
            await log_write(int(found['id']), 'info',
                                   f"Импорт обновил: {', '.join(plan['changed'])}")
            trail.append(f"Обновлена «{plan['card'].get('name') or plan['login']}»: "
                         f"{', '.join(plan['changed'])}")
            continue

        counts['same'] += 1

    trail.append(f"Итого: заведено {counts['created']}, обновлено {counts['updated']}, "
                 f"без изменений {counts['same']}")

    return counts


async def _profiles_index(site_id: int) -> tuple[dict, dict]:
    """Учётки сайта в двух видах: по номеру на сайте и по логину.

    Два указателя, а не один, потому что и ключей два: номер сайта живёт в кэше
    анкеты и не меняется никогда, логин — колонка учётки, и его правят руками.
    Почему сперва ищут по номеру — в шапке `import.py`.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, login, profile, profile_edited FROM profile '
            ' WHERE site_id = %s', (int(site_id),))
        rows = await db.fetchall()

    by_uid, by_login = {}, {}
    for row in rows or []:
        card = json_column_load(row.get('profile'))
        item = {'id': int(row['id']), 'login': str(row.get('login') or ''),
                'card': card,
                # ⚠ Отметки «правлено руками» — `import_plan`, иначе он затрёт
                # правку человека. Колонка, а не ключ карточки: карточку
                # перезаписывают целиком.
                'edited': row.get('profile_edited')}
        by_login[item['login']] = item
        site_uid = str(card.get('id') or '')
        if site_uid:
            by_uid[site_uid] = item

    return by_uid, by_login


async def _profile_create(site_id: int, plan: dict) -> int:
    """Заводит учётку по импортированной анкете. Пароля у неё нет и быть не может.

    Партнёрская панель паролей анкет не показывает — их там попросту нет на
    странице. Поэтому импортированная анкета видна в списке, но войти под ней
    нельзя, пока пароль не впишут руками; страница это и показывает, гася кнопку.
    """
    try:
        # Номер на сайте кладём в **два места сразу** — колонку и ключ карточки
        # (`profile_card_keep`, §3.14): удар присутствия собирает адрес из
        # колонки, и анкета, заведённая без неё, била бы в пустоту молча.
        site_uid = str((plan.get('card') or {}).get('id') or '').strip()
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO profile (site_id, login, profile_id, password,'
                ' note, role, profile) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (int(site_id), plan['login'], site_uid[:IMPORT_UID_MAX] or None,
                 '', 'заведена импортом', IMPORT_ROLE,
                 json.dumps(plan['card'], ensure_ascii=False)))
            profile_id = db.lastrowid
            await db.connection.commit()

        return int(profile_id or 0)
    except Exception as e:
        # Дубль логина ловится уникальным ключом — это не беда импорта, а гонка с
        # человеком, который завёл ту же анкету руками секунду назад.
        logger_info(f'[dating] импорт: анкета {plan["login"]} не завелась: {e}')

        return 0


async def _profile_update(profile_id: int, plan: dict) -> None:
    """Переписывает кэш анкеты. Колонки учётки не трогает — они принадлежат человеку."""
    import json

    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                             (json.dumps(plan['card'], ensure_ascii=False), int(profile_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] импорт: анкета #{profile_id} не обновилась: {e}')


async def _session_drop(session_id: str) -> None:
    """Гасит вкладку импорта. Не роняет ответ: уборка работу не роняет."""
    if not session_id:
        return

    try:
        await rhapsody_api_session_close(session_id)
    except Exception as e:
        logger_info(f'[dating] импорт: вкладка {session_id} не закрылась: {e}')
