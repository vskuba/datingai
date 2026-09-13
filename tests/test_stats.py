"""Статистика чата: что считается разговором.

⚠ **Написано по живой жалобе.** Вкладка показывала «входящих 6, исходящих 165,
разговоров 93» — при одном настоящем диалоге. Девяносто три это те, кого позвали
инвайтом и кто промолчал: считалось `COUNT(DISTINCT sender_uid)`, а `sender_uid` у
исходящей реплики — **адресат**, а не отправитель (столбец один на обе стороны).

Отсюда главная проверка этого файла: число разговоров в статистике и число строк
на вкладке «Диалоги» — **одно и то же число**. Разойдись они, два места об одном и
том же спорили бы на соседних вкладках, и верить нельзя было бы ни одному.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def talks(auth_client):
    """Две анкеты и четыре пары — из них разговоров только два.

    | Пара | Реплики | Разговор? |
    |------|---------|-----------|
    | #1 ↔ zz-mute | только наша | нет: позвали, он промолчал |
    | #1 ↔ zz-talk | наша и его | да |
    | #2 ↔ zz-talk | наша и его | да — **тот же человек у второй анкеты** |
    | #2 ↔ zz-mute2 | только наша | нет |

    ⚠ **Отдаёт и «сколько было до».** Стенд общий: на нём оседают строки прошлых
    прогонов — сорвавшихся, прерванных, — и «разговоров ровно два» было бы
    проверкой чистоты стенда, а не правила. Считаем **приращение**.
    """
    before = (await auth_client.get('/api/dating/stats',
                                    params={'site_id': STAND_SITE})).json()['result']['chat']

    girls = []
    for _ in range(2):
        girls.append(int((await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'stat-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']))

    async with mysql_get_db_async() as db:
        for girl, uid, sides in ((girls[0], 'zz-mute', ('out',)),
                                 (girls[0], 'zz-talk', ('out', 'in')),
                                 (girls[1], 'zz-talk', ('out', 'in')),
                                 (girls[1], 'zz-mute2', ('out',))):
            for side in sides:
                await db.execute(
                    'INSERT INTO dating_message (profile_id, sender_uid, sender_name,'
                    ' direction, text, source, message_uid)'
                    " VALUES (%s, %s, 'Кто-то', %s, 'привет', 'scenario', %s)",
                    (girl, uid, side, f'zz-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()

    yield {'girls': girls, 'before': before}

    async with mysql_get_db_async() as db:
        for girl in girls:
            await db.execute('DELETE FROM dating_message WHERE profile_id = %s', (girl,))
            await db.execute('DELETE FROM dating_profile WHERE id = %s', (girl,))
        await db.connection.commit()


class TestDialogsAreTalks:
    """Разговор — пара, которой **ответили**."""

    async def test_silent_pairs_do_not_count(self, auth_client, talks):
        """⚠ Инвайт без ответа — не разговор.

        Ровно эта ошибка и была видна: сто шестьдесят пять приглашений при одном
        ответе показывались как девяносто три разговора. Здесь четыре новых пары,
        а разговоров из них два.
        """
        got = await _chat(auth_client)

        assert got['dialogs'] - talks['before']['dialogs'] == 2, got

    async def test_the_same_man_at_two_girls_is_two(self, auth_client, talks):
        """Считаем пары, а не людей: `zz-talk` один, а строк на вкладке две."""
        first = await _chat(auth_client, profile_id=talks['girls'][0])
        second = await _chat(auth_client, profile_id=talks['girls'][1])

        assert (first['dialogs'], second['dialogs']) == (1, 1)

    async def test_it_matches_the_dialogs_tab(self, auth_client, talks):
        """⚠⚠ Главная проверка: два места об одном числе обязаны сойтись.

        Она же и единственная, которой мусор на стенде не мешает: оба запроса
        видят его одинаково, и сравниваются они друг с другом.
        """
        stats = await _chat(auth_client)
        tab = (await auth_client.get('/api/dating/chat/dialogs',
                                     params={'site_id': STAND_SITE})).json()['result']

        assert stats['dialogs'] == tab['contacts_total'], \
            'статистика и вкладка «Диалоги» считают разговоры по-разному'

    async def test_the_filter_narrows_it(self, auth_client, talks):
        """Анкета — фильтр: у каждой по одному разговору."""
        got = await _chat(auth_client, profile_id=talks['girls'][0])

        assert got['dialogs'] == 1


class TestRepliesAreCountedRaw:
    """А вот реплики считаются как есть — и их больше, чем разговоров."""

    async def test_incoming_and_outgoing(self, auth_client, talks):
        got = await _chat(auth_client)

        assert got['in'] - talks['before']['in'] == 2, 'входящие: по одной в двух парах'
        assert got['out'] - talks['before']['out'] == 4, 'исходящие: по одной в каждой'

    async def test_outgoing_may_exceed_dialogs_and_that_is_right(self, auth_client,
                                                                talks):
        """⚠ Инвайт тоже ложится исходящей репликой. Сто реплик при одном ответе —
        это один разговор, а не сто."""
        got = await _chat(auth_client, profile_id=talks['girls'][0])

        assert got['out'] == 2 and got['dialogs'] == 1


async def _chat(client, profile_id: int = 0) -> dict:
    got = (await client.get('/api/dating/stats',
                            params={'site_id': STAND_SITE,
                                    'profile_id': profile_id})).json()

    return got['result']['chat']
