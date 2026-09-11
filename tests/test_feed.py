"""Лента событий: что круги сделали только что.

⚠ **Кольцо подделываем, а не берём живое**, и не ради скорости: сюита идёт **с
хоста**, а Redis живёт в контейнере под своим именем — снаружи его просто нет. Тот
же приём и та же причина, что у круга рассылки (`test_mailing_round.py`).

⚠ Подделка при этом честная: `lpush` кладёт в голову, `ltrim` режет хвост, `lrange`
отдаёт срез. Иначе проверялось бы устройство заглушки, а не правило — а правила
здесь ровно про порядок и про то, что старое выбрасывается само.

Живьём проверено в браузере: две строки в полосе вкладок «Профилей», верхняя
мигает до смены или щелчка, нижняя — своим, спокойным цветом.
"""

import pytest

from src.dating import dating_feed as feed_
from src.uvicorn.routers.dating.dating_feed import dating_feed_list
from src.dating.dating_feed import (DATING_FEED_KEEP, DATING_FEED_TEXT_MAX,
                                    dating_feed_done, dating_feed_read,
                                    dating_feed_write)

pytestmark = pytest.mark.core

SITE = 999_783


class _FakeRedis:
    """Redis настолько, насколько его читает лента: список и срок жизни."""

    def __init__(self):
        self.store: dict[str, list] = {}

    # ⚠ Конвейер здесь не украшение: запись идёт тремя командами (`lpush`,
    # `ltrim`, `expire`), и подделка без него молча роняла бы `AttributeError` —
    # а модуль проглотил бы его, как любой сбой Redis, и лента всегда была бы
    # пустой при зелёных тестах.
    def pipeline(self):
        return _FakePipe(self)

    def lpush(self, key, value):
        self.store.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, stop):
        self.store[key] = self.store.get(key, [])[start:stop + 1]

    def expire(self, key, seconds):
        return True

    def lrange(self, key, start, stop):
        return self.store.get(key, [])[start:stop + 1]

    # ⚠ Нужны по-настоящему: замена итога ищет свою строку в голове (`lindex`) и
    # правит её на месте (`lset`). Без них подделка роняла бы `AttributeError`, а
    # модуль проглотил бы его как любой сбой Redis — и проверка замены зеленела бы,
    # проверяя устройство заглушки вместо правила.
    def lindex(self, key, at):
        rows = self.store.get(key, [])

        return rows[at] if -len(rows) <= at < len(rows) else None

    def lset(self, key, at, value):
        self.store.setdefault(key, [])[at] = value

    def delete(self, key):
        self.store.pop(key, None)


class _FakePipe:
    """Конвейер: копит команды и выполняет их разом, как настоящий."""

    def __init__(self, conn):
        self.conn = conn
        self.calls = []

    def lpush(self, key, value):
        self.calls.append(('lpush', key, value))

    def ltrim(self, key, start, stop):
        self.calls.append(('ltrim', key, start, stop))

    def expire(self, key, seconds):
        self.calls.append(('expire', key, seconds))

    def execute(self):
        for name, *args in self.calls:
            getattr(self.conn, name)(*args)
        self.calls = []


@pytest.fixture
def ring(monkeypatch):
    """Пустое кольцо на каждый тест."""
    fake = _FakeRedis()
    monkeypatch.setattr(feed_, 'redis_conn_get', lambda: fake)

    return SITE


class TestWrite:
    """Что попадает в кольцо."""

    def test_event_comes_back(self, ring):
        dating_feed_write(SITE, 'mail', 'Забираю письма: анкет 13')

        rows = dating_feed_read(SITE)

        assert len(rows) == 1
        assert rows[0]['text'] == 'Забираю письма: анкет 13'
        assert rows[0]['kind'] == 'mail'

    def test_fresh_comes_first(self, ring):
        """⚠ Свежее сверху, и это не вкус: страница читает две первые строки.

        Переворачивай она список сама — платила бы за порядок, который можно
        задать при записи.
        """
        dating_feed_write(SITE, 'mail', 'первое')
        dating_feed_write(SITE, 'chat', 'второе')

        assert [row['text'] for row in dating_feed_read(SITE)] == ['второе', 'первое']

    def test_ring_forgets_the_oldest(self, ring):
        """Кольцо, а не журнал: старое выбрасывается само, чистить нечего."""
        for n in range(DATING_FEED_KEEP + 5):
            dating_feed_write(SITE, 'mail', f'событие {n}')

        rows = dating_feed_read(SITE, DATING_FEED_KEEP)

        assert len(rows) == DATING_FEED_KEEP
        assert rows[0]['text'] == f'событие {DATING_FEED_KEEP + 4}'

    def test_unknown_kind_becomes_warn(self, ring):
        """⚠ Вид из закрытого набора: им страница красит строку.

        Вид, о котором она не знает, приехал бы бесцветным — а событие, которое
        никто не назвал, скорее про беду, чем про удачу.
        """
        dating_feed_write(SITE, 'выдуманный', 'что-то')

        assert dating_feed_read(SITE)[0]['kind'] == 'warn'

    def test_long_text_is_cut_on_write(self, ring):
        """⚠ Режем при записи, а не при показе: строка живёт в одну строку полосы.

        Обрезанная на странице занимала бы место в кольце целиком и уезжала бы в
        ответ ручки — трафик ради текста, который никто не увидит.
        """
        dating_feed_write(SITE, 'mail', 'я' * (DATING_FEED_TEXT_MAX + 50))

        assert len(dating_feed_read(SITE)[0]['text']) == DATING_FEED_TEXT_MAX

    def test_empty_text_is_not_an_event(self, ring):
        dating_feed_write(SITE, 'mail', '   ')

        assert dating_feed_read(SITE) == []

    def test_no_site_no_event(self, ring):
        """Событий «вообще» не бывает: они всегда про конкретный сайт."""
        dating_feed_write(0, 'mail', 'ничьё')

        assert dating_feed_read(0) == []


class TestDone:
    """Итог заменяет строку «идёт», а не встаёт второй."""

    def test_result_replaces_the_started_line(self, ring):
        """⚠ Иначе на экране остаётся вечное «забираю письма».

        Человек видит начало работы и не знает, чем она кончилась, — ровно то, на
        что и пожаловались: «Забираю письма: анкет 13, нужно выводить результат».
        """
        started = dating_feed_write(SITE, 'mail', 'Забираю письма: анкет 13…')
        dating_feed_done(SITE, started, 'mail', 'Новых писем: 4')

        rows = dating_feed_read(SITE)

        assert [row['text'] for row in rows] == ['Новых писем: 4']

    def test_someone_elses_event_is_not_overwritten(self, ring):
        """⚠ Затирать голову вслепую нельзя: пока шёл заход, в ленту лёг чужой круг.

        Слепой `lset 0` стёр бы его событие — то есть лента теряла бы записи тем
        чаще, чем больше работы идёт разом.
        """
        started = dating_feed_write(SITE, 'mail', 'Забираю письма: анкет 13…')
        dating_feed_write(SITE, 'chat', 'Новых сообщений в чате: 2')
        dating_feed_done(SITE, started, 'mail', 'Новых писем: 4')

        тексты = [row['text'] for row in dating_feed_read(SITE)]

        assert тексты[0] == 'Новых писем: 4'
        assert 'Новых сообщений в чате: 2' in тексты

    def test_result_without_a_start_is_still_written(self, ring):
        """Начала не было (Redis моргнул) — итог всё равно должен быть виден."""
        dating_feed_done(SITE, '', 'mail', 'Новых писем: 4')

        assert [row['text'] for row in dating_feed_read(SITE)] == ['Новых писем: 4']


class TestRepeats:
    """⚠ Повтор обновляет строку, а не занимает вторую.

    Круги ходят с разной частотой — за письмами раз в две минуты, за перепиской раз
    в три, — и «Писем не пришло» каждые две минуты выталкивало бы переписку из двух
    видимых строк ещё до того, как человек её заметит. На это и пожаловались: «в
    ленте одни письма, а что чат забирается — не вижу».
    """

    def test_same_event_twice_is_one_line(self, ring):
        dating_feed_write(SITE, 'mail', 'Писем не пришло')
        dating_feed_write(SITE, 'mail', 'Писем не пришло')

        assert len(dating_feed_read(SITE)) == 1

    def test_repeat_refreshes_the_stamp(self, ring):
        """Время у схлопнутой строки — новое: иначе она выглядела бы застрявшей."""
        dating_feed_write(SITE, 'mail', 'Писем не пришло')
        было = dating_feed_read(SITE)[0]['at']
        dating_feed_write(SITE, 'mail', 'Писем не пришло')

        assert dating_feed_read(SITE)[0]['at'] >= было

    def test_another_kind_with_the_same_text_is_a_new_line(self, ring):
        """Схлопываем по паре «вид + текст»: разные занятия — разные события."""
        dating_feed_write(SITE, 'mail', 'ничего нет')
        dating_feed_write(SITE, 'chat', 'ничего нет')

        assert len(dating_feed_read(SITE)) == 2

    def test_chat_survives_a_chattier_mail_round(self, ring):
        """Ради чего всё: частая почта не должна выталкивать переписку из двух строк.

        Почта ходит чаще и повторяет один и тот же итог; переписка должна остаться
        видимой второй строкой.
        """
        dating_feed_write(SITE, 'chat', 'Новой переписки нет')
        for _ in range(4):
            dating_feed_write(SITE, 'mail', 'Писем не пришло')

        виды = [row['kind'] for row in dating_feed_read(SITE)[:2]]

        assert виды == ['mail', 'chat']


class TestAge:
    """Возраст считается при чтении, а не хранится."""

    def test_age_is_counted_on_read(self, ring):
        dating_feed_write(SITE, 'mail', 'свежее')

        assert dating_feed_read(SITE)[0]['age_sec'] >= 0


class TestSeparateSites:
    """⚠ Лента по сайту, а не одна на всех.

    Селект сайта стоит в шапке, и человек смотрит на выбранный: общая лента
    показывала бы ему забор писем на соседнем вперемешку со своим.
    """

    def test_other_site_does_not_see_it(self, ring):
        dating_feed_write(SITE, 'mail', 'наше')

        assert dating_feed_read(SITE + 1) == []


class TestHandle:
    """Ручка страницы.

    ⚠ **Зовём обработчик в процессе, а не по HTTP** (`testing.md`, §1.2). Сюита
    идёт с хоста, а приложение — в контейнере: подделка кольца живёт здесь, а
    ручка по HTTP отвечала бы из настоящего Redis, которого у сюиты нет. Проверять
    же надо ручку, а не связность контейнеров.
    """

    async def test_handle_answers_with_the_ring(self, ring):
        dating_feed_write(SITE, 'chat', 'Новых сообщений в чате: 3')

        got = (await dating_feed_list(site_id=SITE, user=None))['result']

        assert got[0]['text'] == 'Новых сообщений в чате: 3'

    async def test_no_site_is_an_empty_answer_not_a_refusal(self, ring):
        """⚠ Пусто — не ошибка: круги могли ничего не делать, а Redis — молчать.

        Для показа это одно и то же, и различать их на странице нечем.
        """
        assert (await dating_feed_list(user=None))['result'] == []

    async def test_limit_does_not_exceed_the_ring(self, ring):
        for n in range(5):
            dating_feed_write(SITE, 'mail', f'событие {n}')

        got = (await dating_feed_list(site_id=SITE, limit=9999, user=None))['result']

        assert len(got) == 5
