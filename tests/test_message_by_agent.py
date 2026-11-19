"""Реплика агента в переписке помечена: в окне разговора видно, кто сказал.

Анкету ведут параллельно живой человек и агент (Luxee, 19.11.2026), и по ленте
нельзя было понять, чьи слова сбили разговор: кнопка оператора шлёт той же
дорогой, что агент, и `source` их не различает. Признак — `message.by_agent`.
"""

import inspect
import uuid
from pathlib import Path

import pytest

from mysql_.mysql_ import mysql_get_db_async
from src.dating import answer, initiative_round, invite_send
from src.dating.send import send_message_remember
from src.uvicorn.routers.dating.profile_view import profile_view_messages

pytestmark = pytest.mark.chat

PAGE = (Path(__file__).parents[1] / 'data' / 'uvicorn' / 'template' / 'admin'
        / 'dating' / 'profile.html').read_text(encoding='utf-8')


class TestTheRowKnowsItsAuthor:
    async def test_agent_and_operator_rows_differ(self, girl):
        await send_message_remember(girl, 'u-agent', 'agent says hi', uuid.uuid4().hex,
                                    by_agent=True)
        await send_message_remember(girl, 'u-agent', 'operator says hi', uuid.uuid4().hex)

        got = {r['text']: r['by_agent'] for r in
               (await profile_view_messages(girl, uid='u-agent', user=None))['result']}

        assert got == {'agent says hi': 1, 'operator says hi': 0}

    async def test_moved_invite_is_the_agents(self, girl):
        async with mysql_get_db_async() as db:
            await db.execute(
                "INSERT INTO invite (site_id, profile_id, uid, name, body, status, sent_at)"
                " VALUES (999343, %s, 'u-inv', 'Brian', 'hey you', 'sent', NOW())", (girl,))
            await db.connection.commit()
            from src.dating.chat import chat_invites_to_history
            await chat_invites_to_history(db, girl, 'u-inv')
            await db.connection.commit()
            await db.execute("SELECT by_agent FROM message WHERE profile_id = %s"
                             " AND sender_uid = 'u-inv'", (girl,))
            rows = await db.fetchall()

        assert rows and all(r['by_agent'] == 1 for r in rows)


class TestTheAgentRoadsMarkIt:
    @pytest.mark.parametrize('module,func', [(answer, 'answer_run'),
                                             (initiative_round, '_send'),
                                             (invite_send, 'invite_send_one')])
    def test_agent_roads_pass_the_flag(self, module, func):
        code = inspect.getsource(getattr(module, func))
        assert 'by_agent=True' in code, f'{module.__name__}.{func} пишет без признака агента'


class TestThePageShowsIt:
    def test_icon_only_for_agent_rows(self):
        at = PAGE.index('class="msg-agent"')
        assert 'out && Number(m.by_agent)' in PAGE[at - 400:at]
