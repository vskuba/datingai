"""Страницы: вход и панель анкет. Больше в проекте страниц нет.

Отдаются сервером (Jinja2), а не собираются на клиенте: страница одна, состояние
живёт в базе, и городить сборку фронтенда ради двух шаблонов нечем оправдать.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from src.auth.auth import AuthUser, auth_required
from src.uvicorn.uvicorn_base import uvicorn_base_path

router = APIRouter(tags=['pages'])


@router.get('/', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_index(request: Request, user: AuthUser = Depends(auth_required)):
    """Корень — это и есть панель анкет: другой её в проекте нет."""
    return request.app.state.jinja2_template.TemplateResponse(
        'admin/dating.html', {'request': request, 'user': user, 'base': uvicorn_base_path()})


@router.get('/admin/dating', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating(request: Request, user: AuthUser = Depends(auth_required)):
    """Тот же адрес, что был в RhapsodyAi. Оставлен ради закладок и ссылок в
    переписке: переезд не должен ломать то, что люди уже сохранили."""
    return request.app.state.jinja2_template.TemplateResponse(
        'admin/dating.html', {'request': request, 'user': user, 'base': uvicorn_base_path()})


@router.get('/login', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_login(request: Request):
    """Форма входа. Открыта всем — иначе войти было бы некуда."""
    return request.app.state.jinja2_template.TemplateResponse(
        'login.html', {'request': request, 'base': uvicorn_base_path(),
         'next': request.query_params.get('next', '/')})
