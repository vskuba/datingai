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

## ⚠⚠ Роли и права — наши, механизм — слоя

Скелет не знает ни одного права по имени: какие права бывают и что входит в
роль, отвечает `auth_access.py` через договор `PanelAccess`. Полноправие при
этом определяется **совпадением набора прав с каталогом**, а не именем `admin`:
сравнение по имени отсекало бы любую другую полноправную роль.

## ⚠⚠ Вторая роль переименована: `dating` → `operator`

Имя роли — не косметика: она лежит в столбце `user.role`, её кладёт в куку вход
и сравнивает `PanelUser`. Значения в базе переносит миграция
`20261005120000_user_role_operator.sql`.

⚠ Порядок миграции и выкладки не важен: `PanelUser` трактует незнакомое значение
роли как **наименьшие права**, а это и есть оператор. Разбор — в шапке самой
миграции.

⚠ Переменные окружения посева сменились вместе с ролью:
`DATING_USERNAME`/`DATING_PASSWORD` → `OPERATOR_USERNAME`/`OPERATOR_PASSWORD`.
"""

from dashboard.src.panel.panel_access import panel_access_use
from dashboard.src.panel.panel_auth import (PanelUser as AuthUser,
                               panel_auth_admin_required as auth_admin_required,
                               panel_auth_need as auth_need,
                               panel_auth_required as auth_required,
                               panel_auth_token_user as auth_token_user,
                               panel_auth_user_check as auth_user_check)

from src.auth.auth_access import (AUTH_ACCESS_ROLE_ADMIN as AUTH_ROLE_ADMIN,
                                  AUTH_ACCESS_ROLE_OPERATOR as AUTH_ROLE_OPERATOR,
                                  AuthAccess, AuthPermission)

# ⚠⚠ Каталог прав подключается **на импорте модуля**, а не на старте приложения.
# Причина в том, что `auth_required` зовут из 393 мест, и часть из них — тесты,
# которые приложение не поднимают вовсе. Подключи мы договор в `lifespan`, в
# таких прогонах права остались бы незаведёнными, то есть полными у всех: тест
# на отказ зеленел бы, ничего не проверив.
panel_access_use(AuthAccess())

# Какие роли бывают. ⚠ Имя прежнее: список читает страница людей.
AUTH_ROLES = tuple(sorted(AuthAccess().roles_all()))

__all__ = ['AUTH_ROLE_ADMIN', 'AUTH_ROLE_OPERATOR', 'AUTH_ROLES', 'AuthPermission',
           'AuthUser', 'auth_admin_required', 'auth_need', 'auth_required',
           'auth_token_user', 'auth_user_check']
