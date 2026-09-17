"""Связь с RhapsodyAi: то, чего у этого проекта нет своего.

Проверяем не соседа, а **свою** сторону контракта: что запрос уходит куда надо,
ответ разбирается, а его отказ превращается в понятную фразу и в 502, а не в
пустой список.

⚠ **Сосед здесь поддельный** (`rhapsody_fake.py`), и это не упрощение, а условие:
тест против живого краснел бы от чужого перезапуска и молчал бы про то, ради чего
написан. Заодно появилось то, чего с живым не проверить вовсе, — занятый браузер,
протухший токен, оборванная связь: ждать их в нужную секунду нельзя.

Заглушка стоит на HTTP, поэтому дверь проверяется целиком: как строится путь, как
уезжает метка и токен, как код отказа превращается во фразу для журнала.
"""

import pytest

from src.rhapsody.rhapsody_api import (RHAPSODY_API_SITE_TAG, RhapsodyError,
                                       rhapsody_api_site_get, rhapsody_api_site_list)
from src.uvicorn.routers.site import site_list

pytestmark = pytest.mark.core
class TestSiteList:
    """Список сайтов: своей таблицы сайтов здесь нет."""

    async def test_list_comes_from_the_neighbour(self, rhapsody):
        rhapsody.sites = [{'id': 7, 'name': 'Ladadate', 'url': 'https://ladadate.com',
                           'tags': 'dating'}]

        sites = await rhapsody_api_site_list()

        assert [site['id'] for site in sites] == [7]
        assert {'id', 'name', 'url'} <= set(sites[0])

    async def test_tag_goes_to_the_neighbour(self, rhapsody):
        """⚠ Отбор по метке делает **он**, а не мы после ответа.

        У соседа в закладках лежит и его собственная админка, и рабочие стенды —
        анкету на них не заведёшь. Правило «что считать сайтом знакомств» должно
        жить там, где заводят закладки, поэтому метка уезжает в запросе.
        """
        rhapsody.sites = [{'id': 1, 'name': 'Ladadate', 'url': '', 'tags': 'dating'},
                          {'id': 2, 'name': 'Админка соседа', 'url': '', 'tags': 'admin'}]

        sites = await rhapsody_api_site_list()

        assert [site['id'] for site in sites] == [1], 'чужая закладка попала в селект'
        assert f'tag={RHAPSODY_API_SITE_TAG}' in rhapsody.calls[-1][1]

    async def test_empty_tag_asks_for_everything(self, rhapsody):
        await rhapsody_api_site_list(tag='')

        assert 'tag=' not in rhapsody.calls[-1][1]

    async def test_empty_list_is_not_an_error(self, rhapsody):
        """У соседа может не быть ни одного сайта с меткой — это ответ, а не сбой."""
        rhapsody.sites = []

        assert await rhapsody_api_site_list() == []


class TestSiteGet:
    """Одна закладка: адрес и настройка присутствия."""

    async def test_unknown_site_is_empty_not_error(self, rhapsody):
        """⚠ 404 от соседа — «такого сайта нет», а не сбой связи.

        Разница видна вызывающему: пустоту он превращает в 422 (ошибка запроса), а
        обрыв — в 502 (сосед недоступен). Смешай их — и панель отвечала бы «шлюз не
        отвечает» на опечатку в номере сайта.
        """
        assert await rhapsody_api_site_get(999_777) == {}

    async def test_other_refusals_are_raised(self, rhapsody):
        """А вот 409 пустотой не притворяется: сайт есть, просто сейчас не отдали."""
        rhapsody.refuse(409, 'предел сессий исчерпан: 25')

        with pytest.raises(RhapsodyError):
            await rhapsody_api_site_get(343)


class TestRefusalBecomesWords:
    """⚠ Код отказа превращается во фразу — в самой двери, а не у вызывающего.

    Текст уезжает в журнал анкеты и на страницу, и «409» там не говорит ничего
    никому: за ним стоит занятый пул вкладок, и лечится он не перезапуском панели,
    а свободной вкладкой у соседа.
    """

    @pytest.mark.parametrize('code, words', [
        (409, 'браузер соседа занят'),
        (401, 'токен не принят'),
        (422, 'сосед не принял запрос'),
        (500, 'сосед сломался внутри'),
        (503, 'сосед не отвечает'),
    ])
    async def test_code_turns_into_a_phrase(self, rhapsody, code, words):
        rhapsody.refuse(code, 'предел сессий исчерпан: 25')

        with pytest.raises(RhapsodyError) as refusal:
            await rhapsody_api_site_list()

        assert words in str(refusal.value)
        assert 'предел сессий исчерпан: 25' in str(refusal.value), 'сосед объяснил, мы потеряли'
        assert f'→ {code}' in str(refusal.value), 'кода нет — нечего искать в его логах'

    async def test_broken_link_says_it_is_the_neighbour(self, rhapsody):
        """Оборванная связь — отдельная ветка: у неё нет кода, и путать её с
        отказом значит не проверить одну из двух."""
        rhapsody.offline()

        with pytest.raises(RhapsodyError, match='RhapsodyAi недоступен'):
            await rhapsody_api_site_list()

    async def test_token_is_required(self, rhapsody, monkeypatch):
        """Без токена не стучимся вовсе: молчаливый 401 у соседа выглядел бы как
        «сайтов нет»."""
        from src.rhapsody import rhapsody_api

        monkeypatch.setattr(rhapsody_api, 'RHAPSODY_API_TOKEN', '')

        with pytest.raises(RhapsodyError, match='RHAPSODY_TOKEN'):
            await rhapsody_api_site_list()

        assert not rhapsody.calls, 'запрос ушёл без токена'


class TestSiteHandler:
    """Ручка `/api/site`: отдаёт снимок, а сверяет с соседом в фоне.

    ⚠ **502 отсюда не приходит вовсе.** Раньше приходил: список брали у соседа при
    каждом открытии, и его недоступность гасила селект, а с ним всю панель — без
    выбранного сайта нет ни анкет, ни профилей, ни журналов. Снимок и его сверка —
    в `test_site_snapshot.py`; здесь только то, что видно из этой ручки.
    """

    async def test_handler_returns_the_snapshot(self, rhapsody):
        from src.uvicorn.routers.dating.site_sync import site_sync_now

        await site_sync_now()
        answer = await site_list(user=None)

        assert {343, 19} <= {site['id'] for site in answer['result']}
        assert 'refreshing' in answer['sync'], 'странице нечем узнать, идёт ли сверка'

    async def test_unreachable_neighbour_keeps_the_select(self, rhapsody):
        from src.uvicorn.routers.dating.site_sync import site_sync_now

        await site_sync_now()
        rhapsody.offline()

        answer = await site_list(user=None)

        assert answer['result'], 'селект опустел вместе с соседом'


class TestSiteRoute:
    """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""

    async def test_closed_without_login(self, client):
        """Ручка под входом: служебный токен соседа живёт на сервере, и отдавать
        через неё что-либо анониму нельзя."""
        assert (await client.get('/api/site')).status_code == 401

    async def test_health_is_public(self, client):
        """Живность — без входа: её спрашивает compose и внешний опрос."""
        response = await client.get('/health')

        assert response.status_code == 200
        assert response.json()['status'] == 'ok'
