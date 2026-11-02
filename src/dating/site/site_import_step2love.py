"""Step2Love: переписка двумя источниками — история чата и списки писем.

У агентской панели `agency.step2love.com` нет страницы-журнала на манер
`userchatmessages`: её нет вовсе, и запрос к ней отвечает `404` при совершенно
живой панели (проверено журналом «Запросы»: сотня 404 за день при живых страницах
руками). Журнал здесь разложен по-другому, и источников два:

    Переписка → История чата   → /chat/?…&chatfilter=N&com=detailed  ← реплики
    Непрочитанные сообщения    → /mailbox.php?date_from=…&filter=9    ← письма
    Неотвеченные сообщения     → /mailbox.php?date_from=…&filter=2    ← письма

Даты панель этого сайта понимает только как `гггг-мм-дд`; `дд.мм.гггг` она молча
подменяет своим диапазоном (тот же закон, что у Goldenbride с ISO).

## ⚠⚠ Реплики чата лежат **на шаг глубже**, чем видно из списка

Здесь легко ошибиться, и эта ошибка уже была записана в этом файле как решение.
Страница «История чата» показывает **сводку** — `Создан | Завершен | Мужчина |
Пригласил | Девушка | Состояние`, — и ни одного текста сообщения в ней правда
нет. Отсюда и был сделан вывод «чата в панели нет, он живёт в сокет-подсистеме
`chat.step2love.com`». Вывод неверен: **каждая строка сводки — ссылка**
`/chat/?chatid=738476`, и за ней лежит расшифровка разговора целиком —

    2026-11-02 11:56:48 : Chat is started
    2026-11-02 11:56:48 Anastasiya: Hello Semen, nice to talk with you…
    2026-11-02 11:56:56 Semen: Hi

Цена прежнего вывода: у сайта без сокета и без API это **единственная** дорога
входящей реплики, и пока её не было, в базе по Step2Love лежал ровно ноль
сообщений при живом разговоре в панели (замер 02.11.2026).

⚠ Сводка делится фильтрами, и реплики есть **не во всех**: `chatfilter=1`
(активные) и `2` (завершённые) — это разговоры, а `3`/`4` («непринятые
приглашения») — инвайты, которые не приняли; их таблица — отправлено, отозвано,
причина, — и ни одной реплики там нет. Ходим только за первыми двумя.

⚠⚠ Окно дат активные сессии **не отсекает** — замерено тремя окнами (октябрь,
весь ноябрь, «сегодня с 13:00»): все три вернули одну и ту же сессию, начатую
сегодня в 11:56. Это и нужно: живой разговор, начатый неделю назад, не выпадет
из суточного окна круга. На завершённых окно работает обычным образом.

⚠ У реплики чата **нет своего номера** — номер есть у сессии. Ключ строки
(`uid`, см. `chat_panel_rows`) собирается здесь: `738476-3` — сессия и место
реплики в ней, считая системные строки, чтобы пропуск системной не сдвигал
нумерацию.

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

from logging_.logging_ import logger_info
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

# Страница истории чата. Она же показывает сводку, она же — одну сессию: разницу
# задают доводы (`com=detailed` + `chatfilter` против `chatid`).
CHAT_PATH = 'chat/'

# Фильтры сводки, в которых лежат **разговоры**: активные и завершённые. Порядок
# и есть порядок заходов — живые важнее: если упрёмся в потолок сессий, обрежется
# хвост завершённых, а не разговор, который идёт прямо сейчас.
#
# ⚠ Фильтров у панели четыре, и `3`/`4` сюда не входят намеренно: «непринятые
# приглашения» — это инвайты, которые не приняли, и реплик в них нет вовсе
# (таблица «Отправлено | Отозвано | Причина | …»).
CHAT_FILTERS = (('Активные', '1'), ('Завершенные', '2'))

# Сколько сессий читаем за один заход. Каждая — отдельный запрос к панели, а круг
# ходит по настройке сайта (у Step2Love — минута), и без потолка оживший чат
# означал бы сотни запросов в минуту.
#
# ⚠ Потолок **не молчит**: упёршись, пишем в журнал, сколько сессий осталось за
# бортом. Молчаливое обрезание читается как «привезли всё».
CHAT_SESSIONS_MAX = 40

# Ссылка на сессию в сводке. По ней и собирается список: что панель считает
# разговором, решает она, а не наш разбор столбцов.
#
# ⚠ Ссылки как таковой в разметке **нет**: сводка вешает переход на саму строку
# (`<tr onclick="location.href='/chat/?chatid=738476';">`), и поиск по `href`
# не находит ни одной сессии при полной таблице. Поэтому ищем номер, а не тег.
_CHAT_ID_RE = re.compile(r'chatid=(\d+)')

# Строка сводки **целиком, вместе с открывающим тегом**: номер сессии лежит
# именно в нём (см. выше), а отбор по нашим анкетам — в ячейках.
_CHAT_ROW_RE = re.compile(r'<tr[^>]*>.*?</tr>', re.S | re.I)

# Номер строки: первая ячейка, состоящая из одних цифр.
_NUMBER_RE = re.compile(r'^\d+$')

# Первое число где угодно в тексте — номер сессии из подписи «Чат #738476».
_DIGITS_RE = re.compile(r'\d+')

# Дата панели: `гггг-мм-дд` с временем или без; `дд.мм.гггг` — на всякий случай.
_WHEN_RE = re.compile(r'\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}')


class DatingSiteImportStep2Love(DatingSiteImport):
    """История чата плюс два фильтра писем (см. заголовок файла)."""

    name = 'step2love'

    TASKS_PATH = 'tasks.php'

    def date_fmt(self) -> str:
        return '%Y-%m-%d'

    def has_journal(self) -> bool:
        return True

    async def journal_rows(self, site_id: int, profiles: dict, date_from: str,
                           date_to: str) -> tuple[int, list[dict], str]:
        """Реплики чата и письма — одним списком.

        ## ⚠⚠ Источника два, и падение одного не уносит второй

        Заход считается неудачным, только когда **не вышло ни у одного**:
        «ничего не прочитали» — это отказ, «не прочитали одно из двух» —
        третий ответ `why` (см. контракт грани). Вот зачем это правило: чат —
        живой разговор, у которого эта дорога единственная, а списки писем
        висят на странице задач, которую панель меняет без нас. Сложи их в одну
        судьбу — и пропавший пункт «Неотвеченные сообщения» оставил бы девушек
        без входящих реплик, не сказав об этом ни слова про чат.
        """
        total, rows, why = 0, [], []

        # Порядок не случаен: чат первым. Он живой, и при потолке запросов
        # обрезаться должно то, что подождёт.
        sources = (('чат', self._chat), ('письма', self._mail))

        for title, source in sources:
            try:
                count, part = await source(site_id, profiles, date_from, date_to)
            except DatingPanelFetchError as e:
                why.append(f'{title} — {e}')
                continue

            total += count
            rows.extend(part)

        if len(why) == len(sources):
            raise DatingPanelFetchError('; '.join(why))

        return total, rows, '; '.join(why)

    async def _mail(self, site_id: int, profiles: dict, date_from: str,
                    date_to: str) -> tuple[int, list[dict]]:
        """Письма: страница задач → её же фильтрованные списки `mailbox.php`."""
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

    async def _chat(self, site_id: int, profiles: dict, date_from: str,
                    date_to: str) -> tuple[int, list[dict]]:
        """Реплики чата: сводка сессий → расшифровка каждой.

        Два захода: сперва список номеров сессий по фильтрам сводки, затем по
        запросу на сессию. Дороже одного списка, но дешевле браузера, а другой
        дороги у входящей реплики на этом сайте нет вовсе.
        """
        found = await self._chat_sessions(site_id, profiles, date_from, date_to)

        total, rows = 0, []
        for chat_id in found:
            html = await panel_fetch_html(site_id, CHAT_PATH, {'chatid': chat_id})
            lines = self._chat_log(chat_id, html)
            total += len(lines)
            rows.extend(lines)

        return total, rows

    @staticmethod
    async def _chat_sessions(site_id: int, profiles: dict, date_from: str,
                             date_to: str) -> list[str]:
        """Номера сессий из сводки — только наших анкет и не больше потолка.

        ⚠ Отбираем **по строке сводки**, а не по разбору столбцов: в строке
        стоят оба номера, и «есть ли в ней хоть одна наша анкета» — вопрос, на
        который не нужно знать, которая колонка «Девушка». Колонки панель
        переставит без нас, номера — нет.

        ⚠ Пустой список анкет значит «не сужаем»: у `chat_panel_import` он
        пустым не бывает (он же и отказывает раньше), а пробнику и тесту
        удобнее видеть всё, что отдала панель.
        """
        ours = re.compile(r'(?<!\d)(?:' + '|'.join(
            re.escape(str(uid)) for uid in profiles) + r')(?!\d)') if profiles else None

        found = []
        for title, filt in CHAT_FILTERS:
            html = await panel_fetch_html(site_id, CHAT_PATH, {
                # Панель этого сайта ждёт время рядом с датой — так подписаны её
                # собственные ссылки; голая дата сужает окно до полуночи.
                'from_date': f'{date_from} 00:00:00',
                'to_date': f'{date_to} 23:59:59',
                'chatfilter': filt, 'com': 'detailed'})

            for row in _CHAT_ROW_RE.findall(html):
                if ours and not ours.search(row):
                    continue
                for one in _CHAT_ID_RE.findall(row):
                    if one not in found:
                        found.append(one)

        if len(found) > CHAT_SESSIONS_MAX:
            logger_info(f'[dating] Step2Love: сессий чата {len(found)}, читаем '
                        f'{CHAT_SESSIONS_MAX} — остальные подождут захода круга')
            found = found[:CHAT_SESSIONS_MAX]

        return found

    @classmethod
    def _chat_log(cls, chat_id: str, html: str) -> list[dict]:
        """Расшифровка одной сессии — записями того же вида, что отдаёт сценарий.

        ⚠ Стороны берутся из **шапки** страницы, а не из реплик: в реплике
        стоит одно имя, а нужен номер — по нему `chat_panel_rows` и узнаёт нашу
        анкету. Шапка даёт обе пары «номер — имя» разом.

        ⚠⚠ Нет номеров сторон — **отказ**, а не пустой список. Пустая сессия
        законна (разговор начат, никто не писал), а сессия без сторон значит,
        что разметка сменилась: запиши мы её нулём, забор молча перестал бы
        возить реплики, продолжая докладывать «ok».
        """
        parser = _ChatLog()

        try:
            parser.feed(str(html or ''))
        except Exception as e:
            raise DatingPanelFetchError(f'чат #{chat_id} не разобрался: {e}')

        sides = parser.sides
        if not (sides.get('from') or {}).get('uid') \
                or not (sides.get('to') or {}).get('uid'):
            raise DatingPanelFetchError(
                f'чат #{chat_id}: на странице нет номеров сторон — разметка '
                'панели сменилась')

        out = []
        # Место реплики считаем **по всем строкам**, включая системные («Chat is
        # started»): пропусти мы их в счёте, и появление новой системной строки
        # сдвинуло бы ключи всех последующих реплик — то есть привезло бы
        # разговор заново, вторым экземпляром.
        for at, line in enumerate(parser.lines, 1):
            if not line['who'] or not line['text']:
                continue

            said = cls._who(chat_id, line['who'], line['name'], sides)
            heard = 'to' if said == 'from' else 'from'

            out.append({'uid': f'{chat_id}-{at}',
                        'when': line['when'],
                        'from': sides[said]['name'], 'from_uid': sides[said]['uid'],
                        'to': sides[heard]['name'], 'to_uid': sides[heard]['uid'],
                        'text': line['text']})

        return out

    @staticmethod
    def _who(chat_id: str, who: str, name: str, sides: dict) -> str:
        """Кто сказал реплику: решает класс, имя — сверка.

        ⚠⚠ Ошибка здесь тяжелее всех прочих в этом файле: перепутай стороны — и
        **наша собственная реплика ляжет входящей**, потому что направление
        `chat_panel_rows` выводит из того, чей номер совпал с анкетой. Агент
        ответил бы на свой же текст, и выглядело бы это исправной работой.

        Поэтому два сигнала, а не один. Класс (`chatMsgFrom`/`chatMsgTo`) —
        главный: он часть разметки и не зависит от того, как кого зовут. Имя —
        сверка, и она вмешивается **только при однозначном противоречии**: имя
        не совпало со своей стороной, но в точности совпало с другой. Имя, не
        совпавшее ни с одной (прозвище, сокращение), ничего не значит — верим
        классу.
        """
        said = ' '.join(str(name or '').split()).casefold()
        if not said or said == str((sides.get(who) or {}).get('name') or '').casefold():
            return who

        other = 'to' if who == 'from' else 'from'
        if said != str((sides.get(other) or {}).get('name') or '').casefold():
            return who

        logger_info(f'[dating] чат #{chat_id}: разметка относит реплику к стороне '
                    f'«{who}», а имя «{name}» — к другой; верим имени')

        return other

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
        """Наш диапазон вместо того окна, что несёт ссылка панели.

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


class _ChatLog(HTMLParser):
    """Страница одной сессии `/chat/?chatid=N`: её стороны и реплики по порядку.

    Разметка подписана классами, и именно они здесь опора:

        div.oneChatFrom2   шапка одной стороны: «ID: 1803186», аватар, «Anastasiya»
        div.oneChatTo2     шапка другой
        div.oneChatText2   строка журнала; системная несёт ещё `oneChatMessageSystem`
          span.chatDate      время
          span.chatMsgFrom   сказала сторона `from`
          span.chatMsgTo     сказала сторона `to`

    ⚠ Регулярным выражением это не берётся: в шапке стороны лежит **вложенный**
    `div` (значок нового пользователя), и нежадное `(.*?)</div>` обрывается на
    нём — номер прочитался бы, а имя потерялось. Поэтому счётчик вложенности,
    как у `_Mailbox` рядом.

    ⚠ Имя в шапке — **лоскут без двоеточия**: подписи панель ставит через него
    (`ID:`, `Возраст:`), а имя стоит само по себе. Правило держится за знак
    препинания, а не за порядок строк, и переживёт добавление новой подписи.
    """

    # Класс блока шапки → сторона, о которой он рассказывает.
    SIDES = {'oneChatFrom2': 'from', 'oneChatTo2': 'to'}

    # Класс подписи реплики → сторона, которая её сказала.
    WHO = {'chatMsgFrom': 'from', 'chatMsgTo': 'to'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.number = ''                  # номер сессии из «Чат #738476»
        self.sides: dict[str, dict] = {}  # 'from'/'to' → {'uid', 'name'}
        self.lines: list[dict] = []       # строки журнала по порядку чтения
        self._mode = ''                   # '' | 'side' | 'line'
        self._side = ''                   # какая сторона, пока `_mode == 'side'`
        self._depth = 0                   # вложенность внутри блока
        self._pieces: list[str] = []      # лоскуты шапки между <br>
        self._buf = ''
        self._span = ''                   # что читаем сейчас: number | when | who:…
        self._line: dict | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        css = str(a.get('class') or '')

        if tag == 'br':
            self._flush()

            return

        if tag == 'div':
            if self._mode:
                # Вложенный блок — не новый повод: значок, обёртка аватара.
                self._depth += 1

                return

            for mark, side in self.SIDES.items():
                if mark in css:
                    self._open('side', side)

                    return

            if 'oneChatText2' in css:
                self._open('line', '')
                self._line = {'when': '', 'who': '', 'name': '', 'text': ''}

            return

        if tag != 'span':
            return

        if 'chatID2' in css:
            self._span, self._buf = 'number', ''

            return

        if self._mode != 'line':
            return

        for mark, who in self.WHO.items():
            if mark in css:
                self._span, self._buf = f'who:{who}', ''

                return

        if 'chatDate' in css:
            self._span, self._buf = 'when', ''

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        # `<br/>` сюда, `<br>` — в `handle_starttag`: панель пишет и так и так.
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._mode or self._span:
            self._buf += data

    def handle_endtag(self, tag: str) -> None:
        if tag == 'span' and self._span:
            self._take(' '.join(self._buf.split()))
            self._span, self._buf = '', ''

            return

        if tag != 'div' or not self._mode:
            return

        if self._depth:
            self._depth -= 1

            return

        if self._mode == 'side':
            self._flush()
            self.sides[self._side] = self._known(self._pieces)
        elif self._line is not None:
            # Текст реплики — то, что осталось после подписей: каждая из них
            # забрала свой лоскут, обнулив накопитель на входе и выходе.
            self._line['text'] = ' '.join(self._buf.split()).lstrip(':').strip()
            self.lines.append(self._line)

        self._mode, self._side, self._line, self._buf = '', '', None, ''

    def _open(self, mode: str, side: str) -> None:
        self._mode, self._side, self._depth = mode, side, 0
        self._pieces, self._buf, self._span = [], '', ''

    def _flush(self) -> None:
        """Лоскут шапки между `<br>` — отдельной записью. В реплике границ нет."""
        if self._mode != 'side':
            return

        text = ' '.join(self._buf.split())
        if text:
            self._pieces.append(text)
        self._buf = ''

    def _take(self, text: str) -> None:
        """Прочитанная подпись — туда, где её ждут."""
        if self._span == 'number':
            found = _DIGITS_RE.search(text)
            self.number = found.group(0) if found else ''
        elif self._line is None:
            return
        elif self._span == 'when':
            self._line['when'] = text
        elif self._span.startswith('who:'):
            self._line['who'], self._line['name'] = self._span[4:], text

    @staticmethod
    def _known(pieces: list) -> dict:
        """Шапка стороны: номер из подписанного лоскута, имя — из неподписанного.

        ⚠ Подписанных лоскутов в шапке **два** — «ID: 1814826» и «Возраст: 48»,
        — и оба содержат число. Поэтому номер берётся из того, чья подпись
        названа (`ID`), а «первый подписанный» остаётся запасным путём: переставь
        панель строки местами, и возрастом подписался бы номер собеседника.
        """
        uid = named = name = ''

        for piece in pieces:
            if ':' not in piece:
                name = name or piece

                continue

            found = _DIGITS_RE.search(piece)
            if not found:
                continue

            named = named or found.group(0)
            if not uid and 'id' in piece.split(':', 1)[0].strip().casefold():
                uid = found.group(0)

        return {'uid': uid or named, 'name': name}


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
