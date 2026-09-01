"""Поддельное API сайта знакомств: готовые ответы вместо живого ladadate.com.

⚠ **Сюита не ходит наружу вообще.** Правило то же, что и с соседом
(`rhapsody_fake.py`), и причин у него здесь даже больше: сайт знакомств — чужая
рабочая система, вход под живой анкетой считается там действием, а частые заходы
он считает подозрением и отвечает блокировкой. Блокировка стоит анкеты, а не
запроса.

Подменяется, как и у соседа, **транспорт httpx внутри модуля**, а не его функции:
так под проверкой остаётся всё, ради чего модуль написан, — как собирается путь,
как уезжает токен, как код отказа превращается во фразу для журнала («403 → сайт
просит подождать»), как протухший токен приводит к повторному входу.

## Как пользоваться

    async def test_список_приходит(site_api):
        site_api.users['online'] = [{'AccountId': '1', 'Name': 'Vincent', 'Age': 49}]

        rows = await dating_people_api_rows(PROFILE, 'online', SITE_API_FAKE_BASE, 'пароль')

Готовые положения сайта — методами: `refuse`, `offline`, `expire_token`. Что у
него лежит — поля `users`, `password`. Что у него спросили — `calls`; по нему
проверяется «вошли один раз, а не на каждый список».
"""

import json
import time

import httpx
import pytest

from src.dating import dating_people_api

# Адрес поддельного сайта. Настоящий не годится: тест не должен зависеть ни от
# доступности ladadate.com, ни от того, что там сегодня в списках.
SITE_API_FAKE_BASE = 'https://site.test/api'

# Пароль, который поддельный сайт считает верным.
SITE_API_FAKE_PASSWORD = 'верный-пароль'

# Что у сайта лежит по умолчанию. Поля — те же, что отдаёт живой (проверено на
# ladadate.com): по ним и написан разбор.
SITE_API_FAKE_USERS = {
    'my-favorites': [
        {'AccountId': '788856', 'Name': 'Jose', 'Age': 50, 'City': 'Almeria',
         'Country': 'Spain', 'CountryCode': 'ES', 'ProfilePhoto': None},
    ],
    'online': [
        {'AccountId': '812886', 'Name': 'Vincent pitchen', 'Age': 49, 'City': 'paris',
         'Country': 'France', 'CountryCode': 'FR',
         'ProfilePhoto': 'https://site.test/content/photos/7f23711de44941d4a2f8f9508b86b5de.jpg'},
        {'AccountId': '169410', 'Name': 'Zahir', 'Age': 51, 'City': 'Rome',
         'Country': 'Italy', 'CountryCode': 'IT', 'ProfilePhoto': None},
    ],
}


class SiteApiFake:
    """Сайт с заранее заготовленными ответами.

    Отвечает на те пути, которые модуль действительно спрашивает; на остальные —
    404, чтобы тест, ушедший не туда, узнал об этом сразу.
    """

    def __init__(self):
        self.users = {state: [dict(row) for row in rows]
                      for state, rows in SITE_API_FAKE_USERS.items()}
        self.password = SITE_API_FAKE_PASSWORD

        # Что у сайта спросили: `(метод, путь)`.
        self.calls: list[tuple[str, str]] = []

        # Токены, которые сайт считает своими. Выданные им же — чтобы «протухание»
        # можно было устроить, не подделывая подпись.
        self.tokens: set = set()

        self._refuse: tuple[int, dict, str] | None = None
        self._offline = ''

    # ── Положения сайта ──────────────────────────────────────────────────────

    def refuse(self, code: int, body: dict = None, on: str = '') -> None:
        """Сайт отвечает отказом. `on` — часть пути; пусто — отказ на всё."""
        self._refuse = (int(code), dict(body or {}), str(on))

    def offline(self, reason: str = 'connection refused') -> None:
        """Сайта нет: связь не устанавливается."""
        self._offline = str(reason)

    def expire_token(self) -> None:
        """Все выданные токены протухли — сайт отвечает на них 401.

        Ровно то, что происходит на живом каждые 12 часов, и ждать этого в тесте
        было бы нечем.
        """
        self.tokens.clear()

    # ── Ответы ───────────────────────────────────────────────────────────────

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))

        if self._offline:
            raise httpx.ConnectError(self._offline, request=request)

        if self._refuse:
            code, body, on = self._refuse
            if not on or on in path:
                return self._json(code, body)

        if path.endswith('/auth/login'):
            sent = json.loads(request.content or b'{}')
            if str(sent.get('Password') or '') != self.password:
                return self._json(400, {'Error': 'Invalid username or password',
                                        'Reason': 'ERROR_USER_PASSWORD_INVALID'})

            token = _token(hours=12)
            self.tokens.add(token)

            return self._json(200, {'Token': token})

        token = str(request.headers.get('Authorization') or '').removeprefix('Bearer ')
        if token not in self.tokens:
            return self._json(401, {'Error': 'Invalid or missing token'})

        state = path.rsplit('/', 1)[-1]
        if '/users/' in path and state in self.users:
            return self._json(200, {'Users': self.users[state]})

        return self._json(404, {'Error': f'заглушка не знает пути {path}'})

    @staticmethod
    def _json(code: int, body: dict) -> httpx.Response:
        return httpx.Response(code, content=json.dumps(body, ensure_ascii=False),
                              headers={'content-type': 'application/json'})


class _Httpx:
    """Подставной `httpx` для модуля: тот же, но клиент — с заглушкой."""

    HTTPError = httpx.HTTPError

    def __init__(self, fake: SiteApiFake):
        self._fake = fake

    def AsyncClient(self, **kwargs) -> httpx.AsyncClient:  # noqa: N802 — имя из httpx
        kwargs.pop('transport', None)

        return httpx.AsyncClient(transport=httpx.MockTransport(self._fake.handle), **kwargs)


class _FakeRedis:
    """Redis настолько, насколько его использует кэш токена."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttl[key] = ex

    def delete(self, key):
        self.store.pop(key, None)
        self.ttl.pop(key, None)


@pytest.fixture
def site_api(monkeypatch) -> SiteApiFake:
    """Поддельный сайт и поддельный Redis под токен — на время теста."""
    fake = SiteApiFake()
    fake.redis = _FakeRedis()

    monkeypatch.setattr(dating_people_api, 'httpx', _Httpx(fake))
    monkeypatch.setattr(dating_people_api, 'redis_conn_get', lambda: fake.redis)

    return fake


def _token(hours: int) -> str:
    """JWT, какой отдаёт живой сайт: три части, срок в полезной нагрузке.

    Подпись не подделываем и не проверяем — её проверяет сайт, а нам от токена
    нужен только `exp`, по которому считается срок хранения.
    """
    import base64

    def part(data: dict) -> str:
        raw = json.dumps(data, ensure_ascii=False).encode()

        return base64.urlsafe_b64encode(raw).decode().rstrip('=')

    # Латиницей: токен уезжает в заголовок `Authorization`, а заголовки — ascii.
    return '.'.join([part({'alg': 'HS256', 'typ': 'JWT'}),
                     part({'exp': int(time.time()) + hours * 3600,
                           'iat': int(time.time()), 'unique_name': 'test'}),
                     'signature-not-checked'])
