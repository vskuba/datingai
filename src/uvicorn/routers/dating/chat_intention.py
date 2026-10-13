"""Намерения разговора: две ручки вкладки «Намерения и действия».

Правила самих намерений — в `src/dating/chat_intention.py`; здесь только дверь.

⚠ **Своя дверь, а не хвост чата.** Вкладка живёт в полосе «Чата», но намерения
приезжают и из писем — их пишет одна точка `_agent_ask` на все четыре канала.
Повесь мы ручку на `/api/dating/chat`, адрес обещал бы только чат.

⚠ **Права — как у пулов, всем вошедшим.** Это работа оператора: он смотрит, что
агент наобещал и что из этого сделано. Админского здесь нет ничего — ни адресов,
ни паролей.
"""

from fastapi import APIRouter, Depends

from src.auth.auth import AuthUser, auth_required
from src.dating.chat_intention import chat_intention_pairs, chat_intention_rows

router = APIRouter(prefix='/api/dating/chat-intention', tags=['dating'])


@router.get('/pairs', include_in_schema=False)
async def chat_intention_pairs_list(site_id: int = 0, profile_id: int = 0,
                                    page: int = 1, per: int = 0,
                                    user: AuthUser = Depends(auth_required)):
    """Пары с намерениями — левая колонка вкладки.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не пары всех сайтов.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.
        page: страница, с единицы. За концом — поправится сама.
        per: строк на странице. Ноль — предел по умолчанию.

    Returns:
        dict: `result` — пары страницы, рядом `page`, `pages`, `total`.
    """
    got = await chat_intention_pairs(int(site_id or 0), int(profile_id or 0),
                                     int(page or 1), int(per or 0))

    return {'result': got['rows'], 'page': got['page'], 'pages': got['pages'],
            'total': got['total']}


@router.get('', include_in_schema=False)
async def chat_intention_list(site_id: int = 0, profile_id: int = 0,
                              uid: str = '', status: str = '',
                              page: int = 1, per: int = 0,
                              user: AuthUser = Depends(auth_required)):
    """Намерения страницей — средняя и правая колонки вкладки.

    Args:
        site_id: сайт-закладка. Ноль — пустой список.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.
        uid: сузить до собеседника. Пусто — все.
        status: `done` — средняя колонка (что сделано), `open` — правая (пул).
            Пусто — и те, и другие.
        page: страница, с единицы.
        per: строк на странице. Ноль — предел по умолчанию.

    Returns:
        dict: `result` — записи страницы, рядом `page`, `pages`, `total`.

    ⚠ **Одна ручка на две колонки.** Они спрашивают одно и то же разными
    доводами; вторая ручка держала бы две копии отбора и разошлась бы с первой.
    """
    got = await chat_intention_rows(int(site_id or 0), int(profile_id or 0),
                                    str(uid or ''), str(status or ''),
                                    int(page or 1), int(per or 0))

    return {'result': got['rows'], 'page': got['page'], 'pages': got['pages'],
            'total': got['total']}
