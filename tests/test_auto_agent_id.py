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
    """Подменяет поход к соседу и сам ответ; копит, с чем звали `answer_run`.

    ⚠ Выключатель сайта поднимается здесь же. Эти тесты про то, **что уезжает в
    `answer_run`**, а не про право отвечать; без подмены они спрашивали бы базу и
    падали от того, что у тестового сайта галочка снята, — то есть по причине, к их
    вопросу не относящейся. Само право проверяется ниже, `TestTheSiteSwitch`.
    """
    got = []

    async def _answer_run(profile, presence, agent, limit=0):
        got.append(agent)

        return {'asked': 1, 'sent': 1, 'silent': 0, 'offline': 0, 'errors': []}

    async def _answer_on(site_id):
        return True

    monkeypatch.setattr(auto, 'answer_run', _answer_run)
    monkeypatch.setattr(auto, '_answer_on', _answer_on)

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


class TestTheSiteSwitch:
    """⚠⚠ Галочка сайта «отправка ответов» обязана выключать автоответ.

    Она уже переставала работать однажды, и молча: убирая вторую проверку (флаг
    анкеты `auto_answer`), потеряли и первую — `_answer_on` остался в файле, но
    звать его перестали. Панель галочку исправно сохраняла, круг отвечал.

    ⚠ Сторона ошибки здесь обратная обычной: не «агент молчит, когда должен
    говорить», а «пишет живому человеку, когда человек это запретил». Отменить
    ушедшее нельзя, поэтому тест отдельный и проверяет обе стороны выключателя.
    """

    @pytest.fixture
    def only_answer(self, monkeypatch):
        """Ответ подменён, выключатель — нет: он и есть предмет проверки."""
        got = []

        async def _answer_run(profile, presence, agent, limit=0):
            got.append(int(profile['id']))

            return {'asked': 1, 'sent': 1, 'silent': 0, 'offline': 0, 'errors': []}

        async def _agent_id(profile):
            return 42

        monkeypatch.setattr(auto, 'answer_run', _answer_run)
        monkeypatch.setattr(auto, '_agent_id', _agent_id)

        return got

    async def test_the_switch_off_keeps_us_silent(self, monkeypatch, only_answer):
        async def _answer_on(site_id):
            return False

        monkeypatch.setattr(auto, '_answer_on', _answer_on)
        got = await auto.auto_round(_profiles(1, 2))

        assert only_answer == [], 'галочка снята, а ответ всё равно ушёл'
        assert got['profiles'] == 0

    async def test_the_switch_on_lets_us_answer(self, monkeypatch, only_answer):
        async def _answer_on(site_id):
            return True

        monkeypatch.setattr(auto, '_answer_on', _answer_on)
        await auto.auto_round(_profiles(1, 2))

        assert only_answer == [1, 2], 'галочка поднята, а ответа нет'

    async def test_the_switch_is_asked_before_the_neighbour(self, monkeypatch,
                                                           only_answer):
        """⚠ Порядок: выключатель дешевле похода к соседу за агентом."""
        asked = []

        async def _agent_id(profile):
            asked.append(int(profile['id']))

            return 42

        async def _answer_on(site_id):
            return False

        monkeypatch.setattr(auto, '_agent_id', _agent_id)
        monkeypatch.setattr(auto, '_answer_on', _answer_on)
        await auto.auto_round(_profiles(1))

        assert asked == [], 'за агентом сходили при снятой галочке'
