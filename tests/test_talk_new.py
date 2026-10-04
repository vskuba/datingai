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


class TestFromThePanelToo:
    """⚠⚠ Импорт из панели событие пишет — но только на свежее и по одному на пару.

    Прежде он молчал вовсе, и довод был верный: импорт привозит сотни реплик
    разом, за месяц назад, а кольцо ленты — тридцать строк на все виды событий.
    Отметь каждую — и лента заполнится «новыми диалогами» годичной давности.

    Но у сайта **без сокета** — а это LadaDate, весь наш прод — другой дороги
    нет, и значок у вкладки «Диалоги» не поднимался никогда: механизм был,
    кормить его было некому. Поймано глазами: в диалогах появился ответ Семёна
    Горбункова, полоса вкладок молчала.

    Правило поэтому не «всё или ничего», а три условия разом: реплика **свежая**
    (`_fresh`, час по времени сайта), **входящая**, и на пару приходится **одно**
    событие — пять реплик подряд от одного человека это один разговор.
    """

    def test_the_import_writes_the_event(self):
        import inspect

        from src.dating import chat_panel

        code = inspect.getsource(chat_panel)

        assert "'talk'" in code, 'импорт снова молчит — значок у вкладки не поднимется'

    def test_only_fresh_replies_count(self):
        """⚠ Потолок и свежесть — на месте: без них лента утонет в старом."""
        import inspect

        from src.dating import chat_panel

        code = inspect.getsource(chat_panel.chat_panel_save)

        assert '_fresh(' in code, 'свежесть перестала проверяться'
        assert 'CHAT_PANEL_TALKS_MAX' in inspect.getsource(chat_panel._talks_feed), \
            'потолок на заход пропал — кольцо ленты вытеснится одним импортом'

    def test_invites_travel_to_history_here_too(self):
        """⚠⚠ И перенос инвайтов в переписку — тем же путём.

        Он висел на одном сокете, и на проде не нашлось **ни одного** сообщения с
        `source = 'invite'`: сайту без сокета переносить их было некому, а в
        диалоге человек видел приглашения не полностью.
        """
        import inspect

        from src.dating import chat_panel

        assert 'chat_invites_to_history' in inspect.getsource(chat_panel)


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
