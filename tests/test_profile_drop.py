"""Удаление анкеты уносит то, что каскад не уносит.

## ⚠⚠⚠ Что здесь стережётся

У `profile` есть внешние ключи с каскадом, и переписка, пулы, журнал уходят за
анкетой сами. А у семи таблиц ключа **нет вовсе** — только колонка
`profile_id`, — и их строки анкету переживают: номер в них после этого ни на
что не указывает.

Замер 03.11.2026 на данных сюиты: `intention_mail` накопила 166 таких строк,
`gallery_file` оставляла по две за прогон и трижды роняла разбор галереи. На
проде их ноль — но лишь потому, что анкеты в панели **не удаляют**.

## ⚠⚠ Список сверяется со схемой, а не с другим списком

Ровно та же грабля уже стоила сюите 825 строк в `favorite`: список таблиц
разошёлся со схемой, и расхождение молчало. Поэтому главный тест здесь
спрашивает **базу** — у кого есть `profile_id` и чем он кончается при удалении
анкеты, — а не сравнивает две рукописи.
"""

import uuid

import pytest
from mysql_.mysql_ import mysql_get_db_async

from src.dating.profile_drop import (PROFILE_DROP_KEEP, PROFILE_DROP_TABLES,
                                     profile_drop)

pytestmark = pytest.mark.profile


async def _keyless() -> set:
    """Таблицы с `profile_id`, которые каскад за анкетой не уносит.

    ⚠ Спрашивается **схема**: какие таблицы знают номер анкеты и у каких из них
    нет внешнего ключа с `CASCADE`. Второй список имён рядом разошёлся бы с
    первым молча — это уже случалось у уборки сюиты.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT c.TABLE_NAME AS name FROM INFORMATION_SCHEMA.COLUMNS c'
            " WHERE c.TABLE_SCHEMA = DATABASE() AND c.COLUMN_NAME = 'profile_id'"
            "   AND c.TABLE_NAME <> 'profile'"
            '   AND NOT EXISTS ('
            '       SELECT 1 FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS r'
            '       JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k'
            '         ON k.CONSTRAINT_SCHEMA = r.CONSTRAINT_SCHEMA'
            '        AND k.CONSTRAINT_NAME = r.CONSTRAINT_NAME'
            '      WHERE r.CONSTRAINT_SCHEMA = c.TABLE_SCHEMA'
            '        AND k.TABLE_NAME = c.TABLE_NAME'
            "        AND k.COLUMN_NAME = 'profile_id'"
            "        AND r.REFERENCED_TABLE_NAME = 'profile'"
            "        AND r.DELETE_RULE = 'CASCADE')")

        return {str(one['name']) for one in await db.fetchall() or []}


class TestTheListMatchesTheSchema:
    """⚠⚠ Список обязан совпадать со схемой в обе стороны."""

    async def test_every_keyless_table_is_dropped(self):
        """Таблица, которую каскад не уносит, обязана быть в списке.

        ⚠ Красный здесь значит не «тест устарел», а «новая таблица копит
        мусор молча»: её строки переживут анкету и останутся навсегда.
        """
        missed = [one for one in await _keyless()
                  if one not in PROFILE_DROP_TABLES
                  and one not in PROFILE_DROP_KEEP]

        assert not missed, (
            f'{", ".join(missed)} за анкетой не уходят и в `PROFILE_DROP_TABLES` '
            f'их нет — строки останутся в базе ничьими навсегда')

    async def test_the_list_has_no_ghosts(self):
        """И обратное: лишнее имя уезжает в `DELETE` текстом.

        Опечатка в нём уронит удаление анкеты целиком — то есть кнопка в
        панели перестанет работать.
        """
        known = await _keyless()
        extra = [one for one in PROFILE_DROP_TABLES if one not in known]

        assert not extra, (
            f'в списке числятся {", ".join(extra)}, а каскад их уносит сам '
            f'(или столбца `profile_id` у них нет)')

    async def test_the_journals_survive_on_purpose(self):
        """⚠⚠ Два журнала переживают анкету намеренно, и это записано списком.

        `scenario_run` отвечает на «что вообще происходило», `request` — на
        «куда мы ходили и что ответили». Снеси их вместе с анкетой, и
        расследование вчерашнего отказа лишилось бы ровно тех строк, ради
        которых его и ведут.

        ⚠ Список нужен затем, что соседний тест сверяет снос со схемой: без
        него он требовал бы снести и журналы, а молчаливое отсутствие в первом
        списке неотличимо от забывчивости.
        """
        assert PROFILE_DROP_KEEP == ('scenario_run', 'request')

        both = set(PROFILE_DROP_TABLES) & set(PROFILE_DROP_KEEP)

        assert not both, f'{both} числятся и в сносе, и в пощаде'


class TestNothingSurvivesTheDeletion:
    """⚠⚠⚠ Главное: после удаления ничьих строк не остаётся."""

    async def test_a_keyless_row_goes_with_the_profile(self, girl):
        """Строка галереи — та самая, на которой это и поймали."""
        file_id = f'drive-{uuid.uuid4().hex[:10]}'

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO gallery_file (profile_id, file_id, path, name,'
                " mime, kind, is_gone) VALUES (%s, %s, 'public/a.jpg', 'a',"
                " 'image/jpeg', 'image', 0)", (int(girl), file_id))
            await db.connection.commit()

        got = await profile_drop(int(girl))

        assert got['profile'] == 1, 'анкета не удалилась'
        assert got.get('gallery_file') == 1, 'строка галереи не попала в уборку'

        async with mysql_get_db_async() as db:
            await db.execute('SELECT id FROM gallery_file WHERE file_id = %s',
                             (file_id,))

            assert await db.fetchone() is None, 'строка пережила анкету'

    async def test_no_orphans_are_left_anywhere(self, girl):
        """⚠ Проверяется **весь** список разом, а не одна таблица.

        Своя проверка на каждую разошлась бы со списком при первом же
        добавлении — ровно так, как разошлась уборка сюиты.
        """
        profile_id = int(girl)

        await profile_drop(profile_id)

        async with mysql_get_db_async() as db:
            for table in PROFILE_DROP_TABLES:
                await db.execute(
                    f'SELECT COUNT(*) AS n FROM `{table}`'
                    ' WHERE profile_id = %s', (profile_id,))
                left = int((await db.fetchone())['n'])

                assert left == 0, f'{table}: осталось {left} ничьих строк'

    async def test_deleting_twice_is_quiet(self, girl):
        """⚠ Второй заход ничего не ломает: человек нажал кнопку дважды."""
        profile_id = int(girl)

        await profile_drop(profile_id)
        again = await profile_drop(profile_id)

        assert again['profile'] == 0, 'второе удаление что-то нашло'

    async def test_a_neighbour_profile_is_not_touched(self, girl, girl_make):
        """⚠⚠ Сносится **эта** анкета, а не все строки таблицы.

        Условие по номеру тут единственное, что отделяет её от соседок, и
        забытое `WHERE` выглядело бы зелёным на тесте с одной анкетой.
        """
        other = await girl_make()

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO gallery_file (profile_id, file_id, path, name,'
                " mime, kind, is_gone) VALUES (%s, %s, 'public/a.jpg', 'a',"
                " 'image/jpeg', 'image', 0)",
                (int(other), f'drive-{uuid.uuid4().hex[:10]}'))
            await db.connection.commit()

        await profile_drop(int(girl))

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM gallery_file'
                             ' WHERE profile_id = %s', (int(other),))

            assert int((await db.fetchone())['n']) == 1, \
                'удаление одной анкеты унесло строки соседней'


class TestTheHandleUsesTheDoor:
    """⚠⚠ Ручка панели ходит той же дверью, а не репозиторием."""

    def test_the_route_calls_the_door(self):
        import inspect

        import src.uvicorn.routers.dating.profile_ as mod

        body = inspect.getsource(mod.profile_delete)

        assert 'profile_drop' in body, \
            'ручка снова сносит анкету репозиторием — каскад уносит не всё'
        assert 'DatingProfileRepository().delete' not in body
