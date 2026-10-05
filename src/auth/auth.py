"""Вход в панель: **дверь** к общему скелету `py_modules/panel_/`.

Своей копии здесь больше нет. Вход, две роли, зависимости роутов и проверка
пароля одинаковы у всех панелей — различается только предметная часть, ради
которой панель открывают. Устройство и грабли — `.claude/docs-common/panel.md`.

## ⚠ Почему дверь, а не прямой импорт слоя по месту

Имена здесь зовут 393 раза: `auth_required` висит на каждой точке API,
`AuthUser` — в объявлении почти каждого обработчика. Переименовать их все в
`panel_auth_required`/`PanelUser` значило бы тронуть 393 строки ради того, чтобы
имя стало длиннее, — и всё это в единственном месте, которое нельзя сломать
незаметно: право доступа.

Дверь даёт то же самое дешевле: реализация одна, в слое, а строки вызова не
трогаются вовсе. Тот же приём, которым здесь подключены круги
(`src/dating/round.py`) и смена воркера.

## ⚠⚠ Вторая роль переименована: `dating` → `operator`

Имя роли — не косметика: она лежит в столбце `user.role`, её кладёт в куку вход
и сравнивает `PanelUser`. Пока здесь было своё имя, проект из общего скелета
выпадал. Значения в базе переносит миграция
`20261005120000_user_role_operator.sql`.

⚠ Порядок миграции и выкладки не важен: `PanelUser` трактует незнакомое значение
роли как **наименьшие права**, а это и есть оператор. Разбор — в шапке самой
миграции.

⚠ Переменные окружения посева сменились вместе с ролью:
`DATING_USERNAME`/`DATING_PASSWORD` → `OPERATOR_USERNAME`/`OPERATOR_PASSWORD`.
"""

from panel_.panel_auth import (PANEL_AUTH_ROLE_ADMIN as AUTH_ROLE_ADMIN,
                               PANEL_AUTH_ROLE_OPERATOR as AUTH_ROLE_OPERATOR,
                               PANEL_AUTH_ROLES as AUTH_ROLES,
                               PanelUser as AuthUser,
                               panel_auth_admin_required as auth_admin_required,
                               panel_auth_required as auth_required,
                               panel_auth_token_user as auth_token_user,
                               panel_auth_user_check as auth_user_check)

__all__ = ['AUTH_ROLE_ADMIN', 'AUTH_ROLE_OPERATOR', 'AUTH_ROLES', 'AuthUser',
           'auth_admin_required', 'auth_required', 'auth_token_user',
           'auth_user_check']
