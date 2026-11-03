"""Звезда избранного: у кого ручкой, у кого сценарием.

## ⚠⚠⚠ Что здесь стережётся

У Step2Love сценариев избранных **нет вовсе** — ни «в избранные», ни «из
избранных» (спрошено у соседа 03.11.2026), и занятие для него не работало
никак: круг ходил, упирался в «сценарий не найден» и шёл дальше. Зато нашлась
ручка — та же, что дёргает его собственная страница чата по щелчку на
звёздочке.

Сайт при этом отвечает **`200` и на отказ**: без заголовка `X-Requested-With`
в теле приезжает «look like not Ajax». Проверка по коду ответа сочла бы звезду
зажжённой, человек увидел бы в журнале «добавлен», а на сайте ничего бы не
произошло. Поэтому здесь проверяется не «запрос ушёл», а **что именно ушло** и
**как читается ответ**.

Живая проба 03.11.2026: без заголовка — отказ, с ним
`{"status":"OK","reason":"Successfully added"}`, и номер появился в
`favoriteList` кадра авторизации.
"""

import json

import pytest

from src.dating.site.site_favorite import (DatingSiteFavorite,
                                           DatingSiteFavoriteStep2Love)
from src.dating.site.site_registry import site_registry_favorite

pytestmark = pytest.mark.profile

PRESENCE = {'base': 'https://example.com', 'driver': 'step2love',
            'send': {'base': 'https://chat.example.com'}}
PROFILE = {'id': 78, 'site_id': 999_345, 'profile_id': '1803186',
           'storage_state': '{"cookies": []}'}


class _Answer:
    """Ответ сайта: код и тело. ⚠ Код и приговор здесь **разные** вещи."""

    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class TestTheDriverIsChosenBySite:
    """⚠ Сайт со своей дорогой — драйвером, прочие — прежним сценарием."""

    def test_step2love_goes_by_handle(self):
        assert site_registry_favorite(PRESENCE).by_handle() is True

    @pytest.mark.parametrize('driver', ['goldenbride', 'ladadate', '', 'нет'])
    def test_everyone_else_keeps_the_scenario(self, driver):
        """⚠⚠ Красный здесь значит, что у живого сайта отняли рабочую дорогу.

        У прочих сценарии есть и работают; подмени их ручкой, которой у них
        нет, — звезда перестала бы ставиться молча.
        """
        got = site_registry_favorite({'driver': driver})

        assert got.by_handle() is False
        assert isinstance(got, DatingSiteFavorite)

    def test_no_presence_is_the_base_one(self):
        assert site_registry_favorite(None).by_handle() is False


class TestTheBaseDriverSaysItCannot:
    """⚠ Базовый отвечает «не умею», а не делает вид, что сделал.

    Тихий `{'ok': False}` здесь был бы хуже исключения: занятие сочло бы это
    отказом сайта и записало неудачу в журнал анкеты, хотя дорога просто
    другая.
    """

    @pytest.mark.parametrize('what', ['add', 'forget', 'uids'])
    async def test_it_raises(self, what):
        driver = DatingSiteFavorite({})

        with pytest.raises(NotImplementedError):
            await (getattr(driver, what)(PROFILE, '1')
                   if what != 'uids' else driver.uids(PROFILE))


class TestWhatGoesToTheSite:
    """⚠⚠ Проверяется **что ушло**, а не «запрос состоялся»."""

    async def test_the_add_carries_the_command_and_the_header(self, monkeypatch):
        from src.dating.site import site_favorite as mod

        seen = {}

        async def post(profile, url, data=None, headers=None, **kw):
            seen.update({'url': url, 'data': data, 'headers': headers})

            return _Answer({'status': 'OK'})

        monkeypatch.setattr(mod, 'site_session_post', post)

        got = await DatingSiteFavoriteStep2Love(PRESENCE).add(PROFILE, '1814826')

        assert got['ok'] is True
        assert seen['url'] == 'https://example.com/ajax.php?com=ajax'
        assert json.loads(seen['data']['jsonData']) == {
            'cmd': 'add_favorites', 'userID': 1814826}
        assert seen['headers']['X-Requested-With'] == 'XMLHttpRequest', (
            'без этого заголовка сайт отвечает отказом под кодом 200')

    async def test_the_forget_differs_only_by_the_word(self, monkeypatch):
        from src.dating.site import site_favorite as mod

        seen = {}

        async def post(profile, url, data=None, headers=None, **kw):
            seen.update(data or {})

            return _Answer({'status': 'OK'})

        monkeypatch.setattr(mod, 'site_session_post', post)

        await DatingSiteFavoriteStep2Love(PRESENCE).forget(PROFILE, '1814826')

        assert json.loads(seen['jsonData'])['cmd'] == 'del_favorites'

    async def test_it_goes_through_the_session_door(self):
        """⚠⚠ Своего клиента здесь нет: cookie и выход наружу — общей дверью.

        Заведи свой рядом — сайт увидел бы у одной учётки два адреса, потому
        что чтение ходило бы через прокси анкеты, а запись напрямую.
        """
        import inspect

        from src.dating.site import site_favorite as mod

        body = inspect.getsource(mod)

        assert 'site_session_post' in body
        assert 'AsyncClient' not in body, 'появился свой клиент мимо двери'


class TestTheCodeIsNotTheVerdict:
    """⚠⚠⚠ `200` у этого сайта ещё не удача — приговор лежит в теле.

    Без заголовка `X-Requested-With` сайт отвечает `200` и телом «look like
    not Ajax». Проверь мы код — звезда считалась бы зажжённой, журнал сказал
    бы «добавлен», а на сайте не изменилось бы ничего.
    """

    async def test_an_error_body_under_200_is_a_refusal(self, monkeypatch):
        from src.dating.site import site_favorite as mod

        async def post(*a, **kw):
            return _Answer({'status': 'error', 'reason': 'look like not Ajax'},
                           200)

        monkeypatch.setattr(mod, 'site_session_post', post)

        got = await DatingSiteFavoriteStep2Love(PRESENCE).add(PROFILE, '1')

        assert got['ok'] is False
        assert 'look like not Ajax' in got['error']

    async def test_a_body_without_status_is_a_refusal_too(self, monkeypatch):
        """⚠ Непонятный ответ — отказ, а не удача: цена ошибки несимметрична."""
        from src.dating.site import site_favorite as mod

        async def post(*a, **kw):
            return _Answer('не json', 200)

        monkeypatch.setattr(mod, 'site_session_post', post)

        got = await DatingSiteFavoriteStep2Love(PRESENCE).add(PROFILE, '1')

        assert got['ok'] is False

    async def test_a_dead_site_is_a_reason_not_a_crash(self, monkeypatch):
        """⚠ Ничего не бросает: зовёт круг, обходящий анкеты сайта."""
        from src.dating.site import site_favorite as mod

        async def boom(*a, **kw):
            raise RuntimeError('сайт не ответил')

        monkeypatch.setattr(mod, 'site_session_post', boom)

        got = await DatingSiteFavoriteStep2Love(PRESENCE).add(PROFILE, '1')

        assert got['ok'] is False and 'сайт не ответил' in got['error']


class TestTheListComesFromTheSocketFrame:
    """⚠⚠ Список избранных приезжает в кадре авторизации, а не запросом.

    Тем же открытием соединения, которым идут отправка и приглашения, — то
    есть лишнего похода на сайт он не стоит вовсе.
    """

    async def test_it_reads_the_favorite_list(self, monkeypatch):
        from src.dating.site import site_favorite as mod

        async def open_(*a, **kw):
            return {'client': None, 'socket_id': 'sid',
                    'frames': [{'data': {'favoriteList': [1814826, 2]}}]}

        monkeypatch.setattr(mod, 'chat_socket_open', open_)

        got = await DatingSiteFavoriteStep2Love(PRESENCE).uids(PROFILE)

        assert got == {'1814826', '2'}

    async def test_an_empty_list_is_an_answer(self, monkeypatch):
        """⚠ Пусто — это «избранных нет», законный ответ."""
        from src.dating.site import site_favorite as mod

        async def open_(*a, **kw):
            return {'client': None, 'socket_id': 'sid',
                    'frames': [{'data': {'favoriteList': []}}]}

        monkeypatch.setattr(mod, 'chat_socket_open', open_)

        assert await DatingSiteFavoriteStep2Love(PRESENCE).uids(PROFILE) == set()

    async def test_a_dead_socket_raises(self, monkeypatch):
        """⚠⚠⚠ «Список не пришёл» обязан отличаться от «избранных нет».

        Спутай их — и одно моргание сайта погасило бы всех живых избранных
        разом: сверка считает пропавших из списка ушедшими.
        """
        from src.dating.site import site_favorite as mod

        async def boom(*a, **kw):
            raise RuntimeError('соединение не открылось')

        monkeypatch.setattr(mod, 'chat_socket_open', boom)

        with pytest.raises(Exception):
            await DatingSiteFavoriteStep2Love(PRESENCE).uids(PROFILE)
