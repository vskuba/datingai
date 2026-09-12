"""Круг отправки инвайтов: кого берёт, когда молчит и чем ограничен.

⚠ **Ни одна реплика наружу не уходит.** Сама отправка (`dating_invite_send_one`)
подменена: настоящая ушла бы живому человеку в чат. Проверяется здесь **отбор** —
кого круг берёт и почему пропускает; что отправитель делает с ответом сайта,
проверяет `tests/test_invite.py` на кнопке, а дорога у них одна.

⚠ Сайт и анкеты — стендовые (`999_343`): круг ходит по всем сайтам, и живой номер
означал бы работу по настоящим анкетам.
"""

import time
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import dating_invite_send_round
from src.dating.dating_invite_send_round import (DATING_INVITE_SEND_EVERY_MIN,
                                                 dating_invite_send_round_run)

pytestmark = pytest.mark.chat

STAND_SITE = 999_343

# Пустой стенд для проверок самого отбора: ни анкет, ни пула.
BARE_SITE = 999_779

# Смена на все сутки: круг сверяет часы, а тест не про часы.
ALWAYS = '[{"from": "00:00", "to": "23:45"}]'


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    """Снимает паузу между репликами: она про живой сайт, а не про отбор."""
    dating_invite_send_round._sent_at.clear()
    monkeypatch.setattr(dating_invite_send_round, '_every', _answer(0))

    yield

    dating_invite_send_round._sent_at.clear()


@pytest.fixture
def site(monkeypatch):
    """⚠⚠ Сужает **отбор** круга до стенда — и не трогает флаг в базе.

    Правило общее (`testing.md`): тест, зовущий круг, подменяет его отбор, а не
    только действие. Здесь у него есть вторая, более острая причина: **этот же
    круг крутит живой воркер в соседнем контейнере**, по той же базе. Подними мы
    `invite_send_on` у строки, воркер увидел бы её раньше теста, взял его же
    черновики и попробовал отправить их по-настоящему — а его отправку никакой
    `monkeypatch` не подменяет: это другой процесс.

    Поймано флейком: `test_one_per_pass` падал через раз, потому что одну из трёх
    реплик успевал забрать воркер.

    Отбор возвращаем прямо, минуя базу: строка сайта так и остаётся `is_gone = 1`
    с опущенным флагом, и живому кругу её не видно вовсе.
    """
    async def ours():
        return [{'site_id': STAND_SITE, 'invite_schedule': ALWAYS}]

    monkeypatch.setattr(dating_invite_send_round, '_sites', ours)

    return STAND_SITE


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда, которая **в сети**: без этого круг её не берёт."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'inv-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    await _status(profile_id, 'online')

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_invite WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM dating_profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


@pytest.fixture
def sent(monkeypatch):
    """Перехваченная отправка: список ушедших вместо похода на сайт."""
    got = []

    async def _send(invite):
        got.append(dict(invite))

        return {'ok': True, 'error': ''}

    monkeypatch.setattr(dating_invite_send_round, 'dating_invite_send_one', _send)

    return got


class TestPicks:
    """Кого круг берёт."""

    async def test_draft_of_an_online_girl_goes(self, site, girl, sent):
        await _draft(girl)

        got = await dating_invite_send_round_run()

        assert got['sent'] == 1 and len(sent) == 1

    async def test_one_per_pass(self, site, girl, sent):
        """⚠ По одной за заход: пул, ушедший залпом, — это робот, чем он и является."""
        for _ in range(3):
            await _draft(girl)

        assert (await dating_invite_send_round_run())['sent'] == 1
        assert len(sent) == 1

    async def test_the_priority_one_jumps_the_queue(self, site, girl, sent,
                                                    monkeypatch):
        """⚠ Отметка на карточке — про очередь **отправки** тоже, а не только набора.

        Попасть в пул и уйти из пула — разные сроки: при паузе в минуту приоритетная
        реплика ждала бы полсотни чужих час, а человек в чате десять минут.
        """
        old = await _draft_uid(girl, 'Старый', '2026-09-01 10:00:00')
        vip = await _draft_uid(girl, 'Важный', '2026-09-05 10:00:00')
        monkeypatch.setattr(dating_invite_send_round, 'dating_online_check',
                            lambda site_id, uid: {'online': True, 'known': True})

        await _priority(vip)
        try:
            await dating_invite_send_round_run()
        finally:
            await _priority_drop(vip)

        assert [one['uid'] for one in sent] == [vip], f'ушёл {old}, а не приоритетный'

    async def test_oldest_goes_first(self, site, girl, sent):
        """Реплика сочинялась под того, кто был в чате тогда: чем дольше лежит,
        тем меньше от неё толку."""
        first = await _draft(girl, when='2026-09-01 10:00:00')
        await _draft(girl, when='2026-09-02 10:00:00')

        await dating_invite_send_round_run()

        assert sent[0]['id'] == first


class TestSkips:
    """Кого не берёт — и это главное, потому что реплика уходит живому человеку."""

    async def test_offline_girl_writes_nothing(self, site, girl, sent):
        """Реплика уходит её сессией, а ответ читать некому, если её нет."""
        await _draft(girl)
        await _status(girl, 'offline')

        assert (await dating_invite_send_round_run())['sent'] == 0
        assert not sent

    async def test_disabled_girl_writes_nothing(self, site, girl, sent):
        """«Не ведём» значит не ведём совсем."""
        await _draft(girl)
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_profile SET enabled = 0 WHERE id = %s', (girl,))
            await db.connection.commit()

        assert (await dating_invite_send_round_run())['sent'] == 0

    async def test_row_with_an_error_waits_for_a_human(self, site, girl, sent):
        """⚠ Взявшись за неудачную строку снова, круг зациклился бы на отказе
        сайта («слишком часто») ровно тогда, когда тот просит остановиться."""
        await _draft(girl, error='собеседника нет в чате')

        assert (await dating_invite_send_round_run())['sent'] == 0
        assert not sent

    async def test_outside_the_shift_nothing_goes(self, girl, sent, monkeypatch):
        """Реплика в четыре утра выдаёт робота вернее любого текста.

        ⚠ Смена считается **от текущей минуты**, а не вписана числом: тест с
        постоянными часами зеленел бы или краснел смотря по времени прогона —
        ровно тот флейк, что уже описан у почтовых тестов.
        """
        hour = (time.gmtime().tm_hour + 6) % 24
        away = '[{"from": "%02d:00", "to": "%02d:15"}]' % (hour, hour)

        async def dead_hours():
            return [{'site_id': STAND_SITE, 'invite_schedule': away}]

        monkeypatch.setattr(dating_invite_send_round, '_sites', dead_hours)
        await _draft(girl)

        assert (await dating_invite_send_round_run())['sent'] == 0
        assert not sent

    async def test_day_cap_stops_the_round(self, site, girl, sent, monkeypatch):
        """Потолок считается **по факту отправки**, а не по попыткам."""
        monkeypatch.setattr(dating_invite_send_round, '_max_day', _answer(0))
        await _draft(girl)

        assert (await dating_invite_send_round_run())['sent'] == 0
        assert not sent


class TestSelection:
    """Сам отбор сайтов — на настоящем `_sites`, без подмены.

    ⚠ Стенд здесь **свой и пустой** (`999_779`): у него нет ни пула, ни анкет, и
    живой воркер, увидев поднятый флаг, не найдёт что отправить. Проверять флаг на
    стенде с черновиками значило бы отдать их ему.
    """

    async def test_site_without_the_switch_is_not_taken(self):
        await _flag(BARE_SITE, on=False)

        assert BARE_SITE not in [int(row['site_id'])
                                 for row in await dating_invite_send_round._sites()]

    async def test_site_with_the_switch_is_taken(self):
        """⚠ Поднятый `invite_send_on` — это и есть решение «наружу писать»."""
        try:
            await _flag(BARE_SITE, on=True)

            assert BARE_SITE in [int(row['site_id'])
                                 for row in await dating_invite_send_round._sites()]
        finally:
            await _flag(BARE_SITE, on=False)

    async def test_gone_site_is_not_taken(self):
        """Закладку убрали у соседа — писать с неё некому."""
        try:
            await _flag(BARE_SITE, on=True, gone=True)

            assert BARE_SITE not in [int(row['site_id'])
                                     for row in await dating_invite_send_round._sites()]
        finally:
            await _flag(BARE_SITE, on=False)


class TestPause:
    """Пауза между репликами."""

    async def test_second_pass_in_a_row_waits(self, site, girl, sent, monkeypatch):
        monkeypatch.setattr(dating_invite_send_round, '_every', _answer(600))
        await _draft(girl)
        await _draft(girl)

        assert (await dating_invite_send_round_run())['sent'] == 1
        assert (await dating_invite_send_round_run())['sent'] == 0, \
            'вторая реплика ушла, не дождавшись паузы'

    async def test_hard_floor_beats_the_setting(self):
        """⚠ Настройку правят руками, и «5» превратило бы смену в очередь из
        сотен реплик в час — то самое, от чего пауза и заведена."""
        dating_invite_send_round._sent_at.clear()

        assert DATING_INVITE_SEND_EVERY_MIN >= 20


# ── Вспомогательное ──────────────────────────────────────────────────────────

def _answer(value):
    """Подменённая настройка: всегда одно и то же число."""
    async def _got(site_id):
        return value

    return _got


async def _flag(site_id: int, on: bool, gone: bool = False) -> None:
    """Выключатель отправки у стендового сайта."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_site (site_id, name, invite_send_on, invite_schedule, is_gone)'
            ' VALUES (%s, %s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE invite_send_on = VALUES(invite_send_on),'
            '                         is_gone = VALUES(is_gone)',
            (int(site_id), 'Стенд DatingAi: отбор', 1 if on else 0, ALWAYS,
             1 if (gone or not on) else 0))
        await db.connection.commit()


async def _status(profile_id: int, status: str) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET status = %s WHERE id = %s',
                         (status, int(profile_id)))
        await db.connection.commit()


async def _draft(profile_id: int, error: str = '', when: str = '') -> int:
    """Реплика в пуле. Возвращает её номер."""
    async with mysql_get_db_async() as db:
        if when:
            await db.execute(
                'INSERT INTO dating_invite (site_id, profile_id, uid, name, body,'
                " status, error, created_at) VALUES (%s, %s, %s, %s, %s, 'draft', %s, %s)",
                (STAND_SITE, int(profile_id), f'u{uuid.uuid4().hex[:8]}', 'Frank',
                 'Привет!', error, when))
        else:
            await db.execute(
                'INSERT INTO dating_invite (site_id, profile_id, uid, name, body,'
                " status, error) VALUES (%s, %s, %s, %s, %s, 'draft', %s)",
                (STAND_SITE, int(profile_id), f'u{uuid.uuid4().hex[:8]}', 'Frank',
                 'Привет!', error))
        made = db.lastrowid
        await db.connection.commit()

    return int(made)


class TestFeed:
    """Лента событий: «От → кому» видно с любой страницы раздела.

    ⚠ Журнал анкеты этого не заменяет: его открывают у конкретной девушки и
    постфактум, а вопрос «панель сейчас пишет живым людям?» задают о сайте
    целиком и в тот же момент.
    """

    async def test_sent_invite_shows_who_wrote_whom(self, girl, monkeypatch):
        from src.dating import dating_invite_send
        from src.dating.dating_feed import dating_feed_read

        said = []
        monkeypatch.setattr(dating_invite_send, 'dating_feed_write',
                            lambda site_id, kind, text, profile_id=0: said.append(
                                (site_id, kind, text)) or '')
        monkeypatch.setattr(dating_invite_send, 'dating_send_message',
                            _ok_send())
        monkeypatch.setattr(dating_invite_send, 'rhapsody_api_site_get', _site())
        await _name(girl, 'Diana')
        invite_id = await _draft(girl)

        got = await dating_invite_send.dating_invite_send_one(
            await _invite_row(invite_id))

        assert got['ok']
        assert said, 'отправка прошла молча — в ленте ничего'
        site_id, kind, text = said[0]
        assert kind == 'invite' and site_id == STAND_SITE
        assert text == 'Инвайт: Diana → Frank', text

    async def test_girl_without_a_name_shows_her_login(self, girl, monkeypatch):
        """Пустая левая половина («Инвайт:  → Frank») читалась бы как сбой."""
        from src.dating import dating_invite_send

        said = []
        monkeypatch.setattr(dating_invite_send, 'dating_feed_write',
                            lambda site_id, kind, text, profile_id=0: said.append(text) or '')
        monkeypatch.setattr(dating_invite_send, 'dating_send_message', _ok_send())
        monkeypatch.setattr(dating_invite_send, 'rhapsody_api_site_get', _site())
        invite_id = await _draft(girl)

        await dating_invite_send.dating_invite_send_one(await _invite_row(invite_id))

        assert said and said[0].startswith('Инвайт: inv-'), said

    async def test_failure_is_a_warning_in_the_feed(self, girl, monkeypatch):
        """«Пул полон, а ничего не уходит» иначе выглядит как вставший круг."""
        from src.dating import dating_invite_send

        said = []
        monkeypatch.setattr(dating_invite_send, 'dating_feed_write',
                            lambda site_id, kind, text, profile_id=0: said.append(
                                (kind, text)) or '')
        monkeypatch.setattr(dating_invite_send, 'rhapsody_api_site_get', _site())

        # ⚠ Отказ **стойкий**, а не «его нет в сети»: уход из чата состояние, оно
        # проходит само, в ленту не пишется и строку не глушит (см. `TestOffline…`).
        async def refused(profile, presence, uid, body):
            return {'ok': False, 'error': 'слишком часто'}

        monkeypatch.setattr(dating_invite_send, 'dating_send_message', refused)
        invite_id = await _draft(girl)

        got = await dating_invite_send.dating_invite_send_one(await _invite_row(invite_id))

        assert not got['ok']
        kind, text = said[0]
        assert kind == 'warn' and 'слишком часто' in text, text


def _ok_send():
    async def _send(profile, presence, uid, body):
        return {'ok': True}

    return _send


def _site():
    async def _got(site_id):
        return {'id': site_id, 'presence': '{}'}

    return _got


async def _name(profile_id: int, name: str) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET profile = %s WHERE id = %s',
                         ('{"name": "%s"}' % name, int(profile_id)))
        await db.connection.commit()


async def _invite_row(invite_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, uid, name, body'
                         ' FROM dating_invite WHERE id = %s', (int(invite_id),))
        row = await db.fetchone()

    return dict(row)


class TestOfflineIsNotAFailure:
    """«Его нет в сети» не глушит черновик и не тратит заход впустую.

    ⚠ Пул набирается раз в полчаса, а люди на сайте живут минутами: через час
    половина адресатов ушла. Поймано в первый же час работы круга — пул на 50
    черновиков, в сети из них 27, и круг пошёл заглушать остальные по одной в
    минуту причиной, которая проходит сама.
    """

    async def test_away_leaves_the_row_clean(self, girl, monkeypatch):
        from src.dating import dating_invite_send

        async def away(profile, presence, uid, body):
            return {'ok': False, 'offline': True}

        monkeypatch.setattr(dating_invite_send, 'dating_send_message', away)
        monkeypatch.setattr(dating_invite_send, 'rhapsody_api_site_get',
                            _site_answer())
        invite_id = await _draft(girl)

        got = await dating_invite_send.dating_invite_send_one(await _one(invite_id))
        row = await _one(invite_id)

        assert not got['ok'] and 'нет в сети' in got['error']
        assert row['status'] == 'draft'
        assert row['error'] == '', '«нет в сети» записали как отказ — строка заглушена'

    async def test_a_real_refusal_still_stops_it(self, girl, monkeypatch):
        """⚠ Обратное правило остаётся: стойкий отказ строку держит, иначе круг
        зациклится ровно тогда, когда сайт просит остановиться."""
        from src.dating import dating_invite_send

        async def refused(profile, presence, uid, body):
            return {'ok': False, 'error': 'слишком часто'}

        monkeypatch.setattr(dating_invite_send, 'dating_send_message', refused)
        monkeypatch.setattr(dating_invite_send, 'rhapsody_api_site_get',
                            _site_answer())
        invite_id = await _draft(girl)

        await dating_invite_send.dating_invite_send_one(await _one(invite_id))
        row = await _one(invite_id)

        assert row['status'] == 'draft' and row['error'] == 'слишком часто'

    async def test_round_skips_those_who_left(self, site, girl, sent, monkeypatch):
        """Круг берёт того, кто в ленте **сейчас**, а не самого старого подряд."""
        from src.dating import dating_invite_send_round as round_

        old_uid = await _draft_uid(girl, 'ушёл', when='2026-09-01 10:00:00')
        new_uid = await _draft_uid(girl, 'на месте', when='2026-09-02 10:00:00')

        monkeypatch.setattr(round_, 'dating_online_check',
                            lambda site_id, uid: {'online': str(uid) == new_uid,
                                                  'known': True, 'age_sec': 1})

        got = await round_._next(STAND_SITE)

        assert got and got['uid'] == new_uid, f'взяли ушедшего: {got}'
        assert old_uid != new_uid


def _site_answer():
    async def _got(site_id):
        return {'id': site_id, 'presence': '{}'}

    return _got


async def _one(invite_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, uid, name, body, status,'
                         ' error FROM dating_invite WHERE id = %s', (int(invite_id),))

        return dict(await db.fetchone())


async def _priority(uid: str) -> None:
    """Карточка собеседника с поднятой отметкой «Приоритет в рассылке»."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_companion (site_id, uid, name, priority)'
            " VALUES (%s, %s, 'Важный', 1)", (STAND_SITE, uid))
        await db.connection.commit()


async def _priority_drop(uid: str) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_companion WHERE site_id = %s AND uid = %s',
                         (STAND_SITE, uid))
        await db.connection.commit()


async def _draft_uid(profile_id: int, tag: str, when: str) -> str:
    uid = f'u{uuid.uuid4().hex[:8]}'
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_invite (site_id, profile_id, uid, name, body,'
            " status, error, created_at) VALUES (%s, %s, %s, %s, %s, 'draft', '', %s)",
            (STAND_SITE, int(profile_id), uid, tag, 'Привет!', when))
        await db.connection.commit()

    return uid
