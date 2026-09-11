"""Разбор кадров чата Goldenbride: что из провода становится сообщением.

**Написаны до выноса протокола в драйверы сайтов и переехали на них без единой
правки ожиданий** — сменились только адреса функций (`_message_parse` →
`site.incoming_parse` и так далее). Что проверяется и каких значений ждём, осталось
слово в слово: в этом и был смысл — доказать, что перенос ничего не изменил.

Отсюда правило на будущее: **ожидания в этом файле не правятся заодно с кодом.**
Понадобилось изменить поведение — это отдельный шаг с отдельным обоснованием, а не
приписка к рефакторингу.

Кадры лежат отдельно (`tests/frames/goldenbride_chat.json`) строками, ровно в том
виде, в каком приходят по сокету. Про их происхождение — `tests/frames/readme.md`.

Без сети и базы: разбор кадра — чистая функция, а цена ошибки в ней молчаливая.
Пропущенное сообщение не выглядит как поломка: анкета работает, чат идёт, просто
одного письма нет. Именно в таком виде система теряла сообщения месяцами.
"""

import json
from pathlib import Path

import pytest

from src.dating.dating_chat import _confirm, _pending_take
from src.dating.site.site_registry import site_registry_chat

pytestmark = pytest.mark.chat
# Драйвер сайта: он и есть предмет проверки. Берётся через реестр, а не прямым
# импортом класса, — заодно проверяется, что умолчание реестра ведёт к
# Goldenbride: пока в закладках нет ключа `driver`, на этом держится прод.
SITE = site_registry_chat({})

FRAMES = json.loads((Path(__file__).parent / 'frames' / 'goldenbride_chat.json')
                    .read_text(encoding='utf-8'))

# Логин анкеты, под которой слушаем. Нужен ровно для одного: отличить своё
# вернувшееся эхо от чужого сообщения.
OUR_LOGIN = 'natalia@example.com'

# Имена методов и длины колонок писаны здесь **литералами, а не импортом
# констант** — и это не небрежность.
#
# Константу проверяемого модуля тест утаскивает за собой: переименуй
# `confirmFromClient` в коде, и ожидание переименуется вместе с ним, а тест
# останется зелёным. Проверено мутацией — так и вышло. Имя метода задаёт сайт, а
# не мы, и в тесте оно обязано стоять само по себе.
METHOD_HELLO = 'connectFromServer'
METHOD_MESSAGE = 'receivePrivateMessage'
METHOD_PENDING = 'notSendMessages'
METHOD_CONFIRM = 'confirmFromClient'

# Длины колонок `dating_message`, под которые режется разобранное.
LEN_UID, LEN_NAME, LEN_TEXT, LEN_MESSAGE_UID = 32, 190, 2000, 64


class TestMethodOf:
    """Имя метода кадра. По нему слушатель решает, что с кадром делать вообще."""

    @pytest.mark.parametrize('frame,method', [
        ('hello', METHOD_HELLO),
        ('incoming_live', METHOD_MESSAGE),
        ('pending', METHOD_PENDING),
        ('ack_echo_client', METHOD_CONFIRM),
        ('foreign_billing', 'billings'),
    ])
    def test_method_is_read(self, frame, method):
        assert SITE.method_of(FRAMES[frame]) == method

    @pytest.mark.parametrize('frame', ['garbage', 'json_array'])
    def test_unreadable_frame_gives_empty(self, frame):
        """Мусор даёт пустую строку, а не исключение.

        Кадр приходит из сети, и одна битая строка не имеет права оборвать
        слушание: за ней идут нормальные сообщения.
        """
        assert SITE.method_of(FRAMES[frame]) == ''


class TestIncoming:
    """Живое входящее: кадр `receivePrivateMessage` с телом-строкой внутри JSON."""

    def test_live_message_is_parsed(self):
        message = SITE.incoming_parse(FRAMES['incoming_live'], OUR_LOGIN)

        assert message == {'sender_uid': '40317',
                           'sender_name': 'Nadezhda',
                           'text': 'Привет! Как твои дела?',
                           'message_uid': '01J8QK3F2M7NPQRSTVWXYZ0123'}

    def test_own_echo_is_dropped(self):
        """Своё же сообщение, вернувшееся эхом, входящим не считается.

        Показать человеку его собственный текст как новое письмо хуже, чем не
        показать ничего.
        """
        assert SITE.incoming_parse(FRAMES['incoming_echo'], OUR_LOGIN) is None

    def test_echo_is_told_by_sender_not_by_gender(self):
        """Отличаем своё по отправителю, а не по `isMan`.

        Это поле говорит, мужчина ли написал, а вовсе не «мы ли». У женской анкеты
        входящие от мужчин помечены `isMan: true`, и проверка на него глушила ровно
        те сообщения, ради которых всё и затевалось.
        """
        from_man = SITE.incoming_parse(FRAMES['incoming_live'], OUR_LOGIN)
        from_woman = SITE.incoming_parse(FRAMES['incoming_from_woman'], OUR_LOGIN)

        assert from_man is not None, 'сообщение от мужчины отсеялось'
        assert from_woman is not None, 'сообщение от женщины отсеялось'

    def test_echo_check_needs_our_login(self):
        """Без логина анкеты эхо не отличить — и оно проходит как входящее.

        Так ведёт себя код сейчас: `profile_login` по умолчанию пуст. Зафиксировано
        не как желаемое, а как есть: слушатель логин всегда передаёт.
        """
        assert SITE.incoming_parse(FRAMES['incoming_echo']) is not None

    @pytest.mark.parametrize('frame,why', [
        ('incoming_no_uid', 'без отправителя писать в переписку некому'),
        ('incoming_empty_text', 'пустой текст показывать нечем'),
        ('incoming_body_broken', 'тело не разбирается'),
        ('foreign_billing', 'чужой метод — вёрстка чата, не сообщение'),
        ('foreign_ping', 'служебный кадр'),
        ('garbage', 'не JSON вовсе'),
        ('json_array', 'JSON, но не объект'),
    ])
    def test_unsuitable_frames_give_none(self, frame, why):
        assert SITE.incoming_parse(FRAMES[frame], OUR_LOGIN) is None, why

    def test_missing_name_is_empty_not_failure(self):
        """Имя отправителя приходит не всегда — это не повод терять сообщение."""
        message = SITE.incoming_parse(FRAMES['incoming_no_name'], OUR_LOGIN)

        assert message is not None
        assert message['sender_name'] == ''
        assert message['text'] == 'без имени отправителя'

    def test_missing_confirm_id_is_empty(self):
        """Без номера подтверждения сообщение принимается, но подтвердить нечем.

        Дальше это значит две вещи: сайт пришлёт его снова, а от повтора спасёт
        уникальный ключ в базе — не подтверждение.
        """
        message = SITE.incoming_parse(FRAMES['incoming_no_confirm_id'], OUR_LOGIN)

        assert message is not None
        assert message['message_uid'] == ''

    def test_body_may_come_as_object(self):
        """Тело кадра бывает и объектом, а не строкой с JSON внутри."""
        message = SITE.incoming_parse(FRAMES['incoming_body_object'], OUR_LOGIN)

        assert message is not None
        assert message['text'] == 'тело объектом, а не строкой'

    def test_long_values_are_cut_to_column_width(self):
        """Длины режутся под колонки `dating_message`, а не летят в базу как есть.

        Иначе длинный текст ронял бы вставку, и сообщение пропадало целиком —
        вместе с теми, что пришли в том же кадре.
        """
        frame = json.dumps({'method': METHOD_MESSAGE,
                            'message': json.dumps({'senderUid': 'u' * 100,
                                                   'senderName': 'n' * 500,
                                                   'message': 'т' * 5000,
                                                   'messageIdForConfirm': 'i' * 200})})
        message = SITE.incoming_parse(frame, OUR_LOGIN)

        assert len(message['sender_uid']) == LEN_UID
        assert len(message['sender_name']) == LEN_NAME
        assert len(message['text']) == LEN_TEXT
        assert len(message['message_uid']) == LEN_MESSAGE_UID


class TestPending:
    """Накопившееся (`notSendMessages`): приходит другой формой, чем живое.

    Без вложенного JSON, поля зовутся `id` и `text`. Приводится к общему виду,
    чтобы дальше путь был один.
    """

    async def test_pending_becomes_messages(self, saver):
        saved = await _pending_take(None, 7, FRAMES['pending'], SITE)

        assert saved == 2
        assert saver.saved == [
            {'sender_uid': '40317', 'sender_name': '',
             'text': 'писала, пока тебя не было', 'message_uid': '01J8QK3F2M7NPQRSTVWXYZ0130'},
            {'sender_uid': '40412', 'sender_name': '',
             'text': 'и я тоже писала', 'message_uid': '01J8QK3F2M7NPQRSTVWXYZ0131'},
        ]

    async def test_empty_queue_saves_nothing(self, saver):
        """`notSendMessages: []` — именно так сервер отвечает на запрос истории
        с пустым `content`. Раньше на этом терялось всё пропущенное: очередь на
        сервере была, а мы считали её пустой."""
        assert await _pending_take(None, 7, FRAMES['pending_empty'], SITE) == 0
        assert saver.saved == []

    async def test_broken_items_are_skipped_not_fatal(self, saver):
        """Негодная запись пропускается, годная в том же кадре — сохраняется.

        Пачка приходит целиком, и одна кривая запись не имеет права утащить с
        собой остальные.
        """
        saved = await _pending_take(None, 7, FRAMES['pending_broken_item'], SITE)

        assert saved == 1
        assert [m['text'] for m in saver.saved] == ['единственная годная']

    @pytest.mark.parametrize('frame', ['garbage', 'incoming_live'])
    async def test_unreadable_pending_gives_zero(self, saver, frame):
        assert await _pending_take(None, 7, FRAMES[frame], SITE) == 0

    async def test_saved_message_is_confirmed(self, saver):
        """Каждое разобранное подтверждается — иначе сайт пришлёт его снова."""
        await _pending_take(None, 7, FRAMES['pending'], SITE)

        assert [f['method'] for f in saver.confirmed] == [METHOD_CONFIRM] * 2
        assert [f['messageIdForConfirm'] for f in saver.confirmed] == [
            '01J8QK3F2M7NPQRSTVWXYZ0130', '01J8QK3F2M7NPQRSTVWXYZ0131']


class TestConfirm:
    """Кадр подтверждения: чем отвечаем на принятое сообщение."""

    async def test_confirm_frame_shape(self):
        sent = []
        await _confirm(_FakeSocket(sent), {'sender_uid': '40317', 'text': 'x',
                                           'message_uid': '01J8QK3F2M7NPQRSTVWXYZ0130'}, SITE)

        assert json.loads(sent[0]) == {'toId': '40317', 'content': '',
                                       'method': METHOD_CONFIRM,
                                       'messageIdForConfirm': '01J8QK3F2M7NPQRSTVWXYZ0130'}

    async def test_nothing_to_confirm_sends_nothing(self):
        """Без номера подтверждать нечего — молчим, а не шлём пустой кадр."""
        sent = []
        await _confirm(_FakeSocket(sent), {'sender_uid': '40317', 'message_uid': ''}, SITE)

        assert sent == []

    async def test_broken_socket_does_not_raise(self):
        """Неушедшее подтверждение не роняет слушание: повтор поймает ключ базы."""
        await _confirm(_FakeBrokenSocket(), {'sender_uid': '40317', 'message_uid': 'x'}, SITE)


# ── Обвязка ──────────────────────────────────────────────────────────────────

class _FakeSocket:
    """Сокет, который только запоминает отправленное."""

    def __init__(self, sent: list):
        self.sent = sent

    async def send(self, raw):
        self.sent.append(raw)


class _FakeBrokenSocket:
    async def send(self, raw):
        raise ConnectionError('соединение закрыто')


class _Saver:
    """Куда `_pending_take` складывает вместо базы и журнала."""

    def __init__(self):
        self.saved: list[dict] = []
        self.logged: list[tuple] = []
        self.confirmed: list[dict] = []


@pytest.fixture
def saver(monkeypatch):
    """Подменяет запись в базу, журнал и подтверждение по сокету.

    Разбор накопившегося неотделим от записи — она внутри той же функции, — и
    проверить форму разбора иначе нечем. Подменяем ровно три выхода наружу,
    оставляя всю логику разбора настоящей.
    """
    from src.dating import dating_chat

    box = _Saver()

    async def save(profile_id, message):
        box.saved.append(dict(message))
        return True

    async def log(profile_id, sender, name, text):
        box.logged.append((profile_id, sender, name, text))

    async def confirm(socket, message, site):
        if message.get('message_uid'):
            box.confirmed.append({'toId': message['sender_uid'], 'content': '',
                                  'method': METHOD_CONFIRM,
                                  'messageIdForConfirm': message['message_uid']})

    monkeypatch.setattr(dating_chat, '_message_save', save)
    monkeypatch.setattr(dating_chat, 'dating_log_message_in', log)
    monkeypatch.setattr(dating_chat, '_confirm', confirm)

    return box
