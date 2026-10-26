"""Драйверы импорта переписки: выбор по реестру и дорога журнала каждого сайта.

Проверяются **обе** половины разделения, ради которого слой затевался:

* реестр выбирает драйвера по `presence.driver`, а не по догадке;
* драйвер отвечает за **что принести** — страницы, даты, разбор, — а база,
  близнецы и диалоги остаются в `chat_panel`: здесь их нет и нечего искать.

Страницы Step2Love — живые образцы (`frames/step2love_tasks.html` и
`step2love_mailbox.html`, сняты с агентской панели и обезличены); строки
писем — **синтетические**: переписки в агентской части сайта на день правки
нет ни одной (сверено по всем фильтрам за месяц через читалку панели), живой
формат строк сверяют глазами на первом же импорте. Оттого и проверки
бережливые: пустоту драйвер называет пустотой, непонятый список — отказом с
причиной, а не нулём в отчёте.
"""

from pathlib import Path

import pytest

from src.dating.panel_fetch import DatingPanelFetchError
from src.dating.site import site_import_step2love
from src.dating.site.site_import import DatingSiteImport
from src.dating.site.site_import_goldenbride import DatingSiteImportGoldenbride
from src.dating.site.site_import_step2love import DatingSiteImportStep2Love
from src.dating.site.site_registry import DATING_SITE_IMPORT_DRIVERS, site_registry_import

pytestmark = pytest.mark.profile

TASKS = (Path(__file__).parent / 'frames' / 'step2love_tasks.html').read_text(encoding='utf-8')
MAILBOX_EMPTY = (Path(__file__).parent / 'frames' / 'step2love_mailbox.html').read_text(encoding='utf-8')


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
        """Подменяет читалку панели: по фильтру — своя страница, и список запросов."""
        seen: list = []

        async def fetch(site_id: int, path: str, params: dict) -> str:
            seen.append((path, dict(params)))

            if path == 'tasks.php':
                return pages['tasks']

            mail = pages['mail']
            return mail[str(params['filter'])] if str(params.get('filter')) in mail \
                else mail.get('', '')

        monkeypatch.setattr(site_import_step2love, 'panel_fetch_html', fetch)

        return seen

    async def test_empty_lists_are_honest_zero(self, monkeypatch):
        """Пустой ящик панель называет словами — драйвер верит им и молчит."""
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'': MAILBOX_EMPTY}})

        total, rows = await DatingSiteImportStep2Love().journal_rows(
            999_345, {}, '2026-10-26', '2026-10-27')

        assert total == 0 and rows == []

    async def test_window_ours_not_panels(self, monkeypatch):
        """Ссылка «Задач» несёт своё тридцатидневное окно; круг ходит от пяти
        минут и просит сутки — своё окно обязано перекрывать панелино."""
        seen = self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'': MAILBOX_EMPTY}})

        await DatingSiteImportStep2Love().journal_rows(999_345, {}, '2026-10-26', '2026-10-27')

        assert seen[0][0] == 'tasks.php'
        assert len(seen) == 3, 'заходов ровно по числу пунктов переписки'

        for path, params in seen[1:]:
            assert params['date_from'] == '2026-10-26' and params['date_to'] == '2026-10-27', path
            assert params['filter'] in {'9', '2'}, params

    async def test_rows_come_in_scenario_shape(self, monkeypatch):
        """Ячейки — тем же видом, что отдаёт `extract` сценария: общий разбор
        строк не должен дорогой различать панель и сценарий."""
        page = _mailbox_page(_letter('1041', '2026-10-26 14:05', 'Gerard', 'Diana', 'Hi darl'))
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'9': page, '2': MAILBOX_EMPTY}})

        total, rows = await DatingSiteImportStep2Love().journal_rows(
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

        total, rows = await DatingSiteImportStep2Love().journal_rows(
            999_345, {}, '2026-10-26', '2026-10-27')

        assert total == 2 and len(rows) == 1

    async def test_missing_task_point_refuses(self, monkeypatch):
        """Список задач панель меняет без нас: «не забрали ничего» обязано
        звучать причиной, а не нулём в отчёте."""
        self._serve(monkeypatch, {'tasks': TASKS.replace('filter=9', 'filter=7'),
                                  'mail': {'': MAILBOX_EMPTY}})

        with pytest.raises(DatingPanelFetchError, match='нет пункта'):
            await DatingSiteImportStep2Love().journal_rows(999_345, {}, '2026-10-26',
                                                           '2026-10-27')

    async def test_unparsable_rows_refuse_not_silence(self, monkeypatch):
        """Строки есть, разобрать нельзя ни одной — отказ: пустота вместо
        непонятой страницы обнулила бы доверие к импорту целиком."""
        page = _mailbox_page('<div class="mailbox-row"><span>страница не про письма</span>'
                             '</div>')
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'9': page, '2': MAILBOX_EMPTY}})

        with pytest.raises(DatingPanelFetchError, match='сверяют руками'):
            await DatingSiteImportStep2Love().journal_rows(999_345, {}, '2026-10-26',
                                                           '2026-10-27')

    async def test_page_without_mark_or_rows_refuses(self, monkeypatch):
        """Ни строк, ни слов пустоты — страница непонятная: это не «пусто»,
        и молча проглотить её нельзя."""
        page = ('<table><tr><td>Your ID-number:</td><td><b>A-4913343</b></td></tr></table>'
                '<div class="mailbox-row"><img src="logo.png"></div>')
        self._serve(monkeypatch, {'tasks': TASKS, 'mail': {'9': page, '2': MAILBOX_EMPTY}})

        with pytest.raises(DatingPanelFetchError, match='непонятной страницей'):
            await DatingSiteImportStep2Love().journal_rows(999_345, {}, '2026-10-26',
                                                           '2026-10-27')
