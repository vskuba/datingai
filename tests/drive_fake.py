"""Поддельный Google Диск: готовые ответы вместо живого googleapis.com.

⚠ **Сюита не ходит наружу вообще** (`testing.md` §1.2). У Диска причина та же,
что у сайта знакомств, плюс своя: ходим мы туда **ключом сервисного аккаунта**,
и живой прогон означал бы настоящие запросы настоящими полномочиями к чужим
фотографиям.

Подменяется, как у соседа и у сайта, **транспорт httpx внутри модулей слоя**, а
не функции `google_drive_*`. Разница принципиальная: подменив функции, сюита
проверяла бы собственные заглушки, а сломаться может именно то, что они
скрывают, — обход страниц `pageToken`, сборка путей при обходе вглубь, подмена
размера в ссылке превью, распознавание перенаправления на вход.

⚠ Модулей **два**: запросы живут в `google_drive_.py`, байты — в
`google_drive_bytes.py`. Подмена одного означала бы, что второй ходит наружу.

⚠ **Кэш токена чистится фикстурой.** Он модульный (`_tokens`), и без чистки
второй тест получал бы токен первого — то есть проверка «токен берётся из кэша»
проходила бы и при сломанном кэше.

⚠ **Ключ настоящий, подпись настоящая, Google о нём не знает.** Пара RSA
заводится один раз на прогон: заглушке подпись безразлична, но дверь обязана
собрать JWT по-настоящему — иначе мимо проверки прошёл бы весь путь выдачи
токена.

## Как пользоваться

    async def test_walk_goes_deep(drive):
        drive.add_folder('Dating', 'root-1')
        drive.add_folder('photo', 'f-2', parent='root-1')
        drive.add_file('a.png', 'file-3', parent='f-2')

Готовые положения Диска — полями: `not_shared` (ничего не расшарено),
`split_pages` (ответ двумя страницами), `login_redirect` (превью уводит на
вход), `silent` (500). Что спросили — `calls` и `queries`.
"""

import contextlib
import json
import re

import httpx
import pytest

from google_drive_ import google_drive_ as drive_module
from google_drive_ import google_drive_bytes as bytes_module

# Адрес, который заглушка считает своим превью. Живой Диск отдаёт ссылку с
# суффиксом размера (`=s220`) — тем же видом, иначе подмена размера не
# проверяется вовсе.
DRIVE_FAKE_THUMB = 'https://lh3.test/thumb-{file_id}=s220'

# ⚠ Второй адрес превью — **по номеру файла**, без сохранённой ссылки. Так
# ходят показывающие (`GOOGLE_DRIVE_THUMB_BY_ID`), и заглушка обязана знать оба:
# знай она только первый, прокси вкладки шёл бы на живой googleusercontent.com.
DRIVE_FAKE_THUMB_BY_ID = 'https://lh3.googleusercontent.com/d/'

# Байты за картинку. ⚠ **Настоящий PNG, а не сигнатура с мусором**: разбор
# снимка открывает его Pillow (`ai_vision_normalize`), и подделка валится
# `OSError: Truncated File Read` — отказом, который про заглушку, а не про код.
def _png() -> bytes:
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new('RGB', (16, 16), (120, 140, 160)).save(out, 'PNG')

    return out.getvalue()


DRIVE_FAKE_PNG = _png()

DRIVE_FAKE_FOLDER_MIME = 'application/vnd.google-apps.folder'

# Пара RSA на весь прогон: заводить её на каждый тест — лишние полсекунды.
_key_pem: bytes = b''


class DriveFake:
    """Диск с заранее разложенным деревом.

    Отвечает на те пути, которые дверь действительно спрашивает; на прочие —
    404, чтобы запрос, ушедший не туда, обнаружился сразу.
    """

    def __init__(self):
        # Файлы и папки по номеру: номер → запись в виде ответа Диска.
        self.items: dict[str, dict] = {}
        # Кто чей ребёнок: номер папки → список номеров.
        self.children: dict[str, list[str]] = {}

        # Что спросили: полные адреса и условия `q`.
        self.calls: list[str] = []
        self.queries: list[str] = []

        # Положения Диска.
        self.not_shared = False       # аккаунту не видно ничего
        self.split_pages = False      # ответ двумя страницами
        self.login_redirect = False   # превью уводит на страницу входа
        self.silent = False           # Диск отвечает 500

    def add_folder(self, name: str, file_id: str, parent: str = '') -> str:
        """Папка в дереве; `parent` пусто — лежит в корне чужого Диска."""
        return self._add(name, file_id, DRIVE_FAKE_FOLDER_MIME, parent)

    def add_file(self, name: str, file_id: str, parent: str,
                 mime: str = 'image/png', md5: str = 'md5-по-умолчанию',
                 thumb: bool = True) -> str:
        """Файл в папке. `thumb=False` — свежезалитый, превью ещё не нарисовано."""
        item = self._add(name, file_id, mime, parent)
        self.items[item].update({
            'size': '1024', 'md5Checksum': md5,
            'imageMediaMetadata': {'width': 800, 'height': 600},
        })
        if thumb:
            self.items[item]['thumbnailLink'] = DRIVE_FAKE_THUMB.format(file_id=file_id)
        return item

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Ответ Диска на один запрос — разбор по пути."""
        url = str(request.url)
        self.calls.append(url)

        if url.startswith('https://oauth2.googleapis.com/token'):
            # ⚠ Токен только латиницей: он уезжает в заголовок `Authorization`,
            # а заголовки HTTP не держат ничего, кроме ASCII. Кириллица здесь
            # роняет **все** тесты разом, и роняет невнятно —
            # `UnicodeEncodeError` из недр httpx, без слова «токен».
            return httpx.Response(200, json={'access_token': 'fake-access-token',
                                             'expires_in': 3600})
        if self.silent:
            return httpx.Response(500, text='Backend Error')

        if url.startswith((DRIVE_FAKE_THUMB.split('{')[0], DRIVE_FAKE_THUMB_BY_ID)):
            if self.login_redirect:
                return httpx.Response(302, headers={
                    'location': 'https://accounts.google.com/signin'})
            return httpx.Response(200, content=DRIVE_FAKE_PNG,
                                  headers={'content-type': 'image/png'})

        if '/drive/v3/files' in url and 'alt=media' in url:
            return httpx.Response(200, content=DRIVE_FAKE_PNG,
                                  headers={'content-type': 'image/png'})

        if '/drive/v3/files' in url:
            return self._list(request)

        return httpx.Response(404, json={'error': {'message': f'нет пути: {url}'}})

    def _add(self, name: str, file_id: str, mime: str, parent: str) -> str:
        self.items[file_id] = {'id': file_id, 'name': name, 'mimeType': mime,
                               'createdTime': '2026-01-01T00:00:00Z'}
        self.children.setdefault(parent, []).append(file_id)
        return file_id

    def _list(self, request: httpx.Request) -> httpx.Response:
        """`files.list`: разбираем `q` ровно настолько, насколько его строит дверь."""
        query = request.url.params.get('q', '')
        self.queries.append(query)
        rows = [] if self.not_shared else self._match(query)

        # Вторая страница: отдаём первую запись и обещаем продолжение. Делим по
        # флагу, а не по `pageSize`, — иначе ради проверки обхода пришлось бы
        # заводить в дереве тысячу файлов.
        page = request.url.params.get('pageToken', '')
        if self.split_pages and len(rows) > 1 and not page:
            return _json({'files': rows[:1], 'nextPageToken': 'страница-2'})
        if page:
            rows = rows[1:]

        return _json({'files': rows})

    def _match(self, query: str) -> list[dict]:
        """Записи, подходящие под условие Диска.

        ⚠ Родитель берётся **своим выражением**, а не «первой кавычкой»: в
        условии поиска по имени (`name = 'X' and 'root' in parents`) первой
        идёт имя, и наивный разбор искал бы детей у папки с именем файла.
        """
        parent = re.search(r"'([^']+)'\s+in\s+parents", query)
        if parent:
            found = [self.items[i] for i in self.children.get(parent.group(1), [])]
        else:
            found = list(self.items.values())

        name = re.search(r"name\s*=\s*'([^']*)'", query)
        if name:
            found = [row for row in found if row['name'] == name.group(1)]
        if DRIVE_FAKE_FOLDER_MIME in query:
            found = [row for row in found if row['mimeType'] == DRIVE_FAKE_FOLDER_MIME]

        return [dict(row) for row in found]


@pytest.fixture(autouse=True)
def drive(monkeypatch, tmp_path) -> DriveFake:
    """Поддельный Диск и поддельный ключ. **Всегда**, а не по запросу.

    ⚠⚠ `autouse` здесь запрет, а не удобство: забывший заглушку тест пошёл бы на
    живой Диск настоящим ключом сервисного аккаунта. Тот же урок, что у
    `site_api_fake`: там за забывчивость сюиты сайт закрыл нам адрес целиком.

    Тестам, которые просят `drive` по имени, достаётся **тот же** объект.
    """
    fake = DriveFake()

    key = tmp_path / 'drive_key.json'
    key.write_text(json.dumps({
        'type': 'service_account',
        'client_email': 'fake@fake.iam.gserviceaccount.com',
        'private_key': _key_get(),
    }), encoding='utf-8')

    monkeypatch.setattr(drive_module, 'http_pool_client', _pool(fake))
    monkeypatch.setattr(bytes_module, 'http_pool_client', _pool(fake))
    monkeypatch.setattr(drive_module, 'config_get',
                        lambda name, default='': (str(key) if name == 'GOOGLE_DRIVE_KEY'
                                                  else default))
    # ⚠ Кэш токена модульный: без чистки второй тест получил бы токен первого, и
    # проверка «токен берётся из кэша» зеленела бы при сломанном кэше.
    monkeypatch.setattr(drive_module, '_tokens', {})

    return fake


def _pool(fake: DriveFake):
    """Подставной `http_pool_client`: тот же клиент, но транспорт — заглушка.

    ⚠ С переезда общего слоя (`http_`) модуль ходит не своим `httpx`, а этой
    ручкой, и подменять надо **её**: старая подмена атрибута `httpx` молчала бы
    промахом — атрибута в модуле уже нет, а тест ушёл бы на живой Диск.
    Заглушка транспорта — не «ещё один клиент»: общий пул тут как раз лишний,
    тесту нужен ответ из руки, а не соединение.
    """

    @contextlib.asynccontextmanager
    async def client(*, timeout: float = 10, proxy: str = '', **kw):
        kw.pop('transport', None)
        kw.pop('follow_redirects', None)

        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.answer),
                                     timeout=timeout, **kw) as one:
            yield one

    return client


def _json(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload,
                          headers={'content-type': 'application/json'})


def _key_get() -> str:
    """PEM приватного ключа: один на прогон, Google о нём не знает."""
    global _key_pem

    if not _key_pem:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        pair = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _key_pem = pair.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption())

    return _key_pem.decode()
