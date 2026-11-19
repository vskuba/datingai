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


async def _said(profile_id: int, uid: str, silent: bool = False, ago_min: int = 1):
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, created_at, answer_silent_at)'
            " VALUES (%s, %s, 'X', 'hi', %s, 'in', NOW() - INTERVAL %s MINUTE,"
            '         IF(%s, NOW(), NULL))',
            (int(profile_id), uid, f'zz-{uuid.uuid4().hex[:12]}', ago_min, int(silent)))
        await db.connection.commit()
