"""Страницы панели: вход, анкеты, seed и бэкап.

Отдаются сервером (Jinja2), а не собираются на клиенте: страниц несколько,
состояние живёт в базе, и городить сборку фронтенда ради четырёх шаблонов нечем
оправдать.

Все страницы под входом наследуют `base.html` — шапку с разделами. Исключение
одно, `login.html`: на форме входа навигация по закрытой панели бессмысленна.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from src.auth.auth import AuthUser, auth_admin_required, auth_required
from src.uvicorn.uvicorn_base import uvicorn_base_path

router = APIRouter(tags=['pages'])


@router.get('/', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_index(request: Request, user: AuthUser = Depends(auth_required)):
    """Корень — это и есть панель анкет: главной её в проекте нет."""
    return _dashboard_page(request, user, 'admin/dating.html')


@router.get('/admin/dating', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating(request: Request, user: AuthUser = Depends(auth_required)):
    """Тот же адрес, что был в RhapsodyAi. Оставлен ради закладок и ссылок в
    переписке: переезд не должен ломать то, что люди уже сохранили."""
    return _dashboard_page(request, user, 'admin/dating.html')


@router.get('/admin/dating/profile', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating_profile(request: Request, user: AuthUser = Depends(auth_required)):
    """Страница «Профили»: учётки сайта таблицей — состояние, работа, вход.

    Открыта всем вошедшим, а не только админу: это работа с анкетами, ради которой
    панель и открывают, а не операция над устройством установки.
    """
    return _dashboard_page(request, user, 'admin/dating_profile.html')


@router.get('/admin/seed', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_seed(request: Request, user: AuthUser = Depends(auth_admin_required)):
    """Seed SQL: выгрузка выбранных таблиц и заливка обратно. Только админу."""
    return _dashboard_page(request, user, 'admin/seed.html')


@router.get('/admin/users', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_users(request: Request, user: AuthUser = Depends(auth_admin_required)):
    """Пользователи: учётки, роли и токены доступа. Только админу — отсюда
    раздают права на саму панель."""
    return _dashboard_page(request, user, 'admin/users.html')


@router.get('/admin/backup', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_backup(request: Request, user: AuthUser = Depends(auth_admin_required)):
    """Бэкапы: полный дамп базы и восстановление. Только админу — восстановление
    перезаписывает базу целиком."""
    return _dashboard_page(request, user, 'admin/backup.html')


@router.get('/login', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_login(request: Request):
    """Форма входа. Открыта всем — иначе войти было бы некуда."""
    return request.app.state.jinja2_template.TemplateResponse(
        'login.html', {'request': request, 'base': uvicorn_base_path(),
                       'next': request.query_params.get('next', '/')})


# ── Приватное ────────────────────────────────────────────────────────────────

def _dashboard_page(request: Request, user: AuthUser, template: str):
    """Страница под входом: шаблон плюс общий контекст.

    Контекст один на все страницы, и собирается он здесь, а не в каждой точке:
    забытый `base` в одной из них ломает на проде все её ссылки разом, а
    заметно это только под общим доменом.
    """
    return request.app.state.jinja2_template.TemplateResponse(
        template, {'request': request, 'user': user, 'base': uvicorn_base_path()})
