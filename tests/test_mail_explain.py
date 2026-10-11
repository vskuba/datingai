"""Окно «почему этой паре не пишут письма»: считает ли оно то же, что круги.

## ⚠⚠ Главное здесь — сторож согласия, а не проверка полей

Занятий два, правил у них шестнадцать, и почти все невидимы: человек видит
молчащую переписку и не знает, упёрлась ли пара в дневной предел получателя, не
вышла ли пауза отправительницы, не закрылся ли человек или просто не идёт смена.
Окно отвечает на это — но ровно до тех пор, пока отвечает **теми же правилами**,
что и круги.

Отбор кругов (`favorite_mail._quiet`, `mail_reply._to_answer`) идёт по всем парам
сайта одним запросом; окно спрашивает про одну пару и выражено иначе. Свести их в
один запрос не выходит, и значит остаётся риск: правка в одном месте, молчание в
другом — и окно объясняет занятие, которого нет.

Поэтому `TestTheyAgree` гоняет **обе дороги по одним данным**. Та же грабля в
проекте уже случалась дважды — с `initiative_why` и со значком потолка.

⚠ База настоящая, сайт — стенд сюиты, ручки зовутся в процессе: тому же правилу
следуют `test_initiative_explain.py` и `test_favorite_mail.py`.

⚠⚠ **Redis подменяется, а не используется живым.** С хоста он недоступен вовсе
(`REDIS_URL` — имя сети compose, порт наружу не опубликован), а весь код глотает
его сбой молча: на живом Redis проверки «он в сети» и «остуда» зеленели бы, ничего
не проверяя.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_explain import mail_explain

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


# ── Посев ────────────────────────────────────────────────────────────────────

async def _said_in(profile_id: int, uid: str, *, minutes_ago: int = 60,
                   body: str = 'his letter') -> int:
    """Его входящее письмо. Возвращает номер строки `mail_in`."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in'
            ' (site_id, profile_id, message_id, uid, name, body, date_post)'
            ' VALUES (%s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
            (STAND_SITE, int(profile_id), int(uuid.uuid4().int % 10 ** 9),
             str(uid), 'He', body, int(minutes_ago)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _star(profile_id: int, uid: str, state: str = 'done') -> None:
    """Звёздочка: пара в избранных."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            ' VALUES (%s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE state = VALUES(state)',
            (STAND_SITE, int(profile_id), str(uid), str(state)))
        await db.connection.commit()


async def _block(profile_id: int, uid: str, *, channel: str = 'all',
                 reason: str = 'он закрылся от вас') -> None:
    """Запрет «не пишем» на пару."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO block'
            ' (site_id, profile_id, uid, name, reason, source, channel)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (STAND_SITE, int(profile_id), str(uid), 'He', reason, 'hand',
             str(channel)))
        await db.connection.commit()


async def _fav_sent(profile_id: int, uid: str, *, minutes_ago: int = 10) -> None:
    """Ушедшее приставание — им считаются оба предела и последняя реплика."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite_mail'
            ' (site_id, profile_id, uid, name, body, status, sent_at)'
            " VALUES (%s, %s, %s, 'He', 'hi', 'sent',"
            '         NOW() - INTERVAL %s MINUTE)',
            (STAND_SITE, int(profile_id), str(uid), int(minutes_ago)))
        await db.connection.commit()


# ── Проверки ─────────────────────────────────────────────────────────────────

class TestKnown:
    """Знает ли окно эту пару вообще."""

    async def test_unknown_profile_is_not_an_error(self):
        """Анкеты нет — пустой ответ, а не исключение.

        ⚠ Человек открывает окно у разговора, которого в наших таблицах может не
        быть вовсе. Падение здесь нарисовалось бы красным там, где всё в порядке;
        в `known: false` пустоту превращает ручка.
        """
        assert await mail_explain(0, 'nobody') == {}
        assert await mail_explain(999_999_999, 'nobody') == {}

    async def test_pair_without_letters_is_known(self, girl):
        """Анкета есть, писем нет — пара известна, просто пустая.

        ⚠ Отсутствие переписки **не** делает пару неизвестной: приставание ей
        законно (звёздочка сама означает, что разговор был), и окно обязано это
        показать, а не отговориться незнанием.
        """
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['known'] is True
        assert got['site_id'] == STAND_SITE
        assert got['last']['at'] is None
        assert got['last']['side'] == ''


class TestCommon:
    """Общее для обоих занятий: последняя реплика, запрет, остуда."""

    async def test_last_word_is_his(self, girl):
        """Его письмо — последняя реплика, и сторона названа."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, minutes_ago=30)

        got = await mail_explain(girl, uid)

        assert got['last']['side'] == 'him'
        assert got['last']['at'] is not None

    async def test_our_letter_is_the_last_word(self, girl):
        """Наше ушедшее письмо позже его — последнее слово наше.

        ⚠⚠ Считается **по всей почте**, а не по входящим: смотри окно только
        `mail_in`, затихшим выглядел бы всякий, кому мы писали последними.
        """
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, minutes_ago=120)
        await _fav_sent(girl, uid, minutes_ago=10)

        got = await mail_explain(girl, uid)

        assert got['last']['side'] == 'her'

    async def test_block_is_seen_with_mark_and_channel(self, girl):
        """Запрет виден приметой и каналом — по каналу решается охват."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _block(girl, uid, channel='mail',
                     reason='вы не подходите ему по возрасту')

        got = await mail_explain(girl, uid)

        assert got['block']['is'] is True
        assert got['block']['channel'] == 'mail'
        assert 'возраст' in got['block']['mark']

    async def test_no_block_is_silent(self, girl):
        """Запрета нет — поле пустое, а не отсутствующее."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['block'] == {'is': False, 'mark': '', 'channel': ''}
