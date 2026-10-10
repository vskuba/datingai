"""Учётки, которые заводятся сами: **дверь** к `py_modules/panel_/panel_user.py`.

Чтобы в свежую панель было чем войти, заводятся две штуки по числу ролей —
администратор из `ADMIN_USERNAME` и оператор из `OPERATOR_USERNAME`.

⚠ Условия у них **разные**, и это не оплошность: админ заводится только при
пустой таблице (признак свежей установки), оператор — когда такого логина ещё
нет (вторая роль обычно появляется позже самой панели). Разбор —
`.claude/docs-common/panel.md`, §4.
"""

from dashboard.src.panel.panel_user import (PANEL_USER_SEED_TOKEN_NOTE as AUTH_SEED_TOKEN_NOTE,
                               panel_user_seed_admin as auth_seed_first_user,
                               panel_user_seed_operator as auth_seed_operator)

__all__ = ['AUTH_SEED_TOKEN_NOTE', 'auth_seed_first_user', 'auth_seed_operator']
