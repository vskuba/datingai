"""Живое соединение с чатом сайта: забрать переписку сразу, а не по тику круга.

Круг забора ходит по тику (`chat_hold._chat_import_loop`, 15 секунд плюс сам
забор), и реплика мужчины ждала ответа от 15 до 45 секунд просто потому, что
круг спал. У сайта, где сервер чата сам шлёт оператору события (Luxee, socket.io
панели агентства), их можно слушать: пришло новое — забор зовётся сразу.

    chat_live_run ── на сайт, чей драйвер переписки умеет `live()`:
        └─ driver.journal_live(site, on_change, stop_at)   ← держит сокет
               └─ on_change() ── wake(site) ── забор сайта вне очереди

## ⚠⚠ Ускорение, а не замена

Круг по тику остаётся: пропущенный сигнал, оборванный сокет, протухший токен
стоят тогда только задержки, а не потерянной реплики. Поэтому и сам сигнал
ничего не записывает — он лишь зовёт тот же забор, что и круг, с теми же
отметками и правилами записи (`chat_panel.py`).

## ⚠ Соединение переоткрывается

По сроку (`CHAT_LIVE_SESSION_SEC`) — токен страницы чата живёт ограниченно, и
свежий берётся новым заходом; по обрыву — с паузой, растущей до минут:
сервер, рвущий связь, не станет добрее от того, что мы стучимся чаще.
"""

import asyncio
import time

from logging_.logging_ import logger_info

from src.dating.chat_panel import chat_panel_journal_ready
from src.dating.site.site_registry import site_registry_import
from src.dating.site_presence import site_presence

# Сколько живёт одно соединение до плановой смены: токен страницы чата
# протухает, а свежий даёт только новый заход на неё.
CHAT_LIVE_SESSION_SEC = 1800

# Пауза после обрыва: растёт вдвое до потолка, сбрасывается удачным сеансом.
CHAT_LIVE_BACKOFF_MIN = 10
CHAT_LIVE_BACKOFF_MAX = 300

# Как часто пересматривать список сайтов: новый сайт с живым драйвером или
# включённый забор подхватываются без перезапуска смены.
CHAT_LIVE_RESCAN_SEC = 300


async def chat_live_run(deadline: float, sites, wake) -> None:
    """Держать живые соединения сайтов до `deadline` (`time.monotonic()`).

    Args:
        sites: корутина без доводов — номера сайтов, у которых забор включён.
        wake: корутина `wake(site_id)` — забрать переписку сайта сейчас.

    ⚠ Ничего не бросает: беда одного сайта — строка в журнал и пауза у него
    же, а не остановка соседей и смены.
    """
    tasks: dict = {}
    try:
        while time.monotonic() < deadline:
            for site_id in await _sites_live(sites):
                if site_id in tasks and not tasks[site_id].done():
                    continue
                tasks[site_id] = asyncio.create_task(_site_run(site_id, deadline, wake))

            await asyncio.sleep(max(1.0, min(CHAT_LIVE_RESCAN_SEC,
                                             deadline - time.monotonic())))
    finally:
        for task in tasks.values():
            task.cancel()


async def _sites_live(sites) -> list[int]:
    """Сайты, чей драйвер переписки держит живое соединение и чья панель заведена."""
    found = []
    try:
        for site_id in await sites():
            driver = site_registry_import(await site_presence(site_id))
            if driver.live() and await chat_panel_journal_ready(site_id, driver):
                found.append(int(site_id))
    except Exception as e:
        logger_info(f'[dating] живые соединения: список сайтов не собрался: {e}')

    return found


async def _site_run(site_id: int, deadline: float, wake) -> None:
    """Сеансы одного сайта подряд: открыть, слушать, по сроку или обрыву — снова."""
    backoff = CHAT_LIVE_BACKOFF_MIN
    pending: set = set()

    async def on_change() -> None:
        # ⚠ Задача, а не ожидание: слушатель не должен стоять, пока идёт забор,
        # иначе следующий сигнал пролежит в сокете. Наложения разводит сам
        # забор — замок на сайт и отметка «повторить» (`chat_hold`).
        task = asyncio.create_task(wake(site_id))
        pending.add(task)
        task.add_done_callback(pending.discard)

    while time.monotonic() < deadline:
        started = time.monotonic()
        try:
            driver = site_registry_import(await site_presence(site_id))
            await driver.journal_live(site_id, on_change,
                                      min(deadline, started + CHAT_LIVE_SESSION_SEC))
            backoff = CHAT_LIVE_BACKOFF_MIN
            continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger_info(f'[dating] живое соединение сайта #{site_id} оборвалось: {e}')

        await asyncio.sleep(max(0.0, min(backoff, deadline - time.monotonic())))
        backoff = min(backoff * 2, CHAT_LIVE_BACKOFF_MAX)
