"""Импорт переписки из панели агентства: кнопка и её ручка.

Сообщения снимает сценарий у соседа (`чат: сообщения из панели`), разбирает и
кладёт `src/dating/dating_chat_panel.py`. Здесь порядок вызовов: найти сценарий,
погнать его за диапазон дат, разобрать ответ, записать.

Показывать привезённое отдельной страницей не нужно: сообщения ложатся в
`dating_message`, и карточка чата на «Профилях» показывает их сама — тем же
списком диалогов и той же лентой реплик, что и пойманное сокетом.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from datetime_.datetime_ import datetime_utc_now

from src.auth.auth import auth_required
from src.dating.dating_chat_panel import (DATING_CHAT_PANEL_DATE_FMT,
                                          DATING_CHAT_PANEL_DAYS,
                                          DATING_CHAT_PANEL_SCENARIO,
                                          dating_chat_panel_profiles,
                                          dating_chat_panel_rows, dating_chat_panel_save)
from src.dating.dating_scenario import dating_scenario_run
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_scenario_find

router = APIRouter(prefix='/api/dating/chat', tags=['dating'])

# Предел диапазона. Панель отдаёт период целиком одной страницей, без
# постраничности: попроси у неё год — получишь либо таймаут браузера, либо
# страницу, которую нечем прокрутить.
DATING_CHAT_DAYS_MAX = 60


@router.get('/ready', include_in_schema=False)
async def dating_chat_ready(site_id: int = 0, user=Depends(auth_required)):
    """Настроен ли импорт переписки у сайта — есть ли у него сценарий.

    Признак берётся у соседа, а не из своей настройки: сценарий заводят там, и
    своя галочка разъехалась бы с ним в день, когда его переименуют.

    Отказ соседа — не «не настроено», а `ready: false` с причиной: кнопку гасим в
    обоих случаях, но чинить человеку разное.
    """
    if not int(site_id or 0):
        return {'result': {'ready': False, 'why': 'сайт не выбран'}}

    try:
        scenario = await rhapsody_api_scenario_find(int(site_id), DATING_CHAT_PANEL_SCENARIO)
    except RhapsodyError as e:
        return {'result': {'ready': False, 'why': str(e)}}

    if not scenario:
        return {'result': {'ready': False,
                           'why': f'у сайта нет сценария «{DATING_CHAT_PANEL_SCENARIO}» — '
                                  f'заведите его в RhapsodyAi на вкладке «Сценарии»'}}

    return {'result': {'ready': True, 'why': ''}}


@router.post('/import', include_in_schema=False)
async def dating_chat_import(site_id: int = 0, days: int = DATING_CHAT_PANEL_DAYS,
                             user=Depends(auth_required)):
    """Забирает переписку сайта за последние `days` дней и кладёт в базу.

    Args:
        site_id: сайт-закладка, с которого забираем.
        days: за сколько дней спросить панель. Предел — `DATING_CHAT_DAYS_MAX`.

    Returns:
        dict: `total` — сколько строк дала панель, `ours` — сколько из них про наши
        анкеты, `added` — сколько новых, `same` — сколько уже было, `contacts` —
        скольким собеседникам завели карточку, `from`/`to` — запрошенный диапазон.

    Raises:
        HTTPException: 422 — не выбран сайт, кривой диапазон, нет сценария или у
            сайта нет ни одной анкеты с номером; 502 — сосед не ответил.

    ⚠ **Сценарий встал — это ответ, а не поломка панели** (200 со `status: error`).
    Правило то же, что у импорта анкет: сайт мог сменить вёрстку или не пустить в
    панель, и код ответа говорит про наш запрос, а не про то, что вышло у него.
    """
    site_id = int(site_id or 0)
    if not site_id:
        raise HTTPException(status_code=422, detail='не выбран сайт')

    # Без `or`: `days=0` это не «не задали», а бессмысленная просьба, и молча
    # подставлять ей неделю значит делать не то, о чём попросили.
    days = int(days)
    if days < 1 or days > DATING_CHAT_DAYS_MAX:
        raise HTTPException(status_code=422,
                            detail=f'диапазон от 1 до {DATING_CHAT_DAYS_MAX} дней')

    # Анкеты спрашиваем **до** прогона: без них разбирать нечего, и гонять браузер
    # сорок секунд ради ответа, который некуда положить, незачем.
    profiles = await dating_chat_panel_profiles(site_id)
    if not profiles:
        raise HTTPException(
            status_code=422,
            detail='у сайта нет анкет с номером на сайте — сперва импортируйте анкеты')

    try:
        scenario = await rhapsody_api_scenario_find(site_id, DATING_CHAT_PANEL_SCENARIO)
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not scenario:
        raise HTTPException(status_code=422,
                            detail=f'у сайта нет сценария «{DATING_CHAT_PANEL_SCENARIO}»')

    today = datetime_utc_now()
    since = today - timedelta(days=days)
    date_from = since.strftime(DATING_CHAT_PANEL_DATE_FMT)
    date_to = today.strftime(DATING_CHAT_PANEL_DATE_FMT)

    # Общей дверью (`dating_scenario_run`): она пишет прогон в журнал прогонов.
    # Анкеты у импорта нет — он идёт по сайту целиком, и приписывать прогон одной
    # из привезённых анкет было бы враньём.
    #
    # `account_id = 0` — «все анкеты»: панель так и подписывает первый пункт
    # селекта. Спрашивать по одной значило бы двадцать прогонов вместо одного.
    try:
        run = await dating_scenario_run(
            scenario, site_id, 0, '',
            {'account_id': '0', 'date_from': date_from, 'date_to': date_to}, {},
            keep_session=False)
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if str(run.get('status')) == 'error':
        return {'result': {'status': 'error', 'total': 0, 'ours': 0, 'added': 0,
                           'same': 0, 'contacts': 0,
                           'from': date_from, 'to': date_to,
                           'error': f"сценарий встал на шаге {run.get('step_no')}: "
                                    f"{run.get('error')}"}}

    extract = run.get('extract') or {}
    rows = dating_chat_panel_rows(extract, profiles)
    saved = await dating_chat_panel_save(rows)

    return {'result': {'status': 'ok',
                       'total': len(extract.get('сообщения') or []),
                       'ours': len(rows),
                       'from': date_from, 'to': date_to, 'error': '', **saved}}
