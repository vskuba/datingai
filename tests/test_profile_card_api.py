"""Ручки карточки: параметры наружу, внутрь и с панели."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from mysql_.mysql_ import mysql_get_db_async

from src.dating import panel_fetch as panel_fetch_module
from src.dating.profile_param import PROFILE_PARAM_KEYS
from tests.panel_fake import PANEL_FAKE_STATE, PANEL_FAKE_URL

pytestmark = pytest.mark.profile

SITE = 999_784
SAMPLE = (Path(__file__).parent / 'frames' / 'ladadate_lady_edit.html'
          ).read_text(encoding='utf-8')


@pytest.fixture
async def site(auth_client):
    """Сайт с панелью, описанием и живой сессией. Что завела, то и убрала.

    ⚠ `presence` обязателен: ручка берёт описание сайта сама (`profile_site`), и
    без него она отвечает «нет сайта с таким номером» — снимка списка у выдуманного
    номера нет и быть не может. Драйвер оттуда же, им выбирается разбор панели.
    """
    before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
              ['result']['settings'])

    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {'admin_url': PANEL_FAKE_URL,
                                             'admin_mark': 'Ladies profiles',
                                             'presence': json.dumps(
                                                 {'driver': 'ladadate',
                                                  'base': PANEL_FAKE_URL})}})

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

    ⚠ Подменяется клиент внутри `panel_fetch`, как в `tests/panel_fake.py`; доводы
    против кандидатов заставы (`http_pool_transport` даёт настоящий транспорт,
    `http_heartbeat_client` — та самая подменяемая функция) разобраны там же.
    """

    class Page:
        def __init__(self):
            self.html = SAMPLE

        def answer(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=self.html)

    fake = Page()
    monkeypatch.setattr(
        panel_fetch_module, 'http_heartbeat_client',
        lambda proxy='': httpx.AsyncClient(
            transport=httpx.MockTransport(fake.answer), follow_redirects=True))

    return fake


async def _uid(profile_id: int, uid: str) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                         (json.dumps({'id': uid}), int(profile_id)))
        await db.connection.commit()


class TestTheCardGives:
    async def test_the_frame_always_comes(self, auth_client, girl):
        got = (await auth_client.get(f'/api/dating/profile/{girl}/card')).json()

        assert list(got['result']['parameters']) == list(PROFILE_PARAM_KEYS)

    async def test_the_registry_comes_with_it(self, auth_client, girl):
        """⚠ Подписи отдаёт сервер: своя копия на странице разошлась бы молча."""
        got = (await auth_client.get(f'/api/dating/profile/{girl}/card')).json()
        fields = got['result']['parameter_fields']

        assert len(fields) == 21
        assert fields[0]['key'] == 'birthday'
        assert fields[0]['title'] == 'Дата рождения'

    async def test_the_read_mark_is_a_string_even_when_empty(self, auth_client, girl):
        """⚠ Поле отдаётся всегда: `undefined` на странице читался бы как
        «не снимали» случайно, а не по правилу."""
        got = (await auth_client.get(f'/api/dating/profile/{girl}/card')).json()

        assert got['result']['parameters_at'] == ''


class TestTheCardTakes:
    async def test_a_parameter_is_saved_and_marked(self, auth_client, girl):
        await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'eyes': 'Hazel'}})

        got = (await auth_client.get(f'/api/dating/profile/{girl}/card')).json()

        assert got['result']['parameters']['eyes'] == 'Hazel'
        assert 'parameters.eyes' in got['result']['edited']

    async def test_a_stranger_key_is_refused(self, auth_client, girl):
        """⚠ 422, а не молчаливый отсев: чужой ключ — ошибка вызывающего."""
        got = await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'шпион': 'да'}})

        assert got.status_code == 422

    async def test_a_too_long_area_is_refused(self, auth_client, girl):
        got = await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'hobby': 'x' * 2001}})

        assert got.status_code == 422

    async def test_a_too_long_text_is_refused(self, auth_client, girl):
        got = await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'eyes': 'x' * 191}})

        assert got.status_code == 422

    async def test_the_mark_can_be_given_back(self, auth_client, girl):
        await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'eyes': 'Hazel'}})

        got = await auth_client.delete(
            f'/api/dating/profile/{girl}/card/parameters.eyes')

        assert got.status_code == 200
        assert 'parameters.eyes' not in got.json()['result']['edited']


class TestTheRefreshButton:
    """Кнопка «Обновить с сайта»: какие коды она отдаёт на какие исходы.

    ## ⚠⚠ Обработчик зовётся **в процессе**, а не по HTTP

    Сюита ходит по HTTP против поднятого приложения, и это правильно почти везде.
    Но заглушка панели подменяет транспорт **в нашем процессе**, а обработчик за
    HTTP живёт в контейнере — там подмены нет, и он пошёл бы на живой адрес.
    Поэтому здесь прямой вызов, как у карточки собеседника
    (`test_companion_about.py`): проверяются коды ответа, а они и есть контракт
    ручки.

    ⚠ Отсюда же `_act` вместо `auth_client.post`: `Depends(auth_required)` при
    прямом вызове не срабатывает, и пользователя передаём доводом.
    """

    @staticmethod
    async def _act(profile_id: int):
        from src.uvicorn.routers.dating.profile_action import (
            profile_action_parameters)

        return await profile_action_parameters(profile_id, user={'id': 1})

    async def test_it_reads_the_panel(self, auth_client, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        await _uid(one, '100001')

        got = await self._act(one)

        assert got['result']['parameters']['country'] == 'Ukraine'

    async def test_a_changed_markup_answers_502(self, auth_client, site, page,
                                                girl_make):
        one = await girl_make(site_id=SITE)
        await _uid(one, '100001')
        page.html = '<form><input name="СовсемДругое" value="1"></form>'

        with pytest.raises(HTTPException) as got:
            await self._act(one)

        assert got.value.status_code == 502
        assert 'разметка' in got.value.detail

    async def test_no_number_answers_422(self, auth_client, site, page, girl_make):
        one = await girl_make(site_id=SITE)

        with pytest.raises(HTTPException) as got:
            await self._act(one)

        assert got.value.status_code == 422
        assert 'номера на сайте' in got.value.detail

    async def test_an_unknown_profile_answers_404(self, auth_client):
        """⚠ Этот — по HTTP: заглушка панели ему не нужна вовсе, отказ приходит
        раньше похода к ней, и проверить настоящий код ответа тут можно."""
        got = await auth_client.post(
            '/api/dating/profile/10000000/card/parameters')

        assert got.status_code == 404
