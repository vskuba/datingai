"""Реестр параметров анкеты: один каркас на окно, панель и био.

## ⚠⚠ Зачем каркас постоянный

Все 21 ключ лежат в карточке **всегда**, неизвестное — пустой строкой. Иначе
«ключа нет» и «ключ пуст» стали бы двумя разными состояниями, которые пришлось бы
различать в четырёх местах: в показе, в сборке био, в сравнении при правке и в
отсеве пустых.
"""

import pytest

from src.dating.profile_param import (PROFILE_PARAM_AREA_MAX,
                                      PROFILE_PARAM_KEYS,
                                      PROFILE_PARAM_PREFIX,
                                      PROFILE_PARAM_TEXT_MAX,
                                      PROFILE_PARAMS, profile_param_bio,
                                      profile_param_fields,
                                      profile_param_frame, profile_param_max)

pytestmark = pytest.mark.profile


class TestTheFrameIsAlwaysTheSame:
    """⚠ «Каркас ключей одинаковый для всех профилей» — проверяемо, а не на словах."""

    def test_there_are_twenty_one_keys(self):
        assert len(PROFILE_PARAM_KEYS) == 21
        assert len(set(PROFILE_PARAM_KEYS)) == 21, 'ключ повторился'

    def test_empty_values_give_the_whole_frame(self):
        got = profile_param_frame({})

        assert list(got) == list(PROFILE_PARAM_KEYS)
        assert set(got.values()) == {''}

    def test_none_gives_the_whole_frame_too(self):
        assert list(profile_param_frame(None)) == list(PROFILE_PARAM_KEYS)

    def test_strangers_are_dropped(self):
        got = profile_param_frame({'height': '172 cm', 'шпион': 'да'})

        assert got['height'] == '172 cm'
        assert 'шпион' not in got

    def test_order_is_the_registry_order(self):
        got = profile_param_frame({'hobby': 'x', 'birthday': '1990-01-21'})

        assert list(got)[0] == 'birthday', 'порядок не реестра'

    def test_values_are_trimmed(self):
        assert profile_param_frame({'eyes': '  Hazel \n'})['eyes'] == 'Hazel'


class TestTheBioLines:
    def test_empty_fields_are_not_named(self):
        """⚠ «Hair: —» модель читает как факт и может об этом заговорить."""
        got = profile_param_bio({'eyes': 'Hazel', 'hair': ''})

        assert got == ['Eyes: Hazel']

    def test_labels_come_from_the_registry(self):
        got = profile_param_bio({'marital_state': 'Never been Married'})

        assert got == ['Marital status: Never been Married']

    def test_lines_follow_the_registry_order(self):
        got = profile_param_bio({'hobby': 'swimming', 'birthday': '1990-01-21'})

        assert got == ['Birthday: 1990-01-21', 'Hobby: swimming']

    def test_nothing_known_gives_no_lines(self):
        assert profile_param_bio({}) == []
        assert profile_param_bio(None) == []


class TestTheFieldsForThePage:
    """⚠ Подписи и вид поля страница берёт **с сервера**: своя копия списка на
    странице разошлась бы с кодом молча — грабля словаря тегов галереи."""

    def test_every_key_has_a_title_and_a_kind(self):
        got = profile_param_fields()

        assert len(got) == 21
        for one in got:
            assert one['title'], f'у {one["key"]} нет подписи окна'
            assert one['kind'] in ('text', 'area')

    def test_two_fields_are_areas(self):
        areas = [one['key'] for one in profile_param_fields()
                 if one['kind'] == 'area']

        assert areas == ['hobby', 'about_partner']


class TestTheLengthLimits:
    def test_text_field_limit(self):
        assert profile_param_max('eyes') == PROFILE_PARAM_TEXT_MAX

    def test_area_field_limit(self):
        assert profile_param_max('hobby') == PROFILE_PARAM_AREA_MAX

    def test_stranger_has_no_limit(self):
        """Ноль — «такого поля нет»; вызывающий решает, что с этим делать."""
        assert profile_param_max('шпион') == 0


class TestThePrefix:
    def test_the_mark_name_is_built_from_the_prefix(self):
        assert PROFILE_PARAM_PREFIX + 'height' == 'parameters.height'

    def test_the_registry_rows_have_four_parts(self):
        for one in PROFILE_PARAMS:
            assert len(one) == 4, f'строка реестра не из четырёх частей: {one}'
