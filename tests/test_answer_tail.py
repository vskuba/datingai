"""⚠⚠ Собеседник пишет очередями — агент обязан увидеть всю очередь.

Повод написать — последняя реплика диалога, и в вопрос уходила **она одна**.
А `_answered_mark` после отправки закрывает **все** входящие собеседника. То
есть реплики между предпоследней и последней исчезали дважды: модель их не
видела, и в переписку у соседа они не попадали.

Живой случай с прода, Diana ↔ Mike, 27.09:

    07:07 in  mmmmmm
    07:07 in  good and you??? how was your coffee???
    07:08 in  how did you sleep???      ← только это видел агент
    07:09 out coffee was perfect…

Два вопроса из трёх не дошли никуда и больше не дойдут: строки помечены
отвеченными. На установке таких пар восемь, в одной накопилось семнадцать
реплик.

## ⚠⚠⚠ Граница хвоста — наша последняя реплика, а не `is_answered`

Напрашивается «всё неотвеченное», но пометка проставляется не всегда:
привезённое из панели ложится задним числом, уже после нашего ответа, и
остаётся с нулём навсегда. У того же Mike по `is_answered` в хвост попали бы
девять реплик, включая «hi», на которое ответили двумя часами раньше.

База здесь настоящая, наружу не ходит ничего: агент и отправка подменены.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async
from src.dating import answer
from src.dating.answer import answer_run

pytestmark = pytest.mark.chat

SITE = 999_343
UID = '78'


async def _say(profile_id: int, text: str, direction: str, shift: str) -> None:
    """Реплика в переписку со сдвигом времени назад от текущего момента."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message'
            ' (profile_id, sender_uid, sender_name, text, message_uid,'
            '  direction, is_read, source, created_at)'
            " VALUES (%s, %s, 'Он', %s, %s, %s, 1, 'socket',"
            f'         NOW() - INTERVAL {shift})',
            (profile_id, UID, text, f'tail-{uuid.uuid4().hex[:12]}', direction))
        await db.connection.commit()


@pytest.fixture
async def talk(rhapsody):
    """Одноразовая анкета без сообщений: каждый тест кладёт свои."""
    from src.uvicorn.routers.dating.profile_ import (DatingProfileSave,
                                                     profile_create,
                                                     profile_delete)
    made = await profile_create(
        DatingProfileSave(site_id=SITE, login=f'tail-{uuid.uuid4().hex[:8]}',
                          password='x'), user=None)
    profile_id = int(made['result'])

    yield profile_id

    try:
        await profile_delete(profile_id, user=None)
    except Exception:
        pass


@pytest.fixture
def asked(monkeypatch):
    """Агент, который молчит, но запоминает заданный вопрос."""
    seen = []

    async def _ask(agent_id, question, language, profile=None,
                   history='', uid='', anyway=False, name='', **_):
        seen.append(question)

        return ('', '', 0)

    async def _send(profile, presence, uid, text):
        raise AssertionError('агент промолчал — отправлять нечего')

    monkeypatch.setattr(answer, '_agent_ask', _ask)
    monkeypatch.setattr(answer, 'send_message', _send)

    return seen


def _run(profile_id: int):
    return answer_run({'id': profile_id, 'site_id': SITE, 'role': 'lady'},
                      {}, agent_id=1, limit=5)


class TestTailIsGlued:
    async def test_queue_reaches_the_agent_whole(self, talk, asked):
        """⚠⚠ Ради этого всё: три реплики подряд приходят одним вопросом."""
        await _say(talk, 'мы ответили вчера', 'out', '3 HOUR')
        await _say(talk, 'mmmmmm', 'in', '30 MINUTE')
        await _say(talk, 'how was your coffee?', 'in', '20 MINUTE')
        await _say(talk, 'how did you sleep???', 'in', '10 MINUTE')

        await _run(talk)

        assert len(asked) == 1, 'повод один — вопрос тоже один'
        assert asked[0] == ('mmmmmm\n\nhow was your coffee?\n\n'
                            'how did you sleep???')

    async def test_single_reply_stays_single(self, talk, asked):
        """Одна реплика — вопрос ровно из неё, без разделителей."""
        await _say(talk, 'мы ответили вчера', 'out', '3 HOUR')
        await _say(talk, 'ты тут?', 'in', '10 MINUTE')

        await _run(talk)

        assert asked == ['ты тут?']

    async def test_answered_before_our_reply_is_not_repeated(self, talk, asked):
        """⚠⚠⚠ Сказанное ДО нашего ответа в хвост не идёт.

        Та самая ошибка, на которой ловилась первая редакция: по `is_answered`
        сюда попало бы «привет», отвеченное час назад, и агент ответил бы на
        давно закрытое.
        """
        await _say(talk, 'привет', 'in', '3 HOUR')
        await _say(talk, 'привет, как ты?', 'out', '2 HOUR')
        await _say(talk, 'нормально', 'in', '10 MINUTE')

        await _run(talk)

        assert asked == ['нормально'], 'в хвост попало отвеченное до нас'

    async def test_whole_talk_when_we_never_spoke(self, talk, asked):
        """Мы не говорили вовсе — берём всё сказанное им."""
        await _say(talk, 'привет', 'in', '30 MINUTE')
        await _say(talk, 'ответь мне', 'in', '10 MINUTE')

        await _run(talk)

        assert asked == ['привет\n\nответь мне']

    async def test_tail_is_capped(self, talk, asked, monkeypatch):
        """Предел бережёт вопрос от пачки, привезённой импортом."""
        monkeypatch.setattr(answer, 'ANSWER_TAIL_LIMIT', 3)

        await _say(talk, 'мы ответили вчера', 'out', '3 HOUR')

        for i in range(5):
            await _say(talk, f'реплика {i}', 'in', f'{50 - i * 10} MINUTE')

        await _run(talk)

        assert asked == ['реплика 2\n\nреплика 3\n\nреплика 4'], \
            'предел обязан оставлять ПОСЛЕДНИЕ реплики, а не первые'
