"""Избранные: разбор состояния звёздочки и отбор «кого добавлять».

⚠ **Наружу сюита не ходит** (`testing.md`): нажатие звёздочки — это вход браузером
под живой анкетой и ~40 секунд, а ещё и **действие на чужом сайте**, которое видит
живой человек. Проверяется то, что и ломается, — две чистые функции:

* `dating_favorite_state` — что случилось, по классу до и после. Классы меняет сайт,
  а не мы, и образцы здесь **живые**: сняты пробными прогонами под анкетой 849.
* `dating_favorite_pick` — кого брать в заход. Здесь всё решается у нас, и цена
  ошибки высока в обе стороны: пропустим — человек не попадёт в избранные, возьмём
  лишнего — сорок секунд браузера, а то и снятая звёздочка.
"""

import pytest

from src.dating.dating_favorite import (DATING_FAVORITE_ALREADY, DATING_FAVORITE_DONE,
                                        DATING_FAVORITE_FAIL,
                                        DATING_FAVORITE_TRIES_MAX,
                                        dating_favorite_pick, dating_favorite_state)

pytestmark = pytest.mark.mail

# Живые классы звёздочки, слово в слово с прогонов. ⚠ Не выдуманные: выдуманные
# проверяли бы нашу мысль о разметке, а не разметку.
#
# ⚠ У «до» два пробела подряд — там, где у избранного стоит класс-признак. Так сайт
# и отдаёт: класс собирается шаблоном, и на месте признака остаётся пустота.
BEFORE_EMPTY = 'favorite__status  profile_btn-friend'
AFTER_CLICK = ('favorite__status profile_btn-friend profile_btn-friend_disable '
               'favorite__status-ok')
ALREADY_FAVORITE = 'favorite__status favorite__status-ok profile_btn-friend'


class TestState:
    """Что случилось со звёздочкой."""

    def test_pressed_star_lights_up(self):
        """Не горела, нажали — загорелась. Это наша работа, и она удалась."""
        assert dating_favorite_state(BEFORE_EMPTY, AFTER_CLICK) \
               == (DATING_FAVORITE_DONE, '')

    def test_already_favorite_is_not_done(self):
        """⚠ Горела до нас — `already`, а не `done`.

        Для круга разницы нет (оба значат «больше не ходить»), но событие разное:
        `done` сделала панель, `already` было и так. Свести их значило бы потерять
        ответ на «а мы-то что сделали».
        """
        assert dating_favorite_state(ALREADY_FAVORITE, ALREADY_FAVORITE) \
               == (DATING_FAVORITE_ALREADY, '')

    def test_star_that_did_not_light_up_is_a_failure(self):
        """Нажали, а класс не появился — нажатие не дошло до сайта."""
        state, error = dating_favorite_state(BEFORE_EMPTY, BEFORE_EMPTY)

        assert state == DATING_FAVORITE_FAIL
        assert error

    def test_empty_after_is_a_failure_not_success(self):
        """⚠ Пустое «после» — отказ, а не «не изменилось».

        Пусто значит, что шага не было или звёздочку не нашли. Ответить на это
        «уже в избранных» значило бы выдумать удачу там, где мы ничего не видели.
        """
        state, error = dating_favorite_state(ALREADY_FAVORITE, '')

        assert state == DATING_FAVORITE_FAIL
        assert error

    def test_class_is_read_by_whole_word(self):
        """Признак ищется целым классом, а не подстрокой.

        `favorite__status-ok-hidden` — не зажжённая звезда, и попадись такой класс,
        подстрочный поиск счёл бы человека избранным и больше к нему не пришёл.
        """
        state, _ = dating_favorite_state(BEFORE_EMPTY,
                                         'favorite__status favorite__status-ok-hidden')

        assert state == DATING_FAVORITE_FAIL

    def test_several_stars_on_the_page_count_as_one_lit(self):
        """⚠ `extract` отдаёт все совпадения через перевод строки.

        Звёздочка на анкете одна, но второй узел с тем же классом (всплывшая
        карточка, скрытый шаблон) превратил бы строку в две. Зажжённая звезда
        где-то на странице означает, что он в избранных.
        """
        assert dating_favorite_state(BEFORE_EMPTY, f'{BEFORE_EMPTY}\n{ALREADY_FAVORITE}') \
               == (DATING_FAVORITE_DONE, '')


class TestPick:
    """Кого брать в заход."""

    def test_those_in_the_site_list_are_left_alone(self):
        """Кто уже в избранных на сайте — того не трогаем вовсе.

        ⚠ Не из экономии: кнопка на сайте это **переключатель**, и нажатие по уже
        избранному сняло бы звёздочку.
        """
        assert dating_favorite_pick(['1', '2', '3'], {'2'}, {}, 10) == ['1', '3']

    def test_settled_are_skipped_even_without_the_site_list(self):
        """⚠ Удачу помним и без списка сайта.

        Список мог не прийти вовсе (сайт молчит, API не настроен), и пустое
        множество не должно означать «добавить всех заново».
        """
        settled = {'1': {'state': DATING_FAVORITE_DONE, 'tries': 0},
                   '2': {'state': DATING_FAVORITE_ALREADY, 'tries': 0}}

        assert dating_favorite_pick(['1', '2', '3'], set(), settled, 10) == ['3']

    def test_failure_is_retried_until_the_cap(self):
        """Отказ повторяем: причина обычно временная — занятая вкладка, сеть."""
        settled = {'1': {'state': DATING_FAVORITE_FAIL, 'tries': 1}}

        assert dating_favorite_pick(['1'], set(), settled, 10) == ['1']

    def test_failure_stops_at_the_cap(self):
        """⚠ Но не вечно: три захода — уже про причину, которая сама не пройдёт."""
        settled = {'1': {'state': DATING_FAVORITE_FAIL,
                         'tries': DATING_FAVORITE_TRIES_MAX}}

        assert dating_favorite_pick(['1'], set(), settled, 10) == []

    def test_batch_cuts_the_tail_keeping_order(self):
        """Потолок отрезает хвост, а порядок задают письма — свежие первыми.

        Человек, написавший минуту назад, ждёт звёздочки больше, чем тот, чьё письмо
        лежит с прошлой недели.
        """
        assert dating_favorite_pick(['9', '8', '7', '6'], set(), {}, 2) == ['9', '8']

    def test_zero_batch_takes_nobody(self):
        """Потолок в ноль — заход без работы, а не заход без потолка."""
        assert dating_favorite_pick(['1', '2'], set(), {}, 0) == []

    def test_repeated_sender_is_taken_once(self):
        """Два письма от одного человека — одна звёздочка."""
        assert dating_favorite_pick(['5', '5', '6'], set(), {}, 10) == ['5', '6']

    def test_empty_uid_is_not_a_person(self):
        """Пустой номер из базы — мусор, а не собеседник: на него не ходим."""
        assert dating_favorite_pick(['', '  ', '7'], set(), {}, 10) == ['7']
