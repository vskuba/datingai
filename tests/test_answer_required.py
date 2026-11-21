"""Обязательные ответы сайта: очередь Catch Up у Luxee берётся первой и без молчания.

Пока в очереди Catch Up панели Luxee висит задание, панель не даёт писать в
другие диалоги (код 485). 19.11.2026 судья промолчал в ответ Ricardo, задание
осталось — и ответ Kateryna другому человеку не ушёл. Решение владельца: в
диалогах очереди отвечать всегда.
"""

import uuid
from contextlib import asynccontextmanager

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import answer as answer_mod
from src.dating.site import site_chat_luxee
from src.dating.site.site_registry import site_registry_chat

pytestmark = pytest.mark.chat


class TestTheQueueGoesFirst:
    async def test_required_comes_first_even_if_silenced(self, girl):
        await _said(girl, 'u-req-old', silent=True, ago_min=60)
        await _said(girl, 'u-req-new', ago_min=1)

        plain = await answer_mod._waiting_find(girl, 5, [], False)
        assert [r['sender_uid'] for r in plain] == ['u-req-new'], \
            'без очереди решение молчать обязано прятать повод'

        got = await answer_mod._waiting_find(girl, 5, [], False, {'u-req-old'})
        assert [r['sender_uid'] for r in got] == ['u-req-old', 'u-req-new']

    async def test_our_last_line_in_the_queue_is_a_follow_up(self, girl):
        """Задание «напиши повторно»: последней была наша реплика, он молчит.

        Без очереди такой диалог не повод, в очереди — повод, и один раз за
        выдержку: отметка ставится на самой нашей реплике.
        """
        mine = await _said(girl, 'u-req-out', ago_min=3000, direction='out')

        assert await answer_mod._waiting_find(girl, 5, [], False) == []
        got = await answer_mod._waiting_find(girl, 5, [], False, {'u-req-out'})
        assert [(r['sender_uid'], r['direction']) for r in got] == [('u-req-out', 'out')]

        await answer_mod._tried_row(mine)
        assert await answer_mod._waiting_find(girl, 5, [], False, {'u-req-out'}) == [], \
            'агента спросят о том же каждую минуту'

    async def test_no_follow_up_right_after_our_own_line(self, girl):
        """⚠⚠ Наша реплика свежая — повтора нет, даже если пара ещё в очереди.

        Kateryna → Specialted, 21.11.2026: задание «ответь» не успевало
        исчезнуть из очереди, и вслед за нашим ответом через 16 секунд уходил
        «повтор» — шесть реплик из двенадцати подряд.
        """
        await _said(girl, 'u-req-fresh', ago_min=2, direction='out')

        assert await answer_mod._waiting_find(girl, 5, [], False, {'u-req-fresh'}) == [], \
            'повтор ушёл вслед за нашей же репликой'

        await _said(girl, 'u-req-quiet', ago_min=11, direction='out')
        got = await answer_mod._waiting_find(girl, 5, [], False, {'u-req-quiet'})
        assert [r['sender_uid'] for r in got] == ['u-req-quiet']

    async def test_only_her_pairs_count(self, monkeypatch):
        async def _pairs(self, site_id, presence, owner):
            return {('622354', '3075112'), ('622231', '2194241')}

        monkeypatch.setattr(site_chat_luxee.DatingSiteChatLuxee, 'required_pairs', _pairs)

        got = await answer_mod._required({'site_id': 999_344, 'profile_id': '622354'},
                                         {'driver': 'luxee'})

        assert got == {'3075112'}

    def test_the_forced_answer_is_wired(self):
        import inspect

        code = inspect.getsource(answer_mod.answer_run)
        assert 'uid in required' in code and '_required(profile, presence)' in code


class TestTheDriverReadsTheQueue:
    async def test_other_sites_have_no_queue(self):
        assert await site_registry_chat({'driver': 'ladadate'}).required_pairs(
            343, {}, '1') == set()

    async def test_luxee_reads_and_remembers(self, monkeypatch):
        calls = []

        @asynccontextmanager
        async def _open(site_id, presence, owner):
            calls.append(owner)
            yield object()

        async def _tasks(sock):
            return [{'importUid': 622354, 'userUid': 3075112, 'isActive': True},
                    {'importUid': 622231, 'userUid': 1, 'isActive': False}]

        monkeypatch.setattr(site_chat_luxee, 'site_socket_luxee_open', _open)
        monkeypatch.setattr(site_chat_luxee, 'site_socket_luxee_tasks', _tasks)
        monkeypatch.setattr(site_chat_luxee, '_TASKS', {})
        driver = site_registry_chat({'driver': 'luxee'})

        first = await driver.required_pairs(999_344, {}, '622354')
        again = await driver.required_pairs(999_344, {}, '622231')

        assert first == again == {('622354', '3075112')}
        assert calls == [622354], 'очередь одна на сайт — второй сокет лишний'


async def _said(profile_id: int, uid: str, silent: bool = False, ago_min: int = 1,
                direction: str = 'in') -> int:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, created_at, answer_silent_at)'
            " VALUES (%s, %s, 'X', 'hi', %s, %s, NOW() - INTERVAL %s MINUTE,"
            '         IF(%s, NOW(), NULL))',
            (int(profile_id), uid, f'zz-{uuid.uuid4().hex[:12]}', direction, ago_min,
             int(silent)))
        await db.connection.commit()

        return int(db.lastrowid)


class TestFollowUpsHaveALimit:
    """⚠⚠ Повторы по заданию «напиши повторно»: потолок, пауза, сверх — Skip.

    Kateryna → Specialted, 21.11.2026: панель заводила задание каждые ~15 минут
    его молчания, агент писал на каждое — двенадцать реплик подряд без ответа.
    """

    @staticmethod
    def _wire(monkeypatch, skip_error: str = ''):
        skipped = []

        async def _skip(self, site_id, presence, owner, uid):
            skipped.append(uid)
            return skip_error

        async def _limits(site_id):
            return 2, 3600

        monkeypatch.setattr(site_chat_luxee.DatingSiteChatLuxee, 'required_skip', _skip)
        monkeypatch.setattr(answer_mod, '_followup_limits', _limits)

        return skipped

    @staticmethod
    async def _trim(girl, uids):
        profile = {'id': girl, 'site_id': 999_344, 'profile_id': '622231'}
        return await answer_mod._required_trim(profile, {'driver': 'luxee'}, set(uids))

    async def test_answer_and_follow_ups_up_to_the_cap(self, monkeypatch, girl):
        skipped = self._wire(monkeypatch)
        await _said(girl, 'u-f', ago_min=400, direction='in')
        await _said(girl, 'u-f', ago_min=390, direction='out')   # ответ
        await _said(girl, 'u-f', ago_min=300, direction='out')   # повтор 1

        assert await self._trim(girl, ['u-f']) == {'u-f'}, 'второй повтор отняли'
        assert skipped == []

        await _said(girl, 'u-f', ago_min=200, direction='out')   # повтор 2

        assert await self._trim(girl, ['u-f']) == set(), 'третий повтор пропустили бы'
        assert skipped == ['u-f'], 'задание не пропущено — замок остался'

    async def test_too_early_is_skipped_not_held(self, monkeypatch, girl):
        """Пауза не вышла — задание пропускается: ждущее держит замок на всю учётку."""
        skipped = self._wire(monkeypatch)
        await _said(girl, 'u-e', ago_min=100, direction='in')
        await _said(girl, 'u-e', ago_min=20, direction='out')

        assert await self._trim(girl, ['u-e']) == set()
        assert skipped == ['u-e']

    async def test_fresh_line_leaves_the_stale_task_alone(self, monkeypatch, girl):
        """Сразу после нашего ответа висит прежнее «ответь» — его не пропускаем."""
        skipped = self._wire(monkeypatch)
        await _said(girl, 'u-s', ago_min=5, direction='in')
        await _said(girl, 'u-s', ago_min=2, direction='out')

        assert await self._trim(girl, ['u-s']) == {'u-s'} and skipped == []

    async def test_his_word_is_not_a_follow_up(self, monkeypatch, girl):
        skipped = self._wire(monkeypatch)
        await _said(girl, 'u-h', ago_min=100, direction='out')
        await _said(girl, 'u-h', ago_min=90, direction='out')
        await _said(girl, 'u-h', ago_min=80, direction='out')
        await _said(girl, 'u-h', ago_min=1, direction='in')

        assert await self._trim(girl, ['u-h']) == {'u-h'} and skipped == []

    async def test_failed_skip_keeps_the_pair(self, monkeypatch, girl):
        """Не снялось — пара остаётся обязательной: лишний повтор лучше замка."""
        self._wire(monkeypatch, skip_error='сокет отказал')
        await _said(girl, 'u-x', ago_min=100, direction='in')
        await _said(girl, 'u-x', ago_min=20, direction='out')

        assert await self._trim(girl, ['u-x']) == {'u-x'}

    async def test_luxee_skips_by_the_task_number(self, monkeypatch):
        sent = []

        @asynccontextmanager
        async def _open(site_id, presence, owner):
            yield object()

        async def _skip(sock, sid, reason=2, description=''):
            sent.append((sid, reason))

        monkeypatch.setattr(site_chat_luxee, 'site_socket_luxee_open', _open)
        monkeypatch.setattr(site_chat_luxee, 'site_socket_luxee_skip', _skip)
        monkeypatch.setitem(site_chat_luxee._TASKS, 999_344, (
            0.0, set(), [{'importUid': 622231, 'userUid': 3178392, 'sid': 'T-77'}]))

        driver = site_registry_chat({'driver': 'luxee'})
        assert await driver.required_skip(999_344, {}, '622231', '3178392') == ''
        assert sent == [('T-77', 2)]
        assert 999_344 not in site_chat_luxee._TASKS, 'снимок очереди не сброшен'
        assert await driver.required_skip(999_344, {}, '622231', '1') != ''
