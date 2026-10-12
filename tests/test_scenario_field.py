"""Перевод имён шагов сценария: переходный период понимает оба написания.

## ⚠⚠ Зачем вообще два имени

Имя шага задаёт **сценарий у соседа**, а читаем результат мы. Переименовать его у
себя в одиночку нельзя — разъехались бы молча. Поэтому переезд трёхзаходный, и
между первым заходом (этот код) и вторым (миграция сценариев) обязаны работать
**оба** написания: иначе прод встанет на то время, что лежит между двумя
выкладками.
"""

import pytest

from src.dating.scenario_field import (SCENARIO_FIELD_WAS, scenario_field,
                                       scenario_field_name)

pytestmark = pytest.mark.core


class TestItReadsBothNames:
    def test_the_new_name_is_read(self):
        assert scenario_field({'name': 'Olga'}, 'name') == 'Olga'

    def test_the_old_name_is_read_too(self):
        """⚠ Пока сценарии не переименованы, приходит именно старое имя."""
        assert scenario_field({'имя': 'Olga'}, 'name') == 'Olga'

    def test_the_new_name_wins_when_both_are_there(self):
        """Ответ со старым и новым разом — состояние переходного периода, и
        верить надо новому: старое уйдёт со следующей выкладкой соседа."""
        assert scenario_field({'name': 'Olga', 'имя': 'Другая'}, 'name') == 'Olga'

    def test_a_missing_field_gives_none(self):
        assert scenario_field({'city': 'Lviv'}, 'name') is None

    def test_a_stranger_name_gives_none_and_does_not_raise(self):
        assert scenario_field({'name': 'Olga'}, 'шпион') is None


class TestEmptyIsNotMissing:
    """⚠⚠ Пустую строку сценарий **положил**, и это ответ, а не её отсутствие.

    Статус офлайн-собеседника приходит пустым, и спроси мы запасное имя при
    пустом значении — получили бы значение из старого ключа поверх нового.
    """

    def test_an_empty_new_value_is_returned_as_is(self):
        assert scenario_field({'status_class': '', 'статус_класс': 'online'},
                              'status_class') == ''

    def test_a_zero_is_returned_as_is(self):
        assert scenario_field({'is_new': 0, 'новый': 1}, 'is_new') == 0


class TestTheMap:
    def test_every_new_name_is_latin(self):
        import re

        for name in SCENARIO_FIELD_WAS:
            assert re.fullmatch(r'[a-z][a-z0-9_]*', name), \
                f'новое имя «{name}» не латиницей'

    def test_every_old_name_is_unique(self):
        """⚠ Два новых имени на одно старое значили бы, что ответ сценария
        читается двумя ключами разом — и один из них всегда пустой."""
        was = list(SCENARIO_FIELD_WAS.values())

        assert len(was) == len(set(was)), 'старое имя переведено дважды'

    def test_one_old_name_has_one_translation(self):
        """⚠ `номер` значит разное в импорте и в переписке, но имя одно — и
        перевод у него один. Карта переводит имена, а не значения."""
        assert SCENARIO_FIELD_WAS['number'] == 'номер'


class TestTheNameForPeople:
    """⚠ Имя для журнала: сказать «шага `messages` нет» про сценарий, где шаг
    зовётся «сообщения», значит отправить человека искать несуществующее."""

    def test_it_names_both_while_the_move_is_on(self):
        assert scenario_field_name('messages') == 'messages/сообщения'

    def test_an_unknown_name_is_given_as_is(self):
        assert scenario_field_name('whatever') == 'whatever'
