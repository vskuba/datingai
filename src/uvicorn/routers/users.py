"""Точки управления людьми: учётки, роли, токены доступа.

Живут в разделе настроек и открыты только администратору — как seed и бэкапы.
Право проверяется зависимостью `auth_admin_required` в объявлении каждого роута,
а не в теле: забытую проверку тогда находит чтение файла, а не разбор
происшествия.

Решения «можно ли это сделать» принимает домен (`src/auth/auth_users.py`), здесь
только разбор запроса и перевод его отказов в коды HTTP:

* `ValueError` → 422: запрос понят, но так делать нельзя (имя занято, короткий
  пароль, правка роли себе);
* `LookupError` → 404: править нечего.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.auth.auth import AuthUser, auth_admin_required
from src.auth.auth_users import (auth_users_create, auth_users_delete, auth_users_list,
                                 auth_users_token_add, auth_users_token_delete,
                                 auth_users_update)

router = APIRouter(prefix='/api/users', tags=['users'])


@router.get('')
async def users_list(user: AuthUser = Depends(auth_admin_required)):
    """Учётки с ролями и выданными токенами. Значений токенов здесь нет."""
    return {'result': {'users': await auth_users_list()}}


@router.post('', status_code=201)
async def users_create(payload: dict, user: AuthUser = Depends(auth_admin_required)):
    """Заводит учётку."""
    try:
        return {'result': await auth_users_create(
            str(payload.get('username') or ''), str(payload.get('password') or ''),
            str(payload.get('role') or ''))}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.put('/{user_id}')
async def users_update(user_id: int, payload: dict,
                       user: AuthUser = Depends(auth_admin_required)):
    """Меняет пароль и роль. Пустое поле — «не трогать»."""
    try:
        return {'result': await auth_users_update(
            user_id, str(payload.get('password') or ''), str(payload.get('role') or ''),
            actor_id=user.user_id)}
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.delete('/{user_id}')
async def users_delete(user_id: int, user: AuthUser = Depends(auth_admin_required)):
    """Удаляет учётку вместе с её токенами. Анкеты и переписка остаются."""
    try:
        await auth_users_delete(user_id, actor_id=user.user_id)
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    return {'result': 'ok'}


@router.post('/{user_id}/token')
async def users_token_add(user_id: int, payload: dict,
                          user: AuthUser = Depends(auth_admin_required)):
    """Выдаёт токен. **Значение приходит только в этом ответе** — больше нигде."""
    try:
        return {'result': await auth_users_token_add(
            user_id, str(payload.get('note') or ''))}
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete('/token/{token_id}')
async def users_token_delete(token_id: int, user: AuthUser = Depends(auth_admin_required)):
    """Отзывает токен. Учётка остаётся."""
    await auth_users_token_delete(token_id)

    return {'result': 'ok'}
