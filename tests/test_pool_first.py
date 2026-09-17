"""Выбранный в ленте «Все онлайн» уходит первым в очереди набора пула.

⚠ **Перестановка, а не отбор.** Человек выбрал карточку и нажал «Генерировать» —
он ждёт письма (реплику) именно этому, а набор берёт людей лентой и мог бы не
дойти до него вовсе. Но остальные адресаты в очереди остаются: сделай мы из
выбора фильтр, заход останавливался бы после первой готовой пары, а человек
выбирал одного, а не отменял прочих.

⚠ **«Уже в пуле» здесь не проверяется**, и это не забывчивость: проверка стоит
дальше по дороге — инвайт пропускает того, кому реплику готовили (`_invited`),
рассылка — занятую тройку (`_taken`). Поставленный первым, но занятый, будет
пропущен, как и раньше.
"""

import pytest

from src.dating.mailing import mailing_people_first

pytestmark = pytest.mark.mail

FEED = [{'uid': 'a', 'name': 'Первый'},
         {'uid': 'b', 'name': 'Второй'},
         {'uid': 'c', 'name': 'Третий'}]


class TestFirst:

    def test_chosen_goes_to_the_head(self):
        got = mailing_people_first(FEED, 'c')

        assert [one['uid'] for one in got] == ['c', 'a', 'b']

    def test_others_stay(self):
        """⚠ Отбор вместо перестановки остановил бы заход после первой пары."""
        got = mailing_people_first(FEED, 'b')

        assert len(got) == len(FEED)
        assert {one['uid'] for one in got} == {'a', 'b', 'c'}

    def test_order_of_the_rest_is_kept(self):
        """Лента приходит от сайта в своём порядке; тасовать её сверх нужного —
        значит давать разных адресатов двум заходам подряд без причины."""
        got = mailing_people_first(FEED, 'b')

        assert [one['uid'] for one in got] == ['b', 'a', 'c']

    @pytest.mark.parametrize('uid', ['', None, '   '])
    def test_no_choice_changes_nothing(self, uid):
        assert mailing_people_first(FEED, uid) == FEED

    def test_stranger_changes_nothing(self):
        """⚠ Выбор живёт в браузере и переживает смену сайта и обновление ленты:
        ушедший из сети не повод переставлять очередь."""
        assert mailing_people_first(FEED, 'нет-такого') == FEED

    def test_empty_feed_survives(self):
        assert mailing_people_first([], 'a') == []
