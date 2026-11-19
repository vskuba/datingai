"""Чат Luxee: переписка-мессенджер панели агентства.

Кадров сокета под девушкой у Luxee нет — читает и пишет общий сокет панели
(`site_socket_luxee.py`, транспорт `panel_socket`). От чата здесь нужен один
ответ: реплика ждёт собеседника в диалоге и тогда, когда его нет в сети, —
Big D «Offline» на странице чата получал реплики и отвечал на них позже.

Наследник goldenbride, а не базы: до этого файла Luxee брал переходное
умолчание goldenbride, и класс от базы молча сменил бы ему всё остальное.
"""

from src.dating.site.site_goldenbride import DatingSiteChatGoldenbride


class DatingSiteChatLuxee(DatingSiteChatGoldenbride):
    """Luxee: всё как у умолчания, плюс доставка не в сети."""

    name = 'luxee'

    def reaches_offline(self) -> bool:
        return True
