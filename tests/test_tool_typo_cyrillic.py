"""Кириллица в именах и ключах кода: проверка правила §1.2.

⚠ Функция живёт в общем слое (`py_modules/tool_/tool_typo.py`), а проверяется
отсюда: своей сюиты у слоя нет — его функции проверяют тесты проектов. Тот же
приём, что у `test_text_emoji.py` и `test_text_trim.py`.

## ⚠⚠ Почему тесты появились только сейчас

Проверка умела ключи словарей давно, но её **не звали**: застава выкладки
(`deploy_precheck`) смотрела только имена (`variable_cyrillic`), а имена и ключи —
разные узлы дерева. Из-за этого `{'текст': '', 'ссылка': ''}` простояло в
разборщике таблицы панели годами при зелёной заставе.

Теперь ключи судятся тем же `cyrillic` заставы, и у правила появилась цена ошибки —
значит нужны и сторожа.
"""

import textwrap

import pytest

from tool_.tool_typo import tool_typo_cyrillic

pytestmark = pytest.mark.core


def _found(tmp_path, code: str) -> list:
    """Находки по куску кода. Отступ снимается — исходник пишется с ним."""
    (tmp_path / 'sample.py').write_text(textwrap.dedent(code), encoding='utf-8')

    return tool_typo_cyrillic(tmp_path)


def _words(tmp_path, code: str) -> list:
    return [one['word'] for one in _found(tmp_path, code)]


class TestItSeesKeysNotOnlyNames:
    """⚠⚠ Ключ словаря для дерева — строковый литерал, а не имя: проверка имён
    его не видит вовсе, и ровно так нарушение и прожило."""

    def test_a_dict_literal_key_is_found(self, tmp_path):
        assert _words(tmp_path, "cell = {'текст': '', 'link': ''}") == ['текст']

    def test_an_index_read_is_found(self, tmp_path):
        assert _words(tmp_path, "value = row['ссылка']") == ['ссылка']

    def test_a_get_call_is_found(self, tmp_path):
        """`row.get('ключ')` — самая частая запись чтения, и прежде её не видели."""
        assert _words(tmp_path, "value = row.get('подсказка')") == ['подсказка']

    def test_pop_and_setdefault_are_found(self, tmp_path):
        got = _words(tmp_path, """
            row.pop('первый', None)
            row.setdefault('второй', 1)
        """)

        assert got == ['первый', 'второй']

    def test_a_cyrillic_name_is_still_found(self, tmp_path):
        """Прежнее умение никуда не делось: имена судятся по-прежнему."""
        assert _words(tmp_path, "def счёт():\n    pass") == ['счёт']


class TestItLeavesEverythingElseAlone:
    """Обратная сторона, и она важнее: проверка, которая часто ошибается, живёт
    неделю — её снимают вместе с настоящими находками."""

    def test_latin_keys_are_silent(self, tmp_path):
        assert _found(tmp_path, "cell = {'text': '', 'link': ''}") == []

    def test_a_russian_value_is_not_a_key(self, tmp_path):
        """⚠ Правило про имена, а не про язык: текст человеку — данные."""
        assert _found(tmp_path, "answer = {'detail': 'анкеты нет'}") == []

    def test_a_russian_message_is_silent(self, tmp_path):
        assert _found(tmp_path, "raise ValueError('почта обязательна')") == []

    def test_a_substring_check_is_not_a_key(self, tmp_path):
        """⚠⚠ `in` намеренно не ловится: тем же `in` проверяют подстроку
        (`'отказ' in str(error)`), и для дерева это один и тот же узел. Ловля
        дала бы проверку, которая ошибается чаще, чем находит."""
        assert _found(tmp_path, "if 'разметка' in str(error):\n    pass") == []

    def test_a_comment_is_silent(self, tmp_path):
        assert _found(tmp_path, "value = row['text']  # ключ ячейки таблицы") == []

    def test_a_single_letter_key_is_silent(self, tmp_path):
        """⚠ Односимвольный ключ — буква алфавита в таблице перевода, не имя.
        Без послабления проверка выдавала 97 находок на одном словаре."""
        assert _found(tmp_path, "LATIN = {'а': 'a', 'б': 'b'}") == []


class TestTheAllowMark:
    """⚠ Чужой ключ бывает невыносим: имя задал сценарий, схема или протокол на
    той стороне, и переименование в одиночку разъехалось бы с ними молча."""

    def test_a_marked_key_is_silent(self, tmp_path):
        got = _found(
            tmp_path,
            "link = row.get('ссылка')  # cyrillic-key: шаг extract сценария")

        assert got == []

    def test_an_empty_mark_is_itself_a_finding(self, tmp_path):
        """⚠⚠ `# cyrillic-key:` без слов — это «замолчи», а не «вот владелец
        имени»: через полгода его не отличить от забытого."""
        got = _found(tmp_path, "link = row.get('ссылка')  # cyrillic-key:")

        assert len(got) == 1
        assert 'без причины' in got[0]['hint']

    def test_the_mark_covers_the_whole_dict_from_its_opening_line(self, tmp_path):
        got = _found(tmp_path, """
            TAGS = {  # cyrillic-key: имена шагов сценария у соседа
                'первый': 1,
                'второй': 2,
            }
        """)

        assert got == []

    def test_the_mark_covers_the_dict_from_the_line_above(self, tmp_path):
        """Комментарий перед определением — основной способ комментировать."""
        got = _found(tmp_path, """
            # cyrillic-key: имена шагов сценария у соседа
            TAGS = {
                'первый': 1,
                'второй': 2,
            }
        """)

        assert got == []

    def test_the_mark_does_not_leak_to_the_next_statement(self, tmp_path):
        """⚠ Пометка покрывает свой словарь, а не всё ниже по файлу."""
        got = _words(tmp_path, """
            # cyrillic-key: имена шагов сценария у соседа
            TAGS = {'первый': 1}
            OTHER = {'второй': 2}
        """)

        assert got == ['второй']


class TestTheFindingShape:
    def test_it_points_at_the_key_line_not_the_dict_line(self, tmp_path):
        """⚠ Иначе у словаря на тридцать ключей все находки пришлись бы на одну
        строку, и чинить их было бы не по чему."""
        got = _found(tmp_path, """
            CELLS = {
                'text': '',
                'ссылка': '',
            }
        """)

        assert len(got) == 1
        assert got[0]['line'] == 4

    def test_a_broken_file_does_not_stop_the_walk(self, tmp_path):
        """Файл, который не разбирается, пропускается: один сломанный файл не
        должен ронять проверку всего дерева."""
        (tmp_path / 'broken.py').write_text('def (((', encoding='utf-8')
        (tmp_path / 'good.py').write_text("row['ключ']", encoding='utf-8')

        got = tool_typo_cyrillic(tmp_path)

        assert [one['word'] for one in got] == ['ключ']
