"""Вход, выход и «кто я» — три точки, больше панели не нужно."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info

from src.auth.auth import AuthUser, auth_required, auth_user_check

router = APIRouter(prefix='/auth', tags=['auth'])


class AuthLogin(BaseModel):
    """Пара логин-пароль."""

    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=200)


@router.post('/login')
async def auth_login(data: AuthLogin, request: Request):
    """Заводит сессию. Не сошлось — 401 без подробностей о том, что именно."""
    user = await auth_user_check(data.username, data.password)
    if not user:
        logger_info(f'[auth] неудачный вход: {data.username}')
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail='неверный логин или пароль')

    request.session['user_id'] = user.user_id
    request.session['username'] = user.username
    # Роль кладётся в куку вместе с именем: `auth_required` читает её оттуда,
    # чтобы не ходить в базу на каждый запрос. Кука подписана `SessionMiddleware`,
    # подделать роль с той стороны нельзя.
    request.session['role'] = user.role
    logger_info(f'[auth] вход: {user}')

    return {'result': {'id': user.user_id, 'username': user.username, 'role': user.role}}


@router.post('/logout')
async def auth_logout(request: Request):
    """Гасит сессию. Повторный выход не ошибка: результат тот же — сессии нет."""
    request.session.clear()

    return {'result': 'ok'}


@router.get('/me')
async def auth_me(user: AuthUser = Depends(auth_required)):
    """Кто сейчас в сессии — страница показывает это в шапке."""
    return {'result': {'id': user.user_id, 'username': user.username, 'role': user.role}}
