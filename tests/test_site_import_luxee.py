"""Переписка Luxee сокетом панели: всё новое, ничего лишнего, «прочитано» не трогаем.

Сокет подменён целиком (`_Server`): сторож живого HTTP стоит на транспорте httpx
и вебсокета не ловит. Ответы сервера повторяют форму, снятую с живой панели
19.11.2026: `cm_get_profiles` → внешние анкеты с `import_uid`, диалог
`{мужчина}_{внешняя}`, сообщения с `author`, `index`, `created` в миллисекундах.

Главное, что здесь стерегут:

* **пропусков нет** — диалог листается назад до отметки, сколько бы ни проспал
  круг, а без отметки — до окна дат;
* **отметка ставится после записи** — прочитанное, но не записанное, читается
  снова;
* **`cl_consume` не уходит никогда** — чтение панели не гасит оператору счётчик
  непрочитанного.
"""

from contextlib import asynccontextmanager

import pytest

from src.dating.chat_panel import CHAT_PANEL_FIELD, chat_panel_rows
from src.dating.panel_fetch import DatingPanelFetchError
from src.dating.site import site_import_luxee, site_socket_luxee
from src.dating.site.site_import_luxee import DatingSiteImportLuxee
from src.dating.site.site_registry import site_registry_import

pytestmark = pytest.mark.chat

SITE = 999_344
GIRL, OUTER, MAN = 622180, 3161888, 2365098
IDENTITY = f'{MAN}_{OUTER}'
PROFILES = {str(GIRL): 59}
PRESENCE = {'driver': 'luxee', 'base': 'https://luxee.io'}

# Страница чата: токен сокета стоит сразу за объектом шаблонов.
PAGE = ("modelsChat = new ModelChatInstance({}, {skipTaskFormTemplate: '#x',\n"
        "    },\n    'eyJchat.token.here',\n    \"eyJapi.token\",")

DAY_MS = 86_400_000
NOW_MS = 1_795_100_000_000


class _Server:
    """Поддельный сервер чата: отвечает на запросы страницы по `key`."""

    def __init__(self, count: int, last_activity: int = NOW_MS):
        self.messages = [_message(i, MAN if i % 2 else OUTER, f'text {i}')
                         for i in range(1, count + 1)]
        self.last_activity = last_activity
        self.sent = []
        self.queue = [('message', [{'type': 's_ua_success',
                                    'data': {'status': True}}])]

    async def emit(self, event, packet):
        self.sent.append(packet['type'])
        self.queue.append(('message', [{'type': 'answer',
                                        'data': {'status': True, 'key': packet['key'],
                                                 **self._answer(packet)}}]))

    async def next(self, timeout):
        return self.queue.pop(0)

    def _answer(self, packet):
        kind, data = packet['type'], packet['data']
        if kind == 'cm_get_profiles':
            return {'message': {str(OUTER): {'uid': OUTER, 'import_uid': GIRL}}}
        if kind == 'cl_ch_all':
            return {'message': [{'identity': IDENTITY, 'index': len(self.messages),
                                 'lastActivity': str(self.last_activity)}],
                    'token': 'page2'}
        if kind == 'cl_ch_more':
            return {'message': [], 'token': None}
        if kind == 'cl_c_messages':
            return {'message': self.messages[-20:]}
        if kind == 'cl_cm_messages':
            below = [m for m in self.messages if m['index'] < int(data['token'])]
            return {'message': below[-20:]}

        return {'message': []}


@pytest.fixture
def serve(monkeypatch):
    """Подменяет страницу, сокет и Redis; отдаёт поддельный сервер и отметки."""
    marks = {}

    class _Redis:
        def hgetall(self, key):
            return dict(marks.get(key, {}))

        def hset(self, key, mapping):
            marks.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    def make(server):
        async def fetch(site_id, path, params=None, headers=None):
            assert path == site_socket_luxee.SITE_SOCKET_LUXEE_PAGE
            return PAGE

        @asynccontextmanager
        async def open_(url, query=None, headers=None):
            assert query['token'] == 'eyJchat.token.here'
            assert url == 'wss://luxee.io/chat/socket.io/'
            yield server

        monkeypatch.setattr(site_socket_luxee, 'panel_fetch_html', fetch)
        monkeypatch.setattr(site_socket_luxee, 'socketio_v2_open', open_)
        monkeypatch.setattr(site_import_luxee, 'redis_conn_get', lambda: _Redis())

        return marks

    return make


class TestTheDriverIsChosen:
    def test_luxee_reads_by_socket(self):
        driver = site_registry_import(PRESENCE)

        assert isinstance(driver, DatingSiteImportLuxee) and driver.has_journal()


class TestNothingIsMissed:
    async def test_first_run_takes_the_whole_window(self, serve):
        """Отметки нет — листаем назад до окна дат: 45 сообщений, три страницы."""
        server = _Server(45)
        serve(server)

        total, rows, why = await DatingSiteImportLuxee(PRESENCE).journal_rows(
            SITE, PROFILES, _day(NOW_MS - DAY_MS), _day(NOW_MS))

        assert why == '' and total == 45
        assert sorted(int(r['uid'][3:]) for r in rows) == list(range(1, 46))
        assert server.sent.count('cl_cm_messages') == 2

    async def test_a_dialog_seen_first_is_read_whole(self, serve):
        """Окно решает, какие диалоги брать, но не сколько из них читать.

        Первые 30 сообщений — трёхдневной давности, окно — сутки: без чтения
        целиком агент не увидел бы начала разговора, а отметка закрыла бы его.
        """
        server = _Server(45)
        for message in server.messages[:30]:
            message['created'] = str(NOW_MS - 3 * DAY_MS)
        serve(server)

        total, rows, _ = await DatingSiteImportLuxee(PRESENCE).journal_rows(
            SITE, PROFILES, _day(NOW_MS - DAY_MS), _day(NOW_MS))

        assert total == 45

    async def test_an_old_dialog_seen_first_is_read_too(self, serve):
        """Без отметки диалог берётся, как давно бы он ни жил: затихший 17.11
        не подтянулся, хотя панель уже выдала по нему задание Catch Up."""
        server = _Server(5, last_activity=NOW_MS - 30 * DAY_MS)
        serve(server)

        total, _, _ = await DatingSiteImportLuxee(PRESENCE).journal_rows(
            SITE, PROFILES, _day(NOW_MS - DAY_MS), _day(NOW_MS))

        assert total == 5

    async def test_resumes_from_the_mark_however_long_it_slept(self, serve):
        """Отметка 10, в диалоге 437: приезжают ровно 11…437, без окна дат."""
        server = _Server(437, last_activity=NOW_MS - 30 * DAY_MS)
        marks = serve(server)
        marks[site_import_luxee.LUXEE_CHAT_MARKS.format(site_id=SITE)] = {IDENTITY: '10'}

        total, rows, _ = await DatingSiteImportLuxee(PRESENCE).journal_rows(
            SITE, PROFILES, _day(NOW_MS), _day(NOW_MS))

        assert sorted(int(r['uid'][3:]) for r in rows) == list(range(11, 438))

    async def test_unchanged_dialog_is_not_read(self, serve):
        server = _Server(5)
        marks = serve(server)
        marks[site_import_luxee.LUXEE_CHAT_MARKS.format(site_id=SITE)] = {IDENTITY: '5'}

        total, rows, _ = await DatingSiteImportLuxee(PRESENCE).journal_rows(
            SITE, PROFILES, _day(NOW_MS), _day(NOW_MS))

        assert total == 0 and rows == [] and 'cl_c_messages' not in server.sent

    async def test_read_state_is_never_touched(self, serve):
        server = _Server(45)
        serve(server)

        await DatingSiteImportLuxee(PRESENCE).journal_rows(
            SITE, PROFILES, _day(NOW_MS - DAY_MS), _day(NOW_MS))

        assert 'cl_consume' not in server.sent
        assert server.sent[0] == 'cm_get_profiles'


class TestTheMarkFollowsTheWrite:
    async def test_mark_appears_only_after_save(self, serve):
        server = _Server(7)
        marks = serve(server)
        key = site_import_luxee.LUXEE_CHAT_MARKS.format(site_id=SITE)
        driver = DatingSiteImportLuxee(PRESENCE)

        await driver.journal_rows(SITE, PROFILES, _day(NOW_MS - DAY_MS), _day(NOW_MS))
        assert key not in marks

        await driver.journal_saved(SITE)
        assert marks[key] == {IDENTITY: '7'}


class TestRowsReadAsOurs:
    async def test_sides_and_wink(self, serve):
        """Его подмигивание — входящее словом, её реплика — исходящая."""
        server = _Server(0)
        server.messages = [_message(1, OUTER, 'Hi there'), _message(2, MAN, '', 3)]
        serve(server)

        _, cells, _ = await DatingSiteImportLuxee(PRESENCE).journal_rows(
            SITE, PROFILES, _day(NOW_MS - DAY_MS), _day(NOW_MS))
        rows = {r['message_uid']: r for r in chat_panel_rows({CHAT_PANEL_FIELD: cells},
                                                            PROFILES)}

        mine, his = rows['panel:sid0001'], rows['panel:sid0002']
        assert (mine['direction'], mine['sender_uid'], mine['text']) == \
               ('out', str(MAN), 'Hi there')
        assert (his['direction'], his['sender_name'], his['text']) == \
               ('in', 'Big D', '[wink]')
        assert mine['profile_id'] == 59 and len(his['sent_at']) == 19


class TestRefusals:
    async def test_page_without_token_is_a_refusal(self, serve, monkeypatch):
        serve(_Server(1))

        async def fetch(*args, **kwargs):
            return '<html>login</html>'

        monkeypatch.setattr(site_socket_luxee, 'panel_fetch_html', fetch)

        with pytest.raises(DatingPanelFetchError, match='токена'):
            await DatingSiteImportLuxee(PRESENCE).journal_rows(
                SITE, PROFILES, _day(NOW_MS), _day(NOW_MS))


def _message(index: int, author: int, body: str = '', kind: int = 1) -> dict:
    """Сообщение сокета в форме живого ответа."""
    return {'sid': f'sid{index:04d}', 'index': index, 'type': kind, 'body': body,
            'created': str(NOW_MS - (100 - index) * 60_000),
            'author': {'uid': author},
            'memberStatus': {str(MAN): {'status': 2, 'readAt': '1'}},
            'channel': {'identity': IDENTITY,
                        'members': [{'uid': OUTER, 'username': 'Olena'},
                                    {'uid': MAN, 'username': 'Big D '}]}}


def _day(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d')
