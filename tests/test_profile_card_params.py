"""Отметки «правлено руками» у параметров: имена составные.

    profile_edited = ["about", "parameters.height", "parameters.hobby"]

Механизм тот же, что у пяти прежних полей: отметка защищает поле от перезаписи
сайтом, снимается ссылкой «вернуть сайту». Новое — только составное имя.
"""

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load
from src.dating.profile_card import (PROFILE_CARD_FIELDS, PROFILE_CARD_OWN,
                                     profile_card_edited, profile_card_forget,
                                     profile_card_keep, profile_card_save)
from src.dating.profile_param import PROFILE_PARAM_KEYS

pytestmark = pytest.mark.profile


async def _card(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile FROM profile WHERE id = %s',
                         (int(profile_id),))

        return json_column_load((await db.fetchone() or {}).get('profile'))


async def _marks(profile_id: int) -> list:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile_edited FROM profile WHERE id = %s',
                         (int(profile_id),))

        return profile_card_edited((await db.fetchone() or {}).get('profile_edited'))


class TestTheFieldList:
    def test_every_parameter_is_editable(self):
        for key in PROFILE_PARAM_KEYS:
            assert f'parameters.{key}' in PROFILE_CARD_FIELDS

    def test_the_five_old_fields_are_still_there(self):
        assert PROFILE_CARD_OWN == ('id', 'name', 'age', 'city', 'about')

    def test_there_are_twenty_six_in_all(self):
        assert len(PROFILE_CARD_FIELDS) == 26


class TestKeepDropsNestedKeys:
    def test_a_marked_parameter_is_dropped(self):
        got = profile_card_keep({'parameters': {'height': '180 cm', 'eyes': 'Blue'}},
                                ['parameters.height'])

        assert got['parameters'] == {'eyes': 'Blue'}

    def test_unmarked_parameters_survive(self):
        got = profile_card_keep({'parameters': {'eyes': 'Blue'}}, ['about'])

        assert got['parameters'] == {'eyes': 'Blue'}

    def test_the_key_stays_even_when_everything_is_marked(self):
        """⚠⚠ Ключ остаётся **пустым словарём**, а не исчезает: на этом стоит
        глубокое слияние в `profile_card_merge`."""
        got = profile_card_keep({'parameters': {'height': '180 cm'}},
                                ['parameters.height'])

        assert got['parameters'] == {}

    def test_a_card_without_parameters_is_untouched(self):
        """⚠ Три прежних писателя ключа `parameters` не присылают вовсе."""
        got = profile_card_keep({'name': 'Olga'}, ['parameters.height'])

        assert got == {'name': 'Olga'}

    def test_parameters_at_passes_through(self):
        got = profile_card_keep({'parameters_at': '2026-10-12 16:32:00',
                                 'parameters': {}}, [])

        assert got['parameters_at'] == '2026-10-12 16:32:00'


class TestEditedKnowsCompositeNames:
    def test_a_parameter_mark_survives_the_filter(self):
        assert profile_card_edited(['parameters.height']) == ['parameters.height']

    def test_a_stranger_name_is_dropped(self):
        assert profile_card_edited(['parameters.шпион']) == []


class TestSavingParameters:
    async def test_a_changed_parameter_is_marked(self, girl):
        await profile_card_save(girl, {'login': f'kept-{girl}@example.com',
                                       'parameters': {'eyes': 'Hazel'}})

        assert 'parameters.eyes' in await _marks(girl)
        assert (await _card(girl))['parameters']['eyes'] == 'Hazel'

    async def test_an_unchanged_parameter_is_not_marked(self, girl):
        """⚠ Метится изменённое, а не присланное: «открыл поглядеть и сохранил»
        не должно заморозить все 21 поле разом."""
        login = f'kept-{girl}@example.com'
        await profile_card_save(girl, {'login': login,
                                       'parameters': {'eyes': 'Hazel'}})
        await profile_card_save(girl, {'login': login,
                                       'parameters': {'eyes': 'Hazel',
                                                      'hair': 'Red'}})

        marks = await _marks(girl)

        assert 'parameters.hair' in marks
        assert marks.count('parameters.eyes') == 1, 'отметка задвоилась'

    async def test_the_frame_is_whole_after_saving(self, girl):
        """⚠⚠ Сверяется **состав**, а не порядок: MySQL пересортировывает ключи
        объекта внутри JSON-колонки по-своему, и сохранённый порядок не наш.

        Полагаться на него негде и не надо: порядок задаёт реестр — его держат
        `profile_param_bio` (строки био) и `profile_param_frame` (ответ ручки,
        по которому страница рисует строки окна).
        """
        await profile_card_save(girl, {'login': f'kept-{girl}@example.com',
                                       'parameters': {'eyes': 'Hazel'}})

        assert set((await _card(girl))['parameters']) == set(PROFILE_PARAM_KEYS)

    async def test_strangers_are_not_saved(self, girl):
        await profile_card_save(girl, {'login': f'kept-{girl}@example.com',
                                       'parameters': {'шпион': 'да'}})

        assert 'шпион' not in (await _card(girl))['parameters']

    async def test_no_parameters_key_means_do_not_touch(self, girl):
        login = f'kept-{girl}@example.com'
        await profile_card_save(girl, {'login': login,
                                       'parameters': {'eyes': 'Hazel'}})
        await profile_card_save(girl, {'login': login, 'name': 'Olga'})

        assert (await _card(girl))['parameters']['eyes'] == 'Hazel'

    async def test_the_mark_can_be_given_back_to_the_site(self, girl):
        await profile_card_save(girl, {'login': f'kept-{girl}@example.com',
                                       'parameters': {'eyes': 'Hazel'}})

        left = await profile_card_forget(girl, 'parameters.eyes')

        assert 'parameters.eyes' not in left
        assert (await _card(girl))['parameters']['eyes'] == 'Hazel', \
            'значение должно остаться — обновит его следующее чтение панели'
