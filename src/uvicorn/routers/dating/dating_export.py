"""Страница «Профили»: собрать список профилей с сайта одним прогоном сценария.

Своей таблицы у выгрузки нет и хранения тоже: страница просит список, показывает
его и забывает. Это осознанно — выгрузка нужна, чтобы посмотреть, кто есть на
сайте прямо сейчас, а не чтобы завести вторую копию чужого каталога рядом со
своей базой.

Работы здесь ровно на две строчки: найти у соседа сценарий сайта и погнать его.
Всё, что требует браузера, живёт в RhapsodyAi; всё, что требует разбора его
ответа, — в `src/dating/dating_export.py`.

**Упавший сценарий — это 200, а не 5xx.** Правило берётся у самого соседа: код
ответа говорит про запрос, а не про результат сценария (`browser_scenario_run`).
Сценарий, вставший на третьем шаге, — законный ответ «вот что вышло», и страница
показывает его текстом вместе с номером шага. Отдай мы 500, «сайт поменял
вёрстку» стало бы неотличимо от «панель сломалась».
"""

from fastapi import APIRouter, Depends, HTTPException

from src.auth.auth import auth_required
from src.dating.dating_export import dating_export_rows
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_scenario_find,
                                       rhapsody_api_scenario_run)

router = APIRouter(prefix='/api/dating/export', tags=['dating'])

# Как сценарий выгрузки называется у соседа. Ищем по имени, а не по подстановкам
# (так ищется вход, `dating_login`): подстановок у выгрузки нет — она работает на
# открытом каталоге, без входа под анкетой, — и узнать её по ним невозможно.
DATING_EXPORT_SCENARIO = 'экспорт анкет'


@router.post('/profile')
async def dating_export_profile(site_id: int = 0, user=Depends(auth_required)):
    """Гонит сценарий выгрузки и отдаёт список профилей таблицей.

    Args:
        site_id: сайт-закладка RhapsodyAi, с которого выгружаем.

    Returns:
        dict: `status` (`ok` или `error`), `columns` — имена колонок в порядке
        шагов сценария, `profiles` — строки, `error` и `step_no` — если сценарий
        встал.

    Raises:
        HTTPException: 422 — не выбран сайт или у сайта нет сценария выгрузки;
            502 — RhapsodyAi не ответил.
    """
    if not int(site_id or 0):
        raise HTTPException(status_code=422, detail='не выбран сайт')

    try:
        scenario = await rhapsody_api_scenario_find(int(site_id), DATING_EXPORT_SCENARIO)
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # Нет сценария — ошибка запроса, а не сбой: сайт выбран правильный, просто
    # выгружать его пока нечем. Текст говорит, что делать, потому что делать это
    # человеку в соседней панели, а не здесь.
    if not scenario:
        raise HTTPException(
            status_code=422,
            detail=f'для сайта нет сценария «{DATING_EXPORT_SCENARIO}»: '
                   f'заведите его в RhapsodyAi на вкладке «Сценарии»')

    # Вкладку сосед открывает и закрывает сам (`keep_session=False`): держать её
    # после выгрузки незачем — следующий прогон начнётся с чистой страницы, а
    # брошенная вкладка стоит ~143 МБ в его пуле.
    #
    # Подстановок и секретов нет: сценарий ходит по открытому каталогу. Войди он
    # под анкетой — понадобился бы `dating_login`, и это была бы другая работа.
    try:
        result = await rhapsody_api_scenario_run(int(scenario['id']), '', {}, {},
                                                 keep_session=False)
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    columns, profiles = dating_export_rows(result.get('extract') or {})

    return {'result': {'status': result.get('status') or 'error',
                       'scenario': scenario.get('name') or DATING_EXPORT_SCENARIO,
                       'columns': columns,
                       'profiles': profiles,
                       'step_no': int(result.get('step_no') or 0),
                       'error': str(result.get('error') or '')}}
