"""Поддельный RhapsodyAi: готовые ответы вместо живого соседа.

⚠ **Сюита не имеет права ходить в другой проект.** Тест, которому нужен поднятый
RhapsodyAi, проверяет не нас: он краснеет от чужого перезапуска, от занятого пула
вкладок, от переименованного сайта — и молчит про то, ради чего написан. Хуже
того, он **пишет в чужую систему**: прогон сценария занимает вкладку в общем пуле.

Поэтому здесь один общий поддельный сосед, и пользуются им все тесты, которым он
нужен.

## Подменяется HTTP, а не функции двери

Заглушка встаёт на **транспорт httpx внутри `rhapsody_api`**, а не поверх его
функций. Разница принципиальная:

* дверь остаётся под проверкой целиком — как строится путь, как передаётся метка,
  как токен уезжает в заголовок и, главное, как **код отказа превращается во
  фразу** («409 → браузер соседа занят»). Подмени мы `rhapsody_api_site_list`, всё
  это осталось бы непроверенным ровно там, где живут отказы;
* точка подмены одна на все вызовы. Функции двери разошлись по десятку модулей и
  импортированы по имени (`from … import rhapsody_api_site_list`), так что
  подменять пришлось бы в каждом импортёре — и первый забытый молча пошёл бы к
  живому соседу.

Подменяется **имя `httpx` внутри модуля двери**, а не `httpx.AsyncClient` глобально:
глобальная подмена сломала бы и сам тестовый клиент, который ходит в панель.

## Как пользоваться

    async def test_список_приходит(rhapsody):
        rhapsody.sites = [{'id': 7, 'name': 'Ladadate', 'url': 'https://…'}]

        assert await rhapsody_api_site_list() == rhapsody.sites

    async def test_занятый_браузер(rhapsody):
        rhapsody.refuse(409, 'предел сессий исчерпан: 25')

        with pytest.raises(RhapsodyError, match='браузер соседа занят'):
            await rhapsody_api_site_list()

Готовые положения соседа — методами: `refuse`, `offline`, `forget_scenarios`.
Что у него лежит — полями: `sites`, `scenarios`, `run`. Что у него спросили —
`calls`, по нему проверяется «сходили один раз, а не на каждую перерисовку».

⚠ **Тесты с этой заглушкой зовут обработчики в процессе**, а не по HTTP: панель
живёт в контейнере, и подменить в ней что-либо снаружи нельзя. Остальная сюита
по-прежнему ходит по HTTP (`conftest.py`) — там, где соседа не трогают.
"""

import json

import httpx
import pytest

from src.rhapsody import rhapsody_api

# Настройка опроса присутствия — та же по устройству, что у живого сайта: адрес и
# ручка heartbeat (`dating.md`, §2). Нужна, чтобы пинг доходил до своей работы, а не
# отваливался на «у сайта не настроен опрос присутствия».
#
# ⚠ До живого сайта опрос при этом **не доходит**: без cookie он отвечает «вход не
# выполнен» раньше всякой сети. Учётки сюиты входа не проходят — значит и запроса
# наружу нет.
RHAPSODY_FAKE_PRESENCE = {'base': 'https://ladadate.com', 'heartbeat': '/heartbeat'}

# Что у поддельного соседа лежит по умолчанию. Два сайта, потому что «настройки
# одного сайта не видны у другого» — проверка, которую надо чем-то проверять, а
# третий ничего не добавил бы.
RHAPSODY_FAKE_SITES = [
    {'id': 343, 'name': 'Ladadate', 'url': 'https://ladadate.com', 'tags': 'dating',
     'presence': RHAPSODY_FAKE_PRESENCE},
    {'id': 19, 'name': 'Goldenbride', 'url': 'https://goldenbride.net', 'tags': 'dating',
     'presence': RHAPSODY_FAKE_PRESENCE},
]

# Сценарии: у первого сайта есть импорт и избранные, у второго нет ничего. Не для
# красоты — «у сайта нет сценария» отдельная ветка почти в каждой ручке, и без
# такого сайта её нечем проверить.
RHAPSODY_FAKE_SCENARIOS = [
    {'id': 92, 'site_id': 343, 'name': 'импорт анкет'},
    {'id': 259, 'site_id': 343, 'name': 'избранные собеседники: список'},
    {'id': 278, 'site_id': 343, 'name': 'анкеты мужчин: список'},
    {'id': 479, 'site_id': 343, 'name': 'чат: сообщения из панели'},
]

# Чем кончается прогон, если тест не сказал иного.
RHAPSODY_FAKE_RUN = {'status': 'success', 'step_no': 0, 'error': '', 'extract': {}}

# Адрес и токен поддельного соседа. Настоящие значения из окружения не годятся:
# тест не должен зависеть от того, что записано в `.env` рабочей установки.
RHAPSODY_FAKE_URL = 'http://rhapsody.test'
# Латиницей: токен уезжает в заголовок `Authorization`, а заголовки — ascii.
RHAPSODY_FAKE_TOKEN = 'test-token'


class RhapsodyFake:
    """Сосед с заранее заготовленными ответами.

    Держит то же, что держит живой: закладки сайтов, сценарии и итог прогона.
    Отвечает на те пути, которые дверь действительно спрашивает; на остальные —
    404, и это осознанно: тест, дёрнувший неописанную ручку, должен узнать об этом
    от заглушки, а не молча получить пустоту.
    """

    def __init__(self):
        self.sites = [dict(site) for site in RHAPSODY_FAKE_SITES]
        self.scenarios = [dict(row) for row in RHAPSODY_FAKE_SCENARIOS]
        self.run = dict(RHAPSODY_FAKE_RUN)

        # Что у соседа спросили: `(метод, путь)`. По нему проверяется не только
        # «сходили», но и «сходили один раз» — кэш сценариев и выдержка списков
        # только этим и проверяются.
        self.calls: list[tuple[str, str]] = []

        self._refuse: tuple[int, str, str] | None = None
        self._offline = ''

    # ── Положения соседа ─────────────────────────────────────────────────────

    def refuse(self, code: int, detail: str = '', on: str = '') -> None:
        """Сосед отвечает отказом.

        Args:
            code: код ответа. 409 — занят браузер, 401 — протух токен, 404 — нет
                такого, 502/503 — не отвечает.
            detail: его объяснение. Уезжает в текст `RhapsodyError`, а оттуда в
                журнал анкеты — ради этой дороги отказы и проверяются.
            on: часть пути, к которой отказ относится. Пусто — отказывает на всё.
        """
        self._refuse = (int(code), str(detail), str(on))

    def offline(self, reason: str = 'connection refused') -> None:
        """Соседа нет вовсе: связь не устанавливается.

        Отдельно от `refuse`: обрыв связи и отказ по коду — разные ветки в двери
        («RhapsodyAi недоступен» против «сосед ответил отказом»), и путать их
        значит не проверить одну из них.
        """
        self._offline = str(reason)

    def forget_scenarios(self) -> None:
        """У сайтов не заведено ни одного сценария."""
        self.scenarios = []

    # ── Ответы ───────────────────────────────────────────────────────────────

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Один запрос двери — один заготовленный ответ."""
        path = request.url.path
        self.calls.append((request.method, str(request.url).replace(RHAPSODY_FAKE_URL, '')))

        if self._offline:
            raise httpx.ConnectError(self._offline, request=request)

        if self._refuse:
            code, detail, on = self._refuse
            if not on or on in path:
                return self._json(code, {'detail': detail} if detail else {})

        return self._route(request, path)

    # ── Приватное ────────────────────────────────────────────────────────────

    def _route(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == '/api/browser/site':
            tag = request.url.params.get('tag') or ''

            return self._ok([site for site in self.sites
                             if not tag or tag in str(site.get('tags') or '').split(',')])

        if path.startswith('/api/browser/site/'):
            found = self._by_id(self.sites, path.rsplit('/', 1)[-1])

            return self._ok(found) if found else self._json(404, {'detail': 'нет такого сайта'})

        if path == '/api/browser/scenario':
            return self._ok(self.scenarios)

        if path.endswith('/run'):
            found = self._by_id(self.scenarios, path.split('/')[-2])

            return self._ok(self.run) if found \
                else self._json(404, {'detail': 'нет такого сценария'})

        if path.startswith('/api/browser/scenario/'):
            found = self._by_id(self.scenarios, path.rsplit('/', 1)[-1])

            return self._ok(found) if found \
                else self._json(404, {'detail': 'нет такого сценария'})

        return self._json(404, {'detail': f'заглушка не знает пути {path}'})

    @staticmethod
    def _by_id(rows: list, raw: str) -> dict:
        return next((row for row in rows if str(row.get('id')) == str(raw)), {})

    @staticmethod
    def _ok(result) -> httpx.Response:
        return RhapsodyFake._json(200, {'result': result})

    @staticmethod
    def _json(code: int, body: dict) -> httpx.Response:
        return httpx.Response(code, content=json.dumps(body, ensure_ascii=False),
                              headers={'content-type': 'application/json'})


class _Httpx:
    """Подставной `httpx` для двери: тот же модуль, но клиент — с заглушкой.

    Дверь пишет `async with httpx.AsyncClient(timeout=…) as client`, поэтому
    достаточно подменить имя `httpx` в её модуле. `HTTPError` берётся настоящий:
    дверь ловит именно его, и подделка здесь означала бы, что обрыв связи в тесте
    ловится не тем же кодом, что в бою.
    """

    HTTPError = httpx.HTTPError

    def __init__(self, fake: RhapsodyFake):
        self._fake = fake

    def AsyncClient(self, **kwargs) -> httpx.AsyncClient:  # noqa: N802 — имя из httpx
        kwargs.pop('transport', None)

        return httpx.AsyncClient(transport=httpx.MockTransport(self._fake.handle), **kwargs)


@pytest.fixture
def rhapsody(monkeypatch) -> RhapsodyFake:
    """Поддельный сосед на время теста.

    ⚠ Память двери чистится **до и после**: найденный сценарий она помнит десять
    минут, и заготовленное одним тестом дожило бы до следующего — а тот получил бы
    номер сценария, которого у его соседа нет.
    """
    fake = RhapsodyFake()

    monkeypatch.setattr(rhapsody_api, 'httpx', _Httpx(fake))
    monkeypatch.setattr(rhapsody_api, 'RHAPSODY_API_URL', RHAPSODY_FAKE_URL)
    monkeypatch.setattr(rhapsody_api, 'RHAPSODY_API_TOKEN', RHAPSODY_FAKE_TOKEN)

    rhapsody_api.rhapsody_api_scenario_forget()
    yield fake
    rhapsody_api.rhapsody_api_scenario_forget()
