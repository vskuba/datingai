"""Токен работает, пока живёт: вход — только когда его нет или сайт его отверг.

⚠⚠ **Правило простое, но нарушение его стоило суток работы сайта.** Круг ленты
«Все онлайн» ходит каждые две минуты. Пока вход получается — это один вход в 12
часов и три сотни запросов по готовому токену. Стоит же входу пойти в каждый
заход — и счётчик неудач на стороне сайта набирает своё за сутки:
`ERROR_SING_IN_BY_IP_DAILY_FAILED_LIMIT`, адрес закрыт для всех анкет разом.

Здесь стережётся два ответа на «когда входим»:

* **есть токен** — не входим никогда, сколько бы запросов ни шло;
* **сайт ответил `401`** — токен забываем и входим ровно один раз.

И отдельно — как выбирается анкета, от лица которой снимается лента. Список
собеседников у сайта один на всех: под какой девушкой ни смотри, ответ тот же.
Значит выбор — вопрос не правильности, а цены: берём ту, у которой токен уже
есть, и не тратим попытку входа.
"""

import pytest

from src.dating import site_api
from src.dating.site_api import (SITE_API_TOKEN_KEY,
                                        site_api_call,
                                        site_api_has_token)
from src.uvicorn.routers.dating import people

pytestmark = pytest.mark.profile

SITE = 999_343
BASE = 'https://example.invalid/api'

# Номера выдуманные: живую анкету тест не трогает даже в ветках, где входа нет.
PROFILE = {'id': 999_401, 'login': 'first@example.invalid'}


class _Redis:
    """Redis настолько, насколько его читает кэш токена."""

    def __init__(self, store: dict = None):
        self.store = dict(store or {})

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False

        self.store[key] = value

        return True

    def ttl(self, key):
        return 0

    def delete(self, key):
        self.store.pop(key, None)


class _Answer:
    """Ответ сайта: код и тело. Больше вызову ничего не нужно."""

    def __init__(self, status: int, body=None):
        self.status_code = status
        self.text = ''
        self._body = body if body is not None else {'result': []}

    def json(self):
        return self._body


@pytest.fixture
def site(monkeypatch):
    """Подменяет всё, что ходит наружу, и считает входы.

    Считаем именно **входы**, а не запросы: беда была в них.
    """
    seen = {'login': 0, 'request': 0, 'answers': []}

    async def _login(client, profile, base, password):
        seen['login'] += 1
        token = f'token-{seen["login"]}'
        site_api._token_put(int(profile['id']), token)

        return token

    # ⚠ Подпись повторяет живую целиком, включая анкету и дверь: транспорт пишет
    # ими строку в журнал запросов, и заглушка, не принявшая их, ловила бы
    # `TypeError` вместо проверяемого правила.
    async def _request(client, base, path, token, payload, verb,
                       profile=None, door=''):
        seen['request'] += 1
        seen.setdefault('tokens', []).append(token)
        answers = seen['answers']

        return answers.pop(0) if answers else _Answer(200)

    async def _proxy(profile):
        return ''

    monkeypatch.setattr(site_api, '_login', _login)
    monkeypatch.setattr(site_api, '_request', _request)
    monkeypatch.setattr(site_api, 'proxy_url', _proxy)
    monkeypatch.setattr(site_api, 'http_pool_transport', lambda proxy='': None)

    return seen


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(site_api, 'redis_conn_get', lambda: fake)

    return fake


class TestTokenReuse:
    """Пока токен живой, входа быть не должно."""

    @pytest.mark.asyncio
    async def test_first_call_logs_in_once(self, site, redis):
        """Первый запрос токена не имеет — входит и запоминает его."""
        await site_api_call(PROFILE, BASE, 'secret', '/users/online')

        assert site['login'] == 1
        assert redis.get(SITE_API_TOKEN_KEY.format(profile_id=PROFILE['id']))

    @pytest.mark.asyncio
    async def test_next_calls_reuse_token(self, site, redis):
        """Десять заходов подряд — вход по-прежнему один.

        Это и есть круг ленты: раз в две минуты, весь день. Сломайся правило —
        тут стало бы десять входов, а на сайте за сутки семьсот.
        """
        for _ in range(10):
            await site_api_call(PROFILE, BASE, 'secret', '/users/online')

        assert site['login'] == 1
        assert site['request'] == 10
        # Все запросы ушли с одним и тем же токеном — тем, что выдан на входе.
        assert set(site['tokens']) == {'token-1'}

    @pytest.mark.asyncio
    async def test_ready_token_skips_login(self, site, redis):
        """Токен уже лежит в Redis — входа нет вовсе, даже первого."""
        redis.set(SITE_API_TOKEN_KEY.format(profile_id=PROFILE['id']), 'ready')

        await site_api_call(PROFILE, BASE, 'secret', '/users/online')

        assert site['login'] == 0
        assert site['tokens'] == ['ready']

    @pytest.mark.asyncio
    async def test_401_relogins_once(self, site, redis):
        """Сайт отверг токен — забываем его и входим заново. Ровно один раз."""
        redis.set(SITE_API_TOKEN_KEY.format(profile_id=PROFILE['id']), 'stale')
        site['answers'] = [_Answer(401)]

        await site_api_call(PROFILE, BASE, 'secret', '/users/online')

        assert site['login'] == 1
        assert site['tokens'] == ['stale', 'token-1']

    @pytest.mark.asyncio
    async def test_after_401_the_new_token_serves(self, site, redis):
        """А следующий заход идёт уже новым токеном — и снова без входа."""
        redis.set(SITE_API_TOKEN_KEY.format(profile_id=PROFILE['id']), 'stale')
        site['answers'] = [_Answer(401)]

        await site_api_call(PROFILE, BASE, 'secret', '/users/online')
        await site_api_call(PROFILE, BASE, 'secret', '/users/online')

        assert site['login'] == 1
        assert site['tokens'][-1] == 'token-1'


class TestHasToken:
    """Публичная проверка, которой пользуется выбор анкеты."""

    def test_says_no_without_token(self, redis):
        assert site_api_has_token(PROFILE['id']) is False

    def test_says_yes_with_token(self, redis):
        redis.set(SITE_API_TOKEN_KEY.format(profile_id=PROFILE['id']), 'live')

        assert site_api_has_token(PROFILE['id']) is True

    def test_redis_failure_says_no(self, monkeypatch):
        """Сбой Redis отвечает «нет»: лишний вход дешевле молчаливой поломки."""
        def _boom():
            raise RuntimeError('redis лёг')

        monkeypatch.setattr(site_api, 'redis_conn_get', _boom)

        assert site_api_has_token(PROFILE['id']) is False


class TestPickProfile:
    """Кем идём за лентой: только в сети и, по возможности, с готовым токеном."""

    @pytest.fixture
    def rows(self, monkeypatch):
        """Подменяет базу: отдаёт то, что положили в `rows['out']`."""
        state = {'out': [], 'sql': ''}

        class _Db:
            async def execute(self, sql, args=None):
                state['sql'] = sql
                state['args'] = args

            async def fetchall(self):
                return state['out']

        class _Conn:
            async def __aenter__(self):
                return _Db()

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(people, 'mysql_get_db_async', lambda: _Conn())

        return state

    @pytest.mark.asyncio
    async def test_nobody_online_means_no_profile(self, rows, monkeypatch):
        """Никого в сети — пусто, и вызывающий на сайт не пойдёт.

        Ради этого всё и затевалось: спящей анкетой лента не снимается.
        """
        rows['out'] = []

        assert await people._site_profile(SITE) == {}

    @pytest.mark.asyncio
    async def test_only_online_asked_of_the_base(self, rows, monkeypatch):
        """В запрос уходит условие «в сети» — а не отбор в питоне после."""
        rows['out'] = [{'id': 1, 'site_id': SITE, 'login': 'a'}]
        monkeypatch.setattr(people, 'site_api_has_token', lambda one: False)

        await people._site_profile(SITE)

        assert "status = 'online'" in rows['sql']
        assert 'enabled = 1' in rows['sql']

    @pytest.mark.asyncio
    async def test_prefers_the_one_holding_a_token(self, rows, monkeypatch):
        """Из трёх онлайн берём ту, у которой токен, — даже если она не первая."""
        rows['out'] = [{'id': 11, 'site_id': SITE, 'login': 'a'},
                       {'id': 22, 'site_id': SITE, 'login': 'b'},
                       {'id': 33, 'site_id': SITE, 'login': 'c'}]
        monkeypatch.setattr(people, 'site_api_has_token',
                            lambda one: one == 22)

        got = await people._site_profile(SITE)

        assert got['id'] == 22

    @pytest.mark.asyncio
    async def test_no_token_anywhere_takes_the_first(self, rows, monkeypatch):
        """Токена нет ни у кого — берём первую: один вход на двенадцать часов."""
        rows['out'] = [{'id': 11, 'site_id': SITE, 'login': 'a'},
                       {'id': 22, 'site_id': SITE, 'login': 'b'}]
        monkeypatch.setattr(people, 'site_api_has_token', lambda one: False)

        got = await people._site_profile(SITE)

        assert got['id'] == 11


class TestRedisGivesBytes:
    """⚠⚠ Живой Redis отдаёт **байты**, и подделка обязана вести себя так же.

    **Здесь сюита была слепа, и это стоило недели простоя.** Клиент Redis заведён
    без `decode_responses`, то есть `get` возвращает `bytes`. Прежний код читал их
    через `str()` — а `str(b"eyJhbGci…")` даёт питоновское представление,
    `b'eyJhbGci…'`, вместе с буквой `b` и кавычками.

    Токен от этого становился негодным, сайт отвечал
    `401 IDX12709: CanReadToken() returned false`, и панель **входила заново на
    каждый запрос** вместо одного входа в 12 часов. Сайт счёл это перебором
    паролей и закрыл наш адрес.

    Тесты выше этого не ловили: их поддельный Redis отдавал `str`. Поэтому здесь
    он отдаёт байты — как настоящий.
    """

    @pytest.fixture
    def redis_bytes(self, monkeypatch):
        """Redis, отдающий байты. Ровно как живой."""
        class _Bytes(_Redis):
            def get(self, key):
                value = self.store.get(key)

                return value.encode() if isinstance(value, str) else value

        fake = _Bytes()
        monkeypatch.setattr(site_api, 'redis_conn_get', lambda: fake)

        return fake

    @pytest.mark.asyncio
    async def test_a_cached_token_still_works(self, site, redis_bytes):
        """Токен из байтов уходит на сайт **чистым**, без `b'…'`."""
        redis_bytes.set(SITE_API_TOKEN_KEY.format(profile_id=PROFILE['id']),
                        'eyJhbGci.payload.sig')

        await site_api_call(PROFILE, BASE, 'secret', '/users/online')

        assert site['login'] == 0, 'вошли заново при живом токене'
        assert site['tokens'] == ['eyJhbGci.payload.sig'], site['tokens']

    @pytest.mark.asyncio
    async def test_ten_calls_still_cost_one_login(self, site, redis_bytes):
        """⚠ Тот же счёт, что и с текстовым Redis: десять заходов — один вход.

        Сломайся чтение — здесь было бы десять входов, и это ровно та картина,
        которую живой журнал показал: `login 200 → 401 → login 200` на каждом
        круге.
        """
        for _ in range(10):
            await site_api_call(PROFILE, BASE, 'secret', '/users/online')

        assert site['login'] == 1, f'входов {site["login"]} вместо одного'

    def test_has_token_reads_bytes_too(self, redis_bytes):
        redis_bytes.set(SITE_API_TOKEN_KEY.format(profile_id=PROFILE['id']), 'live')

        assert site_api_has_token(PROFILE['id']) is True
