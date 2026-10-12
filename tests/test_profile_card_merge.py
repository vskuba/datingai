"""Дверь «сайт принёс карточку»: слияние, уважающее правку руками.

## ⚠⚠ Почему одна функция, а не три копии

Путь был написан дважды и слово в слово — у чтения страницы кабинета
(`profile_page.py`) и у кэша анкеты (`profile_cache.py`); отличались они только
фразой в журнале, и в докстринге обоих стояло «правятся парой». Чтение панели
стало бы третьей копией.

⚠ Фраза журнала приезжает доводом `note` именно поэтому: она и была всей разницей.
Обёрток, подставляющих её у вызывающих, нет — иначе то же дело снова лежало бы в
трёх файлах, два из которых ничего не делают.
"""

import json

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load
from src.dating.profile_card import profile_card_merge
from src.dating.profile_param import PROFILE_PARAM_KEYS

pytestmark = pytest.mark.profile


async def _put(profile_id: int, card: dict, marks=None) -> None:
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE profile SET profile = %s, profile_edited = %s WHERE id = %s',
            (json.dumps(card, ensure_ascii=False),
             json.dumps(marks) if marks else None, int(profile_id)))
        await db.connection.commit()


async def _card(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile FROM profile WHERE id = %s',
                         (int(profile_id),))

        return json_column_load((await db.fetchone() or {}).get('profile'))


class TestItRespectsTheMarks:
    async def test_a_marked_field_is_not_overwritten(self, girl):
        await _put(girl, {'name': 'Olga', 'city': 'Lviv'}, ['name'])

        await profile_card_merge(girl, {'name': 'Другая', 'city': 'Kyiv'}, 'тест')

        got = await _card(girl)

        assert got['name'] == 'Olga', 'правленое руками затёрто'
        assert got['city'] == 'Kyiv'

    async def test_a_marked_parameter_is_not_overwritten(self, girl):
        await _put(girl, {'parameters': {'height': '180 cm', 'eyes': 'Blue'}},
                   ['parameters.height'])

        await profile_card_merge(
            girl, {'parameters': {'height': '172 cm', 'eyes': 'Hazel'}}, 'тест')

        got = (await _card(girl))['parameters']

        assert got['height'] == '180 cm', 'правленый руками рост затёрт'
        assert got['eyes'] == 'Hazel'


class TestTheDeepMerge:
    """⚠⚠ Плоское слияние стирало бы правленое руками целиком."""

    async def test_everything_marked_survives(self, girl):
        """Все 21 поля правлены руками: пришедшее даёт пустой `parameters`, и
        плоское `{**before, **kept}` положило бы его поверх полного."""
        full = {key: f'{key}-ручной' for key in PROFILE_PARAM_KEYS}
        marks = [f'parameters.{key}' for key in PROFILE_PARAM_KEYS]
        await _put(girl, {'parameters': full}, marks)

        await profile_card_merge(
            girl, {'parameters': {key: 'с-сайта' for key in PROFILE_PARAM_KEYS}},
            'тест')

        got = (await _card(girl))['parameters']

        assert got == full, 'правленое руками стёрто пустым словарём'

    async def test_old_parameters_survive_a_partial_fresh(self, girl):
        await _put(girl, {'parameters': {'eyes': 'Hazel', 'hair': 'Red'}})

        await profile_card_merge(girl, {'parameters': {'eyes': 'Blue'}}, 'тест')

        got = (await _card(girl))['parameters']

        assert got == {'eyes': 'Blue', 'hair': 'Red'}


class TestOtherWritersDoNotLoseParameters:
    """⚠ Страница кабинета и кэш анкеты ключа `parameters` не присылают."""

    async def test_a_card_without_parameters_leaves_them_alone(self, girl):
        await _put(girl, {'name': 'Olga', 'parameters': {'eyes': 'Hazel'}})

        await profile_card_merge(girl, {'name': 'Другая'}, 'тест')

        got = await _card(girl)

        assert got['name'] == 'Другая'
        assert got['parameters'] == {'eyes': 'Hazel'}


class TestImportKeepsParametersItsOwnWay:
    """⚠⚠ Импорт через `profile_card_merge` **не** ходит, и это не упущение.

    У него решение и запись разведены намеренно: `import_plan` — чистая функция,
    она только планирует карточку (`{**before, **fresh}`), а `UPDATE` делает роутер
    импорта. Параметры переживают его потому, что `before` — прежняя карточка
    целиком. Проверяем именно это: своего ключа импорт не присылает, прежний обязан
    уцелеть.
    """

    def test_the_planned_card_keeps_the_old_parameters(self):
        from src.dating.import_ import import_plan

        found = {'id': 1, 'login': 'olga@example.com',
                 'card': {'name': 'Olga', 'parameters': {'eyes': 'Hazel'}},
                 'edited': None}

        got = import_plan({'login': 'olga@example.com', 'name': 'Другая'},
                          found, 'https://site.test')

        assert got['card']['parameters'] == {'eyes': 'Hazel'}, \
            'импорт потерял параметры — он кладёт карточку поверх прежней'


class TestItNeverRaises:
    async def test_a_missing_profile_gives_an_empty_dict(self):
        """⚠ Не бросает: вход и «Проверка» зовут её последним шагом, и терять
        открытую сессию из-за неудачной записи незачем."""
        assert await profile_card_merge(10_000_000, {'name': 'x'}, 'тест') == {}
