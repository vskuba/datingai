"""Присутствие девушки Luxee кнопкой панели: «Set online» / «Set offline».

У Luxee кабинета под девушкой нет — «в сети» её держит сама панель агентства.
Вход по смене здесь — нажатие «Set online», выход — «Set offline», а круг
панели раз в минуту сверяет статусы с расписанием (`panel_presence.py`).
"""

import json

import pytest

from src.dating.site.site_panel import DatingSitePanel, DatingSitePanelLuxee

pytestmark = pytest.mark.profile

# Смена на весь день и смена, которой нет вовсе: «в смене» и «вне смены».
ALL_DAY = json.dumps([{'from': '00:00', 'to': '24:00'}])
NEVER = json.dumps([{'from': '03:00', 'to': '03:15'}])
NOON = 12 * 60


@pytest.fixture
def luxee():
    return DatingSitePanelLuxee({'driver': 'luxee'})


class TestTheDriverSpeaksThePanel:
    def test_base_panel_has_no_button(self):
        assert DatingSitePanel().PRESENCE_SET_PATH == ''

    def test_toggle_form_carries_profiles_and_state(self, luxee):
        assert luxee.presence_form(['1', '2'], True) == {
            'profiles[]': ['1', '2'], 'online': '10'}
        assert luxee.presence_form(['1'], False)['online'] == '1'

    def test_read_form_has_no_state(self, luxee):
        assert luxee.presence_form(['1']) == {'profiles[]': ['1']}

    def test_read_answer_is_a_plain_map(self, luxee):
        assert luxee.presence_answer('{"622348":true,"622356":false}') == {
            '622348': True, '622356': False}

    def test_toggle_answer_lives_under_data(self, luxee):
        assert luxee.presence_answer(
            '{"data":{"622348":true},"errors":[]}') == {'622348': True}

    def test_panel_errors_are_a_refusal(self, luxee):
        with pytest.raises(ValueError, match='нет прав'):
            luxee.presence_answer('{"data":{},"errors":[{"message":"нет прав"}]}')

    def test_login_page_is_a_refusal(self, luxee):
        with pytest.raises(ValueError):
            luxee.presence_answer('<html>Log In</html>')

    def test_empty_answer_is_a_refusal(self, luxee):
        """⚠ «Никого не переключили» не должно читаться удачей."""
        with pytest.raises(ValueError):
            luxee.presence_answer('{}')


@pytest.fixture
def panel(monkeypatch):
    """Панель без сети: состояние девушек в словаре, журнал POST-ов."""
    import src.dating.panel_presence as mod

    state = {}
    posts = []
    statuses = {}

    async def fake_post(site_id, path, data, files=None, headers=None):
        posts.append({'path': path, 'data': data, 'files': files})
        uids = data['profiles[]']
        if 'online' in data:
            for uid in uids:
                state[uid] = data['online'] == '10'

            return json.dumps({'data': {uid: state[uid] for uid in uids},
                               'errors': []})

        return json.dumps({uid: state.get(uid, False) for uid in uids})

    async def fake_presence(site_id):
        return {'driver': 'luxee'}

    async def fake_setting(site_id, key):
        return 'https://luxee.test/profile/' if key == 'admin_url' else ''

    async def fake_log(profile_id, kind, text):
        pass

    async def fake_status(ids, status):
        for one in ids:
            statuses[one] = status

    monkeypatch.setattr(mod, 'panel_fetch_post', fake_post)
    monkeypatch.setattr(mod, 'site_presence', fake_presence)
    monkeypatch.setattr(mod, 'site_setting', fake_setting)
    monkeypatch.setattr(mod, 'log_write', fake_log)
    monkeypatch.setattr(mod, '_status_set', fake_status)

    return state, posts, statuses


def _girls(monkeypatch, rows):
    import src.dating.panel_presence as mod

    async def fake_rows(site_id):
        return [{**row, 'uid': str(row['profile_id'])} for row in rows]

    monkeypatch.setattr(mod, '_site_profiles', fake_rows)


class TestTheKeepFollowsTheSchedule:
    async def test_on_shift_offline_girl_is_switched_on(self, panel, monkeypatch):
        from src.dating.panel_presence import panel_presence_keep

        state, posts, statuses = panel
        _girls(monkeypatch, [{'id': 1, 'profile_id': 11, 'profile': '{}',
                              'enabled': 1, 'schedule': ALL_DAY}])

        got = await panel_presence_keep(7, NOON)

        assert got == {'checked': 1, 'on': 1, 'off': 0, 'error': ''}
        assert state['11'] is True
        assert statuses[1] == 'online'

    async def test_off_shift_online_girl_is_switched_off(self, panel, monkeypatch):
        from src.dating.panel_presence import panel_presence_keep

        state, _, statuses = panel
        state['11'] = True
        _girls(monkeypatch, [{'id': 1, 'profile_id': 11, 'profile': '{}',
                              'enabled': 1, 'schedule': NEVER}])

        got = await panel_presence_keep(7, NOON)

        assert got['off'] == 1
        assert state['11'] is False
        assert statuses[1] == 'offline'

    async def test_disabled_girl_goes_off_even_on_shift(self, panel, monkeypatch):
        from src.dating.panel_presence import panel_presence_keep

        state, _, _ = panel
        state['11'] = True
        _girls(monkeypatch, [{'id': 1, 'profile_id': 11, 'profile': '{}',
                              'enabled': 0, 'schedule': ALL_DAY}])

        await panel_presence_keep(7, NOON)

        assert state['11'] is False

    async def test_matching_states_press_nothing(self, panel, monkeypatch):
        """Один POST чтения, ни одного переключения: тишина, когда всё сходится."""
        from src.dating.panel_presence import panel_presence_keep

        state, posts, _ = panel
        state['11'] = True
        _girls(monkeypatch, [{'id': 1, 'profile_id': 11, 'profile': '{}',
                              'enabled': 1, 'schedule': ALL_DAY}])

        got = await panel_presence_keep(7, NOON)

        assert got['on'] == got['off'] == 0
        assert [one['path'] for one in posts] == ['/profile/online-status/']

    async def test_the_form_goes_as_plain_fields(self, panel, monkeypatch):
        """⚠ `files={}` — строкой полей, как `$.post` страницы, а не multipart."""
        from src.dating.panel_presence import panel_presence_keep

        _, posts, _ = panel
        _girls(monkeypatch, [{'id': 1, 'profile_id': 11, 'profile': '{}',
                              'enabled': 1, 'schedule': ALL_DAY}])

        await panel_presence_keep(7, NOON)

        assert all(one['files'] == {} for one in posts)

    async def test_a_failed_press_keeps_the_real_status(self, panel, monkeypatch):
        """⚠ Не сработало — у нас «не в сети», а не желаемое «в сети»."""
        import src.dating.panel_presence as mod
        from src.dating.panel_fetch import DatingPanelFetchError

        _, _, statuses = panel
        _girls(monkeypatch, [{'id': 1, 'profile_id': 11, 'profile': '{}',
                              'enabled': 1, 'schedule': ALL_DAY}])
        read = mod.panel_fetch_post

        async def toggle_fails(site_id, path, data, files=None, headers=None):
            if 'online' in data:
                raise DatingPanelFetchError('панель увела на вход')

            return await read(site_id, path, data, files)

        monkeypatch.setattr(mod, 'panel_fetch_post', toggle_fails)

        got = await mod.panel_presence_keep(7, NOON)

        assert got['on'] == 0
        assert statuses[1] == 'offline'


class TestEnterAndLeave:
    async def test_enter_presses_set_online(self, panel):
        from src.dating.panel_presence import panel_presence_enter

        state, _, statuses = panel

        await panel_presence_enter({'id': 1, 'site_id': 7, 'profile_id': '',
                                    'profile': '{"id": "11"}'})

        assert state['11'] is True
        assert statuses[1] == 'online'

    async def test_enter_without_number_is_a_refusal(self, panel):
        from src.dating.panel_presence import (DatingPanelPresenceError,
                                               panel_presence_enter)

        with pytest.raises(DatingPanelPresenceError, match='номера'):
            await panel_presence_enter({'id': 1, 'site_id': 7, 'profile_id': '',
                                        'profile': '{}'})

    async def test_leave_presses_set_offline(self, panel, monkeypatch):
        import src.dating.panel_presence as mod

        state, _, _ = panel
        state['11'] = True

        async def fake_profile(profile_id):
            return {'id': 1, 'site_id': 7, 'profile_id': '11', 'profile': '{}'}

        monkeypatch.setattr(mod, '_profile', fake_profile)

        await mod.panel_presence_leave(1)

        assert state['11'] is False

    async def test_leave_does_not_throw(self, panel, monkeypatch):
        """⚠ Выход не срывается: недошедшее нажатие выправит сверка."""
        import src.dating.panel_presence as mod
        from src.dating.panel_fetch import DatingPanelFetchError

        async def fake_profile(profile_id):
            return {'id': 1, 'site_id': 7, 'profile_id': '11', 'profile': '{}'}

        async def broken(*a, **k):
            raise DatingPanelFetchError('сеть')

        monkeypatch.setattr(mod, '_profile', fake_profile)
        monkeypatch.setattr(mod, 'panel_fetch_post', broken)

        await mod.panel_presence_leave(1)


class TestTheDoorsAreWired:
    def test_login_forks_before_the_password(self):
        """⚠ Пароль кнопке панели не нужен: развилка обязана стоять раньше."""
        import inspect

        from src.dating import login

        body = inspect.getsource(login.login_run)

        assert body.index('panel_presence_on') < body.index('password_pick')

    def test_state_clear_presses_offline(self):
        import inspect

        from src.dating import schedule_round

        assert 'panel_presence_leave' in inspect.getsource(
            schedule_round._state_clear)

    def test_token_road_imports_its_retry_door(self):
        """⚠ Прежде `login_retry_fail` в `_by_token` не был импортирован."""
        import inspect

        from src.dating import login

        assert 'import login_retry_fail' in inspect.getsource(login._by_token)


class TestAStrangerIsLeftOut:
    """Номер, которого панель не знает, она не возвращает — он выпадает."""

    async def test_unknown_number_is_dropped_others_are_kept(self, panel, monkeypatch):
        import src.dating.panel_presence as mod

        state, _, _ = panel
        _girls(monkeypatch, [
            {'id': 1, 'profile_id': 11, 'profile': '{}', 'enabled': 1, 'schedule': ALL_DAY},
            {'id': 2, 'profile_id': 99, 'profile': '{}', 'enabled': 1, 'schedule': ALL_DAY}])
        known = mod.panel_fetch_post

        async def stranger_missing(site_id, path, data, files=None, headers=None):
            data = {**data, 'profiles[]': [u for u in data['profiles[]'] if u != '99']}

            return await known(site_id, path, data, files)

        monkeypatch.setattr(mod, 'panel_fetch_post', stranger_missing)

        got = await mod.panel_presence_keep(7, NOON)

        assert got == {'checked': 1, 'on': 1, 'off': 0, 'error': ''}
        assert state['11'] is True

    async def test_press_on_a_stranger_says_so(self, panel, monkeypatch):
        import src.dating.panel_presence as mod

        async def empty_data(site_id, path, data, files=None, headers=None):
            return json.dumps({'data': {'5': True}, 'errors': []})

        monkeypatch.setattr(mod, 'panel_fetch_post', empty_data)

        with pytest.raises(mod.DatingPanelPresenceError, match='не знает'):
            await mod.panel_presence_enter({'id': 1, 'site_id': 7, 'profile_id': '99',
                                            'profile': '{}'})

    async def test_dead_session_is_an_error(self, panel, monkeypatch):
        import src.dating.panel_presence as mod
        from src.dating.panel_fetch import DatingPanelFetchError

        _girls(monkeypatch, [{'id': 1, 'profile_id': 11, 'profile': '{}',
                              'enabled': 1, 'schedule': ALL_DAY}])

        async def dead(*a, **k):
            raise DatingPanelFetchError('панель увела на вход')

        monkeypatch.setattr(mod, 'panel_fetch_post', dead)

        got = await mod.panel_presence_keep(7, NOON)

        assert 'увела на вход' in got['error']

    async def test_every_post_asks_as_the_page_script(self, panel, monkeypatch):
        """⚠⚠ Без `X-Requested-With` обе ручки отвечают 403."""
        import src.dating.panel_presence as mod

        seen = []
        known = mod.panel_fetch_post

        async def spy(site_id, path, data, files=None, headers=None):
            seen.append(headers or {})

            return await known(site_id, path, data, files)

        monkeypatch.setattr(mod, 'panel_fetch_post', spy)
        _girls(monkeypatch, [{'id': 1, 'profile_id': 11, 'profile': '{}',
                              'enabled': 1, 'schedule': ALL_DAY}])

        await mod.panel_presence_keep(7, NOON)

        assert seen and all(one.get('X-Requested-With') == 'XMLHttpRequest'
                            for one in seen)


class TestReviveLeavesPanelSitesAlone:
    """⚠⚠ Круг восстановления метил анкеты Luxee `error` — их присутствие
    держит сверка панели, а настройки `heartbeat` у сайта нет."""

    async def test_panel_held_site_is_skipped(self, monkeypatch):
        import src.dating.panel_presence as mod
        from src.dating import revive_round

        async def only_seven(site_id):
            return int(site_id) == 7

        monkeypatch.setattr(mod, 'panel_presence_on', only_seven)

        got = await revive_round._not_panel_held(
            [{'id': 1, 'site_id': 7}, {'id': 2, 'site_id': 343}])

        assert [one['id'] for one in got] == [2]
