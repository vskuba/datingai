"""Сайты-закладки — витрина того, что лежит у соседа.

Своей таблицы сайтов здесь нет: закладки, сценарии и пул Chromium — хозяйство
RhapsodyAi. Но странице нужен список, чтобы человек выбрал, к какому сайту
относится анкета, и ходить за ним из браузера напрямую нельзя: служебный токен
живёт на сервере и в разметку попадать не должен.

Поэтому одна тонкая ручка: страница спрашивает свой сервер, сервер — соседа.
"""

from fastapi import APIRouter, Depends, HTTPException

from src.auth.auth import auth_required
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_list

router = APIRouter(prefix='/api/site', tags=['site'])


@router.get('')
async def site_list(user=Depends(auth_required)):
    """Сайты-закладки RhapsodyAi в порядке раскладки.

    Сосед недоступен — 502 с его текстом: в панели должно быть написано «RhapsodyAi
    недоступен», а не пустой селект, из которого непонятно, кончились ли сайты или
    сломалась связь.
    """
    try:
        return {'result': await rhapsody_api_site_list()}
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))
