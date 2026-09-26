"""Пауза после неудачного входа в API сайта.

⚠⚠ **Написано по живой блокировке, и она стоила суток работы всех анкет сайта.**

Сайт считает **неудачные** попытки входа с одного IP за сутки и, набрав своё,
закрывает адрес целиком (`ERROR_SING_IN_BY_IP_DAILY_FAILED_LIMIT`). Дальше не
входит никто — ни ошибшаяся анкета, ни двенадцать соседних.

Кормили счётчик мы сами: круг ленты ходит каждые две минуты, при отказе токена не
появляется, и следующий заход честно пробует снова. Тридцать неудач в час,
семьсот в сутки — блокировка держалась ровно столько, сколько работал воркер.

Здесь стережётся то, что этого больше не будет.
"""

import pytest

from src.dating import site_api
from src.dating.site_api import (SITE_API_FAIL_FIRST_SEC,
                                        SITE_API_FAIL_KIND_LIMIT,
                                        SITE_API_FAIL_KIND_OTHER,
                                        SITE_API_FAIL_LIMIT_SEC,
                                        SITE_API_FAIL_MAX_SEC,
                                        _fail_clear, _fail_kind, _fail_left,
                                        _fail_mark, _fail_when, _fail_words)

pytestmark = pytest.mark.profile


class _Redis:
    """Redis настолько, насколько его использует лестница пауз."""

    def __init__(self):
        self.store: dict = {}
        self.ttl_: dict = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        self.store[key] = value
        self.ttl_[key] = ex

        return True

    def ttl(self, key):
        return self.ttl_.get(key, 0)

    def delete(self, key):
        self.store.pop(key, None)
        self.ttl_.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(site_api, 'redis_conn_get', lambda: fake)

    return fake


class TestBackoff:

    def test_first_failure_buys_silence(self, redis):
        """Первая неудача — пять минут молчания, а не немедленный повтор.

        ⚠ Ровно этого и не хватало: круг возвращался через две минуты и делал
        новую неудачную попытку, продлевая счётчик сайта.
        """
        _fail_mark(1, 'Login or password invalid')

        assert _fail_left(1) == SITE_API_FAIL_FIRST_SEC

    def test_ladder_doubles_and_stops(self, redis):
        """Срок растёт вдвое и упирается в потолок: час, а не сутки.

        Потолок нужен, чтобы случайно сломанный пароль не запирал анкету
        навсегда — починив его, человек ждёт не больше часа.
        """
        for _ in range(10):
            _fail_mark(1, 'Login or password invalid')

        assert _fail_left(1) == SITE_API_FAIL_MAX_SEC

    def test_daily_limit_waits_an_hour_at_once(self, redis):
        """⚠ Сайт назвал суточный лимит — ждём час сразу, минуя лестницу.

        Лестница здесь бессмысленна: счётчик у сайта суточный, и частые попытки
        его же и продлевают. Пять минут молчания после такого ответа означали бы
        двенадцать новых неудач в час — то есть ровно то, из-за чего блокировка и
        случилась.
        """
        _fail_mark(2, 'Exceeded the daily limit registrations '
                      '(ERROR_SING_IN_BY_IP_DAILY_FAILED_LIMIT)')

        assert _fail_left(2) == SITE_API_FAIL_LIMIT_SEC

    def test_the_other_wording_counts_too(self, redis):
        """Второй ответ сайта про то же — «invalid login attempts per day».

        ⚠ Приметы ищем **словами, а не кодом**: код сайт придумывает сам, и в
        одну блокировку он прислал две разные формулировки.
        """
        _fail_mark(3, 'Exceeded the limit of invalid login attempts per day')

        assert _fail_left(3) == SITE_API_FAIL_LIMIT_SEC

    def test_success_clears_the_ladder(self, redis):
        """Удачный вход стирает паузу: следующая неудача считается с начала."""
        _fail_mark(4, 'Login or password invalid')
        _fail_clear(4)

        assert _fail_left(4) == 0

    def test_broken_redis_does_not_lock_the_profile(self, monkeypatch):
        """⚠ Сбой Redis отвечает «можно», а не «молчи».

        Без Redis нет ни счётчика, ни общего токена: запирать анкету наглухо
        из-за недоступного Redis значило бы менять редкую беду на постоянный
        простой.
        """
        def _broken():
            raise RuntimeError('Redis лёг')

        monkeypatch.setattr(site_api, 'redis_conn_get', _broken)

        assert _fail_left(5) == 0
        _fail_mark(5, 'что угодно')  # не бросает


class TestWords:
    """Что человек читает в журнале анкеты.

    ⚠⚠ **Строка объясняет, а не отчитывается.** Прежняя — «вход отложен на 2388
    с: прошлая попытка не прошла. Сайт считает неудачные входы с нашего адреса и
    закрывает его целиком» — заставляла спрашивать заново: кто отложил, почему
    счёт в секундах, при чём тут адрес. Читает её человек, а не мы, и понятной она
    обязана быть без чтения кода.
    """

    def test_the_two_cases_read_differently(self, redis):
        """⚠ «Сайт закрыл адрес» и «вход не прошёл» чинятся по-разному.

        Первое — не про пароль вовсе: менять его бессмысленно, сайт до проверки не
        доходит. Второе — как раз может быть про пароль. Одинаковая строка
        заставляла бы гадать, что случилось.
        """
        limit = _fail_words(2388, SITE_API_FAIL_KIND_LIMIT)
        other = _fail_words(300, SITE_API_FAIL_KIND_OTHER)

        assert 'закрыл вход' in limit and 'Пароль тут ни при чём' in limit
        # ⚠ И срок назван суточным: час здесь был ошибкой, из-за которой предел
        # сайта не опустевал никогда — мы давали ему 24 неудачи в сутки.
        assert 'суточный' in limit
        assert 'прошлый вход не прошёл' in other

    def test_the_wait_is_human(self, redis):
        """Секунды в журнале не читаются: «39 мин» вместо «2388 с»."""
        assert _fail_when(45) == '45 с'
        assert _fail_when(300) == '5 мин'
        assert _fail_when(2388) == '39 мин'
        # «1 ч 0 мин» человек читает как опечатку, а не как ровный час.
        assert _fail_when(3600) == '1 ч'
        assert _fail_when(5400) == '1 ч 30 мин'

    def test_the_kind_is_remembered(self, redis):
        """Вид отказа лежит рядом со сроком — по нему и выбирается объяснение."""
        _fail_mark(11, 'Exceeded the daily limit registrations')
        _fail_mark(12, 'Login or password invalid')

        assert _fail_kind(11) == SITE_API_FAIL_KIND_LIMIT
        assert _fail_kind(12) == SITE_API_FAIL_KIND_OTHER

    def test_the_kind_does_not_break_the_ladder(self, redis):
        """⚠ Срок по-прежнему удваивается: вид записан рядом, а не вместо него."""
        _fail_mark(13, 'Login or password invalid')
        _fail_mark(13, 'Login or password invalid')

        assert _fail_left(13) == SITE_API_FAIL_FIRST_SEC * 2

    def test_an_old_mark_is_still_understood(self, redis):
        """⚠ Метка прежнего вида — голое число — не должна ронять разбор.

        Метки живут до часа, и выкладка посреди этого часа встретит записи старого
        образца. Потерять на них паузу значило бы потерять её ровно тогда, когда
        она нужнее всего.
        """
        redis.set('dating:site_api:fail:14', '300', ex=300)
        _fail_mark(14, 'Login or password invalid')

        assert _fail_left(14) == 600
        assert _fail_kind(14) == SITE_API_FAIL_KIND_OTHER

    def test_unknown_mark_reads_as_ordinary(self, redis):
        """Метки нет вовсе — объясняем обычным отказом, а не блокировкой адреса."""
        assert _fail_kind(99) == SITE_API_FAIL_KIND_OTHER


class TestSiteWideBan:
    """⚠⚠ Закрыт адрес — не трогаем сайт **ни одной** анкетой.

    **Написано по неделе простоя.** Упёршись одной анкетой в «предел по адресу», мы
    шли пробовать остальными двенадцатью — и добавляли двенадцать неудач к
    счётчику, который и так переполнен. Счётчик у сайта суточный: пока мы стучимся,
    он не опустеет никогда, и запрет держится не сутки, а бесконечно.

    Различать роды предела обязательно:

        ERROR_SING_IN_BY_IP_DAILY_FAILED_LIMIT       — закрыт наш адрес;
        ERROR_SING_IN_BY_ACCOUNT_DAILY_FAILED_LIMIT  — закрыта эта учётка.

    Первый — про всех сразу.
    """

    def test_an_ip_ban_closes_the_whole_site(self, redis):
        from src.dating.site_api import _fail_site_left, _fail_site_mark

        _fail_site_mark(343, 'Exceeded the daily limit registrations '
                             '(ERROR_SING_IN_BY_IP_DAILY_FAILED_LIMIT)')

        assert _fail_site_left(343) == SITE_API_FAIL_LIMIT_SEC

    def test_an_account_ban_does_not_close_the_site(self, redis):
        """⚠ Предел учётки — про неё одну: остальные девушки работать могут.

        Ошибиться здесь в эту сторону значит остановить двенадцать анкет из-за
        одной, у которой, может быть, просто сменили пароль.
        """
        from src.dating.site_api import _fail_site_left, _fail_site_mark

        _fail_site_mark(343, 'Exceeded the limit of invalid login attempts per day '
                             '(ERROR_SING_IN_BY_ACCOUNT_DAILY_FAILED_LIMIT)')

        assert _fail_site_left(343) == 0

    def test_an_ordinary_refusal_does_not_close_the_site(self, redis):
        from src.dating.site_api import _fail_site_left, _fail_site_mark

        _fail_site_mark(343, 'Login or password invalid')

        assert _fail_site_left(343) == 0

    def test_the_daily_limit_waits_a_whole_day(self, redis):
        """⚠⚠ Сутки, а не час.

        Час здесь и стоял, и это была ошибка ценой в неделю: пробуя раз в час, мы
        давали сайту 24 неудачи в сутки — ровно столько, чтобы суточный предел не
        опустел никогда.
        """
        assert SITE_API_FAIL_LIMIT_SEC >= 24 * 3600

    # ⚠ Тест «запрет снимают руками» убран вместе с `site_api_fail_clear`: он
    # охранял способность, которой никто не пользовался — ни ручка, ни кнопка.
    # Сторож у мёртвого кода держит его живым на вид: имя встречается, значит
    # «зовётся», и следующий поиск неиспользуемого пройдёт мимо.
