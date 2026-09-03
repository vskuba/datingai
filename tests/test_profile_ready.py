"""⚠ Признак «панель может работать с анкетой» — один на все сайты.

Воркер отбирал анкеты условием «снят вход». Для сокетного сайта верно: без входа
нет адреса сокета. Но у Ladadate сокета нет вовсе — там пишут сценарием, который
входит сам, каждый раз заново. Из 24 анкет Ladadate воркер видел **ноль**, и понять
это по панели было нельзя: анкета выглядела нормальной.

Три признака, и путать их дорого: `enabled` — ведём ли (ставит человек), `can_work`
— можно ли работать (ставит код), `status` — в сети ли сейчас (ставит опрос).
"""

import pytest

from src.dating.dating_ready import (dating_ready_check, dating_ready_transport,
                                     dating_ready_write)

# Учётка в разных состояниях. `password` — непустая строка: сама пара не важна,
# важно, есть ли чем войти.
#
# ⚠ `password_source` обязателен: откуда пароль, анкета решает явно
# (`dating.md`, §5.12), и строка без него читается как «берём общий агентский» —
# а его в этих проверках нет.
LOGGED = {'enabled': 1, 'storage_state': '{"cookies": []}', 'ws_url': 'wss://site/ws',
          'password': 'секрет', 'password_source': 'own'}
NO_LOGIN = {'enabled': 1, 'storage_state': None, 'ws_url': '',
            'password': 'секрет', 'password_source': 'own'}
NO_PASSWORD = {'enabled': 1, 'storage_state': None, 'ws_url': '',
               'password': '', 'password_source': 'own'}


class TestScenarioTransport:
    """Сценарию нужен пароль, а вход он делает сам."""

    def test_password_is_enough(self):
        """⚠ Главный случай, из-за которого признак и завёлся: **входа нет, а
        работать можно.** Сценарий войдёт сам."""
        state = dating_ready_check(NO_LOGIN, 'scenario')

        assert state['can'] is True and state['why'] == ''

    def test_site_password_works_too(self):
        """Общий агентский пароль сайта годится — когда анкета его и выбрала.

        ⚠ Выбор явный: `password_source = 'site'`. Прежде общий подставлялся сам,
        если своего нет, и по анкете было не видно, под каким паролем она работает.
        """
        state = dating_ready_check({**NO_PASSWORD, 'password_source': 'site'},
                                   'scenario', site_password='общий')

        assert state['can'] is True

    def test_own_choice_does_not_fall_back_to_site(self):
        """⚠ Выбран свой, а поле пусто — общий **не подставляется**.

        Подстановка вернула бы ту же неявность, от которой уходим, только теперь
        вопреки явно сделанному выбору: человек сказал «свой», а анкета пошла бы
        под чужим.
        """
        state = dating_ready_check(NO_PASSWORD, 'scenario', site_password='общий')

        assert state['can'] is False
        assert 'свой' in state['why']

    def test_no_password_at_all_is_refused(self):
        """Нечем войти — и причина сказана словами: без неё панель говорит «не
        работает», а человек ищет поломку в коде вместо того, чтобы вписать пароль.
        """
        state = dating_ready_check(NO_PASSWORD, 'scenario')

        assert state['can'] is False
        assert 'парол' in state['why']

    def test_login_is_not_required(self):
        """Снятый вход сценарию не мешает, но и не требуется: он входит заново."""
        assert dating_ready_check(LOGGED, 'scenario')['can'] is True


class TestSocketTransport:
    """Сокету нужен снятый вход: адрес берётся из него."""

    def test_logged_in_is_ready(self):
        assert dating_ready_check(LOGGED, 'ws')['can'] is True

    def test_without_login_is_refused(self):
        state = dating_ready_check(NO_LOGIN, 'ws')

        assert state['can'] is False
        assert 'вход' in state['why']

    def test_login_without_socket_url_is_refused(self):
        """⚠ Вход снят, а адреса сокета нет — частичный вход бывает.

        Прежний отбор (`storage_state IS NOT NULL`) такую анкету брал, и держатель
        падал на пустом адресе.
        """
        state = dating_ready_check({**LOGGED, 'ws_url': ''}, 'ws')

        assert state['can'] is False
        assert 'сокет' in state['why']

    def test_http_transport_needs_login_but_not_socket(self):
        """HTTP-ручка сайта: запрос идёт с cookie входа, а адрес сокета ни к чему."""
        assert dating_ready_check({**LOGGED, 'ws_url': ''}, 'http')['can'] is True
        assert dating_ready_check(NO_LOGIN, 'http')['can'] is False


class TestDisabledIsNotReady:
    """⚠ Выключенная анкета готовой не считается ни при каком транспорте."""

    @pytest.mark.parametrize('transport', ['ws', 'http', 'scenario'])
    def test_disabled_beats_everything(self, transport):
        """Иначе панель показывала бы «готова» у той, с которой ничего не делают, и
        человек искал бы поломку в работе, а не в галочке."""
        state = dating_ready_check({**LOGGED, 'enabled': 0}, transport)

        assert state['can'] is False
        assert 'выключена' in state['why']


class TestTransportChoice:
    """Чем сайт отправляет: наша настройка старше `presence`."""

    def test_our_setting_wins(self):
        """⚠ То же правило, что в отправке: `presence` описывает сайт, но правит его
        только сосед, а «чем отправлять» решает наша сторона."""
        assert dating_ready_transport({'send': {'transport': 'ws'}},
                                      {'send_transport': 'scenario'}) == 'scenario'

    def test_presence_default_falls_back_to_the_site(self):
        """`presence` в настройке — «как настроено у сайта»."""
        assert dating_ready_transport({'send': {'transport': 'ws'}},
                                      {'send_transport': 'presence'}) == 'ws'

    def test_no_settings_reads_the_site(self):
        assert dating_ready_transport({'send': {'transport': 'ws'}}, {}) == 'ws'

    def test_nothing_configured_is_http(self):
        """Умолчание — `http`: так было до появления настройки, и менять его значило
        бы тихо переключить сайты, которых правка не касалась."""
        assert dating_ready_transport({}, {}) == 'http'

    def test_presence_may_be_a_json_string(self):
        """`presence` приходит от соседа то словарём, то строкой JSON — разбор общий
        (`dating_json.py`), и здесь это не должно ломаться."""
        assert dating_ready_transport('{"send": {"transport": "ws"}}', {}) == 'ws'


class TestWriteIsQuiet:
    """Запись признака не должна ронять работу."""

    async def test_broken_database_does_not_raise(self, monkeypatch):
        """⚠ Признак — служебное сведение: сбой его записи не повод срывать круг
        воркера, внутри которого он и считается."""
        from src.dating import dating_ready

        def _broken():
            raise RuntimeError('база недоступна')

        monkeypatch.setattr(dating_ready, 'mysql_get_db_async', _broken)

        # Не падает и ничего не возвращает — это и проверяем.
        assert await dating_ready_write(849, {'can': True, 'why': ''}) is None

    async def test_reason_is_cut_to_the_column(self, monkeypatch):
        """Причина режется по длине колонки: `varchar(200)`, а текст приходит от
        сайта и бывает длинным."""
        from src.dating import dating_ready

        seen = {}

        class _Cursor:
            async def execute(self, sql, args=None):
                seen['args'] = args

            @property
            def connection(self):
                return self

            async def commit(self):
                return None

        class _Db:
            async def __aenter__(self):
                return _Cursor()

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(dating_ready, 'mysql_get_db_async', lambda: _Db())

        await dating_ready_write(849, {'can': False, 'why': 'ы' * 500})

        assert len(seen['args'][1]) == 200
