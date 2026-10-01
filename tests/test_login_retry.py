"""Повторы входа: сколько раз пробовать и с какой паузой — настройками сайта.

## ⚠⚠ Живая беда: девять входов за четырнадцать минут

Анкета #41 (Ladadate) на проде входила **каждые 86 секунд**, безостановочно:
сценарий проходил форму, сайт кабинет не подтверждал, круг расписания пробовал
снова. Ровно то, за что сайт закрывает вход по внешнему адресу, — и ровно та
беда, из-за которой мы уже просидели сутки в блокировке.

## ⚠ Лестница отката это не поймала, и вот почему

Лестница пауз (`revive.py`) существовала, работала и к этому входу
**отношения не имела**: её спрашивает круг восстановления, а входил круг
расписания — прямым вызовом `login_run`, мимо всех правил.

Отсюда главное в устройстве: **правило стоит у самой двери**. Не в круге, не в
трёх кругах, а во входе. Обойти его нельзя, не переписав вход.
"""

import uuid

import pytest

from src.dating.login_retry import (LOGIN_RETRY_EVERY_DEFAULT,
                                           LOGIN_RETRY_MAX_DEFAULT,
                                           DatingLoginHeld,
                                           login_retry_allowed,
                                           login_retry_fail,
                                           login_retry_hold)

pytestmark = pytest.mark.profile


def _profile(tries: int, last=None, site_id: int = 1):
    return {'id': 1, 'site_id': site_id, 'login_fail_tries': tries,
            'login_fail_at': last}


async def _girl() -> int:
    """Анкета сюиты: метка `@example.com` уносит её уборкой после прогона."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO profile (site_id, login, password, enabled)'
            ' VALUES (%s, %s, %s, 1)',
            (999_343, f'retry-{uuid.uuid4().hex[:8]}@example.com', 'x'))
        await db.connection.commit()

        return int(db.lastrowid)


async def _row(profile_id: int) -> dict:
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT login_fail_tries, login_fail_at FROM profile'
                         ' WHERE id = %s', (int(profile_id),))

        return dict(await db.fetchone())


class TestFirstTryIsFree:
    """Первая попытка идёт без вопросов: пауза — это реакция на неудачу."""

    async def test_a_clean_profile_may_log_in(self):
        allowed, why = await login_retry_allowed(_profile(0))

        assert allowed and why == ''

    async def test_a_failure_without_time_does_not_block(self):
        """⚠ Счётчик есть, времени нет — пускаем.

        Такое бывает у анкеты, чей счётчик пришёл из миграции или был поправлен
        руками. Держать вход по половине состояния значило бы запереть анкету
        молча и навсегда.
        """
        allowed, _ = await login_retry_allowed(_profile(1, last=None))

        assert allowed


class TestPauseBetweenTries:
    """Пауза считается от последней неудачи и названа в причине словами."""

    async def test_right_after_a_failure_it_holds(self):
        from datetime_.datetime_ import datetime_utc_now

        allowed, why = await login_retry_allowed(
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
            seconds=LOGIN_RETRY_EVERY_DEFAULT + 60)
        allowed, _ = await login_retry_allowed(_profile(1, last=long_ago))

        assert allowed


class TestCeiling:
    """⚠⚠ Кончились попытки — автомат замолкает совсем, а не ждёт дольше."""

    async def test_the_ceiling_stops_the_automation(self):
        from datetime import timedelta

        from datetime_.datetime_ import datetime_utc_now

        # Времени прошло сколько угодно — потолок это не лечит.
        long_ago = datetime_utc_now() - timedelta(days=3)
        allowed, why = await login_retry_allowed(
            _profile(LOGIN_RETRY_MAX_DEFAULT, last=long_ago))

        assert not allowed
        assert 'Войти' in why, 'в причине нет того, что делать человеку: ' + why

    async def test_the_counter_does_not_heal_with_time(self):
        """⚠ Счётчик не сбрасывается временем, и это не забывчивость.

        Считай мы «давно было, начнём заново» — потолок не наступил бы никогда:
        сайт, отвергающий вход ровно раз в паузу, получал бы попытки вечно.
        """
        import inspect

        from src.dating import login_retry

        code = inspect.getsource(login_retry.login_retry_fail)

        assert 'login_fail_tries + 1' in code
        assert 'SET login_fail_tries = 0' not in code


class TestTheCeilingCountsTheSiteNotTheBrowser:
    """⚠⚠ Живая беда: три сорванных вкладки Chromium заперли здоровую анкету.

    Прод, анкета #40 (Ladadate), вечер 28.09. Три неудачи подряд, и все три
    браузерные: «Target page, context or browser has been closed» на шаге 2 и
    дважды `Locator.click: Timeout 30000ms` на шаге 4. Сайт при этом пускал — тем
    же логином человек вошёл руками без заминки, и остальные двенадцать анкет
    Ladadate в те же сутки входили как обычно.

    Потолок защёлкнулся, и с 28.09 21:50 автомат к сайту не ходил **ни разу**:
    двенадцать «попыток» лестницы восстановления упёрлись в свою же дверь, а через
    сутки панель объявила «сутки не выходит войти». Снять защёлку могла только
    кнопка человека.

    ⚠⚠ **Второй конец той же ошибки — отказ сайта в потолок не попадал вовсе.**
    Ветка «Сайт вход не признал» уходит из входа своим `return` и до счётчика не
    доходит. То есть беда, ради которой потолок и заводили (сайт не подтверждает
    кабинет, круг расписания долбит вход каждые 86 секунд), им не ловилась.

    Отсюда правило: потолок считает **отказ сайта**, а сорвавшийся прогон получает
    только паузу. Сломанный браузер соседа — не причина запирать учётку.
    """

    async def test_a_site_refusal_counts(self):
        """Отказ сайта — настоящая неудача входа: счётчик и время."""
        girl = await _girl()
        await login_retry_fail(girl)
        row = await _row(girl)

        assert int(row['login_fail_tries']) == 1
        assert row['login_fail_at'] is not None

    async def test_a_broken_run_pauses_but_does_not_count(self):
        """⚠ Сорвавшийся прогон ставит паузу и **не** двигает потолок."""
        girl = await _girl()
        await login_retry_hold(girl)
        row = await _row(girl)

        assert int(row['login_fail_tries']) == 0, (
            'сорвавшаяся вкладка Chromium снова копит потолок — три подряд запрут '
            'здоровую анкету до нажатия кнопки')
        assert row['login_fail_at'] is not None, (
            'после сорвавшегося прогона нет паузы — круг пойдёт в браузер снова '
            'через минуту')

    async def test_the_pause_holds_without_a_counter(self):
        """⚠ Пауза держит и при нулевом счётчике: иначе у сорвавшегося прогона
        её не было бы вовсе."""
        from datetime_.datetime_ import datetime_utc_now

        allowed, why = await login_retry_allowed(_profile(0, last=datetime_utc_now()))

        assert not allowed
        assert 'осталось' in why, why
        # Номера попытки тут нет и быть не должно: к потолку это не попытка.
        assert 'Попытка' not in why, why

    async def test_broken_runs_never_latch(self):
        """⚠⚠ Сколько бы прогонов ни сорвалось, потолок не наступит.

        Пять подряд — и после паузы вход снова разрешён. Это и есть разница с
        отказом сайта: тот на третий раз замолкает совсем.
        """
        from datetime import timedelta

        from datetime_.datetime_ import datetime_utc_now

        girl = await _girl()
        for _ in range(5):
            await login_retry_hold(girl)

        row = await _row(girl)
        long_ago = datetime_utc_now() - timedelta(seconds=LOGIN_RETRY_EVERY_DEFAULT + 60)
        allowed, why = await login_retry_allowed(
            _profile(int(row['login_fail_tries']), last=long_ago))

        assert allowed, why

    def test_the_refusal_branch_counts_at_the_door(self):
        """⚠⚠ Ветка «сайт вход не признал» обязана считать неудачу до своего `return`.

        Здесь и была ошибка: ветка уходила из входа сама, и отказ сайта в счётчик
        не попадал никогда.
        """
        import inspect

        from src.dating import login

        code = inspect.getsource(login.login_run)
        start = code.index("if verify['status'] != 'online':")
        end = code.index("return {'status': 'error'", start)

        assert 'login_retry_fail' in code[start:end], (
            'отказ сайта снова не считается в потолок — круг будет долбить вход '
            'до конца времён')

    def test_the_broken_run_branch_only_holds(self):
        """⚠ Ветка сорвавшегося прогона ставит паузу, а не считает потолок."""
        import inspect

        from src.dating import login

        code = inspect.getsource(login.login_run)
        tail = code[code.rindex("if result.get('status') == 'error':"):]

        assert 'login_retry_hold' in tail, 'сорвавшийся прогон перестал ставить паузу'
        assert 'login_retry_fail' not in tail, (
            'сорвавшийся прогон снова копит потолок')


class TestTheDoorHoldsTheRule:
    """⚠⚠ Проверка стоит во входе, а не в кругах — это и есть суть правки."""

    def test_the_login_checks_before_running(self):
        import inspect

        from src.dating import login

        code = inspect.getsource(login.login_run)

        assert 'login_retry_allowed' in code, \
            'вход снова не спрашивает счётчик — круги смогут долбить сайт'
        assert 'DatingLoginHeld' in code

    def test_a_failure_is_counted_at_the_door(self):
        """Неудачу считает сам вход: вызывающий её забудет — и уже забывал."""
        import inspect

        from src.dating import login

        assert 'login_retry_fail' in inspect.getsource(login.login_run)

    def test_success_clears_the_counter(self):
        """⚠ Гасится одним запросом с тревогой: разойдись они — анкета вошла бы,
        а автомат считал бы её исчерпавшей попытки."""
        import inspect

        from src.dating import login

        code = inspect.getsource(login._keep_online_set)

        assert 'login_fail_tries = 0' in code and 'login_fail_at = NULL' in code

    def test_the_schedule_round_is_silent_on_a_hold(self):
        """⚠ Круг расписания молчит: он идёт раз в минуту.

        Пиши он строку в журнал, четверть часа паузы дала бы пятнадцать
        одинаковых записей, и настоящая причина утонула бы в них. Именно этот
        круг и долбил сайт.
        """
        import inspect

        from src.dating import schedule_round

        code = inspect.getsource(schedule_round._enter)

        assert 'except DatingLoginHeld' in code, 'круг снова считает паузу отказом'
        # ⚠ Ветка паузы обязана стоять **до** `except Exception`: `DatingLoginHeld`
        # — его наследник, и после общей до неё бы не дошло. С `DatingLoginError`
        # порядок безразличен: они друг другу не родня.
        assert code.index('except DatingLoginHeld') < code.index('except Exception'), \
            'ветка паузы стоит после общей — до неё не дойдёт'

    def test_the_human_button_forces_it(self):
        """⚠ Кнопка человека сильнее счётчика: он знает больше автомата."""
        import inspect

        from src.uvicorn.routers.dating import profile_action

        code = inspect.getsource(profile_action.profile_action_login)

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

        assert int(known['login_retry_max']['default']) == LOGIN_RETRY_MAX_DEFAULT
        assert int(known['login_retry_every']['default']) == LOGIN_RETRY_EVERY_DEFAULT

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
        from src.dating.login import DatingLoginError

        assert not issubclass(DatingLoginHeld, DatingLoginError)


class TestSocketOnlyWhereItExists:
    """⚠⚠ Адрес чат-сокета снимается только у сайта, который им пишет.

    **Поймано в журнале прода:**

        21:59 · Nadezhda — Адрес чат-сокета снять не вышло, живой чат работать
        не будет

    У Ladadate сокета **нет вовсе**: чат там обычный HTTP, проверено перехватом
    сети (`heartbeat.md`, §4). А перехват запускался всё равно —
    `ws_capture` берёт `presence.chat_url`, а когда его нет, подставляет
    **корень сайта** и честно ждёт сокета на главной странице.

    Стоило это вкладки живого Chromium у соседа на каждый вход. И строка врала
    дважды: снимать было нечего, а чат у Ladadate работает и без сокета — то
    есть человека пугали поломкой, которой нет.
    """

    def test_the_login_asks_before_capturing(self):
        import inspect

        from src.dating import login

        code = inspect.getsource(login.login_run)

        assert '_ws_needed(' in code, \
            'вход снова снимает сокет у всех — у сайта без сокета это враньё в журнале'

    async def test_a_http_site_needs_no_socket(self):
        """Ladadate: транспорт `http` — сокет не нужен."""
        from src.dating.login import _ws_needed

        site = {'presence': {'send': {'transport': 'http'}}}

        assert not await _ws_needed(999_343, site)

    async def test_a_ws_site_still_needs_it(self):
        """⚠ Goldenbride: транспорт `ws` — сокет нужен, и правило его не трогает."""
        from src.dating.login import _ws_needed

        site = {'presence': {'send': {'transport': 'ws'}}}

        assert await _ws_needed(999_343, site)

    async def test_our_setting_wins_over_the_neighbour(self, monkeypatch):
        """⚠ Наша настройка старше соседского описания — как и у отправки.

        Сайт, которому в «Чем отправляем сообщения» поставили `scenario`, пишет
        сценарием, даже если у соседа в описании стоит `ws`. Сокет ему не нужен.
        """
        from src.dating import login

        async def _settings(site_id):
            return {'send_transport': 'scenario'}

        monkeypatch.setattr('src.uvicorn.routers.dating.site_.site_settings',
                            _settings)

        assert not await login._ws_needed(
            999_343, {'presence': {'send': {'transport': 'ws'}}})
