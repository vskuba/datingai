"""Уборка за сюитой знает всё, что за анкетой не уходит само.

Строки, заведённые тестом, убираются двумя дорогами. Двенадцать таблиц уходят
**каскадом** — `ON DELETE CASCADE` на `profile`, и достаточно снести анкету.
Остальные приходится чистить руками, и список таких живёт в `conftest.py`.

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
import pytest

from mysql_.mysql_ import mysql_get_db_async

from tests.conftest import (TEST_STAND_SITE_FROM, TEST_SWEEP_BY_PROFILE,
                            TEST_SWEEP_ORPHANS)

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

    async def test_nothing_of_ours_is_left_behind(self):
        """После уборки в стендовых таблицах не остаётся ничьих строк.

        ⚠ Проверка идёт по **текущему** состоянию базы: уборка шла до прогона
        (`cleanup_test_profiles`), и к этому мигу ничьего быть не должно. Если
        строки есть — либо условие уборки не покрывает их вид, либо что-то
        завело их уже после неё.
        """
        left = {}
        async with mysql_get_db_async() as db:
            for table in TEST_SWEEP_ORPHANS:
                await db.execute(
                    f'SELECT COUNT(*) AS n FROM `{table}`'
                    f' WHERE `site_id` >= %s'
                    f'   AND (`profile_id` IS NULL'
                    f'        OR `profile_id` NOT IN (SELECT `id` FROM `profile`))',
                    (TEST_STAND_SITE_FROM,))
                found = int(((await db.fetchone()) or {}).get('n') or 0)
                if found:
                    left[table] = found

        assert not left, \
            f'после уборки остались ничьи строки сюиты: {left}'
