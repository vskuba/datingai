"""Био девушки: проза плюс блок параметров.

`girl_bio` уезжает двумя дорогами — при заведении агента (`dating_girl_create`
добывает из неё факты) и на **каждом** ответе в чате (`answer.py`). Поэтому здесь
проверяется и состав, и предел длины.
"""

import pytest

from src.dating.agent import agent_bio

pytestmark = pytest.mark.profile

CARD = {'name': 'Olga', 'age': 36, 'city': 'Lviv', 'about': 'Red-haired woman.',
        'parameters': {'birthday': '1990-01-21', 'eyes': 'Hazel',
                       'height': '172 cm', 'hobby': 'swimming', 'hair': ''}}


class TestTheProseIsUntouched:
    """⚠ Первый абзац — то, что уезжало всегда; параметры его не меняют."""

    def test_the_prose_still_comes_first(self):
        got = agent_bio(CARD)

        assert got.startswith('My name is Olga. I am 36 years old. I live in Lviv.')

    def test_a_card_without_parameters_works_as_before(self):
        got = agent_bio({'name': 'Olga', 'age': 36, 'city': 'Lviv',
                         'about': 'Red-haired woman.'})

        assert got == 'My name is Olga. I am 36 years old. I live in Lviv. Red-haired woman.'

    def test_no_bio_rule_is_not_touched(self):
        """⚠ Параметры в условие «биографии нет» не входят: оно про анкету с сайта."""
        assert agent_bio({'name': 'Olga', 'parameters': {'eyes': 'Hazel'}}) == ''


class TestTheParameterBlock:
    def test_known_parameters_become_lines(self):
        got = agent_bio(CARD)

        assert 'Birthday: 1990-01-21' in got
        assert 'Eyes: Hazel' in got
        assert 'Height: 172 cm' in got

    def test_empty_parameters_are_not_named(self):
        assert 'Hair:' not in agent_bio(CARD)

    def test_the_block_is_separated_by_a_blank_line(self):
        """Чтобы модель видела границу между рассказом и справкой."""
        assert '\n\nBirthday: 1990-01-21' in agent_bio(CARD)

    def test_the_block_keeps_the_registry_order(self):
        got = agent_bio(CARD)

        assert got.index('Birthday:') < got.index('Eyes:') < got.index('Hobby:')


class TestTheLimit:
    """⚠⚠ Режется **только блок параметров**: проза идёт одной строкой, и обрезка
    по границе строки унесла бы её целиком, оставив агента без имени."""

    def test_a_huge_hobby_does_not_eat_the_prose(self):
        card = dict(CARD, parameters=dict(CARD['parameters'], hobby='x' * 5000))

        got = agent_bio(card)

        assert got.startswith('My name is Olga.')
        assert 'Birthday: 1990-01-21' in got, 'короткие строки должны уцелеть'
        assert 'x' * 5000 not in got, 'строка сверх предела должна отпасть'
