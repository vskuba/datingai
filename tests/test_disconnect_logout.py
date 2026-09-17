"""Выход на сайте: ручкой, а сценарием — только если ручки нет.

⚠ **Что выход делает — вопрос к сайту.** У Ladadate `GET /signout` отвечает `302`
и `Set-Cookie: AUTH=; expires=вчера`, то есть гасит cookie **в браузере**, а
сессию на своей стороне оставляет живой: сохранённый `AUTH` после него работает
(проверено живьём). Настоящий выход с нашей стороны — стёртое состояние, и его
делает `disconnect_run` независимо от исхода запроса.
"""

import pytest

from src.dating import disconnect

pytestmark = pytest.mark.profile

SITE = 999_777
STATE = '{"cookies": [{"name": "AUTH", "value": "x", "domain": "stand.invalid"}]}'
OURS = {'base': 'https://stand.invalid',
        'logout': {'path': '/signout', 'method': 'GET'}}


@pytest.fixture
def profile():
    return {'id': 999_001, 'site_id': SITE, 'login': 'zz@example.com',
            'role': 'lady', 'storage_state': STATE}


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """Журнал анкеты в сюите не пишем: тест про выбор дороги, а не про запись."""
    async def _log(*args, **kwargs):
        return None

    monkeypatch.setattr(disconnect, 'log_write', _log)


def _config(value):
    async def _got(site_id):
        return value

    return _got


def _http(seen):
    async def _call(profile, presence, step):
        seen.append((presence.get('base'), step.get('path')))

        return True, ''

    return _call


class TestPicksTheHandle:
    """Ручка старше сценария."""

    async def test_our_setting_wins(self, profile, monkeypatch):
        """⚠ Шаг ищем в **нашей** настройке присутствия: выход — её часть, он про
        то же соединение и те же cookie. Соседская остаётся запасной."""
        seen = []
        monkeypatch.setattr('src.dating.heartbeat.heartbeat_config',
                            _config(OURS))
        monkeypatch.setattr(disconnect, '_logout_http', _http(seen))

        ok, error = await disconnect._logout(
            profile, {}, {'logout': {'path': '/чужой'}}, None, None, None)

        assert (ok, error) == (True, '')
        assert seen == [('https://stand.invalid', '/signout')], \
            'взяли соседскую настройку вместо своей'

    async def test_neighbour_is_the_fallback(self, profile, monkeypatch):
        """У сайта без своей настройки выход всё равно должен работать."""
        seen = []
        monkeypatch.setattr('src.dating.heartbeat.heartbeat_config',
                            _config({}))
        monkeypatch.setattr(disconnect, '_logout_http', _http(seen))

        ok, _ = await disconnect._logout(
            profile, {}, {'base': 'https://neighbour.invalid',
                          'logout': {'path': '/out'}}, None, None, None)

        assert ok and seen == [('https://neighbour.invalid', '/out')]

    async def test_no_handle_and_no_scenario_says_both(self, profile, monkeypatch):
        """⚠ Причина называет **оба** пути: человек, читающий её, должен понимать,
        что можно завести либо ручку, либо сценарий."""
        monkeypatch.setattr('src.dating.heartbeat.heartbeat_config',
                            _config({}))

        ok, error = await disconnect._logout(profile, {}, {}, None, None, None)

        assert not ok
        assert 'logout' in error and 'сценария' in error and 'lady' in error


class TestHandleItself:
    """Сам запрос."""

    async def test_without_a_session_there_is_nothing_to_log_out(self, profile):
        ok, error = await disconnect._logout_http(
            {**profile, 'storage_state': None}, OURS, OURS['logout'])

        assert not ok and 'выходить нечем' in error

    async def test_without_a_base_we_do_not_guess(self, profile):
        """Некуда слать — честный отказ, а не запрос в пустоту."""
        ok, error = await disconnect._logout_http(
            profile, {'logout': {'path': '/signout'}}, {'path': '/signout'})

        assert not ok and 'корень' in error
