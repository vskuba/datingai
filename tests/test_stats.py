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
            await db.execute('DELETE FROM dating_invite WHERE profile_id = %s', (girl,))
            for table in ('dating_mailing', 'dating_favorite_mail'):
                await db.execute(f'DELETE FROM {table} WHERE profile_id = %s', (girl,))
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


class TestTheWindow:
    """Окно времени: сутки, полсуток или всё.

    ⚠ **Скользящее, а не календарное.** Вопрос к нему «сколько сделано за
    последние сутки», и полночь на него не отвечает: в 00:10 календарные сутки
    показали бы десять минут работы.
    """

    async def test_old_replies_fall_out_of_the_day(self, auth_client, talks):
        girl = talks['girls'][0]
        await _message_at(girl, 'in', hours=30)

        whole = await _chat(auth_client)
        day = await _chat(auth_client, hours=24)

        assert whole['in'] > day['in'], 'старая реплика осталась в сутках'

    async def test_a_fresh_reply_is_inside_both(self, auth_client, talks):
        girl = talks['girls'][0]
        before = (await _chat(auth_client, hours=12))['in']
        await _message_at(girl, 'in', hours=1)

        assert (await _chat(auth_client, hours=12))['in'] == before + 1

    async def test_twelve_is_narrower_than_a_day(self, auth_client, talks):
        girl = talks['girls'][0]
        await _message_at(girl, 'in', hours=18)

        day = await _chat(auth_client, hours=24)
        half = await _chat(auth_client, hours=12)

        assert day['in'] > half['in']

    async def test_zero_means_everything(self, auth_client, talks):
        """Ноль — «за всё время», а не «за ноль часов»."""
        girl = talks['girls'][0]
        await _message_at(girl, 'in', hours=400)

        assert (await _chat(auth_client, hours=0))['in'] >= 1

    async def test_the_window_reaches_every_group(self):
        """⚠ Окно обязано доезжать до **всех** занятий, а не только до чата:
        числа стоят рядом, и одно «за всё время» среди суточных врёт молча."""
        import inspect

        from src.uvicorn.routers.dating import dating_stats

        code = inspect.getsource(dating_stats.dating_stats_get)

        for group in ('_chat', '_mail', '_favorite', '_invite'):
            assert f'{group}(site_id, profile_id, hours)' in code, group


class TestByProfiles:
    """Вкладка «По профилям»: кто из девушек работает.

    ⚠ Вопрос **другой**, чем у общей сводки: та отвечает «сколько сделано на
    сайте» — одно число на занятие. Здесь строка на анкету, и открывают её, чтобы
    сравнить девушек между собой.
    """

    async def test_every_profile_is_there_even_the_silent_one(self, auth_client,
                                                              talks):
        """⚠ Ноль — это ответ: «она включена, а инвайтов от неё не ушло ни одного».

        Отбери мы только тех, у кого реплики есть, молчащая анкета просто пропала
        бы из списка — и вопрос «а почему от неё ничего» остался бы без ответа.
        """
        rows = await _profiles(auth_client)
        mine = [one for one in rows if one['profile_id'] in talks['girls']]

        assert len(mine) == 2, 'анкета без инвайтов выпала из сводки'
        assert all(one['invite'] == {'sent': 0, 'failed': 0} for one in mine)

    async def test_sent_and_failed_are_counted(self, auth_client, talks):
        girl = talks['girls'][0]
        await _invite(girl, 'sent')
        await _invite(girl, 'sent')
        await _invite(girl, 'failed', error='он вас закрыл')

        row = next(one for one in await _profiles(auth_client)
                   if one['profile_id'] == girl)

        assert row['invite'] == {'sent': 2, 'failed': 1}

    async def test_three_activities_come_separately(self, auth_client, talks):
        """⚠ Работа у анкеты троякая: зовёт в чат, пристаёт к избранным, шлёт
        рассылку. Одни инвайты отвечали бы только про чат."""
        girl = talks['girls'][0]
        await _invite(girl, 'sent')
        await _letter('dating_favorite_mail', girl, 'sent')
        await _letter('dating_mailing', girl, 'sent')
        await _letter('dating_mailing', girl, 'draft', error='слишком часто')

        row = next(one for one in await _profiles(auth_client)
                   if one['profile_id'] == girl)

        assert row['invite']['sent'] == 1
        assert row['favorite'] == {'sent': 1, 'failed': 0}
        assert row['mailing'] == {'sent': 1, 'failed': 1}

    async def test_the_pools_are_not_multiplied_by_each_other(self, auth_client,
                                                              talks):
        """⚠⚠ Три таблицы считаются **порознь**, а не одним сшитым `LEFT JOIN`.

        Сшей их, строки перемножились бы: две рассылки и три инвайта дали бы шесть
        и шесть. Ошибка видна только на живых данных — числа втрое больше правды.
        """
        girl = talks['girls'][0]
        for _ in range(3):
            await _invite(girl, 'sent')
        for _ in range(2):
            await _letter('dating_mailing', girl, 'sent')

        row = next(one for one in await _profiles(auth_client)
                   if one['profile_id'] == girl)

        assert (row['invite']['sent'], row['mailing']['sent']) == (3, 2)

    async def test_a_draft_with_a_reason_counts_as_a_failure(self, auth_client,
                                                             talks):
        """⚠ Неудачная отправка **оставляет реплику в пуле** с причиной, и по
        статусу её от нетронутой не отличить.

        Спроси мы `status = 'failed'`, число молчало бы ровно о тех попытках,
        ради которых вкладку и открывают.
        """
        girl = talks['girls'][0]
        await _invite(girl, 'draft', error='слишком часто')
        await _invite(girl, 'draft')

        row = next(one for one in await _profiles(auth_client)
                   if one['profile_id'] == girl)

        assert row['invite'] == {'sent': 0, 'failed': 1}

    async def test_no_site_no_rows(self, auth_client):
        got = (await auth_client.get('/api/dating/stats/profiles',
                                     params={'site_id': 0})).json()

        assert got['result'] == []


async def _letter(table: str, profile_id: int, status: str, error: str = '') -> None:
    """Письмо в пуле. ⚠ `uid` свой у каждого: ключ пула — пара, а у рассылки тройка."""
    async with mysql_get_db_async() as db:
        await db.execute(
            f'INSERT INTO {table} (site_id, profile_id, uid, name, body, status,'
            ' error, sent_at) VALUES (%s, %s, %s, %s, %s, %s, %s,'
            "                        IF(%s = 'sent', NOW(), NULL))",
            (STAND_SITE, int(profile_id), f'zz-{uuid.uuid4().hex[:8]}', 'Кто-то',
             'привет', status, error, status))
        await db.connection.commit()


async def _message_at(profile_id: int, side: str, hours: int) -> None:
    """Реплика такой-то давности: окно проверяется ею."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_message (profile_id, sender_uid, sender_name,'
            ' direction, text, source, message_uid, created_at)'
            " VALUES (%s, 'zz-win', 'Кто-то', %s, 'привет', 'scenario', %s,"
            '         NOW() - INTERVAL %s HOUR)',
            (int(profile_id), side, f'zz-{uuid.uuid4().hex[:12]}', int(hours)))
        await db.connection.commit()


async def _profiles(client) -> list:
    return (await client.get('/api/dating/stats/profiles',
                             params={'site_id': STAND_SITE})).json()['result']


async def _invite(profile_id: int, status: str, error: str = '') -> None:
    """Инвайт в нужном состоянии. ⚠ `uid` свой у каждого: ключ пула — пара."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_invite (site_id, profile_id, uid, name, body, status,'
            ' error, sent_at) VALUES (%s, %s, %s, %s, %s, %s, %s,'
            "                         IF(%s = 'sent', NOW(), NULL))",
            (STAND_SITE, int(profile_id), f'zz-{uuid.uuid4().hex[:8]}', 'Кто-то',
             'привет', status, error, status))
        await db.connection.commit()


async def _chat(client, profile_id: int = 0, hours: int = 0) -> dict:
    got = (await client.get('/api/dating/stats',
                            params={'site_id': STAND_SITE,
                                    'profile_id': profile_id,
                                    'hours': hours})).json()

    return got['result']['chat']
