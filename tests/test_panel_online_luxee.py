"""Лента «Все онлайн» Luxee со страницы панели: разбор плиток и подкачка.

У Luxee нет кабинета под каждой девушкой — есть одна панель агентства, и
мужчины, которые сейчас на сайте, видны в ней разделом «Clients». Нам нужны
только **платящие** (`Payed`): бесплатный не может ответить, и писать ему —
тратить реплику.

Рамка `frames/luxee_clients_online.json` — урезанный ответ подкачки
`/clients/list/?token=…` (три плитки, имена заменены). Третья плитка
нарочно `Free`: фильтр сервера — первая линия, проверка плитки — вторая.
"""

import json
from pathlib import Path

import pytest

from src.dating.site.site_panel import DatingSitePanel, DatingSitePanelLuxee

pytestmark = pytest.mark.profile

SAMPLE = (Path(__file__).parent / 'frames' / 'luxee_clients_online.json'
          ).read_text(encoding='utf-8')


@pytest.fixture
def luxee():
    return DatingSitePanelLuxee({'driver': 'luxee'})


class TestTheDriverKnowsTheWay:
    def test_base_panel_has_no_online_feed(self):
        """⚠ Пустой путь — выключатель: у прочих сайтов лента идёт своей дорогой."""
        assert DatingSitePanel().ONLINE_PATH == ''

    def test_luxee_reads_clients_from_the_site_root(self, luxee):
        assert luxee.ONLINE_PATH == '/clients/list/'

    def test_the_server_filter_asks_online_and_payed(self, luxee):
        got = luxee.online_params('0')

        assert got['ClientsFilterForm[is_online]'] == '10'
        assert got['ClientsFilterForm[purchased]'] == '2'
        assert got['token'] == '0'


class TestTheSampleIsParsed:
    def test_only_payed_tiles_pass(self, luxee):
        rows, _ = luxee.online_page(SAMPLE)

        assert [row['uid'] for row in rows] == ['1001', '1002']

    def test_the_tile_becomes_our_card(self, luxee):
        rows, _ = luxee.online_page(SAMPLE)
        one = rows[0]

        assert one['name'] == 'Testname One'
        assert one['age'] == 60
        assert one['photo'].startswith('https://img.luxee.date/thumb/')
        assert one['online'] is True
        assert one['is_new'] is False
        assert one['link'] == ''
        assert one['city'] == 'Malelane, South Africa'

    def test_the_next_token_is_returned(self, luxee):
        _, token = luxee.online_page(SAMPLE)

        assert token == '1794829710.1003'


class TestTheEdges:
    def test_the_last_page_has_no_token(self, luxee):
        page = json.loads(SAMPLE)
        page['token'] = ''

        _, token = luxee.online_page(json.dumps(page))

        assert token == ''

    def test_the_blank_silhouette_is_no_photo(self, luxee):
        page = json.loads(SAMPLE)
        page['html'] = page['html'].replace(
            'https://img.luxee.date/thumb/', 'https://img.luxee.date/blank/male.png?')

        rows, _ = luxee.online_page(json.dumps(page))

        assert rows[0]['photo'] == ''

    def test_offline_tile_is_dropped(self, luxee):
        page = json.loads(SAMPLE)
        page['html'] = page['html'].replace(
            'label-success">Online', 'label-default">Offline', 1)

        rows, _ = luxee.online_page(json.dumps(page))

        assert [row['uid'] for row in rows] == ['1002']

    def test_not_json_is_a_refusal(self, luxee):
        """⚠ Страница входа вместо JSON — отказ, а не пустая лента: пустота
        погасила бы точки у всех разом и выглядела бы как «никого нет»."""
        with pytest.raises(ValueError):
            luxee.online_page('<html>login</html>')

    def test_success_false_is_a_refusal(self, luxee):
        with pytest.raises(ValueError):
            luxee.online_page(json.dumps({'success': False, 'html': '', 'token': ''}))

    def test_a_shouting_name_is_tamed(self, luxee):
        page = json.loads(SAMPLE)
        page['html'] = page['html'].replace('Testname One', 'VINCENT')

        rows, _ = luxee.online_page(json.dumps(page))

        assert rows[0]['name'] == 'Vincent'


# ── Подкачка: страница за страницей, пока сайт не скажет «конец» ───────────

def _page(uids: list[str], token: str) -> str:
    """Ответ подкачки с плитками этих номеров — из той же рамки."""
    one = json.loads(SAMPLE)['html'].split('<div class="profile-tile-wrap-outside"')[1]
    tiles = ''.join('<div class="profile-tile-wrap-outside"'
                    + one.replace('1001', uid) for uid in uids)

    return json.dumps({'success': True, 'html': tiles, 'token': token})


@pytest.fixture
def panel(monkeypatch):
    """Панель Luxee без сети: страницы по метке, журнал заходов."""
    import src.dating.panel_online as mod

    pages = {}
    asked = []

    async def fake_fetch(site_id, path, params=None, headers=None):
        asked.append({'path': path, 'params': dict(params or {}),
                      'headers': dict(headers or {})})
        answer = pages[params['token']]
        if isinstance(answer, Exception):
            raise answer

        return answer

    async def fake_presence(site_id):
        return {'driver': 'luxee'}

    async def fake_setting(site_id, key):
        return 'https://luxee.test/profile/' if key == 'admin_url' else ''

    monkeypatch.setattr(mod, 'panel_fetch_html', fake_fetch)
    monkeypatch.setattr(mod, 'site_presence', fake_presence)
    monkeypatch.setattr(mod, 'site_setting', fake_setting)

    return pages, asked


class TestThePagesAreWalked:
    async def test_all_pages_are_joined(self, panel):
        from src.dating.panel_online import panel_online_rows

        pages, asked = panel
        pages['0'] = _page(['1', '2'], 't1')
        pages['t1'] = _page(['3'], '')

        got = await panel_online_rows(7)

        assert [row['uid'] for row in got] == ['1', '2', '3']
        assert [one['params']['token'] for one in asked] == ['0', 't1']

    async def test_every_page_asks_as_the_page_script(self, panel):
        """⚠ Без `X-Requested-With` сайт отдаёт целую страницу, а не JSON."""
        from src.dating.panel_online import panel_online_rows

        pages, asked = panel
        pages['0'] = _page(['1'], '')

        await panel_online_rows(7)

        assert asked[0]['headers']['X-Requested-With'] == 'XMLHttpRequest'
        assert asked[0]['path'] == '/clients/list/'

    async def test_a_man_moving_between_pages_is_shown_once(self, panel):
        from src.dating.panel_online import panel_online_rows

        pages, _ = panel
        pages['0'] = _page(['1', '2'], 't1')
        pages['t1'] = _page(['2', '3'], '')

        got = await panel_online_rows(7)

        assert [row['uid'] for row in got] == ['1', '2', '3']

    async def test_the_same_token_does_not_loop(self, panel):
        from src.dating.panel_online import panel_online_rows

        pages, asked = panel
        pages['0'] = _page(['1'], 't1')
        pages['t1'] = _page(['2'], 't1')

        await panel_online_rows(7)

        assert len(asked) == 2

    async def test_a_broken_page_fails_the_whole_feed(self, panel):
        """⚠ Половина ленты погасила бы точки у второй половины."""
        from src.dating.panel_fetch import DatingPanelFetchError
        from src.dating.panel_online import panel_online_rows

        pages, _ = panel
        pages['0'] = _page(['1'], 't1')
        pages['t1'] = '<html>Log In</html>'

        with pytest.raises(DatingPanelFetchError):
            await panel_online_rows(7)


class TestTheRoadIsChosen:
    async def test_luxee_with_a_panel_takes_this_road(self, panel):
        from src.dating.panel_online import panel_online_on

        assert await panel_online_on(7) is True

    async def test_no_panel_address_no_road(self, panel, monkeypatch):
        import src.dating.panel_online as mod

        async def no_url(site_id, key):
            return ''

        monkeypatch.setattr(mod, 'site_setting', no_url)

        assert await mod.panel_online_on(7) is False

    async def test_other_sites_keep_their_road(self, panel, monkeypatch):
        import src.dating.panel_online as mod

        async def step2love(site_id):
            return {'driver': 'step2love'}

        monkeypatch.setattr(mod, 'site_presence', step2love)

        assert await mod.panel_online_on(7) is False

    async def test_the_feed_needs_no_girl(self, panel):
        """⚠ Дверь ленты — агентство: «никто не в сети» здесь не бывает."""
        import src.uvicorn.routers.dating.people as people

        assert await people._panel_door(7, 'online') == {
            'id': 0, 'site_id': 7, 'login': ''}
        assert await people._panel_door(7, 'favorite') == {}
