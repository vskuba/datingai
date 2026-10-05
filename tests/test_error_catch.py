"""Застава журнала: падение процесса попадает в таблицу отказов само.

До неё `logger_exception` стоял в пяти местах — middleware ошибок сервера, три
кронджоба, страховка смены, — и его записи не попадали **никуда**, кроме
`data/log/app.log` внутри контейнера. То есть упавший ночью кронджоб узнавался
от человека со словами «что-то не работает».

Все прочие источники этой таблицы предметные: код поймал отказ и сам о нём
сказал. Так наполняют журнал ровно до первой забытой ветки — пишущий код занят
своей работой, а строчку про журнал в нём надо помнить.

⚠ Проверки здесь зовут обработчики **в процессе**: застава подвешивается к
корневому логгеру, и подвесить её в панели, живущей в контейнере, снаружи
нельзя.
"""
import logging
import uuid

import pytest

from journal_.journal_catch import journal_catch_remove
from mysql_.mysql_ import mysql_get_db_async

from src.dating.error import (ERROR_CRASH_PREFIX, error_catch_install,
                              error_crash_trim, error_rows, error_title,
                              error_write)

pytestmark = pytest.mark.core

# Примета строк этого файла: по ней они и убираются.
CATCH_MARK = 'проба-заставы'


@pytest.fixture(autouse=True)
async def catch():
    """Застава на время теста — и чистая таблица после.

    ⚠ Снимаем **всегда**: обработчик висит на корневом логгере процесса, и
    оставленный он писал бы ошибки соседних тестов в таблицу до конца прогона.
    """
    error_catch_install('cronjob')

    yield

    journal_catch_remove()

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM error WHERE message LIKE %s',
                         (f'%{CATCH_MARK}%',))
        await db.connection.commit()


async def _crashes() -> list:
    """Строки сбоев — только свои, с приметой этого файла."""
    return [one for one in await error_rows()
            if one['source'].startswith(ERROR_CRASH_PREFIX)
            and CATCH_MARK in one['message']]


class TestAnErrorReachesTheTable:
    """⚠ Код о журнале ничего не знает — пишет за него застава."""

    async def test_logged_error_is_written(self):
        logging.getLogger('src.cronjob.проба').error(f'{CATCH_MARK}: сломалось')

        rows = await _crashes()

        assert len(rows) == 1, rows
        assert CATCH_MARK in rows[0]['message']

    async def test_a_warning_is_not(self):
        """⚠ Ниже ERROR — не ошибки: журнал бы ими затопило."""
        logging.getLogger('src.cronjob.проба').warning(f'{CATCH_MARK}: странно')

        assert await _crashes() == []

    async def test_the_place_comes_along(self):
        """⚠⚠ Место берётся из **трассировки**, а не из полей записи.

        Поля показывают, кто позвал логгер, а зовут его через обёртку общего
        слоя — и у каждой второй ошибки стояло бы одно и то же `logging_.py`.
        """
        try:
            {}[f'{CATCH_MARK}-ключ']
        except KeyError:
            logging.getLogger('src.cronjob.проба').exception(f'{CATCH_MARK}: упало')

        rows = await _crashes()

        assert len(rows) == 1
        assert 'test_error_catch.py:' in rows[0]['message'], rows[0]['message']


class TestTheSameErrorIsOneRow:
    """⚠⚠ Сломавшийся цикл даёт одну ошибку в секунду — строка на каждую это
    десятки тысяч записей за ночь и таблица, в которой ничего не найти."""

    async def test_changing_numbers_do_not_split_rows(self):
        """Номер в тексте не разводит строки: считает `journal_key` слоя."""
        log = logging.getLogger('src.cronjob.проба')
        log.error(f'{CATCH_MARK}: задача 5219 не найдена')
        log.error(f'{CATCH_MARK}: задача 5220 не найдена')

        rows = await _crashes()

        assert len(rows) == 1, [one['message'] for one in rows]
        assert rows[0]['hits'] == 2

    async def test_different_errors_split_rows(self):
        log = logging.getLogger('src.cronjob.проба')
        log.error(f'{CATCH_MARK}: база недоступна')
        log.error(f'{CATCH_MARK}: диск переполнен')

        assert len(await _crashes()) == 2


class TestTheTitleIsReadable:
    """⚠ В `source` лежит ключ с отпечатком — человеку его показывать незачем."""

    def test_a_crash_is_named_by_its_process(self):
        assert error_title('crash:worker:a1b2c3d4') == 'Сбой воркера'
        assert error_title('crash:uvicorn:ffffffff') == 'Сбой сервера'

    def test_an_unknown_process_still_reads(self):
        """Незнакомый процесс не теряется: подпись собирается из его имени."""
        assert error_title('crash:чужой:0000') == 'Сбой: чужой'

    def test_domain_failures_keep_their_titles(self):
        assert error_title('sites_sync') == 'Список сайтов'


class TestTheTrimSparesDomainFailures:
    """⚠⚠ Главное свойство чистки: она про сбои и только про них."""

    async def test_old_crashes_go(self):
        logging.getLogger('src.cronjob.проба').error(f'{CATCH_MARK}: давнее')
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE error SET last_at = NOW(3) - INTERVAL 30 DAY'
                ' WHERE message LIKE %s', (f'%{CATCH_MARK}%',))
            await db.connection.commit()

        assert await error_crash_trim(keep_days=7) >= 1
        assert await _crashes() == []

    async def test_a_domain_failure_outlives_the_trim(self):
        """⚠⚠ Предметный отказ живёт до **своей удачи**, а не по сроку.

        «Список сайтов не обновляется неделю» обязан висеть, пока не
        обновится: чистка по времени убрала бы единственное объяснение того,
        что человек видит на странице.
        """
        source = f'{CATCH_MARK}-{uuid.uuid4().hex[:8]}'
        await error_write(source, f'{CATCH_MARK}: предметный отказ')

        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE error SET last_at = NOW(3) - INTERVAL 90 DAY'
                ' WHERE source = %s', (source,))
            await db.connection.commit()

        await error_crash_trim(keep_days=7)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM error WHERE source = %s',
                             (source,))
            left = int(((await db.fetchone()) or {}).get('n') or 0)

        assert left == 1, 'чистка сбоев унесла предметный отказ'


class TestTheJournalDoesNotEatItself:
    """⚠⚠ Запись в базу сама зовёт код, который может пожаловаться в логгер."""

    async def test_the_writer_is_skipped(self):
        """Жалоба самого журнала в таблицу не идёт: иначе запись о недоступной
        базе пошла бы в ту же недоступную базу."""
        logging.getLogger('src.dating.error').error(f'{CATCH_MARK}: я сам упал')

        assert await _crashes() == []

    def test_the_catch_is_installed_once(self):
        """⚠ У uvicorn lifespan запускается заново при перезагрузке кода, и
        второй обработчик считал бы каждую ошибку дважды."""
        assert error_catch_install('uvicorn') is None, \
            'застава поставилась второй раз — ошибки задвоятся'
