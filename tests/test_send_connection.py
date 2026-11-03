"""Отправка кадром туда, где вебсокет завёрнут в HTTP (Step2Love).

Сайт сюда не зовётся ни разу: ответы подставляются записанными, снятыми с
живого чата 02.11.2026. Проверяется то, что ломалось живьём, и каждая проверка
стоит отдельного отказа — молчаливого, который выглядел исправной работой:

* кадр без `action: "create"` сайт принимает и **выбрасывает**, отвечая `200 []`;
* `200` сам по себе ничего не значит — приговор приходит следующим запросом;
* номер чата берётся из живого списка сокета, а не угадывается по собеседнику.
"""

import json

import pytest

from tests_.tests_guard import tests_guard_calls

from src.dating import chat_socket, send_connection
from src.dating.send_connection import send_connection_run

pytestmark = pytest.mark.chat

SITE_CHAT = 'https://chat.example.com'
GIRL, MAN, OTHER = '1803186', '1814826', '999001'
UID = 'c0ffee00c0ffee00'

PROFILE = {'id': 78, 'site_id': 999_345, 'profile_id': GIRL,
           'storage_state': json.dumps(
               {'cookies': [{'name': 'clientSessionID', 'value': 'zzz',
                             'domain': '.example.com'}]})}
PRESENCE = {'base': 'https://example.com',
            'send': {'transport': 'connection', 'base': SITE_CHAT}}
SEND = PRESENCE['send']


def _auth(*chats) -> str:
    """Ответ на открытие: `socketID` плюс кадр авторизации с живыми чатами."""
    frame = {'ID': 'a1', 'appNameCreate': 'session', 'appNameTarget': 'session',
             'method': 'auth', 'userID': int(GIRL),
             'data': {'ID': int(GIRL), 'name': 'Diana',
                      'chatObjectList': list(chats)}}

    return json.dumps({'socketID': 'sock-1',
                       'data': json.dumps(['20000', json.dumps(frame)])})


def _chat(man: str = MAN, chat_id: int = 738_476, destroy: bool = False) -> dict:
    return {'chatID': chat_id, 'userIDMan': int(man), 'userIDGirl': int(GIRL),
            'destroy': destroy, 'statusID': 1}


def _verdict(frame_id: str, status: int = 1, info: str = '') -> str:
    """Приговор сайта нашему кадру — тем же видом, каким он приходит живьём."""
    one = {'ID': frame_id, 'statusID': status, 'action': 'update',
           'resData': {'ID': 56_652_460} if status == 1 else {'info': info}}

    return json.dumps([json.dumps(one)])


class _Answer:
    """Ответ httpx ровно в той части, которую читает транспорт."""

    def __init__(self, text: str, status: int = 200):
        self.text = text
        self.status_code = status

    def json(self):
        return json.loads(self.text)


class _Client:
    """Поддельный клиент: отдаёт заготовленное и помнит, о чём спрашивали."""

    def __init__(self, opened: str, replies: list):
        self.opened = opened
        self.replies = list(replies)
        self.sent: list = []

    async def post(self, url, headers=None, content=None):
        self.sent.append({'url': url, 'body': content})

        if url.endswith(chat_socket.CHAT_SOCKET_OPEN) \
                or '/connection?' in url:
            return _Answer(self.opened)

        return _Answer(self.replies.pop(0) if self.replies else '[]')


@pytest.fixture
def serve(monkeypatch):
    """Подменяет клиента и убирает паузы опроса: сторожу ждать нечего."""
    def make(opened: str, replies: list) -> _Client:
        client = _Client(opened, replies)
        # ⚠ Подменяем **сборщик клиента у транспорта**: он переехал в
        # `chat_socket.py` вместе с открытием соединения и опросами. Так
        # сторож заодно держит само его существование — вернись отправка к
        # своему `httpx` мимо транспорта, подмена перестанет действовать.
        monkeypatch.setattr(chat_socket, 'chat_socket_client',
                            lambda cookies, proxy='': client)
        monkeypatch.setattr(chat_socket, 'CHAT_SOCKET_POLL_SEC', 0)

        async def step(client_, config, one, cookies, values=None):
            """Открытие идёт общим шагом — подменяем его там же, где зовут."""
            return await client_.post(
                str(one.get('base') or '') + str(one.get('path') or ''),
                content=one.get('body'))

        monkeypatch.setattr(chat_socket, 'http_heartbeat_step_call', step)

        return client

    return make


class TestItSends:
    """Удачная отправка: кадр, подтверждение, ответ наружу."""

    async def test_the_frame_carries_the_action_create(self, serve):
        """⚠⚠⚠ Без `action: "create"` сайт молча выбрасывает кадр.

        Отвечает при этом `200 []` — ровно как на принятый, — и две реплики
        ушли в никуда прежде, чем это нашлось.
        """
        client = serve(_auth(_chat()), [_verdict(UID)])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert out['ok'] is True
        frame = json.loads(json.loads(client.sent[1]['body'])['data'][0])
        assert frame['action'] == 'create'

    async def test_the_frame_is_what_the_sites_sdk_builds(self, serve):
        """Поля кадра — те же, что собирает SDK страницы."""
        client = serve(_auth(_chat()), [_verdict(UID)])

        await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi there', UID)

        frame = json.loads(json.loads(client.sent[1]['body'])['data'][0])

        assert frame['appNameCreate'] == frame['appNameTarget'] == 'chat'
        assert frame['method'] == 'sendMessage'
        assert frame['userID'] == int(GIRL), 'номер анкеты числом, а не строкой'
        assert frame['data'] == {'chatID': 738_476, 'message': 'hi there',
                                 'attachID': 0}
        assert frame['statusID'] == 0

    async def test_the_frame_id_is_our_message_uid(self, serve):
        """`ID` кадра — наш идентификатор: по нему и узнаётся ответ сайта."""
        client = serve(_auth(_chat()), [_verdict(UID)])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        frame = json.loads(json.loads(client.sent[1]['body'])['data'][0])

        assert frame['ID'] == UID and out['message_uid'] == UID

    async def test_the_socket_id_goes_back_with_the_frame(self, serve):
        """Кадр уходит в то соединение, которое сами и открыли."""
        client = serve(_auth(_chat()), [_verdict(UID)])

        await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert json.loads(client.sent[1]['body'])['socketID'] == 'sock-1'

    async def test_a_verdict_in_a_later_poll_still_counts(self, serve):
        """⚠⚠ Приговор приходит **следующим** запросом, а не этим.

        `/connection/message` — и отправка, и опрос: ответом приезжает то, что
        накопилось, а решение по нашему кадру приходит кругом позже.
        """
        client = serve(_auth(_chat()), ['[]', '[]', _verdict(UID)])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert out['ok'] is True
        assert len(client.sent) == 4, 'отправка и три опроса'


class TestItRefuses:
    """Чего транспорт не считает удачей."""

    async def test_silence_is_not_success(self, serve):
        """⚠⚠ `200` без подтверждения — это молча выброшенный кадр.

        Считай мы удачей код ответа — строка пула закрылась бы, и реплика не
        ушла бы уже никогда. Цена ошибки несимметрична: лишняя попытка упрётся
        в запрет повтора, ложная удача не упрётся ни во что.
        """
        client = serve(_auth(_chat()), [])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert out['ok'] is False
        assert 'не подтвердил' in out['error']
        # Открытие, отправка и все положенные опросы: молчание не укорачивает
        # ожидание — иначе редкий медленный ответ читался бы как отказ.
        assert len(client.sent) == 2 + chat_socket.CHAT_SOCKET_POLL_TRIES

    async def test_a_refusal_speaks_the_sites_reason(self, serve):
        """Отказ сайта едет словами, а не «что-то пошло не так»."""
        serve(_auth(_chat()), [_verdict(UID, status=2, info='noMoney')])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert out['ok'] is False and 'noMoney' in out['error']

    async def test_a_verdict_about_another_frame_is_not_ours(self, serve):
        """Чужой приговор нашим не считается: кадры в ответе вперемешку."""
        serve(_auth(_chat()), [_verdict('чужой-кадр'), _verdict('и-этот-тоже')])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert out['ok'] is False and 'не подтвердил' in out['error']

    async def test_no_live_chat_is_a_state_not_a_breakage(self, serve):
        """Нет открытого разговора — `offline`, тем же словом, что у сокета.

        Это не поломка настройки: разговор закрыт либо не начинался, и
        вызывающие уже умеют читать `offline` как состояние.
        """
        serve(_auth(_chat(man=OTHER)), [])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert out['ok'] is False and out['offline'] is True
        assert 'нет открытого чата' in out['error']

    async def test_a_destroyed_chat_is_not_a_place_to_write(self, serve):
        """Закрытый разговор — не адрес: сайт держит его в списке помеченным."""
        serve(_auth(_chat(destroy=True)), [])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert out['offline'] is True

    async def test_without_the_chat_server_address_it_refuses(self, serve):
        """Нет адреса чат-сервера — отказ словами, а не поход в никуда."""
        serve(_auth(_chat()), [])

        out = await send_connection_run(PROFILE, PRESENCE, {'transport': 'connection'},
                                        MAN, 'hi', UID)

        assert out['ok'] is False and 'presence.send.base' in out['error']

    async def test_without_a_session_it_refuses(self, serve):
        """Без cookie анкеты отправлять нечем — и это говорится словами."""
        serve(_auth(_chat()), [])

        out = await send_connection_run({**PROFILE, 'storage_state': '{}'},
                                        PRESENCE, SEND, MAN, 'hi', UID)

        assert out['ok'] is False and 'вход не выполнен' in out['error']

    async def test_a_closed_connection_refuses(self, serve):
        """Сайт не открыл соединение — говорим это, а не шлём кадр в пустоту."""
        serve(json.dumps({'data': '[]'}), [])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert out['ok'] is False and 'не открыл соединение' in out['error']


class TestTheClientIsShared:
    """⚠ Клиент разовый, транспорт общий — и закрывать его нельзя.

    Правило проекта (`http_pool.py`): cookie у каждой анкеты свои, а соединения
    общие. Клиент с собственным пулом (`http_heartbeat_client`) просит, чтобы
    его закрыли, — а у кругов нет места, где это сделать: они ходят по анкете,
    и клиент оставался бы незакрытым на каждый заход.

    ⚠ Смотрим на **транспорт** (`chat_socket.py`): сборка клиента переехала
    туда вместе с открытием соединения, и отправка его больше не заводит.
    """

    def test_it_takes_the_shared_transport(self):
        """⚠ Сверяем **вызовы**, а не текст файла.

        Первая запись этого сторожа краснела на верном коде: имя отвергнутого
        кандидата стоит в соседнем комментарии — там, где и положено
        объяснению. Разбор даёт `tests_guard_calls` общего слоя, у него эта
        грабля записана в докстринге.
        """
        calls = tests_guard_calls(chat_socket.chat_socket_client)

        assert 'http_pool_transport' in calls
        assert 'http_heartbeat_client' not in calls

    def test_nothing_closes_the_shared_transport(self):
        """`aclose()` унёс бы транспорт у всех, кто им пользуется."""
        closed = {one for one in tests_guard_calls(chat_socket)
                  if one.endswith('aclose')}

        assert not closed, closed


class TestTheAnswerKeepsItsShape:
    """Форма ответа одна на все пути отправки — читателю не гадать."""

    async def test_every_key_is_always_there(self, serve):
        serve(_auth(_chat()), [_verdict(UID)])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert set(out) >= {'ok', 'offline', 'response', 'message_uid', 'error'}

    async def test_a_refusal_has_them_too(self, serve):
        serve(json.dumps({'data': '[]'}), [])

        out = await send_connection_run(PROFILE, PRESENCE, SEND, MAN, 'hi', UID)

        assert set(out) >= {'ok', 'offline', 'response', 'message_uid', 'error'}
        assert out['message_uid'] == UID, 'номер реплики возвращается и при отказе'
