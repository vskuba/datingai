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

⚠ Подменяется `dating_site_api` — **общая дверь к сайту**. Через неё ходят и списки
собеседников, и отправка писем; заглушка на одном из двух вызывающих означала бы,
что второй ходит на живой сайт.

## Как пользоваться

    async def test_список_приходит(site_api):
        site_api.users['online'] = [{'AccountId': '1', 'Name': 'Vincent', 'Age': 49}]

        rows = await dating_people_api_rows(PROFILE, 'online', SITE_API_FAKE_BASE, 'пароль')

Готовые положения сайта — методами: `refuse`, `offline`, `expire_token`,
`forbid_send`. Что у него лежит — поля `users`, `password`. Что у него спросили —
`calls`; по нему проверяется «вошли один раз, а не на каждый список». Что ушло —
`sent`.

⚠ **Письма никуда не уходят.** Это главное, ради чего заглушка нужна отправке:
`POST /messages/send` на живом сайте — необратимое действие, и одна неосторожная
сюита означала бы полсотни писем незнакомым людям.
"""

import asyncio
import json
import time

import httpx
import pytest

from src.dating import dating_site_api

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


# Входящие письма живого вида: в списке тела нет.
SITE_API_FAKE_LETTERS = [
    {'MessageId': 201, 'DatePost': '2026-09-09T09:29:46Z', 'IsReplied': False,
     'User': {'AccountId': '181685', 'Name': 'Semen', 'Age': 46, 'City': 'Krakow',
              'Country': 'Poland', 'CountryCode': 'PL',
              'ProfilePhoto': 'https://site.test/content/photos/a1.jpg'}},
    {'MessageId': 202, 'DatePost': '2026-09-09T10:02:11Z', 'IsReplied': False,
     'User': {'AccountId': '812886', 'Name': 'Vincent', 'Age': 49, 'City': 'paris',
              'Country': 'France', 'CountryCode': 'FR', 'ProfilePhoto': None}},
]

# Тела — отдельным ответом, как у живого сайта.
SITE_API_FAKE_BODIES = {201: 'Moto is my life', 202: 'Hello from Paris'}


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

        # Насколько ответ уступает цикл событий. Ноль — «один оборот»: этого
        # довольно, чтобы вторая задача успела войти, пока первая ждёт ответа.
        self.pause: float = 0.0

        # ⚠ И полные адреса — **с запросом**. `calls` держит голый путь, и по нему
        # проверяют «куда сходили»; но у части ручек смысл несёт именно запрос
        # (`?startDate=…` у писем), а в пути его нет вовсе.
        self.urls: list[str] = []

        # Токены, которые сайт считает своими. Выданные им же — чтобы «протухание»
        # можно было устроить, не подделывая подпись.
        self.tokens: set = set()

        # Письма, которые «ушли»: `(uid, тело, CheckId)`. По ним и проверяется, что
        # отправка дошла до сайта — и что ушло ровно то, что лежало в пуле.
        self.sent: list[tuple[str, str, str]] = []

        # Разрешения, выданные `check-send`. ⚠ Сайт принимает только своё, и
        # заглушка тоже: письмо с чужим `CheckId` должно отвергаться здесь, иначе
        # проверка «шлём разрешение, которое дали» ничего не значит.
        self.checks: set = set()

        # Кому писать нельзя: `check-send` ответит отказом. Живой сайт так и делает
        # — например, когда этому человеку уже писали слишком часто.
        self.no_send: set = set()

        # Входящие письма: что лежит у сайта для нашей девушки. ⚠ Поля — те же, что
        # у живого (`GET /messages`): список **без текста**, тело отдаётся отдельно
        # по номеру. Проверять забор на списке с телами значило бы проверять
        # выдуманный протокол.
        self.letters: list[dict] = [dict(row) for row in SITE_API_FAKE_LETTERS]

        # Тела писем по номеру — то, что отдаёт `GET /messages/{id}`.
        self.bodies: dict = dict(SITE_API_FAKE_BODIES)

        self._refuse: tuple[int, dict, str] | None = None
        self._offline = ''

    # ── Положения сайта ──────────────────────────────────────────────────────

    def refuse(self, code: int, body: dict = None, on: str = '') -> None:
        """Сайт отвечает отказом. `on` — часть пути; пусто — отказ на всё."""
        self._refuse = (int(code), dict(body or {}), str(on))

    def offline(self, reason: str = 'connection refused') -> None:
        """Сайта нет: связь не устанавливается."""
        self._offline = str(reason)

    def forbid_send(self, uid: str) -> None:
        """Этому адресату сайт писать не даёт: `check-send` отвечает 403."""
        self.no_send.add(str(uid))

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
        self.urls.append(str(request.url))

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

        # ── Письма ──────────────────────────────────────────────────────────
        if path.endswith('/messages'):
            # ⚠ Список **без тел**, как у живого: тело отдаётся по номеру ниже.
            return self._json(200, {'Messages': [
                {k: v for k, v in row.items()} for row in self.letters]})

        if '/messages/check-send/' in path:
            uid = path.rsplit('/', 1)[-1]
            if uid in self.no_send:
                return self._json(403, {'Error': 'Too many requests',
                                        'Reason': 'ERROR_TOO_MANY_REQUESTS'})

            check = f'check-{uid}-{len(self.checks)}'
            self.checks.add(check)

            return self._json(200, {
                'CheckId': check,
                'Message': 'You can send a message to this user within the next '
                           '30 seconds.'})

        # Одно письмо: то же самое плюс `Body`.
        one = path.rsplit('/', 1)[-1]
        if '/messages/' in path and one.isdigit():
            found = next((row for row in self.letters
                          if str(row.get('MessageId')) == one), None)
            if not found:
                return self._json(404, {'Error': 'Message not found',
                                        'Reason': 'ERROR_MESSAGE_NOT_FOUND'})

            return self._json(200, {**found,
                                    'Body': self.bodies.get(int(one), ''),
                                    'Attachment': None})

        if path.endswith('/messages/send'):
            sent = json.loads(request.content or b'{}')
            check = str(sent.get('CheckId') or '')
            if check not in self.checks:
                # ⚠ Живой сайт разрешение проверяет, и заглушка тоже: иначе тест
                # «сперва спросили, потом отправили» проходил бы и без первого шага.
                return self._json(404, {'Error': 'Check not found',
                                        'Reason': 'ERROR_MESSAGE_NOT_FOUND'})

            self.checks.discard(check)
            self.sent.append((str(sent.get('RecipientAccountId') or ''),
                              str(sent.get('Body') or ''), check))

            return self._json(200, {'Status': 'OK',
                                    'Message': 'Message sent successfully.'})

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

        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer), **kwargs)

    async def _answer(self, request: httpx.Request) -> httpx.Response:
        """Ответ с уступкой цикла событий.

        ⚠ **`await` здесь не украшение, и без него заглушка врёт.** Живой запрос
        уступает цикл, поэтому две задачи по-настоящему оказываются внутри входа
        одновременно — на этом и построена гонка, из-за которой сайт браковал
        второй вход. Отвечай заглушка синхронно, первый вызов успевал бы целиком
        до начала второго, и проверка «вход один» зеленела бы **и без замка**:
        проверено — так она и вела себя, пока сюда не добавили `sleep(0)`.
        """
        await asyncio.sleep(self._fake.pause)

        return self._fake.handle(request)


class _FakeRedis:
    """Redis настолько, насколько его использует кэш токена."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        # ⚠ `nx` подделывается по-настоящему, а не игнорируется. На нём держится
        # замок входа (`dating_site_api._lock_take`): «занял» отличается от «уже
        # занято» именно ответом `set`. Верни заглушка успех всегда — замок
        # выдавал бы право на вход каждому, и проверка «вход один» зеленела бы,
        # ничего не проверяя. Ответ повторяет redis-py: `True` либо `None`.
        if nx and key in self.store:
            return None

        self.store[key] = value
        self.ttl[key] = ex

        return True

    def delete(self, key):
        self.store.pop(key, None)
        self.ttl.pop(key, None)


@pytest.fixture
def site_api(monkeypatch) -> SiteApiFake:
    """Поддельный сайт и поддельный Redis под токен — на время теста."""
    fake = SiteApiFake()
    fake.redis = _FakeRedis()

    # ⚠ Подменяем **транспортный модуль**, а не тот, что зовёт ручку: вход, токен и
    # разбор отказов живут в `dating_site_api.py` — общие у списков собеседников и
    # у отправки писем. Подмени мы `dating_people_api`, письма ходили бы наружу.
    monkeypatch.setattr(dating_site_api, 'httpx', _Httpx(fake))
    monkeypatch.setattr(dating_site_api, 'redis_conn_get', lambda: fake.redis)

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
