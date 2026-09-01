"""Кэш собеседников: что показываем, когда снять список не вышло.

Юнит-тесты на саму ручку, мимо HTTP и мимо соседа. Проверяется правило, ради
которого кэш и заведён: **отказ не выбрасывает снимок**. Дождаться настоящего
отказа в нужную секунду нельзя — пул вкладок у соседа общий, и занят он бывает
не по расписанию, — поэтому прогон подменяется здесь.
"""

import asyncio
import json

import pytest

from src.uvicorn.routers.dating import dating_people
from src.uvicorn.routers.dating.dating_people import (DATING_PEOPLE_FLOOR_SEC,
                                                      DATING_PEOPLE_FRESH_SEC,
                                                      _PeopleFailure, dating_people_list)

PROFILE = {'id': 849, 'site_id': 343, 'login': 'diana@example.com'}
SITE = {'id': 343, 'name': 'Ladadate'}
PEOPLE = [{'uid': '812886', 'name': 'Vincent', 'age': 49, 'photo': '', 'photo_uid': '',
           'online': False, 'is_new': False, 'link': ''}]


class _FakeRedis:
    """Redis настолько, насколько его использует кэш: `get` и `set` со сроком.

    Поддельный, а не живой: тесты не должны требовать поднятого Redis, а срок
    годности здесь всё равно проверяется временем внутри снимка, не самим Redis.
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttl[key] = ex


class _Door:
    """Что подменено вокруг ручки: журнал и Redis — чтобы тест мог в них заглянуть."""

    def __init__(self, redis):
        self.redis = redis
        self.log: list[tuple[str, str]] = []


@pytest.fixture
async def door(monkeypatch):
    """Подменяет всё, что ходит наружу: базу, Redis, соседа и журнал.

    Журнал тоже: он пишет в базу, а проверяем мы здесь не запись строки, а то,
    какой список увидит человек.

    Фикстура **асинхронная** не для красоты: уборка гасит фоновые задачи, а
    сделать это можно только пока жив цикл событий. У синхронной фикстуры
    уборка идёт после него, и отмена падает с «Event loop is closed».
    """
    async def _profile(profile_id):
        return dict(PROFILE)

    async def _site(site_id):
        return dict(SITE)

    fake = _Door(_FakeRedis())

    async def _log(profile_id, level, text):
        fake.log.append((level, text))

    monkeypatch.setattr(dating_people, 'dating_profile_row', _profile)
    monkeypatch.setattr(dating_people, 'dating_profile_site', _site)
    monkeypatch.setattr(dating_people, 'dating_log_write', _log)
    monkeypatch.setattr(dating_people, 'redis_conn_get', lambda: fake.redis)

    _state_clear()

    yield fake

    await _state_stop()
    _state_clear()


async def _state_stop() -> None:
    """Гасит незавершённые фоновые задачи и дожидается их конца.

    Часть тестов подменяет прогон вечным ожиданием. Отменить задачу мало —
    отмена доходит до неё только на следующем шаге цикла, и без ожидания
    `asyncio` ругается «Task was destroyed but it is pending» уже после того,
    как тест прошёл.
    """
    tasks = [t for t in dating_people._refreshing.values() if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _state_clear() -> None:
    """Сбрасывает память процесса между тестами: сторожа обновления и его ошибку.

    Она переживает запросы намеренно (в этом и смысл), а значит переживёт и тест —
    и следующий получил бы чужой отказ или закрытого сторожа.

    """
    dating_people._refreshing.clear()
    dating_people._refresh_at.clear()
    dating_people._refresh_error.clear()


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
        свободной может не оказаться. Список недельной давности человеку полезнее
        пустого места с красной надписью — он про людей на сайте, а не про
        состояние чужого пула.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot',
                            _snapshot(fails='браузер соседа занят: предел сессий исчерпан: 25'))
        _age(door, 849, 'favorite', DATING_PEOPLE_FRESH_SEC + 1)
        await _refreshed(849, 'favorite')

        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['people'] == PEOPLE, 'снимок выброшен при отказе'
        assert answer['cached'] is True

    async def test_reason_is_not_swallowed(self, door, monkeypatch):
        """Причина сорвавшегося обновления доезжает до страницы.

        Прогон идёт в фоне, и ответ ручки уходит раньше, чем тот доберётся до
        конца. Без памяти об ошибке колонка показывала бы недельный список
        бесконечно, ни словом не обмолвившись, что новый не снимается.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        _age(door, 849, 'favorite', DATING_PEOPLE_FRESH_SEC + 1)

        # Первый запрос заводит обновление и уходит со старым снимком; жалоба
        # появится в следующем — том, который человек увидит через круг опроса.
        await dating_people_list(849, 'favorite', fresh=False, user=None)
        await _refreshed(849, 'favorite')

        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert 'браузер соседа занят' in answer['error']
        assert answer['taken_at'], 'без времени снимка непонятно, насколько он старый'

    async def test_reason_clears_after_success(self, door, monkeypatch):
        """Удачное обновление стирает прежнюю жалобу: держать её значило бы пугать
        человека ошибкой, которой больше нет."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['error'] == ''

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

        assert door.log and door.log[-1][0] == 'fail'
        assert 'Избранные' in door.log[-1][1] and 'браузер соседа занят' in door.log[-1][1]

    async def test_success_goes_to_the_log_too(self, door, monkeypatch):
        """Удачный прогон — тоже строка: по ней видно, когда список снимали и
        сколько тогда было народу."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        assert door.log[-1][0] == 'ok'
        assert '1 собеседников' in door.log[-1][1]


class TestStaleAnswersAtOnce:
    """⚠ Устаревший снимок отдаётся сразу, а сценарий идёт в фоне."""

    async def test_stale_returns_without_waiting(self, door, monkeypatch):
        """Ждать прогон, имея на руках список, — значит держать человека сорок
        секунд на слове «гружу…» вместо ответа, который у нас уже есть."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        # Прогон, который никогда не кончится: дождись его ручка — тест бы завис.
        async def _never(profile, site, kind):
            await asyncio.sleep(3600)

        monkeypatch.setattr(dating_people, '_snapshot', _never)
        _age(door, 849, 'favorite', DATING_PEOPLE_FRESH_SEC + 1)

        answer = await asyncio.wait_for(
            dating_people_list(849, 'favorite', fresh=False, user=None), timeout=2)

        assert answer['result']['people'] == PEOPLE
        assert answer['result']['refreshing'] is True, 'обновление не завелось'

    async def test_second_ask_does_not_start_second_run(self, door, monkeypatch):
        """Пока прогон идёт, второй не заводится.

        Страница спрашивает список, ожидая результата, и без сторожа каждый опрос
        плодил бы ещё один вход на сайт под той же анкетой.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        runs = []

        async def _slow(profile, site, kind):
            runs.append(kind)
            await asyncio.sleep(3600)

        monkeypatch.setattr(dating_people, '_snapshot', _slow)
        _age(door, 849, 'favorite', DATING_PEOPLE_FRESH_SEC + 1)

        for _ in range(5):
            await dating_people_list(849, 'favorite', fresh=False, user=None)
        await asyncio.sleep(0)

        assert len(runs) == 1, f'заведено прогонов: {len(runs)}'

    async def test_failed_refresh_is_not_retried_at_once(self, door, monkeypatch):
        """⚠ Сорвавшийся прогон не запускается заново на каждом опросе.

        Первый сторож («уже идёт») этот случай пропускает: задача-то завершилась.
        Без второго страница гоняла бы вход на сайт раз в несколько секунд —
        ровно то, от чего бережёт кэш.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        runs = []

        async def _fails(profile, site, kind):
            runs.append(kind)
            raise _PeopleFailure('браузер соседа занят')

        monkeypatch.setattr(dating_people, '_snapshot', _fails)
        _age(door, 849, 'favorite', DATING_PEOPLE_FRESH_SEC + 1)

        for _ in range(5):
            await dating_people_list(849, 'favorite', fresh=False, user=None)
            await asyncio.sleep(0)
        await _refreshed(849, 'favorite')

        assert len(runs) == 1, f'сорвавшийся прогон повторился {len(runs)} раз'


class TestFreshness:
    """Два порога: выдержка и жёсткий пол."""

    async def test_fresh_snapshot_does_not_touch_the_site(self, door, monkeypatch):
        """Свежий снимок отдаётся не спрашивая сайт — ради этого кэш и существует."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['cached'] is True and answer['error'] == ''
        assert answer['refreshing'] is False, 'свежий снимок не нуждается в обновлении'

    async def test_floor_holds_against_the_button(self, door, monkeypatch):
        """⚠ Жёсткий пол не обходит даже «обновить».

        Нажать кнопку десять раз подряд легко, и без пола это были бы десять
        входов в кабинет за минуту: сайт считает такое подозрением и отвечает
        блокировкой, а блокировка стоит анкеты, а не запроса.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        _age(door, 849, 'favorite', DATING_PEOPLE_FLOOR_SEC - 2)
        answer = (await dating_people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['cached'] is True and answer['error'] == ''
        assert answer['refreshing'] is False, 'кнопка пробила жёсткий пол'


class TestCacheKeeps:
    """Снимок хранится неделю и переживает перезапуск процесса."""

    async def test_snapshot_kept_for_a_week(self, door, monkeypatch):
        """Срок годности — неделя. Не «сколько он годен», а «сколько его есть чем
        заменить»: пока сценарий не принёс новый, старый — всё, что мы знаем."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        assert door.redis.ttl['dating:people:849:favorite'] == 7 * 24 * 3600

    async def test_week_old_snapshot_is_still_shown(self, door, monkeypatch):
        """Недельный снимок показывается, а не заменяется словом «гружу…»."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await dating_people_list(849, 'favorite', fresh=False, user=None)

        async def _never(profile, site, kind):
            await asyncio.sleep(3600)

        monkeypatch.setattr(dating_people, '_snapshot', _never)
        _age(door, 849, 'favorite', 6 * 24 * 3600)

        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['people'] == PEOPLE
        assert answer['cached'] is True
        assert answer['age_sec'] > 5 * 24 * 3600

    async def test_broken_redis_does_not_break_the_handler(self, door, monkeypatch):
        """⚠ Сбой Redis не роняет ручку.

        Кэш — ускорение, а не источник правды: не прочитался — сходим к соседу,
        как в первый раз. Ронять из-за этого страницу значило бы менять
        «медленно» на «никак».
        """
        class _Broken:
            def get(self, key):
                raise RuntimeError('redis лёг')

            def set(self, key, value, ex=None):
                raise RuntimeError('redis лёг')

        monkeypatch.setattr(dating_people, 'redis_conn_get', lambda: _Broken())
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))

        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['people'] == PEOPLE


async def _refreshed(profile_id: int, kind: str) -> None:
    """Дожидается фонового обновления, если оно заведено."""
    task = dating_people._refreshing.get((profile_id, kind))
    if task:
        await task


def _age(door, profile_id: int, kind: str, seconds: float) -> None:
    """Состаривает снимок в кэше — иначе тест ждал бы настоящие пять минут."""
    key = f'dating:people:{profile_id}:{kind}'
    snapshot = json.loads(door.redis.store[key])
    snapshot['at'] -= seconds
    door.redis.store[key] = json.dumps(snapshot, ensure_ascii=False)
