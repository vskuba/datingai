"""Ручка страницы «Ошибки»: что сломано прямо сейчас.

Отказы, которые не привязаны к анкете, — не обновился список сайтов, не ответил
сосед, встала фоновая сверка. Журнал анкеты на такое не годится: работа не её.
Хранение и правила — `src/dating/error.py`.

Открыто всем вошедшим: оператор первым замечает, что панель показывает не то, и
отправлять его к администратору за объяснением значило бы терять время дважды.
"""

from fastapi import APIRouter, Depends

from src.auth.auth import auth_required
from src.dating.error import ERROR_LIMIT, error_rows

router = APIRouter(prefix='/api/dating/error', tags=['dating'])


@router.get('', include_in_schema=False)
async def error_list(site_id: int = 0, limit: int = ERROR_LIMIT,
                            user=Depends(auth_required)):
    """Что сломано сейчас — свежее сверху.

    Args:
        site_id: показать отказы этого сайта. Ноль — все.
        limit: сколько строк отдать.

    Returns:
        dict: `errors` — записи `source`, `title`, `site_id`, `message`, `hits`,
        `first_at`, `last_at`.

    ⚠ **Отбор по сайту не прячет общие отказы** (`site_id = 0`): «список сайтов не
    обновляется» не относится ни к одному сайту в отдельности, но касается любого —
    и объясняет ровно то, что человек видит на странице.

    Время — в UTC, как всё в проекте: сдвигает страница по настройке `timezone`.
    """
    return {'result': {'errors': await error_rows(int(site_id or 0),
                                                         min(int(limit or 0), ERROR_LIMIT))}}
