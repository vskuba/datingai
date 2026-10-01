"""⚠ Молчание агента — законный исход, и его надо помнить.

Пустой ответ LLM значит «сейчас отвечать не надо». Помнить это было негде: ставилась
только отметка попытки, а `is_answered` оставался нулём. Отсюда два следствия, и
второе дороже первого:

* значок «нужен ответ» мигал вечно;
* ⚠ круг спрашивал агента снова каждые пять минут, тот снова молчал — **молчание
  стоило денег на LLM в цикле**.

База здесь настоящая: проверяется, что метка легла и что отбор её видит. Наружу не
ходит ничего — агент и отправка подменены.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async
from src.dating import answer
from src.dating.answer import _waiting_find, answer_run
from src.uvicorn.routers.dating.profile_ import (DatingProfileSave,
                                                       profile_create,
                                                       profile_delete)
from src.uvicorn.routers.dating.profile_view import profile_view_card

pytestmark = pytest.mark.chat
# ⚠ Стенд, а не живой сайт: анкета сюиты не должна попадать в круги воркера
# (`test_mail_in.py`, `STAND_SITE`).
SITE = 999_343
UID = '77'


@pytest.fixture
async def talk(rhapsody):
    """Одноразовая анкета с одним входящим сообщением.

    ⚠ Своя, а не живая: сторож в `conftest` не даст писать в переписку
    существующих. Фикстура `rhapsody` нужна потому, что заведение анкеты сверяет
    сайт у соседа.
    """
    answer = await profile_create(
        DatingProfileSave(site_id=SITE, login=f'silent-{uuid.uuid4().hex[:8]}',
                          password='x'), user=None)
    profile_id = int(answer['result'])

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message'
            ' (profile_id, sender_uid, sender_name, text, message_uid,'
            '  direction, is_read, source)'
            " VALUES (%s, %s, 'Он', 'ты тут?', %s, 'in', 1, 'socket')",
            (profile_id, UID, f'probe-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()

    yield profile_id

    try:
        await profile_delete(profile_id, user=None)
    except Exception:
        pass


@pytest.fixture
def silent_agent(monkeypatch):
    """Агент, который молчит. Возвращает, сколько раз его спросили."""
    asked = []

    async def _ask(agent_id, question, language, profile=None,
                   history='', uid='', anyway=False, name='', **_):
        asked.append(question)

        return ('', '', 0)          # пустой ответ — решение молчать

    async def _send(profile, presence, uid, text):
        raise AssertionError('после молчания отправлять нечего')

    monkeypatch.setattr(answer, '_agent_ask', _ask)
    monkeypatch.setattr(answer, 'send_message', _send)

    return asked


class TestSilenceIsRemembered:
    """Метка ложится и закрывает повод."""

    async def test_silence_marks_the_message(self, talk, silent_agent):
        result = await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                         {}, agent_id=1, limit=5)

        assert result['silent'] == 1 and result['sent'] == 0

        async with mysql_get_db_async() as db:
            await db.execute('SELECT answer_silent_at, is_answered FROM message'
                             ' WHERE profile_id = %s', (talk,))
            row = await db.fetchone()

        assert row['answer_silent_at'] is not None, 'молчание не запомнилось'
        assert row['is_answered'] == 0, \
            '`is_answered` означает «мы отправили ответ» — врать в нём нельзя'

    async def test_agent_is_not_asked_twice(self, talk, silent_agent):
        """⚠ Главное здесь: второй круг агента **не спрашивает**.

        Прежде спрашивал — каждые пять минут, до конца времён. Отметка попытки
        держит только `ANSWER_RETRY_SEC`, и потом всё повторялось.
        """
        await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                {}, agent_id=1, limit=5)
        assert len(silent_agent) == 1

        # Отметку попытки снимаем: проверяем, что держит именно метка молчания.
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE message SET answer_tried_at = NULL'
                             ' WHERE profile_id = %s', (talk,))
            await db.connection.commit()

        await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                {}, agent_id=1, limit=5)

        assert len(silent_agent) == 1, 'агента спросили о том же во второй раз'

    async def test_selection_skips_the_silenced(self, talk, silent_agent):
        await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                {}, agent_id=1, limit=5)

        assert await _waiting_find(talk, 5, [], False) == []

    async def test_badge_goes_out(self, talk, silent_agent):
        """Значок «нужен ответ» гаснет: работы по этому диалогу больше нет.

        Мигающий значок требует того, чего не будет — круг про это сообщение больше
        не спросит.
        """
        card = (await profile_view_card(talk, user=None))['result']
        assert card['contacts'][0]['needs_answer'] is True, 'значок не горел изначально'

        await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                {}, agent_id=1, limit=5)

        card = (await profile_view_card(talk, user=None))['result']

        assert card['contacts'][0]['needs_answer'] is False, 'значок остался мигать'

    async def test_dialog_shows_the_decision(self, talk, silent_agent):
        """Окно получает метку с временем — иначе тишина читается как поломка.

        Человек ждёт ответа, его нет, и непонятно: сломалось или так задумано.
        """
        await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                {}, agent_id=1, limit=5)

        card = (await profile_view_card(talk, user=None))['result']
        last = card['contacts'][0]['messages_last'][-1]

        assert last['answer_silent_at'] is not None


class TestSilenceIsNotForever:
    """⚠ Молчание относится к сказанному, а не к собеседнику навсегда."""

    async def test_new_message_is_asked_again(self, talk, silent_agent):
        """Написал снова — агента спрашиваем опять.

        Иначе одно молчание закрыло бы разговор навсегда: собеседник пишет, а мы
        молчим, потому что когда-то решили промолчать на другое сообщение.
        """
        await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                {}, agent_id=1, limit=5)
        assert len(silent_agent) == 1

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO message'
                ' (profile_id, sender_uid, sender_name, text, message_uid,'
                '  direction, is_read, source)'
                " VALUES (%s, %s, 'Он', 'ну как?', %s, 'in', 1, 'socket')",
                (talk, UID, f'probe-{uuid.uuid4().hex[:12]}'))
            await db.connection.commit()

        await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                {}, agent_id=1, limit=5)

        assert len(silent_agent) == 2, 'новое сообщение осталось без внимания'

    async def test_badge_lights_up_again(self, talk, silent_agent):
        """И значок снова горит: повод появился новый."""
        await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                {}, agent_id=1, limit=5)

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO message'
                ' (profile_id, sender_uid, sender_name, text, message_uid,'
                '  direction, is_read, source)'
                " VALUES (%s, %s, 'Он', 'ну как?', %s, 'in', 1, 'socket')",
                (talk, UID, f'probe-{uuid.uuid4().hex[:12]}'))
            await db.connection.commit()

        card = (await profile_view_card(talk, user=None))['result']

        assert card['contacts'][0]['needs_answer'] is True


class TestAnswerStillWorks:
    """Обратная сторона: непустой ответ уходит как раньше.

    Без этой проверки правило могло бы оказаться безусловным — «молчим всегда», — и
    автоответ тихо перестал бы работать вовсе.
    """

    async def test_non_empty_answer_is_sent(self, talk, monkeypatch):
        sent = []

        async def _ask(agent_id, question, language, profile=None,
                   history='', uid='', anyway=False, name='', **_):
            return ('и тебе привет', '', 0)

        async def _send(profile, presence, uid, text):
            sent.append(text)

            return {'ok': True, 'error': '', 'offline': False, 'response': '',
                    'message_uid': 'x'}

        monkeypatch.setattr(answer, '_agent_ask', _ask)
        monkeypatch.setattr(answer, 'send_message', _send)

        result = await answer_run({'id': talk, 'site_id': SITE, 'role': 'lady'},
                                         {}, agent_id=1, limit=5)

        assert sent == ['и тебе привет'] and result['sent'] == 1

        async with mysql_get_db_async() as db:
            await db.execute('SELECT answer_silent_at FROM message'
                             ' WHERE profile_id = %s', (talk,))
            row = await db.fetchone()

        assert row['answer_silent_at'] is None, 'ответ отметили как молчание'
