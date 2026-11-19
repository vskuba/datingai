"""Отправка сокетом чата панели (Luxee): в нужный диалог, с честным приговором.

Сокет подменён целиком: сторож живого HTTP вебсокета не ловит. Форма событий —
как у страницы чата панели: реплика уходит `cl_m_message`, принятая
возвращается `server_m_new` с нашим ключом в `request.data.key`, отказ —
`s_m_error` с кодом и словами.

Стерегут здесь:

* **диалог** — пара «мужчина + внешняя анкета этой девушки», при нескольких —
  самый свежий; чужая девушка и чужой мужчина не годятся;
* **удача — только подтверждённая**: отказ и молчание сервера — неудача;
* **прочие правила отправки** — анкета Luxee готова без входа, мессенджер
  доставляет и тому, кого нет в сети.
"""

from contextlib import asynccontextmanager

import pytest

from src.dating import send_panel_socket
from src.dating.ready import ready_check
from src.dating.send_panel_socket import send_panel_socket_run
from src.dating.site import site_socket_luxee
from src.dating.site.site_registry import site_registry_chat

pytestmark = pytest.mark.chat

GIRL, MAN = 622180, '2365098'
PROFILE = {'id': 59, 'site_id': 999_344, 'profile_id': str(GIRL), 'login': str(GIRL)}
PRESENCE = {'driver': 'luxee', 'base': 'https://luxee.io'}

# Внешние анкеты: две — этой девушки, одна — чужой.
OUTER = {'3161872': {'import_uid': GIRL}, '3161888': {'import_uid': GIRL},
         '3161999': {'import_uid': 622231}}


@pytest.fixture
def serve(monkeypatch):
    def make(server):
        async def fetch(site_id, path, params=None, headers=None):
            return ("{skipTaskFormTemplate: '#x',\n    },\n    'eyJchat.token',")

        @asynccontextmanager
        async def open_(url, query=None, headers=None):
            yield server

        monkeypatch.setattr(site_socket_luxee, 'panel_fetch_html', fetch)
        monkeypatch.setattr(site_socket_luxee, 'socketio_v2_open', open_)
        monkeypatch.setattr(site_socket_luxee, 'SITE_SOCKET_LUXEE_ASK_SEC', 0.2)

        return server

    return make


class TestTheRightDialog:
    async def test_latest_dialog_of_this_girl_with_this_man(self, serve):
        server = serve(_Server([
            {'identity': f'{MAN}_3161872', 'lastActivity': '100'},
            {'identity': f'{MAN}_3161888', 'lastActivity': '900'},
            {'identity': f'{MAN}_3161999', 'lastActivity': '999'},
            {'identity': '1111111_3161888', 'lastActivity': '5000'}]))

        got = await send_panel_socket_run(PROFILE, PRESENCE, MAN, 'hello', 'k-1')

        assert got['ok'] and got['response'] == 'sid-new'
        (data,) = _sent(server)
        assert (data['identity'], data['ownerUid'], data['body'], data['key'],
                data['type']) == (f'{MAN}_3161888', GIRL, 'hello', 'k-1', 1)

    async def test_sides_order_in_the_identity_does_not_matter(self, serve):
        server = serve(_Server([{'identity': f'3161888_{MAN}', 'lastActivity': '1'}]))

        assert (await send_panel_socket_run(PROFILE, PRESENCE, MAN, 'hi', 'k'))['ok']
        assert _sent(server)[0]['identity'] == f'3161888_{MAN}'

    async def test_no_dialog_no_cold_message(self, serve):
        server = serve(_Server([{'identity': '1111111_3161888', 'lastActivity': '1'}]))

        got = await send_panel_socket_run(PROFILE, PRESENCE, MAN, 'hi', 'k')

        assert not got['ok'] and 'диалога' in got['error'] and _sent(server) == []


class TestOnlyConfirmedIsSent:
    async def test_refusal_is_told_with_code(self, serve):
        serve(_Server([{'identity': f'{MAN}_3161888', 'lastActivity': '1'}],
                      verdict='refused'))

        got = await send_panel_socket_run(PROFILE, PRESENCE, MAN, 'hi', 'k')

        assert not got['ok'] and '485' in got['error'] and 'Catch Up' in got['error']

    async def test_silence_is_a_failure(self, serve):
        serve(_Server([{'identity': f'{MAN}_3161888', 'lastActivity': '1'}],
                      verdict='silent'))

        got = await send_panel_socket_run(PROFILE, PRESENCE, MAN, 'hi', 'k')

        assert not got['ok'] and 'не подтвердил' in got['error']

    async def test_read_state_is_never_touched(self, serve):
        server = serve(_Server([{'identity': f'{MAN}_3161888', 'lastActivity': '1'}]))

        await send_panel_socket_run(PROFILE, PRESENCE, MAN, 'hi', 'k')

        assert 'cl_consume' not in [kind for kind, _ in server.sent]

    async def test_profile_without_number_is_refused(self):
        got = await send_panel_socket_run({'site_id': 1, 'login': 'x@y'}, PRESENCE,
                                          MAN, 'hi', 'k')

        assert not got['ok'] and set(got) == {'ok', 'error', 'offline', 'response',
                                              'message_uid'}


class TestTheRulesAround:
    def test_luxee_profile_is_ready_without_login(self):
        assert ready_check({'enabled': 1}, 'panel_socket') == {'can': True, 'why': ''}
        assert not ready_check({'enabled': 0}, 'panel_socket')['can']

    def test_messenger_reaches_the_offline(self):
        assert site_registry_chat(PRESENCE).reaches_offline()
        assert not site_registry_chat({'driver': 'ladadate'}).reaches_offline()
        assert not site_registry_chat({'driver': 'step2love'}).reaches_offline()

    def test_send_routes_the_transport(self):
        import inspect
        from src.dating import send

        assert send.SEND_SOURCE['panel_socket'] == 'socket'
        assert 'send_panel_socket_run' in inspect.getsource(send.send_message)
        assert send_panel_socket.send_panel_socket_run is send.send_panel_socket_run


class _Server:
    """Поддельный сервер чата панели."""

    def __init__(self, channels: list, verdict: str = 'ok'):
        self.channels = channels
        self.verdict = verdict
        self.sent = []
        self.queue = [('message', [{'type': 's_ua_success', 'data': {'status': True}}])]

    async def emit(self, event, packet):
        kind, data = packet['type'], packet['data']
        self.sent.append((kind, data))
        if kind == 'cl_m_message':
            self._verdict(data)
            return

        answer = {'status': True, 'key': packet['key'], 'message': []}
        if kind == 'cm_get_profiles':
            answer['message'] = OUTER
        elif kind == 'cl_ch_all':
            answer['message'] = self.channels
        self.queue.append(('message', [{'type': 'answer', 'data': answer}]))

    def _verdict(self, data):
        if self.verdict == 'ok':
            self.queue.append(('message', [{'type': 'server_m_new', 'data': {
                'message': {'message': {'sid': 'sid-new'},
                            'request': {'data': {'key': data['key']}}}}}]))
        elif self.verdict == 'refused':
            self.queue.append(('message', [{'type': 's_m_error', 'data': {
                'message': {'identity': data['identity'], 'key': data['key']},
                'error': {'code': 485, 'text': 'Finish Catch Up first'}}}]))

    async def next(self, timeout):
        if not self.queue:
            raise TimeoutError()
        return self.queue.pop(0)


def _sent(server) -> list:
    return [data for kind, data in server.sent if kind == 'cl_m_message']
