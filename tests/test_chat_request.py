"""Запросы чата: без принятого запроса разговора не бывает.

⚠⚠⚠ Что здесь стережётся. У Step2Love написать человеку нельзя, пока между
вами нет открытого чата, а открывается он только принятым запросом. Пока
запрос висит, `chatObjectList` пуст, отправка отвечает `offline`, и со
стороны это выглядит так, будто собеседник молчит — а он ждёт галочки.

Сайт сюда не зовётся ни разу: кадры подставляются записанными, той же формы,
что приезжает живьём (`chatRequestObjectList` в кадре авторизации).

⚠ Разбор тел в поддельном клиенте — обычный `json.loads`, а не
`json_from_string` общего слоя: тот вынимает JSON из **грязного** ответа
модели, а здесь мы читаем то, что сами же собрали строкой выше.
"""

import json

import pytest

from src.dating import chat_request, chat_socket
from src.dating.chat_request import (chat_request_accept,
                                     chat_request_accept_all,
                                     chat_request_rows)
from src.dating.chat_socket import ChatSocketError

pytestmark = pytest.mark.chat

SITE_CHAT = 'https://chat.example.com'
GIRL, MAN, OTHER = '1803186', '1814826', '999001'

PROFILE = {'id': 78, 'site_id': 999_345, 'profile_id': GIRL,
           'storage_state': json.dumps(
               {'cookies': [{'name': 'clientSessionID', 'value': 'zzz',
                             'domain': '.example.com'}]})}
PRESENCE = {'base': 'https://example.com',
            'send': {'transport': 'connection', 'base': SITE_CHAT}}


def _request(create: str = MAN, target: str = GIRL, status: int = 1,
             message: str = 'let us talk') -> dict:
    """Запрос чата — тем же видом, каким он лежит в кадре авторизации."""
    # ⚠⚠ Имена полей — **из живого кадра** (снят 03.11.2026), а не из
    # геттеров SDK: там они `getIDCreate`, а в кадре `userIDCreate`. На этом
    # разбор уже ошибся — и сюита ошибку повторила, потому что поддельный
    # кадр собирали теми же неверными именами.
    return {'userIDCreate': int(create), 'userIDTarget': int(target),
            'statusID': status, 'message': message,
            'attachID': 0, 'createTime': 1_793_704_265_611,
            'destroyMsg': '', 'destroyTimeout': 300_000,
            'userObjectCreate': {'ID': int(create), 'name': 'Semen'},
            'userObjectTarget': {'ID': int(target), 'name': 'Diana'}}


def _auth(*requests) -> str:
    """Ответ на открытие: `socketID` плюс кадр авторизации со списком."""
    frame = {'ID': 'a1', 'appNameCreate': 'session', 'appNameTarget': 'session',
             'method': 'auth', 'userID': int(GIRL),
             'data': {'ID': int(GIRL), 'name': 'Diana',
                      'chatObjectList': [],
                      'chatRequestObjectList': list(requests)}}

    return json.dumps({'socketID': 'sock-1',
                       'data': json.dumps(['20000', json.dumps(frame)])})


def _verdict(frame_id: str, status: int = 1, info: str = '') -> str:
    one = {'ID': frame_id, 'statusID': status, 'action': 'update',
           'resData': {'chatID': 738_476} if status == 1 else {'info': info}}

    return json.dumps([json.dumps(one)])


class _Answer:
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

    def _is_open(self, url: str) -> bool:
        return url.endswith(chat_socket.CHAT_SOCKET_OPEN) or '/connection?' in url

    async def post(self, url, headers=None, content=None):
        self.sent.append({'url': url, 'body': content})

        if self._is_open(url):
            return _Answer(self.opened)

        return _Answer(self.replies.pop(0) if self.replies else '[]')


class _Judging(_Client):
    """Клиент, который отвечает приговором **на тот самый** кадр.

    ⚠ Приговор узнаётся по `ID` кадра, и подставить его заранее нельзя —
    идентификатор собирается внутри. Поэтому клиент читает отправленное.
    """

    def __init__(self, opened: str, status: int = 1, info: str = ''):
        super().__init__(opened, [])
        self.status, self.info = status, info

    async def post(self, url, headers=None, content=None):
        self.sent.append({'url': url, 'body': content})

        if self._is_open(url):
            return _Answer(self.opened)

        frames = (json.loads(content or '{}').get('data') or [])
        if not frames:
            return _Answer('[]')

        return _Answer(_verdict(json.loads(frames[0])['ID'],
                                status=self.status, info=self.info))


@pytest.fixture
def serve(monkeypatch):
    """Подменяет транспорт: клиент поддельный, пауз опроса нет."""
    def make(client: _Client) -> _Client:
        monkeypatch.setattr(chat_socket, 'chat_socket_client',
                            lambda cookies, proxy='': client)
        monkeypatch.setattr(chat_socket, 'CHAT_SOCKET_POLL_SEC', 0)

        async def step(client_, config, one, cookies, values=None):
            return await client_.post(
                str(one.get('base') or '') + str(one.get('path') or ''),
                content=one.get('body'))

        monkeypatch.setattr(chat_socket, 'http_heartbeat_step_call', step)

        return client

    return make


class TestItFindsWaitingRequests:
    """Кто постучался и ждёт решения."""

    async def test_a_waiting_request_is_found(self, serve):
        serve(_Client(_auth(_request()), []))

        rows = await chat_request_rows(PROFILE, PRESENCE)

        assert len(rows) == 1
        assert rows[0]['uid'] == MAN
        assert rows[0]['name'] == 'Semen'
        assert rows[0]['text'] == 'let us talk'

    async def test_an_empty_list_is_an_honest_zero(self, serve):
        serve(_Client(_auth(), []))

        assert await chat_request_rows(PROFILE, PRESENCE) == []

    async def test_a_decided_request_is_skipped(self, serve):
        """⚠ Принимать можно только **ждущий**: прочие статусы — уже решённые
        (принят, отозван, просрочен). `confirm` на такой получил бы отказ, а мы
        записали бы это поломкой."""
        for decided in (2, 3, 4, 5, 8):
            serve(_Client(_auth(_request(status=decided)), []))

            assert await chat_request_rows(PROFILE, PRESENCE) == [], decided

    async def test_our_own_request_is_not_ours_to_accept(self, serve):
        """⚠⚠ В списке лежат и **наши** запросы, отправленные инвайтами.

        Принять свой же нельзя — сайт отвечает «This user not target», и
        попытка выглядела бы поломкой. Отличаем по `idTarget`: адресат — мы.
        """
        serve(_Client(_auth(_request(create=GIRL, target=MAN)), []))

        assert await chat_request_rows(PROFILE, PRESENCE) == []

    async def test_a_request_without_a_sender_is_skipped(self, serve):
        serve(_Client(_auth({**_request(), 'userIDCreate': 0}), []))

        assert await chat_request_rows(PROFILE, PRESENCE) == []

    async def test_without_the_chat_address_it_refuses(self, serve):
        serve(_Client(_auth(_request()), []))

        with pytest.raises(ChatSocketError, match='presence.send.base'):
            await chat_request_rows(PROFILE, {'send': {}})


class TestItAccepts:
    """Принятие: один кадр, и разговор открыт."""

    async def test_the_frame_is_what_the_sdk_builds(self, serve):
        client = serve(_Judging(_auth(_request())))

        out = await chat_request_accept(PROFILE, PRESENCE, MAN)
        frame = json.loads(json.loads(client.sent[1]['body'])['data'][0])

        assert frame['appNameCreate'] == frame['appNameTarget'] == 'chatRequest'
        assert frame['method'] == 'confirm'
        assert frame['action'] == 'create', 'без него сайт молча выбросит кадр'
        # ⚠⚠ Два разных номера: `userID` кадра — мы, `userID` внутри `data` —
        # тот, кто прислал запрос. Перепутай их — подтвердит не та сторона.
        assert frame['userID'] == int(GIRL)
        assert frame['data'] == {'userID': int(MAN)}
        assert out['ok'] is True, out['error']

    async def test_silence_is_not_success(self, serve):
        """⚠⚠ `200` без подтверждения — молча выброшенный кадр."""
        serve(_Client(_auth(_request()), []))

        out = await chat_request_accept(PROFILE, PRESENCE, MAN)

        assert out['ok'] is False and 'не подтвердил' in out['error']

    async def test_a_refusal_speaks_the_sites_reason(self, serve):
        serve(_Judging(_auth(_request()), status=2, info='This user not target'))

        out = await chat_request_accept(PROFILE, PRESENCE, MAN)

        assert out['ok'] is False and 'This user not target' in out['error']

    async def test_it_never_raises(self, serve, monkeypatch):
        """⚠ Принимает круг по всем анкетам: исключение унесло бы остальных."""
        async def boom(*a, **kw):
            raise RuntimeError('сайт лёг')

        monkeypatch.setattr(chat_request, 'chat_socket_open', boom)

        out = await chat_request_accept(PROFILE, PRESENCE, MAN)

        assert out['ok'] is False and 'сайт лёг' in out['error']


class TestItAcceptsTheWholeQueue:
    """Заход по всем ждущим."""

    async def test_one_failure_does_not_stop_the_rest(self, serve, monkeypatch):
        """⚠ Каждый запрос сам по себе: неудача на первом не повод бросать
        второго."""
        serve(_Client(_auth(_request(create=MAN), _request(create=OTHER)), []))

        tried = []

        async def accept(profile, presence, uid, proxy=''):
            tried.append(uid)

            return {'ok': uid == OTHER, 'response': '',
                    'error': '' if uid == OTHER else 'сайт отказал'}

        monkeypatch.setattr(chat_request, 'chat_request_accept', accept)

        out = await chat_request_accept_all(PROFILE, PRESENCE)

        assert tried == [MAN, OTHER], 'второй запрос не попробовали'
        assert out == {'found': 2, 'taken': 1, 'error': ''}

    async def test_the_batch_has_a_ceiling(self, serve, monkeypatch):
        """⚠ Потолок не про нагрузку: каждый принятый открывает разговор,
        который кто-то должен вести."""
        serve(_Client(_auth(*[_request(create=str(1_000_000 + n))
                              for n in range(5)]), []))
        monkeypatch.setattr(chat_request, 'CHAT_REQUEST_MAX', 2)

        async def accept(profile, presence, uid, proxy=''):
            return {'ok': True, 'response': '', 'error': ''}

        monkeypatch.setattr(chat_request, 'chat_request_accept', accept)

        out = await chat_request_accept_all(PROFILE, PRESENCE)

        assert out['found'] == 2 and out['taken'] == 2

    async def test_a_broken_connection_is_a_reason_not_a_crash(self, monkeypatch):
        async def boom(*a, **kw):
            raise ChatSocketError('вход не выполнен')

        monkeypatch.setattr(chat_request, 'chat_socket_open', boom)

        out = await chat_request_accept_all(PROFILE, PRESENCE)

        assert out['taken'] == 0 and 'вход не выполнен' in out['error']


class TestItDoesNotRefuseRequests:
    """⚠ Отклонять не умеем намеренно: отказ — решение человека, а не круга.

    Метод у сайта есть (`cancel`), и добавить его недолго. Но промолчать можно
    всегда, а отклонённый запрос собеседник увидит отказом — пусть это будет
    осознанная правка, а не побочная возможность.
    """

    def test_no_cancel_anywhere(self):
        import inspect

        body = inspect.getsource(chat_request)
        code = '\n'.join(one for one in body.split('\n')
                         if not one.strip().startswith('#'))

        assert "'cancel'" not in code and '"cancel"' not in code

    def test_but_accepting_goes_through_the_shared_transport(self):
        """⚠ И своего HTTP здесь нет: кадры шлёт общий `chat_socket`."""
        from tests_.tests_guard import tests_guard_calls

        calls = tests_guard_calls(chat_request)

        assert 'chat_socket_send' in calls
        assert 'chat_socket_open' in calls
        assert 'AsyncClient' not in calls, 'появился свой клиент мимо транспорта'
