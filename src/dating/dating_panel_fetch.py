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
    try:
        answer = await client.get(f"{base}/{str(path or '').lstrip('/')}",
                                  params=params or {}, cookies=cookies,
                                  follow_redirects=False,
                                  timeout=DATING_PANEL_FETCH_TIMEOUT)
    except Exception as e:
        raise DatingPanelFetchError(f'панель не ответила: {type(e).__name__}: {e}')
    finally:
        await client.aclose()

    if answer.status_code in (301, 302, 303, 307, 308):
        raise DatingPanelFetchError('панель увела на вход: сессия не признана')

    if answer.status_code != 200:
        raise DatingPanelFetchError(f'панель ответила {answer.status_code}')

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
