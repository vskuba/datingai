"""Начало разговора: человек ответил впервые.

Событие живёт полчаса в ленте и нужно ровно одному месту — счётчику у слова
«Диалоги». Проверяется здесь то, что легко сломать незаметно:

* **первое слово от человека**, а не любая его реплика — иначе счётчик прыгал бы
  на каждое сообщение в идущем разговоре;
* **первое от него**, а не от нас: наш инвайт ложится в ту же таблицу исходящим, и
  считай мы обе стороны, разговор «начинался» бы в момент отправки приглашения;
* **повтор не событие**: сообщение приезжает дважды (сокет не подтвердил доставку),
  и ключ его отбрасывает — вместе с ним обязано отпасть и событие.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import chat
from src.dating.chat import _message_save

pytestmark = pytest.mark.chat

STAND_SITE = 999_343
UID = 'zz-talk'


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда. Убирает за собой переписку."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'talk-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


@pytest.fixture
def feed(monkeypatch):
    """Перехваченная лента: список событий вместо Redis."""
    got = []

    monkeypatch.setattr(chat, 'feed_write',
                        lambda site_id, kind, text, profile_id=0:
                        got.append({'site_id': site_id, 'kind': kind, 'text': text,
                                    'profile_id': profile_id}))

    return got


class TestFirstWord:
    """Разговор начинается там, где человек заговорил."""

    async def test_the_first_incoming_is_an_event(self, girl, feed):
        assert await _message_save(girl, _said('привет')) is True

        assert [one['kind'] for one in feed] == ['talk']
        assert 'Frank' in feed[0]['text']
        assert feed[0]['profile_id'] == girl and feed[0]['site_id'] == STAND_SITE

    async def test_the_second_one_is_not(self, girl, feed):
        """Иначе счётчик у «Диалогов» прыгал бы на каждую реплику разговора."""
        await _message_save(girl, _said('привет'))
        await _message_save(girl, _said('ну как ты?'))

        assert len(feed) == 1

    async def test_the_same_message_twice_is_not(self, girl, feed):
        """⚠ Сокет переспрашивает: не подтвердили доставку — реплика приедет снова.

        Ключ такую строку отбрасывает, и событие обязано отпасть вместе с ней.
        """
        one = _said('привет')

        assert await _message_save(girl, one) is True
        assert await _message_save(girl, one) is False
        assert len(feed) == 1

    async def test_our_own_reply_does_not_start_it(self, girl, feed):
        """⚠ Инвайт ложится в ту же таблицу исходящим.

        Считай мы обе стороны, «новый диалог» загорался бы в момент, когда мы
        позвали человека, — то есть ровно тогда, когда разговора ещё нет.
        """
        await _out(girl)

        await _message_save(girl, _said('привет'))

        assert len(feed) == 1, 'наша реплика съела событие или удвоила его'


class TestNotFromThePanel:
    """⚠ Импорт переписки из панели событий не рождает, и это решение.

    Он привозит сотни реплик разом и за месяц назад: лента заполнилась бы
    «новыми диалогами» годичной давности, а счётчик у вкладки обещал бы работу,
    которой нет. Событие — про **сейчас**, поэтому живёт только у сокета.
    """

    def test_panel_import_writes_no_talk_event(self):
        import inspect

        from src.dating import chat_panel

        assert "'talk'" not in inspect.getsource(chat_panel)


def _said(text: str) -> dict:
    """Реплика от человека, какой её отдаёт драйвер сайта."""
    return {'sender_uid': UID, 'sender_name': 'Frank', 'text': text,
            'message_uid': f'zz-{uuid.uuid4().hex[:12]}'}


async def _out(profile_id: int) -> None:
    """Наша реплика тому же человеку — исходящая."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name,'
            " direction, text, source, message_uid) VALUES (%s, %s, %s, 'out', %s,"
            " 'scenario', %s)",
            (int(profile_id), UID, 'Frank', 'Привет!', f'zz-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()
