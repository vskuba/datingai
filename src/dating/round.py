"""Обвязка воркерных кругов: сон до дедлайна, часовой пояс и журнал отказа.

Держатель сокетов (`chat_hold.py`) крутил пятнадцать кругов руками, и
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
from logging_.logging_ import logger_exception, logger_info
from redis_.redis_ import redis_conn_get
from setting_.setting_ import setting_get


async def round_loop(deadline: float, tick_sec: float, name: str,
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


async def round_offset_loop(deadline: float, tick_sec: float, name: str,
                                   body: Callable[[int], Awaitable[None]]) -> None:
    """То же, но `body` получает смещение пояса — перечитанным каждым кругом.

    ⚠ Смещение читаем **каждый круг**, а не один раз: настройку `timezone` правят
    на ходу, и запомненное значение означало бы расписание по старому поясу до
    конца смены — шесть часов.
    """

    async def _with_offset() -> None:
        offset = datetime_offset_hours(await setting_get('timezone', '+0'))
        await body(offset)

    await round_loop(deadline, tick_sec, name, _with_offset)


# ── Общее для кругов: часы и слот ────────────────────────────────────────────
# Девять кругов держали одно и то же: две функции про слот и часы. Тела
# совпадали строка в строку, разъезжались только ключ да слово в журнале —
# и ровно поэтому молча разъезжались правила. Ключи остаются в кругах, сюда
# переехало только тело.


def round_minutes(offset_hours: int = 0) -> int:
    """Минуты от начала местных суток: смены сверяют с часами на стене.

    ⚠ Один на все круги. Копии считали двумя способами (`tm_hour * 60 + tm_min`
    и целыми секундами от эпохи) и сходились лишь пока смещение целое в часах —
    но стоило одной копии отстать, как рассылка вставала по одним часам, а
    инвайты по другим.
    """
    local = time.gmtime(time.time() + int(offset_hours or 0) * 3600)

    return local.tm_hour * 60 + local.tm_min


def round_slot_due(key: str, site_id: int, what: str) -> bool:
    """Пора ли идти этому сайту; отметки ещё нет — пора.

    ⚠⚠ Сбой Redis решается в сторону бездействия: лишний заход стоит дела
    (вызовы LLM, вкладка браузера, подозрение сайта), пропущенный — только
    задержки. Где цена ошибок несимметрична, сомнение решается молчанием.
    """
    try:
        return not redis_conn_get().get(key.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] отметка {what} не прочиталась: {e}')

        return False


def round_slot_mark(key: str, site_id: int, every: float, what: str) -> None:
    """Отмечает заход. Срок отметки **и есть** настройка: Redis погасит её сам.

    ⚠ Ноль поднимаем до секунды: нулевой срок Redis не принимает, отметка не
    поставилась бы вовсе — и круг ходил бы каждым тиком.
    """
    try:
        redis_conn_get().setex(key.format(site_id=int(site_id)),
                               max(1, int(every)), '1')
    except Exception as e:
        logger_info(f'[dating] отметка {what} не поставилась: {e}')


def round_slot_release(key: str, site_id: int, what: str) -> None:
    """Снимает слот: захода не было — и держать слот нечем.

    ⚠ Сбой глотаем молча — отметка всё равно погаснет сама по сроку; хуже
    уронить обход остальных сайтов из-за неубранного ключа.
    """
    try:
        redis_conn_get().delete(key.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] отметка {what} не снялась: {e}')
