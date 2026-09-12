"""Круг опроса: колонка «В сети» свежая без открытой страницы.

Проверяется **отбор и запись**, а не поход на сайт: сам опрос
(`dating_presence_probe`) подменён — настоящий постучался бы на ladadate.com.

⚠ Отбор круга подменяется, а не флаг в базе: тот же круг крутит живой воркер в
соседнем контейнере, по той же базе (`testing.md`). Оставь мы отбор настоящим,
воркер опрашивал бы наши стендовые анкеты вперёд теста.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import dating_status, dating_status_round
from src.dating.dating_status import dating_status_probe_site
from src.dating.dating_status_round import dating_status_round_run

pytestmark = pytest.mark.profile

STAND_SITE = 999_343

# Ответ поддельного опроса: сайт признал вход и насчитал два непрочитанных.
ONLINE = {'status': 'online', 'unread': 2, 'error': '', 'ms': 12}
OFFLINE = {'status': 'offline', 'unread': -1, 'error': 'сайт не признал вход', 'ms': 9}


@pytest.fixture(autouse=True)
def ready(monkeypatch):
    """Присутствие у стенда «настроено»: настройка живёт у сайта, а его нет."""
    async def yes(presence=None, site_id=0):
        return True

    monkeypatch.setattr(dating_status, 'dating_presence_ready', yes)


@pytest.fixture
def probe(monkeypatch):
    """Поддельный опрос: что ответить и кого спрашивали."""
    asked = []
    answer = {'row': ONLINE}

    async def _probe(profile, presence=None):
        asked.append(int(profile['id']))

        return dict(answer['row'])

    monkeypatch.setattr(dating_status, 'dating_presence_probe', _probe)

    return {'asked': asked, 'answer': answer}


@pytest.fixture
def quiet(monkeypatch):
    """Молчащий журнал «Ошибок»: он ходит к соседу, а сюита туда не ходит."""
    said = []

    async def _write(source, message, session_id='', context=None):
        said.append(message)

    monkeypatch.setattr(dating_status, 'rhapsody_api_error_write', _write)

    return said


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда со снятым входом: без `storage_state` опрашивать нечем."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'ask-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE dating_profile SET storage_state = %s,"
                         " status = 'offline' WHERE id = %s",
                         ('{"cookies": []}', profile_id))
        await db.connection.commit()

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


class TestProbeSite:
    """Опрос сайта: кого спрашиваем и что пишем."""

    async def test_online_lands_in_the_card(self, girl, probe, quiet):
        got = await dating_status_probe_site(STAND_SITE)

        assert girl in probe['asked']
        assert got['ready'] and got['online'] >= 1
        assert (await _row(girl))['status'] == 'online'

    async def test_seen_moves_only_on_a_confirmed_login(self, girl, probe, quiet):
        """⚠ `last_seen_at` — «когда сайт признал учётку», а не «когда стучались».

        Сдвинь его неудачный опрос, и по колонке «Вход» живая анкета перестанет
        отличаться от отвалившейся неделю назад.
        """
        await dating_status_probe_site(STAND_SITE)
        seen = (await _row(girl))['last_seen_at']
        assert seen is not None, 'подтверждённый вход метку не поставил'

        probe['answer']['row'] = OFFLINE
        await dating_status_probe_site(STAND_SITE)

        assert (await _row(girl))['status'] == 'offline'
        assert (await _row(girl))['last_seen_at'] == seen, 'неудача сдвинула метку'

    async def test_unread_minus_one_lands_as_zero(self, girl, probe, quiet):
        """«Не спрашивали» — это не «минус одно письмо»: колонка про количество."""
        probe['answer']['row'] = OFFLINE

        await dating_status_probe_site(STAND_SITE)

        assert (await _row(girl))['unread'] == 0

    async def test_profile_without_a_session_is_not_asked(self, girl, probe, quiet):
        """Без `storage_state` удар слать некуда."""
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_profile SET storage_state = NULL WHERE id = %s',
                             (girl,))
            await db.connection.commit()

        await dating_status_probe_site(STAND_SITE)

        assert girl not in probe['asked']

    async def test_disabled_profile_is_not_asked(self, girl, probe, quiet):
        """«Не ведём» значит не ведём совсем: ходить на сайт под снятой с ведения
        учёткой панель права не имеет."""
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_profile SET enabled = 0 WHERE id = %s', (girl,))
            await db.connection.commit()

        await dating_status_probe_site(STAND_SITE)

        assert girl not in probe['asked']

    async def test_site_without_presence_is_skipped(self, girl, probe, monkeypatch):
        """Настройки нет — опрашивать нечем, и объявлять анкету сломанной за это
        нельзя: `ready` отвечает «не спрашивали», а не «ошибка»."""
        async def no(presence=None, site_id=0):
            return False

        monkeypatch.setattr(dating_status, 'dating_presence_ready', no)

        got = await dating_status_probe_site(STAND_SITE)

        assert got == {'ready': False, 'checked': 0, 'online': 0, 'profiles': []}
        assert not probe['asked']


class TestGoneNote:
    """Запись в «Ошибки» — только про смену состояния."""

    async def test_leaving_the_net_is_written_once(self, girl, probe, quiet):
        """⚠ Не каждый неудачный заход: круг ходит раз в минуту, и сотня анкет
        залила бы вкладку одним и тем же."""
        await dating_status_probe_site(STAND_SITE)          # стала online
        probe['answer']['row'] = OFFLINE
        await dating_status_probe_site(STAND_SITE)          # ушла — строка
        await dating_status_probe_site(STAND_SITE)          # всё ещё нет — молчим

        assert len([one for one in quiet if 'ушла из сети' in one]) == 1


class TestRound:
    """Круг поверх опроса: кому пора."""

    async def test_round_asks_and_counts(self, girl, probe, quiet, monkeypatch):
        _only_stand(monkeypatch)
        dating_status_round._asked.clear()

        got = await dating_status_round_run()

        assert got['sites'] == 1 and got['checked'] >= 1
        assert girl in probe['asked']

    async def test_second_pass_in_a_row_waits(self, girl, probe, quiet, monkeypatch):
        """Срок сайта (`probe_every`) — не пожелание: без него круг спрашивал бы
        сайт каждые пятнадцать секунд."""
        _only_stand(monkeypatch)
        monkeypatch.setattr(dating_status_round, '_every', _answer(600))
        dating_status_round._asked.clear()

        assert (await dating_status_round_run())['checked'] >= 1
        assert (await dating_status_round_run())['checked'] == 0

    async def test_site_without_probeable_profiles_is_not_visited(self, monkeypatch):
        """Отбор идёт **от анкет**: сайтов у соседа десятки, работают один-два."""
        sites = await dating_status_round._sites()

        assert 999_781 not in sites, 'сайт без анкет попал в обход'


# ── Вспомогательное ──────────────────────────────────────────────────────────

def _only_stand(monkeypatch):
    """Сужает обход круга до стенда: тот же круг крутит живой воркер."""
    async def ours():
        return [STAND_SITE]

    monkeypatch.setattr(dating_status_round, '_sites', ours)


def _answer(value):
    async def _got(site_id):
        return value

    return _got


async def _row(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT status, unread, last_seen_at FROM dating_profile'
                         ' WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    return dict(row)
