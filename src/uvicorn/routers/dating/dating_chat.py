"""Импорт переписки из панели агентства: кнопка и её ручка.

Сообщения снимает сценарий у соседа (`чат: сообщения из панели`), разбирает и
кладёт `src/dating/dating_chat_panel.py`. Здесь порядок вызовов: найти сценарий,
погнать его за диапазон дат, разобрать ответ, записать.

Показывать привезённое отдельной страницей не нужно: сообщения ложатся в
`dating_message`, и карточка чата на «Профилях» показывает их сама — тем же
списком диалогов и той же лентой реплик, что и пойманное сокетом.
"""

from fastapi import APIRouter, Depends, HTTPException

from src.auth.auth import auth_required
from src.dating.dating_chat_panel import (DATING_CHAT_PANEL_DAYS,
                                          DATING_CHAT_PANEL_SCENARIO,
                                          dating_chat_panel_import)
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_scenario_find

router = APIRouter(prefix='/api/dating/chat', tags=['dating'])


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
        days: за сколько дней спросить панель. Предел —
            `DATING_CHAT_PANEL_DAYS_MAX`, и проверяет его сама работа.

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
    # Работа — в `dating_chat_panel_import`: её же зовёт круг воркера, который
    # забирает сообщения сам по настройке сайта. ⚠ Одной функцией на два вызова:
    # разойдись они — и автоматический импорт однажды стал бы разбирать ответ
    # иначе, чем ручной, а заметить это было бы нечем.
    #
    # Здесь остаётся то, что про HTTP: причины отказа превращаются в коды. Функция
    # их только называет — кругу воркера падать нельзя, он обходит все сайты.
    result = await dating_chat_panel_import(site_id, days)

    if result['status'] == 'error':
        error = result['error']

        # ⚠ **Сорвавшийся прогон — это ответ, а не код ошибки.** Механика цела,
        # сосед на месте, просто панель сайта не отдала таблицу: страница показывает
        # причину в панели импорта, где человек её и читает. Отдай мы 502 — он увидел
        # бы «шлюз не отвечает» вместо «сценарий встал на шаге 7: нет таблицы».
        if 'сценарий встал' in error:
            return {'result': result}

        # 502 — «не мы»: соседа нет или он отказал. 422 — «спросили не то»: нет
        # сайта, нет анкет, нет сценария. Различать обязательно: по 502 звонят
        # соседу, по 422 правят настройку.
        raise HTTPException(status_code=502 if 'RhapsodyAi' in error else 422,
                            detail=error)

    return {'result': result}
