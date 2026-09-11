"""Лестница попыток автовхода — чистая арифметика, без сети и базы.

Проверяется тестами, потому что цена ошибки несимметрична: слишком частые попытки
долбят сайт настоящим входом через браузер, слишком редкие оставляют анкету
молчащей на часы.
"""

import pytest

from datetime import datetime, timedelta

from src.dating.dating_revive import (DATING_REVIVE_GIVE_UP_SEC, dating_revive_delay,
                                      dating_revive_due, dating_revive_state)

pytestmark = pytest.mark.profile

NOW = datetime(2026, 8, 18, 12, 0, 0)


class TestReviveDelay:

    def test_ladder_is_exact(self):
        """1, 5, 15, 45, 90 минут — ровно в таком порядке."""
        assert [dating_revive_delay(n) // 60 for n in range(5)] == [1, 5, 15, 45, 90]

    def test_tail_is_two_hours(self):
        """После лестницы — каждые два часа, сколько бы попыток ни прошло."""
        assert dating_revive_delay(5) // 60 == 120
        assert dating_revive_delay(12) // 60 == 120

    def test_negative_tries_is_first_step(self):
        """Битое значение из базы не должно давать отрицательную паузу."""
        assert dating_revive_delay(-3) // 60 == 1


class TestReviveState:

    def test_no_chain_is_ok(self):
        assert dating_revive_state(None, NOW) == 'ok'

    def test_fresh_chain_is_failing(self):
        assert dating_revive_state(NOW - timedelta(hours=3), NOW) == 'failing'

    def test_day_old_chain_is_given_up(self):
        """Сутки — рубеж: дальше автомат не пробует, и карточка перестаёт мигать."""
        old = NOW - timedelta(seconds=DATING_REVIVE_GIVE_UP_SEC + 1)
        assert dating_revive_state(old, NOW) == 'given_up'

    def test_exactly_a_day_is_given_up(self):
        old = NOW - timedelta(seconds=DATING_REVIVE_GIVE_UP_SEC)
        assert dating_revive_state(old, NOW) == 'given_up'


class TestReviveDue:

    def test_no_time_set_is_not_due(self):
        assert dating_revive_due(None, NOW) is False

    def test_past_time_is_due(self):
        assert dating_revive_due(NOW - timedelta(seconds=1), NOW) is True

    def test_future_time_is_not_due(self):
        assert dating_revive_due(NOW + timedelta(minutes=5), NOW) is False
