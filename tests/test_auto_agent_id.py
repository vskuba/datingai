"""Круг автоответа спрашивает агента анкеты, а не тащит дальше чужое имя.

## ⚠⚠ Что здесь ловится

Из круга пропала строка `agent_id = await _agent_id(profile)` — её удалили заодно
с соседней проверкой. `NameError` не случился: в модуле есть **функция** с тем же
именем, импортированная ради `_agent_id`. Имя осталось видимым, функция истинна,
проверка «агент есть?» её пропустила — и она доехала до `int(agent_id)` у соседа.

Итог: `int() argument … not 'function'` по строке в журнал на анкету, и **сутки
молчания автоответа на проде** при поднятом выключателе сайта. Круг честно находил
повод написать, отмечал попытку и умирал на походе к агенту — со стороны это
выглядело как «ответ не формируется», без единого слова об ошибке в панели.

⚠ Проверяем не «нет ли исключения», а **что именно уехало в `answer_run`**: номер,
а не что-нибудь истинное. Проверка на исключение прошла бы и на функции — падало-то
у соседа, за заглушкой.
"""

import pytest

from src.dating import auto

pytestmark = pytest.mark.chat


def _profiles(*ids) -> list:
    return [{'profile': {'id': one, 'enabled': 1, 'site_id': 999_343,
                         'profile': '{}'},
             'presence': {}} for one in ids]


@pytest.fixture
def spy(monkeypatch):
    """Подменяет поход к соседу и сам ответ; копит, с чем звали `answer_run`."""
    got = []

    async def _answer_run(profile, presence, agent, limit=0):
        got.append(agent)

        return {'asked': 1, 'sent': 1, 'silent': 0, 'offline': 0, 'errors': []}

    monkeypatch.setattr(auto, 'answer_run', _answer_run)

    return got


class TestTheAgentIsANumber:
    """⚠⚠ Главное: в `answer_run` уезжает номер агента."""

    async def test_it_is_an_int(self, monkeypatch, spy):
        async def _agent_id(profile):
            return 77

        monkeypatch.setattr(auto, '_agent_id', _agent_id)
        await auto.auto_round(_profiles(1))

        assert spy == [77]
        # ⚠ Тот самый признак: функция истинна и «непустой проверкой» проходит,
        # а номером не является. Ровно на этом круг и падал.
        assert isinstance(spy[0], int), f'в агента уехало {type(spy[0]).__name__}'

    async def test_every_profile_is_asked(self, monkeypatch, spy):
        """Агент **у каждой свой**: один на всех сложил бы факты девушек вместе."""
        asked = []

        async def _agent_id(profile):
            asked.append(int(profile['id']))

            return 100 + int(profile['id'])

        monkeypatch.setattr(auto, '_agent_id', _agent_id)
        await auto.auto_round(_profiles(1, 2, 3))

        assert asked == [1, 2, 3]
        assert spy == [101, 102, 103]

    async def test_the_module_name_is_not_reused(self):
        """⚠ Имя переменной обязано отличаться от импортированной функции.

        Совпади они снова — пропажа присваивания опять пройдёт молча, потому что
        имя останется видимым. Разные имена делают её `NameError`, то есть шумной.
        """
        import inspect

        code = inspect.getsource(auto.auto_round)

        assert 'agent_id =' not in code, \
            'переменная снова названа как импортированная функция'
        assert 'agent = await _agent_id(profile)' in code, \
            'круг перестал спрашивать агента анкеты'


class TestNoAgentMeansSilence:
    """Агента нет — молчим, а не шлём от чужого имени."""

    async def test_zero_skips_the_profile(self, monkeypatch, spy):
        async def _agent_id(profile):
            return 0

        monkeypatch.setattr(auto, '_agent_id', _agent_id)
        got = await auto.auto_round(_profiles(1))

        assert spy == [], 'анкету без агента всё-таки обошли'
        assert got['profiles'] == 0


class TestDisabledIsStillSkipped:
    """⚠ `enabled` проверяется **до** похода к соседу: тот стоит запроса."""

    async def test_disabled_never_asks_the_neighbour(self, monkeypatch, spy):
        asked = []

        async def _agent_id(profile):
            asked.append(int(profile['id']))

            return 5

        monkeypatch.setattr(auto, '_agent_id', _agent_id)
        profiles = _profiles(1)
        profiles[0]['profile']['enabled'] = 0
        await auto.auto_round(profiles)

        assert asked == [], 'за агентом выключенной анкеты всё-таки сходили'
        assert spy == []
