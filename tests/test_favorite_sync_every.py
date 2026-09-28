"""Сверка избранных с сайтом — по своему сроку, а не на каждый заход круга.

## ⚠⚠ Живая беда: 157 запросов в час впустую

Сверка стояла **первой строкой каждого захода** набора писем избранным. А заход
случается не только по сроку круга: его будит и свежий снимок ленты «Все онлайн»,
а тот снимается раз в три минуты.

Считано по журналу запросов прода:

    GET /users/my-favorites — 157 за час, 8 анкет, каждые три минуты

И почти все впустую: **список избранных ведём мы сами** — жмём звёздочку своим
сценарием и сами её снимаем. Сайт не может изменить его за нашей спиной; сверка
нужна лишь на случай правки руками в кабинете, а это не то, что делают каждые
три минуты.

⚠ **Сверку не убрали совсем, и это важно.** Наша таблица знает только тех, кто
писал письма, а в виджете «Мои фавориты» у анкеты бывает восемнадцать человек —
без сверки пул честно отвечал бы «писать некому», имея кому.
"""

import itertools

import pytest

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

# ⚠⚠ Свой номер сайта каждому тесту. Отметка сверки живёт **в памяти процесса**
# (`_sync_at`), а сюита идёт одним процессом: возьми два теста один номер — второй
# увидит отметку, поставленную первым, и «после старта сверяем сразу» обернётся
# `False`. Зависел бы не код, а порядок запуска.
#
# ⚠ Прежде чистоту наводил `favorite_sync_forget()` в `setup_method` — функция в коде
# проекта, которую не звал никто, кроме этих тестов. Её убрали: крючок ради сюиты в
# рабочем коде читается как рабочий путь.
#
# ⚠ Номера начинаются заведомо выше стендового: `_sync_due` в базу не ходит и строки
# сайта не спрашивает, так что заводить их не нужно, а пересечься с чужими нельзя.
_site_seq = itertools.count(STAND_SITE + 100)


def _site() -> int:
    """Незанятый номер сайта — по одному на вызов."""
    return next(_site_seq)


class TestTheGate:
    """Сам сторож: когда сверке пора, а когда рано."""

    def test_the_first_pass_syncs(self):
        """После старта сверяем сразу: своей таблице ещё нечем верить."""
        from src.dating.favorite_mail import _sync_due

        assert _sync_due(_site()) is True

    def test_right_after_a_sync_it_waits(self):
        from src.dating.favorite_mail import _sync_due, _sync_mark

        site = _site()
        _sync_mark(site, 3600)

        assert _sync_due(site) is False, \
            'сверка пошла бы снова — вернулись 157 запросов в час'

    def test_after_the_period_it_syncs_again(self):
        from src.dating.favorite_mail import _sync_due, _sync_mark

        site = _site()
        _sync_mark(site, 0)

        assert _sync_due(site) is True

    def test_sites_do_not_share_the_mark(self):
        """⚠ Отметка на сайт, а не общая: у разных сайтов свой срок."""
        from src.dating.favorite_mail import _sync_due, _sync_mark

        marked, other = _site(), _site()
        _sync_mark(marked, 3600)

        assert _sync_due(other) is True

    def test_the_mark_is_set_before_going(self):
        """⚠ Отметка ставится **до** похода, а не после.

        Сверка идёт по анкете с запросом на каждую — восемь анкет это восемь
        запросов и почти секунда. Не отметь мы сразу, следующий тик завёл бы
        вторую сверку поверх первой.
        """
        import inspect

        from src.dating import favorite_mail

        code = inspect.getsource(favorite_mail.favorite_mail_stars_sync)

        assert code.index('_sync_mark(') < code.index('await _sync_stars('), \
            'отметка стоит после похода — два тика сверят разом'


class TestThePass:
    """Как сторож встроен в заход набора пула.

    ⚠ Сверка переехала из тела набора в свою `favorite_mail_stars_sync`: её зовёт
    и набор, и круг — **до** счёта места в пуле. Довод переезда — в её докстринге:
    полный пул не давал почистить сам себя. Сторож того порядка —
    `test_favorite_gone.py`, `TestTheFullPoolStillSyncs`.
    """

    def test_the_pass_asks_the_gate(self):
        import inspect

        from src.dating import favorite_mail

        code = inspect.getsource(favorite_mail.favorite_mail_stars_sync)

        assert '_sync_due(site_id)' in code, \
            'сверка снова идёт на каждый заход круга'

    def test_the_making_still_syncs(self):
        """⚠ Набор сверку не потерял: кнопка человека зовёт именно его."""
        import inspect

        from src.dating import favorite_mail

        assert 'await favorite_mail_stars_sync(' in inspect.getsource(
            favorite_mail.favorite_mail_make)

    def test_the_human_button_always_syncs(self):
        """⚠ Кнопка человека сверяет мимо срока.

        Он нажал её именно затем, чтобы увидеть сайт **сейчас**, а не наш снимок
        часовой давности. Признак кнопки — заданный `profile_id`: круг зовёт
        набор по сайту целиком, человек — по своей анкете.
        """
        import inspect

        from src.dating import favorite_mail

        code = inspect.getsource(favorite_mail.favorite_mail_stars_sync)

        assert 'if profile_id or _sync_due(site_id):' in code

    def test_the_sync_itself_is_not_gone(self):
        """⚠⚠ Сверку не убрали совсем — без неё пул слепнет.

        Наша таблица знает только писавших письма; в «Мои фавориты» у анкеты
        бывает восемнадцать человек, и набор отвечал бы «писать некому».
        """
        import inspect

        from src.dating import favorite_mail

        assert 'await _sync_stars(' in inspect.getsource(
            favorite_mail.favorite_mail_stars_sync)


class TestTheSetting:
    """Срок задаётся на странице «Сайты», а не константой."""

    async def test_the_key_is_registered(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'favorite_sync_every' in known
        assert known['favorite_sync_every']['default'] == '3600'

    async def test_it_is_not_the_other_two(self, auth_client):
        """⚠ Три разных срока про избранных, и путать их дорого.

        `favorite_every` — как часто **жать звёздочки**; `favorites_keep` —
        сколько живёт снимок для страницы; этот — как часто **спрашивать сайт**.
        """
        got = (await auth_client.get('/api/dating/site')).json()['result']
        keys = {row['key'] for row in got['known']}

        assert {'favorite_sync_every', 'favorite_every', 'favorites_keep'} <= keys

    def test_the_default_is_an_hour_not_minutes(self):
        """Час: список ведём мы, сайт его за нашей спиной не меняет."""
        from src.dating.favorite_mail import FAVORITE_SYNC_EVERY_DEFAULT

        assert FAVORITE_SYNC_EVERY_DEFAULT >= 3600
