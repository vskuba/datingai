"""Управление людьми: **дверь** к общему скелету `py_modules/panel_/panel_user.py`.

Учётки, роли и токены доступа устроены одинаково у всех панелей, вместе со всеми
своими запретами: нельзя менять роль себе, нельзя удалить себя, нельзя тронуть
последнего администратора. Каждый из них закрывает состояние, из которого нет
выхода через панель, — разбор в `.claude/docs-common/panel.md`, §5.

⚠ Посев первых учёток живёт там же (`panel_user_seed_admin`/`_operator`) и
приходит сюда через `auth_seed.py` — отдельным файлом, потому что его зовёт
старт приложения, а не страница.
"""

from panel_.panel_user import (PANEL_USER_NAME_RE as AUTH_USERS_NAME_RE,
                               PANEL_USER_PASSWORD_MIN as AUTH_USERS_PASSWORD_MIN,
                               PANEL_USER_TOKEN_TAIL as AUTH_USERS_TOKEN_TAIL,
                               panel_user_create as auth_users_create,
                               panel_user_delete as auth_users_delete,
                               panel_user_get as auth_users_get,
                               panel_user_list as auth_users_list,
                               panel_user_token_add as auth_users_token_add,
                               panel_user_token_delete as auth_users_token_delete,
                               panel_user_update as auth_users_update)

from src.auth.auth import AUTH_ROLES as AUTH_USERS_ROLES

__all__ = ['AUTH_USERS_NAME_RE', 'AUTH_USERS_PASSWORD_MIN', 'AUTH_USERS_ROLES',
           'AUTH_USERS_TOKEN_TAIL', 'auth_users_create', 'auth_users_delete',
           'auth_users_get', 'auth_users_list', 'auth_users_token_add',
           'auth_users_token_delete', 'auth_users_update']
