"""История инвайтов пары подрезается: остаются последние `invite_keep`.

## ⚠⚠ Зачем

Инвайты хранятся, чтобы в диалоге понимать, **на что** ответил человек. Но одному
человеку пишут в разные дни, и за месяц у пары «девушка — собеседник» набегает
под сотню строк, а отвечает он на одну-две последние. Остальные — мёртвый груз,
который и мешает ответить на тот самый вопрос: по сотне приглашений не понять,
какое из них он прочитал.

## ⚠⚠ Строки за последние сутки не трогаются никогда

По ним считается дневной потолок повторов — «не звать одного чаще N раз в сутки»
(`invite_repeat_max_day`, по умолчанию 10). Счётчик устроен как
`SUM(sent_at > NOW() - INTERVAL 1 DAY)`, то есть **по самим строкам**: сотри их —
и потолок поднимется молча. Человек получал бы приглашения без счёта, а в
настройке по-прежнему стояло бы «не чаще десяти».

Это и есть цена, которую нельзя платить за опрятность, и потому чистка трогает
только то, что старше суток.
"""

import uuid

import pytest
from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.profile

STAND_SITE = 999_343
UID = 'keep-777'


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'keep-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM invite WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


class TestTrim:
    """Сколько строк остаётся у пары после подрезки."""

    async def test_old_rows_are_cut_to_the_limit(self, girl):
        from src.dating.invite import _keep_last

        await _rows(girl, old=5)

        gone = await _keep_last(girl, UID, 2)

        assert gone == 3, f'убрано {gone}, а лишних было три'
        assert await _count(girl) == 2

    async def test_the_newest_survive(self, girl):
        """⚠ Остаются **последние**, а не первые попавшиеся."""
        from src.dating.invite import _keep_last

        made = await _rows(girl, old=4)
        await _keep_last(girl, UID, 2)

        assert await _ids(girl) == sorted(made)[-2:]

    async def test_one_is_a_legal_setting(self, girl):
        from src.dating.invite import _keep_last

        await _rows(girl, old=3)
        await _keep_last(girl, UID, 1)

        assert await _count(girl) == 1

    async def test_nothing_to_cut_is_not_an_error(self, girl):
        from src.dating.invite import _keep_last

        await _rows(girl, old=1)

        assert await _keep_last(girl, UID, 2) == 0
        assert await _count(girl) == 1


class TestTodayIsUntouchable:
    """⚠⚠ Сутки неприкосновенны: на них стоит дневной потолок повторов."""

    async def test_todays_rows_survive_even_above_the_limit(self, girl):
        from src.dating.invite import _keep_last

        await _rows(girl, fresh=6)

        assert await _keep_last(girl, UID, 2) == 0, \
            'подрезаны свежие строки — дневной потолок повторов поднимется молча'
        assert await _count(girl) == 6

    async def test_old_are_cut_while_fresh_stay(self, girl):
        """Смешанный случай: старое уходит, сегодняшнее остаётся целиком."""
        from src.dating.invite import _keep_last

        await _rows(girl, old=5, fresh=3)
        await _keep_last(girl, UID, 2)

        assert await _count(girl) == 3, 'осталось не только сегодняшнее'
        assert await _fresh_count(girl) == 3

    def test_the_query_names_the_day(self):
        """Условие суток — в самом запросе, а не в вызывающем коде."""
        import inspect

        from src.dating.invite import _keep_last

        code = inspect.getsource(_keep_last)

        assert 'INTERVAL 1 DAY' in code, \
            'из чистки пропало правило «сутки не трогаем»'


class TestItRunsOnInsert:
    """Подрезка идёт сразу после вставки — так и просил заказчик."""

    def test_the_insert_trims(self):
        import inspect

        from src.dating.invite import _put

        assert '_keep_last(' in inspect.getsource(_put)

    def test_the_setting_is_read_once_per_pass(self):
        """⚠ Настройка читается раз на заход, а не на карточку.

        Пул набирается десятками; полсотни одинаковых чтений — работа впустую.
        """
        import inspect

        from src.dating.invite import invite_make

        code = inspect.getsource(invite_make)

        assert 'keep = await _keep(site_id)' in code
        assert code.index('keep = await _keep') < code.index('for girl, person in')

    async def test_the_setting_is_registered(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'invite_keep' in known
        assert known['invite_keep']['default'] == '2'

    async def test_a_broken_database_does_not_stop_the_pool(self, girl, monkeypatch):
        """⚠ Уборка не имеет права сорвать набор: инвайт уже лёг.

        Падать из-за необрезанного хвоста значило бы терять работу ради
        опрятности.
        """
        from src.dating import invite

        def _broken():
            raise RuntimeError('база отвалилась')

        monkeypatch.setattr('mysql_.mysql_.mysql_get_db_async', _broken)

        assert await invite._keep_last(girl, UID, 2) == 0


async def _rows(profile_id: int, old: int = 0, fresh: int = 0) -> list[int]:
    """Кладёт истории пары `old` строк старше суток и `fresh` сегодняшних."""
    made = []
    async with mysql_get_db_async() as db:
        for number in range(old + fresh):
            await db.execute(
                'INSERT INTO invite'
                ' (site_id, profile_id, uid, name, body, status, created_at)'
                " VALUES (%s, %s, %s, 'Кто-то', 'Hi!', 'sent',"
                '         NOW() - INTERVAL %s DAY)',
                (STAND_SITE, int(profile_id), UID, 3 if number < old else 0))
            made.append(int(db.lastrowid))
        await db.connection.commit()

    return made


async def _count(profile_id: int) -> int:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM invite'
                         ' WHERE profile_id = %s AND uid = %s', (int(profile_id), UID))

        return int(((await db.fetchone()) or {}).get('n') or 0)


async def _fresh_count(profile_id: int) -> int:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM invite'
                         ' WHERE profile_id = %s AND uid = %s'
                         '   AND created_at > NOW() - INTERVAL 1 DAY',
                         (int(profile_id), UID))

        return int(((await db.fetchone()) or {}).get('n') or 0)


async def _ids(profile_id: int) -> list[int]:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id FROM invite'
                         ' WHERE profile_id = %s AND uid = %s ORDER BY id',
                         (int(profile_id), UID))

        return [int(row['id']) for row in await db.fetchall() or []]
