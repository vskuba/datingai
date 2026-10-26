"""Уборка за сюитой знает всё, что за анкетой не уходит само.

Строки, заведённые тестом, убираются двумя дорогами. Двенадцать таблиц уходят
**каскадом** — `ON DELETE CASCADE` на `profile`, и достаточно снести анкету.
Остальные приходится чистить руками, и список таких живёт в `conftest.py`.

⚠⚠ Руками — это **три прохода, и они о разных видах строки**: по живым анкетам
сюиты, по пережившим свою анкету (номер в строке больше ни на что не указывает) и
по стендовому сайту — там, где `profile_id` в таблице нет вовсе. Третий вид
двумя первыми не ловится ни при каком состоянии базы.

## ⚠⚠ Чем это кончилось, пока список вёлся на глаз

В нём было две таблицы из четырёх. `favorite` копила мусор прогон за прогоном —
**825 строк** от сюиты за две недели, 85% всей таблицы; `stat_day` — 746;
`scenario_run` — 1518. Сюита при этом была зелёной: тесты проверяют поведение, а
не чистоту базы после себя.

Узнать об этом можно было только одним способом — спросить **схему**: у каких
таблиц есть `profile_id` и чем он кончается при удалении анкеты. Этим сторожа
здесь и занимаются: список сверяется не с другим списком, а с базой.

⚠ Поэтому тесты ходят в `INFORMATION_SCHEMA`, а не в заранее записанный набор
имён: второй набор разошёлся бы с первым ровно так же.
"""
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from tests.conftest import (ADMIN_USERNAME, OPERATOR_USERNAME,
                            TEST_STAND_SITE_FROM, TEST_SWEEP_BY_PROFILE,
                            TEST_SWEEP_BY_SITE, TEST_SWEEP_ORPHANS,
                            TEST_SWEEP_SEEDED_TABLE, _STAND_SITES)

pytestmark = pytest.mark.core

# Таблица, которую сюита не трогает вовсе: журнал внешних запросов пишет живое
# приложение, и за этим следит отдельный сторож (`no_test_rows_in_request_log`).
SWEEP_NOT_OURS = ('request',)


async def _keyless() -> list:
    """Таблицы с `profile_id`, которые за анкетой сами не уходят.

    Returns:
        Имена таблиц, у которых ключа на `profile` нет вовсе либо он гасится в
        `NULL`: каскад их не уносит, и чистить приходится самим.
    """
    async with mysql_get_db_async() as db:
        await db.execute("""
            SELECT c.TABLE_NAME AS name,
                   (SELECT r.DELETE_RULE
                      FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS r
                      JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
                        ON k.CONSTRAINT_NAME = r.CONSTRAINT_NAME
                       AND k.TABLE_SCHEMA = r.CONSTRAINT_SCHEMA
                     WHERE k.TABLE_NAME = c.TABLE_NAME
                       AND k.REFERENCED_TABLE_NAME = 'profile'
                     LIMIT 1) AS rule_
              FROM INFORMATION_SCHEMA.COLUMNS c
             WHERE c.TABLE_SCHEMA = DATABASE()
               AND c.COLUMN_NAME = 'profile_id'
             ORDER BY c.TABLE_NAME""")
        rows = await db.fetchall() or []

    return [str(one['name']) for one in rows
            if str(one['name']) != 'profile'
            and str(one['name']) not in SWEEP_NOT_OURS
            and str(one['rule_'] or '') != 'CASCADE']


class TestTheSweepMatchesTheSchema:
    """⚠⚠ Список таблиц сверяется со схемой, а не с другим списком."""

    async def test_every_keyless_table_is_swept(self):
        """Таблица, которую каскад не уносит, обязана быть в уборке.

        ⚠ Красный здесь означает не «тест устарел», а «новая таблица копит
        мусор молча»: её строки переживут анкету и останутся в базе навсегда.
        """
        missed = [one for one in await _keyless()
                  if one not in TEST_SWEEP_BY_PROFILE]

        assert not missed, (
            f'таблицы {", ".join(missed)} за анкетой не уходят и в уборке их '
            f'нет — строки останутся в базе навсегда; добавьте в '
            f'`TEST_SWEEP_BY_PROFILE`')

    async def test_the_sweep_lists_no_ghosts(self):
        """И обратное: в списке нет таблиц, которых уборка не касается.

        ⚠ Лишнее имя — не безобидность: оно уезжает в `DELETE` текстом, и
        опечатка в нём уронит уборку целиком, то есть прогон начнётся с отказа.
        """
        known = set(await _keyless())
        extra = [one for one in TEST_SWEEP_BY_PROFILE if one not in known]

        assert not extra, (
            f'в уборке числятся {", ".join(extra)}, а каскад их уносит сам '
            f'(или столбца `profile_id` у них нет)')


class TestOrphansAreSweptToo:
    """⚠⚠ Строка переживает свою анкету — и уборка по номеру её не находит."""

    async def test_orphan_tables_have_a_site(self):
        """Второй проход ищет по сайту, значит `site_id` у таблицы обязан быть.

        ⚠ `gallery_file` его не знает, и в `TEST_SWEEP_ORPHANS` её нет
        намеренно: её строки заводят четыре теста галереи, и анкету они сносят
        сами, последней.
        """
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT TABLE_NAME AS name FROM INFORMATION_SCHEMA.COLUMNS'
                " WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'site_id'")
            with_site = {str(one['name']) for one in await db.fetchall() or []}

        missed = [one for one in TEST_SWEEP_ORPHANS if one not in with_site]

        assert not missed, \
            f'у {", ".join(missed)} нет `site_id` — второй проход их не найдёт'

    async def test_an_orphan_row_is_swept(self):
        """Осиротевшую строку уборка находит и уносит.

        ⚠⚠ Проверяется **механика**, а не состояние базы. Прежде тест смотрел,
        пусто ли в таблицах сейчас, — и в полном прогоне падал на живой работе
        соседних тестов: 18 строк `scenario_run`, которые завели идущие рядом
        проверки, он считал пропущенным мусором. Ложная тревога на исправной
        уборке хуже отсутствия теста: ей перестают верить.

        Теперь строка заводится здесь, с заведомо чужим номером анкеты, и тест
        убеждается, что проход её уносит. От порядка тестов это не зависит.
        """
        from tests.conftest import _test_orphans_drop

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO scenario_run (site_id, profile_id, scenario_id,'
                ' scenario_name, status) VALUES (%s, NULL, 0, %s, %s)',
                (TEST_STAND_SITE_FROM + 7, 'проба уборки', 'error'))
            await db.connection.commit()
            made = int(db.lastrowid)

        _test_orphans_drop()

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM scenario_run WHERE id = %s',
                             (made,))
            left = int(((await db.fetchone()) or {}).get('n') or 0)

        assert left == 0, 'осиротевшая строка осталась: второй проход её не видит'


async def _site_only() -> list:
    """Таблицы, у которых есть `site_id` и нет `profile_id` вовсе.

    Returns:
        Имена таблиц, которые ни один проход по номеру анкеты не находит: её
        номера в них не существует.
    """
    async with mysql_get_db_async() as db:
        await db.execute("""
            SELECT TABLE_NAME AS name,
                   MAX(COLUMN_NAME = 'site_id')    AS has_site,
                   MAX(COLUMN_NAME = 'profile_id') AS has_profile
              FROM INFORMATION_SCHEMA.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE()
             GROUP BY TABLE_NAME
            HAVING has_site = 1 AND has_profile = 0
             ORDER BY TABLE_NAME""")
        rows = await db.fetchall() or []

    return [str(one['name']) for one in rows
            if str(one['name']) not in SWEEP_NOT_OURS]


class TestTablesWithoutAProfileAreSweptByTheirSite:
    """⚠⚠ Третий вид строки: анкеты в таблице нет **вовсе**.

    Два прохода выше стоят на номере анкеты — по живым и по пережившим свою. У
    этих таблиц номера анкеты не существует, и оба прохода их не видели ни при
    каком состоянии базы: `companion` накопила 309 строк сюиты из 1343.
    """

    async def test_every_site_only_table_is_swept(self):
        """Таблица с `site_id` и без `profile_id` обязана быть в третьем проходе.

        ⚠ Красный здесь означает «новая таблица копит мусор молча»: её строки не
        привязаны ни к анкете, ни к каскаду, и убрать их нечем.
        """
        missed = [one for one in await _site_only()
                  if one not in TEST_SWEEP_BY_SITE]

        assert not missed, (
            f'у таблиц {", ".join(missed)} нет `profile_id`, и уборка по анкете '
            f'их не найдёт никогда — добавьте в `TEST_SWEEP_BY_SITE`')

    async def test_the_third_pass_lists_no_ghosts(self):
        """И обратное: лишнее имя уезжает в `DELETE` текстом.

        ⚠ Опечатка в нём уронит уборку целиком, то есть прогон начнётся с
        отказа — а уборка идёт **до** прогона.
        """
        known = set(await _site_only())
        extra = [one for one in TEST_SWEEP_BY_SITE if one not in known]

        assert not extra, (
            f'в третьем проходе числятся {", ".join(extra)}, а `profile_id` у '
            f'них есть (или `site_id` нет) — их убирают проходы по анкете')

    async def test_a_row_without_a_profile_is_swept(self):
        """Строку без номера анкеты третий проход находит по стендовому сайту.

        ⚠⚠ Проверяется **механика**, а не состояние базы — по той же причине,
        что и у прохода выше: в полном прогоне соседние тесты заводят свои
        `companion` и `error`, и тест считал их пропущенным мусором.
        """
        from tests.conftest import _test_site_rows_drop

        mark = TEST_STAND_SITE_FROM + 7

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO companion (site_id, uid, name) VALUES (%s, %s, %s)',
                (mark, 'проба-уборки', 'Проба'))
            await db.connection.commit()

        _test_site_rows_drop()

        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM companion WHERE site_id = %s', (mark,))
            left = int(((await db.fetchone()) or {}).get('n') or 0)

        assert left == 0, 'строка без анкеты осталась: третий проход её не видит'

    async def test_the_seeded_stand_site_survives(self):
        """⚠⚠ А вот посевную строку `site` уборка щадить обязана.

        На ней стоит заведение анкет: номер сайта должен признаваться
        заведённым. Снеси её — и соседний прогон, уже решивший, что сеять нечего
        (`_site_seeded`), получал бы отказ на каждой анкете.
        """
        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM `site` WHERE `site_id` = %s',
                             (_STAND_SITES[0][0],))
            found = int(((await db.fetchone()) or {}).get('n') or 0)

        assert found == 1, 'посевная строка стендового сайта не на месте'

    async def test_a_live_synced_site_survives(self):
        """⚠⚠ Строка настоящего сайта из тестового ряда обязана пережить уборку.

        Тем и кончился прогон ночи 26.10.2026: автонумерация соседнего
        `browser_site` дошла до `999344`, и уборка снесла живую закладку вместе
        с настройками сайта — кнопка «Обновить с сайта» умерла молча. Примета
        живой строки не номер, а синк: `synced_at` у неё есть, у придуманного
        тестом сайта строки в `site` нет вовсе. Пробу заводим свои номерами и
        ими же убираем — под защитой синка обычный проход их не возьмёт.
        """
        from tests.conftest import _test_site_rows_drop

        mark = TEST_STAND_SITE_FROM + 8

        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM companion WHERE `site_id` = %s', (mark,))
            await db.execute('DELETE FROM `site` WHERE `site_id` = %s', (mark,))
            await db.execute(
                'INSERT INTO `site` (site_id, name, url, is_gone, synced_at)'
                ' VALUES (%s, %s, %s, 0, NOW())', (mark, 'проба-синка',
                                                   'http://stand.invalid'))
            await db.execute(
                'INSERT INTO companion (site_id, uid, name) VALUES (%s, %s, %s)',
                (mark, 'проба-живая', 'Проба'))
            await db.connection.commit()

        try:
            _test_site_rows_drop()

            async with mysql_get_db_async() as db:
                await db.execute('SELECT COUNT(*) AS n FROM `site`'
                                 ' WHERE `site_id` = %s', (mark,))
                site_left = int(((await db.fetchone()) or {}).get('n') or 0)
                await db.execute('SELECT COUNT(*) AS n FROM companion'
                                 ' WHERE `site_id` = %s', (mark,))
                rows_left = int(((await db.fetchone()) or {}).get('n') or 0)
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM companion WHERE `site_id` = %s',
                                 (mark,))
                await db.execute('DELETE FROM `site` WHERE `site_id` = %s',
                                 (mark,))
                await db.connection.commit()

        assert site_left == 1, 'синкованная строка `site` не пережила уборку'
        assert rows_left == 1, 'строка живого сайта в `companion` не пережила уборку'


class TestPanelUsersAreSweptByName:
    """⚠⚠ Четвёртый вид строки: человек панели. Ни анкеты, ни сайта у него нет.

    Три прохода выше отбирают по номеру анкеты и по стендовому сайту — учётку
    панели они не видят ни при каком состоянии базы. Без этого прохода
    накопилась одна учётка от 15.09, и копилась бы навсегда: каскада на людей
    нет, а прогон, упавший до своей уборки, оставляет их в списке рядом с живыми.
    """

    async def test_a_suite_user_is_swept(self):
        """Учётку с именем сюиты проход находит и уносит."""
        from tests.conftest import _test_users_drop

        name = f'test-{uuid.uuid4().hex[:10]}'
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO `user` (username, password_hash, role)'
                " VALUES (%s, 'x', 'operator')", (name,))
            await db.connection.commit()

        _test_users_drop()

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM `user` WHERE username = %s',
                             (name,))
            left = int(((await db.fetchone()) or {}).get('n') or 0)

        assert left == 0, 'учётка сюиты осталась: четвёртый проход её не видит'

    async def test_a_human_user_survives(self):
        """⚠⚠ Живые учётки проход щадит, и это важнее, чем уборка.

        Примета здесь — **имя**, а человек заводит учётки теми же буквами. Если
        условие однажды расширят до `test-%`, упадёт этот тест, а не доверие к
        панели: учётка с именем «test-отчёт» снеслась бы молча.
        """
        from tests.conftest import _test_users_drop

        kept = f'test-отчёт-{uuid.uuid4().hex[:4]}'
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO `user` (username, password_hash, role)'
                " VALUES (%s, 'x', 'operator')", (kept,))
            await db.connection.commit()

        _test_users_drop()

        async with mysql_get_db_async() as db:
            await db.execute('SELECT username FROM `user` WHERE username IN'
                             ' (%s, %s, %s)',
                             (kept, ADMIN_USERNAME, OPERATOR_USERNAME))
            alive = {str(one['username']) for one in await db.fetchall() or []}

            await db.execute('DELETE FROM `user` WHERE username = %s', (kept,))
            await db.connection.commit()

        assert kept in alive, 'уборка снесла учётку, похожую на тестовую'
        assert ADMIN_USERNAME in alive, 'уборка снесла администратора'
