"""Протокол чата одного сайта: какие кадры уходят и как читаются пришедшие.

Каждый сайт знакомств разговаривает по-своему: имена методов, форма кадра, места
для номера собеседника, признак доставки. Раньше всё это лежало константами в
`dating_chat.py` и `dating_send.py` — под один сайт, Goldenbride, — и второй сайт
означал бы либо ветвление по всему файлу, либо копию обоих модулей.

Здесь — граница. **Драйвер отвечает только на вопросы о кадрах** и ничего не
делает сам: не ходит в сеть, не пишет в базу, не ведёт журнал. Всё это остаётся у
`dating_chat` и `dating_send` — оно про нашу механику, а не про сайт. Поэтому
драйвер — набор чистых функций, и проверяется он записанным кадром, а не живым
сайтом (`tests/test_chat_frames.py`, `tests/test_send_frames.py`).

## Два уровня

`DatingSiteChat` (здесь) описывает сайт **настройкой**: разделы `chat` и `send`
в `browser_site.presence` говорят, как называются методы и где лежат поля. Сайту с
похожей формой кадра — плоский JSON с именем метода — своего файла не нужно.

Сайту, чья форма в настройку не укладывается, заводится **свой файл** рядом:
он наследует этот класс и переопределяет то, что отличается. Так сделан
`site_goldenbride.py` — там свой формат идентификатора сообщения, который никаким
JSON не опишешь.

Правило границы: **данные — в настройку, поведение — в подкласс.** Имя метода,
имя поля, список кадров прелюдии это данные. Порядок разбора, вычисление
идентификатора, условия «считать ли доставленным» — поведение.

## Настройка

    "chat": {
      "subprotocol": "chat",
      "hello": ["connectFromServer", "reconnectFromServer"],
      "ask": [{"method": "getHistoryByContactIds", "to": "content", "suffix": ","},
              {"method": "getChatInfo"}],
      "incoming": {"method": "receivePrivateMessage", "body": "message", "body_json": true,
                   "uid": "senderUid", "name": "senderName", "text": "message",
                   "confirm_id": "messageIdForConfirm"},
      "pending":  {"method": "notSendMessages", "body": "message", "body_json": true,
                   "uid": "id", "text": "text", "confirm_id": "messageIdForConfirm"},
      "confirm":  {"method": "confirmFromClient"}
    }

    "send": {
      "transport": "ws",
      "frame": {"toId": "{{to}}", "content": "{{text}}",
                "method": "sendPrivateMessage", "messageIdForConfirm": "{{uid}}"},
      "before": [{"method": "startTyping", "to": "toId"}],
      "ack": {"ok_mark": "confirmFromServer", "refuse_mark": "not delivered",
              "offline_mark": "offlineUser"}
    }

Раздела нет — берутся значения по умолчанию из констант ниже.
"""

import json
import secrets
import time
from urllib.parse import quote_plus

# Длины колонок `dating_message`, под которые режется разобранное. Здесь, а не в
# драйвере сайта: ширина колонки — наше свойство, а не его.
DATING_SITE_UID_MAX = 32
DATING_SITE_NAME_MAX = 190
DATING_SITE_TEXT_MAX = 2000
DATING_SITE_MESSAGE_UID_MAX = 64

# Метки подстановки в шаблоне кадра: сайты называют поля по-разному, и подстановка
# избавляет от кода под каждый.
DATING_SITE_MARK_TO = '{{to}}'
DATING_SITE_MARK_TEXT = '{{text}}'
DATING_SITE_MARK_UID = '{{uid}}'


class DatingSiteChat:
    """Сайт, разговаривающий плоскими JSON-кадрами с именем метода.

    Всё берётся из настройки; чего в ней нет — из `_default_*` ниже. Подкласс
    сайта переопределяет либо эти умолчания, либо сам разбор.
    """

    # Имя драйвера — то, что пишется в `presence.driver`.
    name = 'base'

    def __init__(self, presence: dict | None = None):
        presence = presence or {}
        self.presence = presence
        self.chat = dict(presence.get('chat') or {})
        self.send = dict(presence.get('send') or {})

    # ── Умолчания. Подкласс сайта переопределяет их, а не разбор ────────────

    def _default_subprotocol(self) -> str:
        return ''

    def _default_hello(self) -> tuple:
        return ()

    def _default_ask(self) -> tuple:
        return ()

    def _default_incoming(self) -> dict:
        return {}

    def _default_pending(self) -> dict:
        return {}

    def _default_confirm(self) -> dict:
        return {}

    def _default_send_frame(self) -> dict:
        return {}

    def _default_before(self) -> tuple:
        return ()

    def _default_ack(self) -> dict:
        return {}

    # ── Соединение ──────────────────────────────────────────────────────────

    def subprotocol(self) -> str:
        """Подпротокол вебсокета (`Sec-WebSocket-Protocol`).

        У некоторых сайтов без него соединение устанавливается и входящие идут, а
        всё присланное клиентом игнорируется **молча** — ни ответа, ни ошибки.
        """
        return str(self.chat.get('subprotocol') or self.send.get('subprotocol')
                   or self._default_subprotocol())

    def is_hello(self, raw) -> bool:
        """Поздоровался ли сервер. До приветствия слать бесполезно."""
        marks = self.chat.get('hello') or self._default_hello()
        answer = raw if isinstance(raw, str) else str(raw)

        return any(str(mark) in answer for mark in marks)

    # ── Приём ───────────────────────────────────────────────────────────────

    def method_of(self, raw) -> str:
        """Имя метода кадра. Не разобрался — пустая строка, а не исключение.

        Кадр приходит из сети, и одна битая строка не имеет права оборвать
        слушание: за ней идут нормальные сообщения.
        """
        frame = self._json(raw)

        return str(frame.get('method') or '') if isinstance(frame, dict) else ''

    def is_pending(self, raw) -> bool:
        """Кадр с накопившимся за время без соединения."""
        method = str((self.chat.get('pending') or self._default_pending()).get('method') or '')

        return bool(method) and self.method_of(raw) == method

    def ask_frames(self, contacts: str) -> list:
        """Что спросить сразу после приветствия — то же, что шлёт страница чата.

        `contacts` — собеседники строкой. Кадру, у которого в настройке указано
        место (`to`), они кладутся туда: у некоторых сайтов пустой список означает
        «накопившегося нет», и очередь так и остаётся непрочитанной.
        """
        frames = []
        for step in (self.chat.get('ask') or self._default_ask()):
            frames.append(self._step_frame(step, contacts))

        return frames

    def incoming_parse(self, raw, login: str = '') -> dict | None:
        """Кадр сокета → сообщение. Чужие кадры и мусор дают None.

        `login` — логин нашей анкеты: по нему отсеивается своё же сообщение,
        вернувшееся эхом. Показать человеку его собственный текст как новое письмо
        хуже, чем не показать ничего.
        """
        rule = self.chat.get('incoming') or self._default_incoming()
        body = self._body_of(raw, rule)
        if body is None:
            return None

        text = str(body.get(rule.get('text') or 'text') or '').strip()
        sender = str(body.get(rule.get('uid') or 'uid') or '').strip()
        if not text or not sender:
            return None

        # Отличаем своё по отправителю, а не по полу или флагу «isMan»: тот
        # говорит, мужчина ли написал, а вовсе не «мы ли».
        if login and sender == str(login):
            return None

        return self._message(sender, body.get(rule.get('name') or 'name'), text,
                             body.get(rule.get('confirm_id') or 'confirm_id'))

    def pending_parse(self, raw) -> list:
        """Накопившееся → список сообщений в том же виде, что и живые.

        Приходит другой формой: без вложенного тела у каждого сообщения, поля
        зовутся иначе. Приводим к общему виду, чтобы дальше путь был один.
        """
        rule = self.chat.get('pending') or self._default_pending()
        items = self._body_of(raw, rule, expect_list=True)
        if not items:
            return []

        found = []
        for item in items:
            if not isinstance(item, dict):
                continue

            text = str(item.get(rule.get('text') or 'text') or '').strip()
            sender = str(item.get(rule.get('uid') or 'uid') or '').strip()
            if not text or not sender:
                continue

            found.append(self._message(sender, '', text,
                                       item.get(rule.get('confirm_id') or 'confirm_id')))

        return found

    def confirm_frame(self, message: dict) -> dict | None:
        """Чем подтвердить приём. Нечего подтверждать — None.

        Без подтверждения сайт пришлёт то же самое снова; от повтора спасает
        уникальный ключ в базе, а не этот кадр.
        """
        rule = self.chat.get('confirm') or self._default_confirm()
        method = str(rule.get('method') or '')
        uid = str(message.get('message_uid') or '')
        if not method or not uid:
            return None

        return {'toId': str(message.get('sender_uid') or ''), 'content': '',
                'method': method, 'messageIdForConfirm': uid}

    # ── Отправка ────────────────────────────────────────────────────────────

    def message_uid(self) -> str:
        """Идентификатор отправляемого сообщения.

        По умолчанию случайная строка. Сайту, который смотрит на её формат,
        нужен свой — переопределяется в подклассе, настройкой это не описать.
        """
        return secrets.token_hex(16)

    def prelude_frames(self, to_uid: str) -> list:
        """Что послать перед самим сообщением.

        Снято с живых страниц: сайт может принять кадр сообщения и не доставить
        его никому, если перед этим не открыть диалог. Пустой список в настройке —
        это «прелюдии нет», а не «взять по умолчанию».
        """
        steps = self.send.get('before')
        if steps is None:
            steps = self._default_before()

        return [self._step_frame(step, to_uid) for step in steps]

    def send_frame(self, to_uid: str, text: str, message_uid: str) -> dict:
        """Кадр самого сообщения по шаблону из настройки."""
        template = self.send.get('frame') or self._default_send_frame()

        return {key: site_base_render(str(value), to_uid, text, message_uid)
                for key, value in template.items()}

    def ack_read(self, answer: str, message_uid: str) -> dict:
        """Что сайт ответил про наше сообщение.

        Returns:
            dict: **всегда три ключа** — `ok` (принял ли сайт кадр), `error`
            (причина, если нет) и `offline` (собеседника нет в чате).

        Все три — в каждой ветке, а не только там, где они «интересны». Читатель
        отличает «нет в чате» от отказа по значению, и ключ, появляющийся не
        всегда, вынуждает его брать ответ через `.get()` и молча получать `None`
        там, где смысл был бы у `False`. Один раз так и вышло: форма разъехалась
        по веткам, и держалось это только на аккуратности единственного читателя.

        «Нет в чате» — состояние, а не поломка: собеседник вернётся, и ответ
        уйдёт тогда. Поэтому у отказа и молчания `offline` именно `False`, а не
        отсутствие ключа: это разные ответы на разные вопросы.
        """
        ack = dict(self.send.get('ack') or {})
        default = self._default_ack()

        offline = str(ack.get('offline_mark') or default.get('offline_mark') or '')
        if offline and offline in answer:
            return {'ok': False, 'offline': True,
                    'error': 'собеседник вышел из чата — сообщение не принимается'}

        refuse = str(ack.get('refuse_mark') or default.get('refuse_mark') or '').strip()
        if refuse and refuse.lower() in answer.lower():
            return {'ok': False, 'offline': False,
                    'error': f'сайт отказал: {self.refuse_reason(answer)}'}

        # Признак доставки — собственное подтверждение сервера, а не отсутствие
        # ошибки. Молчание здесь означает, что сообщение никуда не пошло.
        mark = str(ack.get('ok_mark') or default.get('ok_mark') or '')
        if mark and mark not in answer:
            return {'ok': False, 'offline': False,
                    'error': 'сайт не подтвердил приём сообщения'}

        return {'ok': True, 'offline': False, 'error': ''}

    def ack_method(self) -> str:
        """Имя метода ответного кадра, если сайт его называет. Пусто — узнаём по
        номеру нашего сообщения."""
        return str((self.send.get('ack') or {}).get('method') or '').strip()

    def ack_ok_mark(self) -> str:
        """Метка, на которой ожидание ответа можно прекратить."""
        ack = self.send.get('ack') or {}

        return str(ack.get('ok_mark') or self._default_ack().get('ok_mark') or '')

    def refuse_reason(self, answer: str) -> str:
        """Причина отказа человеческими словами, а не кадром целиком."""
        for line in answer.split('\\"'):
            if 'not delivered' in line.lower() or 'unacceptable' in line.lower():
                return line.strip()

        return answer[:200]

    # ── Приватное ───────────────────────────────────────────────────────────

    def _message(self, sender, name, text, message_uid) -> dict:
        """Сообщение в нашем виде, обрезанное под колонки `dating_message`.

        Режем здесь, а не при вставке: длинный текст ронял бы вставку, и
        сообщение пропадало бы целиком — вместе с пришедшими в том же кадре.
        """
        return {'sender_uid': str(sender)[:DATING_SITE_UID_MAX],
                'sender_name': str(name or '')[:DATING_SITE_NAME_MAX],
                'text': str(text)[:DATING_SITE_TEXT_MAX],
                'message_uid': str(message_uid or '')[:DATING_SITE_MESSAGE_UID_MAX]}

    def _step_frame(self, step, value: str) -> dict:
        """Кадр прелюдии или запроса. Строка — только метод; объект задаёт место.

        Место для номера собеседника у каждого кадра своё, и это не придирка к
        форме: кадр, открывающий диалог, ждёт его в одном поле, а «печатает» — в
        другом. С пустым полем сайт принимает сообщение и не доставляет никому.
        """
        if isinstance(step, str):
            return {'toId': '', 'content': '', 'method': step}

        step = dict(step or {})
        frame = {'toId': '', 'content': '', 'method': str(step.get('method') or '')}
        place = str(step.get('to') or '')
        if place:
            frame[place] = f"{value}{step.get('suffix') or ''}"

        return frame

    def _body_of(self, raw, rule: dict, expect_list: bool = False):
        """Тело кадра: сам кадр разбирается, потом — вложенное поле.

        Тело бывает строкой с JSON внутри (`body_json`) и обычным полем. Не
        подошло по методу или не разобралось — None.
        """
        frame = self._json(raw)
        if not isinstance(frame, dict):
            return None

        method = str(rule.get('method') or '')
        if method and frame.get('method') != method:
            return None

        body = frame.get(str(rule.get('body') or 'message'))
        if isinstance(body, str) and rule.get('body_json', True):
            body = self._json(body)

        if expect_list:
            return body if isinstance(body, list) else None

        return body if isinstance(body, dict) else None

    @staticmethod
    def _json(raw):
        """JSON или None. Мусор из сети — обычное дело, а не повод падать."""
        try:
            return json.loads(raw)
        except Exception:
            return None


def site_base_render(template: str, to_uid: str, text: str, message_uid: str) -> str:
    """Подставляет значения в шаблон кадра или тела запроса.

    Функцией модуля, а не методом драйвера: подстановки одни и те же и в кадре
    сокета, и в теле HTTP-запроса (`dating_send._send_http`), а два места, где
    метки называются по-разному, — это ровно тот сорт расхождения, который потом
    ищут по всему проекту.
    """
    return (template.replace(DATING_SITE_MARK_TO, str(to_uid))
                    .replace(DATING_SITE_MARK_TEXT, str(text))
                    .replace(DATING_SITE_MARK_UID, str(message_uid)))


# Незакодированные знаки устаревшего JS `escape()`. ⚠ Набор именно его, а не
# `encodeURIComponent`: у того `!'()*~` остаются, а `+` кодируется — то есть строка
# выходит другая.
SITE_BASE_JS_ESCAPE_SAFE = ('ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                            'abcdefghijklmnopqrstuvwxyz0123456789@*_+-./')


def site_base_js_escape(text: str) -> str:
    """Кодирует текст так, как это делает устаревшая функция JS `escape()`.

    ⚠ **Зачем нам чужая древность.** Страница чата LadaDate шлёт сообщение так:

        $.ajax({ url: "/chat-send", data: { id: …, body: escape(mes) } })

    То есть `escape` внутри form-urlencode — **двойное кодирование**. Сервер
    разбирает именно эту пару: пойманное тело выглядит как
    `body=Henry%252C%2520do…`, где `%252C` → `%2C` → запятая.

    Сделай мы «правильно», одним слоем, — у собеседника на месте запятой оказался
    бы `%2C`. Поэтому воспроизводим как есть.

    ⚠ Не-ASCII кодируется как `%uXXXX` — этого нет ни в одном стандарте, и ни один
    URL-кодировщик так не делает. Проверено живой отправкой: `Michał` доехал целым
    именно так.
    """
    out = []
    for char in str(text or ''):
        code = ord(char)
        if char in SITE_BASE_JS_ESCAPE_SAFE:
            out.append(char)
        elif code < 256:
            out.append(f'%{code:02X}')
        else:
            out.append(f'%u{code:04X}')

    return ''.join(out)


def site_base_encode(value: str, how: str) -> str:
    """Готовит значение к подстановке в тело запроса.

    Args:
        value: то, что подставляем, — обычно текст сообщения.
        how: как кодировать. Задаётся сайтом (`presence.send.encode`):

            | Значение | Что делает | Кому |
            |----------|-----------|------|
            | пусто | как есть | кадр сокета, JSON-тело |
            | `form` | обычный form-urlencode | нормальная форма |
            | `js_escape_form` | `escape()`, потом form-urlencode | LadaDate |

    ⚠ **Кодировать обязательно, и это не педантизм.** `site_base_render` — простая
    подстановка: пробел, `&` или `=` в тексте порвали бы тело
    `id={{to}}&body={{text}}`, и сообщение ушло бы обрезанным по первому пробелу
    либо не ушло вовсе.

    ⚠ **Незнакомое имя — как есть, а не отказ.** `presence` правят у соседа прямо
    в базе, и опечатка в ключе не должна останавливать отправку: худшее, что
    случится, — тело уйдёт незакодированным, как было до этой функции.
    """
    how = str(how or '').strip().lower()

    if how == 'form':
        return quote_plus(str(value or ''))

    if how == 'js_escape_form':
        return quote_plus(site_base_js_escape(value))

    return str(value or '')


def site_base_ulid(length: int = 26, time_length: int = 10) -> str:
    """ULID: первые знаки — время в миллисекундах, остальные случайны.

    Живёт здесь, а не в драйвере одного сайта: формат общий и пригодится
    следующему, а вот **нужен ли он** — вопрос к сайту, и отвечает на него драйвер.

    Алфавит — Crockford base32, без I, L, O, U: они путаются с цифрами.
    """
    alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'

    stamp = int(time.time() * 1000)
    head = ''
    for _ in range(time_length):
        head = alphabet[stamp % 32] + head
        stamp //= 32

    tail = ''.join(secrets.choice(alphabet) for _ in range(length - time_length))

    return head + tail
