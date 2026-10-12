"""Регистр имени собеседника: каждое слово с большой буквы, остальные строчные.

Сайт отдаёт имена как попало — «VINCENT VEGA», «DirK», «zaoui». Имя не просто
показывается: оно подставляется меткой прямо в текст письма и инвайта, и «Hi
VINCENT» уходит живому человеку. Поэтому регистр правится **на входе**, у каждой
двери, которой имя попадает к нам, а не на показе.

⚠ Здесь проверяется правило (`people_name_case`) и то, что каждая дверь его
зовёт. Дверей семь, и забытая — это не сломанный тест, а одна ветка данных,
которая молча несёт крик: её ловит `TestDoors`.
"""

import inspect
import pytest

from src.dating.people import people_name_case, people_rows

pytestmark = pytest.mark.profile


class TestNameCase:
    """Само правило."""

    @pytest.mark.parametrize('was, becomes', [
        ('VINCENT VEGA', 'Vincent Vega'),
        ('ViNcEnT', 'Vincent'),
        ('zaoui', 'Zaoui'),
        ('Barry Schwartz', 'Barry Schwartz'),
        ('Mr Michael J T Williams', 'Mr Michael J T Williams'),
    ])
    def test_word_by_word(self, was, becomes):
        assert people_name_case(was) == becomes

    @pytest.mark.parametrize('was, becomes', [
        ("o'NEILL", "O'Neill"),
        ('anna-maria', 'Anna-Maria'),
        ('JEAN-LUC PICARD', 'Jean-Luc Picard'),
    ])
    def test_hyphen_and_apostrophe_start_a_word(self, was, becomes):
        """⚠ Дефис и апостроф — **границы слова**, а не знаки внутри него.

        Иначе «jean-luc» стало бы «Jean-luc»: вторая половина двойного имени
        осталась бы строчной, и это заметно сильнее, чем исходный крик.
        """
        assert people_name_case(was) == becomes

    def test_spaces_collapse(self):
        """Хвосты и двойные пробелы уходят: имя подставляется в текст письма."""
        assert people_name_case('  Frank   Miller ') == 'Frank Miller'

    @pytest.mark.parametrize('was, becomes', [
        ('許萊納', '許萊納'),
        ('vega3d', 'Vega3d'),
        ('', ''),
        (None, ''),
    ])
    def test_nothing_to_change(self, was, becomes):
        """Нелатиница, цифры и пустота проходят насквозь.

        ⚠ `None` тоже: имя приходит не всегда, и падать на его отсутствии значило
        бы терять карточку целиком из-за пустого поля.
        """
        assert people_name_case(was) == becomes

    def test_twice_is_the_same(self):
        """Правило устойчиво: второй проход ничего не меняет.

        Это не придирка — так сверялись разбор на Python и уборка старых имён на
        SQL в миграции `20260918120000_name_case`. Разойдись они, круг правил бы
        имя туда-сюда на каждом заходе.
        """
        once = people_name_case('ALEJANDRO suarez M')

        assert people_name_case(once) == once


class TestDoors:
    """Каждая дверь снаружи зовёт правило."""

    def test_scenario_list(self):
        """Список собеседников сценарием — `people_rows`."""
        rows = people_rows({'все': [
            {'name': 'VINCENT PITCHEN\n,\xa0\n49',
             'link': 'https://ladadate.com/profile/812886-men-from-france',
             'photo': '', 'status_class': '', 'is_new': ''}]}, 'все')

        assert rows[0]['name'] == 'Vincent Pitchen'

    @pytest.mark.parametrize('module, place', [
        ('src.dating.people_api', 'списки собеседников через API сайта'),
        ('src.dating.favorite', 'favorites'),
        ('src.dating.profile_cache', 'снимок собеседников кабинета'),
        ('src.dating.chat_panel', 'переписка из панели агентства'),
        ('src.dating.dialog', 'переписка со страницы чата'),
        # ⚠⚠ Дверь карточки — `companion_card`, а не роутер. Прежде регистр
        # правила сама ручка `POST`, и потому её и проверяли здесь; теперь
        # карточку пишет одна дверь (`companion_save`), и правило стоит в ней —
        # разом за ручку ленты, за забор избранных и за `companion_touch`,
        # который его как раз **не** звал и нёс «VINCENT» в письмо.
        ('src.dating.companion_card', 'карточка собеседника'),
    ])
    def test_every_door_calls_it(self, module, place):
        """⚠ Проверка по исходнику, а не по вызову: дверь ведёт наружу.

        Позвать `profile_cache` или `chat_panel` по-настоящему — это сходить на
        сайт; заглушки на каждую из шести дверей стоили бы дороже самой правки, а
        ловили бы то же самое. Вопрос здесь ровно один: правило в этом файле
        **зовётся**. Пропала строка — пропал и регистр, и тест это видит.
        """
        source = inspect.getsource(__import__(module, fromlist=['_']))

        assert f'people_name_case(' in source, f'{place}: имя идёт мимо правила'
