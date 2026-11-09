"""Закрытый чат — ответ уходит приглашением, а не возвращается в пул.

## ⚠⚠ Зачем

У Step2Love написать можно только в **открытый** чат, а открывает его принятый
запрос. Собеседник, закрывший окно между своей репликой и нашим ответом,
оставляет пустой `chatObjectList`: отправка отвечает «у анкеты нет открытого
чата», черновик возвращается в пул, круг повторяет через минуту — и так, пока
человек не вернётся. Если не вернётся вовсе (закрыл анкету), это навсегда:
249 попыток к Lance 1821030 за сутки, 56 к Lance 1822092 за час.

Лечится тем же, чем у инициативы: реплика уходит **приглашением** с текстом
ответа — собеседник читает его в списке запросов и открывает чат галочкой.

## ⚠⚠⚠ «Ушло приглашением» — не «доставлено»

Строка метится третьим состоянием `invited`. Назови это `sent` — агент счёл бы
реплику сказанной и продолжил разговор от непрочитанного; оставь `draft` —
круг разошлёт второе приглашение через минуту. Судьбу строки разбирает круг:
примут — доставка, истечёт срок — отказ с причиной.
"""

import inspect
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import answer as answer_mod
from src.dating.chat_reply import (CHAT_REPLY_INVITED, chat_reply_invited,
                                   chat_reply_put, chat_reply_waiting)

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


async def _said(profile_id: int, uid: str, text: str = 'привет') -> int:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction) VALUES (%s, %s, %s, %s, %s, %s)',
            (int(profile_id), uid, 'Someone', text,
             f'zz-{uuid.uuid4().hex[:12]}', 'in'))
        await db.connection.commit()

        return int(db.lastrowid)


async def _row(reply_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM chat_reply WHERE id = %s', (int(reply_id),))

        return dict(await db.fetchone() or {})


class TestTheReplyMayInvite:
    """Круг ответов разрешает приглашение — иначе закрытому чату нечем писать."""

    def test_the_round_sets_the_flag(self):
        code = inspect.getsource(answer_mod.answer_run)

        assert 'may_invite=True' in code, \
            'ответ снова не умеет уйти приглашением'

    def test_the_flag_is_explained(self):
        """⚠ Довод записан рядом: прежде ответ его не ставил намеренно."""
        code = inspect.getsource(answer_mod.answer_run)
        at = code.index('may_invite=True')

        assert 'закры' in code[max(0, at - 1500):at], \
            'у флага не осталось довода — следующий читатель снимет его обратно'


class TestTheRowWaitsForConsent:
    """⚠⚠ Ушедшее приглашением ждёт согласия, а не считается доставкой."""

    async def test_it_is_marked_invited(self, girl, monkeypatch):
        message_id = await _said(girl, 'u-inv-1')
        reply_id = await chat_reply_put(STAND_SITE, girl, message_id,
                                        'u-inv-1', 'Brian', 'ответ')

        await chat_reply_invited(reply_id)

        got = await _row(reply_id)

        assert got['status'] == CHAT_REPLY_INVITED
        assert got['sent_at'] is not None, 'без времени судьбу не разобрать'

    async def test_an_invited_row_is_not_taken_again(self, girl):
        """⚠⚠ Главное: круг не шлёт второе приглашение по тому же поводу."""
        message_id = await _said(girl, 'u-inv-2')
        reply_id = await chat_reply_put(STAND_SITE, girl, message_id,
                                        'u-inv-2', 'Brian', 'ответ')
        await chat_reply_invited(reply_id)

        assert await chat_reply_waiting(girl, message_id) == {}, \
            'ждущее согласия приглашение снова выдано как черновик'

    async def test_it_is_not_shown_as_a_draft(self, girl, auth_client):
        """⚠ В разговоре оно помечено своим словом, а не «ответ в пуле».

        Текст уже у собеседника — в списке запросов; подписать его «в пуле»
        значило бы сказать, что он ещё у нас.
        """
        from src.dating.chat_reply import chat_reply_pending

        message_id = await _said(girl, 'u-inv-3')
        reply_id = await chat_reply_put(STAND_SITE, girl, message_id,
                                        'u-inv-3', 'Brian', 'ответ')
        await chat_reply_invited(reply_id)

        assert await chat_reply_pending(girl, 'u-inv-3') == []


class TestTheRuleIsOneWithTheInitiative:
    """⚠⚠ Состояние то же, что у инициативы, и слово для него одно."""

    def test_both_pools_use_the_same_word(self):
        from src.dating.chat_initiative import CHAT_INITIATIVE_INVITED

        assert CHAT_REPLY_INVITED == CHAT_INITIATIVE_INVITED, \
            'два слова на одно состояние — читатель будет гадать, чем они разнятся'

    def test_the_verdict_is_asked_of_the_site(self):
        """⚠ Судьбу приглашения спрашивают у сайта одной дверью на оба пула."""
        from src.dating import chat_request

        assert hasattr(chat_request, 'chat_request_talk_open'), \
            'пропала дверь «открыт ли разговор» — пулы начнут спрашивать по-своему'
