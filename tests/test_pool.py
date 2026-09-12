"""Пулы анкеты: черновики уходят вместе с ней из сети.

Проверяется на живой базе, а не на подделке: вопрос здесь — что остаётся в
таблицах после ухода анкеты, и с поддельной базой проверялась бы подделка.

⚠ **Номера свои, выдуманные** — и у анкет, и у сайта. Пулы общие, и тест, взявший
живую анкету, стёр бы настоящие письма. Всё заведённое убирается в `finally`.

Наблюдалось живьём и этим же и чинится: пул рассылки Ladadate был полон —
пятьдесят писем, **все** от анкет не в сети, — и круг не мог добрать ни одного
письма тем трём девушкам, что были на смене.
"""

import json
import time

import pytest

from mysql_.mysql_ import mysql_get_db_async
from src.dating import dating_online
from src.dating.dating_online import DATING_ONLINE_FRESH_SEC
from src.dating.dating_pool import (DATING_POOL_TABLES, dating_pool_drop,
                                    dating_pool_sweep, dating_pool_sweep_away)

pytestmark = pytest.mark.mail

SITE = 999_779
GONE = 999_7791      # анкета, которая ушла из сети
HERE = 999_7792      # анкета, которая на смене

# Собеседники: один сидит в чате, второй уже ушёл.
HERE_UID = '999779001'
AWAY_UID = '999779002'
SENT_UID = '999779003'   # ⚠ свой: ключ пула — пара «анкета + собеседник»


@pytest.fixture
async def pools():
    """Две анкеты — ушедшая и оставшаяся — и по черновику каждой в трёх пулах.

    Плюс **отправленное** письмо ушедшей: на нём и проверяется, что история не
    трогается.
    """
    async with mysql_get_db_async() as db:
        for profile_id, status in ((GONE, 'offline'), (HERE, 'online')):
            await db.execute(
                'INSERT INTO dating_profile (id, site_id, login, password, role, status,'
                '                            enabled)'
                " VALUES (%s, %s, %s, 'x', 'lady', %s, 1)"
                ' ON DUPLICATE KEY UPDATE status = VALUES(status)',
                (profile_id, SITE, f'pool{profile_id}@test', status))

        for table in DATING_POOL_TABLES:
            for profile_id in (GONE, HERE):
                await db.execute(
                    f'INSERT INTO {table} (site_id, profile_id, uid, name, body, status)'
                    " VALUES (%s, %s, 'u1', 'Кто-то', 'текст', 'draft')",
                    (SITE, profile_id))

            await db.execute(
                f'INSERT INTO {table} (site_id, profile_id, uid, name, body, status)'
                " VALUES (%s, %s, 'u2', 'Кто-то', 'ушедшее', 'sent')", (SITE, GONE))

        await db.connection.commit()

    yield

    async with mysql_get_db_async() as db:
        for table in DATING_POOL_TABLES:
            await db.execute(f'DELETE FROM {table} WHERE site_id = %s', (SITE,))
        await db.execute('DELETE FROM dating_profile WHERE site_id = %s', (SITE,))
        await db.connection.commit()


class _FakeRedis:
    """Redis настолько, насколько его читает снимок ленты."""

    def __init__(self):
        self.value = None

    def get(self, key):
        return self.value


@pytest.fixture
def redis(monkeypatch):
    """Подменяет снимок «кто в сети». Список ставит сам тест — через `put`."""
    fake = _FakeRedis()
    monkeypatch.setattr(dating_online, 'redis_conn_get', lambda: fake)

    def put(uids, age_sec=5):
        fake.value = json.dumps({'at': time.time() - age_sec,
                                 'people': [{'uid': u} for u in uids]})

    fake.put = put

    return fake


@pytest.fixture
async def invites():
    """Два черновика инвайта от анкеты на смене: один адресат в сети, второй ушёл.

    Плюс ушедший инвайт и письмо рассылки тому же ушедшему — на них проверяется,
    чего чистка не трогает.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_profile (id, site_id, login, password, role, status,'
            '                            enabled)'
            " VALUES (%s, %s, %s, 'x', 'lady', 'online', 1)"
            ' ON DUPLICATE KEY UPDATE status = VALUES(status)',
            (HERE, SITE, f'pool{HERE}@test'))

        for uid in (HERE_UID, AWAY_UID):
            await db.execute(
                'INSERT INTO dating_invite (site_id, profile_id, uid, name, body, status)'
                " VALUES (%s, %s, %s, 'Кто-то', 'привет', 'draft')", (SITE, HERE, uid))

        await db.execute(
            'INSERT INTO dating_invite (site_id, profile_id, uid, name, body, status)'
            " VALUES (%s, %s, %s, 'Кто-то', 'ушло', 'sent')", (SITE, HERE, SENT_UID))
        await db.execute(
            'INSERT INTO dating_mailing (site_id, profile_id, uid, name, body, status)'
            " VALUES (%s, %s, %s, 'Кто-то', 'письмо', 'draft')", (SITE, HERE, AWAY_UID))
        await db.connection.commit()

    yield

    async with mysql_get_db_async() as db:
        for table in DATING_POOL_TABLES:
            await db.execute(f'DELETE FROM {table} WHERE site_id = %s', (SITE,))
        await db.execute('DELETE FROM dating_profile WHERE site_id = %s', (SITE,))
        await db.connection.commit()


class TestDrop:
    """Уход анкеты: её черновики стираются во всех трёх пулах."""

    async def test_drafts_of_the_profile_are_gone(self, pools):
        got = await dating_pool_drop(GONE, 'смена кончилась')

        assert got['total'] == len(DATING_POOL_TABLES)
        assert await _drafts(GONE) == 0

    async def test_the_other_profile_keeps_hers(self, pools):
        """⚠ Стираем **её** черновики, а не пул сайта.

        Пул общий, и уход одной девушки не повод выбрасывать письма остальных: они
        уйдут, как только до них дойдёт очередь отправки.
        """
        await dating_pool_drop(GONE)

        assert await _drafts(HERE) == len(DATING_POOL_TABLES)

    async def test_sent_letters_stay(self, pools):
        """⚠ Отправленное — история, и на ней держатся пределы частоты.

        `favorite_mail_per_day` и `favorite_mail_gap` считают по ушедшему: сотри
        его, и вернувшаяся анкета написала бы тому же человеку второй раз подряд.
        """
        await dating_pool_drop(GONE)

        assert await _sent(GONE) == len(DATING_POOL_TABLES)


class TestSweep:
    """Круг проверяет пул перед набором: черновиков ушедших в нём быть не должно."""

    async def test_sweep_clears_only_the_offline_ones(self, pools):
        """Главное условие: в сети — остаётся, не в сети — уходит.

        Это и есть та поломка, ради которой всё написано: пул сайта был занят
        письмами тех, кому их уже некому отправить.
        """
        total = await dating_pool_sweep(SITE)

        assert total == len(DATING_POOL_TABLES)
        assert await _drafts(GONE) == 0
        assert await _drafts(HERE) == len(DATING_POOL_TABLES)

    async def test_sweep_does_not_touch_sent(self, pools):
        await dating_pool_sweep(SITE)

        assert await _sent(GONE) == len(DATING_POOL_TABLES)

    async def test_sweep_is_idempotent(self, pools):
        """Второй заход находить уже нечего — иначе круг писал бы в лог на каждый тик."""
        await dating_pool_sweep(SITE)

        assert await dating_pool_sweep(SITE) == 0

    async def test_replies_are_not_a_pool_here(self):
        """⚠ `dating_mail_reply` в списке нет, и это решение, а не забывчивость.

        Ответ ждёт живой человек, написавший письмо; «девушка вышла из сети» — не
        повод оставить его без ответа. Круг отправки дождётся её следующей смены, а
        потолок сайта ответы и не занимают: они набираются по входящим письмам.
        """
        assert 'dating_mail_reply' not in DATING_POOL_TABLES


class TestSweepAway:
    """Вторая сторона: черновик инвайта тому, кого **уже нет** в чате.

    Реплика зовёт сидящего в чате сейчас. Ушёл — звать некого, а место в пуле она
    держит: наблюдалось живьём, 50 черновиков при 27 годных, и круг перестал
    добирать новых.
    """

    async def test_the_one_who_left_is_gone(self, invites, redis):
        redis.put([HERE_UID])

        assert await dating_pool_sweep_away(SITE) == 1
        assert await _invite_uids() == [HERE_UID]

    async def test_the_one_still_in_the_feed_stays(self, invites, redis):
        redis.put([HERE_UID, AWAY_UID])

        assert await dating_pool_sweep_away(SITE) == 0
        assert sorted(await _invite_uids()) == sorted([AWAY_UID, HERE_UID])

    async def test_no_snapshot_touches_nothing(self, invites, redis):
        """⚠ «Не знаем» — не «ушёл». Неснятая лента не повод терять готовый текст."""
        assert await dating_pool_sweep_away(SITE) == 0
        assert len(await _invite_uids()) == 2

    async def test_a_stale_snapshot_touches_nothing(self, invites, redis):
        """Тот же довод: несвежий снимок ничего не знает про сейчас."""
        redis.put([HERE_UID], age_sec=DATING_ONLINE_FRESH_SEC + 60)

        assert await dating_pool_sweep_away(SITE) == 0

    async def test_sent_invites_stay(self, invites, redis):
        """Ушедшее — история, а не пул: её не чистят никогда."""
        redis.put([])

        await dating_pool_sweep_away(SITE)

        async with mysql_get_db_async() as db:
            await db.execute("SELECT COUNT(*) AS n FROM dating_invite"
                             " WHERE site_id = %s AND status = 'sent'", (SITE,))
            assert int((await db.fetchone())['n']) == 1

    def test_the_invite_round_sweeps_before_it_counts(self):
        """Иначе круг увидит пул полным — тем самым мёртвым текстом — и уйдёт."""
        import inspect

        from src.dating import dating_invite_round

        code = inspect.getsource(dating_invite_round)

        assert code.index('await dating_pool_sweep_away') \
            < code.index('await dating_invite_pool_left')

    async def test_letters_are_not_touched(self, invites, redis):
        """⚠ Только инвайты. Письмо ждёт в почте — уход адресата ему не помеха."""
        redis.put([])

        await dating_pool_sweep_away(SITE)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM dating_mailing'
                             ' WHERE site_id = %s', (SITE,))
            assert int((await db.fetchone())['n']) == 1


class TestRoundsSweepFirst:
    """⚠ Круги чистят пул **до** того, как считают в нём место.

    Порядок здесь и есть исправление: посчитай круг сперва, он увидел бы полный пул
    и ушёл, ничего не сделав, — ровно то, что и наблюдалось.
    """

    @pytest.mark.parametrize('module, counter', [
        ('src.dating.dating_mailing_round', '_pool_size'),
        ('src.dating.dating_invite_round', 'dating_invite_pool_left'),
        ('src.dating.dating_favorite_mail_round', 'dating_favorite_mail_pool_left'),
    ])
    def test_sweep_comes_before_the_count(self, module, counter):
        import importlib
        import inspect

        code = inspect.getsource(importlib.import_module(module))

        assert 'dating_pool_sweep' in code, 'круг не чистит пул вовсе'
        assert code.index('await dating_pool_sweep') < code.index(f'await {counter}'), \
            'пул считается раньше чистки — круг увидит место занятым'


async def _invite_uids() -> list[str]:
    """Кому остались черновики инвайтов на выдуманном сайте."""
    async with mysql_get_db_async() as db:
        await db.execute("SELECT uid FROM dating_invite"
                         " WHERE site_id = %s AND status = 'draft'", (SITE,))

        return [str(row['uid']) for row in await db.fetchall() or []]


async def _drafts(profile_id: int) -> int:
    """Сколько черновиков анкеты лежит во всех пулах вместе."""
    return await _count(profile_id, 'draft')


async def _sent(profile_id: int) -> int:
    return await _count(profile_id, 'sent')


async def _count(profile_id: int, status: str) -> int:
    total = 0
    async with mysql_get_db_async() as db:
        for table in DATING_POOL_TABLES:
            await db.execute(f'SELECT COUNT(*) AS n FROM {table}'
                             ' WHERE profile_id = %s AND status = %s',
                             (profile_id, status))
            total += int((await db.fetchone())['n'])

    return total
