"""Step2Love: ленты «Все онлайн» и «Гости» читаются GET'ом, сценарий не зовётся.

26.11.2026 в 20:56 агентство Step2Love заблокировало нам панель: сценарий
«анкеты мужчин: список» гонялся каждые две минуты (`online_check_every`),
«гости: список» — под каждой анкетой, и каждый прогон у соседа был входом
в панель. Ленты переехали на обычный GET под живым cookie анкеты
(`site/site_feed.py`, `site/site_feed_step2love.py`).

Рамка `frames/step2love_online.html` — **восстановленная** форма страницы
онлайна по строкам `extract` сценарного прогона; при живом снятии — заменить
содержимое, тесты не трогая.

⚠ Ни один тест не ходит по сети: дверь `site_session_get` подменяется, а
`scenario_run` / `scenario_find` — взрывом: ленточный сайт не должен звать
сценарий ни на одной из дорог.
"""

from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from src.dating import guests_round
from src.dating.guests import guests_visit_day
from src.dating.people import PEOPLE_ROWS_PAGE_MAX
from src.dating.site import site_feed
from src.dating.site.site_feed import DatingSiteFeedError
from src.dating.site.site_feed_step2love import DatingSiteFeedStep2Love
from src.dating.site.site_registry import site_registry_feed
from src.uvicorn.routers.dating import people

pytestmark = pytest.mark.profile

PAGE = (Path(__file__).parent / 'frames' / 'step2love_online.html'
        ).read_text(encoding='utf-8')

BASE = 'https://mystep2love.com'
PRESENCE = {'driver': 'step2love', 'base': BASE}
PROFILE = {'id': 401, 'site_id': 999345, 'login': 'girl.one',
           'status': 'online', 'storage_state': '{"cookies": []}'}

# Пустая живая страница: таблица есть, строк нет — на витрине это отказ,
# в середине пагинации — конец ленты, на гостях — правда.
EMPTY = '<html><body><table class="grid"><tbody></tbody></table></body></html>'

# Форма входа: сессия мертва, но перенаправления не было — страница пришла
# на своём адресе и с формой на месте списка.
LOGIN = ('<html><body><form action="/account/login.php">'
         '<input name="user_name" type="text"></form></body></html>')

# Страница гостей: сперва список визитов — его маркеры `(ID: N)` встречает
# разбор раньше сетки, — потом та же сетка карточек.
GUESTS_PAGE = PAGE.replace(
    '<table class="grid">',
    '<div>Steve (ID: 1834776) Send Intro 27-10-2026 Возраст: 65 '
    'Nina (ID: 2210884) Send Intro 26-10-2026 Возраст: 58</div>'
    '<table class="grid">')


def _boom(*args, **kwargs):
    raise AssertionError('сюда ленточный сайт не доходит')


def _value(result):
    """Асинхронная заглушка, всегда отвечающая `result`."""
    async def stub(*args, **kwargs):
        return result

    return stub


def _stub_http(monkeypatch, responder):
    """Поддельная дверь: записи (номер анкеты, адрес) вместо живого GET'а."""
    calls = []

    async def fake(profile, url, timeout=15):
        calls.append((int(profile['id']), str(url)))
        html, final = responder(str(url))

        return httpx.Response(200, text=html, request=httpx.Request('GET', final))

    monkeypatch.setattr(site_feed, 'site_session_get', fake)

    return calls


def _no_scenario(monkeypatch, module):
    """Ни одна дорога ленты не должна позвать сценарий — ни именем, ни номером."""
    for name in ('scenario_run', 'scenario_find', 'rhapsody_api_scenario_find',
                 'rhapsody_api_scenario_forget'):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, _boom)


@pytest.fixture
def feed():
    return DatingSiteFeedStep2Love(PRESENCE)


class TestTheSwitchKnowsItsSites:
    def test_step2love_reads_feeds_by_http(self):
        assert site_registry_feed(PRESENCE).by_http() is True

    def test_every_other_site_keeps_the_scenario(self):
        """⚠ Базовый отвечает «нет» — сайты с живыми сценариями не трогаем."""
        assert site_registry_feed({'driver': 'goldenbride'}).by_http() is False
        assert site_registry_feed(None).by_http() is False

    def test_the_first_page_and_the_wing_pages_differ(self, feed):
        """⚠ Продолжения — `ladies/online/pageN`, как знал сценарий 537."""
        assert feed.online_url(1) == BASE + '/online/'
        assert feed.online_url(2) == (BASE + '/ladies/online/page2/'
                                      '?&m_read=&type_in=&folder=&favorite=')
        assert feed.guests_url() == BASE + '/guests/'

    def test_no_base_no_way(self):
        """Адрес сайта — из закладки; без него ходить некуда."""
        with pytest.raises(DatingSiteFeedError):
            DatingSiteFeedStep2Love({'driver': 'step2love'}).online_url(1)


class TestTheGridIsParsed:
    def test_every_person_lies_twice_on_the_page(self, feed):
        """Каждый человек — ячейка с фото и ячейка со значком; строки — обе."""
        rows = feed.page_rows(PAGE)

        assert len(rows) == 6
        assert all(row['number'] and row['link'] for row in rows)
        assert [row for row in rows if row['status_class']] == \
               [row for row in rows if row['photo'].endswith('.png')]

    def test_the_photo_copy_wins_the_twin(self, feed):
        """⚠ Копия со значком не должна становиться карточкой: лицо — фото."""
        rows = feed.page_rows(PAGE)
        steve = [row for row in rows if row['number'] == '(ID: 1834776)']

        assert 'default_1.gif' in steve[0]['photo']
        assert steve[1]['photo'].endswith('online.png')

    def test_the_order_of_cells_does_not_matter(self, feed):
        """Порядок тиль в разметке — не гарантия; правило задаёт разбор."""
        page = ('<table><tr>'
                '<td><p class="gal_id">(ID: 5)</p>'
                '<p><img src="/t/vi/online.png"></p></td>'
                '<td><p class="gal_id">(ID: 5)</p>'
                '<p class="gal_name"><a href="/profile/5/">Ann</a></p>'
                '<p class="gal_photo"><img src="/uploades/photos/a5.png"></p>'
                '</td></tr></table>')

        assert feed.page_rows(page)[0]['photo'] == '/uploades/photos/a5.png'


class TestTheOnlineFeed:
    async def test_the_pages_walk_until_the_first_empty_one(self, monkeypatch,
                                                            feed):
        calls = _stub_http(monkeypatch,
                           lambda url: (PAGE if url.endswith('/online/')
                                        else EMPTY, url))
        cards = await feed.online_rows(PROFILE)

        assert len(calls) == 2                      # витрина + одна пустая
        assert [card['uid'] for card in cards] == ['2210884', '1834776',
                                                   '3055127']
        assert 'avatar_v2' in cards[0]['photo']     # фото не потерялось в дубле

    async def test_an_endless_page_stops_at_the_cap(self, monkeypatch, feed):
        """⚠ Сайт отдаёт одну и ту же страницу вечно — идём не дальше `men10`."""
        calls = _stub_http(monkeypatch, lambda url: (PAGE, url))
        cards = await feed.online_rows(PROFILE)

        assert len(calls) == PEOPLE_ROWS_PAGE_MAX
        assert len(cards) == 3                      # дубли склеил `people_rows`

    async def test_a_dead_session_bounced_to_login_is_failure(self, monkeypatch,
                                                              feed):
        """⚠⚠ «Список не пришёл» — не «никого нет»: снимок остаётся с причиной."""
        _stub_http(monkeypatch, lambda url: (LOGIN, BASE + '/account/login.php'))

        with pytest.raises(DatingSiteFeedError):
            await feed.online_rows(PROFILE)

    async def test_a_login_form_without_a_redirect_is_failure(self, monkeypatch,
                                                              feed):
        _stub_http(monkeypatch, lambda url: (LOGIN, url))

        with pytest.raises(DatingSiteFeedError):
            await feed.online_rows(PROFILE)

    async def test_login_at_the_second_page_kills_the_whole_feed(self, monkeypatch,
                                                                 feed):
        """Сессия умерла посреди съёма — половины ленты не бывает."""
        _stub_http(monkeypatch,
                   lambda url: (PAGE if url.endswith('/online/') else LOGIN, url))

        with pytest.raises(DatingSiteFeedError):
            await feed.online_rows(PROFILE)

    async def test_an_empty_first_page_of_a_live_session_is_failure(
            self, monkeypatch, feed):
        """Ни строк, ни признаков входа — сменилась вёрстка, не молчим."""
        _stub_http(monkeypatch, lambda url: (EMPTY, url))

        with pytest.raises(DatingSiteFeedError):
            await feed.online_rows(PROFILE)


class TestTheGuestsPage:
    async def test_the_dates_come_from_the_page_text(self, monkeypatch, feed):
        _stub_http(monkeypatch, lambda url: (GUESTS_PAGE, url))
        cards = await feed.guests_rows(PROFILE)

        steve = [one for one in cards if one['uid'] == '1834776'][0]

        assert steve['visit'] == guests_visit_day('27-10-2026')
        assert steve['age'] == 65

    async def test_no_visitors_is_not_a_failure(self, monkeypatch, feed):
        """Пустая страница гостей законна — в отличие от онлайна."""
        _stub_http(monkeypatch, lambda url: (EMPTY, url))

        assert await feed.guests_rows(PROFILE) == []

    async def test_a_bounced_guest_page_is_failure(self, monkeypatch, feed):
        _stub_http(monkeypatch, lambda url: (LOGIN, BASE + '/account/login.php'))

        with pytest.raises(DatingSiteFeedError):
            await feed.guests_rows(PROFILE)


class TestTheRoadsNeverCallTheScenario:
    """⚠⚠ Главный смысл правки: на этих двух дорогах сценарий не зовётся."""

    async def test_the_online_snapshot_goes_by_http(self, monkeypatch):
        calls = _stub_http(monkeypatch,
                           lambda url: (PAGE if url.endswith('/online/')
                                        else EMPTY, url))
        _no_scenario(monkeypatch, people)
        monkeypatch.setattr(people, 'site_presence', _value(PRESENCE))
        monkeypatch.setattr(people, 'panel_online_on', _value(False))
        monkeypatch.setattr(people, '_api_for', _boom)

        cards = await people._snapshot(PROFILE, 'online')

        assert len(cards) == 3
        assert urlparse(calls[0][1]).path == '/online/'

    async def test_the_site_feed_without_online_girls_waits(self, monkeypatch):
        """⚠ Спящая анкета — не дверь: вместо прогона — прежний снимок с пометкой."""
        marked = []
        _no_scenario(monkeypatch, people)
        monkeypatch.setattr(people, 'site_presence', _value(PRESENCE))
        monkeypatch.setattr(people, '_panel_door', _value({}))
        monkeypatch.setattr(people, '_site_profile', _value({}))
        monkeypatch.setattr(people, '_site_sleeper', _boom)
        monkeypatch.setattr(people, '_api_for', _boom)
        monkeypatch.setattr(people, '_error_mark',
                            lambda key, why: marked.append((key, why)))

        assert await people.people_snap_start(999345) is False

        assert marked and 'ни одна анкета не в сети' in marked[0][1]

    async def test_the_guests_round_skips_sleeping_girls(self, monkeypatch):
        """Гости ленточного сайта: под спящей анкетой — ни запроса, ни остуды."""
        seen, slots = [], []
        calls = _stub_http(monkeypatch, lambda url: (GUESTS_PAGE, url))
        _no_scenario(monkeypatch, guests_round)

        async def seen_add(site_id, profile_id, rows):
            seen.append((profile_id, rows))

            return len(rows)

        async def state(profile_id):
            return dict(PROFILE, id=profile_id,
                        status='online' if profile_id == 401 else 'offline')

        async def seconds(site_id, key):
            return 3600 if key == 'mailing_guests_every' else 200

        monkeypatch.setattr(guests_round, 'site_presence', _value(PRESENCE))
        monkeypatch.setattr(guests_round, 'site_seconds', seconds)
        monkeypatch.setattr(guests_round, 'guests_site_profiles', _value([
            {'id': 401, 'login': 'girl.one', 'proxy_id': 0},
            {'id': 402, 'login': 'girl.two', 'proxy_id': 0}]))
        monkeypatch.setattr(guests_round, '_profile_state', state)
        monkeypatch.setattr(guests_round, 'guests_seen', seen_add)
        # Сторожа круга — синхронные; асинхронная заглушка здесь была бы
        # истиной по факту существования корутины, а не по ответу.
        monkeypatch.setattr(guests_round, 'round_mem_due',
                            lambda *a, **k: True)
        monkeypatch.setattr(guests_round, 'round_slot_due',
                            lambda *a, **k: True)
        monkeypatch.setattr(guests_round, 'round_mem_mark',
                            lambda *a, **k: None)
        monkeypatch.setattr(guests_round, 'round_slot_mark',
                            lambda *a, **k: slots.append(a[1]))

        got = await guests_round.guests_site_sweep(999345)

        assert 'error' not in got
        assert [one[0] for one in seen] == [401]      # спящая — не снималась
        assert {one[0] for one in calls} == {401}     # и не стучала вовсе
        assert sorted(slots) == [401, 402]            # слот поставлен обеим
