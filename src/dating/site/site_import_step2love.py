"""Step2Love: переписка не страницей, а списком дел — страница «Задачи».

У агентской панели `agency.step2love.com` нет страницы-журнала на манер
`userchatmessages`: её нет вовсе, и запрос к ней отвечает `404` при совершенно
живой панели (проверено журналом «Запросы»: сотня 404 за день при живых страницах
руками). Журнал здесь разложен по-другому: страница `tasks.php` перечисляет дела
агентства, и под двумя пунктами лежат **фильтрованные списки писем** —

    Непрочитанные сообщения   → /mailbox.php?date_from=…&filter=9
    Неотвеченные сообщения    → /mailbox.php?date_from=…&filter=2
    Пропущенные запросы чата  → /chat/?…&chatfilter=3&com=detailed

Даты панель этого сайта понимает только как `гггг-мм-дд`; `дд.мм.гггг` она молча
подменяет своим диапазоном (тот же закон, что у Goldenbride с ISO).

## Почему чата здесь нет

Третья ссылка ведёт не в переписку, а в **статистику чат-сессий**: её таблица —
`Создан | Завершен | Мужчина | Пригласил | Девушка | Состояние` — без единого
текста сообщения. Сами сообщения чата Step2Love живут в сокет-подсистеме
(`chat.step2love.com`), в панельный журнал этой панели не ложатся и приходят
дорогой сокета/API. Читать из «Задач» стало бы письмами; чужие строки под видом
переписки — враньё, и его не прячут в драйвере.

## ⚠ Формат живой строки писем не сверен

На день написания переписки в агентстве нет ни одной (все счётчики — нули, и
страницы говорят «No messages found» — проверено 2026.10.27 через читалку панели
по всем фильтрам за месяц), поэтому разбор собран **по форме фильтра и по
разряду содержимого** и бережлив: строка, из которой не удалось вытащить
номер и дату, — не строка переписки. Пустой список панель называет словами
(`NO_MESSAGES_MARK`) — это правда; а если строк полно, но разобрать нельзя ни
одной, — отказ с причиной, а не тихая пустота: запись пустоты вместо непонятой
страницы обнулила бы доверие к импорту целиком. Первый живой заход обязан быть
сверен глазами с той же страницей панели.

⚠ **Строки страницы — не таблица.** На `mailbox.php` первая таблица разметки —
блок `Your ID-number`, а `panel_fetch_table` читает только первую, до писем он
там не доходит вовсе; сами письма панель кладёт блоками
`div class="mailbox-row"` (увидено при осмотре живой страницы). Их читает свой
разборщик ниже — с той же формой ячейки, что отдаёт табличный, чтобы общий
`_letters` не различал дорог.
"""

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from src.dating.panel_fetch import DatingPanelFetchError, panel_fetch_html
from src.dating.site.site_import import DatingSiteImport

# Пункты страницы задач, которые и есть переписка: подпись → фильтр mailbox'а.
# Порядок и есть порядок заходов: непрочитанное важнее, между списками возможные
# повторы снимает номер строки.
TASK_FILTERS = (('Непрочитанные сообщения', '9'),
                ('Неотвеченные сообщения', '2'))

# Слова пустоты. Панель говорит их прямо; пока они на месте — пустой список
# правда пуст, а не потерялся в разборе.
NO_MESSAGES_MARK = 'No messages found'

# Номер строки: первая ячейка, состоящая из одних цифр.
_NUMBER_RE = re.compile(r'^\d+$')

# Дата панели: `гггг-мм-дд` с временем или без; `дд.мм.гггг` — на всякий случай.
_WHEN_RE = re.compile(r'\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}')


class DatingSiteImportStep2Love(DatingSiteImport):
    """Задачи → два фильтра писем; чат-сессии не читаем (см. заголовок файла)."""

    name = 'step2love'

    TASKS_PATH = 'tasks.php'

    def date_fmt(self) -> str:
        return '%Y-%m-%d'

    def has_journal(self) -> bool:
        return True

    async def journal_rows(self, site_id: int, profiles: dict, date_from: str,
                           date_to: str) -> tuple[int, list[dict]]:
        total, rows, seen = 0, [], set()

        tasks = await panel_fetch_html(site_id, self.TASKS_PATH, {})

        for title, filt in TASK_FILTERS:
            link = self._task_link(tasks, filt)
            if not link:
                # Пункта нет: список задач панель меняет без нас. Молчать нельзя —
                # «не забрали ничего» должно звучать причиной, а не нулём.
                raise DatingPanelFetchError(
                    f'на странице задач панели нет пункта «{title}»')

            html = await panel_fetch_html(site_id, link[0],
                                         self._window(link[1], date_from, date_to))
            table = self._mailbox_rows(html)
            total += len(table)

            if not table:
                if NO_MESSAGES_MARK not in html:
                    raise DatingPanelFetchError(
                        f'«{title}»: ни строк, ни слов пустоты — панель '
                        f'ответила непонятной страницей')
                continue

            parsed = self._letters(table)
            if not parsed and table:
                raise DatingPanelFetchError(
                    f'«{title}»: строк {len(table)}, разобрать не удалось ни '
                    'одну — формат строк сверяют руками')

            for row in parsed:
                if row['number'] in seen:
                    continue
                seen.add(row['number'])
                rows.append(row)

        return total, rows

    @staticmethod
    def _mailbox_rows(html: str) -> list[list[dict]]:
        """Письма страницы — ячейками, как их отдаёт табличный читатель.

        Строка-«No messages found» — не письмо, её убираем: пустой список
        панель уже назвала словами, и `journal_rows` обязан увидеть честный
        ноль, а не строку, которую нельзя разобрать.
        """
        parser = _Mailbox()

        try:
            parser.feed(str(html or ''))
        except Exception as e:
            # Разметка, на которой падает сам разборщик, — не повод возвращать
            # половину: отказ с причиной честнее молчаливого недобора.
            raise DatingPanelFetchError(f'письма панели не разобрались: {e}')

        return [row for row in parser.rows
                if not any(NO_MESSAGES_MARK in str(c.get('text') or '') for c in row)]

    @staticmethod
    def _task_link(tasks: str, filt: str) -> tuple[str, dict] | None:
        """Ссылка пункта задач: (`путь`, `параметры`).

        Берём **ссылку панели**, а не собираем адрес руками: панель сама решает,
        что за `mailbox.php` стоит за пунктом, и переименование страницы уводит
        нас за собой, а не в отказ.
        """
        for href in re.findall(r'href=["\']([^"\']+)["\']', tasks):
            if f'filter={filt}' not in href or 'mailbox' not in href:
                continue

            url = urlparse(href)

            return url.path.lstrip('/'), {k: v[0] for k, v in parse_qs(url.query).items()}

        return None

    @staticmethod
    def _window(params: dict, date_from: str, date_to: str) -> dict:
        """Наш диапазон вместо панелиного окна ссылки.

        ⚠ Ссылка «Задач» несёт своё окно (тридцать дней назад от сегодня); круг
        ходит от пяти минут и просит сутки. Спрашивать окно панели значило бы
        возить одно и то же по сотне раз за день.
        """
        params = dict(params)
        params['date_from'] = date_from
        params['date_to'] = date_to

        return params

    @staticmethod
    def _letters(table: list) -> list[dict]:
        """Строки писем — в запись того же вида, что отдаёт сценарий.

        Столбцы панель подписывает `ID | Name | Age | City …`, но порядок колонок
        её таблицы — не обещание, поэтому разбор **по содержимому**, а не по
        номерам столбцов: номер — первая целочисленная ячейка, дата — первая
        похожая на дату, стороны — ячейки со ссылками (номер — в ссылке, имя —
        текстом рядом).

        ⚠ Сторона **одна** в строке (панель показывает письма, адресованные
        девушке, и подписывает её) — становится получателем: письмо адресовано
        ей, и входящим оно или исходящим решит `chat_panel_rows` по нашим
        анкетам, как решают обе прежние дороги.
        """
        out = []
        for row in table:
            number = when = ''
            parties, texts = [], []

            for cell in row:
                text, link = str(cell.get('text') or '').strip(), \
                    str(cell.get('link') or '').strip()

                if not number and _NUMBER_RE.match(text or link):
                    number = text or link
                elif not when and _WHEN_RE.search(text):
                    when = text
                elif link and re.search(r'\d', link):
                    parties.append({'text': text, 'link': link,
                                    'title': str(cell.get('title') or '')})
                elif text:
                    texts.append({'text': text,
                                  'title': str(cell.get('title') or '')})

            if not number or not when:
                continue

            one = {'number': number, 'when': when}

            if len(parties) >= 2:
                one['from'], one['from_uid'] = parties[0]['text'], parties[0]['link']
                one['to'], one['to_uid'] = parties[1]['text'], parties[1]['link']
            elif parties:
                one['to'], one['to_uid'] = parties[0]['text'], parties[0]['link']

            # Текст — самое длинное из того, что не номер, не дата и не сторона:
            # панель обрезает ячейку шириной колонки, а полное письмо кладёт в
            # подсказку `title` — тот же закон, что у Goldenbride.
            body = max(texts, key=lambda one_t: len(one_t['title'] or one_t['text']),
                       default=None)
            if body:
                one['text'] = body['text']
                one['text_title'] = body['title']

            out.append(one)

        return out


class _Mailbox(HTMLParser):
    """Письма `mailbox.php`: содержимое каждого блока `mailbox-row`.

    ⚠ Табличный читатель до них не доходит: первой таблицей на странице стоит
    блок `Your ID-number`, им он и ограничивается, а письма — div-блоки. Форма
    ячейки та же, что у `panel_fetch_table` (`text`/`link`/`title`), — общий
    `_letters` не должен дорогой различать таблицу и блоки.

    ⚠ Состояние — счётчик глубины да два накопителя, и это осознанно проще
    дерева: нужны строки да ссылки в порядке чтения, полный DOM без библиотеки
    стоил бы вчетверо больше кода ради возможностей, которых здесь не спрашивают.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self._cells: list[dict] | None = None   # None — вне блока письма
        self._depth = 0                         # вложенность div'ов внутри блока
        self._buf = ''                          # текст между ссылками
        self._pending_title = ''                # подсказка обёртки этого текста
        self._link: dict | None = None          # ссылка, которую сейчас читаем

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)

        if tag == 'div':
            if self._cells is None:
                if 'mailbox-row' in str(a.get('class') or ''):
                    self._cells, self._depth = [], 0
                    self._buf = self._pending_title = ''
                    self._link = None
                return

            self._depth += 1

        if self._cells is None:
            return

        if tag == 'a':
            self._flush()
            self._link = {'text': '', 'link': str(a.get('href') or ''),
                          'title': str(a.get('title') or '')}

            return

        # Граница лоскута — и начало тега тоже: иначе подпись, у которой слева
        # ещё не закрытый текст, потеряла бы свою подсказку.
        if self._link is None:
            self._flush()

        hint = str(a.get('title') or '')

        if hint:
            if self._link is not None:
                self._link['title'] = self._link['title'] or hint
            else:
                # Подсказка на элементе-обёртке относится к тексту целиком:
                # панель кладёт туда полное письмо, срезанное шириной блока.
                self._pending_title = hint

    def handle_data(self, data: str) -> None:
        if self._cells is not None:
            if self._link is not None:
                self._link['text'] += data
            else:
                self._buf += data

    def handle_endtag(self, tag: str) -> None:
        if self._cells is None:
            return

        if tag == 'a' and self._link is not None:
            self._link['text'] = self._link['text'].strip()
            self._cells.append(self._link)
            self._link = None

            return

        if tag == 'div' and not self._depth:
            self._flush()
            if any(str(c.get('text') or '') or c.get('link') for c in self._cells):
                self.rows.append(self._cells)
            self._cells = None

            return

        if tag == 'div':
            self._depth -= 1

        # Конец любого тега внутри строки — граница ячейки: панель складывает
        # номер, дату, имена вразноброс по inline-элементам, и без разбивки по
        # ним `1041` из `<b>` приехал бы одним лоскутом с подписью письма, а
        # разборщик номера перестал бы видеть «ячейку из одних цифр».
        if self._link is None:
            self._flush()

    def _flush(self) -> None:
        """Лоскут текста между границами — отдельной ячейкой, подсказкой — целиком."""
        text = self._buf.strip()

        if text:
            self._cells.append({'text': text, 'link': '',
                                'title': self._pending_title})
        self._buf = ''
        self._pending_title = ''
