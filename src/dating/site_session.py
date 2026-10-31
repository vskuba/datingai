"""Запрос к сайту **под сессией анкеты**: её cookie, её выход наружу.

Часть страниц сайта закрыта от чужих, и открывает их не токен API, а обычная
веб-сессия — та самая, которую держит присутствие. Таких мест уже два:

* анкета собеседника (`companion_about.py`) — GET страницы вместо сценария;
* вложение письма (`mail_in_vision.py`) — файл, закрытый от посторонних.

Приём у них общий до последнего довода: разовый клиент, cookie анкеты, её
прокси и общий транспорт. Вторая копия разошлась бы с первой ровно там, где
это дороже всего, — в выходе наружу: одна дверь пошла бы через прокси анкеты,
другая напрямую, и сайт увидел бы два адреса у одной учётки.

## ⚠⚠ Клиент разовый, а транспорт общий — и закрывать его нельзя

Cookie у каждой анкеты свои, поэтому клиент делается под запрос. Пул соединений
при этом один на всех (`http_pool.py`), и `aclose()` унёс бы транспорт у
**всех** анкет разом.

⚠ Общий клиент слоя (`http_heartbeat_client`) сюда не подходит и не по лени: он
собран под **пачку** ударов присутствия одного выхода, живёт без cookie и
требует закрытия вызывающим. Здесь запрос разовый и именно под cookie.

⚠ Представляемся браузером: сайт отдаёт страницу и без этого, но `python-httpx`
в его журнале посещений — примета робота.
"""

import httpx

from http_.http_heartbeat import http_heartbeat_cookies
from http_.http_pool import http_pool_transport

from src.dating.json_column import json_column_load
from src.dating.proxy import proxy_url

# Чем представляемся. ⚠ Одна строка на все двери: разные UA у одной учётки —
# та же примета робота, что и «python-httpx».
SITE_SESSION_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# Сколько ждём ответа по умолчанию, секунд.
SITE_SESSION_TIMEOUT = 15


class DatingSiteSessionError(RuntimeError):
    """Под сессией анкеты не вышло. Текст годен для журнала и страницы."""


async def site_session_get(profile: dict, url: str,
                           timeout: float = SITE_SESSION_TIMEOUT
                           ) -> httpx.Response:
    """GET страницы или файла под сессией анкеты.

    Args:
        profile: строка `profile` — нужны `storage_state` и то, по чему
            считается выход наружу (`proxy.py`).
        url: полный адрес.
        timeout: сколько ждать, секунд.

    Returns:
        httpx.Response: ответ как есть. Код **не проверяется** — что считать
        неудачей, решает вызывающий: у страницы анкеты и у файла это разные
        вопросы, и «пустая страница» для одного законна, а для другого нет.

    Raises:
        DatingSiteSessionError: у анкеты нет живой сессии либо сайт не ответил.

    ⚠ Ответ не разбираем и не режем: одна дверь и на текст, и на байты.
    """
    cookies = http_heartbeat_cookies(
        json_column_load(profile.get('storage_state')), str(url or ''))

    if not cookies:
        raise DatingSiteSessionError(
            'у анкеты нет живой сессии — войдите под ней')

    client = httpx.AsyncClient(transport=http_pool_transport(),
                               cookies=cookies, follow_redirects=True,
                               timeout=timeout,
                               proxy=await proxy_url(profile) or None)

    try:
        return await client.get(str(url),
                                headers={'user-agent': SITE_SESSION_UA})
    except Exception as e:
        raise DatingSiteSessionError(f'сайт не ответил: {e}') from e
