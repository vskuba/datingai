"""Снимок списка сайтов: панель работает, когда сосед недоступен.

⚠ **Ради этого снимок и заведён.** Селект сайта стоит в шапке каждой страницы
раздела, и список для него брали у соседа при каждом открытии: лежит он — селект
пуст, и панель целиком выглядит сломанной, хотя наши данные на месте.

Сосед здесь поддельный (`rhapsody_fake.py`), база настоящая: проверяется как раз
она — что снимок лёг, пережил отказ и обновился, когда сосед ответил.
"""

import pytest

from src.dating.dating_error import (DATING_ERROR_SITES_SYNC, dating_error_clear,
                                     dating_error_rows)
from src.uvicorn.routers.dating import dating_site
from src.uvicorn.routers.dating.dating_site import (dating_site_rows, dating_site_sync_now,
                                                    dating_site_sync_start)
from src.uvicorn.routers.site import site_list

# Сайты поддельного соседа.
LADA, GOLD = 343, 19


@pytest.fixture
async def synced(rhapsody):
    """Снимок, сверенный с поддельным соседом, и чистая страница «Ошибки» после.

    Сторожа сверки сбрасываются: они переживают запросы намеренно (в этом и смысл),
    а значит переживут и тест — следующий не смог бы завести свою сверку.
    """
    await _sync_stop()
    _guards_clear()
    await dating_site_sync_now()

    yield rhapsody

    await _sync_stop()
    _guards_clear()
    await dating_error_clear(DATING_ERROR_SITES_SYNC)


def _guards_clear() -> None:
    dating_site._sync_task = None
    dating_site._sync_at[0] = 0.0
    dating_site._sync_error[0] = ''


async def _sync_stop() -> None:
    """Дожидается фоновой сверки, если она заведена.

    ⚠ **Не бросить её на произвол.** Сверка держит соединение из общего пула, а пул
    закрывается после каждого теста (`conftest.py`): брошенная задача не возвращает
    соединение, и закрытие пула ждёт его вечно. Так и вышло — сюита повисла на
    первом же прогоне.
    """
    task = dating_site._sync_task
    if task and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:
            pass


class TestSnapshot:
    """Что лежит в снимке и что из него показывается."""

    async def test_sync_fills_the_snapshot(self, synced):
        rows = {row['id']: row for row in await dating_site_rows()}

        assert LADA in rows and GOLD in rows
        assert rows[LADA]['name'] == 'Ladadate' and rows[LADA]['url']
        assert rows[LADA]['synced_at'], 'снимок без времени сверки не отличить от свежего'

    async def test_rename_reaches_the_snapshot(self, synced):
        """⚠ Расхождение с соседом лечится сверкой, и это цена всей затеи.

        Переименовали сайт у него — новое имя приходит в ту же секунду, когда он
        отвечает. Живёт расхождение ровно столько, сколько сосед недоступен.
        """
        synced.sites[0]['name'] = 'Ladadate новое имя'
        await dating_site_sync_now()

        rows = {row['id']: row for row in await dating_site_rows()}

        assert rows[LADA]['name'] == 'Ladadate новое имя'

    async def test_gone_site_leaves_the_select(self, synced):
        """Закладку убрали у соседа — из селекта она уходит: предлагать её к выбору
        значит обещать работу, которой не будет."""
        synced.sites = [site for site in synced.sites if site['id'] != GOLD]
        await dating_site_sync_now()

        assert GOLD not in [row['id'] for row in await dating_site_rows()]

    async def test_gone_site_keeps_its_settings(self, synced):
        """⚠ Ушедший сайт **метится, а не удаляется**.

        Вместе со строкой ушли бы наши настройки этого сайта, а закладку могли
        убрать по ошибке или на время — восстановить её у соседа куда проще, чем
        вспомнить, что было настроено здесь.
        """
        from src.uvicorn.routers.dating.dating_site import dating_site_save, DatingSiteSave

        await dating_site_save(GOLD, DatingSiteSave(settings={'тест_снимок': '1'}), user=None)
        try:
            synced.sites = [site for site in synced.sites if site['id'] != GOLD]
            await dating_site_sync_now()

            # Закладку вернули: сайт снова в списке, и настройки должны быть на месте.
            synced.sites = [{'id': GOLD, 'name': 'Goldenbride',
                             'url': 'https://goldenbride.net', 'tags': 'dating'}]
            await dating_site_sync_now()

            from src.uvicorn.routers.dating.dating_site import dating_site_setting

            assert await dating_site_setting(GOLD, 'тест_снимок') == '1', 'настройки потеряны'
        finally:
            await dating_site_save(GOLD, DatingSiteSave(settings={}), user=None)


class TestNeighbourDown:
    """⚠ Главное: лежащий сосед не гасит панель."""

    async def test_list_survives_an_unreachable_neighbour(self, synced):
        """Список остаётся тем, что мы знали. Это и есть весь смысл снимка."""
        synced.offline()
        _guards_clear()

        answer = await site_list(user=None)

        assert [row['id'] for row in answer['result']] == [GOLD, LADA], 'селект опустел'

    async def test_failed_sync_does_not_erase_the_snapshot(self, synced):
        """Сосед недоступен — это «сейчас не узнали», а не «сайтов больше нет».

        Сотри мы снимок неудачной сверкой — получили бы ровно тот пустой селект, от
        которого уходим.
        """
        synced.offline()
        _guards_clear()
        await dating_site_sync_now()

        assert len(await dating_site_rows()) == 2

    async def test_refusal_lands_on_the_errors_page(self, synced):
        """Отказ сверки виден на «Ошибках», а не вместо списка.

        Молча показывать снимок — худший вид вранья: человек смотрит на список
        сайтов и не знает, что тот недельной давности.
        """
        synced.offline()
        _guards_clear()
        await dating_site_sync_now()

        rows = [row for row in await dating_error_rows()
                if row['source'] == DATING_ERROR_SITES_SYNC]

        assert rows, 'отказ сверки нигде не записан'
        assert 'RhapsodyAi недоступен' in rows[0]['message']
        assert rows[0]['site_id'] == 0, 'отказ приписан сайту, хотя он про связь'

    async def test_repeats_count_instead_of_flooding(self, synced):
        """⚠ Строка одна на источник, повторы — счётчиком.

        Лежащий сосед отвечает отказом на каждый показ страницы; строка на каждый —
        это тысяча одинаковых записей за час и таблица, в которой ничего не найти.
        """
        synced.offline()
        for _ in range(3):
            _guards_clear()
            await dating_site_sync_now()

        rows = [row for row in await dating_error_rows()
                if row['source'] == DATING_ERROR_SITES_SYNC]

        assert len(rows) == 1, f'строк отказа: {len(rows)}'
        assert rows[0]['hits'] >= 3, 'повторы не считаются'

    async def test_success_clears_the_error(self, synced):
        """Удачная сверка стирает отказ: строка означает «сломано прямо сейчас», и
        оставь мы её после починки — странице перестали бы верить."""
        synced.offline()
        _guards_clear()
        await dating_site_sync_now()

        synced._offline = ''
        _guards_clear()
        await dating_site_sync_now()

        assert not [row for row in await dating_error_rows()
                    if row['source'] == DATING_ERROR_SITES_SYNC]

    async def test_settings_page_does_not_502(self, synced):
        """Страница настроек тоже переживает: 502 отсюда больше не приходит."""
        synced.offline()
        _guards_clear()

        answer = await dating_site.dating_site_list(user=None)

        assert len(answer['result']['sites']) == 2


class TestSyncGuards:
    """Сверка заводится не на каждый чих: страниц в разделе пять."""

    async def test_second_page_does_not_start_a_second_sync(self, synced):
        """Открыли две страницы подряд — сверка одна.

        Без сторожа лежащий сосед получал бы запрос на каждое открытие любой
        страницы раздела.
        """
        _guards_clear()
        synced.calls.clear()

        assert dating_site_sync_start() is True
        assert dating_site_sync_start() is True, 'вторая должна увидеть первую, а не завести свою'

        # Дожидаемся, а не гасим: проверяем, сколько запросов ушло соседу.
        await dating_site._sync_task

        # Один вопрос соседу на две страницы — ради этого сторож и стоит.
        asks = [call for call in synced.calls if '/api/browser/site' in call[1]]

        assert len(asks) == 1, f'сверок завелось: {len(asks)}'

    async def test_interval_holds_between_syncs(self, synced):
        """После сверки следующая — не раньше выдержки: иначе список сайтов
        спрашивался бы у соседа на каждый щелчок по меню."""
        _guards_clear()
        await dating_site_sync_now()
        dating_site._sync_at[0] = __import__('time').time()

        assert dating_site_sync_start() is False
