"""Страницы админ-панели по удерживаемой сессии: HTTP вместо браузера.

Панель агентства отдаёт свои таблицы **готовой разметкой** — это server-rendered
страница, а не приложение на JavaScript. Значит, браузер для чтения не нужен
вовсе: хватит cookie, которые уже держит `dating_panel.py`, и разбора таблицы.

## ⚠ Чем это лучше сценария, и насколько

Раньше каждая таблица снималась прогоном у соседа: вкладка Chromium, вход по
форме, переход на страницу, `extract_rows`. Замерено на «сообщениях из панели»:

| Чем берём | Сколько занимает | Что тратим |
|-----------|------------------|------------|
| сценарий у соседа | ~40 секунд | вкладка Chromium, вход формой на каждый заход |
| ручкой по cookie | **0,2–0,4 секунды** | один GET, 15 КБ разметки |

Круг импорта переписки ходит по настройке сайта — от пяти минут, — и разница
здесь не в удобстве: сорок секунд браузера каждые пять минут означают, что пул
вкладок у соседа занят нами почти всё время.

## ⚠ Свой разбор таблицы, а не библиотека

В образе нет ни `bs4`, ни `lxml`, и тянуть их ради одной таблицы незачем: разбор
здесь — пятьдесят строк на `html.parser` из стандартной библиотеки. Нужна ровно
одна форма ответа: строки `<tbody>`, в каждой ячейки, у ячейки — текст, первая
ссылка и подсказка `title`.

⚠ **Текст ячейки собирается целиком**, вместе с текстом вложенной ссылки: панель
пишет в ячейку «181685 Semen Gorbunkov», где номер — ссылка, а имя рядом. Разбор
на номер и имя — дело вызывающего (`dating_chat_panel.py`), здесь только ячейки.

## ⚠ Сессия — общая с присмотром

Cookie берутся из `dating_site.panel_state`, а живость держит круг панели. Если
панель нас не признала, `dating_panel_fetch` **не входит сама**: вход — дело
круга, и заводить его из читателя значило бы получить два входа наперегонки, когда
за таблицами придут двое.
"""

from html.parser import HTMLParser

import time

import httpx

from logging_.logging_ import logger_info

from http_.http_heartbeat import http_heartbeat_client, http_heartbeat_cookies

from src.dating.dating_json import dating_json_load


class DatingPanelFetchError(RuntimeError):
    """Страница не прочиталась. Текст годен и для журнала, и для ответа ручки."""


# Сколько ждём страницу. Панель отвечает за 0,2–0,4 секунды; дольше десяти ждать
# нечего — круг придёт снова.
DATING_PANEL_FETCH_TIMEOUT = 20.0


async def dating_panel_fetch_html(site_id: int, path: str, params: dict = None) -> str:
    """Страница панели под нашей сессией. Разметка строкой.

    Args:
        site_id: сайт, чью панель читаем.
        path: путь относительно корня панели — `userchatmessages`.
        params: строка запроса словарём.

    Returns:
        str: разметка страницы.

    Raises:
        DatingPanelFetchError: панели нет в настройках, сессии нет, панель увела на
            форму входа или ответила отказом.

    ⚠ **Переадресацию не идём** (`follow_redirects=False`), и это главная проверка:
    панель уводит гостя на форму входа, а вошедшего пускает с `200`. Пойди мы за
    ней, ответом стала бы страница входа — с кодом `200` и без единой строки
    таблицы, то есть «панель отдала пусто» вместо «сессия протухла».
    """
    from src.dating.dating_panel import _row, _url, dating_panel_root

    url = await _url(site_id)
    if not url:
        raise DatingPanelFetchError('у сайта не задан адрес админ-панели')

    # ⚠ Адреса строим от **корня панели**, а не от корня сайта: таблицы лежат
    # рядом с той страницей, что стоит в настройке (`/partner/agency/…`), а не в
    # корне домена. С origin вместо корня запрос уходил бы на
    # `https://ladadate.com/userchatmessages` — и панель честно отвечала `404`.
    base = dating_panel_root(url)
    state = dating_json_load((await _row(site_id)).get('panel_state'))
    cookies = http_heartbeat_cookies(state, base)
    if not cookies:
        raise DatingPanelFetchError('в панель ещё не входили: нет cookie')

    client = http_heartbeat_client()
    started = time.monotonic()
    try:
        answer = await client.get(f"{base}/{str(path or '').lstrip('/')}",
                                  params=params or {}, cookies=cookies,
                                  follow_redirects=False,
                                  timeout=DATING_PANEL_FETCH_TIMEOUT)
    except Exception as e:
        await _note(site_id, base, path, params, 0,
                    f'панель не ответила: {type(e).__name__}: {e}', started)

        raise DatingPanelFetchError(f'панель не ответила: {type(e).__name__}: {e}')
    finally:
        await client.aclose()

    # ⚠ Переадресация здесь — **отказ**, а не удача: панель уводит на форму входа
    # тогда, когда сессию не признала. По коду `302` этого не видно, поэтому исход
    # в журнал пишем разобранным.
    if answer.status_code in (301, 302, 303, 307, 308):
        await _note(site_id, base, path, params, answer.status_code,
                    'панель увела на вход: сессия не признана', started)

        raise DatingPanelFetchError('панель увела на вход: сессия не признана')

    if answer.status_code != 200:
        await _note(site_id, base, path, params, answer.status_code,
                    f'панель ответила {answer.status_code}', started)

        raise DatingPanelFetchError(f'панель ответила {answer.status_code}')

    await _note(site_id, base, path, params, answer.status_code, '', started)

    return answer.text


def dating_panel_fetch_table(html: str) -> list[list[dict]]:
    """Строки первой таблицы разметки: список строк, в строке — ячейки.

    Returns:
        list: `[[{текст, ссылка, подсказка}, …], …]`. `ссылка` — текст первой
        ссылки ячейки, `подсказка` — атрибут `title` первого элемента, у которого
        он есть.

    ⚠ **Берём строки `tbody`, а не все `tr`.** Иначе заголовок таблицы приезжал бы
    первой записью — с текстом «ID», «Date UTC» вместо номера и даты, — и разбор
    честно превращал бы его в сообщение неизвестно от кого.
    """
    parser = _Table()
    try:
        parser.feed(str(html or ''))
    except Exception as e:
        logger_info(f'[dating] таблица панели не разобралась: {e}')

        return []

    return parser.rows


# ── Приватное ────────────────────────────────────────────────────────────────

class _Table(HTMLParser):
    """Разбор первой таблицы страницы: строки `tbody` и ячейки в них.

    ⚠ Состояние — три флажка, и это осознанно проще дерева: нужна одна форма
    ответа, а полный разбор DOM без библиотеки стоил бы вчетверо больше кода ради
    возможностей, которых здесь не спрашивают.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self._in_body = False
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        # В какой вложенной ссылке мы сейчас: её текст идёт и в ячейку, и отдельно.
        self._in_link = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._done:
            return

        if tag == 'tbody':
            self._in_body = True

            return

        if not self._in_body:
            return

        if tag == 'tr':
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = {'текст': '', 'ссылка': '', 'подсказка': ''}
        elif tag == 'a' and self._cell is not None:
            self._in_link = True

        # Подсказку берём у любого тега внутри ячейки: панель вешает её на `<p>`,
        # и полный текст сообщения лежит именно там — в самой ячейке он обрезан.
        if self._cell is not None:
            title = dict(attrs).get('title')
            if title and not self._cell['подсказка']:
                self._cell['подсказка'] = str(title)

    def handle_endtag(self, tag: str) -> None:
        if self._done:
            return

        if tag == 'tbody':
            # Первой таблицей и ограничиваемся: на странице панели ниже бывают
            # свои таблицы разметки, и вторая приехала бы строками без смысла.
            self._in_body = False
            self._done = True
        elif tag == 'a':
            self._in_link = False
        elif tag in ('td', 'th') and self._cell is not None and self._row is not None:
            self._cell['текст'] = ' '.join(self._cell['текст'].split())
            self._cell['ссылка'] = ' '.join(self._cell['ссылка'].split())
            self._row.append(self._cell)
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is None:
            return

        self._cell['текст'] += data
        if self._in_link:
            self._cell['ссылка'] += data


async def _note(site_id: int, base: str, path: str, params: dict, status: int,
                error: str, started: float) -> None:
    """Строка в журнал запросов. Панель — дверь **сайта**, а не анкеты.

    ⚠ Поэтому `profile_id` здесь ноль: сессию панели держит сайт целиком
    (`dating_panel.py`), и приписать поход какой-то одной девушке было бы
    неправдой — в панели видны все.

    ⚠⚠ **Путь пишем от корня сайта, а не от корня панели.** Вызывающие передают
    его относительным (`userchatmessages`), потому что адрес собирается от корня
    панели (`https://ladadate.com/partner/agency`). Запиши мы этот кусок как есть —
    в журнале стоит `/userchatmessages`, а на сайт ушло
    `/partner/agency/userchatmessages`: путь, которого на сайте нет. Человек по
    нему ничего не найдёт и не поймёт, в какую панель мы вообще стучимся.

    Собираем ровно то, что ушло: корень панели плюс путь плюс строка запроса, и
    отрезаем адрес сайта — он один на все строки и в каждой был бы шумом.
    """
    from src.dating.dating_request import (DATING_REQUEST_PANEL, dating_request_ms,
                                           dating_request_write)

    url = httpx.URL(f"{str(base or '').rstrip('/')}/{str(path or '').lstrip('/')}")
    if params:
        url = url.copy_merge_params({key: value for key, value in params.items()
                                     if value not in (None, '')})

    # `raw_path` — путь со строкой запроса и без адреса сайта, ровно как в запросе.
    shown = url.raw_path.decode('utf-8', 'replace')

    await dating_request_write(
        door=DATING_REQUEST_PANEL, profile_id=0, site_id=int(site_id),
        method='GET', path=shown, status=int(status or 0),
        ok=not error, error=error, ms=dating_request_ms(started))
