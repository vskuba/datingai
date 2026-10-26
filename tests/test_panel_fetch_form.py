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

    def test_a_multiple_select_keeps_every_selected_option(self):
        """⚠ `select multiple` (интересы Luxee): взяли бы последнюю выбранную
        строку — список интересов ужался бы до одной молча, поле-то непустое."""
        got = panel_fetch_form(
            '<select name="I[]" multiple><option value="1" selected>Food</option>'
            '<option value="2">Drink</option>'
            '<option value="3" selected>Fitness</option></select>')

        assert got['I[]'] == 'Food, Fitness'

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


class TestTheMarkedFields:
    """Отмеченные `radio`/`checkbox` и числовые `hidden`: Step2Love пишет в
    `value` нумерацию сайта, а то, что человек видит на странице, стоит
    **текстом сразу за тегом**. Тот же случай, что невыбранный `select`: номер
    человеку не говорит ничего.

    ⚠ Текста нет — значение остаётся как есть: разметка вправе нести подпись в
    другом месте, и потерять тогда значение было бы хуже, чем взять номер.
    """

    def test_a_checked_radio_lands_as_its_label(self):
        got = panel_fetch_form(
            '<input type="radio" name="i[0][]" value="156"> Ordinary'
            '<input type="radio" name="i[0][]" value="157" checked> Athletic')

        assert got['i[0][]'] == ' Athletic'

    def test_an_untouched_group_is_empty_not_the_first_option(self):
        """⚠ До отмеченного дело не дошло — но имя обязано быть заведено
        пустой строкой: «ключ есть, пусто» и «ключа нет» решают разное."""
        got = panel_fetch_form(
            '<input type="radio" name="i[7][]" value="201"> one'
            '<input type="radio" name="i[7][]" value="202"> two')

        assert got == {'i[7][]': ''}

    def test_checked_checkboxes_land_together(self):
        """Галки одной группы (ценности Step2Love): потерялась бы любая —
        поле-то непустое."""
        got = panel_fetch_form(
            '<input type="checkbox" name="v[]" value="1" checked> Family'
            '<input type="checkbox" name="v[]" value="2"> Money'
            '<input type="checkbox" name="v[]" value="3" checked> Travel')

        assert got['v[]'] == ' Family,  Travel'

    def test_a_numeric_hidden_lands_as_the_text_beside_it(self):
        """Скрытый `city` с номером города; рядом, в той же ячейке, — само
        название. Без текста остался бы номер: `10126`."""
        got = panel_fetch_form('<input type="hidden" name="city" value="10126">'
                               '&nbsp;Kyiv\t\t    ')

        assert got['city'].strip() == 'Kyiv'

    def test_a_numeric_hidden_without_text_keeps_its_number(self):
        """Между тегом и следующим тегом только пробелы — брать нечего,
        номер остаётся: `AccountId` из образца именно таков."""
        got = panel_fetch_form('<input type="hidden" value="100001" '
                               'name="AccountId" />\n<tr>')

        assert got['AccountId'] == '100001'

    def test_a_word_hidden_is_never_replaced_by_the_text_after_it(self):
        """Не числовой `hidden` — обычное значение: текст за тегом это вёрстка
        таблицы, а не подпись поля."""
        got = panel_fetch_form('<input type="hidden" name="key" value="ok">'
                               'extra text')

        assert got['key'] == 'ok'
