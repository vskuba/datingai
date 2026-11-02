"""Драйверы импорта переписки: выбор по реестру и дорога журнала каждого сайта.

Проверяются **обе** половины разделения, ради которого слой затевался:

* реестр выбирает драйвера по `presence.driver`, а не по догадке;
* драйвер отвечает за **что принести** — страницы, даты, разбор, — а база,
  близнецы и диалоги остаются в `chat_panel`: здесь их нет и нечего искать.

Страницы Step2Love — живые образцы, сняты с агентской панели и обезличены:
задачи и ящик (`frames/step2love_tasks.html`, `step2love_mailbox.html`),
сводка чата и расшифровка сессии (`step2love_chat_list.html`,
`step2love_chat.html`).

⚠ Два источника здесь сверены **по-разному**, и это не небрежность:

* **чат** — живой целиком: страницы сняты с настоящего разговора 02.11.2026
  (две реплики и системная строка), и разбор проверяется против них;
* **письма** — строки **синтетические**: переписки в агентской части сайта на
  день правки нет ни одной (сверено по всем фильтрам за месяц через читалку
  панели), живой формат строк сверяют глазами на первом же импорте. Оттого и
  проверки бережливые: пустоту драйвер называет пустотой, непонятый список —
  отказом с причиной, а не нулём в отчёте.
"""

import re
from pathlib import Path

import pytest

from src.dating.chat_panel import CHAT_PANEL_FIELD, chat_panel_rows
from src.dating.panel_fetch import DatingPanelFetchError
from src.dating.site import site_import_step2love
from src.dating.site.site_import import DatingSiteImport
from src.dating.site.site_import_goldenbride import DatingSiteImportGoldenbride
from src.dating.site.site_import_step2love import DatingSiteImportStep2Love
from src.dating.site.site_registry import DATING_SITE_IMPORT_DRIVERS, site_registry_import

pytestmark = pytest.mark.profile

TASKS = (Path(__file__).parent / 'frames' / 'step2love_tasks.html').read_text(encoding='utf-8')
MAILBOX_EMPTY = (Path(__file__).parent / 'frames' / 'step2love_mailbox.html').read_text(encoding='utf-8')

# Живые страницы истории чата: сводка сессий и расшифровка одной. Сняты с
# агентской панели 02.11.2026 и обезличены (фамилии анкет, номер агентства,
# имена сторон). ⚠ Берутся целиком, а не кусками: в них ровно те две ловушки,
# на которых разбор и ломался — переход, висящий на `<tr onclick>` вместо
# ссылки, и вложенный `div` внутри шапки стороны.
CHAT_LIST = (Path(__file__).parent / 'frames'
             / 'step2love_chat_list.html').read_text(encoding='utf-8')
CHAT_ONE = (Path(__file__).parent / 'frames'
            / 'step2love_chat.html').read_text(encoding='utf-8')

# Сводка без единой сессии: таблица с одной лишь шапкой столбцов.
CHAT_NONE = re.sub(r'<tr style="cursor: pointer;".*?</tr>', '', CHAT_LIST,
                   flags=re.S)


def _letter(number: str, when: str, man: str, girl: str, text: str) -> str:
    """Строка ящика в форме панели: блок `div class="mailbox-row"`.

    Номер, подпись, стороны в ссылках и дату панель разносит по inline-элементам
    вразноброс — здесь один из правдоподобных видов; разбор позаконтентный,
    порядок ему не указ.
    """
    return (f'<div class="mailbox-row"><b>{number}</b> '
            f'<span title="{text} — полный текст">{text}…</span> '
            f'<div><a href="/profile.php?id=128282">{man}</a> '
            f'<a href="/girl.php?id=1777692">{girl}</a></div> {when}</div>')


def _mailbox_page(*rows: str) -> str:
    """Страница ящика: первой таблицей — блок `Your ID-number`, та самая, которой
    табличный читатель ограничился бы и до писем не дошёл бы вовсе."""
    head = '<table><tr><td>Your ID-number:</td><td><b>A-4913343</b></td></tr></table>'

    return head + ('<div class="mailbox-row">No messages found</div>'
                   if not rows else ''.join(rows))


class TestRegistry:
    """Пятая таблица реестра: «чем забирать журнал» — одна строка на сайт."""

    def test_named_driver_wins(self):
        for name, cls in (('step2love', DatingSiteImportStep2Love),
                          ('goldenbride', DatingSiteImportGoldenbride),
                          ('base', DatingSiteImport)):
            assert isinstance(site_registry_import({'driver': name}), cls), name

    def test_unknown_and_missing_fall_to_goldenbride(self):
        """Умолчание то же переходное, что у остальных таблиц реестра: сайт без
        ключа `driver` читается панелью Goldenbride, как читал всегда."""
        for presence in ({}, {'base': 'https://luxee.com'}, {'driver': 'нет-такого'}):
            assert type(site_registry_import(presence)) is DatingSiteImportGoldenbride

    def test_table_is_the_whole_choice(self):
        """Новый сайт — строка здесь плюс файл рядом; веток по сайтам в коде нет."""
        assert set(DATING_SITE_IMPORT_DRIVERS) == {'goldenbride', 'step2love', 'base'}

    def test_dates_speak_each_panels_language(self):
        """Формат даты — часть языка сайта: на чужом панель молча подменяет
        диапазон своим, и импорт возил бы одно и то же."""
        assert DatingSiteImportGoldenbride().date_fmt() == '%d.%m.%Y'
        assert DatingSiteImportStep2Love().date_fmt() == '%Y-%m-%d'

    def test_scenario_name_is_one_question_for_all(self):
        """Сценарий запасной дороги — общий: имя вопроса не зависит от сайта,
        иначе одна и та же настройка у разных сайтов искала бы разные сценарии."""
        assert DatingSiteImportStep2Love().scenario_name() == \
            DatingSiteImportGoldenbride().scenario_name()

    async def test_base_has_no_journal_and_no_lie(self):
        """Базовый драйвер журнала не знает — и не делает вид, что читает."""
        driver = DatingSiteImport({})

        assert driver.has_journal() is False

        with pytest.raises(NotImplementedError):
            await driver.journal_rows(1, {}, '', '')


class TestStep2LoveJournal:
    """Задачи → фильтры писем: что драйвер делает с живой страницей."""

    @staticmethod
    def _serve(monkeypatch, pages: dict) -> list:
        """Подменяет читалку панели: по доводам запроса — своя страница.

        Страниц три вида, и различает их сам запрос, как у живой панели:
        задачи, список писем по фильтру, история чата (сводка по `chatfilter`
        либо расшифровка по `chatid`). Чат по умолчанию пуст — тесты писем про
        него ничего не говорят и говорить не должны.
        """
        seen: list = []

        async def fetch(site_id: int, path: str, params: dict) -> str:
            seen.append((path, dict(params)))

            if path == 'tasks.php':
                return pages['tasks']

            if path == site_import_step2love.CHAT_PATH:
                chat = pages.get('chat') or {}
                key = str(params.get('chatid') or params.get('chatfilter') or '')

                return chat.get(key, chat.get('', CHAT_NONE))

            mail = pages['mail']

            return mail[str(params['filter'])] if str(params.get('filter')) in mail \
                else mail.get('', '')

        monkeypatch.setattr(site_import_step2love, 'panel_fetch_html', fetch)

        return seen

    async def test_empty_lists_are_honest_zero(self, monkeypatch):
        """Пустой ящик панель называет словами — драйвер верит им и молчит."""
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'': MAILBOX_EMPTY}})

        total, rows, why = await DatingSiteImportStep2Love().journal_rows(
            999_345, {}, '2026-10-26', '2026-10-27')

        assert total == 0 and rows == [] and why == ''

    async def test_window_ours_not_panels(self, monkeypatch):
        """Ссылка «Задач» несёт своё тридцатидневное окно; круг ходит от пяти
        минут и просит сутки — своё окно обязано перекрывать окно панели."""
        seen = self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'': MAILBOX_EMPTY}})

        await DatingSiteImportStep2Love().journal_rows(999_345, {}, '2026-10-26', '2026-10-27')

        mail = [one for one in seen if one[0] != site_import_step2love.CHAT_PATH]

        assert mail[0][0] == 'tasks.php'
        assert len(mail) == 3, 'заходов ровно по числу пунктов переписки'

        for path, params in mail[1:]:
            assert params['date_from'] == '2026-10-26' and params['date_to'] == '2026-10-27', path
            assert params['filter'] in {'9', '2'}, params

    async def test_rows_come_in_scenario_shape(self, monkeypatch):
        """Ячейки — тем же видом, что отдаёт `extract` сценария: общий разбор
        строк не должен дорогой различать панель и сценарий."""
        page = _mailbox_page(_letter('1041', '2026-10-26 14:05', 'Gerard', 'Diana', 'Hi darl'))
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'9': page, '2': MAILBOX_EMPTY}})

        total, rows, why = await DatingSiteImportStep2Love().journal_rows(
            999_345, {}, '2026-10-26', '2026-10-27')

        assert total == 1 and len(rows) == 1
        row = rows[0]
        assert row['number'] == '1041' and row['when'] == '2026-10-26 14:05'
        assert row['from'] == 'Gerard' and '128282' in row['from_uid']
        assert row['to'] == 'Diana' and '1777692' in row['to_uid']
        assert row['text'] == 'Hi darl…' and 'полный текст' in row['text_title']

    async def test_same_letter_in_both_lists_once(self, monkeypatch):
        """Непрочитанное лежит и в неотвеченных — номер строки и есть ключ."""
        page = _mailbox_page(_letter('1041', '2026-10-26 14:05', 'Gerard', 'Diana', 'Hi'))
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'9': page, '2': page}})

        total, rows, why = await DatingSiteImportStep2Love().journal_rows(
            999_345, {}, '2026-10-26', '2026-10-27')

        assert total == 2 and len(rows) == 1

    async def test_missing_task_point_complains(self, monkeypatch):
        """Список задач панель меняет без нас: «не забрали ничего» обязано
        звучать причиной, а не нулём в отчёте.

        ⚠ Причина приезжает третьим ответом `why`, а не исключением: с тех пор
        как у сайта два источника журнала, беда писем не отменяет чат. Правило
        «молчаливого нуля не бывает» от этого не ослабло — у него просто
        появился второй канал, и сторож стоит на нём.
        """
        self._serve(monkeypatch, {'tasks': TASKS.replace('filter=9', 'filter=7'),
                                  'mail': {'': MAILBOX_EMPTY}})

        total, rows, why = await DatingSiteImportStep2Love().journal_rows(
            999_345, {}, '2026-10-26', '2026-10-27')

        assert rows == [] and 'нет пункта' in why and 'письма' in why

    async def test_unparsable_rows_complain_not_silence(self, monkeypatch):
        """Строки есть, разобрать нельзя ни одной — причина: пустота вместо
        непонятой страницы обнулила бы доверие к импорту целиком."""
        page = _mailbox_page('<div class="mailbox-row"><span>страница не про письма</span>'
                             '</div>')
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'9': page, '2': MAILBOX_EMPTY}})

        total, rows, why = await DatingSiteImportStep2Love().journal_rows(
            999_345, {}, '2026-10-26', '2026-10-27')

        assert rows == [] and 'сверяют руками' in why

    async def test_page_without_mark_or_rows_complains(self, monkeypatch):
        """Ни строк, ни слов пустоты — страница непонятная: это не «пусто»,
        и молча проглотить её нельзя."""
        page = ('<table><tr><td>Your ID-number:</td><td><b>A-4913343</b></td></tr></table>'
                '<div class="mailbox-row"><img src="logo.png"></div>')
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'9': page, '2': MAILBOX_EMPTY}})

        total, rows, why = await DatingSiteImportStep2Love().journal_rows(
            999_345, {}, '2026-10-26', '2026-10-27')

        assert rows == [] and 'непонятной страницей' in why


class TestStep2LoveChat:
    """История чата: сводка сессий → расшифровка каждой.

    У сайта без сокета и без API это **единственная** дорога входящей реплики,
    и пока её не было, в базе по Step2Love лежал ноль сообщений при живом
    разговоре в панели. Поэтому проверки здесь не про удобство разбора, а про
    то, что реплика доезжает и доезжает с правильной стороной.
    """

    _serve = staticmethod(TestStep2LoveJournal._serve)

    @staticmethod
    def _quiet(**chat) -> dict:
        """Страницы, на которых говорит только чат: письма молчат пустотой."""
        return {'tasks': TASKS, 'mail': {'': MAILBOX_EMPTY}, 'chat': chat}

    async def test_live_page_gives_both_replies_and_skips_the_system_line(self):
        """Живая страница сессии: две реплики, «Chat is started» — не реплика."""
        rows = DatingSiteImportStep2Love()._chat_log('738476', CHAT_ONE)

        assert len(rows) == 2, 'системная строка репликой не считается'
        assert [row['text'] for row in rows] == [
            'Hello Gerard, nice to talk with you, if you do not mind)', 'Hi']
        assert [row['when'] for row in rows] == ['2026-11-02 11:56:48',
                                                 '2026-11-02 11:56:56']

    async def test_sides_come_from_the_header_with_numbers(self):
        """Стороны — номерами из шапки: по ним `chat_panel_rows` узнаёт анкету.

        ⚠ Имя из шапки берётся сквозь **вложенный** `div` (значок нового
        пользователя): на нём обрывается нежадное регулярное выражение, и
        прежде чем ставить счётчик вложенности, разбор терял имя собеседника.
        """
        rows = DatingSiteImportStep2Love()._chat_log('738476', CHAT_ONE)

        girl, man = rows[0], rows[1]

        assert girl['from_uid'] == '1777692' and girl['from'] == 'Diana'
        assert girl['to_uid'] == '128282' and girl['to'] == 'Gerard'
        assert man['from_uid'] == '128282' and man['from'] == 'Gerard'
        assert man['to_uid'] == '1777692' and man['to'] == 'Diana'

    async def test_the_key_counts_the_system_line_too(self):
        """Место реплики считается по **всем** строкам журнала.

        Пропусти системную в счёте — и появление новой сдвинуло бы ключи всех
        последующих реплик, то есть привезло бы разговор вторым экземпляром.
        """
        rows = DatingSiteImportStep2Love()._chat_log('738476', CHAT_ONE)

        assert [row['uid'] for row in rows] == ['738476-2', '738476-3']

    async def test_the_girls_line_is_outgoing_and_the_mans_is_incoming(self):
        """Главное: наша реплика — исходящая, его — входящая.

        Перепутай стороны, и агент отвечал бы на собственный текст, а выглядело
        бы это исправной работой.
        """
        rows = DatingSiteImportStep2Love()._chat_log('738476', CHAT_ONE)
        made = chat_panel_rows({CHAT_PANEL_FIELD: rows}, {'1777692': 78})

        assert [row['direction'] for row in made] == ['out', 'in']
        assert {row['profile_id'] for row in made} == {78}
        assert {row['sender_uid'] for row in made} == {'128282'}, \
            'собеседник в обеих строках один — это один диалог'
        assert [row['message_uid'] for row in made] == ['panel:738476-2',
                                                        'panel:738476-3']

    async def test_namesakes_leave_only_the_markup_to_decide(self):
        """Тёзки: имя не различает сторон, и всё решает класс реплики.

        ⚠ Сторож нужен отдельно от соседнего. Пока имена разные, перепутанный
        класс чинится сверкой по имени — то есть ошибка в разметочной половине
        **не видна**. Назови обеих сторон одинаково, и остаётся только она.
        """
        both = CHAT_ONE.replace('Gerard', 'Diana')
        rows = DatingSiteImportStep2Love()._chat_log('738476', both)
        made = chat_panel_rows({CHAT_PANEL_FIELD: rows}, {'1777692': 78})

        assert [row['direction'] for row in made] == ['out', 'in']

    async def test_a_name_that_contradicts_the_markup_wins(self):
        """Имя вмешивается только при однозначном противоречии разметке."""
        who = DatingSiteImportStep2Love()._who
        sides = {'from': {'uid': '1', 'name': 'Diana'},
                 'to': {'uid': '2', 'name': 'Gerard'}}

        assert who('1', 'from', 'Diana', sides) == 'from', 'имя согласно — класс'
        assert who('1', 'from', 'Gerard', sides) == 'to', 'имя другой стороны — верим ему'
        assert who('1', 'from', 'Johnny', sides) == 'from', 'чужое имя — верим классу'
        assert who('1', 'to', '', sides) == 'to', 'имени нет — верим классу'

    async def test_the_header_number_is_the_id_not_the_age(self):
        """В шапке два подписанных числа — номер и возраст; берётся номер."""
        known = site_import_step2love._ChatLog._known

        assert known(['Возраст: 48', 'ID: 128282', 'Gerard']) == \
            {'uid': '128282', 'name': 'Gerard'}

    async def test_sessions_are_picked_from_the_row_not_from_a_link(self, monkeypatch):
        """Переход висит на `<tr onclick>`, а ссылки в разметке нет вовсе.

        Поиск по `href` находил ноль сессий при полной таблице — отказа при
        этом не было ни одного: забор просто молчал.
        """
        self._serve(monkeypatch, self._quiet(**{'1': CHAT_LIST}))

        found = await DatingSiteImportStep2Love()._chat_sessions(
            999_345, {'1777692': 78}, '2026-11-01', '2026-11-02')

        assert found == ['738476']

    async def test_foreign_sessions_are_not_fetched(self, monkeypatch):
        """Панель агентства показывает всех, а заводим мы у себя не всех."""
        self._serve(monkeypatch, self._quiet(**{'1': CHAT_LIST}))

        found = await DatingSiteImportStep2Love()._chat_sessions(
            999_345, {'999999': 1}, '2026-11-01', '2026-11-02')

        assert found == []

    async def test_only_conversation_filters_are_walked(self, monkeypatch):
        """Непринятые приглашения — не разговор: реплик в них нет вовсе."""
        seen = self._serve(monkeypatch, self._quiet())

        await DatingSiteImportStep2Love()._chat_sessions(999_345, {}, '2026-11-01',
                                                         '2026-11-02')

        asked = [params['chatfilter'] for path, params in seen
                 if path == site_import_step2love.CHAT_PATH]

        assert asked == ['1', '2'], 'активные и завершённые, и только они'

    async def test_the_window_carries_time_not_just_a_date(self, monkeypatch):
        """Панель этого сайта ждёт время рядом с датой — так подписаны её
        собственные ссылки; голая дата сузила бы окно до полуночи."""
        seen = self._serve(monkeypatch, self._quiet())

        await DatingSiteImportStep2Love()._chat_sessions(999_345, {}, '2026-11-01',
                                                         '2026-11-02')

        for path, params in seen:
            if path != site_import_step2love.CHAT_PATH:
                continue
            assert params['from_date'] == '2026-11-01 00:00:00'
            assert params['to_date'] == '2026-11-02 23:59:59'
            assert params['com'] == 'detailed'

    async def test_a_session_without_sides_refuses(self):
        """Шапка без номеров — сменившаяся разметка, а не пустой разговор.

        Запиши мы её нулём, забор молча перестал бы возить реплики, продолжая
        докладывать «ok».
        """
        broken = CHAT_ONE.replace('oneChatFrom2', 'oneChatWhoever')

        with pytest.raises(DatingPanelFetchError, match='номеров сторон'):
            DatingSiteImportStep2Love()._chat_log('738476', broken)

    async def test_chat_reaches_journal_rows(self, monkeypatch):
        """Сквозь `journal_rows`: сводка, расшифровка, строки наружу."""
        self._serve(monkeypatch, self._quiet(**{'1': CHAT_LIST, '738476': CHAT_ONE}))

        total, rows, why = await DatingSiteImportStep2Love().journal_rows(
            999_345, {'1777692': 78}, '2026-11-01', '2026-11-02')

        assert total == 2 and len(rows) == 2 and why == ''
        assert rows[1]['text'] == 'Hi'

    async def test_broken_letters_do_not_take_chat_down(self, monkeypatch):
        """Источника два, и падение одного не уносит второй.

        Пункт задач панель меняет без нас, а чат — живой разговор, у которого
        эта дорога единственная.
        """
        self._serve(monkeypatch, {'tasks': TASKS.replace('filter=9', 'filter=7'),
                                  'mail': {'': MAILBOX_EMPTY},
                                  'chat': {'1': CHAT_LIST, '738476': CHAT_ONE}})

        total, rows, why = await DatingSiteImportStep2Love().journal_rows(
            999_345, {'1777692': 78}, '2026-11-01', '2026-11-02')

        assert len(rows) == 2, 'реплики доехали, хотя письма отказали'
        assert 'письма' in why and 'нет пункта' in why, 'и беда писем названа'

    async def test_both_sources_down_is_a_refusal(self, monkeypatch):
        """«Ничего не прочитали» — отказ, а не тихий ноль."""
        async def fetch(site_id: int, path: str, params: dict) -> str:
            raise DatingPanelFetchError('сессия панели протухла')

        monkeypatch.setattr(site_import_step2love, 'panel_fetch_html', fetch)

        with pytest.raises(DatingPanelFetchError, match='чат.*письма'):
            await DatingSiteImportStep2Love().journal_rows(999_345, {}, '2026-11-01',
                                                            '2026-11-02')

    async def test_the_cap_does_not_keep_quiet(self, monkeypatch, caplog):
        """Потолок сессий обрезает хвост — и говорит об этом в журнал.

        Молчаливое обрезание читается как «привезли всё».
        """
        many = ''.join(
            f'<tr style="cursor: pointer;" onclick="location.href=\'/chat/?chatid={n}\';">'
            f'<td>1777692</td></tr>'
            for n in range(site_import_step2love.CHAT_SESSIONS_MAX + 5))
        self._serve(monkeypatch, self._quiet(**{'1': f'<table>{many}</table>'}))

        with caplog.at_level('INFO'):
            found = await DatingSiteImportStep2Love()._chat_sessions(
                999_345, {'1777692': 78}, '2026-11-01', '2026-11-02')

        assert len(found) == site_import_step2love.CHAT_SESSIONS_MAX
        assert 'сессий чата' in caplog.text
