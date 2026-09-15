"""Журнал внешних запросов: каждый поход наружу виден человеку.

⚠⚠ **Написано по слепоте, которая стоила суток работы.** Круг ленты «Все онлайн»
стучался в `POST /auth/login` каждые две минуты, вход не проходил, за сутки
набралось 484 неудачи — и сайт закрыл наш IP целиком, всем анкетам разом. В
панели это выглядело как «списки не обновляются»: строки о том, что мы ломимся на
сайт каждые две минуты, не было нигде, кроме `docker logs`.

Здесь стережётся, что так больше не будет: **транспорт пишет строку сам**, и
обойти его, добавив новую дверь, нельзя — все двери к сайту идут через него.

⚠ Наружу сюита не ходит: транспорт подменяется, проверяется наша сторона.
"""

import json
import time

import pytest

from src.dating import dating_request, dating_site_api
from src.dating.dating_request import (DATING_REQUEST_DOORS, DATING_REQUEST_LOGIN,
                                       DATING_REQUEST_MAIL, DATING_REQUEST_PEOPLE,
                                       dating_request_ms, dating_request_write)

pytestmark = pytest.mark.core

SITE = 999_343
# Номер выдуманный: живую анкету тест не трогает.
PROFILE = {'id': 999_601, 'site_id': SITE, 'login': 'probe@example.invalid',
           'proxy_ip': '10.9.8.7'}


class _Answer:
    """Ответ сайта: код и тело. Больше транспорту ничего не нужно."""

    def __init__(self, status: int, text: str = ''):
        self.status_code = status
        self.text = text

    def json(self):
        return json.loads(self.text) if self.text else {}


@pytest.fixture
def written(monkeypatch):
    """Ловит строки журнала вместо записи в базу."""
    rows = []

    async def _write(**kwargs):
        rows.append(kwargs)

    monkeypatch.setattr(dating_request, 'dating_request_write', _write)

    return rows


class TestTransportWrites:
    """⚠ Строку пишет транспорт, а не вызывающий.

    Через `dating_site_api` идут все двери к сайту — вход, письма, списки,
    избранные, отправка. Записывай каждая у себя, полдюжины копий разошлись бы в
    мелочах: одна забыла бы миллисекунды, другая выход, третья не отличила бы
    `200` с `IsSuccess: false` от удачи.
    """

    @pytest.fixture
    def door(self, monkeypatch):
        """Подменяет сам HTTP и ловит то, что ушло в журнал."""
        rows = []

        async def _note(**kwargs):
            rows.append(kwargs)

        monkeypatch.setattr(dating_request, 'dating_request_write', _note)

        return rows

    @pytest.mark.asyncio
    async def test_a_good_answer_is_written(self, door, monkeypatch):
        class _Client:
            async def get(self, url, headers=None):
                return _Answer(200)

        await dating_site_api._request(_Client(), 'https://x/api', '/users/online',
                                       'token', None, 'GET', PROFILE,
                                       DATING_REQUEST_PEOPLE)

        assert len(door) == 1, 'строка в журнал не ушла'
        got = door[0]
        assert got['door'] == DATING_REQUEST_PEOPLE
        assert got['profile_id'] == PROFILE['id'] and got['site_id'] == SITE
        assert got['method'] == 'GET' and got['path'] == '/users/online'
        assert got['status'] == 200 and got['ok'] is True
        assert got['proxy_ip'] == '10.9.8.7', 'выход анкеты потерян'

    @pytest.mark.asyncio
    async def test_a_refusal_is_written_with_the_reason(self, door):
        """Отказ пишется с разобранной причиной, а не голым кодом."""
        class _Client:
            async def get(self, url, headers=None):
                return _Answer(400, '{"Message": "Login or password invalid"}')

        await dating_site_api._request(_Client(), 'https://x/api', '/auth/login',
                                       '', None, 'GET', PROFILE, DATING_REQUEST_LOGIN)

        got = door[0]
        assert got['ok'] is False and got['status'] == 400
        assert got['error'], 'причина отказа не записана'

    @pytest.mark.asyncio
    async def test_no_answer_is_written_too(self, door):
        """⚠ Обрыв связи — тоже строка, и код в ней нулевой.

        Именно такие походы и не были видны: сайт молчит, в панели тишина, а
        счётчик на его стороне растёт.
        """
        import httpx

        class _Client:
            async def get(self, url, headers=None):
                raise httpx.ConnectError('соединение оборвалось')

        with pytest.raises(dating_site_api.DatingSiteApiError):
            await dating_site_api._request(_Client(), 'https://x/api', '/users/online',
                                           't', None, 'GET', PROFILE,
                                           DATING_REQUEST_PEOPLE)

        assert len(door) == 1
        assert door[0]['status'] == 0 and door[0]['ok'] is False

    @pytest.mark.asyncio
    async def test_the_door_comes_from_the_caller(self, door):
        """⚠ Занятие называет вызывающий: по пути его не угадать.

        `/messages` — это и забор писем, и отправка; транспорт различить их не
        может, а человек по слову «письма» понимает сразу.
        """
        class _Client:
            async def get(self, url, headers=None):
                return _Answer(200)

        await dating_site_api._request(_Client(), 'https://x/api', '/messages', 't',
                                       None, 'GET', PROFILE, DATING_REQUEST_MAIL)

        assert door[0]['door'] == DATING_REQUEST_MAIL


class TestDoors:
    """Список дверей — один, и живёт он на сервере."""

    def test_every_door_has_a_human_name(self):
        """⚠ Страница берёт названия отсюда, своей копии не держит.

        Два списка разошлись бы, и вкладка показывала бы `mail` там, где журнал
        пишет «письма».
        """
        doors = [getattr(dating_request, name) for name in dir(dating_request)
                 if name.startswith('DATING_REQUEST_')
                 and isinstance(getattr(dating_request, name), str)
                 and not name.endswith(('SETTING', 'DOORS'))]

        for one in doors:
            assert one in DATING_REQUEST_DOORS, f'дверь «{one}» без имени для человека'


class TestMs:
    """Длительность считается монотонными часами."""

    def test_it_never_goes_negative(self):
        """⚠ Поправка NTP посреди запроса дала бы «-4 мс» там, где всё в порядке."""
        assert dating_request_ms(time.monotonic() + 5) == 0

    def test_it_measures(self):
        assert dating_request_ms(time.monotonic() - 0.25) >= 200


class TestWriteIsSafe:
    """⚠ Журнал не имеет права ронять работу."""

    @pytest.mark.asyncio
    async def test_a_broken_base_does_not_break_the_call(self, monkeypatch):
        """Упавшая запись — потерянная строка, а не потерянное письмо."""
        def _boom():
            raise RuntimeError('база легла')

        monkeypatch.setattr(dating_request, 'mysql_get_db_async', _boom)

        # Не бросает.
        await dating_request_write(door=DATING_REQUEST_LOGIN, profile_id=1)


class TestPathIsTheRealOne:
    """⚠ В журнале путь тот, что ушёл на самом деле.

    Поймано глазами на первой же живой строке: `GET userchatmessages` — без
    ведущего слеша и без дат, по которым панель и спрашивают. Вызывающие передают
    путь от корня панели, и в журнал он попадал как есть; человек читает это как
    оборванную строку.
    """

    @pytest.mark.asyncio
    async def test_the_panel_path_keeps_the_slash_and_query(self, written):
        from src.dating import dating_panel_fetch

        await dating_panel_fetch._note(SITE, 'userchatmessages',
                                       {'AccountId': 0, 'DateFrom': '14.09.2026'},
                                       200, '', time.monotonic())

        assert written[0]['path'].startswith('/userchatmessages?'), written[0]['path']
        assert 'DateFrom=14.09.2026' in written[0]['path']

    @pytest.mark.asyncio
    async def test_empty_params_do_not_leave_a_dangling_question_mark(self, written):
        from src.dating import dating_panel_fetch

        await dating_panel_fetch._note(SITE, '/page', None, 200, '', time.monotonic())

        assert written[0]['path'] == '/page'


class TestNotEveryFailureIsARequest:
    """⚠⚠ Наружу не пошли — строки быть не должно.

    Журнал запросов отвечает на вопрос «куда мы ходили». Запись о походе, которого
    не было, делает его ответ ложью — а по нему считают, сколько раз мы постучались
    к сайту, и решают, не мы ли виноваты в блокировке.
    """

    @pytest.mark.asyncio
    async def test_a_profile_without_a_socket_is_not_a_request(self, written):
        from src.dating.dating_send import _send_ws

        got = await _send_ws({'id': 1, 'site_id': SITE, 'ws_url': ''}, None,
                             '40317', 'привет', 'uid-1')

        assert got['ok'] is False
        assert not written, f'записали поход, которого не было: {written}'
