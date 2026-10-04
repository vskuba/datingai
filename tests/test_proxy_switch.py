"""Главный выключатель выходов: снят — прокси нет нигде.

⚠⚠ **Смысл выключателя в слове «нигде».** Дверей наружу шесть — вход браузером,
API сайта, присутствие, сокет чата, отправка, кабинет, — и каждая берёт выход
своим способом: одни через `proxy_url`, присутствие читает адрес прямо из
колонки анкеты, сценарии получают номер выхода. Выключатель, гасящий часть из
них, хуже отсутствующего: человек видит снятую галочку, а анкета ходит каналом.

Отдельным файлом от `test_proxy.py` намеренно: там автофикстура держит
выключатель **включённым**, потому что проверяется механика раздачи. Здесь
проверяется он сам, и включать его нельзя.

По умолчанию выключено. Это тоже проверяется: умолчание «включено» означало бы,
что первая же выкладка на установку без прокси идёт к соседу на каждой двери.
"""

import pytest

from src.dating import heartbeat, proxy, proxy_round
from src.dating.proxy import (PROXY_ENABLED_SETTING,
                                     DatingProxyError, proxy_enabled,
                                     proxy_ready, proxy_url)
from src.uvicorn.routers.dating.site_ import _proxy_off

pytestmark = pytest.mark.profile

SITE = 999_343
# Номер выдуманный: живую анкету тест не трогает.
PROFILE = 999_501


@pytest.fixture
def setting(monkeypatch):
    """Подменяет настройки сайта. `state['value']` — то, что лежит у `proxy_enabled`."""
    state = {'value': '', 'asked': []}

    async def _one(site_id, key, default=''):
        state['asked'].append((int(site_id), str(key)))

        return state['value'] if str(key) == PROXY_ENABLED_SETTING else default

    # ⚠ Подменяем **там, откуда берут**: `proxy_enabled` импортирует
    # функцию внутри себя, из модуля настроек сайта.
    import src.uvicorn.routers.dating.site_ as site_module
    monkeypatch.setattr('src.dating.proxy.site_setting', _one)

    return state


class TestEnabled:
    """Сама настройка: что считается включённым."""

    async def test_off_by_default(self, setting):
        """Ничего не задано — выключено. Умолчание кода, а не пустая строка."""
        setting['value'] = ''

        assert await proxy_enabled(SITE) is False

    async def test_on_by_one(self, setting):
        setting['value'] = '1'

        assert await proxy_enabled(SITE) is True

    async def test_zero_is_off(self, setting):
        """`0` — именно выключено, а не «непустая строка, значит да»."""
        setting['value'] = '0'

        assert await proxy_enabled(SITE) is False

    async def test_unknown_site_is_off(self, setting):
        """Сайт неизвестен — выключено, и в базу за настройкой не ходим.

        Здесь это верное «не знаю»: включённый механизм занимает адреса и ходит к
        соседу, выключенный оставляет всё как было.
        """
        setting['value'] = '1'

        assert await proxy_enabled(0) is False
        assert not setting['asked'], 'за настройкой несуществующего сайта ходили в базу'


class TestDoorsClosed:
    """Двери наружу: при снятой галочке ни одна выхода не получает."""

    async def test_url_is_empty_even_with_fresh_cache(self, setting, monkeypatch):
        """⚠ Свежий кэш **не** отдаётся: выключатель проверяется до него.

        Иначе анкета с адресом в кэше ходила бы прокси ещё полчаса после того,
        как человек его выключил, — то есть выключатель срабатывал бы с
        задержкой, о которой нигде не сказано.
        """
        setting['value'] = '0'

        async def _boom(profile):
            raise AssertionError('за выходом пошли при выключенных прокси')

        monkeypatch.setattr(proxy, 'proxy_take', _boom)

        got = await proxy_url({'id': PROFILE, 'site_id': SITE,
                                      'proxy_url': 'http://live:1', 'proxy_at': None})

        assert got == ''

    async def test_take_refuses(self, setting):
        """Захват выхода в обход `proxy_url` тоже закрыт.

        Функция публичная, и «занять адрес при выключенных прокси» — ровно то,
        чего выключатель не должен допускать.
        """
        setting['value'] = '0'

        with pytest.raises(DatingProxyError, match='выключены'):
            await proxy.proxy_take({'id': PROFILE, 'site_id': SITE})

    async def test_ready_says_yes_and_stays_silent(self, setting, monkeypatch):
        """Анкета к работе готова, и в журнал про выход ничего не пишется.

        ⚠ Строка «своего выхода нет» предупреждает о расхождении с намерением.
        Когда намерения не было, она не предупреждение, а шум — в журнале каждой
        анкеты при каждом входе.
        """
        setting['value'] = '0'
        written = []

        async def _log(profile_id, kind, text):
            written.append((kind, text))

        monkeypatch.setattr(proxy, 'log_write', _log)

        can_go, why = await proxy_ready({'id': PROFILE, 'site_id': SITE})

        assert (can_go, why) == (True, '')
        assert not written, f'в журнал написали лишнее: {written}'

    async def test_heartbeat_map_is_empty(self, setting, monkeypatch):
        """Присутствие читает адрес **из колонки**, минуя раздачу, — гасим и его.

        Это отдельная дверь: не спроси мы выключатель здесь, удары продолжали бы
        идти через канал, которого для сайта уже нет.
        """
        setting['value'] = '0'

        def _boom():
            raise AssertionError('за выходами присутствия пошли в базу')

        monkeypatch.setattr(heartbeat, 'mysql_get_db_async', _boom)

        assert await heartbeat.heartbeat_proxies(SITE) == {}


class TestDoorsOpen:
    """Включённая галочка ничего не ломает: механика прежняя."""

    async def test_url_works_when_on(self, setting, monkeypatch):
        setting['value'] = '1'

        async def _claim(profile):
            return {'id': 3, 'url': 'http://new:1', 'check_ip': '1.2.3.4'}

        monkeypatch.setattr(proxy, 'proxy_take', _claim)

        got = await proxy_url({'id': PROFILE, 'site_id': SITE,
                                      'proxy_url': '', 'proxy_at': None})

        assert got == 'http://new:1'


class TestPingRound:
    """Круг проверки выходов: сайт без прокси срока не задаёт."""

    @pytest.fixture
    def sites(self, monkeypatch):
        """Два сайта, у обоих автопроверка настроена."""
        async def _rows():
            return [{'id': 111}, {'id': 222}]

        async def _seconds(site_id, key):
            return 300

        import src.uvicorn.routers.dating.site_ as site_module
        monkeypatch.setattr('src.dating.proxy_round.site_rows', _rows)
        monkeypatch.setattr('src.dating.proxy_round.site_seconds', _seconds)

    async def test_disabled_site_does_not_ask_for_checks(self, sites, monkeypatch):
        """Прокси выключены у всех — круг молчит, к соседу не идёт.

        Иначе выключение выходов у единственного сайта установки не
        останавливало бы походы к соседу: круг гонял бы проверку каналов,
        которыми никто не ходит.
        """
        async def _off(site_id):
            return False

        monkeypatch.setattr(proxy_round, 'proxy_enabled', _off)

        assert await proxy_round._every() == 0

    async def test_one_enabled_site_is_enough(self, sites, monkeypatch):
        """Хоть у одного включено — проверка идёт: выходы общие на установку."""
        async def _one(site_id):
            return int(site_id) == 222

        monkeypatch.setattr(proxy_round, 'proxy_enabled', _one)

        assert await proxy_round._every() == 300


class TestForgetOnSwitchOff:
    """Снятие галочки снимает выходы с анкет — иначе выключается половина."""

    @pytest.fixture
    def cleared(self, monkeypatch):
        """Считает, у каких сайтов снимали выходы."""
        seen = []

        async def _forget(site_id):
            seen.append(int(site_id))

            return 3

        import src.dating.proxy as proxy_module
        monkeypatch.setattr(proxy_module, 'proxy_forget_site', _forget)

        return seen

    async def test_switch_off_clears(self, cleared):
        await _proxy_off(SITE, {'proxy_enabled': '1'}, {})

        assert cleared == [SITE]

    async def test_switch_on_keeps(self, cleared):
        await _proxy_off(SITE, {}, {'proxy_enabled': '1'})

        assert not cleared

    async def test_saving_other_settings_keeps(self, cleared):
        """⚠ Чистим на **переходе**, а не по факту «сейчас выключено».

        Настройки сохраняются целиком при каждой правке любого поля; чисти мы по
        текущему состоянию, каждое сохранение у сайта без прокси лезло бы в
        таблицу анкет ни за чем.
        """
        await _proxy_off(SITE, {'mail_check_every': '300'}, {'mail_check_every': '120'})

        assert not cleared
