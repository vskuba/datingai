"""Кэш собеседников: что показываем, когда снять список не вышло.

Юнит-тесты на саму ручку, мимо HTTP и мимо соседа. Проверяется правило, ради
которого кэш и заведён: **отказ не выбрасывает снимок**. Дождаться настоящего
отказа в нужную секунду нельзя — пул вкладок у соседа общий, и занят он бывает
не по расписанию, — поэтому прогон подменяется здесь.

## ⚠ Ручка не ждёт прогона никогда

Из этого растёт форма почти каждого теста здесь. Ответ уходит сразу — со снимком,
если он есть, и пустым, если его нет, — а прогон идёт **в фоне** и кладёт снимок
позже. Поэтому «снять список» в тесте — это три шага: спросить, дождаться фоновой
задачи, спросить снова (`_fill`). Ровно то же делает страница: рисует кэш, шлёт
запрос, возвращается за результатом.
"""

import asyncio
import json

import pytest
from fastapi import HTTPException

from src.uvicorn.routers.dating import dating_people
from src.uvicorn.routers.dating.dating_people import (DATING_PEOPLE_FLOOR_SEC,
                                                      DATING_PEOPLE_FRESH_SEC,
                                                      _PeopleFailure, dating_people_list)
from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

PROFILE = {'id': 849, 'site_id': 343, 'login': 'diana@example.com'}
SITE = {'id': 343, 'name': 'Ladadate'}
PEOPLE = [{'uid': '812886', 'name': 'Vincent', 'age': 49, 'photo': '', 'photo_uid': '',
           'online': False, 'is_new': False, 'link': ''}]

# Ключи снимков — те же, что считает сама ручка. Через `_key`, а не строкой:
# ключ служит и именем снимка в Redis, и именем сторожа фонового обновления, и
# второе его написание разошлось бы с первым.
KEY = dating_people._key('favorite', profile_id=PROFILE['id'])
SITE_KEY = dating_people._key('online', site_id=SITE['id'])


class _FakeRedis:
    """Redis настолько, насколько его использует кэш: `get`, `set` со сроком, `delete`.

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

    def delete(self, key):
        self.store.pop(key, None)
        self.ttl.pop(key, None)


class _Door:
    """Что подменено вокруг ручки: журнал, Redis и срок хранения."""

    def __init__(self, redis):
        self.redis = redis
        self.log: list[tuple[str, str]] = []
        # Сколько держим снимок. Настройка сайта в живой панели; здесь — поле,
        # которое тест может подкрутить.
        self.keep = dating_people.DATING_PEOPLE_KEEP_SEC


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

    # Срок хранения задаёт настройка сайта, а та лежит в MySQL. Подменяем: юнит-тест
    # не должен требовать поднятой базы, а проверяем мы здесь не чтение настройки.
    async def _keep(site_id, key):
        return fake.keep

    monkeypatch.setattr(dating_people, 'dating_profile_row', _profile)
    monkeypatch.setattr(dating_people, 'dating_profile_site', _site)
    monkeypatch.setattr(dating_people, 'dating_log_write', _log)
    monkeypatch.setattr(dating_people, 'redis_conn_get', lambda: fake.redis)
    monkeypatch.setattr(dating_people, 'dating_site_seconds', _keep)

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
    """Сбрасывает память процесса между тестами: сторожа обновления, его ошибку и
    подпорку кэша.

    Она переживает запросы намеренно (в этом и смысл), а значит переживёт и тест —
    и следующий получил бы чужой отказ, закрытого сторожа или снимок из подпорки.
    """
    dating_people._refreshing.clear()
    dating_people._refresh_at.clear()
    dating_people._refresh_error.clear()
    dating_people._CACHE_FALLBACK.clear()


def _settings(values: dict):
    """Подменённое чтение настроек сайта: ключ → значение, как их правят на странице."""
    async def _setting(site_id, key, default=''):
        return values.get(key, default)

    return _setting


def _site_profile(found: bool = True):
    """Подменённый выбор анкеты сайта."""
    async def _pick(site_id):
        return dict(PROFILE) if found else {}

    return _pick


def _snapshot(returns=None, fails=''):
    """Подменённый прогон: отдаёт записи или падает с заданной причиной."""
    async def _run(profile, kind):
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
        await _fill()

        monkeypatch.setattr(dating_people, '_snapshot',
                            _snapshot(fails='браузер соседа занят: предел сессий исчерпан: 25'))
        answer = (await _retry(door))['result']

        assert answer['people'] == PEOPLE, 'снимок выброшен при отказе'
        assert answer['cached'] is True

    async def test_reason_is_not_swallowed(self, door, monkeypatch):
        """Причина сорвавшегося обновления доезжает до страницы.

        Прогон идёт в фоне, и ответ ручки уходит раньше, чем тот доберётся до
        конца. Без памяти об ошибке колонка показывала бы недельный список
        бесконечно, ни словом не обмолвившись, что новый не снимается.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        answer = (await _retry(door))['result']

        assert 'браузер соседа занят' in answer['error']
        assert answer['taken_at'], 'без времени снимка непонятно, насколько он старый'

    async def test_reason_clears_after_success(self, door, monkeypatch):
        """Удачное обновление стирает прежнюю жалобу: держать её значило бы пугать
        человека ошибкой, которой больше нет."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        await dating_people_list(849, 'favorite', fresh=False, user=None)
        await _refreshed(KEY)

        # Выдержку между попытками сбрасываем: тест проверяет не её, а то, что
        # удачное обновление стирает прежнюю жалобу.
        dating_people._refresh_at.clear()
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        answer = await _fill()

        assert answer['error'] == ''

    async def test_unreachable_neighbour_returns_cached_list(self, door, monkeypatch):
        """⚠ Недоступный сосед тоже не гасит колонку.

        Сайт живёт у соседа, и `dating_profile_site` при его недоступности
        отвечает 502. Стой этот вызов в ручке до чтения кэша — лежащий сосед
        уносил бы готовый снимок вместе с собой, хотя показать его ничто не
        мешает. Так и было; поймано сверкой кода с документацией.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        async def _no_site(site_id):
            raise HTTPException(status_code=502, detail='RhapsodyAi недоступен')

        monkeypatch.setattr(dating_people, 'dating_profile_site', _no_site)
        _stale(door, KEY)

        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['people'] == PEOPLE, 'снимок унесён вместе с недоступным соседом'
        assert answer['cached'] is True

    async def test_failure_without_cache_is_still_200(self, door, monkeypatch):
        """Нечего показать — всё равно ответ, а не 502.

        Код ответа говорит про запрос, а не про то, что вышло на живом сайте.
        Страница должна показать причину, а не «шлюз не отвечает».
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='сосед не отвечает'))

        # Первый ответ — пустой и без причины: прогон только начался. Причина
        # появляется в следующем, когда фон уже упал.
        first = (await dating_people_list(849, 'online', fresh=False, user=None))['result']

        assert first['people'] == []
        assert first['refreshing'] is True
        assert first['age_sec'] == -1, 'возраст 0 у несуществующего снимка соврал бы свежестью'
        assert first['taken_at'] == ''

        await _refreshed(dating_people._key('online', profile_id=849))
        second = (await dating_people_list(849, 'online', fresh=False, user=None))['result']

        assert 'сосед не отвечает' in second['error']

    async def test_failure_goes_to_the_log(self, door, monkeypatch):
        """Отказ пишется в журнал анкеты: там его и ищут, разбирая, почему список
        не обновляется."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        await dating_people_list(849, 'favorite', fresh=False, user=None)
        await _refreshed(KEY)

        assert door.log and door.log[-1][0] == 'fail'
        assert 'Избранные' in door.log[-1][1] and 'браузер соседа занят' in door.log[-1][1]

    async def test_success_goes_to_the_log_too(self, door, monkeypatch):
        """Удачный прогон — тоже строка: по ней видно, когда список снимали и
        сколько тогда было народу."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        assert door.log[-1][0] == 'ok'
        assert '1 собеседников' in door.log[-1][1]


class TestStaleAnswersAtOnce:
    """⚠ Устаревший снимок отдаётся сразу, а сценарий идёт в фоне."""

    async def test_stale_returns_without_waiting(self, door, monkeypatch):
        """Ждать прогон, имея на руках список, — значит держать человека сорок
        секунд на слове «гружу…» вместо ответа, который у нас уже есть."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        # Прогон, который никогда не кончится: дождись его ручка — тест бы завис.
        async def _never(profile, kind):
            await asyncio.sleep(3600)

        monkeypatch.setattr(dating_people, '_snapshot', _never)
        _stale(door, KEY)

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
        await _fill()

        runs = []

        async def _slow(profile, kind):
            runs.append(kind)
            await asyncio.sleep(3600)

        monkeypatch.setattr(dating_people, '_snapshot', _slow)
        _stale(door, KEY)

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
        await _fill()

        runs = []

        async def _fails(profile, kind):
            runs.append(kind)
            raise _PeopleFailure('браузер соседа занят')

        monkeypatch.setattr(dating_people, '_snapshot', _fails)
        _stale(door, KEY)

        for _ in range(5):
            await dating_people_list(849, 'favorite', fresh=False, user=None)
            await asyncio.sleep(0)
        await _refreshed(KEY)

        assert len(runs) == 1, f'сорвавшийся прогон повторился {len(runs)} раз'


class TestSiteScope:
    """⚠ «Кто в сети» — свойство сайта, а не анкеты."""

    async def test_site_list_has_its_own_key(self, door, monkeypatch):
        """Снимок по сайту лежит отдельно от снимков по анкетам.

        Список мужчин в сети один и тот же под любой учёткой, и держать его для
        каждой анкеты значило бы снимать одно и то же по разу на анкету — десять
        входов на сайт вместо одного, ради одинакового ответа.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        monkeypatch.setattr(dating_people, '_site_profile', _site_profile())

        await dating_people.dating_people_site_list(343, 'online', fresh=False, user=None)
        await _refreshed(SITE_KEY)

        assert SITE_KEY in door.redis.store
        assert dating_people._key('online', profile_id=849) not in door.redis.store

    async def test_second_profile_does_not_run_it_again(self, door, monkeypatch):
        """Щелчок по другой анкете не гонит сценарий заново.

        Ради этого список и переехал на сайт: страница спрашивает его при каждой
        отрисовке вкладки, а вкладка перерисовывается на каждый выбор анкеты.
        """
        runs = []

        async def _count(profile, kind):
            runs.append(kind)

            return list(PEOPLE)

        monkeypatch.setattr(dating_people, '_snapshot', _count)
        monkeypatch.setattr(dating_people, '_site_profile', _site_profile())

        for _ in range(3):
            await dating_people.dating_people_site_list(343, 'online', fresh=False, user=None)
            await asyncio.sleep(0)

        assert len(runs) == 1, f'сценарий прогнался {len(runs)} раз вместо одного'

    async def test_site_without_profiles_is_422(self, door, monkeypatch):
        """Анкета всё равно нужна: сценарий заходит в кабинет, а кабинет чей-то.

        Нет ни одной — говорим прямо, а не отдаём пустой список: пустота читалась
        бы как «на сайте никого нет».
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        monkeypatch.setattr(dating_people, '_site_profile', _site_profile(found=False))

        with pytest.raises(HTTPException) as refusal:
            await dating_people.dating_people_site_list(343, 'online', fresh=False, user=None)

        assert refusal.value.status_code == 422

    async def test_site_list_stays_out_of_a_profile_journal(self, door, monkeypatch):
        """⚠ В журнал анкеты список по сайту не пишется.

        Анкету для входа выбирают за человека, и приписать ей строку «Кто в сети:
        20 собеседников» значило бы сказать, что это её работа. След прогона при
        этом остаётся в журнале прогонов — там он честно за той учёткой, под
        которой заходили.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        monkeypatch.setattr(dating_people, '_site_profile', _site_profile())

        await dating_people.dating_people_site_list(343, 'online', fresh=False, user=None)
        await _refreshed(SITE_KEY)

        assert door.log == []


class TestSource:
    """⚠ Откуда берётся список — настройка сайта, а не выбор кода."""

    async def test_api_site_does_not_touch_the_neighbour(self, door, monkeypatch, site_api):
        """Сайт с настроенным API снимает список сам: ни сценария, ни вкладки в пуле.

        Ради этого всё и делалось. Проверяем не «быстрее» (быстроту тестом не
        поймать), а то, за что платили: к соседу не ходим вовсе.
        """
        monkeypatch.setattr(dating_people, 'dating_site_setting', _settings(
            {'people_source': 'api', 'api_base': SITE_API_FAKE_BASE,
             'api_password': SITE_API_FAKE_PASSWORD}))

        async def _нельзя(*args, **kwargs):
            raise AssertionError('сценарий у соседа всё-таки прогнали')

        monkeypatch.setattr(dating_people, 'dating_scenario_run', _нельзя)
        monkeypatch.setattr(dating_people, 'dating_profile_site', _нельзя)

        answer = await _fill()

        assert [row['uid'] for row in answer['people']] == ['788856']

    async def test_own_password_wins_over_the_site_one(self, door, monkeypatch, site_api):
        """⚠ Свой пароль анкеты сильнее общего.

        Общий заводят агентству оптом; окажись он сильнее, анкета со своей парой
        логин-пароль молча ходила бы под чужим и получала 400.
        """
        monkeypatch.setattr(dating_people, 'dating_site_setting', _settings(
            {'people_source': 'api', 'api_base': SITE_API_FAKE_BASE,
             'api_password': 'общий-неверный'}))

        async def _profile(profile_id):
            return {**PROFILE, 'password': SITE_API_FAKE_PASSWORD}

        monkeypatch.setattr(dating_people, 'dating_profile_row', _profile)

        assert (await _fill())['people'], 'вошли под общим паролем вместо своего'

    async def test_site_without_api_still_runs_the_scenario(self, door, monkeypatch):
        """Сайт без API работает как работал: новая настройка ничего не ломает."""
        monkeypatch.setattr(dating_people, 'dating_site_setting', _settings({}))
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))

        assert (await _fill())['people'] == PEOPLE

    async def test_chosen_api_without_address_is_not_api(self, door, monkeypatch):
        """Источник выбран, а адреса нет — это опечатка в настройке, а не повод
        молча ходить не туда. Работаем сценарием."""
        monkeypatch.setattr(dating_people, 'dating_site_setting', _settings(
            {'people_source': 'api'}))
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))

        assert (await _fill())['people'] == PEOPLE

    async def test_api_refusal_keeps_the_cache(self, door, monkeypatch, site_api):
        """Отказ сайта ведёт себя как отказ соседа: прежний список остаётся,
        причина едет рядом. Кэш об источнике не знает, и это правильно."""
        monkeypatch.setattr(dating_people, 'dating_site_setting', _settings(
            {'people_source': 'api', 'api_base': SITE_API_FAKE_BASE,
             'api_password': SITE_API_FAKE_PASSWORD}))
        await _fill()

        site_api.refuse(403, {'Error': 'Too many requests'}, on='/users/')
        answer = (await _retry(door))['result']

        assert [row['uid'] for row in answer['people']] == ['788856'], 'снимок выброшен'
        assert 'сайт просит подождать' in answer['error']


class TestFreshness:
    """Два порога: выдержка и жёсткий пол."""

    async def test_fresh_snapshot_does_not_touch_the_site(self, door, monkeypatch):
        """Свежий снимок отдаётся не спрашивая сайт — ради этого кэш и существует."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        # Выдержку между попытками снимаем: держать сайт должна свежесть снимка, а
        # не сторож повторных попыток. Иначе тест прошёл бы и без кэша вовсе.
        dating_people._refresh_at.clear()
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
        await _fill()

        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        _stale(door, KEY, DATING_PEOPLE_FLOOR_SEC - 2)
        answer = (await dating_people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['cached'] is True and answer['error'] == ''
        assert answer['refreshing'] is False, 'кнопка пробила жёсткий пол'

    async def test_button_works_right_after_a_run(self, door, monkeypatch):
        """⚠ Кнопка «обновить» не ждёт выдержки между попытками.

        Иначе она молча ничего не делала: минуту назад прошёл удачный прогон,
        человек видит список полуторной минуты и нажимает «⟳» — а обновление не
        заводится, потому что выдержка (пять минут) ещё не кончилась. Ни вращения,
        ни новых имён; кнопка выглядит сломанной.

        Выдержку кнопка обходит по своему смыслу, пол — нет: он про предел сайта.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        runs = []

        async def _count(profile, kind):
            runs.append(kind)

            return list(PEOPLE)

        monkeypatch.setattr(dating_people, '_snapshot', _count)
        # Минута с прошлой попытки: выдержка (пять минут) не кончилась, пол
        # (пятнадцать секунд) — давно. Отодвигаем и снимок, и время попытки: с
        # точки зрения ручки это одно событие — прогон, который минуту назад лёг
        # в кэш.
        _age(door, KEY, 60)
        dating_people._refresh_at[KEY] -= 60
        answer = (await dating_people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['refreshing'] is True, 'кнопка ничего не завела'
        assert answer['people'] == PEOPLE, 'кнопка убрала список, пока снимает новый'

        await _refreshed(KEY)

        assert len(runs) == 1


class TestCacheKeeps:
    """Снимок хранится неделю и переживает перезапуск процесса."""

    async def test_snapshot_kept_for_a_week(self, door, monkeypatch):
        """Срок годности — неделя. Не «сколько он годен», а «сколько его есть чем
        заменить»: пока сценарий не принёс новый, старый — всё, что мы знаем."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        assert door.redis.ttl[KEY] == 7 * 24 * 3600

    async def test_week_old_snapshot_is_still_shown(self, door, monkeypatch):
        """Недельный снимок показывается, а не заменяется словом «гружу…»."""
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        async def _never(profile, kind):
            await asyncio.sleep(3600)

        monkeypatch.setattr(dating_people, '_snapshot', _never)
        _stale(door, KEY, 6 * 24 * 3600)

        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['people'] == PEOPLE
        assert answer['cached'] is True
        assert answer['age_sec'] > 5 * 24 * 3600

    async def test_snapshot_past_the_setting_is_dropped(self, door, monkeypatch):
        """⚠ Срок из настройки сайта проверяется **при показе**, а не только сроком
        Redis.

        Снимок мог лечь с прежним, длинным сроком, а настройку после этого
        укоротили: Redis о правке не знает и додержит его до своего конца. Показать
        в таком случае старое значило бы не выполнить то, о чём попросили.
        """
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        door.keep = 24 * 3600
        _stale(door, KEY, 2 * 24 * 3600)

        answer = (await dating_people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['people'] == [], 'снимок старше настройки всё ещё показывается'
        assert KEY not in door.redis.store, 'просроченный снимок оставлен в Redis'

    async def test_broken_redis_does_not_break_the_handler(self, door, monkeypatch):
        """⚠ Сбой Redis не роняет ручку и не съедает снятый список.

        Ронять страницу значило бы менять «медленно» на «никак». А поскольку
        результат фонового прогона доезжает до страницы **только через кэш**,
        «никак» получилось бы и без падения: сорок секунд работы браузера ушли бы
        в пустоту. Поэтому на время недоступности Redis есть подпорка в памяти.
        """
        class _Broken:
            def get(self, key):
                raise RuntimeError('redis лёг')

            def set(self, key, value, ex=None):
                raise RuntimeError('redis лёг')

            def delete(self, key):
                raise RuntimeError('redis лёг')

        monkeypatch.setattr(dating_people, 'redis_conn_get', lambda: _Broken())
        monkeypatch.setattr(dating_people, '_snapshot', _snapshot(PEOPLE))

        answer = await _fill()

        assert answer['people'] == PEOPLE


async def _fill(profile_id: int = PROFILE['id'], kind: str = 'favorite') -> dict:
    """Первый заход целиком: спросить, дождаться фона, спросить снова.

    ⚠ **Ручка не ждёт прогона никогда** (`api.md`, §3.3): первый ответ приходит
    пустым, со `refreshing`, а снимок кладёт фоновая задача. Поэтому «получить
    список» — это два запроса и ожидание между ними, и тест повторяет ровно то, что
    делает страница.
    """
    key = dating_people._key(kind, profile_id=profile_id)

    await dating_people_list(profile_id, kind, fresh=False, user=None)
    await _refreshed(key)

    return (await dating_people_list(profile_id, kind, fresh=False, user=None))['result']


async def _retry(door, profile_id: int = PROFILE['id'], kind: str = 'favorite') -> dict:
    """Второй заход по устаревшему снимку: состарить, спросить, дождаться фона,
    спросить снова.

    Отдельно от `_fill`, потому что снимок здесь уже есть: первый ответ уходит с
    ним, а увидеть итог обновления (новый список или жалобу) можно только в
    следующем — том, за которым страница вернётся по кругу опроса.
    """
    key = dating_people._key(kind, profile_id=profile_id)

    _stale(door, key)
    await dating_people_list(profile_id, kind, fresh=False, user=None)
    await _refreshed(key)

    return await dating_people_list(profile_id, kind, fresh=False, user=None)


async def _refreshed(key: str) -> None:
    """Дожидается фонового обновления, если оно заведено.

    Ключ сторожа — та же строка, что и ключ снимка в Redis: списки бывают по анкете
    и по сайту, и два вида ключей разошлись бы (`_key` в `dating_people.py`).
    """
    task = dating_people._refreshing.get(key)
    if task:
        await task


def _stale(door, key: str, seconds: float = DATING_PEOPLE_FRESH_SEC + 1) -> None:
    """Состаривает снимок и снимает выдержку между попытками.

    Оба разом, потому что оба нужны, чтобы следующий запрос дошёл до прогона:
    свежий снимок отдаётся без сайта, а сторож повторных попыток держит пять минут
    после **любой** предыдущей попытки — включая ту, которой снимок и положили.
    Разбирались бы тесты с этим по отдельности — половина проверяла бы не то
    правило, которое назвала.
    """
    _age(door, key, seconds)
    dating_people._refresh_at.clear()


def _age(door, key: str, seconds: float) -> None:
    """Состаривает снимок в кэше — иначе тест ждал бы настоящие пять минут."""
    snapshot = json.loads(door.redis.store[key])
    snapshot['at'] -= seconds
    door.redis.store[key] = json.dumps(snapshot, ensure_ascii=False)
