"""Круг восстановления: кого трогаем, кого нет, и что пишем в базу.

Настоящий вход сюда не заходит — он водит браузер соседа и стучится на живой сайт.
Поэтому вход подменяется заглушкой, а проверяется решение: пора входить или рано.
"""

from datetime import datetime, timedelta

from src.dating.dating_revive import dating_revive_plan

NOW = datetime(2026, 8, 18, 12, 0, 0)


def _girl(**kw) -> dict:
    row = {'id': 1, 'login': 'test', 'keep_online': 1, 'logged_in_at': NOW,
           'relogin_since': None, 'relogin_tries': 0, 'relogin_next_at': None}
    row.update(kw)

    return row


class TestRevivePlan:

    def test_online_and_fresh_needs_nothing(self):
        """Вход жив и свеж — трогать нечего."""
        assert dating_revive_plan(_girl(), online=True, now=NOW)['action'] == 'none'

    def test_online_but_old_is_refreshed(self):
        """Профилактика: входу больше двадцати часов — обновляем заранее.

        И заодно заводим череду: плановый вход тоже может не выйти, а без череды
        круг лез бы за ним в браузер соседа каждую минуту.
        """
        girl = _girl(logged_in_at=NOW - timedelta(hours=21))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_since'] == NOW
        assert plan['relogin_tries'] == 1
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=5)

    def test_planned_login_failure_waits_on_ladder(self):
        """Плановый вход не вышел, опрос всё ещё online — череду не стираем.

        Опрос идёт по старым cookie и говорит «жив», но `logged_in_at` не обновился:
        значит вход через браузер не прошёл, и повторять его надо по лестнице.
        """
        girl = _girl(logged_in_at=NOW - timedelta(hours=21),
                     relogin_since=NOW - timedelta(minutes=1), relogin_tries=1,
                     relogin_next_at=NOW + timedelta(minutes=4))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] == NOW - timedelta(minutes=1)
        assert plan['relogin_tries'] == 1
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=4)

    def test_planned_login_retries_when_due(self):
        """Срок повторного планового входа настал — входим, лестница едет дальше."""
        girl = _girl(logged_in_at=NOW - timedelta(hours=21),
                     relogin_since=NOW - timedelta(minutes=6), relogin_tries=1,
                     relogin_next_at=NOW - timedelta(seconds=1))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_tries'] == 2
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=15)

    def test_offline_starts_chain(self):
        """Первый отказ начинает череду: попытка через минуту, а не сейчас."""
        plan = dating_revive_plan(_girl(), online=False, now=NOW)

        assert plan['action'] == 'wait'
        assert plan['relogin_since'] == NOW
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=1)

    def test_offline_waits_until_due(self):
        """Срок не настал — молчим, попытку не двигаем."""
        girl = _girl(relogin_since=NOW - timedelta(minutes=1),
                     relogin_next_at=NOW + timedelta(minutes=4), relogin_tries=1)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] == NOW - timedelta(minutes=1)
        assert plan['relogin_tries'] == 1
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=4)

    def test_offline_logs_in_when_due(self):
        """Срок настал — входим, и следующая попытка назначается по лестнице."""
        girl = _girl(relogin_since=NOW - timedelta(minutes=6),
                     relogin_next_at=NOW - timedelta(seconds=1), relogin_tries=1)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_tries'] == 2
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=15)

    def test_gives_up_after_a_day(self):
        """Сутки прошли — попытки прекращаются, срок следующей стирается."""
        girl = _girl(relogin_since=NOW - timedelta(hours=25),
                     relogin_next_at=NOW - timedelta(minutes=1), relogin_tries=9)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'give_up'
        assert plan['relogin_next_at'] is None

    def test_gives_up_only_once(self):
        """Сдача объявляется один раз: череда в базе останется, круг придёт ещё
        тысячу раз, а сосед и журнал должны увидеть только переход."""
        girl = _girl(relogin_since=NOW - timedelta(hours=30), relogin_next_at=None,
                     relogin_tries=12)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] == NOW - timedelta(hours=30)
        assert plan['relogin_tries'] == 12
        assert plan['relogin_next_at'] is None

    def test_recovery_clears_chain(self):
        """Вход снова живой — череда обнуляется целиком."""
        girl = _girl(relogin_since=NOW - timedelta(hours=2), relogin_tries=4,
                     relogin_next_at=NOW + timedelta(minutes=90))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'clear'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 0
        assert plan['relogin_next_at'] is None
