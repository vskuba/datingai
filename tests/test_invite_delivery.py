"""Журнал доставки инвайтов: страницы и отбор именами.

⚠ **Отбор и перелистывание — на сервере, и это не вкусовщина.** Журнал растёт на
реплику в минуту. Отдай ручка «последние триста», а страница отбери среди них —
«все реплики Семёну» означало бы «те из них, что попали в эти триста», причём
молча: список выглядел бы полным.

Здесь и проверяется, что ответ считает по **всему** журналу: и число страниц, и
состав чипсов.
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

    async def test_the_chips_come_from_the_whole_journal(self, auth_client, journal):
        """⚠ Имя, уехавшее на третью страницу, обязано остаться в отборе — иначе
        отобрать по нему нечем именно тогда, когда это и нужно."""
        got = await _ask(auth_client, per=2)

        people = {one['uid'] for one in got['who']['people']}
        assert {'zz-a', 'zz-b', 'zz-c', 'zz-d'} <= people
        assert 'zz-e' not in people, 'нетронутый черновик попал в отбор'

    async def test_the_chips_carry_the_count(self, auth_client, journal):
        got = await _ask(auth_client, per=2)

        counts = {one['uid']: one['n'] for one in got['who']['people']}
        assert counts['zz-a'] == 2 and counts['zz-d'] == 1

    async def test_the_busiest_chip_comes_first(self, auth_client, journal):
        """Полоса обрезается высотой: первыми должны стоять те, кого много."""
        got = await _ask(auth_client, per=2)
        rows = [one['n'] for one in got['who']['people']]

        assert rows == sorted(rows, reverse=True)


class TestPoolIsNotBroken:
    """⚠ Пул читают **целиком**: страницы завели журналу, а не ему."""

    async def test_the_pool_still_comes_whole(self, auth_client, journal):
        got = (await auth_client.get('/api/dating/invite',
                                     params={'site_id': STAND_SITE,
                                             'status_': 'draft'})).json()

        assert len({row['uid'] for row in got['result']}) == 2, got['result']


async def _ask(client, per: int = 25, page: int = 1, profile_id: int = 0,
               uid: str = '') -> dict:
    return (await client.get('/api/dating/invite',
                             params={'site_id': STAND_SITE, 'status_': 'delivery',
                                     'page': page, 'per': per,
                                     'profile_id': profile_id, 'uid': uid})).json()
