"""Инвайт попадает в переписку в момент первого ответа, а не при отправке.

## ⚠⚠ Почему не при отправке

Приглашение — не разговор. Пока человек молчит, разговора нет вовсе, а
приглашений ему уходит до двадцати за разные дни. Записанные сразу, они давали
диалог из двадцати наших реплик и ни одной его.

Цена видна на живых данных прода: из 15 832 исходящих строк **15 765 были
инвайтами**. Настоящих реплик — 67. То есть переписка была не перепиской, а
складом приглашений, и портила ровно то, ради чего инвайты хранят: понять, **на
что** человек ответил.

## Как стало

Инвайт живёт в своей таблице. Ответил человек — последние приглашения ложатся в
переписку (столько, сколько просит `invite_keep`), и уже за ними идёт его ответ.
Автоответ видит разговор, а не одну реплику без повода.
"""

import uuid

import pytest
from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343
UID = 'hist-424242'


class TestSendingDoesNotWrite:
    """⚠ Отправка инвайта переписку не трогает."""

    def test_the_sender_says_no_history(self):
        import inspect

        from src.dating import invite_send

        assert 'to_history=False' in inspect.getsource(
            invite_send.invite_send_one), \
            'инвайт снова пишется в диалог при отправке'

    def test_ordinary_sending_still_writes(self):
        """⚠ Обычная реплика в переписку идёт, и умолчание это охраняет.

        Сломай мы его — пропали бы ответы агента, то есть половина диалога.
        """
        import inspect

        from src.dating.send import send_message

        assert 'to_history: bool = True' in inspect.getsource(send_message)


class TestFirstAnswerBringsThem:
    """Первый ответ — момент, когда приглашения становятся диалогом."""

    async def test_they_are_copied(self, girl):
        from src.dating.chat import _message_save

        await _invites(girl, 3)
        await _message_save(girl, _incoming('Привет!'))

        assert await _out_count(girl) == 2, 'перенеслось не два последних инвайта'

    async def test_the_newest_ones_are_taken(self, girl):
        """Берутся **последние**: старые приглашения человек уже не помнит."""
        from src.dating.chat import _message_save

        await _invites(girl, 3)
        await _message_save(girl, _incoming('Привет!'))

        assert await _texts(girl) == ['инвайт 2', 'инвайт 3']

    async def test_the_invite_stands_before_the_answer(self, girl):
        """⚠⚠ Время берётся от отправки, а не текущее.

        Поставь мы `NOW()` — приглашение оказалось бы **новее** ответа на него, и
        диалог читался бы задом наперёд. Разбор истории идёт по времени, а не по
        `id` (`send._said_already`), так что это не косметика.
        """
        from src.dating.chat import _message_save

        await _invites(girl, 1)
        await _message_save(girl, _incoming('Привет!'))

        rows = await _rows(girl)

        assert [row['direction'] for row in rows] == ['out', 'in'], \
            'ответ оказался раньше приглашения — диалог читается наоборот'

    async def test_the_second_answer_copies_nothing(self, girl):
        """⚠ Переносим один раз: на второй ответ приглашения уже в переписке."""
        from src.dating.chat import _message_save

        await _invites(girl, 3)
        await _message_save(girl, _incoming('Привет!'))
        await _message_save(girl, _incoming('Ты тут?'))

        assert await _out_count(girl) == 2, 'приглашения перенеслись дважды'

    async def test_a_stranger_brings_nothing(self, girl):
        """Ответ от того, кому не писали, — законный случай: он начал сам."""
        from src.dating.chat import _message_save

        await _message_save(girl, _incoming('Привет, я сам нашёл'))

        assert await _out_count(girl) == 0


class TestWhatIsNotCopied:
    """⚠ В переписку идёт только то, что человеку действительно уходило."""

    async def test_a_draft_is_not_copied(self, girl):
        """Черновик в пуле человеку не уходил — в диалоге ему места нет."""
        from src.dating.chat import _message_save

        await _invites(girl, 1, status='draft')
        await _message_save(girl, _incoming('Привет!'))

        assert await _out_count(girl) == 0

    async def test_a_failed_one_is_not_copied(self, girl):
        """Не дошедшее — тоже не разговор."""
        from src.dating.chat import _message_save

        await _invites(girl, 1, status='failed')
        await _message_save(girl, _incoming('Привет!'))

        assert await _out_count(girl) == 0

    async def test_another_companion_is_not_touched(self, girl):
        """Приглашения другому человеку в этот диалог не попадают."""
        from src.dating.chat import _message_save

        await _invites(girl, 2, uid='other-999')
        await _message_save(girl, _incoming('Привет!'))

        assert await _out_count(girl) == 0


class TestTheSourceIsMarked:
    """⚠ Перенесённое помечено `source = 'invite'`.

    Иначе его не отличить от реплики, которую девушка отправила руками, — а
    разница есть: приглашение писала машина по шаблону.
    """

    async def test_the_source_says_invite(self, girl):
        from src.dating.chat import _message_save

        await _invites(girl, 1)
        await _message_save(girl, _incoming('Привет!'))

        rows = await _rows(girl)

        assert rows[0]['source'] == 'invite'


def _incoming(text: str) -> dict:
    return {'sender_uid': UID, 'sender_name': 'Кто-то', 'text': text,
            'message_uid': f'in-{uuid.uuid4().hex[:10]}'}


async def _invites(profile_id: int, count: int, status: str = 'sent',
                   uid: str = UID) -> None:
    """Кладёт истории пары `count` приглашений, старые сначала."""
    async with mysql_get_db_async() as db:
        for number in range(count):
            await db.execute(
                'INSERT INTO invite'
                ' (site_id, profile_id, uid, name, body, status, sent_at, created_at)'
                " VALUES (%s, %s, %s, 'Кто-то', %s, %s,"
                '         NOW() - INTERVAL %s HOUR, NOW() - INTERVAL %s HOUR)',
                (STAND_SITE, int(profile_id), uid, f'инвайт {number + 1}', status,
                 10 - number, 10 - number))
        await db.connection.commit()


async def _rows(profile_id: int) -> list[dict]:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT text, direction, source, created_at FROM message'
                         ' WHERE profile_id = %s ORDER BY created_at, id',
                         (int(profile_id),))

        return [dict(row) for row in await db.fetchall() or []]


async def _out_count(profile_id: int) -> int:
    return len([row for row in await _rows(profile_id) if row['direction'] == 'out'])


async def _texts(profile_id: int) -> list[str]:
    return [row['text'] for row in await _rows(profile_id) if row['direction'] == 'out']
