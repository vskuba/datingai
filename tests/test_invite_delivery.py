"""Журнал доставки инвайтов: страницы и отбор именами.

⚠ **Отбор и перелистывание — на сервере, и это не вкусовщина.** Журнал растёт на
реплику в минуту. Отдай ручка «последние триста», а страница отбери среди них —
«все реплики Семёну» означало бы «те из них, что попали в эти триста», причём
молча: список выглядел бы полным.

Здесь и проверяется, что число страниц считается по **всему** журналу, а отбор
сужает его целиком, а не присланную страницу.

⚠ **Списка имён ручка при этом не отдаёт.** Отдавала: за сутки это полторы сотни
чипсов стеной поверх самого журнала. Отбор начинается в карточке — имя и так стоит
в строке перед глазами, — а в полосу сверху попадает уже выбранное, с крестиком.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def journal(auth_client):
    """Две анкеты и журнал: по три ушедших реплики каждой, двум собеседникам.

    Плюс одна неудачная, оставшаяся в пуле с причиной: `delivery` обязан показать
    и её — ради таких на вкладку и заходят.
    """
    girls = []
    for _ in range(2):
        girls.append(int((await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'dlv-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']))

    async with mysql_get_db_async() as db:
        for girl in girls:
            for uid, name in (('zz-a', 'Adrian'), ('zz-b', 'Brian'), ('zz-c', 'Carl')):
                await db.execute(
                    'INSERT INTO dating_invite (site_id, profile_id, uid, name, body,'
                    " status, sent_at) VALUES (%s, %s, %s, %s, 'привет', 'sent', NOW())",
                    (STAND_SITE, girl, uid, name))

        # Неудачная: осталась в пуле с причиной.
        await db.execute(
            'INSERT INTO dating_invite (site_id, profile_id, uid, name, body, status,'
            " error) VALUES (%s, %s, 'zz-d', 'Dominic', 'привет', 'draft', 'сайт молчит')",
            (STAND_SITE, girls[0]))
        # И нетронутый черновик: его в журнале доставки быть не должно.
        await db.execute(
            'INSERT INTO dating_invite (site_id, profile_id, uid, name, body, status)'
            " VALUES (%s, %s, 'zz-e', 'Erik', 'привет', 'draft')",
            (STAND_SITE, girls[1]))
        await db.connection.commit()

    yield girls

    async with mysql_get_db_async() as db:
        for girl in girls:
            await db.execute('DELETE FROM dating_invite WHERE profile_id = %s', (girl,))
            await db.execute('DELETE FROM dating_profile WHERE id = %s', (girl,))
        await db.connection.commit()


class TestWhatGetsIn:
    """Что считается доставкой."""

    async def test_the_untouched_draft_is_not_delivery(self, auth_client, journal):
        """Его не отправляли — ему место в пуле, а не в журнале."""
        got = await _ask(auth_client)

        assert 'zz-e' not in [row['uid'] for row in got['result']]

    async def test_the_failed_one_is(self, auth_client, journal):
        """⚠ Неудача **оставляет реплику в пуле** (`draft` с причиной): спроси
        вкладка по статусам, она молчала бы ровно о том, ради чего открыта."""
        got = await _ask(auth_client, per=100)

        assert 'zz-d' in [row['uid'] for row in got['result']]


class TestPages:
    """Страницы считаются по всему журналу, а не по присланному."""

    async def test_the_count_is_the_whole_journal(self, auth_client, journal):
        got = await _ask(auth_client, per=2)

        assert got['total'] == 7, got['total']
        assert got['pages'] == 4
        assert len(got['result']) == 2

    async def test_the_second_page_is_other_rows(self, auth_client, journal):
        first = await _ask(auth_client, per=2, page=1)
        second = await _ask(auth_client, per=2, page=2)

        assert {row['id'] for row in first['result']} \
            & {row['id'] for row in second['result']} == set()

    async def test_a_page_past_the_end_comes_back(self, auth_client, journal):
        """⚠ Правим, а не отвергаем: журнал мог укоротиться, пока человек листал."""
        got = await _ask(auth_client, per=2, page=99)

        assert got['page'] == got['pages'] and got['result']


class TestOrder:
    """⚠ Порядок — по времени события, а не по номеру строки.

    `id` — это порядок, в котором реплики **сочинили**; круг берёт из пула
    старейшую, и с порядком отправки он не совпадает вовсе.
    """

    async def test_the_latest_send_is_on_top(self, auth_client, journal):
        await _sent_at(journal[0], 'zz-a', '2026-09-01 10:00:00')
        await _sent_at(journal[0], 'zz-b', '2026-09-08 10:00:00')
        await _sent_at(journal[0], 'zz-c', '2026-09-03 10:00:00')

        got = await _ask(auth_client, per=100, profile_id=journal[0])
        uids = [row['uid'] for row in got['result'] if row['status'] == 'sent']

        assert uids == ['zz-b', 'zz-c', 'zz-a'], uids

    async def test_the_failed_one_keeps_its_place(self, auth_client, journal):
        """⚠ У неудачной `sent_at` снят — временем остаётся `updated_at`.

        Без этого такие строки уезжали бы в конец журнала, то есть прятались бы
        ровно те, ради которых на вкладку и заходят.
        """
        await _sent_at(journal[0], 'zz-a', '2026-09-01 10:00:00')

        got = await _ask(auth_client, per=100, profile_id=journal[0])
        uids = [row['uid'] for row in got['result']]

        assert uids.index('zz-d') < uids.index('zz-a'), uids

    async def test_the_time_of_the_event_comes_back(self, auth_client, journal):
        """Карточка показывает время первым — брать его должно откуда."""
        got = await _ask(auth_client, per=100)
        failed = [row for row in got['result'] if row['uid'] == 'zz-d'][0]

        assert failed['updated_at'], 'у неудачной строки нет времени события'


class TestFilters:
    """Отбор именами."""

    async def test_by_the_girl(self, auth_client, journal):
        got = await _ask(auth_client, per=100, profile_id=journal[0])

        assert {row['profile_id'] for row in got['result']} == {journal[0]}
        assert got['total'] == 4, 'три ушедших плюс неудачная'

    async def test_by_the_companion(self, auth_client, journal):
        got = await _ask(auth_client, per=100, uid='zz-b')

        assert {row['uid'] for row in got['result']} == {'zz-b'}
        assert got['total'] == 2, 'по одной от каждой анкеты'

    async def test_both_at_once(self, auth_client, journal):
        got = await _ask(auth_client, per=100, profile_id=journal[1], uid='zz-b')

        assert got['total'] == 1

    async def test_no_list_of_names_comes_back(self, auth_client, journal):
        """⚠ Ручка **не** перечисляет имена журнала, и это решение, а не пропуск.

        Перечисляла: за сутки — полторы сотни чипсов стеной поверх самого журнала.
        Отбор начинается в карточке: имя и так стоит в строке перед глазами.
        """
        got = await _ask(auth_client, per=2)

        assert 'who' not in got, 'ручка снова отдаёт список имён'


class TestTheStripStartsEmpty:
    """⚠ Полоса отбора пуста, пока по имени не нажали.

    Так и родилась правка: полоса перечисляла всех, кто есть в журнале, и за сутки
    это полторы сотни чипсов поверх самого журнала.
    """

    async def test_the_work_template_has_one_strip(self, auth_client):
        page = (await auth_client.get('/admin/dating/profile/work',
                                      params={'site_id': STAND_SITE})).text

        assert 'data-slot="invite-sent-filter"' in page, 'полосы отбора нет'
        assert 'invite-sent-girls' not in page and 'invite-sent-people' not in page, \
            'вернулись полосы со всеми именами'

    async def test_the_pager_is_there(self, auth_client):
        page = (await auth_client.get('/admin/dating/profile/work',
                                      params={'site_id': STAND_SITE})).text

        assert 'data-slot="invite-sent-pager"' in page


class TestPoolIsNotBroken:
    """⚠ Пул читают **целиком**: страницы завели журналу, а не ему."""

    async def test_the_pool_still_comes_whole(self, auth_client, journal):
        got = (await auth_client.get('/api/dating/invite',
                                     params={'site_id': STAND_SITE,
                                             'status_': 'draft'})).json()

        assert len({row['uid'] for row in got['result']}) == 2, got['result']


async def _sent_at(profile_id: int, uid: str, when: str) -> None:
    """Двигает время отправки строки: порядок журнала проверяется им."""
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_invite SET sent_at = %s'
                         ' WHERE profile_id = %s AND uid = %s', (when, profile_id, uid))
        await db.connection.commit()


async def _ask(client, per: int = 25, page: int = 1, profile_id: int = 0,
               uid: str = '') -> dict:
    return (await client.get('/api/dating/invite',
                             params={'site_id': STAND_SITE, 'status_': 'delivery',
                                     'page': page, 'per': per,
                                     'profile_id': profile_id, 'uid': uid})).json()
