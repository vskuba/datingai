"""Обвязка воркерных кругов: сон до дедлайна, часовой пояс и журнал отказа.

Держатель сокетов (`dating_chat_hold.py`) крутил пятнадцать кругов руками, и
каждый — одними и теми же четырьмя строками: сон, отмеренный до конца смены;
работа; отказ в журнал. Копии разъезжаются молча: где-то забыли потолок сна,
где-то — перенос строки в сообщении. Здесь раз — и все круги одинаковые.

Отказ одного круга не конец смены: держатель стоит часами, за это время у
соседа бывает и перезапуск, и окно техработ, — круг переждёт и продолжит на
следующем тике. Поэтому ловим **всё** `Exception`, а не конкретные: уронить
смену случайной `KeyError` из разбора чужого ответа дороже, чем круг молчания
до следующего тика.

⚠ `_presence_loop` держателя остаётся своим и сюда не переезжает: он спит
**после** работы (присутствие нужно с первой секунды смены, а не через тик) и
обходит анкеты семафором, а не одним телом.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from datetime_.datetime_ import datetime_offset_hours
from logging_.logging_ import logger_exception
from setting_.setting_ import setting_get


async def dating_round_loop(deadline: float, tick_sec: float, name: str,
                            body: Callable[[], Awaitable[None]]) -> None:
    """Крутит `body` до `deadline` с тиком `tick_sec`; отказ круга — в журнал.

    Сначала сон, потом работа — порядок кругов держателя: смена только что
    поднялась, тяжёлую работу в первую же секунду начинать незачем.

    ⚠ Сон отсекаем остатком до дедлайна: тик крупнее остатка — и смена кончилась
    бы на полминуты позже, а за ней в очереди стоит следующая.
    """
    while time.monotonic() < deadline:
        await asyncio.sleep(min(tick_sec, max(1.0, deadline - time.monotonic())))

        try:
            await body()
        except Exception as e:
            # Не просто текст: «'NoneType' object has no attribute 'get'» без
            # кадров не показывает, кто именно вернул не то — ни у соседа, ни у сайта.
            logger_exception(f'[dating] круг {name}: {e}', exc=e)


async def dating_round_offset_loop(deadline: float, tick_sec: float, name: str,
                                   body: Callable[[int], Awaitable[None]]) -> None:
    """То же, но `body` получает смещение пояса — перечитанным каждым кругом.

    ⚠ Смещение читаем **каждый круг**, а не один раз: настройку `timezone` правят
    на ходу, и запомненное значение означало бы расписание по старому поясу до
    конца смены — шесть часов.
    """

    async def _with_offset() -> None:
        offset = datetime_offset_hours(await setting_get('timezone', '+0'))
        await body(offset)

    await dating_round_loop(deadline, tick_sec, name, _with_offset)
