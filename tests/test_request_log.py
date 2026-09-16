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
    async def test_the_panel_path_is_the_full_one(self, written):
        """⚠⚠ Путь **от корня сайта**, а не от корня панели.

        Вызывающие передают его относительным (`userchatmessages`), потому что
        адрес собирается от корня панели. Запиши мы этот кусок как есть — в
        журнале стоит `/userchatmessages`, а на сайт ушло
        `/partner/agency/userchatmessages`: путь, которого на сайте нет вовсе.
        """
        from src.dating import dating_panel_fetch

        await dating_panel_fetch._note(
            SITE, 'https://ladadate.com/partner/agency', 'userchatmessages',
            {'AccountId': 0, 'DateFrom': '14.09.2026'}, 200, '', time.monotonic())

        assert written[0]['path'].startswith('/partner/agency/userchatmessages?'), \
            written[0]['path']
        assert 'DateFrom=14.09.2026' in written[0]['path']

    @pytest.mark.asyncio
    async def test_empty_params_do_not_leave_a_dangling_question_mark(self, written):
        from src.dating import dating_panel_fetch

        await dating_panel_fetch._note(SITE, 'https://x.test/root', '/page', None,
                                       200, '', time.monotonic())

        assert written[0]['path'] == '/root/page'


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


class TestNothingGoesOutside:
    """⚠⚠ Сторож сюиты: ни одного живого запроса наружу.

    **Написано по живому следу.** Сюита четыре раза за прогон входила на
    ladadate.com под живыми анкетами — и кормила счётчик неудачных входов, из-за
    которого сайт закрыл нам адрес целиком. Заметить это было нечем: тест зеленел,
    просто медленнее.

    Прежний сторож стоял на **сокете** и ставился `monkeypatch`'ем на каждый тест.
    Обе части и подвели: часть работы уходит в фоновые задачи, которые тест не
    ждёт (списки собеседников так и устроены), — они доживали до конца прогона,
    когда сторожа уже нет.

    Теперь запрет на **HTTP** и на **весь прогон** (`conftest.no_outside_http`).
    Здесь проверяется, что он на месте и что дыр в нём нет.
    """

    @pytest.mark.asyncio
    async def test_a_live_http_call_is_refused(self):
        """Обычный httpx наружу — отказ с внятной причиной."""
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            with pytest.raises(AssertionError, match='полез наружу'):
                await client.get('https://ladadate.com/api/auth/login')

    def test_a_sync_call_is_refused_too(self):
        """⚠ Синхронный клиент тоже: дверь одна, а стучатся в неё по-разному."""
        import httpx

        with httpx.Client(timeout=5) as client:
            with pytest.raises(AssertionError, match='полез наружу'):
                client.get('https://ladadate.com/')

    @pytest.mark.asyncio
    async def test_a_websocket_is_refused(self):
        """⚠ Вебсокет httpx не видит вовсе — у него свой стек.

        Чат и отправка ходят именно так, и без этой заглушки «запрет на HTTP»
        оставлял бы открытой дверь, через которую уходит живое сообщение человеку.
        """
        import websockets

        with pytest.raises(AssertionError, match='полез наружу'):
            await websockets.connect('wss://ladadate.com/chat')

    @pytest.mark.asyncio
    async def test_the_panel_itself_is_allowed(self, auth_client):
        """А сама панель — можно: через неё сюита и работает."""
        assert (await auth_client.get('/api/dating/site')).status_code == 200


class TestOnlyOnlineProfilesGoOut:
    """⚠⚠ Наружу ходим только под анкетой, которая в сети.

    **Три круга с одним и тем же промахом**, и каждый чинился отдельно: списки
    собеседников, избранные и — последним — забор писем. Все трое ходили по
    `enabled`, не глядя на состояние анкеты, а ходят они **под самой девушкой**:
    `POST /auth/login` её логином.

    Пока вход получался, это были лишние запросы. Когда общий агентский пароль
    перестал подходить — тринадцать неудачных входов каждые две минуты, девять
    тысяч в сутки. Счётчик у сайта суточный, и опустеть он не мог: мы сами не
    давали. Так запрет и держался неделю.
    """

    async def test_the_mail_round_skips_sleeping_profiles(self):
        """Забор писем: условие «в сети» стоит **в запросе**, а не в питоне после.

        Отбор после выборки означал бы, что база честно отдаёт всех, а забыть
        отсеять их может любая следующая правка.
        """
        import inspect

        from src.dating import dating_mail_in_round

        code = inspect.getsource(dating_mail_in_round._profiles)

        assert "status = 'online'" in code, \
            'круг писем снова ходит к спящим анкетам — это вход под каждой'

    async def test_the_favorite_round_skips_them_too(self):
        import inspect

        from src.dating import dating_favorite_round

        assert "status = 'online'" in inspect.getsource(dating_favorite_round._profiles)


class TestSuiteWritesNothing:
    """⚠⚠ Сюита не оставляет в журнале запросов ни строки.

    **Поймано глазами.** Рядом с настоящими отказами сайта в журнале лежали
    `GET /users/my-favorites → 403 Too many requests` — четыре штуки за четыре
    десятых секунды, из теста «сайт просит подождать». Отличить их от живых
    походов было нечем: дверь, путь и код у поддельного сайта те же.

    Цена ошибки не в мусоре: по этому журналу считают, сколько раз мы постучались
    к сайту на самом деле, и решают, ждать ли сутки до следующей попытки.
    """

    @pytest.mark.asyncio
    async def test_a_write_from_the_suite_is_dropped(self):
        """Прямая запись из теста никуда не идёт — выключатель поднят на прогон."""
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM dating_request')
            before = int((await db.fetchone())['n'])

        await dating_request_write(door=DATING_REQUEST_LOGIN, profile_id=1,
                                   site_id=SITE, path='/auth/login', status=400)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM dating_request')
            after = int((await db.fetchone())['n'])

        assert after == before, 'сюита написала в журнал внешних запросов'

    @pytest.mark.asyncio
    async def test_the_switch_can_be_lowered(self, monkeypatch):
        """⚠ Сам выключатель работает в обе стороны — иначе он молча гасил бы
        журнал и на живой установке."""
        rows = []

        async def _note(**kwargs):
            rows.append(kwargs)

        monkeypatch.setattr(dating_request, 'mysql_get_db_async', _fake_db(rows))
        dating_request.dating_request_off(False)
        try:
            await dating_request_write(door=DATING_REQUEST_LOGIN, profile_id=1)
        finally:
            dating_request.dating_request_off(True)

        assert rows, 'с опущенным выключателем запись не пошла'


def _fake_db(rows):
    """Подделка базы: запоминает, что запись дошла до `INSERT`."""
    class _Db:
        async def execute(self, sql, args=None):
            rows.append(args)

        @property
        def connection(self):
            class _Conn:
                async def commit(self):
                    return None

            return _Conn()

    class _Ctx:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, *args):
            return False

    return lambda: _Ctx()


class TestNeighbourIsExternalToo:
    """⚠⚠ Сосед — тоже наружу, и его походы обязаны быть в журнале.

    **Исправление ошибки суждения.** Сперва RhapsodyAi исключили с доводом «он не
    внешний мир, а вторая половина установки». Довод неверен: ходим мы к нему по
    HTTP, он падает, отказывает и тормозит ровно как чужой сайт. Человек, глядя в
    журнал, видел не «все внешние запросы», а «часть» — и не знал, какую.

    Через соседа идут прогоны сценариев, вызовы LLM, справочник сайтов и агенты:
    это и медленнее всего (прогон — десятки секунд), и чаще всего виновато, когда
    «ничего не работает».
    """

    @pytest.fixture
    def door(self, monkeypatch):
        rows = []

        async def _note(**kwargs):
            rows.append(kwargs)

        monkeypatch.setattr(dating_request, 'dating_request_write', _note)

        return rows

    @pytest.mark.asyncio
    async def test_a_good_call_is_written(self, door, rhapsody):
        from src.rhapsody.rhapsody_api import rhapsody_api_site_list

        rhapsody.sites = [{'id': 7, 'name': 'Ladadate', 'url': 'https://x.test'}]
        await rhapsody_api_site_list()

        assert door, 'поход к соседу не попал в журнал'
        assert door[0]['door'] == 'rhapsody' and door[0]['ok'] is True
        assert door[0]['status'] == 200

    @pytest.mark.asyncio
    async def test_a_refusal_is_written(self, door, rhapsody):
        """Отказ соседа — строка с его кодом и разобранной причиной."""
        from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_list

        rhapsody.refuse(409, 'предел сессий исчерпан: 25')

        with pytest.raises(RhapsodyError):
            await rhapsody_api_site_list()

        assert door[0]['ok'] is False and door[0]['status'] == 409
        assert door[0]['error'], 'причина отказа не записана'

    @pytest.mark.asyncio
    async def test_no_profile_is_attached(self, door, rhapsody):
        """⚠ Анкеты у строки нет, и это не упущение.

        К соседу ходят и по анкете (прогон сценария), и по сайту (справочник,
        агенты), и вовсе без них (проверка выходов). Дверь про **соседа**, а не про
        девушку; приписать ей анкету значило бы соврать в двух случаях из трёх.
        """
        rhapsody.sites = []
        from src.rhapsody.rhapsody_api import rhapsody_api_site_list

        await rhapsody_api_site_list()

        # ⚠ Ключа может не быть вовсе: дверь его просто не передаёт, а умолчание
        # у `dating_request_write` — ноль. Проверяем смысл, а не форму вызова.
        assert door[0].get('profile_id', 0) == 0

    @pytest.mark.asyncio
    async def test_the_door_has_a_human_name(self):
        from src.dating.dating_request import (DATING_REQUEST_DOORS,
                                               DATING_REQUEST_RHAPSODY)

        assert DATING_REQUEST_DOORS[DATING_REQUEST_RHAPSODY] == 'сосед RhapsodyAi'


class TestSitelessRowsAreVisible:
    """⚠⚠ Записать и не показать — хуже, чем не записать.

    **Поймано глазами.** Походы к соседу писались в журнал исправно — двести с
    лишним в час, — но лента отбирала `WHERE site_id = %s`, а у них `site_id = 0`.
    В итоге во вкладке «Запросы» не было **ни одной** строки соседа, и человек
    законно решил, что их не пишут вовсе.

    Молчаливое враньё о полноте опаснее дыры: дыру ищут, а полному на вид журналу
    верят.

    ⚠ Ноль здесь не «потерялся», а значит «поход не про конкретный сайт»: к соседу
    ходят за справочником, агентами и проверкой выходов — сайта там нет и быть не
    может.
    """

    def test_the_query_lets_site_less_rows_through(self):
        """Проверяем сам отбор: поднять ленту без базы дороже, чем она стоит."""
        import inspect

        from src.uvicorn.routers.dating import dating_request as handle

        code = inspect.getsource(handle.dating_request_list)

        assert 'r.site_id = 0' in code, \
            'лента снова прячет походы без сайта — соседа в ней не будет'

    @pytest.mark.asyncio
    async def test_a_neighbour_row_has_no_site(self, written, rhapsody):
        """⚠ И сама дверь сайта не приписывает — иначе это была бы неправда.

        К соседу ходят и по анкете, и по сайту, и вовсе без них. Приписать строке
        сайт значило бы соврать в двух случаях из трёх.
        """
        from src.rhapsody.rhapsody_api import rhapsody_api_site_list

        rhapsody.sites = []
        await rhapsody_api_site_list()

        assert written[0].get('site_id', 0) == 0
