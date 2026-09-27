"""Поддельная админ-панель агентства: готовые страницы вместо живого ladadate.com.

⚠⚠ **Сюита наружу не ходит** (`testing.md`), и у панели причин больше, чем у сайта:
форма ответа — **необратимое действие**. В той же форме живёт вторая кнопка,
`buttonIgnore`, которой панель закрывает письмо без ответа; одна неосторожная сюита
означала бы чужую переписку, закрытую навсегда.

Подменяется, как у сайта и у соседа, **транспорт внутри модуля** (`panel_fetch`), а
не его функции. Так под проверкой остаётся всё, ради чего модуль написан: как
собирается адрес от корня панели, какие cookie уезжают, что считается протухшей
сессией, чем отличается переадресация при чтении от неё же при отправке.

⚠ Застава общего слоя предлагает здесь `http_pool_transport` и
`http_heartbeat_client` — оба не подходят, и стоит сказать почему. Первый даёт
**настоящий** транспорт для живых соединений (в нём весь смысл — переиспользование
пула), а нам нужен `httpx.MockTransport`, который наружу не ходит вовсе. Второй —
та самая функция, которую заглушка и подменяет.

## Как пользоваться

    async def test_reply_goes(panel):
        panel.open(826504459)

        await mail_reply_panel_send(SITE, 826504459, 'привет')

        assert panel.sent[0]['ToBody'] == 'привет'

Положения панели — полями: `messages` (какие письма открыты для ответа), `expired`
(сессия не признана), `broken` (панель отвечает отказом), `redirect_on_post`
(отвечать на форму `302`, как живая). Что ушло — `sent`; что спрашивали — `calls`.

⚠ **Форма никуда не отправляется.** Она копится в `sent`, и это главное, ради чего
заглушка нужна: живая панель на `POST /usermessage` пишет живому человеку.
"""

import httpx
import pytest

# ⚠ Псевдоним обязателен: фикстура в этом же файле зовётся `panel`, и без него
# `def panel(...)` затирает модуль — та же грабля, что у `site_api_fake`.
from src.dating import panel_fetch as panel_fetch_module

# Адрес поддельной панели. ⚠ Домен тот же, что у `test_panel.py`: cookie отбираются
# по домену второго уровня, и сессия с чужого хоста до запроса не доедет.
PANEL_FAKE_URL = 'https://panel.test/partner/agency/profile'
PANEL_FAKE_ROOT = 'https://panel.test/partner/agency'

# Состояние браузера с cookie нужного хоста — то, что кладёт вход сценарием.
PANEL_FAKE_STATE = ('{"cookies": [{"name": "SID", "value": "s1",'
                    ' "domain": "panel.test"}], "origins": []}')

# ⚠⚠⚠ **Контракт формы, снятый с живой панели машинно** (26.09.2026, письмо
# 826504459). Не «примерно так выглядит», а ровно то, что отдаёт Ladadate: адрес,
# метод, кодировка и все пять полей по порядку.
#
# Ради него и заведена эта константа: поддельная страница ниже написана руками, и
# рукописная заглушка расходится с живой страницей молча. Сторож
# (`test_mail_reply_panel.py`) разбирает поддельную страницу тем же разбором и
# сличает с этим словарём — то есть заглушка **приколота** к панели, а не похожа на
# неё.
#
# ⚠ `uploadFile` здесь есть, а мы его **не отправляем**: браузер послал бы пустую
# часть файла, мы не посылаем ничего. Проверить это локально нечем — живая панель
# либо примет, либо нет; записано, чтобы при отказе смотрели сюда первым делом.
PANEL_FAKE_CONTRACT = {
    'action': '/partner/agency/usermessage',
    'method': 'post',
    'enctype': 'multipart/form-data',
    'fields': [
        {'tag': 'textarea', 'name': 'ToBody', 'type': None, 'value': None},
        {'tag': 'input', 'name': 'MessageId', 'type': 'hidden', 'value': 'НОМЕР'},
        {'tag': 'input', 'name': 'uploadFile', 'type': 'file', 'value': None},
        {'tag': 'input', 'name': 'buttonIgnore', 'type': 'submit',
         'value': 'Ignore Message'},
        {'tag': 'button', 'name': 'buttonReply', 'type': 'submit',
         'value': 'Reply'},
    ],
}

# Куда панель принимает форму. ⚠ Путь **от корня сайта**, как в `action` живой формы:
# заглушка отвечает отказом на любой другой, иначе промах в адресе прошёл бы молча.
PANEL_FAKE_POST_PATH = '/partner/agency/usermessage'

# Страница письма с **открытой** формой ответа. Разметка снята с живой панели
# 26.09.2026 и обрезана до того, что читает код: скрытый номер, поле текста и обе
# кнопки.
#
# ⚠⚠ `buttonIgnore` здесь **намеренно**: без него сторож «мы не жмём Ignore» проверял
# бы отсутствие того, чего и на странице нет, то есть ничего.
PANEL_FAKE_OPEN = """
<h5>Message to 599939 Elena</h5>
<form action="/partner/agency/usermessagehistory/599939">
  <input type="hidden" name="id2" value="549126" />
  <input class="btn btn-primary" type="submit" value="History" />
</form>
<form action="/partner/agency/usermessage" method="post"
      enctype="multipart/form-data">
  <textarea class="form-control" id="ToBody" name="ToBody" rows="7"></textarea>
  <input name="MessageId" id="MessageId" type="hidden" value="{message_id}" />
  <input class="custom-file-input" name="uploadFile" id="uploadFile" type="file" />
  <input name="buttonIgnore" class="btn" type="submit" value="Ignore Message" />
  <button name="buttonReply" class="btn btn-primary" type="submit"
          value="Reply">Reply</button>
</form>
"""

# Страница письма, на которое ответить уже нельзя: панель считает его отвеченным.
# ⚠ Отличается **отсутствием формы**, а не словом «отвечено»: именно так и выглядит
# живая страница — проверено на письмах от 23.09 и 09.09.
PANEL_FAKE_CLOSED = """
<h5>Message to 622715 Ekaterina</h5>
<p>Hello my dear Ekaterina, first of all thank you for your last photo.</p>
"""

# Страница входа: панель отдаёт её тем же кодом 200, что и кабинет.
PANEL_FAKE_LOGIN = '<form id="login"><input name="Login" /></form>'


class PanelFake:
    """Панель на столе: какие письма открыты, что ушло, о чём спрашивали."""

    def __init__(self):
        # Номер письма (строкой) → открыта ли у него форма ответа.
        self.messages = {}
        # Отправленные формы — словарями полей, по порядку.
        self.sent = []
        # Куда ходили: `(метод, путь)`.
        self.calls = []
        # Каким `Content-Type` уходила форма — по одному на отправку.
        self.kinds = []
        # Сессия не признана: панель уводит на вход.
        self.expired = False
        # Панель отвечает отказом: код, который она вернёт.
        self.broken = 0
        # Отвечать на форму `302`, как живая.
        self.redirect_on_post = False
        # Закрывать ли письмо после удачной отправки. ⚠ По умолчанию да: живая
        # панель именно так и подтверждает — формы больше нет.
        self.close_on_reply = True

    def open(self, message_id, body: str = 'his letter') -> None:
        """Письмо, на которое можно ответить."""
        self.messages[str(message_id)] = {'open': True, 'body': body}

    def closed(self, message_id) -> None:
        """Письмо, на которое панель отвечать уже не даёт."""
        self.messages[str(message_id)] = {'open': False, 'body': 'his letter'}

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Ответ панели на запрос. ⚠ Разбирает **путь целиком**, как живая."""
        path = request.url.path
        self.calls.append((request.method, path))

        if self.broken:
            return httpx.Response(self.broken, text='panel is down')

        if self.expired:
            # Живая панель уводит на форму входа переадресацией.
            return httpx.Response(302, headers={'Location': '/partner/signin'})

        if request.method == 'POST':
            return self._post(request, path)

        return self._get(path)

    # ── Приватное ────────────────────────────────────────────────────────────

    def _get(self, path: str) -> httpx.Response:
        """Страница письма по номеру из пути."""
        if '/usermessage/' not in path:
            return httpx.Response(200, text=PANEL_FAKE_LOGIN)

        one = path.rsplit('/', 1)[-1]
        known = self.messages.get(one)

        # Незнакомый номер и закрытое письмо панель отдаёт одинаково: страницей без
        # формы. Отдельного «нет такого письма» у неё нет.
        if known is None or not known['open']:
            return httpx.Response(200, text=PANEL_FAKE_CLOSED)

        return httpx.Response(200, text=PANEL_FAKE_OPEN.format(message_id=one))

    def _post(self, request: httpx.Request, path: str) -> httpx.Response:
        """Отправленная форма: разбираем поля и запоминаем.

        ⚠⚠ Адрес сверяется с `action` живой формы. Прежде заглушка принимала форму по
        любому пути — и промах в адресе прошёл бы молча: тест зелёный, а панель на
        проде отвечает `404`. Ровно то расхождение, из-за которого контрактные
        проверки и пишут.
        """
        if path != PANEL_FAKE_POST_PATH:
            return httpx.Response(404, text=f'panel has no {path}')

        # Кодировку запоминаем как есть: форма панели объявлена `multipart`, и
        # отправка строкой полей — расхождение, которое видно только здесь.
        self.kinds.append(request.headers.get('content-type', ''))
        fields = _multipart(request)
        self.sent.append(fields)

        one = str(fields.get('MessageId') or '')
        if self.close_on_reply and one in self.messages:
            self.messages[one]['open'] = False

        if self.redirect_on_post:
            return httpx.Response(
                302, headers={'Location': '/partner/agency/usermessages'})

        return httpx.Response(200, text='<h5>Message sent</h5>')


@pytest.fixture
def panel(monkeypatch) -> PanelFake:
    """Поддельная панель под чтение и отправку.

    ⚠ Подменяется **клиент**, который берёт `panel_fetch` (`http_heartbeat_client`),
    а не сам `panel_fetch_html`: под проверкой остаётся сборка адреса от корня
    панели, отбор cookie по домену и разбор исхода по коду ответа.

    ⚠⚠ `follow_redirects=True` у поддельного клиента — **нарочно**, как у живого:
    переадресацию гасит сам вызов (`follow_redirects=False` в аргументах запроса), и
    проверка «читаем — отказ, отправляем — терпим» проверяет именно это, а не
    настройку клиента.
    """
    fake = PanelFake()

    def client(proxy: str = ''):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.answer),
                                 follow_redirects=True)

    monkeypatch.setattr(panel_fetch_module, 'http_heartbeat_client', client)

    return fake


def _multipart(request: httpx.Request) -> dict:
    """Поля многочастной формы — «имя → значение».

    ⚠ Разбор простой, но не наивный: граница `multipart` своя у каждого запроса, и
    ищем мы её в заголовке, а не по виду тела. Заглушке хватает текстовых полей —
    файлов форма ответа не несёт.

    ⚠ Строку полей (`application/x-www-form-urlencoded`) разбираем тоже: так видно,
    если код однажды перестанет отправлять многочастную форму, — а форма панели
    объявлена `multipart`, и отправлять иначе значит надеяться на чужой сервер.

    ⚠⚠ Пустую часть файла (`uploadFile` без имени и тела) пропускаем: её посылает и
    браузер, и она же включает многочастную кодировку. В `sent` ей места нет — иначе
    сторож «в форму не уехало лишнего» спорил бы с тем, что уехало нарочно.
    """
    kind = request.headers.get('content-type', '')
    if 'multipart/form-data' not in kind:
        from urllib.parse import parse_qsl

        return dict(parse_qsl(request.content.decode('utf-8', 'replace')))

    edge = kind.split('boundary=')[-1].strip('"')
    out = {}
    for part in request.content.decode('utf-8', 'replace').split(f'--{edge}'):
        if 'name="' not in part:
            continue

        name = part.split('name="', 1)[1].split('"', 1)[0]
        _, _, value = part.partition('\r\n\r\n')
        value = value.rstrip('\r\n-')

        # Пустая часть файла — не поле формы: она нужна кодировке, а не панели.
        if name == 'uploadFile' and not value:
            continue

        out[name] = value

    return out
