"""Намерения разговора: что агент пообещал и исполнено ли это.

Исполнителя ещё нет — круга, который берёт открытые намерения и делает дело.
Поэтому здесь проверяется **дверь**: ляжет ли метка, не ляжет ли вторая, пока
первая открыта, отпустит ли ключ выполненная, и не отметят ли одну строку
дважды два исполнителя.
"""
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.chat_intention import (chat_intention_done,
                                       chat_intention_open, chat_intention_put)
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.chat

PROMISE = 'she_promised_send_photo'


def _uid() -> str:
    """Свой собеседник каждому тесту: ключ таблицы — на пару, а не на анкету."""
    return f'ci-{uuid.uuid4().hex[:8]}'


async def _rows(profile_id: int) -> list:
    """Строки намерений этой анкеты, старые первыми."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, uid, intention, status, done_at'
            '  FROM chat_intention WHERE profile_id = %s ORDER BY id',
            (int(profile_id),))

        return [dict(row) for row in (await db.fetchall() or [])]


class TestThePut:
    """Запись намерения."""

    async def test_it_lands(self, girl):
        uid = _uid()

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        [PROMISE]) == 1

        rows = await _rows(girl)
        assert len(rows) == 1
        assert rows[0]['intention'] == PROMISE
        assert rows[0]['status'] == 'open'
        assert rows[0]['done_at'] is None

    async def test_twice_while_open_does_not_land(self, girl):
        """⚠ Одна открытая на пару и метку: повтор новой строки не заводит."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        [PROMISE]) == 0
        assert len(await _rows(girl)) == 1

    async def test_the_same_label_twice_in_one_batch_lands_once(self, girl):
        """Повторы внутри пачки схлопывает ключ базы, а не разбор."""
        uid = _uid()

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        [PROMISE, PROMISE]) == 1

    async def test_another_label_lands_beside_it(self, girl):
        """Ключ — на метку: другое обещание той же паре законно."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        ['he_asked_not_to_write']) == 1
        assert len(await _rows(girl)) == 2

    async def test_another_companion_lands_beside_it(self, girl):
        """Ключ — на пару: та же метка другому человеку законна."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        [PROMISE]) == 1
        assert len(await _rows(girl)) == 2


class TestWhatIsNotWritten:
    """Чего в таблицу не попадает."""

    async def test_a_long_label_is_dropped(self, girl):
        """⚠ Длиннее 100 знаков — пересказ разговора, а не метка."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        ['x' * 101]) == 0
        assert await _rows(girl) == []

    async def test_a_label_of_exactly_the_limit_lands(self, girl):
        """Граница включительная: сто знаков — ещё метка."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        ['x' * 100]) == 1

    async def test_blank_labels_are_dropped(self, girl):
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        ['', '   ']) == 0
        assert await _rows(girl) == []

    async def test_an_empty_list_writes_nothing(self, girl):
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(), []) == 0

    async def test_without_a_companion_writes_nothing(self, girl):
        """⚠ Пустой `uid` — намерению не к кому принадлежать."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, '',
                                        [PROMISE]) == 0
        assert await _rows(girl) == []

    async def test_without_a_profile_writes_nothing(self, girl):
        assert await chat_intention_put(0, TEST_STAND_SITE, _uid(),
                                        [PROMISE]) == 0


class TestTheOpenOnes:
    """Отбор того, что ещё не исполнено."""

    async def test_it_gives_the_open_row(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        got = await chat_intention_open(TEST_STAND_SITE, profile_id=girl)

        assert [one['intention'] for one in got] == [PROMISE]
        assert got[0]['uid'] == uid

    async def test_the_done_one_is_gone_from_it(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]

        await chat_intention_done(one['id'])

        assert await chat_intention_open(TEST_STAND_SITE, profile_id=girl) == []

    async def test_it_filters_by_companion(self, girl):
        mine, other = _uid(), _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, mine, [PROMISE])
        await chat_intention_put(girl, TEST_STAND_SITE, other, [PROMISE])

        got = await chat_intention_open(TEST_STAND_SITE, profile_id=girl,
                                        uid=mine)

        assert [one['uid'] for one in got] == [mine]

    async def test_it_filters_by_profile(self, girl, girl_make):
        """Анкета — отбор, а не условие показа: у соседки свои намерения."""
        other = await girl_make()
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        await chat_intention_put(other, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_open(TEST_STAND_SITE, profile_id=other)

        assert [one['profile_id'] for one in got] == [other]

    async def test_the_limit_cuts_it(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 [PROMISE, 'he_asked_not_to_write'])

        got = await chat_intention_open(TEST_STAND_SITE, profile_id=girl,
                                        limit=1)

        assert len(got) == 1

    async def test_without_a_site_it_gives_nothing(self):
        """⚠ Отбор по сайту обязателен: круги ходят по сайтам поодиночке."""
        assert await chat_intention_open(0) == []


class TestTheDoneMark:
    """Отметка «исполнено»."""

    async def test_it_lands(self, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]

        assert await chat_intention_done(one['id']) is True

        rows = await _rows(girl)
        assert rows[0]['status'] == 'done'
        assert rows[0]['done_at'] is not None

    async def test_the_second_mark_is_not_ours(self, girl):
        """⚠ Условный UPDATE: два исполнителя не отметят одну строку дважды."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]
        await chat_intention_done(one['id'])

        assert await chat_intention_done(one['id']) is False

    async def test_a_done_one_releases_the_key(self, girl):
        """⚠⚠ Второе обещание фото законно — но только после первого."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]
        await chat_intention_done(one['id'])

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        [PROMISE]) == 1
        assert [row['status'] for row in await _rows(girl)] == ['done', 'open']

    async def test_a_missing_row_is_not_ours(self):
        assert await chat_intention_done(0) is False
