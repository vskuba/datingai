"""Настройки панели: **дверь** к общему скелету `py_modules/panel_/`.

Своей копии здесь больше нет: ключ → значение, что прячем от страницы и как
читается часовой пояс — одинаково у всех панелей. Устройство —
`.claude/docs-common/panel.md`.

⚠ Дверь, а не прямой импорт: `setting_timezone_hours` зовут четыре страницы
раздела (`dashboard`, рассылка, инвайты, письма избранным). Переименование ради
длины имени стоило бы правки каждой.

⚠⚠ Роутер при этом **не** проходит насквозь: его подключает `main_uvicorn`
прямо из слоя (`panel_setting_api_router()`). Второй объект роутера на тот же
префикс дал бы задвоенные маршруты, и какой из них ответит — вопрос порядка
подключения.
"""

from dashboard.src.panel.panel_setting_api import (PANEL_SETTING_KEY_MAX as SETTING_KEY_MAX,
                                      PANEL_SETTING_TIMEZONE_DEFAULT
                                      as SETTING_TIMEZONE_DEFAULT,
                                      PANEL_SETTING_TIMEZONE_KEY
                                      as SETTING_TIMEZONE_KEY,
                                      panel_setting_timezone_hours
                                      as setting_timezone_hours)

__all__ = ['SETTING_KEY_MAX', 'SETTING_TIMEZONE_DEFAULT', 'SETTING_TIMEZONE_KEY',
           'setting_timezone_hours']
