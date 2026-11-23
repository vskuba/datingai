"""Платившие нашим девушкам — первыми в наборе, но без флага «Приоритет».

Владелец 23.11.2026: приоритет плативших (`GET /users/payers`). ⚠ Наружу сюита
не ходит, Redis подменяется: живого Redis у сюиты с хоста нет.
"""

import pytest

from src.dating import payers as payers_mod
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.mail

SITE = 999_778

# ⚠ Сверке нужны живые анкеты в базе — только стендовый сайт: заведение
# проверяет номер сайта.
STAND = TEST_STAND_SITE


class _Redis:
    """Ровно то, что зовёт `payers.py`: множество, срок и конвейер."""

    def __init__(self):
        self.sets = {}

    def smembers(self, key):
        return {one.encode() for one in self.sets.get(key, set())}

    def pipeline(self):
        return self

    def delete(self, key):
        self.sets.pop(key, None)

    def sadd(self, key, *values):
        self.sets.setdefault(key, set()).update(values)

    def expire(self, key, sec):
        pass

    def execute(self):
        pass


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(payers_mod, 'redis_conn_get', lambda: fake)
    monkeypatch.setattr(payers_mod, '_synced_at', {})
    return fake


class TestTheOrder:
    async def test_payers_go_after_the_marked_and_before_the_rest(self, monkeypatch):
        from src.dating import mailing

        async def _marked(site_id):
            return {'m'}

        monkeypatch.setattr(mailing, '_marked', _marked)
        monkeypatch.setattr(payers_mod, 'payers_uids', lambda site_id: {'p'})
        people = [{'uid': 'a'}, {'uid': 'p'}, {'uid': 'b'}, {'uid': 'm'}]

        got = await mailing._by_priority(SITE, people)

        assert [one['uid'] for one in got] == ['m', 'p', 'a', 'b']
        assert [one['uid'] for one in got if one['priority']] == ['m'], \
            'платившему поставлен флаг «Приоритет» — инвайты позвали бы его всеми анкетами'

    async def test_no_marks_no_payers_keeps_the_feed(self, monkeypatch):
        from src.dating import mailing

        async def _marked(site_id):
            return set()

        monkeypatch.setattr(mailing, '_marked', _marked)
        monkeypatch.setattr(payers_mod, 'payers_uids', lambda site_id: set())
        people = [{'uid': 'a'}, {'uid': 'b'}]

        assert await mailing._by_priority(SITE, people) is people


@pytest.fixture
def api_site(monkeypatch):
    """Сайт на движке Ladadate, без панели: дорога — списки API."""
    async def _ready(site_id):
        return True

    async def _no_panel(site_id):
        return None

    monkeypatch.setattr('src.uvicorn.routers.dating.people._api_ready', _ready)
    monkeypatch.setattr(payers_mod, '_from_panel', _no_panel)


class TestTheSync:
    async def test_the_panel_gives_everyone_in_one_request(self, redis, monkeypatch):
        """Страница «Payments»: плательщик — столбец «From», «To» — наши анкеты."""
        html = ('<table><tr><th>Payment Type</th><th>Date</th><th>Hold To</th>'
                '<th>From</th><th>To</th><th>Amount</th></tr>'
                '<tr><td>Text Chat</td><td>d</td><td>d</td>'
                '<td><a href="/partner/agency/userprofileview/808675">808675 Peter</a></td>'
                '<td><a href="/partner/agency/userprofileview/418854">418854 V</a></td>'
                '<td>$0.15</td></tr></table>')
        calls = []

        async def _fetch(site_id, path, params=None, headers=None):
            calls.append(path)
            return html

        async def _url(site_id):
            return 'https://ladadate.com/partner/agency/profile'

        async def _ready(site_id):
            return True

        # ⚠ Строки — в том виде, в каком их отдаёт разбор живой страницы (замер
        # на проде 23.11.2026): ссылка ячейки — голый номер.
        cell = lambda text, link='': {'text': text, 'link': link, 'title': ''}
        rows = [[cell('Text Chat'), cell('d'), cell('d'), cell('808675 Peter', '808675'),
                 cell('418854 Valentina', '418854'), cell('$0.15')]]

        monkeypatch.setattr('src.dating.panel_fetch.panel_fetch_html', _fetch)
        monkeypatch.setattr('src.dating.panel_fetch.panel_fetch_table', lambda h: rows)
        monkeypatch.setattr('src.dating.panel._url', _url)
        monkeypatch.setattr('src.uvicorn.routers.dating.people._api_ready', _ready)

        got = await payers_mod._from_panel(SITE)

        assert calls == ['userpayments'], 'одного запроса к панели мало или много'
        assert '808675' in got and '418854' not in got, \
            'в платившие попали наши анкеты из столбца «To»'

    async def test_a_site_without_the_engine_is_not_asked(self, redis, monkeypatch):
        async def _ready(site_id):
            return False

        async def _panel(site_id):
            raise AssertionError('панель спрошена у чужого движка')

        monkeypatch.setattr('src.uvicorn.routers.dating.people._api_ready', _ready)
        monkeypatch.setattr(payers_mod, '_from_panel', _panel)

        assert await payers_mod.payers_sync(SITE) == 0

    async def test_the_answers_of_all_girls_are_joined(self, redis, api_site, monkeypatch, girl_make):
        one = await girl_make(site_id=STAND, online=True)
        two = await girl_make(site_id=STAND, online=True)
        # ⚠ По номеру анкеты: на стенде бывают и чужие анкеты в сети.
        lists = {one: [{'uid': '1'}, {'uid': '2'}], two: [{'uid': '2'}, {'uid': '3'}]}

        async def _payers(profile, base, password):
            return lists.get(int(profile['id']), [])

        async def _setting(site_id, key):
            return 'https://api' if key == 'api_base' else 'pw'

        monkeypatch.setattr('src.dating.people_api.people_api_payers', _payers)
        monkeypatch.setattr('src.dating.site_setting.site_setting', _setting)

        assert await payers_mod.payers_sync(STAND) == 3
        assert payers_mod.payers_uids(STAND) == {'1', '2', '3'}

    async def test_a_dead_site_keeps_the_old_order(self, redis, api_site, monkeypatch, girl_make):
        await girl_make(site_id=STAND, online=True)
        redis.sets[payers_mod.PAYERS_KEY.format(site_id=STAND)] = {'old'}

        async def _payers(profile, base, password):
            raise RuntimeError('сайт не отвечает')

        async def _setting(site_id, key):
            return 'https://api'

        monkeypatch.setattr('src.dating.people_api.people_api_payers', _payers)
        monkeypatch.setattr('src.dating.site_setting.site_setting', _setting)

        assert await payers_mod.payers_sync(STAND) == 0
        assert payers_mod.payers_uids(STAND) == {'old'}, \
            'отказ сайта стёр порядок, верный час назад'

    async def test_a_sync_waits_its_setting(self, redis, monkeypatch):
        """Срок — настройка сайта `payers_every` в часах; ноль — не обновлять."""
        hours = {'v': 2}

        async def _every(site_id):
            return hours['v'] * 3600

        async def _ready(site_id):
            return False

        monkeypatch.setattr(payers_mod, '_every', _every)
        monkeypatch.setattr('src.uvicorn.routers.dating.people._api_ready', _ready)

        assert await payers_mod.payers_due(STAND)
        await payers_mod.payers_sync(STAND)
        assert not await payers_mod.payers_due(STAND)

        payers_mod._synced_at[STAND] -= 2 * 3600 + 1
        assert await payers_mod.payers_due(STAND)

        hours['v'] = 0
        assert not await payers_mod.payers_due(STAND), 'ноль в настройке не выключил'

    def test_the_setting_is_hours_and_can_be_off(self):
        from src.dating.site_setting import SITE_KNOWN

        known = SITE_KNOWN['payers_every']
        assert known['kind'] == 'number' and int(known['min']) == 0
