"""Step2Love: ленты «Все онлайн» и «Гости» — та же сетка `gal_*`, что у всего сайта.

Адреса — из сценариев, которые лента ходила до сих пор (537 «анкеты мужчин:
список (Step2Love)», 536 «гости: список»):

    онлайн, 1-я страница   https://mystep2love.com/online/
    онлайн, 2..10          https://mystep2love.com/ladies/online/page2/?&m_read=&type_in=&folder=&favorite=
    гости                  https://mystep2love.com/guests/

⚠ Продолжения онлайна — путь `ladies/online/pageN`, а не `/online/pageN`:
первая страница и дальние лежат по разным адресам, сценарий это знал шагами
`men`, `men2`…`men10`, здесь те же адреса собраны в одну формулу.

## Строки: одна сетка, двойные карточки

Разбор — сетка `td`, в которой есть `p.gal_id` (то же, что селектор
`td:has(p.gal_id)` сценария), и поля те же: `number`, `name`, `link`, `photo`,
`status_class`. Как и на сценарном съёме, **каждый человек лежит в сетке
дважды**: в своей ячейке он с фотографией, в соседней — со значком статуса, и
карточки из этих строк склеивает `people_rows`, как склеивал всегда. Порядок
строк при этом не случайность, а правило: копия с настоящей фотографией идёт
раньше копии со значком — разбор ниже сортирует их именно так, иначе первой
оказывалась бы карточка с `online.png` вместо портрета.

## Мёртвая сессия

Нас либо перенаправляет на вход (базовый `_fetch` видит это по сменившемуся
пути), либо страница приходит с формой входа на месте списка. Признаки формы
— ниже; их стоит сверить с живым снятием, но пока сработает и грубый сторож:
ни одной строки на первой странице без признаков входа — тоже отказ, а не
пустая лента.
"""

import re
from html import unescape
from html.parser import HTMLParser

from src.dating.guests import GUESTS_FIELD
from src.dating.site.site_feed import DatingSiteFeed, DatingSiteFeedError
from src.dating.site.site_guests_step2love import (GUESTS_TEXT_FIELD,
                                                   DatingSiteGuestsStep2Love)

# Адреса лент (см. заголовок).
ONLINE_PATH = '/online/'
ONLINE_PAGE_PATH = '/ladies/online/page{n}/?&m_read=&type_in=&folder=&favorite='
GUESTS_PATH = '/guests/'

# Чем похожа страница на форму входа. ⚠ Грубый сторож по мотивам движка:
# goldenbride-вход прячется в форме с этими полями. Достоверного признака
# в нашем коде не было вовсе (ленты вход не встречали), так что заводить его
# стоит по живому снятию страницы входа — и держать здесь, а не в развилках.
LOGIN_MARKS = ('name="login_lg"', 'name="user_name"')

# Чем фотография отличается от заглушки: дефолтный портрет и значок статуса —
# не фото, а лишь бы что со страницы.
PHOTO_STUB = 'default_1.gif'
PHOTO_ICON = re.compile(r'(online|offline)[a-z_]*\.png$', re.I)

# Что остаётся от страницы, когда с неё содраны теги: текст, по которому
# разбор гостей (`site_guests_step2love`) добирает даты визитов и возраст —
# ровно тот же `guests_text`, что раньше давал шаг `read_text` сценария.
_TAG_RE = re.compile(r'<(script|style)[^>]*>.*?</\1>|<[^>]*>', re.I | re.S)


class DatingSiteFeedStep2Love(DatingSiteFeed):
    """Step2Love: ленты читаются GET'ом под cookie анкеты, сценарий не нужен."""

    name = 'step2love'

    def by_http(self) -> bool:
        return True

    def online_url(self, page_no: int) -> str:
        base = self._base()

        return base + (ONLINE_PATH if page_no == 1
                       else ONLINE_PAGE_PATH.format(n=page_no))

    def guests_url(self) -> str:
        return self._base() + GUESTS_PATH

    def login_page(self, html: str) -> bool:
        return any(mark in html for mark in LOGIN_MARKS)

    def page_rows(self, html: str) -> list[dict]:
        """Сетка страницы → строки; копии с фото — раньше копий со значком."""
        rows = _Grid().parse(html)

        rows.sort(key=lambda one: _photo_rank(one['photo']))

        return rows

    def guests_cards(self, rows: list[dict], html: str) -> list[dict]:
        """Карточки гостей — тем же разбором, что у сценарного съёма: строки
        плюс текст страницы, из него берутся дата визита и возраст."""
        return DatingSiteGuestsStep2Love(self.presence).rows({
            GUESTS_FIELD: rows, GUESTS_TEXT_FIELD: _page_text(html)})

    # ── Приватное ────────────────────────────────────────────────────────────

    def _base(self) -> str:
        base = self.site_base()
        if not base:
            raise DatingSiteFeedError('у сайта не задан адрес (presence.base)')

        return base


def _photo_rank(photo: str) -> int:
    """Настоящее фото — 0, заглушка или значок — 1. Сортировка устойчива,
    поэтому внутри категории порядок страницы не трогается."""
    one = str(photo or '')

    return 1 if (not one or PHOTO_STUB in one or PHOTO_ICON.search(one)) else 0


def _page_text(html: str) -> str:
    """HTML → одна строка текста: теги в пробелы, сущности раскрыты."""
    return ' '.join(unescape(_TAG_RE.sub(' ', html)).split())


class _Grid(HTMLParser):
    """Ячейки `td`, в которых есть `p.gal_id`, — теми самыми строками extraction.

    ⚠ Поля собираются **по ячейке**, а не по всей странице: человек лежит в
    двух ячейках сразу, и разбор всей страницы скопировал бы номер первой
    ячейки во вторую, где у него своё фото. Тот же смысл, что у селектора
    `td:has(p.gal_id)` сценария.
    """

    _FIELDS = ('link', 'name', 'photo', 'number', 'status_class')

    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.rows: list[dict] = []
        self._td: dict | None = None
        self._p_class = ''
        self._in_p = False
        self._in_link = False

    def parse(self, html: str) -> list[dict]:
        self.feed(html)
        self.close()

        return self.rows

    # ── Приватное ────────────────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs) -> None:
        one = dict(attrs)

        if tag == 'td':
            self._td = {name: '' for name in self._FIELDS}
            self._p_class = ''
            self._in_p = False
            self._in_link = False

            return

        if self._td is None:
            return

        if tag == 'p':
            self._p_class = str(one.get('class') or '')
            self._in_p = True
        elif tag == 'a':
            # Ссылка карточки — только из `p.gal_name`, как брал сценарий:
            # в ячейке гостей и прочего их бывает несколько.
            if 'gal_name' in self._p_class and not self._td['link']:
                self._td['link'] = str(one.get('href') or '')
                self._in_link = True
        elif tag == 'img':
            src = str(one.get('src') or '')

            if src and not self._td['photo']:
                self._td['photo'] = src
            # Значок статуса: картинка внутри абзаца, кончается на .png —
            # тот же отбор, что у селектора `p img[src$=".png"]`. Класс у
            # абзаца спрашивать нельзя: на живой странице значок лежит в `p`
            # вовсе без класса.
            if (src.lower().endswith('.png') and self._in_p
                    and not self._td['status_class']):
                self._td['status_class'] = src

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._td is None:
            return

        if self._in_link and not self._td['name']:
            self._td['name'] = data.strip()
        elif 'gal_id' in self._p_class and not self._td['number']:
            self._td['number'] = data.strip()

    def handle_endtag(self, tag: str) -> None:
        if self._td is None:
            return

        if tag == 'p':
            self._p_class = ''
            self._in_p = False
        elif tag == 'a':
            self._in_link = False
        elif tag == 'td':
            # Ячейка счёт только по наличию `p.gal_id` — признаку карточки.
            if self._td['number']:
                self.rows.append(self._td)
            self._td = None
