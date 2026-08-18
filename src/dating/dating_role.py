"""Выбор сценария под роль учётки.

Мужской и женский кабинеты сайта — разные приложения на одном домене: у одного
React, у другого GWT, и совпадает у них только форма входа. Сценарий выхода,
чтения анкеты, открытия чата — свои у каждой роли.

Поэтому настройка сайта разрешает две записи. Строка — сценарий на все роли:

    "logout_scenario": "выход"

Объект — по роли, и это обычный случай:

    "logout_scenario": {"lady": "выход (девушка)", "man": "выход (мужчина)"}

Ключ `default` в объекте закрывает роли, которые не перечислили. Нет ни роли, ни
`default` — значит для этой роли сценария нет, и вызывающий скажет об этом прямо,
а не потратит минуту на чужой сценарий, падающий по таймауту (так и было: мужской
учётке достался женский сценарий выхода и честно упал через 62 секунды).
"""

# Роль по умолчанию. Страница про девушек, и учётка без явной роли — девушка.
DATING_ROLE_DEFAULT = 'lady'

# Ключ «на все прочие роли» внутри объекта настройки.
DATING_ROLE_ANY = 'default'


def dating_role_scenario(presence: dict, key: str, role: str) -> str:
    """Имя сценария для этой роли или пустая строка, если его нет.

    Args:
        presence: настройка сайта (`browser_site.presence`).
        key: какая связка нужна — `logout_scenario`, `profile_scenario`.
        role: роль учётки (`dating_girl.role`).

    Returns:
        str: имя сценария в `browser_scenario` либо пустая строка.
    """
    value = presence.get(key)
    if isinstance(value, str):
        return value.strip()

    if not isinstance(value, dict):
        return ''

    role = str(role or DATING_ROLE_DEFAULT)

    return str(value.get(role) or value.get(DATING_ROLE_ANY) or '').strip()
