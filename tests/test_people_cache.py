"""Кэш собеседников: что показываем, когда снять список не вышло.

Юнит-тесты на саму ручку, мимо HTTP и мимо соседа. Проверяется правило, ради
которого кэш и заведён: **отказ не выбрасывает снимок**. Дождаться настоящего
отказа в нужную секунду нельзя — пул вкладок у соседа общий, и занят он бывает
не по расписанию, — поэтому прогон подменяется здесь.
"""

import time

import pytest

from src.uvicorn.routers.dating import dating_people
from src.uvicorn.routers.dating.dating_people import (DATING_PEOPLE_FLOOR_SEC,
                                                      _PeopleFailure, dating_people_list)

PROFILE = {'id': 849, 'site_id': 343, 'login': 'diana@example.com'}
SITE = {'id': 343, 'name': 'Ladadate'}
PEOPLE = [{'uid': '812886', 'name': 'Vincent', 'age': 49, 'photo': '', 'photo_uid': '',
           'online': False, 'is_new': False, 'link': ''}]


@pytest.fixture
def door(monkeypatch):
    """Подменяет всё, что ходит наружу: базу, соседа и журнал.

    Журнал тоже: он пишет в базу, а проверяем мы здесь не запись строки, а то,
    какой список увидит человек.
    """
    async def _profile(profile_id):
        return dict(PROFILE)

    async def _site(site_id):
        return dict(SITE)

    written = []

    async def _log(profile_id, level, text):
        written.append((level, text))

    monkeypatch.setattr(dating_people, 'dating_profile_row', _profile)
    monkeypatch.setattr(dating_people, 'dating_profile_site', _site)
    monkeypatch.setattr(dating_people, 'dating_log_write', _log)
    dating_people._people_cache.clear()

    yield written

    dating_people._people_cache.clear()


def _snapshot(returns=None, fails=''):
    """Подменённый прогон: отдаёт записи или падает с заданной причиной."""
    async def _run(profile, site, kind):
        if fails:
            raise _PeopleFailure(fails)

        return list(returns or [])

    return _run


class TestFailureKeepsCache:
    """⚠ Главное правило: не вышло снять заново — отдаём прежнее."""

    async def test_busy_neighbour_returns_cached_list(self, door, monkeypatch):
        """Занятый браузер соседа (409) — не повод показать пустоту.

        Пул Chromium у него на 25 вкладок и общий с его собственными задачами;
        свободной может не оказаться. Список пятиминутной давности человеку
        полезнее пустого места с красной надписью — он про людей на сайте, а не
        про состояние чужого пула.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot',
                            _snapshot(fails='браузер соседа занят: предел сессий исчерпан: 25'))
        _age(849, 'favorite', DATING_PEOPLE_FLOOR_SEC + 1)
        answer = (await dating_people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['people'] == PEOPLE, 'снимок выброшен при отказе'
        assert answer['cached'] is True

    async def test_reason_is_not_swallowed(self, door, monkeypatch):
        """Причина едет рядом со списком.

        Молча показать старое — худший вид вранья: человек смотрит на имена и не
        знает, что список с прошлого часа.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        _age(849, 'favorite', DATING_PEOPLE_FLOOR_SEC + 1)
        answer = (await dating_people_list(849, 'favorite', fresh=True, user=None))['result']

        assert 'браузер соседа занят' in answer['error']
        assert answer['taken_at'], 'без времени снимка непонятно, насколько он старый'

    async def test_failure_without_cache_is_still_200(self, door, monkeypatch):
        """Нечего показать — всё равно ответ, а не 502.

        Код ответа говорит про запрос, а не про то, что вышло на живом сайте.
        Страница должна показать причину, а не «шлюз не отвечает».
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='сосед не отвечает'))
        answer = (await dating_people_list(849, 'online', fresh=False, user=None))['result']

        assert answer['people'] == []
        assert answer['age_sec'] == -1, 'возраст 0 у несуществующего снимка соврал бы свежестью'
        assert answer['taken_at'] == ''
        assert 'сосед не отвечает' in answer['error']

    async def test_failure_goes_to_the_log(self, door, monkeypatch):
        """Отказ пишется в журнал анкеты: там его и ищут, разбирая, почему список
        не обновляется."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        assert door and door[-1][0] == 'fail'
        assert 'Избранные' in door[-1][1] and 'браузер соседа занят' in door[-1][1]

    async def test_success_goes_to_the_log_too(self, door, monkeypatch):
        """Удачный прогон — тоже строка: по ней видно, когда список снимали и
        сколько тогда было народу."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        assert door[-1][0] == 'ok'
        assert '1 собеседников' in door[-1][1]


class TestFreshness:
    """Два порога: выдержка и жёсткий пол."""

    async def test_fresh_snapshot_does_not_touch_the_site(self, door, monkeypatch):
        """Свежий снимок отдаётся не спрашивая сайт — ради этого кэш и существует."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['cached'] is True and answer['error'] == ''

    async def test_floor_holds_against_the_button(self, door, monkeypatch):
        """⚠ Жёсткий пол не обходит даже «обновить».

        Нажать кнопку десять раз подряд легко, и без пола это были бы десять
        входов в кабинет за минуту: сайт считает такое подозрением и отвечает
        блокировкой, а блокировка стоит анкеты, а не запроса.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        _age(849, 'favorite', DATING_PEOPLE_FLOOR_SEC - 2)
        answer = (await dating_people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['cached'] is True and answer['error'] == ''

    async def test_button_passes_the_floor(self, door, monkeypatch):
        """За полом кнопка своё дело делает: выдержку она обходит."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE * 2))
        _age(849, 'favorite', DATING_PEOPLE_FLOOR_SEC + 1)
        answer = (await dating_people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['cached'] is False and len(answer['people']) == 2


def _age(profile_id: int, kind: str, seconds: float) -> None:
    """Состаривает снимок в кэше — иначе тест ждал бы настоящие пять минут."""
    entry = dating_people._people_cache[(profile_id, kind)]
    entry['at'] = time.monotonic() - seconds
