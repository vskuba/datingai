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


class TestTheRoundSurvivesItsOwnPass:
    """⚠⚠⚠ Первый сторож всякого круга — «заход не бросает».

    Этого теста здесь не было, и цена названа замером: круг выложили с `key`
    без `every`, слой отказал на первой же строке, и заход падал **каждые
    пятнадцать секунд** десять минут подряд. Снаружи он при этом выглядел
    живым: панель ничего не показывала, лента молчала, строка стояла только
    в логах воркера (`docker logs dating_worker`).

    Сюита этого не поймала, потому что круга в ней не было вовсе — проверялись
    только чтение и приём. Отсюда правило: у круга обязан быть сторож на
    **сам заход**, а не только на то, что он зовёт.
    """

    async def test_the_pass_does_not_throw_when_a_site_fails(self, monkeypatch):
        """Сайт отвечает отказом — заход возвращает итог, а не исключение."""
        from src.dating import chat_request_round

        async def sites(**kw):
            return [{'site_id': 999_345}, {'site_id': 999_346}]

        async def boom(site_id):
            raise RuntimeError('сайт недоступен')

        monkeypatch.setattr(chat_request_round, 'round_sites', sites)
        monkeypatch.setattr(chat_request_round, 'site_presence', boom)

        out = await chat_request_round.chat_request_round_run()

        assert out['taken'] == 0, 'отказ сайта не должен давать принятых'

    async def test_the_slot_asks_for_a_span(self):
        """⚠ `key` без `every` слой **отвергает**: срок отметки взять негде.

        Проверяется не число, а то, что срок вообще спрашивается: именно его
        отсутствие уронило круг на проде.
        """
        import inspect

        from src.dating import chat_request_round

        body = inspect.getsource(chat_request_round.chat_request_round_run)

        assert 'key=' in body, 'слот у круга пропал'
        assert 'every=' in body, 'слот задан без срока — заход упадёт целиком'

    async def test_the_span_equals_the_tick(self):
        """Срок слота — тик: слот тут от наложения заходов, а не для темпа."""
        from src.dating.chat_request_round import (
            CHAT_REQUEST_ROUND_TICK_SEC, _every)

        assert await _every(999_345) == CHAT_REQUEST_ROUND_TICK_SEC


class TestTheInviteOpensTheTalk:
    """⚠⚠⚠ Первая реплика и приглашение — у этого сайта одно событие.

    Запрос несёт `message`, и собеседник читает его прямо в списке
    приглашений. Значит инвайт в закрытый чат уходит не `sendMessage` —
    писать некуда, пока чата нет, — а `chatRequest/create`.

    Вид кадра взят из SDK сайта дословно (`onlineSDK.min.js`,
    `createRequest`), и проверяется здесь по полям, а не по тому, что
    «отправилось»: у этого сайта пропуск поля уже уносил реплики молча.
    """

    async def test_the_frame_is_what_the_sdk_sends(self, monkeypatch):
        from src.dating.chat_request import chat_request_create

        seen = {}

        async def open_(*a, **kw):
            return {'client': None, 'socket_id': 'sid', 'frames': []}

        async def send(opened, base, presence, s, frame):
            seen.update(frame)

            return {'ok': True, 'response': '', 'error': ''}

        monkeypatch.setattr(chat_request, 'chat_socket_open', open_)
        monkeypatch.setattr(chat_request, 'chat_socket_send', send)

        got = await chat_request_create(PROFILE, PRESENCE, MAN, 'hi there')

        assert got['ok']
        assert seen['appNameCreate'] == 'chatRequest'
        assert seen['appNameTarget'] == 'chatRequest'
        assert seen['method'] == 'create'
        assert seen['action'] == 'create', 'без `action` сайт молча выбросит кадр'
        assert seen['data'] == {'userID': int(MAN), 'message': 'hi there',
                                'attachID': 0}
        assert seen['userID'] == int(GIRL), 'кадр уходит не от нашей анкеты'

    async def test_the_attach_field_is_always_there(self, monkeypatch):
        """⚠ `attachID` нулём, но в кадре: у SDK поле стоит всегда (`a || 0`)."""
        from src.dating.chat_request import chat_request_create

        seen = {}

        async def open_(*a, **kw):
            return {'client': None, 'socket_id': 'sid', 'frames': []}

        async def send(opened, base, presence, s, frame):
            seen.update(frame)

            return {'ok': True, 'response': '', 'error': ''}

        monkeypatch.setattr(chat_request, 'chat_socket_open', open_)
        monkeypatch.setattr(chat_request, 'chat_socket_send', send)

        await chat_request_create(PROFILE, PRESENCE, MAN, 'hi')

        assert 'attachID' in seen['data']

    async def test_a_closed_talk_is_not_a_crash(self, monkeypatch):
        """⚠ Ничего не бросает: шлёт круг, обходящий анкеты сайта."""
        from src.dating.chat_request import chat_request_create

        async def boom(*a, **kw):
            raise ChatSocketError('вход не выполнен')

        monkeypatch.setattr(chat_request, 'chat_socket_open', boom)

        got = await chat_request_create(PROFILE, PRESENCE, MAN, 'hi')

        assert got['ok'] is False and 'вход не выполнен' in got['error']


class TestOnlyTheFirstReplyMayInvite:
    """⚠⚠ Приглашать вправе тот, кто начинает разговор, — и это решает
    вызывающий, а не состояние.

    «Чата нет» выглядит одинаково у инвайта и у ответа. Выведи развилку из
    состояния — и обычный ответ в закрытый разговор ушёл бы приглашением, то
    есть другим событием: собеседник увидел бы не реплику в ленте, а стук в
    список запросов.
    """

    async def test_without_the_flag_a_closed_talk_stays_offline(self, monkeypatch):
        from src.dating import send_connection

        async def open_(*a, **kw):
            return {'client': None, 'socket_id': 'sid', 'frames': []}

        monkeypatch.setattr(send_connection, 'chat_socket_open', open_)

        got = await send_connection.send_connection_run(
            PROFILE, PRESENCE, PRESENCE['send'], MAN, 'hi', 'uid-1')

        assert got['offline'] is True and got['ok'] is False
        assert 'нет открытого чата' in got['error']

    async def test_with_the_flag_it_invites(self, monkeypatch):
        from src.dating import chat_request as mod
        from src.dating import send_connection

        sent = {}

        async def open_(*a, **kw):
            return {'client': None, 'socket_id': 'sid', 'frames': []}

        async def send(opened, base, presence, s, frame):
            sent.update(frame)

            return {'ok': True, 'response': '', 'error': ''}

        monkeypatch.setattr(send_connection, 'chat_socket_open', open_)
        monkeypatch.setattr(mod, 'chat_socket_open', open_)
        monkeypatch.setattr(mod, 'chat_socket_send', send)

        got = await send_connection.send_connection_run(
            PROFILE, PRESENCE, PRESENCE['send'], MAN, 'hi', 'uid-1',
            may_invite=True)

        assert sent.get('method') == 'create', 'приглашение не ушло'
        assert got['ok'] is True
        assert got['offline'] is True, (
            'удача приглашения выдана за доставку: чат ещё не открыт, и строка '
            'пула закрылась бы навсегда')

    async def test_the_roads_that_open_a_talk_set_the_flag(self):
        """⚠ Ставят его те, кто разговор **начинает или будит**.

        Инвайт — первая реплика незнакомому. Инициатива — возврат к
        затихшему, а затихший у этого сайта бывает закрытым: собеседник жмёт
        крестик, и писать туда нечем.
        """
        import inspect

        from src.dating import initiative_round, invite_send

        for mod in (invite_send, initiative_round):
            assert 'may_invite=True' in inspect.getsource(mod), (
                f'{mod.__name__} больше не приглашает — его реплики будут '
                f'молча возвращаться в пул при закрытом разговоре')

    async def test_the_answer_does_not_invite(self):
        """⚠⚠ А ответ — не вправе, и это решение, а не забывчивость.

        Он отвечает на **свежую** реплику, то есть на живой разговор.
        Закрылся тот между его словами и нашими — человек ушёл, и ответ
        дождётся его возвращения; стучаться к нему приглашением значило бы
        подменять ответ другим событием.
        """
        import inspect

        from src.dating import answer

        assert 'may_invite' not in inspect.getsource(answer), (
            'ответ шлёт приглашения — а это другое событие, чем реплика в '
            'разговоре')

    async def test_an_invited_initiative_says_so_in_the_log(self):
        """⚠ Человеку видно, что ушло приглашением, а не репликой.

        У приглашения текст ждёт в списке запросов, пока собеседник не нажмёт
        галочку, — и журнал, назвавший это обычной отправкой, обещал бы
        доставку, которой ещё нет.
        """
        import inspect

        from src.dating import initiative_round

        body = inspect.getsource(initiative_round)

        assert 'приглашением' in body
