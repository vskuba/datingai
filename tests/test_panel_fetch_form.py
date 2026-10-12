"""Чтение формы админ-панели: input, выбранный option, textarea.

⚠ Это **семантика HTML**, а не свойство сайта: значение `input` лежит в `value`,
значение `select` — в тексте `option` с `selected`, значение `textarea` — в его
теле. Поэтому разбор общий, рядом с разбором таблицы, а карта имён — у драйвера.

Проверяется на **настоящем образце** (`tests/frames/ladadate_lady_edit.html`):
рукописная заглушка расходится с живой страницей молча.
"""

from pathlib import Path

import pytest

from src.dating.panel_fetch import panel_fetch_form

pytestmark = pytest.mark.profile

SAMPLE = (Path(__file__).parent / 'frames' / 'ladadate_lady_edit.html'
          ).read_text(encoding='utf-8')


class TestTheSample:
    """Настоящая страница — настоящие имена полей."""

    def test_input_value_is_read(self):
        assert panel_fetch_form(SAMPLE)['Name'] == 'Olga'

    def test_hidden_input_is_read_too(self):
        assert panel_fetch_form(SAMPLE)['AccountId'] == '100001'

    def test_number_input_is_read(self):
        assert panel_fetch_form(SAMPLE)['AgeFrom'] == '35'

    def test_select_gives_the_selected_option_text(self):
        """⚠ Текст, а не `value`: `value="208"` — внутренний номер сайта, он не
        говорит ни человеку, ни модели ничего."""
        assert panel_fetch_form(SAMPLE)['CountryId'] == 'Ukraine'

    def test_entities_are_unescaped(self):
        """⚠ Рост панель пишет сущностями: `5&#39;8&quot; - 172 cm`."""
        assert panel_fetch_form(SAMPLE)['HeightId'] == '5\'8" - 172 cm'

    def test_textarea_body_is_read(self):
        assert panel_fetch_form(SAMPLE)['Interest'] == 'swimming and dancing'

    def test_all_three_birthday_parts_are_read(self):
        got = panel_fetch_form(SAMPLE)

        assert (got['BirthdayDay'], got['BirthdayMonth'],
                got['BirthdayYear']) == ('21', 'Jan', '1990')


class TestTheEdges:
    def test_a_control_without_a_name_is_skipped(self):
        got = panel_fetch_form('<input type="text" value="x">')

        assert got == {}

    def test_a_select_without_a_selected_option_is_empty(self):
        got = panel_fetch_form(
            '<select name="A"><option value="1">one</option></select>')

        assert got['A'] == ''

    def test_an_empty_input_is_an_empty_string_not_absent(self):
        """⚠ Разница существенна: «поля нет» и «поле пусто» решают разное —
        первое значит «разметка сменилась»."""
        got = panel_fetch_form('<input name="A" type="text">')

        assert got == {'A': ''}

    def test_broken_markup_gives_what_it_managed_to_read(self):
        got = panel_fetch_form('<input name="A" value="1"><select name="B">')

        assert got['A'] == '1'

    def test_empty_input_is_legal(self):
        assert panel_fetch_form('') == {}
        assert panel_fetch_form(None) == {}
