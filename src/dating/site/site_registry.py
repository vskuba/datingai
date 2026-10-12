"""Какой драйвер разговаривает с этим сайтом.

Выбор идёт по ключу `driver` в настройке сайта (`browser_site.presence`) — то есть
по явному решению человека, заводившего закладку. Не по домену: он меняется, и у
одного сайта их бывает несколько. Не по `site_id`: это номер строки в чужой базе,
он ничего не говорит о протоколе и разъедется при переносе данных.

Нового сайта достаточно, чтобы появился новый файл рядом с этим и строка в
`DATING_SITE_DRIVERS`. Сайту, чья форма кадра укладывается в настройку, не нужно и
файла — хватит разделов `chat` и `send` в `presence` (см. `site_base.py`).
"""

from src.dating.site.site_about import (DatingSiteAbout,
                                        DatingSiteAboutLadadate)
from src.dating.site.site_base import DatingSiteChat
from src.dating.site.site_goldenbride import (DatingSiteChatGoldenbride,
                                              DatingSiteProfileGoldenbride)
from src.dating.site.site_panel import (DatingSitePanel,
                                        DatingSitePanelLadadate)
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

# Драйверы разбора страницы собеседника — по тем же именам, третья таблица.
#
# ⚠ **Своя таблица, а не поле в прежних.** Разбор HTML отвечает на третий вопрос:
# чат — про кадры сокета, анкета девушки — про имена полей JSON, этот — про классы
# в вёрстке. Сайт может совпасть с соседом в двух и разойтись в третьем, и общая
# таблица заставляла бы заводить класс ради того, что не менялось.
DATING_SITE_ABOUT_DRIVERS = {
    DatingSiteAboutLadadate.name: DatingSiteAboutLadadate,
    DatingSiteAbout.name: DatingSiteAbout,
}

# Драйверы админ-панели агентства — по тем же именам, четвёртая таблица.
#
# ⚠ **Своя таблица, а не поле в прежних.** Панель отвечает на четвёртый вопрос: чат
# — про кадры сокета, анкета девушки — про имена полей JSON, разбор страницы
# собеседника — про классы вёрстки, эта — про имена полей формы панели. Сайт может
# совпасть с соседом в трёх и разойтись в четвёртом.
DATING_SITE_PANEL_DRIVERS = {
    DatingSitePanelLadadate.name: DatingSitePanelLadadate,
    DatingSitePanel.name: DatingSitePanel,
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


def site_registry_chat(presence: dict | None = None) -> DatingSiteChat:
    """Драйвер чата для этого сайта: кадры сокета, отправка, подтверждения.

    Args:
        presence: настройка сайта (`browser_site.presence`).

    Returns:
        DatingSiteChat: драйвер. Неизвестное имя — не отказ: берётся умолчание, а
        разбираться с опечаткой в закладке посреди живого чата поздно.
    """
    return _driver_of(DATING_SITE_DRIVERS, presence)


def site_registry_profile(presence: dict | None = None) -> DatingSiteProfile:
    """Драйвер анкеты для этого сайта: имена полей анкеты и собеседниц.

    Отдельно от чата, потому что это разные разговоры: чат различается формой
    кадра, анкета — именами полей. Сайту, у которого своё только одно из двух,
    вторую половину закрывает базовый драйвер.
    """
    return _driver_of(DATING_SITE_PROFILE_DRIVERS, presence)


def site_registry_about(presence: dict | None = None) -> DatingSiteAbout:
    """Чем разбирать страницу собеседника у этого сайта.

    Третья половина драйвера: чат — кадрами, анкета девушки — именами полей, эта —
    классами в вёрстке. Сайту, чья разметка укладывается в базовую, класса не нужно
    вовсе: хватит раздела `about` в `presence` (см. `site_about.py`).

    ⚠ **Умолчание здесь — базовый драйвер, а не первый попавшийся.** У чата
    умолчанием стоит Goldenbride, и это переходная мера: его протокол жил
    константами раньше, чем появилась настройка. У разбора страницы такой истории
    нет — базовый работает у всякого сайта с постоянными классами.
    """
    if not isinstance(presence, dict):
        return DatingSiteAbout()

    name = str(presence.get('driver') or '').strip()

    return DATING_SITE_ABOUT_DRIVERS.get(name, DatingSiteAbout)(presence)


def site_registry_panel(presence: dict | None = None) -> DatingSitePanel:
    """Чем читать страницу анкеты в админ-панели агентства этого сайта.

    Четвёртая половина драйвера: панель отвечает на свой вопрос — как зовутся поля
    её формы и в каком виде она пишет рост, вес и дату рождения.

    ⚠ **Умолчание — базовый драйвер с пустым путём**, то есть «панель этого сайта
    читать нечем», и ручка говорит это словами. Подставлять сюда чужой разбор
    нельзя: имена полей у панелей свои, и чужая карта дала бы пустые параметры,
    неотличимые от «анкета не заполнена».
    """
    if not isinstance(presence, dict):
        return DatingSitePanel()

    name = str(presence.get('driver') or '').strip()

    return DATING_SITE_PANEL_DRIVERS.get(name, DatingSitePanel)(presence)


def _driver_of(drivers: dict, presence: dict | None):
    """Драйвер из таблицы по ключу `driver`, с общим умолчанием."""
    presence = presence or {}
    name = str(presence.get('driver') or '').strip() or DATING_SITE_DRIVER_DEFAULT

    return drivers.get(name, drivers[DATING_SITE_DRIVER_DEFAULT])(presence)
