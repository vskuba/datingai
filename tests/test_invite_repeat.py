"""Инвайт заканчивается ответом, а не отправкой.

⚠ **Два правила, и оба поймались на живом проде.**

Первое: наш собственный инвайт ложится в `dating_message` исходящим, и пара
переставала быть свободной — «у них же переписка», хотя со стороны человека не
было ни буквы. У пары Соломия ↔ Semen четыре строки, все `out`, входящих ноль.

Второе: строка `sent` жила вечно, и промолчавший вычёркивался из работы насовсем.
Очистка пула не помогала — она сносит черновики, а ушедшее остаётся.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import dating_invite
from src.dating.dating_invite import _invited, _talked

pytestmark = pytest.mark.chat

STAND_SITE = 999_343
UID = 'zz-repeat'


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда. Убирает за собой переписку и инвайты."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'rep-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM dating_invite WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM dating_profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _message(profile_id: int, direction: str) -> None:
    """Реплика в переписке. ⚠ `message_uid` свой у каждой: пара «анкета + номер
    сообщения» уникальна в базе, и две реплики с пустым номером не лягут."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_message (profile_id, sender_uid, sender_name,'
            ' direction, text, source, message_uid) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (int(profile_id), UID, 'Кто-то', direction, 'привет', 'scenario',
             f'zz-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()


async def _invite(profile_id: int, status: str, ago_sec: int = 0) -> None:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_invite (site_id, profile_id, uid, name, body, status,'
            ' sent_at) VALUES (%s, %s, %s, %s, %s, %s,'
            '                  IF(%s = 0, NULL, NOW() - INTERVAL %s SECOND))',
            (STAND_SITE, int(profile_id), UID, 'Кто-то', 'Hi!', status,
             ago_sec, ago_sec))
        await db.connection.commit()


class TestTalked:
    """Разговор — это ответ, а не наш монолог."""

    async def test_our_own_invite_is_not_a_conversation(self, girl):
        """⚠ Позвали и молчание в ответ — пара по-прежнему свободна."""
        await _message(girl, 'out')

        assert (girl, UID) not in await _talked(STAND_SITE)

    async def test_his_answer_is(self, girl):
        """Ответил — разговор начался, первую реплику слать больше некуда."""
        await _message(girl, 'out')
        await _message(girl, 'in')

        assert (girl, UID) in await _talked(STAND_SITE)

    async def test_conversation_belongs_to_the_pair(self, girl, auth_client):
        """⚠ Тот же мужчина может вовсю переписываться с одной нашей анкетой и не
        знать второй. Проверено на живом: Semen ответил Диане и молчит у Соломии —
        и для Соломии он законный адресат первой реплики."""
        other = int((await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'rep2-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result'])
        try:
            await _message(girl, 'in')
            talked = await _talked(STAND_SITE)

            assert (girl, UID) in talked
            assert (other, UID) not in talked, 'чужой разговор закрыл вторую анкету'
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM dating_message WHERE profile_id = %s', (other,))
                await db.execute('DELETE FROM dating_profile WHERE id = %s', (other,))
                await db.connection.commit()


class TestRepeat:
    """Молчащего зовём снова — но не сразу."""

    async def test_draft_holds_him_regardless_of_time(self, girl):
        """Черновик ждёт отправки, и второй задвоил бы карточку."""
        await _invite(girl, 'draft')

        assert UID in await _invited(STAND_SITE)

    async def test_fresh_send_holds_him(self, girl):
        """⚠ Без паузы круг звал бы его каждый заход, пока он в ленте."""
        await _invite(girl, 'sent', ago_sec=60)

        assert UID in await _invited(STAND_SITE)

    async def test_old_send_lets_him_back(self, girl):
        """Сутки прошли, ответа нет — приглашение не состоялось, зовём снова."""
        await _invite(girl, 'sent', ago_sec=90_000)

        assert UID not in await _invited(STAND_SITE)

    async def test_pause_comes_from_the_site(self, girl, monkeypatch):
        """Срок — настройка сайта, а не число в коде."""
        async def week(site_id, key):
            return 604_800

        monkeypatch.setattr(dating_invite, 'DATING_INVITE_REPEAT_DEFAULT', 1)
        monkeypatch.setattr('src.uvicorn.routers.dating.dating_site.dating_site_seconds',
                            week)
        await _invite(girl, 'sent', ago_sec=90_000)

        assert UID in await _invited(STAND_SITE), 'недельную паузу не спросили'
