"""Пул не должен уходить от одной девушки, когда в сети несколько.

Вопрос простой и важный: набирается пул на два десятка карточек — от кого они
уйдут? Если от первой попавшейся, то по журналу сайта это читается как рассылка с
одной учётки, а остальные анкеты простаивают.

⚠ **Проверяется не «выпало по-разному», а сам приём**: выбор делается **на каждую
карточку заново и из всех свободных**. Первое зависит от случая — тест на удачу
краснел бы раз в сто прогонов; второе либо есть в коде, либо нет.

⚠ **У писем избранным выбора нет, и это не упущение.** Там пара задана разговором:
«ты замолчал» пишет та девушка, с которой он говорил. Случайная анкета в этом
месте написала бы незнакомому человеку про переписку, которой у них не было.
"""

import random

import pytest

from src.dating.dating_invite import _cards as invite_cards
from src.dating.dating_mailing import _pairs as mailing_pairs

pytestmark = pytest.mark.chat

GIRLS = [{'id': 1, 'login': 'a@example.com'},
         {'id': 2, 'login': 'b@example.com'},
         {'id': 3, 'login': 'c@example.com'}]

PEOPLE = [{'uid': str(100 + n), 'name': f'Man{n}'} for n in range(30)]

TEMPLATES = [{'id': 7}, {'id': 8}]


class TestEveryCardPicksAnew:
    """Выбор — на каждую карточку, а не один на заход."""

    def test_invites_choose_a_person_for_every_card(self, monkeypatch):
        """⚠⚠ **У инвайтов случай переехал с анкеты на собеседника.**

        Раньше выбирали девушку (человек был задан лентой) — теперь наоборот:
        девушки идут по очереди, а случайный тут собеседник. Ровное чередование
        анкет по журналу сайта читалось бы как рассылка, а очередь по девушкам
        делит пул поровну — это и проверяет соседний тест.
        """
        seen = _watch(monkeypatch)

        got = list(invite_cards(GIRLS, PEOPLE, set(), set()))

        assert len(got) == len(PEOPLE), 'не всем нашлась пара'
        assert len(seen) == len(PEOPLE), \
            'выбор сделан не на каждую карточку'

    def test_invites_choose_among_all_free_people(self, monkeypatch):
        """⚠ Из **всех** свободных, а не из первых двух: суженный набор — тот же
        перекос, только незаметнее.

        Набор тает на глазах: каждый выданный человек занят до конца пула
        (одинаковый текст двоим — провал), поэтому кандидатов на карточку ровно
        столько, сколько осталось.
        """
        seen = _watch(monkeypatch)

        list(invite_cards(GIRLS, PEOPLE, set(), set()))

        assert [len(one) for one in seen] == list(range(len(PEOPLE), 0, -1))

    def test_mailing_chooses_for_every_person(self, monkeypatch):
        seen = _watch(monkeypatch)

        got = list(mailing_pairs(GIRLS, PEOPLE, TEMPLATES, set()))

        assert len(got) == len(PEOPLE)
        assert len(seen) == len(PEOPLE)

    def test_mailing_chooses_among_all_pairs_of_girl_and_template(self, monkeypatch):
        """У рассылки выбирается **тройка**: анкета и шаблон вместе.

        Выбери мы их порознь, у каждой девушки был бы «свой» шаблон — то есть
        один и тот же текст от одного и того же имени.
        """
        seen = _watch(monkeypatch)

        list(mailing_pairs(GIRLS, PEOPLE, TEMPLATES, set()))

        assert {len(one) for one in seen} == {len(GIRLS) * len(TEMPLATES)}


class TestItActuallySpreads:
    """И на живом прогоне пул расходится по анкетам, а не липнет к одной.

    ⚠ Зерно случайности **фиксировано**: без него тест проверял бы удачу и раз в
    сотню прогонов краснел бы на ровном месте — ровно тот флейк, от которого сюита
    и защищается.
    """

    def test_invites_spread_over_the_girls(self):
        """⚠ У инвайтов дележ стал **точным**, а не вероятным.

        Очередь по девушкам не зависит от случая: тридцать карточек на троих —
        ровно по десять. Прежде это был перекос «не больше половины», потому что
        анкета бросалась жребием на каждого человека.
        """
        used = [girl['id'] for girl, _ in invite_cards(GIRLS, PEOPLE, set(), set())]

        assert set(used) == {1, 2, 3}, used
        assert [used.count(one) for one in (1, 2, 3)] == [10, 10, 10], used

    def test_mailing_spreads_over_the_girls(self):
        random.seed(20260913)

        used = [girl['id'] for girl, _, _ in
                mailing_pairs(GIRLS, PEOPLE, TEMPLATES, set())]

        assert set(used) == {1, 2, 3}, used
        assert max(used.count(one) for one in set(used)) < len(PEOPLE) // 2


class TestFavoriteMailKeepsItsGirl:
    """⚠ У писем избранным анкета не выбирается — и выбирать её нельзя.

    Пара задана разговором: «ты замолчал» пишет та девушка, с которой он говорил.
    Случайная анкета написала бы незнакомому человеку про переписку, которой у них
    не было.
    """

    def test_the_pair_comes_from_the_conversation(self):
        import inspect

        from src.dating import dating_favorite_mail

        code = inspect.getsource(dating_favorite_mail)

        assert 'random' not in code, \
            'у писем избранным появился случайный выбор анкеты'
        assert 'f.profile_id' in code, 'пара берётся не из самой переписки'


def _watch(monkeypatch) -> list:
    """Подсматривает, из чего и сколько раз делается выбор.

    Возвращает список наборов-кандидатов: по одному на каждый вызов.
    """
    seen = []
    real = random.choice

    def spy(rows):
        seen.append(list(rows))

        return real(rows)

    monkeypatch.setattr(random, 'choice', spy)

    return seen
