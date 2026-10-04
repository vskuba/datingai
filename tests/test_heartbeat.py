"""Присутствие на сайте: анкета видна в сети, пока идёт её смена.

⚠ **Наружу сюита не ходит** (`testing.md`): удар — это запрос на чужой сайт, и
проверяется здесь всё, кроме самого похода. Транспорт подменяется на уровне
`httpx` внутри общего движка, поэтому под проверкой остаётся то, ради чего он
написан: как собирается адрес, куда уезжают cookie, как читается примета живого
входа и сколько запросов уходит разом.

Живьём проверено вручную: вход сценарием, cookie сняты, страницы сайта под ними
открываются.
"""

import json

import httpx
import pytest

from http_ import http_heartbeat as engine
from http_.http_heartbeat import (http_heartbeat_cookies, http_heartbeat_ready,
                                  http_heartbeat_send, http_heartbeat_send_many)
from http_.http_heartbeat_step import http_heartbeat_step_ok

pytestmark = pytest.mark.profile
# Выдуманный сайт: настоящий не годится — сюита не ходит наружу.
BASE = 'https://site.test'

CONFIG = {'base': BASE,
          'beat': {'path': '/beat', 'body': 'mode=online'},
          'alive': [{'path': '/state', 'auth_field': 'auth'}]}

# Состояние браузера: свои cookie и чужие. ⚠ Чужие здесь не для полноты картины —
# на них держится проверка отбора по домену.
STATE = {'cookies': [
    {'name': 'AUTH', 'value': 'a1', 'domain': '.site.test'},
    {'name': 'SID', 'value': 's1', 'domain': 'www.site.test'},
    {'name': '_ga', 'value': 'g1', 'domain': '.google-analytics.com'},
    {'name': 'cf', 'value': 'c1', 'domain': '.cloudflare.com'},
]}


class _Site:
    """Сайт с заготовленными ответами: считает запросы и помнит, что пришло."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.cookies: list[dict] = []
        self.headers: list[dict] = []
        self.auth = True
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        self.cookies.append(dict(request.headers.get_list('cookie')
                                 and _jar(request) or {}))
        self.headers.append(dict(request.headers))

        if self.down:
            raise httpx.ConnectError('нет связи', request=request)

        if request.url.path == '/state':
            return httpx.Response(200, json={'auth': bool(self.auth)})

        return httpx.Response(200, text='')


def _jar(request: httpx.Request) -> dict:
    """Cookie запроса словарём — по ним видно, чьи ушли, а чьи нет."""
    raw = request.headers.get('cookie') or ''

    return dict(part.strip().split('=', 1) for part in raw.split(';') if '=' in part)


@pytest.fixture
def site(monkeypatch) -> _Site:
    """Поддельный сайт вместо живого — на время теста."""
    fake = _Site()

    class _Httpx:
        HTTPError = httpx.HTTPError
        Limits = httpx.Limits

        @staticmethod
        def AsyncClient(**kwargs):  # noqa: N802 — имя из httpx
            kwargs.pop('transport', None)

            return httpx.AsyncClient(transport=httpx.MockTransport(fake.handle),
                                     **kwargs)

    monkeypatch.setattr(engine, 'httpx', _Httpx)

    return fake


class TestConfigIsRequired:
    """⚠ Без настройки не начинают: «не настроено» и «не в сети» — разные вещи."""

    def test_empty_config_is_not_ready(self):
        assert http_heartbeat_ready({}) is False

    def test_base_alone_is_not_enough(self):
        """Корень без шагов — это адрес, а не описание присутствия."""
        assert http_heartbeat_ready({'base': BASE}) is False

    def test_beat_alone_is_enough(self):
        """Один удар без проверки — уже работа: сайт увидит активность."""
        assert http_heartbeat_ready({'base': BASE, 'beat': {'path': '/b'}}) is True


class TestCookiesAreFiltered:
    """⚠ Уходят только cookie своего домена, и это не гигиена, а маскировка.

    В состоянии браузера лежат чужие — аналитика, CDN. Отправленные скопом, они
    выдают клиент, который «слишком много знает»: у настоящей страницы сайта этих
    имён в запросе нет.
    """

    def test_own_cookies_are_taken(self):
        jar = http_heartbeat_cookies(STATE, BASE)

        assert jar == {'AUTH': 'a1', 'SID': 's1'}

    def test_subdomain_counts_as_own(self):
        """`www.site.test` — тот же сайт: cookie кладут то на домен, то на поддомен."""
        assert 'SID' in http_heartbeat_cookies(STATE, 'https://www.site.test/lk')

    def test_empty_state_gives_nothing(self):
        """Анкета не входила — cookie нет, и удар слать некуда."""
        assert http_heartbeat_cookies({}, BASE) == {}

    def test_no_base_gives_nothing(self):
        """Без корня отбирать не по чему — молчим, а не отдаём всё подряд."""
        assert http_heartbeat_cookies(STATE, '') == {}


class TestBeat:
    """Один удар: куда идёт и что несёт."""

    async def test_it_goes_to_the_configured_path(self, site):
        client = engine.http_heartbeat_client()
        try:
            assert await http_heartbeat_send(client, {'AUTH': 'a1'}, CONFIG) is True
        finally:
            await client.aclose()

        assert site.calls == [('POST', '/beat')]

    async def test_body_makes_it_a_post(self, site):
        """⚠ Метод выводится из тела: есть что послать — `POST`.

        Так настройка короче на строку в самом частом случае. Сказать явно можно
        всегда — это и проверяет соседний тест.
        """
        client = engine.http_heartbeat_client()
        try:
            await http_heartbeat_send(client, {'AUTH': 'a1'},
                                      {'base': BASE, 'beat': {'path': '/b'}})
        finally:
            await client.aclose()

        assert site.calls == [('GET', '/b')], 'шаг без тела ушёл не GET'

    async def test_cookies_travel_with_the_request(self, site):
        """⚠ Cookie уходят **с запросом**, а не лежат в клиенте.

        Клиент общий на все сессии, и запись в него означала бы, что соседняя
        анкета ходит под чужим входом. Проверяется тем, что сайт их получил.
        """
        client = engine.http_heartbeat_client()
        try:
            await http_heartbeat_send(client, {'AUTH': 'a1', 'SID': 's1'}, CONFIG)
        finally:
            await client.aclose()

        assert site.cookies[0] == {'AUTH': 'a1', 'SID': 's1'}

    async def test_without_cookies_nothing_goes(self, site):
        """Нет cookie — нет и запроса: бить за анкету, которая не входила, некуда."""
        client = engine.http_heartbeat_client()
        try:
            assert await http_heartbeat_send(client, {}, CONFIG) is False
        finally:
            await client.aclose()

        assert site.calls == []

    async def test_dead_site_does_not_raise(self, site):
        """Сайт лёг — `False`, а не исключение: круг обходит остальных дальше."""
        site.down = True

        client = engine.http_heartbeat_client()
        try:
            assert await http_heartbeat_send(client, {'AUTH': 'a1'}, CONFIG) is False
        finally:
            await client.aclose()


class TestManyAtOnce:
    """⚠ Ради этого модуль и написан: тысяча сессий одним клиентом."""

    async def test_every_session_gets_its_own_beat(self, site):
        sessions = {profile_id: {'AUTH': f'a{profile_id}'} for profile_id in range(1, 6)}

        client = engine.http_heartbeat_client()
        try:
            done = await http_heartbeat_send_many(client, sessions, CONFIG)
        finally:
            await client.aclose()

        assert done == {1: True, 2: True, 3: True, 4: True, 5: True}
        assert len(site.calls) == 5

    async def test_each_beat_carries_its_own_cookies(self, site):
        """⚠ Сессии не перепутались: у каждой свои cookie в своём запросе.

        Общий клиент — это ровно то место, где сессии могли бы слиться, и по
        одному лишь количеству запросов такую беду не увидеть.
        """
        sessions = {profile_id: {'AUTH': f'a{profile_id}'} for profile_id in range(1, 4)}

        client = engine.http_heartbeat_client()
        try:
            await http_heartbeat_send_many(client, sessions, CONFIG)
        finally:
            await client.aclose()

        assert sorted(jar['AUTH'] for jar in site.cookies) == ['a1', 'a2', 'a3']

    async def test_one_broken_session_does_not_stop_the_rest(self, site):
        """Одна сессия без cookie — остальные всё равно отмечаются.

        ⚠ Иначе одна протухшая анкета уводила бы с сайта всех прочих, и заметить
        причину было бы не по чему.
        """
        client = engine.http_heartbeat_client()
        try:
            done = await http_heartbeat_send_many(
                client, {1: {'AUTH': 'a1'}, 2: {}, 3: {'AUTH': 'a3'}}, CONFIG)
        finally:
            await client.aclose()

        assert done == {1: True, 2: False, 3: True}

    async def test_nobody_means_no_calls(self, site):
        """Пустой набор — ни одного запроса, а не ошибка."""
        client = engine.http_heartbeat_client()
        try:
            assert await http_heartbeat_send_many(client, {}, CONFIG) == {}
        finally:
            await client.aclose()

        assert site.calls == []


class TestAliveMark:
    """⚠ Примета живого входа: без неё сайт «всегда онлайн».

    Сайт отвечает `200` и гостю — выкинутая сессия получает страницу входа тем же
    кодом, что и работающая. Обнаружилось бы это в день, когда все сессии давно
    протухли, а панель показывает их в сети.
    """

    async def test_alive_when_the_field_says_so(self, site):
        client = engine.http_heartbeat_client()
        try:
            got = await engine.http_heartbeat_check(client, {'AUTH': 'a1'}, CONFIG)
        finally:
            await client.aclose()

        assert got['status'] == 'online'

    async def test_offline_when_the_field_says_no(self, site):
        """Сайт ответил `200`, но сказал «не ты» — это `offline`, а не `online`."""
        site.auth = False

        client = engine.http_heartbeat_client()
        try:
            got = await engine.http_heartbeat_check(client, {'AUTH': 'a1'}, CONFIG)
        finally:
            await client.aclose()

        assert got['status'] == 'offline'
        assert 'повторный логин' in got['error']

    async def test_unreachable_site_is_error_not_offline(self, site):
        """⚠ `error` и `offline` — разные исходы, и путать их дорого.

        Первый значит «до сайта не достучались», второй — «сайт нас не признал».
        Слив их в один, панель гоняла бы вход браузером из-за чужой сетевой аварии.
        """
        site.down = True

        client = engine.http_heartbeat_client()
        try:
            got = await engine.http_heartbeat_check(client, {'AUTH': 'a1'}, CONFIG)
        finally:
            await client.aclose()

        assert got['status'] == 'error'

    def test_mark_in_text_works_for_non_json(self):
        """Ответ не JSON — примету ищем подстрокой: у GWT-модулей иначе никак."""
        answer = httpx.Response(200, text='//OK[1,2]')

        assert http_heartbeat_step_ok({'ok_mark': '//OK'}, answer) is True
        assert http_heartbeat_step_ok({'ok_mark': '//OK'},
                                      httpx.Response(200, text='//EX')) is False

    def test_no_mark_falls_back_to_the_code(self):
        """Приметы нет — «раз ответил, значит жив». ⚠ Годна только с `no_redirect`."""
        assert http_heartbeat_step_ok({}, httpx.Response(200, text='')) is True
        assert http_heartbeat_step_ok({}, httpx.Response(403, text='')) is False

    def test_redirect_is_not_alive(self):
        """⚠⚠ Переадресация — это «иди авторизуйся», а не «ты авторизован».

        Настоящая ловушка, и на Ladadate она видна прямо: гость, идущий по
        переадресации, получает `200` со страницы входа — ровно как живая сессия.
        Различает их только `no_redirect`, и тогда у гостя виден `302`.

        Засчитывай мы `3xx` (правило «меньше 400» напрашивается само), сайт
        выглядел бы онлайн всегда, а обнаружилось бы это в день, когда все сессии
        давно протухли.
        """
        assert http_heartbeat_step_ok({}, httpx.Response(302, text='')) is False

    async def test_no_redirect_reaches_the_transport(self, site):
        """Ключ настройки доезжает до запроса, а не остаётся украшением."""
        client = engine.http_heartbeat_client()
        try:
            await http_heartbeat_send(
                client, {'AUTH': 'a1'},
                {'base': BASE, 'beat': {'path': '/lk', 'no_redirect': True}})
        finally:
            await client.aclose()

        assert site.calls == [('GET', '/lk')]

    def test_no_answer_is_not_alive(self):
        """Шага не было — вход не подтверждён: молчание не аргумент."""
        assert http_heartbeat_step_ok({}, None) is False


class TestOwnSettingWins:
    """⚠⚠ Есть своя настройка — к соседу не ходим вовсе.

    **Цена запасного пути, снятая с прода за час:** `/api/browser/site/19` —
    343 запроса. Два килобайта настроек, не менявшихся месяцами, спрашивались по
    анкете на каждом круге опроса; лежащий сосед гасил присутствие Goldenbride
    целиком, хотя удар шлём мы сами, своим клиентом и по своим cookie.

    Настройку перенесли миграцией `20260916150000_dating_goldenbride_heartbeat` —
    **тем самым переводчиком** `_from_neighbour`, что работает в бою, а не руками.
    Отсюда правило, которое и сторожит этот класс: своё старше соседского, и
    соседа не спрашивают даже «на всякий случай».
    """

    async def test_the_neighbour_is_not_asked(self, monkeypatch):
        """Сосед подменяется взрывом: дошёл вызов — тест падает с его причиной."""
        from src.dating import heartbeat

        async def _boom(site_id):
            raise AssertionError(f'пошли к соседу за сайтом #{site_id}, '
                                 f'имея свою настройку')

        monkeypatch.setattr(heartbeat, '_presence_of', _boom)

        async def _own(site_id, key, default=''):
            return json.dumps(CONFIG)

        monkeypatch.setattr('src.dating.heartbeat.site_setting',
                            _own)

        got = await heartbeat.heartbeat_config(19)

        assert got['base'] == BASE

    async def test_without_own_setting_the_neighbour_is_the_fallback(self, monkeypatch):
        """⚠ Запасной путь остаётся, и убирать его рано.

        Он нужен новому сайту, которому присутствие ещё не переносили: пока
        настройки нет, перевод соседского описания — единственное, чем анкета
        держится в сети. Уберём — и заведение сайта станет двухходовым.
        """
        from src.dating import heartbeat

        async def _empty(site_id, key, default=''):
            return ''

        async def _neighbour(site_id):
            return {'base': BASE, 'heartbeat': {'path': '/hb'}}

        monkeypatch.setattr('src.dating.heartbeat.site_setting',
                            _empty)
        monkeypatch.setattr(heartbeat, '_presence_of', _neighbour)

        got = await heartbeat.heartbeat_config(19)

        assert got == {'base': BASE, 'beat': {'path': '/hb'}}


class TestNeighbourConfigIsTranslated:
    """Старое описание соседа читается как запасное — с переводом ключей.

    ⚠ **Перевод делает код, а не человек**, и это не вопрос опрятности. Когда
    присутствие Goldenbride переезжало в нашу настройку, значение для миграции
    собрал этот самый `_from_neighbour`: перепиши его человек руками — и ошибка в
    GWT-теле или в `auth_field` всплыла бы не здесь, а погасшей колонкой «В сети».
    """

    def test_old_keys_become_new(self):
        from src.dating.heartbeat import _from_neighbour

        got = _from_neighbour({'base': BASE,
                               'heartbeat': {'path': '/hb'},
                               'status': [{'path': '/st', 'auth_field': 'auth'}]})

        assert got['beat'] == {'path': '/hb'}
        assert got['alive'] == [{'path': '/st', 'auth_field': 'auth'}]

    def test_chat_parts_are_dropped(self):
        """⚠ Кадры сокета не переводятся: движку они не нужны ни в каком виде.

        Тащить их значило бы вернуть ту самую связку с чатом, ради разрыва которой
        модуль и написан.
        """
        from src.dating.heartbeat import _from_neighbour

        got = _from_neighbour({'base': BASE, 'heartbeat': {'path': '/hb'},
                               'send': {'frame': {'method': 'sendPrivateMessage'}},
                               'driver': 'goldenbride', 'chat_url': f'{BASE}/chat'})

        assert set(got) == {'base', 'beat'}

    def test_config_without_steps_is_not_config(self):
        """Описание без шагов — не настройка: держать сайт им нечем."""
        from src.dating.heartbeat import _from_neighbour

        assert _from_neighbour({'base': BASE, 'driver': 'goldenbride'}) == {}

    def test_nothing_in_nothing_out(self):
        from src.dating.heartbeat import _from_neighbour

        assert _from_neighbour({}) == {}


class TestHandles:
    """Ручки: состояние, проверка и удар по кнопке."""

    async def test_state_without_site_is_empty(self, auth_client):
        answer = await auth_client.get('/api/dating/heartbeat?site_id=0')

        assert answer.status_code == 200
        assert answer.json()['result']['ready'] is False

    async def test_state_tells_what_is_configured(self, auth_client):
        """Сайт без настройки — `ready: false`, и это ответ, а не отказ."""
        answer = await auth_client.get('/api/dating/heartbeat?site_id=999777')
        got = answer.json()['result']

        assert answer.status_code == 200
        assert got['ready'] is False
        assert got['steps'] == []

    async def test_check_of_missing_profile_is_404(self, auth_client):
        answer = await auth_client.post('/api/dating/heartbeat/check',
                                        json={'profile_id': 99_999_999})

        assert answer.status_code == 404

    async def test_beat_of_missing_profile_is_404(self, auth_client):
        answer = await auth_client.post('/api/dating/heartbeat/beat',
                                        json={'profile_id': 99_999_999})

        assert answer.status_code == 404

    async def test_operator_may_look(self, dating_client):
        """Присутствие — работа оператора, а не настройка админа."""
        answer = await dating_client.get('/api/dating/heartbeat?site_id=999777')

        assert answer.status_code == 200

    async def test_closed_without_login(self, client):
        answer = await client.get('/api/dating/heartbeat?site_id=999777')

        assert answer.status_code in (401, 307)


class TestSiteSettingIsRegistered:
    """Настройка заведена в реестре: без этого её не показать и не сохранить."""

    async def test_registry_knows_heartbeat(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        keys = {row['key'] for row in got['known']}

        assert 'heartbeat' in keys, 'настройки нет в реестре — её не сохранить'
        assert 'heartbeat_every' in keys

    async def test_setting_has_its_own_group(self, auth_client):
        """⚠ Своя группа на странице «Сайты», а не «Чат сообщения».

        Присутствие от чата не зависит, и соседство в форме учило бы обратному:
        человек искал бы сокет там, где его нет и не нужно.
        """
        got = (await auth_client.get('/api/dating/site')).json()['result']
        group = {row['key']: row['group'] for row in got['known']}

        assert group['heartbeat'] == 'live'
        assert group['heartbeat'] != group.get('chat_check_every')

    async def test_config_is_json(self, auth_client):
        """⚠ Настройка — JSON, и мусор в ней не должен ронять круг.

        Она правится руками в поле формы, то есть сломанной бывает по определению.
        Разбор молча отдаёт пустоту, а пустая настройка значит «не настроено».
        """
        from src.dating.json_column import json_column_load

        assert json_column_load('{сломано}') == {}
        assert json_column_load(json.dumps(CONFIG))['base'] == BASE


class TestTheTwoPeriodsExplainThemselves:
    """⚠⚠ Два срока в группе «Присутствие», и подписи обязаны их разводить.

    Заказчик спросил прямо: «а в чём разница и зачем нужен второй параметр?» —
    и вопрос был законный, потому что подписи на него не отвечали.

    | | `heartbeat_every` | `probe_every` |
    |--|--|--|
    | что делает | шлёт один запрос `beat` | шлёт `beat` + `alive`, читает непрочитанные |
    | зачем | чтобы **сайт** показывал анкету онлайн | чтобы **панель** знала правду |
    | куда пишет | никуда | `status`, `last_seen_at`, `unread`, `last_error` |

    Первое — **действие** («я здесь»), второе — **вопрос с ответом**, на котором
    стоит автовосстановление входа. Отсюда и разная частота: бить часто,
    спрашивать редко.

    ⚠ **Одним параметром не обойтись ни в какую сторону.** Оставь только удар —
    панель не узнает о протухшей сессии: удар «проходит» и на мёртвом входе, и
    колонка «В сети» зеленела бы у анкеты, которой на сайте нет (это уже было,
    см. §4 про `beat` и `alive`). Оставь только опрос — присутствие провалится
    по частоте: раз в минуту мало, сайт гасит онлайн между заходами.
    """

    async def test_the_labels_do_not_look_alike(self, auth_client):
        """Подписи разводят «говорю» и «спрашиваю» — иначе вопрос вернётся."""
        got = (await auth_client.get('/api/dating/site')).json()['result']
        title = {row['key']: row['title'] for row in got['known']}

        assert 'говорить сайту' in title['heartbeat_every'], \
            'подпись присутствия снова не говорит, что это действие, а не вопрос'
        assert 'В сети' in title['probe_every'], \
            'подпись опроса снова не называет то, ради чего он идёт'

    async def test_the_probe_note_names_what_it_writes(self, auth_client):
        """⚠ У опроса в пояснении — то, что он пишет в панель.

        Без этого он выглядит проверкой ради проверки, и первым делом его
        выкручивают в «15 минут», не зная, что вместе с ним замедлится и
        автовосстановление входа.
        """
        got = (await auth_client.get('/api/dating/site')).json()['result']
        note = {row['key']: row['note'] for row in got['known']}

        assert 'автовосстановление' in note['probe_every']
        assert 'пишет ответ в панель' in note['probe_every']

    async def test_the_key_did_not_move(self, auth_client):
        """⚠⚠ Переименована **подпись**, а не ключ, и это осознанно.

        Ключ живёт в `site_.settings` у каждого сайта: переезд означал бы
        миграцию значений, и забытый сайт молча свалился бы на умолчание. Цена
        несоразмерна — ключ видит один разработчик, а человек читает подпись.
        """
        got = (await auth_client.get('/api/dating/site')).json()['result']
        keys = {row['key'] for row in got['known']}

        assert 'probe_every' in keys, 'ключ переехал — у сайтов слетят значения'
        assert 'heartbeat_every' in keys
