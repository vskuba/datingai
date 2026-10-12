"""Обрезка текста по границе строки: общая на био девушки и био собеседника.

⚠ Функция живёт в общем слое (`py_modules/text_/text_trim_lines.py`), а проверяется
отсюда: своей сюиты у общего слоя нет — его функции проверяют тесты проектов. Тот же
приём, что у `test_text_emoji.py`.
"""

import pytest

from text_.text_trim_lines import text_trim_lines

pytestmark = pytest.mark.profile


class TestItCutsOnLineBoundary:
    """⚠ Оборванная на полуслове строка выглядит как сбой и провоцирует модель
    её «досочинить» — поэтому режем целыми строками."""

    def test_short_text_goes_whole(self):
        assert text_trim_lines('раз\nдва', 100) == 'раз\nдва'

    def test_long_text_loses_whole_lines(self):
        # Строки по 10 знаков с переводом: в 25 знаков влезают две.
        text = '\n'.join(['0123456789'] * 5)

        assert text_trim_lines(text, 25) == '0123456789\n0123456789'

    def test_a_line_is_never_cut_in_half(self):
        text = 'раз\nдвадцать-два-знака-ровно'

        assert text_trim_lines(text, 10) == 'раз'

    def test_first_line_longer_than_limit_gives_nothing(self):
        """⚠ Так было и так остаётся: половина первой строки хуже пустоты.

        Вызывающий обязан это учитывать — `agent_bio` потому и режет **только блок
        параметров**, оставляя прозу нетронутой."""
        assert text_trim_lines('очень длинная первая строка', 5) == ''

    def test_edges_are_trimmed(self):
        assert text_trim_lines('  раз  \n', 100) == 'раз'

    def test_empty_input_is_legal(self):
        assert text_trim_lines('', 100) == ''
        assert text_trim_lines(None, 100) == ''
