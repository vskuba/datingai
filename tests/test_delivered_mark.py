"""Доставка отмечается только у реплики, которая вправду ушла.

## ⚠⚠ Зачем

Сосед пишет обмен в `companion_session` **внутри прогона** — до того, как мы
постучимся к сайту. Не отметь мы доставку отдельно, агент увидел бы в истории
свою реплику, которой собеседник не получал, и построил бы следующий ответ как
продолжение несказанного.

⚠ **Наружу сюита не ходит** (`testing.md`, §1.2): и агент, и отправка —
заглушки, проверяется то, что мы им передали.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import answer as answer_mod

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    """Анкета сюиты. Логин на `@example.com` — метка, по ней идёт уборка."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'deliver-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM chat_reply WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _said(profile_id: int, uid: str = 'u-1') -> None:
    """Входящая реплика: без неё отвечать нечего и агент не зовётся."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            "  message_uid, direction) VALUES (%s, %s, %s, %s, %s, 'in')",
            (int(profile_id), uid, 'Brian', 'привет',
             f'dl-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()


async def _profile(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s', (profile_id,))

        return dict(await db.fetchone())


@pytest.fixture
def marked(monkeypatch):
    """Номера, которые панель отметила доставленными."""
    got = []

    async def _delivered(message_id):
        got.append(int(message_id))

        return True

    monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_delivered', _delivered)

    return got


def _agent(monkeypatch, text: str, message_id: int):
    async def _talk(agent_id, question, variables=None):
        return {'text': text, 'message_id': message_id}

    monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _talk)


def _sending(monkeypatch, ok: bool):
    async def _send(profile, presence, uid, text, **kwargs):
        return {'ok': ok, 'error': '' if ok else 'сайт отказал',
                'offline': False, 'response': '',
                'message_uid': 'u1' if ok else ''}

    monkeypatch.setattr(answer_mod, 'send_message', _send)


class TestChatMarksDelivery:
    async def test_sent_reply_is_marked(self, monkeypatch, marked, girl):
        """Ушедшая реплика помечается доставленной."""
        await _said(girl)
        _agent(monkeypatch, 'hello', 777)
        _sending(monkeypatch, ok=True)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [777], 'ушедшая реплика обязана быть отмечена'

    async def test_failed_send_is_not_marked(self, monkeypatch, marked, girl):
        """⚠⚠ Неудачная отправка отметки не ставит — ради этого всё и затеяно."""
        await _said(girl)
        _agent(monkeypatch, 'hello', 778)
        _sending(monkeypatch, ok=False)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [], 'не ушедшую реплику отмечать нельзя'

    async def test_silence_marks_nothing(self, monkeypatch, marked, girl):
        """Агент промолчал — номера нет, звать нечего."""
        await _said(girl)
        _agent(monkeypatch, '', 0)
        _sending(monkeypatch, ok=True)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == []
