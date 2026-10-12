"""Снятие параметров анкеты со страницы админ-панели.

## ⚠⚠ Главный сторож здесь — отказ при сменившейся разметке

Форма разобралась, а знакомых имён в ней нет — значит сайт перевёрстан. Записать в
этот миг пустоту значило бы обнулить параметры у **всех** анкет сразу, по одному
нажатию кнопки. Пустой разбор — это отказ, а не пустой результат.
"""

import json
from pathlib import Path

import httpx
import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import panel_fetch as panel_fetch_module
from src.dating.json_column import json_column_load
from src.dating.profile_panel import (DatingProfilePanelError,
                                      DatingProfilePanelMarkupError,
                                      profile_panel_read)
from tests.panel_fake import PANEL_FAKE_STATE, PANEL_FAKE_URL

pytestmark = pytest.mark.profile

SITE = 999_783
SAMPLE = (Path(__file__).parent / 'frames' / 'ladadate_lady_edit.html'
          ).read_text(encoding='utf-8')
PRESENCE = {'driver': 'ladadate'}


@pytest.fixture
async def site(auth_client):
    """Сайт с заданной панелью и живой сессией. Что завела, то и убрала."""
    before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
              ['result']['settings'])

    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {'admin_url': PANEL_FAKE_URL,
                                             'admin_mark': 'Ladies profiles'}})

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET panel_state = %s WHERE site_id = %s',
                         (PANEL_FAKE_STATE, SITE))
        await db.connection.commit()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET panel_state = NULL WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()


@pytest.fixture
def page(monkeypatch):
    """Панель, отдающая страницу анкеты. Поле `html` — что она вернёт.

    ⚠ Подменяется **клиент внутри `panel_fetch`**, ровно как в `tests/panel_fake.py`,
    и по той же причине: под проверкой остаётся сборка адреса от корня панели, отбор
    cookie по домену и разбор исхода по коду ответа.

    ⚠ Застава общего слоя предлагает здесь `http_pool_transport` и
    `http_heartbeat_client` — оба не подходят, и это уже разобрано в `panel_fake.py`:
    первый даёт **настоящий** транспорт для живых соединений, второй — та самая
    функция, которую заглушка и подменяет.
    """

    class Page:
        def __init__(self):
            self.html = SAMPLE
            self.paths = []

        def answer(self, request: httpx.Request) -> httpx.Response:
            self.paths.append(request.url.path)

            return httpx.Response(200, text=self.html)

    fake = Page()

    def client(proxy: str = ''):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.answer),
                                 follow_redirects=True)

    monkeypatch.setattr(panel_fetch_module, 'http_heartbeat_client', client)

    return fake


async def _profile(profile_id: int, card: dict) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                         (json.dumps(card, ensure_ascii=False), int(profile_id)))
        await db.connection.commit()
        await db.execute('SELECT id, site_id, profile, profile_id FROM profile'
                         ' WHERE id = %s', (int(profile_id),))

        return dict(await db.fetchone())


async def _card(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile FROM profile WHERE id = %s',
                         (int(profile_id),))

        return json_column_load((await db.fetchone() or {}).get('profile'))


class TestItReads:
    async def test_parameters_land_in_the_card(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001', 'name': 'Olga'})

        await profile_panel_read(row, PRESENCE)

        got = (await _card(one))['parameters']

        assert got['country'] == 'Ukraine'
        assert got['height'] == '172 cm'
        assert got['birthday'] == '1990-01-21'

    async def test_the_path_carries_the_site_number(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001'})

        await profile_panel_read(row, PRESENCE)

        assert page.paths[-1].endswith('/userprofile/edit/100001')

    async def test_the_read_mark_is_set(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001'})

        got = await profile_panel_read(row, PRESENCE)

        assert len(got['parameters_at']) == 19, 'отметка не вида YYYY-MM-DD HH:MM:SS'
        assert (await _card(one))['parameters_at'] == got['parameters_at']

    async def test_the_other_card_fields_survive(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001', 'name': 'Olga',
                                   'about': 'мой текст'})

        await profile_panel_read(row, PRESENCE)

        got = await _card(one)

        assert got['name'] == 'Olga'
        assert got['about'] == 'мой текст'


class TestTheMarkupGuard:
    """⚠⚠ Пустой разбор — отказ, а не пустой результат."""

    async def test_a_changed_markup_is_refused(self, site, page, girl_make):
        """⚠ Своим типом, а не текстом: по нему ручка и отличает 502 от 422.

        Разбирай она исходы подстрокой в сообщении, перефразировка текста молча
        сменила бы код ответа.
        """
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001',
                                   'parameters': {'eyes': 'Hazel'}})
        page.html = '<form><input name="СовсемДругое" value="1"></form>'

        with pytest.raises(DatingProfilePanelMarkupError) as got:
            await profile_panel_read(row, PRESENCE)

        assert 'разметка' in str(got.value)

    async def test_the_markup_error_is_a_child_of_the_common_one(self):
        """⚠ Наследник, а не сосед: кому разница не нужна — ловит родителя."""
        assert issubclass(DatingProfilePanelMarkupError, DatingProfilePanelError)

    async def test_a_missing_number_is_not_a_markup_error(self, site, page,
                                                          girl_make):
        """⚠⚠ Обратная сторона наследования: родителем ловится и потомок, поэтому
        «нет номера» обязано быть проверяемо **не**-перевёрсткой — иначе ручка
        ответила бы на него 502 вместо 422."""
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'name': 'Olga'})
        row['profile_id'] = None

        with pytest.raises(DatingProfilePanelError) as got:
            await profile_panel_read(row, PRESENCE)

        assert not isinstance(got.value, DatingProfilePanelMarkupError)

    async def test_nothing_is_written_when_refused(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001',
                                   'parameters': {'eyes': 'Hazel'}})
        page.html = '<form><input name="СовсемДругое" value="1"></form>'

        with pytest.raises(DatingProfilePanelError):
            await profile_panel_read(row, PRESENCE)

        assert (await _card(one))['parameters'] == {'eyes': 'Hazel'}, \
            'прежние параметры обнулены сменившейся разметкой'


class TestTheRefusals:
    async def test_no_number_on_the_site(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'name': 'Olga'})
        row['profile_id'] = None

        with pytest.raises(DatingProfilePanelError) as got:
            await profile_panel_read(row, PRESENCE)

        assert 'номера на сайте' in str(got.value)

    async def test_a_site_without_a_panel_driver(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001'})

        with pytest.raises(DatingProfilePanelError) as got:
            await profile_panel_read(row, {'driver': 'goldenbride'})

        assert 'читать нечем' in str(got.value)
