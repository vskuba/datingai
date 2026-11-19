"""Чат Luxee: переписка-мессенджер панели агентства.

Кадров сокета под девушкой у Luxee нет — читает и пишет общий сокет панели
(`site_socket_luxee.py`, транспорт `panel_socket`). От чата здесь нужны
два ответа: реплика ждёт собеседника в диалоге и тогда, когда его нет в сети
(Big D «Offline» получал реплики и отвечал позже), и **какие ответы
обязательны** — очередь Catch Up панели, без которой она не даёт писать в
другие диалоги.

Наследник goldenbride, а не базы: до этого файла Luxee брал переходное
умолчание goldenbride, и класс от базы молча сменил бы ему всё остальное.
"""

import time

from logging_.logging_ import logger_info

from src.dating.panel_fetch import DatingPanelFetchError
from src.dating.site.site_goldenbride import DatingSiteChatGoldenbride
from src.dating.site.site_socket_luxee import (site_socket_luxee_open,
                                               site_socket_luxee_tasks)

# Сколько живёт снимок очереди Catch Up. Круг ответов спрашивает её на каждую
# анкету (одиннадцать раз за заход), а очередь одна на сайт: без памяти это
# одиннадцать сокетов в минуту ради одного ответа.
SITE_CHAT_LUXEE_TASKS_SEC = 20

# Снимок: сайт → (когда снят, пары).
_TASKS: dict = {}


class DatingSiteChatLuxee(DatingSiteChatGoldenbride):
    """Luxee: всё как у умолчания, плюс доставка не в сети."""

    name = 'luxee'

    def reaches_offline(self) -> bool:
        return True

    async def required_pairs(self, site_id: int, presence: dict,
                             owner: str) -> set:
        """Очередь Catch Up панели: `{(её номер, его номер)}`.

        ⚠ Не спросилось — пустое множество и строка в журнал: очередь только
        **усиливает** ответ, и её молчание не должно глушить автоответ целиком.
        """
        taken = _TASKS.get(int(site_id))
        if taken and time.monotonic() - taken[0] < SITE_CHAT_LUXEE_TASKS_SEC:
            return taken[1]

        if not str(owner or '').isdigit():
            return set()

        try:
            async with site_socket_luxee_open(int(site_id), presence,
                                              int(owner)) as sock:
                tasks = await site_socket_luxee_tasks(sock)
        except DatingPanelFetchError as e:
            logger_info(f'[dating] очередь Catch Up сайта #{site_id} не спросилась: {e}')

            return set()

        pairs = {(str(t.get('importUid') or ''), str(t.get('userUid') or ''))
                 for t in tasks if t.get('isActive', True)}
        _TASKS[int(site_id)] = (time.monotonic(), pairs)

        return pairs
