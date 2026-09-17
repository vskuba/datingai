"""Отправка в чат Goldenbride: какие кадры уходят и как читается ответ.

**Написаны до выноса протокола в драйверы сайтов и переехали на них без единой
правки ожиданий** — как и `test_chat_frames.py`. Сменились только адреса функций
(`_frame_build` → `site.send_frame`, `dating_send_uid` → `site.message_uid` и так
далее); что проверяется и каких значений ждём — слово в слово прежнее. В этом и
был смысл: доказать, что перенос не изменил ни одного кадра на проводе.

Отсюда правило: **ожидания здесь не правятся заодно с кодом.**

Здесь дороже всего не разбор, а **порядок**: приветствие, прелюдия, сообщение,
подтверждение. Каждый шаг этой цепочки система когда-то пропускала, и каждый раз
это выглядело одинаково — сайт принимал кадр, показывал его в переписке
отправителя и не доставлял никому. Отсюда и проверки на вид «зачем это вообще»:
подпротокол `chat`, непустой `content` в запросе истории, ожидание второго кадра
подтверждения.

Сеть подменена фейковым сокетом: он копит отправленное и отдаёт заготовленные
ответы. Базы здесь нет вовсе — `_send_ws` в неё не ходит, запись отправленного
живёт уровнем выше, в `send_message`.
"""

import json
from pathlib import Path

import pytest

from src.dating import send as send_module
from src.dating.send import _ack_wait, _hello_wait, _open_dialog, _send_ws
from src.dating.site.site_base import site_base_render
from src.dating.site.site_registry import site_registry_chat

pytestmark = pytest.mark.chat
# Драйвер сайта — предмет проверки для всего, что касается формы кадров. Через
# реестр, а не прямым импортом класса: заодно проверяется, что умолчание реестра
# ведёт к Goldenbride (пока в закладках нет ключа `driver`, на этом держится прод).
SITE = site_registry_chat({})

FRAMES = json.loads((Path(__file__).parent / 'frames' / 'goldenbride_chat.json')
                    .read_text(encoding='utf-8'))

# Литералы протокола — не импорт констант модуля. Причина та же, что в
# `test_chat_frames.py`: константа, взятая из проверяемого кода, переименуется
# вместе с ним, и тест останется зелёным на изменившемся проводе.
SUBPROTOCOL = 'chat'
METHOD_SEND = 'sendPrivateMessage'
PRELUDE = ('getHistoryByContactIds', 'getChatInfo', 'startTyping', 'stopTyping')

TO_UID = '40317'
MESSAGE_UID = '01J8QK3F2M7NPQRSTVWXYZ0140'

# Учётка со снятым адресом сокета: без него отправка не начинается вовсе.
PROFILE = {'id': 7, 'login': 'natalia@example.com', 'ws_url': 'wss://goldenbride.net/chat'}


class TestMessageUid:
    """Идентификатор сообщения — ULID, как его делает страница сайта.

    Это не косметика: прежний идентификатор был шестнадцатеричной строкой, время
    в нём читалось как 14554 год, и сообщения не доходили. Единственным различием
    между дошедшим кадром со страницы и нашим был именно он.
    """

    def test_length_and_alphabet(self):
        uid = SITE.message_uid()

        assert len(uid) == 26
        # Crockford base32: без I, L, O, U — чтобы не путались с цифрами.
        assert set(uid) <= set('0123456789ABCDEFGHJKMNPQRSTVWXYZ')
        assert not (set(uid) & set('ILOU')), 'в алфавите не должно быть похожих на цифры букв'

    def test_head_carries_current_time(self):
        """Первые десять знаков — время в миллисекундах, и оно настоящее.

        Проверяем декодированием обратно: расхождение больше суток означало бы,
        что формат разъехался — ровно та поломка, что уже была.
        """
        import time

        alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
        head = SITE.message_uid()[:10]

        stamp = 0
        for char in head:
            stamp = stamp * 32 + alphabet.index(char)

        assert abs(stamp / 1000 - time.time()) < 86400, 'время в идентификаторе не сходится'

    def test_uids_are_unique(self):
        assert len({SITE.message_uid() for _ in range(200)}) == 200


class TestRender:
    """Подстановки в шаблоне кадра: сайты называют поля по-разному."""

    def test_all_three_marks(self):
        rendered = site_base_render('{{to}}|{{text}}|{{uid}}', TO_UID, 'привет', MESSAGE_UID)

        assert rendered == f'{TO_UID}|привет|{MESSAGE_UID}'

    def test_unknown_marks_are_left_alone(self):
        assert site_base_render('{{who}}', TO_UID, 'x', MESSAGE_UID) == '{{who}}'


class TestSendFrame:
    """Кадр самого сообщения."""

    def test_default_frame_is_goldenbride_shape(self):
        """Настройки нет — берётся зашитый шаблон. Именно он уходит на прод."""
        frame = SITE.send_frame(TO_UID, 'привет', MESSAGE_UID)

        assert frame == {'toId': TO_UID, 'content': 'привет',
                         'method': METHOD_SEND, 'messageIdForConfirm': MESSAGE_UID}

    def test_site_template_wins(self):
        """Свой шаблон сайта подставляется целиком — Python ради него не правят."""
        frame = site_registry_chat({'send': {'frame': {'to': '{{to}}', 'body': '{{text}}', 'op': 'send'}}}
                                 ).send_frame(TO_UID, 'привет', MESSAGE_UID)

        assert frame == {'to': TO_UID, 'body': 'привет', 'op': 'send'}


class TestPrelude:
    """Прелюдия: что страница делает перед отправкой.

    Снято с живой страницы. Наш клиент слал только сообщение — сервер принимал
    кадр и никому его не отдавал.
    """

    async def test_prelude_order_and_places(self, no_pause):
        socket = _FakeSocket()
        await _open_dialog(socket, TO_UID, SITE)

        frames = [json.loads(raw) for raw in socket.sent]

        assert [f['method'] for f in frames] == list(PRELUDE)
        # У каждого кадра своё место для номера собеседника, и это не придирка к
        # форме: `getHistoryByContactIds` именно полем `content` открывает диалог.
        # С пустым `content` сервер принимал сообщение и не доставлял никому.
        places = {f['method']: (f['toId'], f['content']) for f in frames}
        assert places['getHistoryByContactIds'] == ('', f'{TO_UID},')
        assert places['getChatInfo'] == ('', '')
        assert places['startTyping'] == (TO_UID, '')
        assert places['stopTyping'] == (TO_UID, '')

    async def test_site_may_replace_prelude(self, no_pause):
        socket = _FakeSocket()
        await _open_dialog(socket, TO_UID, site_registry_chat({'send': {'before': ['ping']}}))

        assert [json.loads(raw)['method'] for raw in socket.sent] == ['ping']

    async def test_site_may_drop_prelude(self, no_pause):
        """Пустой список — не «взять по умолчанию», а «прелюдии нет».

        Отличается от отсутствующего ключа намеренно: у сайта без прелюдии её
        нечем задать иначе.
        """
        socket = _FakeSocket()
        await _open_dialog(socket, TO_UID, site_registry_chat({'send': {'before': []}}))

        assert socket.sent == []

    async def test_broken_socket_stops_quietly(self, no_pause):
        """Оборванная прелюдия не бросает: отправку решает вызывающий."""
        await _open_dialog(_FakeSocket(broken=True), TO_UID, SITE)


class TestHelloWait:
    """Приветствие сервера. До него присланное клиентом игнорируется молча."""

    @pytest.mark.parametrize('frame', ['hello', 'hello_again'])
    async def test_greeting_is_recognised(self, frame):
        assert await _hello_wait(_FakeSocket(recv=[FRAMES[frame]]), SITE) is True

    async def test_silence_is_not_a_greeting(self):
        assert await _hello_wait(_FakeSocket(recv=[]), SITE) is False

    async def test_foreign_frames_before_greeting_are_skipped(self):
        socket = _FakeSocket(recv=[FRAMES['foreign_ping'], FRAMES['foreign_billing'],
                                   FRAMES['hello']])

        assert await _hello_wait(socket, SITE) is True


class TestAckWait:
    """Ответ про наше сообщение среди потока чужих кадров."""

    async def test_own_ack_is_picked_from_stream(self):
        socket = _FakeSocket(recv=[FRAMES['incoming_live'], FRAMES['foreign_billing'],
                                   FRAMES['ack_server']])
        answer = await _ack_wait(socket, SITE, MESSAGE_UID)

        assert 'confirmFromServer' in answer
        assert 'receivePrivateMessage' not in answer, 'чужой кадр попал в ответ'

    async def test_echo_alone_does_not_stop_waiting(self):
        """На принятое сервер шлёт два кадра: эхо клиенту и своё подтверждение.

        Остановиться на первом значит принять эхо за доставку — так и было.
        Оба кадра несут наш номер, поэтому оба попадают в ответ, но ожидание
        обязано дойти до второго.
        """
        socket = _FakeSocket(recv=[FRAMES['ack_echo_client'], FRAMES['ack_server']])
        answer = await _ack_wait(socket, SITE, MESSAGE_UID)

        assert 'confirmFromClient' in answer and 'confirmFromServer' in answer

    async def test_silence_gives_empty_answer(self):
        assert await _ack_wait(_FakeSocket(recv=[]), SITE, MESSAGE_UID) == ''


class TestSendOutcome:
    """Чем кончилась отправка — и это главный вопрос ко всему модулю."""

    async def test_accepted(self, no_pause, fake_ws):
        result = await _send_ws(PROFILE, SITE, TO_UID, 'привет', MESSAGE_UID)

        assert result['ok'] is True
        assert result['error'] == ''
        assert result['message_uid'] == MESSAGE_UID

    async def test_subprotocol_is_negotiated(self, no_pause, fake_ws):
        """Подпротокол `chat` согласуется всегда.

        Без него сокет устанавливается и входящие идут, а всё присланное клиентом
        сервер игнорирует молча — ни ответа, ни ошибки. Из-за одного этого
        заголовка все отправки уходили в никуда.
        """
        await _send_ws(PROFILE, SITE, TO_UID, 'привет', MESSAGE_UID)

        assert fake_ws.kwargs['subprotocols'] == [SUBPROTOCOL]

    async def test_message_goes_after_prelude(self, no_pause, fake_ws):
        """Сообщение уходит последним — после приветствия и прелюдии."""
        await _send_ws(PROFILE, SITE, TO_UID, 'привет', MESSAGE_UID)

        methods = [json.loads(raw)['method'] for raw in fake_ws.socket.sent]
        assert methods == list(PRELUDE) + [METHOD_SEND]

    async def test_offline_user_is_a_state_not_a_failure(self, no_pause,
                                                         fake_ws_with):
        """Собеседник вышел из чата — это состояние, а не поломка.

        Автоответ такое не помечает отвеченным и не пишет в ошибки: собеседник
        вернётся, и ответ уйдёт тогда.
        """
        result = await fake_ws_with([FRAMES['hello'], FRAMES['offline_user']])

        assert result['ok'] is False
        assert result['offline'] is True
        assert 'вышел из чата' in result['error']

    @pytest.mark.parametrize('frame,mark', [
        ('refuse_words', 'Unacceptable words'),
        ('refuse_limit', 'Not more than one message per minute'),
    ])
    async def test_site_refusal_is_quoted(self, no_pause, fake_ws_with, frame, mark):
        """Отказ сайта показываем словами сайта, а не «не подтвердил приём».

        Механика при отказе цела — просто действие платное или текст не прошёл
        фильтр. Спрятать это значит выдать чужое правило за нашу поломку.
        """
        result = await fake_ws_with([FRAMES['hello'], FRAMES[frame]])

        assert result['ok'] is False
        assert 'сайт отказал' in result['error']
        assert mark in result['error']
        # Отказ — не «нет в чате»: собеседник на месте, не прошло сообщение.
        assert not result.get('offline')

    @pytest.mark.parametrize('frames', [
        pytest.param(['hello', 'ack_server'], id='принято'),
        pytest.param(['hello', 'offline_user'], id='нет в чате'),
        pytest.param(['hello', 'refuse_words'], id='отказ'),
        pytest.param(['hello'], id='молчание'),
        pytest.param([], id='без приветствия'),
    ])
    async def test_answer_shape_is_the_same_in_every_outcome(self, no_pause, fake_ws_with,
                                                             frames):
        """Ключи одни и те же, чем бы отправка ни кончилась.

        Это **исправление**, а не фиксация: прежде `offline` клали только ветка
        «нет в чате» и `_fail`, а отказ, молчание и удача — нет. Держалось это на
        аккуратности единственного читателя (`answer.py`), который берёт
        ключ через `.get()`; второй читатель, спросивший `result['offline']`,
        получил бы `KeyError` на ровном месте.

        Правка сделана отдельным шагом, после того как перенос протокола в
        драйверы был доказан прежними тестами. Заодно с рефакторингом её делать
        было нельзя: тогда «тесты зелёные» перестало бы что-либо значить.
        """
        result = await fake_ws_with([FRAMES[name] for name in frames])

        assert set(result) == {'ok', 'error', 'offline', 'response', 'message_uid'}
        assert isinstance(result['offline'], bool), 'отсутствие ключа — не то же, что False'

    async def test_echo_without_server_ack_is_not_delivered(self, no_pause, fake_ws_with):
        """Одно эхо без подтверждения сервера — сообщение не доставлено.

        Раньше такое молчание считалось удачей, и мы месяцами думали, что пишем.
        """
        result = await fake_ws_with([FRAMES['hello'], FRAMES['ack_echo_client']])

        assert result['ok'] is False
        assert result['error'] == 'сайт не подтвердил приём сообщения'

    async def test_silence_is_not_delivery(self, no_pause, fake_ws_with):
        result = await fake_ws_with([FRAMES['hello']])

        assert result['ok'] is False
        assert result['error'] == 'сайт не подтвердил приём сообщения'

    async def test_no_greeting_means_nowhere_to_send(self, no_pause, fake_ws_with):
        result = await fake_ws_with([])

        assert result['ok'] is False
        assert 'не поздоровался' in result['error']

    async def test_missing_socket_url_is_told_plainly(self):
        """Адрес сокета не снят — говорим, что делать, а не «ошибка соединения»."""
        result = await _send_ws({**PROFILE, 'ws_url': ''}, SITE, TO_UID, 'x', MESSAGE_UID)

        assert result['ok'] is False
        assert 'Залогиниться' in result['error']


class TestRefuseReason:
    """Причина отказа человеческими словами, а не кадром целиком."""

    def test_reason_is_extracted(self):
        assert 'Unacceptable words' in SITE.refuse_reason(FRAMES['refuse_words'])

    def test_unknown_answer_is_cut_not_lost(self):
        assert SITE.refuse_reason('что-то незнакомое') == 'что-то незнакомое'


# ── Обвязка ──────────────────────────────────────────────────────────────────

class _FakeSocket:
    """Сокет: копит отправленное, отдаёт заготовленные ответы.

    Кончились ответы — обрыв, а не ожидание: иначе тест простоял бы на реальных
    таймаутах отправки (6 секунд на подтверждение, 15 на приветствие).
    """

    def __init__(self, recv: list | None = None, broken: bool = False):
        self.sent: list[str] = []
        self.queue = list(recv or [])
        self.broken = broken

    async def send(self, raw):
        if self.broken:
            raise ConnectionError('соединение закрыто')
        self.sent.append(raw)

    async def recv(self):
        if not self.queue:
            raise ConnectionError('соединение закрыто')

        return self.queue.pop(0)


class _FakeConnect:
    """`websockets.connect(...)` как асинхронный контекст."""

    def __init__(self, socket, box):
        self.socket = socket
        self.box = box

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *args):
        return False


class _WsBox:
    """Что запомнилось от подключения: сам сокет и аргументы `connect`."""

    def __init__(self):
        self.socket = _FakeSocket()
        self.kwargs: dict = {}
        self.url = ''


@pytest.fixture
def no_pause(monkeypatch):
    """Убирает паузу между кадрами прелюдии — в тесте ждать нечего."""
    monkeypatch.setattr(send_module, 'SEND_BEFORE_PAUSE', 0)


@pytest.fixture
def fake_ws(monkeypatch):
    """Подключение с обычным удачным разговором: приветствие и подтверждение."""
    return _ws_install(monkeypatch, [FRAMES['hello'], FRAMES['ack_server']])


@pytest.fixture
def fake_ws_with(monkeypatch):
    """То же, но кадры ответа задаёт сам тест. Возвращает готовую отправку."""
    async def run(frames):
        _ws_install(monkeypatch, frames)

        return await _send_ws(PROFILE, SITE, TO_UID, 'привет', MESSAGE_UID)

    return run


def _ws_install(monkeypatch, frames: list) -> _WsBox:
    box = _WsBox()
    box.socket.queue = list(frames)

    def connect(url, **kwargs):
        box.url = url
        box.kwargs = kwargs

        return _FakeConnect(box.socket, box)

    monkeypatch.setattr(send_module.websockets, 'connect', connect)

    return box


class TestBodyEncode:
    """⚠ Кодирование тела HTTP-отправки: чужая древность, воспроизведённая как есть.

    Страница чата LadaDate шлёт сообщение так (`chat-app.js`):

        $.ajax({ url: "/chat-send", data: { id: …, body: escape(mes) } })

    То есть устаревший `escape()` **внутри** form-urlencode — двойное кодирование.
    Пойманное живым перехватом тело выглядит как `body=Henry%252C%2520do…`, где
    `%252C` → `%2C` → запятая.

    Сделай мы «правильно», одним слоем, — у собеседника на месте запятой оказался
    бы `%2C`. Поэтому проверяется именно двойной слой, а не «корректный URL».
    """

    def test_plain_text_goes_through_untouched(self):
        from src.dating.site.site_base import site_base_encode

        assert site_base_encode('Hello', '') == 'Hello'

    def test_form_encoding_is_single_layer(self):
        """`form` — для сайта с нормальной формой: один слой, пробел плюсом."""
        from src.dating.site.site_base import site_base_encode

        assert site_base_encode('a b&c', 'form') == 'a+b%26c'

    def test_js_escape_form_is_double_encoded(self):
        """⚠ Двойной слой: `%` первого кодирования сам кодируется во втором."""
        from src.dating.site.site_base import site_base_encode

        # escape(', ') = '%2C%20'; дальше form-urlencode делает '%252C%2520'.
        assert site_base_encode(', ', 'js_escape_form') == '%252C%2520'

    def test_non_ascii_becomes_percent_u(self):
        """⚠ `%uXXXX` нет ни в одном стандарте, и ни один URL-кодировщик так не
        делает. Проверено живой отправкой: `Michał` доехал целым именно так."""
        from src.dating.site.site_base import site_base_js_escape

        assert site_base_js_escape('ł') == '%u0142'

    def test_round_trip_matches_what_the_site_gets(self):
        """Двойное раскодирование возвращает исходный текст — то же, что делает
        сервер сайта, разбирая тело."""
        from urllib.parse import unquote

        from src.dating.site.site_base import site_base_encode

        text = 'Henry, do you prefer a relaxed weekend?'
        got = site_base_encode(text, 'js_escape_form')

        assert unquote(unquote(got)) == text

    def test_unknown_name_leaves_value_as_is(self):
        """⚠ Опечатка в `presence.send_module.encode` не должна останавливать отправку:
        `presence` правят у соседа прямо в базе. Худшее, что случится, — тело
        уйдёт незакодированным, как было до этой функции."""
        from src.dating.site.site_base import site_base_encode

        assert site_base_encode('a b', 'формурленкод') == 'a b'

    def test_text_is_encoded_before_substitution(self):
        """⚠ Порядок важен: кодируем **до** подстановки в шаблон.

        `site_base_render` — простая замена метки, и незакодированный пробел или
        `&` порвали бы тело `id={{to}}&body={{text}}`: сообщение ушло бы обрезанным
        по первому пробелу.
        """
        from src.dating.site.site_base import site_base_encode, site_base_render

        body = site_base_render('id={{to}}&body={{text}}', '845089',
                                site_base_encode('a b&c', 'form'), '')

        assert body == 'id=845089&body=a+b%26c'


class TestSourceOfSend:
    """⚠ Чем ушло — первый вопрос разбора, и столбец обязан отвечать точно.

    Раньше в коде стояло `'scenario' if transport == 'scenario' else 'socket'`, и
    это было верно, пока HTTP-путь никем не был настроен. С переводом LadaDate на
    `/chat-send` каждое исходящее стало писаться как `socket` — у сайта, у которого
    сокета нет вовсе, то есть ровно та путаница, от которой столбец и заводился.
    """

    def test_every_transport_has_its_own_source(self):
        from src.dating.send import SEND_SOURCE

        assert SEND_SOURCE['http'] == 'http'
        assert SEND_SOURCE['scenario'] == 'scenario'
        assert SEND_SOURCE['ws'] == 'socket'

    def test_unknown_transport_falls_back_to_socket(self):
        """Незнакомый транспорт — `socket`: умолчание столбца, а не отказ записи.
        Потерять отправленное сообщение из переписки хуже, чем ошибиться в
        подписи, чем оно ушло."""
        from src.dating.send import SEND_SOURCE

        assert SEND_SOURCE.get('чего-то новое', 'socket') == 'socket'
