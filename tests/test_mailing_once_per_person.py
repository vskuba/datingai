"""Один шаблон рассылки уходит человеку ровно раз — кем бы ни была девушка.

## ⚠⚠ Что было не так

Ключом была тройка «анкета — адресат — шаблон»: не больше письма от **этой**
анкеты **этому** человеку **этим** шаблоном. Разным девушкам правило не мешало —
тройки-то разные, — и один и тот же текст мог прийти человеку дважды: от Дианы и
от Екатерины.

Внутри одного захода этого не случалось: каждый адресат получает одно письмо за
проход. Но заходов много — круг добирает пул по расписанию, — и на следующем
человек снова оказывался свободен, только уже для другой анкеты.

Со стороны получателя это и есть **выданное агентство**: два одинаковых письма от
двух незнакомок. Тот же довод, по которому собеседники уникальны в пуле инвайтов.

⚠ **Разные шаблоны от разных девушек — можно.** У них разный текст, и второе
письмо читается как ещё одно знакомство, а не как копия. Стережём здесь именно
повтор текста, а не второе письмо вообще.
"""

import pytest

from src.dating.mailing import _pairs

pytestmark = pytest.mark.mail

GIRLS = [{'id': 1}, {'id': 2}, {'id': 3}]
PEOPLE = [{'uid': 'a'}, {'uid': 'b'}]
TEMPLATES = [{'id': 10}, {'id': 20}]


class TestOneTemplatePerPerson:
    """Потраченная пара «человек — шаблон» больше не выпадает."""

    def test_a_spent_template_is_not_offered_to_another_girl(self):
        """Шаблон №10 уже уходил человеку «a» — от кого угодно.

        Значит ни одна девушка не должна получить его для «a» снова.
        """
        got = list(_pairs(GIRLS, PEOPLE, TEMPLATES, set(),
                          spent={('a', 10)}))

        for girl, template, person in got:
            assert not (person['uid'] == 'a' and template['id'] == 10), \
                f"анкета #{girl['id']} снова шлёт шаблон 10 человеку «a»"

    def test_the_same_template_stays_free_for_other_people(self):
        """Потрачен он **на этого человека**, а не вообще."""
        got = list(_pairs(GIRLS, PEOPLE, TEMPLATES, set(), spent={('a', 10)}))
        b_templates = {t['id'] for _, t, p in got if p['uid'] == 'b'}

        assert b_templates, 'человек «b» остался без письма'

    def test_other_templates_stay_free_for_the_same_person(self):
        """⚠ Запрет — на текст, а не на человека: вторым шаблоном писать можно."""
        got = list(_pairs(GIRLS, [{'uid': 'a'}], TEMPLATES, set(),
                          spent={('a', 10)}))

        assert [t['id'] for _, t, _ in got] == [20], got

    def test_all_templates_spent_means_no_letter(self):
        """Оба текста человек уже получил — писать нечем, и он пропускается."""
        got = list(_pairs(GIRLS, [{'uid': 'a'}], TEMPLATES, set(),
                          spent={('a', 10), ('a', 20)}))

        assert got == []


class TestTheOldRuleStillHolds:
    """Прежние правила не отменены — добавлено одно."""

    def test_a_taken_triple_is_still_skipped(self):
        """Тройка «анкета — адресат — шаблон» по-прежнему тратится."""
        taken = {(1, 'a', 10), (2, 'a', 10), (3, 'a', 10)}
        got = list(_pairs(GIRLS, [{'uid': 'a'}], [{'id': 10}], taken))

        assert got == []

    def test_a_block_takes_the_girl_not_the_person(self):
        """⚠ Закрылся от одной — от остальных письмо уйдёт: они ему не писали."""
        got = list(_pairs(GIRLS, [{'uid': 'a'}], [{'id': 10}], set(),
                          blocked={(1, 'a')}))

        assert got and all(girl['id'] != 1 for girl, _, _ in got), got

    def test_one_letter_per_person_per_pass(self):
        """Каждый адресат получает одну карточку за проход — это не менялось."""
        got = list(_pairs(GIRLS, PEOPLE, TEMPLATES, set()))

        assert sorted(p['uid'] for _, _, p in got) == ['a', 'b']
