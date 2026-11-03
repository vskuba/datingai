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


class TestTheDriverDecidesWhatDeliveredMeans:
    """⚠⚠⚠ «Ушло» и «дошло» — разные вещи, и решает это драйвер сайта.

    Там, где разговор открывается приглашением, текст лежит у собеседника в
    списке запросов и ждёт галочки. Не нажал — сайт стирает запрос по своему
    сроку, и реплику не читал никто.

    Замер 03.11.2026: инициатива Anastasiya → Semen ушла приглашением в
    17:01:28; к 17:30 запроса не было ни в списке анкеты, ни в панели, чат не
    открылся — а обмен #4730 у соседа стоял «доставлен».

    Цена лжи та же, что у правки черновика руками: агент считает реплику
    сказанной и продолжает разговор от непрочитанного.
    """

    def test_a_site_with_invites_does_not_count_them(self):
        from src.dating.site.site_registry import site_registry_chat

        driver = site_registry_chat({'driver': 'step2love'})

        assert driver.invites() is True
        assert driver.delivered({'ok': True, 'offline': True}) is False, (
            'приглашение считается доставкой — агент продолжит разговор от '
            'непрочитанного')
        assert driver.delivered({'ok': True, 'offline': False}) is True
        assert driver.delivered({'ok': False, 'offline': True}) is False

    @pytest.mark.parametrize('driver', ['ladadate', 'goldenbride', 'base', ''])
    def test_the_other_sites_keep_the_old_meaning(self, driver):
        """⚠⚠⚠ У сайта без приглашений «доставлено» значит просто «ушло».

        Самая дорогая ошибка здесь — поправить под новый сайт и молча сменить
        смысл соседнему: заметно это стало бы не сразу, а странными ответами
        агента через неделю.
        """
        from src.dating.site.site_registry import site_registry_chat

        got = site_registry_chat({'driver': driver})

        assert got.invites() is False
        assert got.delivered({'ok': True}) is True
        assert got.delivered({'ok': True, 'offline': True}) is True, (
            f'{driver}: приглашений у сайта нет, а отметка доставки изменилась')
        assert got.delivered({'ok': False}) is False

    def test_step2love_keeps_its_chat_driver(self):
        """⚠⚠ Новый класс — наследник goldenbride, а не базы.

        До него Step2Love брал драйвер чата **по умолчанию**; класс от
        `DatingSiteChat` молча сменил бы сайту кадры и разбор вместе с
        появлением одного нового ответа.
        """
        from src.dating.site.site_goldenbride import DatingSiteChatGoldenbride
        from src.dating.site.site_registry import site_registry_chat

        assert isinstance(site_registry_chat({'driver': 'step2love'}),
                          DatingSiteChatGoldenbride)

    def test_the_round_asks_the_driver(self):
        """⚠⚠ Круг не судит о доставке сам — иначе правило разойдётся по местам."""
        import inspect

        from src.dating import initiative_round

        body = inspect.getsource(initiative_round._send)

        assert 'site_registry_chat' in body and '.delivered(' in body, (
            'круг снова решает про доставку сам')
        assert "result.get('offline')" not in body.split('delivered')[0], (
            'в круге осталась своя развилка по `offline`')


class TestTheOtherSitesAreUntouched:
    """⚠⚠⚠ Сайт без приглашений живёт как жил — проверяется по коду.

    Приглашения есть только там, где чат открывается запросом (транспорт
    `connection`). У Ladadate и прочих отправка идёт ручкой сайта или
    сценарием, и «нет открытого чата» у них не бывает в принципе.
    """

    def test_the_flag_reaches_only_the_socket_road(self):
        """⚠ `may_invite` уходит **единственному** транспорту.

        ⚠⚠ Смотрим **разобранный код**, а не текст построчно: вызов переносят,
        и проверка по строке краснела бы от переноса, а не от правки.
        """
        import ast
        import inspect
        import textwrap

        from src.dating import send

        tree = ast.parse(textwrap.dedent(inspect.getsource(send.send_message)))

        took = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            names = [one.id for one in list(node.args)
                     + [kw.value for kw in node.keywords]
                     if isinstance(one, ast.Name)]

            if 'may_invite' in names:
                took.append(getattr(node.func, 'id', '')
                            or getattr(node.func, 'attr', ''))

        assert took == ['send_connection_run'], (
            f'флаг ушёл не только в сокетный транспорт: {took} — приглашения '
            f'появились бы там, где их не бывает')

    def test_the_http_road_is_never_offline(self):
        """⚠⚠ У ручки сайта `offline` всегда `False`, и это записано в коде.

        Значит пара «удача и `offline`» — а по ней и различается приглашение —
        у такого сайта невозможна, и отметка доставки у него прежняя.
        """
        import inspect

        from src.dating import send

        body = inspect.getsource(send._send_http)

        assert "'offline': False" in body, (
            'http-дорога научилась отвечать `offline` — проверьте, не стала '
            'ли она считаться приглашением')


class TestAnInviteWaitsForItsAnswer:
    """⚠⚠⚠ Ушедшее приглашением не закрывается как доставленное — оно ждёт.

    Текст лежит у собеседника в списке запросов. Нажал галочку — доставка;
    не нажал, сайт стёр запрос — строка **убирается**, и пара снова свободна.

    Прежде такая строка закрывалась как `sent`: одно непринятое приглашение
    гасило разговор навсегда, потому что к «позванной» паре круг не
    возвращается (замер 03.11.2026, Anastasiya → Semen).
    """

    async def test_an_invite_is_marked_waiting(self, monkeypatch):
        from src.dating import chat_initiative as mod

        seen = {}

        class _Db:
            rowcount = 1

            async def execute(self, sql, args=None):
                seen['sql'], seen['args'] = sql, args

            @property
            def connection(self):
                return self

            async def commit(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(mod, 'mysql_get_db_async', lambda: _Db())

        await mod.chat_initiative_sent(7, delivered=False)

        assert seen['args'][0] == 'invited', (
            'приглашение закрыто как доставленное — пара больше не получит '
            'ни одной инициативы')

        await mod.chat_initiative_sent(7, delivered=True)

        assert seen['args'][0] == 'sent'

    async def test_the_round_asks_the_site_about_the_talk(self):
        """⚠ Судьбу решает **живой чат**, а не время само по себе."""
        import inspect

        from src.dating import initiative_round

        body = inspect.getsource(initiative_round._invited)

        assert 'chat_request_talk_open' in body
        assert 'chat_initiative_expired' in body, (
            'истекшее приглашение не убирается — пара останется «позванной» '
            'навсегда')

    async def test_an_unknown_answer_changes_nothing(self):
        """⚠⚠⚠ «Спросить не удалось» — третий ответ, и он ничего не решает.

        Убери строку по молчанию сайта — реплика пропала бы из-за одного
        моргания, а не из-за отказа собеседника.
        """
        import inspect

        from src.dating import initiative_round

        body = inspect.getsource(initiative_round._invited)

        assert 'if open_ is None:' in body and 'continue' in body

    async def test_the_site_without_invites_is_not_asked(self):
        """⚠ Сайта без приглашений это не касается — он и в базу не ходит."""
        import inspect

        from src.dating import initiative_round

        body = inspect.getsource(initiative_round._invited)
        head = body.split('chat_initiative_invited')[0]

        assert '.invites()' in head, (
            'круг спрашивает базу раньше, чем сайт — лишний запрос у тех, у '
            'кого приглашений не бывает')

    async def test_the_wait_outlasts_the_site_timeout(self):
        """⚠⚠ Запас к сроку сайта: решение необратимо, строка удаляется.

        Срок запроса у сайта — пять минут (`destroyTimeout` живого кадра).
        Поспешив, мы выбросим реплику, которую человек как раз читает;
        опоздав — всего лишь позовём его позже.
        """
        from src.dating.initiative_round import INITIATIVE_INVITE_WAIT_SEC

        assert INITIATIVE_INVITE_WAIT_SEC >= 300 * 2, (
            'запас к сроку приглашения меньше двойного — реплику выбросят у '
            'человека из-под носа')

    async def test_the_talk_check_tells_three_answers_apart(self, monkeypatch):
        """Живой чат, его отсутствие и «не спросилось» — три разных ответа."""
        from src.dating.chat_request import chat_request_talk_open

        async def open_(*a, **kw):
            return {'client': None, 'socket_id': 's',
                    'frames': [{'data': {'chatObjectList': [
                        {'chatID': 1, 'userIDMan': int(MAN),
                         'userIDGirl': int(GIRL)}]}}]}

        async def empty(*a, **kw):
            return {'client': None, 'socket_id': 's',
                    'frames': [{'data': {'chatObjectList': []}}]}

        async def boom(*a, **kw):
            raise ChatSocketError('вход не выполнен')

        monkeypatch.setattr(chat_request, 'chat_socket_open', open_)
        assert await chat_request_talk_open(PROFILE, PRESENCE, MAN) is True

        monkeypatch.setattr(chat_request, 'chat_socket_open', empty)
        assert await chat_request_talk_open(PROFILE, PRESENCE, MAN) is False

        monkeypatch.setattr(chat_request, 'chat_socket_open', boom)
        assert await chat_request_talk_open(PROFILE, PRESENCE, MAN) is None, (
            'молчание сайта принято за отказ собеседника')


class TestNoNameIsDefinedTwice:
    """⚠⚠⚠ Два определения с одним именем Python не считает ошибкой.

    Побеждает **второе**, молча, а вызов первого падает на чужих доводах уже
    в работе. Поймано на проде: `chat_initiative_drop` был заведён дважды, и
    заход инициативы ронялся каждую минуту — `missing 1 required positional
    argument: 'uid'`.

    ⚠ Сюита этого не ловила: проверки смотрели **исходник** («имя упомянуто в
    теле»), а упомянуто оно было верно — неверным оказалось то, на что имя
    указывает. Ловится это только разбором самого модуля.
    """

    @pytest.mark.parametrize('name', [
        'src.dating.chat_initiative', 'src.dating.chat_request',
        'src.dating.initiative_round', 'src.dating.initiative',
        'src.dating.send_connection', 'src.dating.chat_socket',
    ])
    def test_every_name_is_unique(self, name):
        import ast
        import importlib
        import inspect
        import textwrap

        mod = importlib.import_module(name)
        tree = ast.parse(textwrap.dedent(inspect.getsource(mod)))

        seen, twice = set(), []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                continue
            if node.name in seen:
                twice.append(node.name)
            seen.add(node.name)

        assert not twice, (
            f'{name}: {", ".join(twice)} определены дважды — побеждает '
            f'второе, и вызовы первого падают уже в работе')
