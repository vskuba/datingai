"""Повторы входа: сколько раз пробовать и с какой паузой — настройками сайта.

## ⚠⚠ Живая беда: девять входов за четырнадцать минут

Анкета #41 (Ladadate) на проде входила **каждые 86 секунд**, безостановочно:
сценарий проходил форму, сайт кабинет не подтверждал, круг расписания пробовал
снова. Ровно то, за что сайт закрывает вход по внешнему адресу, — и ровно та
беда, из-за которой мы уже просидели сутки в блокировке.

## ⚠ Лестница отката это не поймала, и вот почему

Лестница пауз (`dating_revive.py`) существовала, работала и к этому входу
**отношения не имела**: её спрашивает круг восстановления, а входил круг
расписания — прямым вызовом `dating_login_run`, мимо всех правил.

Отсюда главное в устройстве: **правило стоит у самой двери**. Не в круге, не в
трёх кругах, а во входе. Обойти его нельзя, не переписав вход.
"""

import pytest

from src.dating.dating_login_retry import (DATING_LOGIN_RETRY_EVERY_DEFAULT,
                                           DATING_LOGIN_RETRY_MAX_DEFAULT,
                                           DatingLoginHeld,
                                           dating_login_retry_allowed)

pytestmark = pytest.mark.profile


def _profile(tries: int, last=None, site_id: int = 1):
    return {'id': 1, 'site_id': site_id, 'login_fail_tries': tries,
            'login_fail_at': last}


class TestFirstTryIsFree:
    """Первая попытка идёт без вопросов: пауза — это реакция на неудачу."""

    async def test_a_clean_profile_may_log_in(self):
        allowed, why = await dating_login_retry_allowed(_profile(0))

        assert allowed and why == ''

    async def test_a_failure_without_time_does_not_block(self):
        """⚠ Счётчик есть, времени нет — пускаем.

        Такое бывает у анкеты, чей счётчик пришёл из миграции или был поправлен
        руками. Держать вход по половине состояния значило бы запереть анкету
        молча и навсегда.
        """
        allowed, _ = await dating_login_retry_allowed(_profile(1, last=None))

        assert allowed


class TestPauseBetweenTries:
    """Пауза считается от последней неудачи и названа в причине словами."""

    async def test_right_after_a_failure_it_holds(self):
        from datetime_.datetime_ import datetime_utc_now

        allowed, why = await dating_login_retry_allowed(
            _profile(1, last=datetime_utc_now()))

        assert not allowed
        assert 'осталось' in why, why
        # ⚠ В причине обязан стоять номер попытки: человек по журналу должен
        # понимать, близко ли предел, а не только «опять ждём».
        assert 'Попытка 2 из' in why, why

    async def test_after_the_pause_it_lets_through(self):
        from datetime import timedelta

        from datetime_.datetime_ import datetime_utc_now

        long_ago = datetime_utc_now() - timedelta(
            seconds=DATING_LOGIN_RETRY_EVERY_DEFAULT + 60)
        allowed, _ = await dating_login_retry_allowed(_profile(1, last=long_ago))

        assert allowed


class TestCeiling:
    """⚠⚠ Кончились попытки — автомат замолкает совсем, а не ждёт дольше."""

    async def test_the_ceiling_stops_the_automation(self):
        from datetime import timedelta

        from datetime_.datetime_ import datetime_utc_now

        # Времени прошло сколько угодно — потолок это не лечит.
        long_ago = datetime_utc_now() - timedelta(days=3)
        allowed, why = await dating_login_retry_allowed(
            _profile(DATING_LOGIN_RETRY_MAX_DEFAULT, last=long_ago))

        assert not allowed
        assert 'Войти' in why, 'в причине нет того, что делать человеку: ' + why

    async def test_the_counter_does_not_heal_with_time(self):
        """⚠ Счётчик не сбрасывается временем, и это не забывчивость.

        Считай мы «давно было, начнём заново» — потолок не наступил бы никогда:
        сайт, отвергающий вход ровно раз в паузу, получал бы попытки вечно.
        """
        import inspect

        from src.dating import dating_login_retry

        code = inspect.getsource(dating_login_retry.dating_login_retry_fail)

        assert 'login_fail_tries + 1' in code
        assert 'SET login_fail_tries = 0' not in code


class TestTheDoorHoldsTheRule:
    """⚠⚠ Проверка стоит во входе, а не в кругах — это и есть суть правки."""

    def test_the_login_checks_before_running(self):
        import inspect

        from src.dating import dating_login

        code = inspect.getsource(dating_login.dating_login_run)

        assert 'dating_login_retry_allowed' in code, \
            'вход снова не спрашивает счётчик — круги смогут долбить сайт'
        assert 'DatingLoginHeld' in code

    def test_a_failure_is_counted_at_the_door(self):
        """Неудачу считает сам вход: вызывающий её забудет — и уже забывал."""
        import inspect

        from src.dating import dating_login

        assert 'dating_login_retry_fail' in inspect.getsource(dating_login.dating_login_run)

    def test_success_clears_the_counter(self):
        """⚠ Гасится одним запросом с тревогой: разойдись они — анкета вошла бы,
        а автомат считал бы её исчерпавшей попытки."""
        import inspect

        from src.dating import dating_login

        code = inspect.getsource(dating_login._keep_online_set)

        assert 'login_fail_tries = 0' in code and 'login_fail_at = NULL' in code

    def test_the_schedule_round_is_silent_on_a_hold(self):
        """⚠ Круг расписания молчит: он идёт раз в минуту.

        Пиши он строку в журнал, четверть часа паузы дала бы пятнадцать
        одинаковых записей, и настоящая причина утонула бы в них. Именно этот
        круг и долбил сайт.
        """
        import inspect

        from src.dating import dating_schedule_round

        code = inspect.getsource(dating_schedule_round._enter)

        assert 'except DatingLoginHeld' in code, 'круг снова считает паузу отказом'
        # ⚠ Ветка паузы обязана стоять **до** `except Exception`: `DatingLoginHeld`
        # — его наследник, и после общей до неё бы не дошло. С `DatingLoginError`
        # порядок безразличен: они друг другу не родня.
        assert code.index('except DatingLoginHeld') < code.index('except Exception'), \
            'ветка паузы стоит после общей — до неё не дойдёт'

    def test_the_human_button_forces_it(self):
        """⚠ Кнопка человека сильнее счётчика: он знает больше автомата."""
        import inspect

        from src.uvicorn.routers.dating import dating_profile_action

        code = inspect.getsource(dating_profile_action.dating_profile_action_login)

        assert 'force=True' in code, 'кнопка «Войти» перестала работать на паузе'


class TestSettingsAreRegistered:
    """Обе настройки — на странице «Сайты», иначе задать их нечем."""

    async def test_both_keys_are_known(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'login_retry_max' in known
        assert 'login_retry_every' in known

    async def test_defaults_match_the_code(self, auth_client):
        """⚠ Умолчание реестра и умолчание кода — одно число.

        Разойдись они, сайт без настройки жил бы по одному правилу, а форма
        показывала бы другое: человек читал бы в панели «3», а получал иное.
        """
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert int(known['login_retry_max']['default']) == DATING_LOGIN_RETRY_MAX_DEFAULT
        assert int(known['login_retry_every']['default']) == DATING_LOGIN_RETRY_EVERY_DEFAULT

    async def test_they_live_with_presence(self, auth_client):
        """Группа «Присутствие на сайте»: вход — про то, как анкета держится в сети."""
        got = (await auth_client.get('/api/dating/site')).json()['result']
        group = {row['key']: row['group'] for row in got['known']}

        assert group['login_retry_max'] == group['heartbeat']
        assert group['login_retry_every'] == group['heartbeat']


class TestHoldIsNotAFailure:
    """⚠ Своё исключение, а не `DatingLoginError`, и разница смысловая.

    `DatingLoginError` значит «войти нечем» — нет пароля, сценария, сайта, — и
    человек по нему идёт что-то исправлять. Пауза же значит «всё настроено,
    просто рано»: круг обязан уйти молча.
    """

    def test_it_is_a_separate_exception(self):
        from src.dating.dating_login import DatingLoginError

        assert not issubclass(DatingLoginHeld, DatingLoginError)
