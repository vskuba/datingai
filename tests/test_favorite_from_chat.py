"""Ответивший в чате попадает в избранные наравне с написавшим письмо.

## ⚠⚠ Зачем

Круг избранных брал работу из одной таблицы — входящих писем (`mail_in`). Для
сайта с почтой это верно, но у LadaDate разговор идёт **чатом**: реплики ложатся
в `message`, и круг не видел их вовсе.

Цена была видна на проде: трое ответивших, в избранных **один** — ровно тот, кто
прислал письмо. Двое, ответивших в чате, прошли мимо звёздочки. На сайте, где
писем почти нет, занятие работало только на бумаге.

Условие не менялось — «ответил, значит в избранные». Изменилась дорога, которой
ответ приходит.

⚠ **Наружу сюита не ходит** (`testing.md`): проверяется отбор, а не нажатие
звёздочки — оно стоит входа браузером.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.favorite_round import _senders

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


async def _said(profile_id: int, uid: str, minutes_ago: int = 0,
                direction: str = 'in') -> None:
    """Реплика в чате.

    ⚠ Время считает **база** (`NOW() - INTERVAL`), а не Python: MySQL живёт по UTC,
    и `datetime.now()` положил бы строку в будущее — на этом уже ловились.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, created_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
            (int(profile_id), uid, 'Someone', 'привет',
             f'zz-{uuid.uuid4().hex[:12]}', direction, int(minutes_ago)))
        await db.connection.commit()


async def _wrote(profile_id: int, uid: str, minutes_ago: int = 0) -> None:
    """Входящее письмо."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in (site_id, profile_id, message_id, uid, name, body,'
            '  date_post) VALUES (%s, %s, %s, %s, %s, %s,'
            '  NOW() - INTERVAL %s MINUTE)',
            # ⚠ `message_id` — **число** (номер письма на сайте) и уникален на
            # анкету (`uq_dating_mail_in`). Строка приводилась бы к нулю, и второе
            # письмо той же анкете падало бы дублем ключа.
            (STAND_SITE, int(profile_id), uuid.uuid4().int % 10 ** 12, uid,
             'Someone', 'текст', int(minutes_ago)))
        await db.connection.commit()


class TestChatCounts:
    """Ответ в чате — такая же работа, как письмо."""

    async def test_a_chat_reply_is_work(self, girl):
        """⚠⚠ Ровно то, чего не хватало: у LadaDate писем нет вовсе."""
        await _said(girl, 'chat-1')

        assert await _senders(girl) == ['chat-1']

    async def test_a_letter_still_counts(self, girl):
        """Почта не сломалась: сайту с письмами всё как было."""
        await _wrote(girl, 'mail-1')

        assert await _senders(girl) == ['mail-1']

    async def test_both_sources_come_together(self, girl):
        """⚠ Списки сливаются и сортируются **вместе**, а не «сперва письма».

        Потолок захода отрезает хвост, и отрезать надо самое старое — независимо
        от того, какой дорогой оно пришло.
        """
        await _wrote(girl, 'mail-old', minutes_ago=90)
        await _said(girl, 'chat-new', minutes_ago=1)
        await _wrote(girl, 'mail-mid', minutes_ago=30)

        assert await _senders(girl) == ['chat-new', 'mail-mid', 'mail-old']

    async def test_one_person_two_ways_is_one(self, girl):
        """Человек и написал, и ответил в чате — он один, и берём свежайшее."""
        await _wrote(girl, 'both', minutes_ago=60)
        await _said(girl, 'both', minutes_ago=2)
        await _said(girl, 'other', minutes_ago=30)

        assert await _senders(girl) == ['both', 'other']


class TestOnlyIncoming:
    """⚠ Работа — то, что сказал **он**."""

    async def test_our_own_reply_is_not_work(self, girl):
        """Наша реплика ответом не является, а лежит в той же таблице."""
        await _said(girl, 'quiet', direction='out')

        assert await _senders(girl) == []

    async def test_a_transferred_invite_is_not_work(self, girl):
        """⚠⚠ И перенесённый инвайт тоже: он ложится в переписку исходящим
        (`chat.chat_invites_to_history`), и без проверки направления звёздочка
        уходила бы всякому, кого мы просто позвали."""
        await _said(girl, 'invited', direction='out')
        await _said(girl, 'answered', direction='in')

        assert await _senders(girl) == ['answered']


class TestEmpty:
    """Пусто — не беда."""

    async def test_nobody_wrote(self, girl):
        assert await _senders(girl) == []
