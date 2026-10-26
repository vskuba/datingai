"""Круг импорта переписки: кто, когда и как часто.

⚠ **Наружу не ходит ничего.** Прогон сценария, база и Redis подменены: проверяется
расписание, а не работа браузера. Сторож сети в `conftest` это и караулит.

Почему круг вообще нужен: у сайта без сокета (Ladadate) панель агентства —
**единственный** источник входящих. Пока импорт был только кнопкой, сообщения
приходили ровно тогда, когда человек смотрел на экран.
"""

import pytest

from round_ import round_slot as slot
from round_.round_slot import round_slot_key

from src.dating import chat_hold as hold

pytestmark = pytest.mark.chat
SITE, OTHER = 343, 19


class _FakeRedis:
    """Redis настолько, насколько его читает круг: `get`, `setex` и `ttl`.

    ⚠ `ttl` появился с правкой, где отметка хранит свой срок значением:
    `round_slot_due` с доводом `every` спрашивает остаток. Без этого метода
    подделка бросала бы `TypeError` прямо в путь «сбой — идём» (`unknown=True`):
    тот же сбой в бою значит «пора», и круг забирал бы переписку каждым тиком.
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}
        self.broken = False

    def get(self, key):
        if self.broken:
            raise RuntimeError('redis лёг')

        return self.store.get(key)

    def setex(self, key, seconds, value):
        if self.broken:
            raise RuntimeError('redis лёг')

        self.store[key] = value
        self.expires[key] = int(seconds)

    def ttl(self, key):
        if self.broken:
            raise RuntimeError('redis лёг')

        # Часы подделки не идут: остаток — поставленная выдержка как есть.
        # `-2` — ключа нет, ровно как у живого Redis.
        return int(self.expires.get(key, -2))


@pytest.fixture
def round_(monkeypatch):
    """Круг с подменённым окружением. Возвращает, до чего дошло."""
    state = {
        'sites': [SITE, OTHER],
        'every': {SITE: 300, OTHER: 3600},
        'imported': [],
        'result': {'status': 'ok', 'added': 0, 'contacts': 0, 'error': ''},
        'redis': _FakeRedis(),
    }

    async def _sites():
        return list(state['sites'])

    async def _seconds(site_id, key):
        assert key == hold.CHAT_IMPORT_SETTING, key

        return int(state['every'].get(int(site_id), 0))

    async def _import(site_id, days):
        state['imported'].append((int(site_id), int(days)))

        return dict(state['result'])

    monkeypatch.setattr(hold, '_chat_sites', _sites)
    monkeypatch.setattr(hold, 'site_seconds', _seconds)
    monkeypatch.setattr(hold, 'chat_panel_import', _import)
    # ⚠ Redis подменяем **в модуле слоя**: слот круга переехал в `round_/`, а
    # `chat_hold` зовёт его через нашу дверь. Подмена в `hold` после переезда не
    # делала бы ничего — тест зеленел бы на любом поведении отметки.
    monkeypatch.setattr(slot, 'redis_conn_get', lambda: state['redis'])

    return state


class TestSchedule:
    """Кому и когда пора."""

    async def test_first_round_takes_everyone(self, round_):
        """Отметки нет ни у кого — забираем у всех, у кого настройка задана."""
        await hold._chat_import_round()

        assert round_['imported'] == [(SITE, hold.CHAT_PANEL_ROUND_DAYS),
                                      (OTHER, hold.CHAT_PANEL_ROUND_DAYS)]

    async def test_second_round_skips_marked(self, round_):
        """⚠ Второй обход подряд не забирает: срок не вышел.

        Круг просыпается каждую минуту, а настройка — от пяти. Без отметки импорт
        шёл бы каждый тик: сорок секунд браузера и вкладка в общем пуле соседа
        двенадцать раз в час вместо одного.
        """
        await hold._chat_import_round()
        round_['imported'].clear()

        await hold._chat_import_round()

        assert round_['imported'] == []

    async def test_mark_lives_exactly_the_setting(self, round_):
        """Срок отметки — и есть настройка: Redis сам погасит ключ, когда пора.

        Считать разницу дат было бы тем же самым, только руками — и с риском
        разойтись, когда настройку поменяют.
        """
        await hold._chat_import_round()

        assert round_['redis'].expires[round_slot_key(hold.CHAT_IMPORT_KEY, SITE)] == 300
        assert round_['redis'].expires[round_slot_key(hold.CHAT_IMPORT_KEY, OTHER)] == 3600

    async def test_expired_mark_lets_it_through(self, round_):
        """Отметка погасла — забираем снова. Так круг и работает: ключа нет
        значит пора."""
        await hold._chat_import_round()
        round_['redis'].store.clear()
        round_['imported'].clear()

        await hold._chat_import_round()

        assert [site for site, _ in round_['imported']] == [SITE, OTHER]

    async def test_sites_are_independent(self, round_):
        """Свой срок у каждого сайта: одному хватает часа, другому нужны пять минут."""
        await hold._chat_import_round()
        # Гасим отметку только у одного — второй должен остаться пропущенным.
        round_['redis'].store.pop(round_slot_key(hold.CHAT_IMPORT_KEY, SITE))
        round_['imported'].clear()

        await hold._chat_import_round()

        assert [site for site, _ in round_['imported']] == [SITE]

    async def test_setting_off_means_no_import(self, round_):
        """Ноль в настройке — не забираем вовсе.

        Живая функция отдаёт ноль на незаданную настройку, и это законное «не
        надо»: сайт, у которого импорт не настроен, гонять браузером незачем.
        """
        round_['every'] = {SITE: 0, OTHER: 0}

        await hold._chat_import_round()

        assert round_['imported'] == []

    async def test_round_takes_a_day_not_the_limit(self, round_):
        """⚠ Круг берёт сутки, а не предел в 60 дней.

        Он идёт часто, и неделя каждый раз означала бы привозить одно и то же по
        сотне раз. Историю забирают кнопкой, руками.
        """
        await hold._chat_import_round()

        assert {days for _, days in round_['imported']} == {1}


class TestHoldersNeedASocket:
    """⚠ Держим сокет только там, где он есть.

    «Готова к работе» и «есть что держать» — разные вещи, и с появлением сценарного
    транспорта они разошлись: анкета Ladadate готова работать (сценарий войдёт сам),
    но сокета у её сайта нет вовсе.

    Поймано по логу: отбор стал отдавать все готовые анкеты, держатель полез
    подключаться к каждой и выдал **264 жалобы «адрес сокета не снят» за десять
    минут**. Работе не мешало, но такой лог нельзя читать — а читают его именно
    тогда, когда что-то сломалось.
    """

    def _items(self):
        return [{'profile': {'id': 1, 'login': 'a@test', 'ws_url': 'wss://site/ws?token=a'},
                 'presence': {}, 'site_url': 'https://site'},
                {'profile': {'id': 2, 'login': 'b@test', 'ws_url': ''},
                 'presence': {}, 'site_url': 'https://site'},
                {'profile': {'id': 3, 'login': 'c@test', 'ws_url': '   '},
                 'presence': {}, 'site_url': 'https://site'}]

    async def test_only_profiles_with_a_socket_are_held(self, monkeypatch):
        monkeypatch.setattr(hold, 'chat_hold_profile',
                            lambda *a, **k: _never())

        holders, counters = {}, {}
        hold._holders_sync(holders, counters, self._items(), deadline=9e9)

        try:
            assert sorted(holders) == [1], 'полезли держать сокет там, где его нет'
        finally:
            for held in holders.values():
                held['task'].cancel()

    async def test_appearing_socket_starts_a_holder(self, monkeypatch):
        """Вход сняли — адрес появился, и держатель заводится следующим кругом.

        Обратная сторона: без неё правило могло бы отсечь всех, и чат перестал бы
        работать вовсе.
        """
        monkeypatch.setattr(hold, 'chat_hold_profile',
                            lambda *a, **k: _never())

        holders, counters = {}, {}
        items = self._items()
        hold._holders_sync(holders, counters, items, deadline=9e9)

        items[1]['profile']['ws_url'] = 'wss://site/ws?token=b'
        hold._holders_sync(holders, counters, items, deadline=9e9)

        try:
            assert sorted(holders) == [1, 2]
        finally:
            for held in holders.values():
                held['task'].cancel()

    async def test_lost_socket_stops_the_holder(self, monkeypatch):
        """Адрес пропал (выход, протухший вход) — держателя гасим: висеть на
        мёртвом адресе хуже, чем не висеть вовсе."""
        monkeypatch.setattr(hold, 'chat_hold_profile',
                            lambda *a, **k: _never())

        holders, counters = {}, {}
        items = self._items()
        hold._holders_sync(holders, counters, items, deadline=9e9)
        assert sorted(holders) == [1]

        items[0]['profile']['ws_url'] = ''
        hold._holders_sync(holders, counters, items, deadline=9e9)

        assert holders == {}


def _never():
    """Задача, которая никогда не кончается: держатель здесь не проверяется."""
    import asyncio

    return asyncio.sleep(3600)


class TestFailuresDoNotStopTheRound:
    """⚠ Круг обходит все сайты, и первый сломанный не должен лишать остальных."""

    async def test_error_on_one_site_does_not_stop_the_others(self, round_, monkeypatch):
        """У первого сайта нет сценария — второй всё равно забирается."""
        async def _import(site_id, days):
            round_['imported'].append((int(site_id), int(days)))

            if int(site_id) == SITE:
                return {'status': 'error', 'added': 0, 'contacts': 0,
                        'error': 'у сайта нет сценария'}

            return {'status': 'ok', 'added': 1, 'contacts': 1, 'error': ''}

        monkeypatch.setattr(hold, 'chat_panel_import', _import)

        await hold._chat_import_round()

        assert [site for site, _ in round_['imported']] == [SITE, OTHER]

    async def test_broken_redis_does_not_stop_the_import(self, round_):
        """⚠ Сбой Redis даёт «забираем», а не «молчим».

        Пропущенный круг человек замечает («она не отвечает»), лишний прогон — нет.
        Из двух терпимых исходов выбираем незаметный.
        """
        round_['redis'].broken = True

        await hold._chat_import_round()

        assert [site for site, _ in round_['imported']] == [SITE, OTHER]

    async def test_mark_is_set_before_the_run(self, round_, monkeypatch):
        """⚠ Отметка ставится **до** прогона, а не после.

        Прогон длится минуту, тик круга — минута: без этого следующий тик завёл бы
        второй прогон, пока идёт первый. Тот же приём, что у сторожа фоновых
        обновлений списков.
        """
        seen = {}

        async def _import(site_id, days):
            key = round_slot_key(hold.CHAT_IMPORT_KEY, site_id)
            seen[int(site_id)] = round_['redis'].store.get(key)

            return {'status': 'ok', 'added': 0, 'contacts': 0, 'error': ''}

        monkeypatch.setattr(hold, 'chat_panel_import', _import)

        await hold._chat_import_round()

        # Значение отметки — теперь сама выдержка (слой хранит срок в значении),
        # для сторожа важен факт: к моменту прогона ключ уже стоит.
        assert seen[SITE] is not None, 'отметку поставили после прогона'
