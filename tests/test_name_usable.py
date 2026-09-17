"""Имя, которым нельзя обратиться: чужая письменность.

⚠ **Живой случай.** В ленте Ladadate попался `許萊納`. Письма пишутся
по-английски, агент вставить это имя не смог — вернул пустую реплику, — и набор
пула потратил на него вызов LLM, чтобы отбросить результат. И так каждый заход:
человек в сети, значит попадает в ленту снова. Таких в ленте дюжина из полутора
сотен.
"""

import pytest

from src.dating.mailing import (mailing_companion_vars,
                                       mailing_person_fields)
from src.dating.people import people_name_usable

pytestmark = pytest.mark.mail


class TestUsable:
    """Что считаем годным именем."""

    @pytest.mark.parametrize('name', [
        'Kevin',
        'Brian Ritter',
        # ⚠ Диакритика — не помеха: латиница, и в английском письме такое имя
        # выглядит обычно.
        'Piller László',
        'Thor Andrè Wisløff',
        'Norman Tobon López',
        # Прозвище вместо имени — тоже латиница: обратиться им можно.
        'Tall_Good_Man',
    ])
    def test_latin_is_usable(self, name):
        assert people_name_usable(name) is True

    @pytest.mark.parametrize('name', ['許萊納', 'Амір Даш', 'محمد', 'Ναός'])
    def test_other_scripts_are_not(self, name):
        """⚠ Судим по письменности, а не по «странности»: в английском тексте
        это инородное тело, и агент его не вставляет."""
        assert people_name_usable(name) is False

    @pytest.mark.parametrize('name', ['', None, '   ', '12345', '***'])
    def test_no_letters_is_not_a_name(self, name):
        """Обращение по «12345» хуже, чем общее."""
        assert people_name_usable(name) is False


class TestFields:
    """Подстановка переменных собеседника."""

    def test_alien_name_gives_nothing_to_substitute(self):
        """⚠ Ни целиком, ни по частям: `Hi 許萊納` читается как сбой подстановки."""
        got = mailing_person_fields({'uid': '1', 'name': '許萊納', 'city': 'Kyiv'})

        assert got.get('name', '') == ''
        assert got['firstname'] == '' and got['lastname'] == ''
        assert got['city'] == 'Kyiv', 'заодно потеряли город — это лишнее'

    def test_normal_name_is_untouched(self):
        got = mailing_person_fields({'uid': '2', 'name': 'Brian Ritter'})

        assert got['name'] == 'Brian Ritter'
        assert got['firstname'] == 'Brian' and got['lastname'] == 'Ritter'


class TestCardSaysWhy:
    """Карточка объясняет пустоту, а не молчит."""

    def test_empty_value_comes_with_a_reason(self):
        """⚠ Без объяснения страница показывала бы «не заполнено» у человека,
        чьё имя видно тут же рядом, — это читается как поломка панели."""
        known = [{'name': 'companion_name', 'source': 'companion',
                  'title': 'Имя', 'default_value': ''}]

        row = mailing_companion_vars({'uid': '1', 'name': '許萊納'}, known)[0]

        assert row['filled'] is False
        assert 'латиниц' in row['why']

    def test_default_still_saves_the_letter(self):
        """Умолчание переменной — заведённый исход, и он остаётся: письмо уйдёт
        с общим обращением, а не пропадёт."""
        known = [{'name': 'companion_name', 'source': 'companion',
                  'title': 'Имя', 'default_value': 'there'}]

        row = mailing_companion_vars({'uid': '1', 'name': '許萊納'}, known)[0]

        assert row['value'] == 'there' and row['from_default'] is True

    def test_usable_name_has_no_reason(self):
        known = [{'name': 'companion_name', 'source': 'companion',
                  'title': 'Имя', 'default_value': ''}]

        row = mailing_companion_vars({'uid': '2', 'name': 'Brian'}, known)[0]

        assert row['why'] == '' and row['value'] == 'Brian'
