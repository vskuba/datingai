"""Фоновая сверка снимка списка сайтов со списком у соседа.

Снимок (`dating_site`) показывает панель, а правду о том, какие сайты у соседа
живы, знает только он. Этот модуль — единственная дорога за правдой: заводится в
фоне от первой открытой страницы раздела, не чаще раза в полминуты
(`DATING_SITE_SYNC_EVERY_SEC`), и умеет сверять сразу по кнопке «обновить».

⚠ **Неудачная сверка снимок не трогает** — подробнее у `_sync_run`.

Выделен из `dating_site.py`: у сверки свои свидетели (задачи, выдержка,
последняя ошибка) и своя дверь — только к соседу; настройки сайта ей не нужны.
"""

import asyncio
import time

from datetime_.datetime_ import datetime_utc_now
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_error import (DATING_ERROR_SITES_SYNC, dating_error_clear,
                                     dating_error_write)
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_list

# Как часто сверяем снимок с соседом. Полминуты: список сайтов меняют руками и
# редко, а страниц в разделе пять, и открывают их подряд — сверка на каждое
# открытие была бы запросом к соседу на каждый щелчок по меню.
DATING_SITE_SYNC_EVERY_SEC = 30

# Идущая сверка и её память. Списком из одного значения, а не переменной: так их
# видно из вложенных функций без `global` в каждой.
_sync_task = None
_sync_at = [0.0]
_sync_done = [0.0]
_sync_error = ['']


def dating_site_sync_start() -> bool:
    """Заводит фоновую сверку снимка с соседом, если её пора и можно заводить.

    Returns:
        bool: идёт ли сверка прямо сейчас — своя или начатая раньше.

    ⚠ **Два сторожа, и оба обязательны** — те же, что у списков собеседников
    (`dating_people.py`). Первый: сверка уже идёт, и каждая страница раздела не
    должна заводить свою. Второй: выдержка между попытками — иначе лежащий сосед
    получал бы запрос на каждое открытие любой страницы, а их у раздела пять.

    Ждать сверку **никто не ждёт**: ответ уходит со снимком, а новое имя сайта
    появится в следующем ответе. Список сайтов — не то, ради чего человека держат
    на пустой странице.
    """
    global _sync_task

    if _sync_task and not _sync_task.done():
        return True

    if time.time() - _sync_at[0] < DATING_SITE_SYNC_EVERY_SEC:
        return False

    _sync_at[0] = time.time()
    _sync_task = asyncio.ensure_future(_sync_run())

    return True


async def dating_site_sync_now() -> dict:
    """Сверка прямо сейчас, с ожиданием. Для кнопки «обновить» и для тестов."""
    return await _sync_run()


# ── Приватное ────────────────────────────────────────────────────────────────────────


async def _sync_run() -> dict:
    """Спрашивает список у соседа и переписывает снимок. Не бросает.

    Returns:
        dict: `synced` — сколько сайтов в снимке, `error` — причина, если не вышло.

    ⚠ **Неудачная сверка снимок не трогает.** Сосед недоступен — это «сейчас не
    узнали», а не «сайтов больше нет»; стерев снимок, мы получили бы ровно тот
    пустой селект, от которого уходим.
    """
    try:
        sites = await rhapsody_api_site_list()
    except RhapsodyError as e:
        _sync_error[0] = str(e)
        await dating_error_write(DATING_ERROR_SITES_SYNC, str(e))
        logger_info(f'[dating] список сайтов не сверился: {e}')

        return {'synced': 0, 'error': str(e)}
    except Exception as e:
        # Сюда падает то, чего мы не предусмотрели. Ждать эту задачу некому, и без
        # перехвата исключение утонуло бы в логе `asyncio` без следа на странице.
        _sync_error[0] = f'сверка сорвалась: {e}'
        await dating_error_write(DATING_ERROR_SITES_SYNC, _sync_error[0])
        logger_info(f'[dating] сверка списка сайтов упала: {e}')

        return {'synced': 0, 'error': _sync_error[0]}

    await _snapshot_write(sites)

    _sync_error[0] = ''
    _sync_done[0] = time.time()
    await dating_error_clear(DATING_ERROR_SITES_SYNC)

    return {'synced': len(sites), 'error': ''}


async def _snapshot_write(sites: list) -> None:
    """Кладёт ответ соседа в снимок: заводит новые, обновляет прежние, метит ушедшие.

    ⚠ **Ушедшие метятся, а не удаляются.** Вместе со строкой ушли бы и наши
    настройки этого сайта, а закладку могли убрать по ошибке или на время — и
    восстановить её у соседа куда проще, чем вспомнить, что было настроено здесь.
    """
    now = datetime_utc_now().strftime('%Y-%m-%d %H:%M:%S')
    seen = []

    async with mysql_get_db_async() as db:
        for site in sites:
            site_id = int(site.get('id') or 0)
            if not site_id:
                continue
            seen.append(site_id)

            await db.execute(
                'INSERT INTO site (site_id, name, url, tags, is_gone, synced_at)'
                ' VALUES (%s, %s, %s, %s, 0, %s)'
                ' ON DUPLICATE KEY UPDATE name = VALUES(name), url = VALUES(url),'
                ' tags = VALUES(tags), is_gone = 0, synced_at = VALUES(synced_at)',
                (site_id, str(site.get('name') or '')[:190],
                 str(site.get('url') or '')[:255], str(site.get('tags') or '')[:190], now))

        # Всё, чего в ответе не было, у соседа больше нет. Считаем это правдой
        # только потому, что ответ **удался**: неудачная сверка сюда не доходит.
        if seen:
            marks = ', '.join(['%s'] * len(seen))
            await db.execute(
                f'UPDATE site SET is_gone = 1 WHERE site_id NOT IN ({marks})', seen)

        await db.connection.commit()


def dating_site_sync_answer(refreshing: bool) -> dict:
    """Что странице знать про сверку: когда сверяли, идёт ли сейчас, чем кончилась."""
    return {'refreshing': bool(refreshing),
            'error': _sync_error[0],
            'at': int(_sync_done[0]) or 0}
