"""Каждой девушке — свой текст инвайта, и карточки в пуле перемешаны.

## ⚠⚠ Зачем

Девушек в сети бывает дюжина, и одна реплика на всех выдавала бы агентство: разные
имена, слово в слово одно сообщение, одна минута.

Лечили это сперва тем, что заход шёл от **одной** анкеты — помогало, но упирало пул
в неё одну: остальные ждали следующего круга. Теперь панель просит у агента
столько текстов, сколько девушек, и получает их **в одном вызове**: дюжина вызовов
вернула бы цену, ради ухода от которой шаблон и заводили.

Разными их делает не жребий, а прямое требование к модели и **судья у соседа**,
который сверяет тексты между собой.

⚠ **Наружу сюита не ходит** (`testing.md`): агент подменяется заглушкой.
"""

import inspect

import pytest

from src.dating import invite
from src.dating.mail_agent import _invites

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


class TestManyTemplates:
    """Сколько текстов просим и как раздаём."""

    async def test_as_many_as_girls(self, monkeypatch):
        seen = {}

        async def _agent(site_id, language, **kwargs):
            seen.update(kwargs)

            return [f'текст {n}' for n in range(kwargs['how_many'])]

        _quiet(monkeypatch, _agent)
        girls = [{'id': one, 'profile': '{}'} for one in (1, 2, 3)]
        monkeypatch.setattr(invite, '_girls', _answer(girls))

        await invite.invite_make(STAND_SITE, 10)

        assert seen['how_many'] == 3, 'просим не по числу девушек'

    async def test_each_girl_gets_her_own(self, monkeypatch):
        """Три девушки — три разных текста, и каждой достаётся свой."""
        async def _agent(site_id, language, **kwargs):
            return [f'текст {n}' for n in range(kwargs['how_many'])]

        put = _quiet(monkeypatch, _agent)
        monkeypatch.setattr(invite, '_girls', _answer(
            [{'id': one, 'profile': '{}'} for one in (1, 2, 3)]))
        monkeypatch.setattr(invite, '_people', _answer(
            [{'uid': f'u{one}', 'name': f'M{one}'} for one in range(1, 7)]))

        # ⚠ Потолок — по числу **пар**, а не людей: карточек три девушки × шесть
        # человек, и перед укладкой они перемешиваются. Возьми мы шесть из
        # восемнадцати, тест краснел бы сам по себе — в выборку могла не попасть
        # целая анкета. Измерено: 3 падения из 40 прогонов.
        #
        # Проверяем здесь «каждой свой текст», а не обрезку пула потолком — её
        # держит `TestShuffle`, и там запас взят по той же причине.
        await invite.invite_make(STAND_SITE, 3 * 6)

        by_girl = {}
        for girl_id, text in put:
            by_girl.setdefault(girl_id, set()).add(text)

        assert len(by_girl) == 3, by_girl
        # ⚠ У одной девушки текст один: её карточки в пуле одинаковы, и это
        # законно — каждый человек получает ровно одну.
        assert all(len(texts) == 1 for texts in by_girl.values()), by_girl
        # А между девушками — разные.
        assert len({next(iter(texts)) for texts in by_girl.values()}) == 3, by_girl

    async def test_fewer_templates_are_spread_round(self, monkeypatch):
        """⚠ Агент прислал меньше, чем просили (судья забраковал часть).

        Остатку девушек тексты достаются по кругу: это лучше, чем оставить их без
        инвайтов вовсе, и всё равно лучше прежнего — там один текст был на всех.
        """
        async def _agent(site_id, language, **kwargs):
            return ['один', 'два']

        put = _quiet(monkeypatch, _agent)
        monkeypatch.setattr(invite, '_girls', _answer(
            [{'id': one, 'profile': '{}'} for one in (1, 2, 3, 4)]))
        monkeypatch.setattr(invite, '_people', _answer(
            [{'uid': f'u{one}', 'name': f'M{one}'} for one in range(1, 9)]))

        # ⚠ Потолок берём с запасом — по числу **пар**, а не людей: карточек
        # теперь четыре девушки × восемь человек, и они перемешаны. Возьми мы
        # восемь из тридцати двух, тест краснел бы раз в сотню прогонов на ровном
        # месте: в выборку могла не попасть одна из анкет.
        await invite.invite_make(STAND_SITE, 4 * 8)

        assert {text for _, text in put} == {'один', 'два'}
        assert len({girl for girl, _ in put}) == 4, 'кто-то остался без инвайтов'


class TestShuffle:
    """⚠⚠ Карточки перемешиваются перед укладкой в пул."""

    def test_the_maker_shuffles(self):
        """Без этого пул, обрывающийся на потолке, доставался бы первым двум-трём
        анкетам целиком: `_cards` выдаёт их очередью по девушкам."""
        code = inspect.getsource(invite.invite_make)

        assert 'random.shuffle(' in code, 'карточки больше не перемешиваются'
        assert code.index('random.shuffle(') < code.index('for girl, person in cards:'), \
            'перемешивание после цикла — оно ничего не меняет'

    async def test_the_pool_is_not_one_girl(self, monkeypatch):
        """Потолок меньше числа кандидатов — в пул попадают разные девушки.

        ⚠ Проверка вероятностная по своей природе, поэтому потолок взят с запасом:
        девять мест на четыре анкеты. Выпасть одной и той же четыре раза подряд
        можно, девять — практически нет.
        """
        async def _agent(site_id, language, **kwargs):
            return [f'текст {n}' for n in range(kwargs['how_many'])]

        put = _quiet(monkeypatch, _agent)
        monkeypatch.setattr(invite, '_girls', _answer(
            [{'id': one, 'profile': '{}'} for one in (1, 2, 3, 4)]))
        monkeypatch.setattr(invite, '_people', _answer(
            [{'uid': f'u{one}', 'name': f'M{one}'} for one in range(1, 30)]))

        await invite.invite_make(STAND_SITE, 9)

        assert len({girl for girl, _ in put}) >= 3, \
            'пул достался одной-двум анкетам — перемешивание не работает'


class TestParsingMany:
    """Разбор ответа: тексты разделены строкой из дефисов."""

    def test_one_text_still_works(self):
        """⚠ Просили один — приходит один, без разделителей. Старый вид законен."""
        assert _invites('VERDICT: yes\nWHY: \nTEXT: Спорим, ты улыбнулся?') \
            == ['Спорим, ты улыбнулся?']

    def test_many_are_split(self):
        got = _invites('VERDICT: yes\nWHY: \nTEXT: Раз.\n---\nДва.\n---\nТри.')

        assert got == ['Раз.', 'Два.', 'Три.']

    def test_a_dash_inside_a_phrase_is_not_a_split(self):
        """⚠ «—» внутри реплики встречается постоянно; делить по нему — рвать текст."""
        got = _invites('VERDICT: yes\nWHY: \nTEXT: Скажи — ты из тех, кто краснеет?')

        assert got == ['Скажи — ты из тех, кто краснеет?']

    def test_the_judge_kills_them_all(self):
        """⚠ Вердикт про весь ответ целиком: делить забракованное незачем."""
        assert _invites('VERDICT: no\nWHY: похожи\nTEXT: Раз.\n---\nДва.') == []

    def test_one_bad_does_not_kill_the_rest(self):
        """⚠ Проверки формы — каждому тексту поодиночке.

        Иначе одна выдуманная метка оставляла бы без инвайтов весь заход.
        """
        got = _invites('VERDICT: yes\nWHY: \n'
                       'TEXT: Годный текст.\n---\nHi from [[his_city]]')

        assert got == ['Годный текст.']


def _answer(value):
    async def _got(*args, **kwargs):
        return value

    return _got


def _quiet(monkeypatch, agent) -> list:
    """Заглушает всё, что ходит наружу, и копит уложенное в пул.

    Returns:
        list: пары `(номер анкеты, текст)` — то, что легло бы в базу.
    """
    put = []

    async def _put(site_id, girl, person, text):
        put.append((int(girl['id']), text))

        return True

    monkeypatch.setattr(invite, 'invite_template', agent)
    monkeypatch.setattr(invite, '_put', _put)
    monkeypatch.setattr(invite, '_girls', _answer([{'id': 1, 'profile': '{}'}]))
    monkeypatch.setattr(invite, '_people', _answer(
        [{'uid': 'u1', 'name': 'Brian'}, {'uid': 'u2', 'name': 'Don'}]))
    monkeypatch.setattr(invite, '_invited', _answer(set()))
    monkeypatch.setattr(invite, '_talked', _answer(set()))
    monkeypatch.setattr(invite, '_pool_uids', _answer(set()))
    monkeypatch.setattr(invite, 'block_pairs', _answer(set()))
    monkeypatch.setattr(invite, '_language', _answer('en'))
    monkeypatch.setattr(invite, '_avoid', _answer([]))
    # ⚠ Доля имени — **строка процентов**, и подменяется она потому, что читает
    # настройку сайта. Прежних `_country` и `_time_chance` больше нет: страна и
    # время из инвайта убраны, а жребий приёма и длины бросает сосед.
    monkeypatch.setattr(invite, '_man_percent', _answer('0'))

    return put
