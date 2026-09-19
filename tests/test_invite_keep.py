"""`invite_keep` — сколько инвайтов пары показывать в диалоге.

## ⚠⚠ Настройка больше ничего не удаляет

Прежде она подрезала историю пары: лишние строки стирались, как только в пул
ложился новый инвайт. Файл целиком описывал ту подрезку — теперь описывает показ.

Правило переменилось по существу дела. **Инвайт живёт у собеседника около часа**:
ответить на вчерашний он не может физически, и хранить историю «на случай ответа»
незачем. Удаление в таблице поэтому осталось ровно одно — по сроку
(`invite_clean_hours`), и два правила на «сколько строк живёт» больше не спорят
друг с другом.

⚠ Показ при этом никуда не делся: собеседник ответил — в диалог уезжают последние
`invite_keep` **отправленных** приглашений, чтобы автоответ видел, на что он
отвечает (`test_invite_to_history.py`).
"""

import inspect
import pathlib
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'keep-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM invite WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _invite(profile_id: int, uid: str, body: str,
                  hours_ago: int = 0) -> int:
    """Ушедший инвайт. Время считает база: MySQL живёт по UTC."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO invite (site_id, profile_id, uid, name, body, status,'
            '  sent_at, created_at)'
            " VALUES (%s, %s, %s, 'Brian', %s, 'sent',"
            '  NOW() - INTERVAL %s HOUR, NOW() - INTERVAL %s HOUR)',
            (STAND_SITE, int(profile_id), uid, body, int(hours_ago),
             int(hours_ago)))
        await db.connection.commit()

        return int(db.lastrowid)


class TestNothingIsDeletedByCount:
    """⚠⚠ Главное: укладка нового инвайта больше не стирает старые."""

    def test_the_trimmer_is_gone(self):
        """Функции подрезки нет вовсе — не просто не зовётся."""
        from src.dating import invite

        assert not hasattr(invite, '_keep_last'), \
            'подрезка вернулась — теперь правил удаления снова два'

    def test_the_insert_does_not_trim(self):
        """⚠ Укладка в пул делает ровно одно: кладёт строку."""
        from src.dating.invite import _put

        code = inspect.getsource(_put)

        assert 'keep' not in code, 'в укладку вернулось число строк'
        assert 'DELETE' not in code.upper()

    async def test_five_invites_stay_five(self, girl):
        """Пять приглашений паре — и все пять в базе, сколько бы ни просил показ."""
        for n in range(5):
            await _invite(girl, 'u-1', f'реплика {n}', hours_ago=n)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM invite'
                             ' WHERE profile_id = %s AND uid = %s', (girl, 'u-1'))
            row = await db.fetchone()

        assert int(row['n']) == 5


class TestOnlyAgeDeletes:
    """⚠ Удаление в таблице ровно одно — по сроку."""

    def test_the_round_deletes_by_age(self):
        from src.dating.invite_round import _clean

        code = inspect.getsource(_clean)

        assert 'INTERVAL %s HOUR' in code
        assert 'invite_clean_hours' in code or 'INVITE_CLEAN_SETTING' in code

    def test_no_other_deletes_in_the_maker(self):
        """⚠⚠ В наборе пула удалений нет вовсе.

        Два правила на «сколько строк живёт» спорили бы друг с другом, а человек
        не знал бы, какое сработало.
        """
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / 'src/dating/invite.py').read_text(encoding='utf-8')

        # `DELETE` в модуле набора остаться не должно: чистку пула по кнопке ведёт
        # роутер, а по сроку — круг.
        assert 'DELETE FROM invite' not in source


class TestItStillFeedsTheDialog:
    """Показ работает: ответил — увидел столько, сколько просит настройка."""

    async def test_the_last_ones_go_to_the_dialog(self, girl):
        from src.dating.chat import chat_invites_to_history

        for n in range(5):
            await _invite(girl, 'u-1', f'реплика {n}', hours_ago=5 - n)

        async with mysql_get_db_async() as db:
            got = await chat_invites_to_history(db, girl, 'u-1')
            await db.connection.commit()

        # Настройки у стенда нет — берём умолчание реестра, два приглашения.
        assert got == 2

        async with mysql_get_db_async() as db:
            await db.execute(
                "SELECT text FROM message WHERE profile_id = %s AND source = 'invite'"
                ' ORDER BY created_at', (girl,))
            rows = await db.fetchall() or []

        assert [row['text'] for row in rows] == ['реплика 3', 'реплика 4'], \
            'в диалог уехали не последние приглашения'

    async def test_an_empty_history_shows_nothing(self, girl):
        """⚠ Ноль строк — ноль показа, и это обычный случай: он сам начал разговор."""
        from src.dating.chat import chat_invites_to_history

        async with mysql_get_db_async() as db:
            got = await chat_invites_to_history(db, girl, 'u-new')

        assert got == 0

    async def test_drafts_are_not_shown(self, girl):
        """⚠ Черновик человеку не уходил — в переписке ему места нет."""
        from src.dating.chat import chat_invites_to_history

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO invite (site_id, profile_id, uid, name, body, status)'
                " VALUES (%s, %s, 'u-1', 'Brian', 'лежит в пуле', 'draft')",
                (STAND_SITE, girl))
            await db.connection.commit()

            got = await chat_invites_to_history(db, girl, 'u-1')

        assert got == 0


class TestTheSetting:
    """Реестр настроек."""

    async def test_it_is_registered(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}
        one = known['invite_keep']

        assert one['default'] == '2'
        assert one['title'] == 'Сколько последних инвайтов показывать в диалоге'

    async def test_the_note_says_it_does_not_hide(self, auth_client):
        """⚠⚠ Название говорит «показывать», и подсказка обязана уточнить, что
        лишние **не прячутся**: они просто не попадают в диалог, а живут в базе до
        своего срока."""
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'удал' in known['invite_keep']['note'].lower()
