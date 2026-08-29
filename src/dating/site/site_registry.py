"""Какой драйвер разговаривает с этим сайтом.

Выбор идёт по ключу `driver` в настройке сайта (`browser_site.presence`) — то есть
по явному решению человека, заводившего закладку. Не по домену: он меняется, и у
одного сайта их бывает несколько. Не по `site_id`: это номер строки в чужой базе,
он ничего не говорит о протоколе и разъедется при переносе данных.

Нового сайта достаточно, чтобы появился новый файл рядом с этим и строка в
`DATING_SITE_DRIVERS`. Сайту, чья форма кадра укладывается в настройку, не нужно и
файла — хватит разделов `chat` и `send` в `presence` (см. `site_base.py`).
"""

from src.dating.site.site_base import DatingSiteChat
from src.dating.site.site_goldenbride import (DatingSiteChatGoldenbride,
                                              DatingSiteProfileGoldenbride)
from src.dating.site.site_profile import DatingSiteProfile

# Известные драйверы чата: имя из `presence.driver` → класс.
DATING_SITE_DRIVERS = {
    DatingSiteChatGoldenbride.name: DatingSiteChatGoldenbride,
    DatingSiteChat.name: DatingSiteChat,
}

# Драйверы анкеты — по тем же именам. Две таблицы, а не одна с парами: сайт
# может отличаться только чатом либо только анкетой, и заводить пустой класс
# ради симметрии незачем — недостающую половину закрывает базовая.
DATING_SITE_PROFILE_DRIVERS = {
    DatingSiteProfileGoldenbride.name: DatingSiteProfileGoldenbride,
    DatingSiteProfile.name: DatingSiteProfile,
}

# Чем разговаривать, когда сайт не назвал драйвер.
#
# ⚠ Это **переходная мера**, а не архитектурное решение. Правильное умолчание —
# базовый драйвер, работающий по настройке. Но на день переноса в закладках был
# один сайт, Goldenbride, и его `presence` не содержал ни `driver`, ни разделов
# `chat`/`send` с описанием кадров: весь протокол жил константами в коде. Возьми
# мы базовый — приём и отправка замолчали бы молча, ровно тем способом, каким этот
# протокол ломался всегда.
#
# Убирается так: проставить `driver` каждой закладке в RhapsodyAi, после чего
# заменить эту строку на `DatingSiteChat.name`. До тех пор она держит прод.
DATING_SITE_DRIVER_DEFAULT = DatingSiteChatGoldenbride.name


def dating_site_chat(presence: dict | None = None) -> DatingSiteChat:
    """Драйвер чата для этого сайта: кадры сокета, отправка, подтверждения.

    Args:
        presence: настройка сайта (`browser_site.presence`).

    Returns:
        DatingSiteChat: драйвер. Неизвестное имя — не отказ: берётся умолчание, а
        разбираться с опечаткой в закладке посреди живого чата поздно.
    """
    return _driver_of(DATING_SITE_DRIVERS, presence)


def dating_site_profile(presence: dict | None = None) -> DatingSiteProfile:
    """Драйвер анкеты для этого сайта: имена полей анкеты и собеседниц.

    Отдельно от чата, потому что это разные разговоры: чат различается формой
    кадра, анкета — именами полей. Сайту, у которого своё только одно из двух,
    вторую половину закрывает базовый драйвер.
    """
    return _driver_of(DATING_SITE_PROFILE_DRIVERS, presence)


def _driver_of(drivers: dict, presence: dict | None):
    """Драйвер из таблицы по ключу `driver`, с общим умолчанием."""
    presence = presence or {}
    name = str(presence.get('driver') or '').strip() or DATING_SITE_DRIVER_DEFAULT

    return drivers.get(name, drivers[DATING_SITE_DRIVER_DEFAULT])(presence)
