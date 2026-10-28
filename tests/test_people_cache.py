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

from src.uvicorn.routers.dating import people
from src.uvicorn.routers.dating.people import (PEOPLE_FLOOR_SEC,
                                                      PEOPLE_FRESH_SEC,
                                                      _PeopleFailure, people_list)
from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

pytestmark = pytest.mark.profile
# ⚠ **Анкета здесь в сети, и это не украшение фикстуры.** Списку избранных нужен
# вход под самой девушкой, и спящей его не снимают вовсе (`_hold_why`) — тесты
# ниже про кэш, а не про это правило; само правило стережёт `TestOfflineIsNotAsked`.
PROFILE = {'id': 849, 'site_id': 343, 'login': 'diana@example.com', 'status': 'online'}
SITE = {'id': 343, 'name': 'Ladadate'}
PEOPLE = [{'uid': '812886', 'name': 'Vincent', 'age': 49, 'photo': '', 'photo_uid': '',
           'online': False, 'is_new': False, 'link': ''}]

# Ключи снимков — те же, что считает сама ручка. Через `_key`, а не строкой:
# ключ служит и именем снимка в Redis, и именем сторожа фонового обновления, и
# второе его написание разошлось бы с первым.
KEY = people._key('favorite', profile_id=PROFILE['id'])
SITE_KEY = people._key('online', site_id=SITE['id'])


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
    """Что подменено вокруг ручки: журнал, Redis и настройки-сроки."""

    def __init__(self, redis):
        self.redis = redis
        self.log: list[tuple[str, str]] = []
        # Настройки-сроки сайта. ⚠ **По ключу, а не одним значением на все**: их
        # три, и они разной природы — сколько снимок живёт, когда его освежить и
        # чаще чего к сайту не ходить вовсе. Отдай мы неделю на все три, пол стал
        # бы неделей, и проверки «кнопка не пробивает пол» зеленели бы впустую.
        self.seconds = {
            'favorites_keep': people.PEOPLE_KEEP_SEC,
            'online_check_every': people.PEOPLE_FRESH_SEC,
            'cache_floor': people.PEOPLE_FLOOR_SEC,
        }

    @property
    def keep(self) -> int:
        """Срок хранения избранных — им пользуются почти все тесты."""
        return int(self.seconds['favorites_keep'])

    @keep.setter
    def keep(self, value: int) -> None:
        self.seconds['favorites_keep'] = int(value)


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

    # Сроки задают настройки сайта, а те лежат в MySQL. Подменяем: юнит-тест не
    # должен требовать поднятой базы, а проверяем мы здесь не чтение настройки.
    #
    # Неизвестный ключ отдаёт ноль — так же, как живая функция отдаёт ноль на
    # незаданную настройку, и вызывающий подставляет своё умолчание.
    async def _seconds(site_id, key):
        return int(fake.seconds.get(key, 0))

    monkeypatch.setattr(people, 'profile_row', _profile)
    monkeypatch.setattr(people, 'profile_site', _site)
    monkeypatch.setattr(people, 'log_write', _log)
    monkeypatch.setattr(people, 'redis_conn_get', lambda: fake.redis)
    monkeypatch.setattr(people, 'site_seconds', _seconds)

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
    tasks = [t for t in people._refreshing.values() if not t.done()]
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
    people._refreshing.clear()
    people._refresh_at.clear()
    people._refresh_error.clear()
    people._CACHE_FALLBACK.clear()


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


def _site_sleeper(found: bool = True):
    """Подменённый выбор спящей анкеты: та же форма, что у `_site_profile`."""
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
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        monkeypatch.setattr(people, '_snapshot',
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
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        monkeypatch.setattr(people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        answer = (await _retry(door))['result']

        assert 'браузер соседа занят' in answer['error']
        assert answer['taken_at'], 'без времени снимка непонятно, насколько он старый'

    async def test_reason_clears_after_success(self, door, monkeypatch):
        """Удачное обновление стирает прежнюю жалобу: держать её значило бы пугать
        человека ошибкой, которой больше нет."""
        monkeypatch.setattr(people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        await people_list(849, 'favorite', fresh=False, user=None)
        await _refreshed(KEY)

        # Выдержку между попытками сбрасываем: тест проверяет не её, а то, что
        # удачное обновление стирает прежнюю жалобу.
        people._refresh_at.clear()
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        answer = await _fill()

        assert answer['error'] == ''

    async def test_unreachable_neighbour_returns_cached_list(self, door, monkeypatch):
        """⚠ Недоступный сосед тоже не гасит колонку.

        Сайт живёт у соседа, и `profile_site` при его недоступности
        отвечает 502. Стой этот вызов в ручке до чтения кэша — лежащий сосед
        уносил бы готовый снимок вместе с собой, хотя показать его ничто не
        мешает. Так и было; поймано сверкой кода с документацией.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        async def _no_site(site_id):
            raise HTTPException(status_code=502, detail='RhapsodyAi недоступен')

        monkeypatch.setattr(people, 'profile_site', _no_site)
        _stale(door, KEY)

        answer = (await people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['people'] == PEOPLE, 'снимок унесён вместе с недоступным соседом'
        assert answer['cached'] is True

    async def test_failure_without_cache_is_still_200(self, door, monkeypatch):
        """Нечего показать — всё равно ответ, а не 502.

        Код ответа говорит про запрос, а не про то, что вышло на живом сайте.
        Страница должна показать причину, а не «шлюз не отвечает».
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(fails='сосед не отвечает'))

        # Первый ответ — пустой и без причины: прогон только начался. Причина
        # появляется в следующем, когда фон уже упал.
        first = (await people_list(849, 'online', fresh=False, user=None))['result']

        assert first['people'] == []
        assert first['refreshing'] is True
        assert first['age_sec'] == -1, 'возраст 0 у несуществующего снимка соврал бы свежестью'
        assert first['taken_at'] == ''

        await _refreshed(people._key('online', profile_id=849))
        second = (await people_list(849, 'online', fresh=False, user=None))['result']

        assert 'сосед не отвечает' in second['error']

    async def test_failure_goes_to_the_log(self, door, monkeypatch):
        """Отказ пишется в журнал анкеты: там его и ищут, разбирая, почему список
        не обновляется."""
        monkeypatch.setattr(people, '_snapshot', _snapshot(fails='браузер соседа занят'))
        await people_list(849, 'favorite', fresh=False, user=None)
        await _refreshed(KEY)

        assert door.log and door.log[-1][0] == 'fail'
        assert 'Избранные' in door.log[-1][1] and 'браузер соседа занят' in door.log[-1][1]

    async def test_success_goes_to_the_log_too(self, door, monkeypatch):
        """Удачный прогон — тоже строка: по ней видно, когда список снимали и
        сколько тогда было народу."""
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        assert door.log[-1][0] == 'ok'
        assert '1 собеседников' in door.log[-1][1]


class TestStaleAnswersAtOnce:
    """⚠ Устаревший снимок отдаётся сразу, а сценарий идёт в фоне."""

    async def test_stale_returns_without_waiting(self, door, monkeypatch):
        """Ждать прогон, имея на руках список, — значит держать человека сорок
        секунд на слове «гружу…» вместо ответа, который у нас уже есть."""
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        # Прогон, который никогда не кончится: дождись его ручка — тест бы завис.
        async def _never(profile, kind):
            await asyncio.sleep(3600)

        monkeypatch.setattr(people, '_snapshot', _never)
        _stale(door, KEY)

        answer = await asyncio.wait_for(
            people_list(849, 'favorite', fresh=False, user=None), timeout=2)

        assert answer['result']['people'] == PEOPLE
        assert answer['result']['refreshing'] is True, 'обновление не завелось'

    async def test_second_ask_does_not_start_second_run(self, door, monkeypatch):
        """Пока прогон идёт, второй не заводится.

        Страница спрашивает список, ожидая результата, и без сторожа каждый опрос
        плодил бы ещё один вход на сайт под той же анкетой.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        runs = []

        async def _slow(profile, kind):
            runs.append(kind)
            await asyncio.sleep(3600)

        monkeypatch.setattr(people, '_snapshot', _slow)
        _stale(door, KEY)

        for _ in range(5):
            await people_list(849, 'favorite', fresh=False, user=None)
            await asyncio.sleep(0)

        assert len(runs) == 1, f'заведено прогонов: {len(runs)}'

    async def test_failed_refresh_is_not_retried_at_once(self, door, monkeypatch):
        """⚠ Сорвавшийся прогон не запускается заново на каждом опросе.

        Первый сторож («уже идёт») этот случай пропускает: задача-то завершилась.
        Без второго страница гоняла бы вход на сайт раз в несколько секунд —
        ровно то, от чего бережёт кэш.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        runs = []

        async def _fails(profile, kind):
            runs.append(kind)
            raise _PeopleFailure('браузер соседа занят')

        monkeypatch.setattr(people, '_snapshot', _fails)
        _stale(door, KEY)

        for _ in range(5):
            await people_list(849, 'favorite', fresh=False, user=None)
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
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        monkeypatch.setattr(people, '_site_profile', _site_profile())

        await people.people_site_list(343, 'online', fresh=False, user=None)
        await _refreshed(SITE_KEY)

        assert SITE_KEY in door.redis.store
        assert people._key('online', profile_id=849) not in door.redis.store

    async def test_second_profile_does_not_run_it_again(self, door, monkeypatch):
        """Щелчок по другой анкете не гонит сценарий заново.

        Ради этого список и переехал на сайт: страница спрашивает его при каждой
        отрисовке вкладки, а вкладка перерисовывается на каждый выбор анкеты.
        """
        runs = []

        async def _count(profile, kind):
            runs.append(kind)

            return list(PEOPLE)

        monkeypatch.setattr(people, '_snapshot', _count)
        monkeypatch.setattr(people, '_site_profile', _site_profile())

        for _ in range(3):
            await people.people_site_list(343, 'online', fresh=False, user=None)
            await asyncio.sleep(0)

        assert len(runs) == 1, f'сценарий прогнался {len(runs)} раз вместо одного'

    async def test_site_without_profiles_is_422(self, door, monkeypatch):
        """Анкета всё равно нужна: сценарий заходит в кабинет, а кабинет чей-то.

        Нет ни одной включённой — говорим прямо, а не отдаём пустой список:
        пустота читалась бы как «на сайте никого нет».
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        monkeypatch.setattr(people, '_site_profile', _site_profile(found=False))
        monkeypatch.setattr(people, '_site_sleeper', _site_sleeper(found=False))
        monkeypatch.setattr(people, 'site_setting', _settings({}))

        with pytest.raises(HTTPException) as refusal:
            await people.people_site_list(343, 'online', fresh=False, user=None)

        assert refusal.value.status_code == 422
        assert 'включённой анкеты' in refusal.value.detail, \
            'сценарному сайту не про «не в сети» говорить, у него и спящая подходит'

    async def test_site_list_stays_out_of_a_profile_journal(self, door, monkeypatch):
        """⚠ В журнал анкеты список по сайту не пишется.

        Анкету для входа выбирают за человека, и приписать ей строку «Кто в сети:
        20 собеседников» значило бы сказать, что это её работа. След прогона при
        этом остаётся в журнале прогонов — там он честно за той учёткой, под
        которой заходили.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        monkeypatch.setattr(people, '_site_profile', _site_profile())

        await people.people_site_list(343, 'online', fresh=False, user=None)
        await _refreshed(SITE_KEY)

        assert door.log == []


class TestSleepersServeScenarioSites:
    """⚠ Сценарный сайт снимает ленту, даже когда вся смена спит.

    Правило «только в сети» стережёт вход паролем анкеты; на сценарных сайтах
    входа такого нет — сценарий входит в агентскую панель её же логином, а
    анкета служит дверью в кабинет (`_site_sleeper`).
    """

    async def test_sleeping_site_still_runs_the_feed(self, door, monkeypatch):
        """В сети никто не остался — лента всё равно снимается первой включённой."""
        monkeypatch.setattr(people, 'site_setting', _settings({}))
        monkeypatch.setattr(people, '_site_profile', _site_profile(found=False))
        monkeypatch.setattr(people, '_site_sleeper', _site_sleeper())
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))

        await people.people_site_list(343, 'online', fresh=False, user=None)
        await _refreshed(SITE_KEY)

        answer = (await people.people_site_list(343, 'online', fresh=False,
                                                user=None))['result']

        assert [row['uid'] for row in answer['people']] == \
               [row['uid'] for row in PEOPLE], 'спящий сайт остался без ленты'
        assert answer['error'] == ''

    async def test_api_site_does_not_wake_the_sleeping(self, door, monkeypatch):
        """Сайт с своим API держит прежнее правило: спящая анкета там — это вход
        паролем, а входом паролем и был сентябрьский бан (`_site_profile`)."""
        monkeypatch.setattr(people, 'site_setting',
                            _settings({'people_source': 'api',
                                       'api_base': SITE_API_FAKE_BASE}))

        async def _wakes(site_id):
            raise AssertionError('api-сайт разбудил спящую анкету')

        monkeypatch.setattr(people, '_site_profile', _site_profile(found=False))
        monkeypatch.setattr(people, '_site_sleeper', _wakes)

        with pytest.raises(HTTPException) as refusal:
            await people.people_site_list(343, 'online', fresh=False, user=None)

        assert refusal.value.status_code == 422
        assert 'не в сети' in refusal.value.detail


class TestSource:
    """⚠ Откуда берётся список — настройка сайта, а не выбор кода."""

    async def test_api_site_does_not_touch_the_neighbour(self, door, monkeypatch, site_api):
        """Сайт с настроенным API снимает список сам: ни сценария, ни вкладки в пуле.

        Ради этого всё и делалось. Проверяем не «быстрее» (быстроту тестом не
        поймать), а то, за что платили: к соседу не ходим вовсе.
        """
        monkeypatch.setattr(people, 'site_setting', _settings(
            {'people_source': 'api', 'api_base': SITE_API_FAKE_BASE,
             'api_password': SITE_API_FAKE_PASSWORD}))

        async def _forbidden(*args, **kwargs):
            raise AssertionError('сценарий у соседа всё-таки прогнали')

        monkeypatch.setattr(people, 'scenario_run', _forbidden)
        monkeypatch.setattr(people, 'profile_site', _forbidden)

        answer = await _fill()

        assert [row['uid'] for row in answer['people']] == ['788856']

    async def test_own_source_takes_the_profile_password(self, door, monkeypatch,
                                                         site_api):
        """`password_source: own` — идём своим паролем, общий не подглядываем.

        Общий заводят агентству оптом; окажись он сильнее явного выбора, анкета со
        своей парой логин-пароль молча ходила бы под чужим и получала 400.
        """
        monkeypatch.setattr(people, 'site_setting', _settings(
            {'people_source': 'api', 'api_base': SITE_API_FAKE_BASE,
             'api_password': 'общий-неверный'}))

        async def _profile(profile_id):
            return {**PROFILE, 'password': SITE_API_FAKE_PASSWORD,
                    'password_source': 'own'}

        monkeypatch.setattr(people, 'profile_row', _profile)

        assert (await _fill())['people'], 'вошли под общим паролем вместо своего'

    async def test_site_source_ignores_the_leftover_own_password(self, door, monkeypatch,
                                                                 site_api):
        """⚠ Настоящая ошибка: `password_source: site`, а шли своим паролем.

        Здесь стояло правило «свой сильнее общего» — удобное, но **без
        состояния**: по анкете не видно, под каким паролем она работает. На живой
        установке это сломало одну анкету из тринадцати (849, ID 643694 на сайте):
        в поле пароля лежала строка от прежних времён, а `password_source` человек
        поставил `site`. Колонка отвечала «сайт не принял вход: неверный логин или
        пароль (/auth/login → 400)», и выглядело это как беда самой анкеты — у
        остальных двенадцати поле было пусто, и они работали.

        Правило теперь одно на все двери сайта — `password_pick`.
        """
        monkeypatch.setattr(people, 'site_setting', _settings(
            {'people_source': 'api', 'api_base': SITE_API_FAKE_BASE,
             'api_password': SITE_API_FAKE_PASSWORD}))

        async def _profile(profile_id):
            return {**PROFILE, 'password': 'своё-старьё', 'password_source': 'site'}

        monkeypatch.setattr(people, 'profile_row', _profile)

        assert (await _fill())['people'], \
            'вошли оставленным своим паролем вопреки настройке «общий»'

    async def test_site_without_api_still_runs_the_scenario(self, door, monkeypatch):
        """Сайт без API работает как работал: новая настройка ничего не ломает."""
        monkeypatch.setattr(people, 'site_setting', _settings({}))
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))

        assert (await _fill())['people'] == PEOPLE

    async def test_chosen_api_without_address_is_not_api(self, door, monkeypatch):
        """Источник выбран, а адреса нет — это опечатка в настройке, а не повод
        молча ходить не туда. Работаем сценарием."""
        monkeypatch.setattr(people, 'site_setting', _settings(
            {'people_source': 'api'}))
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))

        assert (await _fill())['people'] == PEOPLE

    async def test_api_refusal_keeps_the_cache(self, door, monkeypatch, site_api):
        """Отказ сайта ведёт себя как отказ соседа: прежний список остаётся,
        причина едет рядом. Кэш об источнике не знает, и это правильно."""
        monkeypatch.setattr(people, 'site_setting', _settings(
            {'people_source': 'api', 'api_base': SITE_API_FAKE_BASE,
             'api_password': SITE_API_FAKE_PASSWORD}))
        await _fill()

        # ⚠ С `Reason`, как у живого: по нему отказ и объясняется словами
        # (`SITE_API_REASON_MEANING`). Без него фраза была бы общей.
        site_api.refuse(403, {'Error': 'Too many requests',
                              'Reason': 'ERROR_TOO_MANY_REQUESTS'}, on='/users/')
        answer = (await _retry(door))['result']

        assert [row['uid'] for row in answer['people']] == ['788856'], 'снимок выброшен'
        assert 'сайт просит подождать' in answer['error']


class TestFreshness:
    """Два порога: выдержка и жёсткий пол."""

    async def test_fresh_snapshot_does_not_touch_the_site(self, door, monkeypatch):
        """Свежий снимок отдаётся не спрашивая сайт — ради этого кэш и существует."""
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        # Выдержку между попытками снимаем: держать сайт должна свежесть снимка, а
        # не сторож повторных попыток. Иначе тест прошёл бы и без кэша вовсе.
        people._refresh_at.clear()
        monkeypatch.setattr(people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        answer = (await people_list(849, 'favorite', fresh=False, user=None))['result']

        assert answer['cached'] is True and answer['error'] == ''
        assert answer['refreshing'] is False, 'свежий снимок не нуждается в обновлении'

    async def test_fresh_site_list_counts_down_the_round(self, door, monkeypatch):
        """Свежая лента онлайна отдаёт ** срок до следующего захода**.

        Таймер перед иконкой ⟳ берёт это число: страница свой срок не считает —
        расписание обновлений у круга (`online_check_every`), и своя копия
        разъехалась бы с ним на первой правке. Проверяем обе стороны числа: что
        оно считается от возраста снимка, а не от жёсткого пола.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        monkeypatch.setattr(people, '_site_profile', _site_profile())

        await people.people_site_list(343, 'online', fresh=False, user=None)
        await _refreshed(SITE_KEY)

        _stale(door, SITE_KEY, 60)
        answer = (await people.people_site_list(343, 'online', fresh=False,
                                                user=None))['result']

        assert answer['cached'] is True
        assert answer['next_in_sec'] == pytest.approx(PEOPLE_FRESH_SEC - 60, abs=2), \
            'таймер снят не с того срока или не от возраста снимка'

    async def test_profile_list_carries_no_countdown(self, door, monkeypatch):
        """У списка без своего круга таймера нет — `-1`, а не выдуманный срок.

        Избранные и диалоги освежаются по запросу, а не по расписанию, и
        «через 5 мин» рядом с их ⟳ было бы обещанием, которое никто не
        собирается исполнять.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        answer = await _fill()

        assert answer['next_in_sec'] == -1

    async def test_floor_holds_against_the_button(self, door, monkeypatch):
        """⚠ Жёсткий пол не обходит даже «обновить».

        Нажать кнопку десять раз подряд легко, и без пола это были бы десять
        входов в кабинет за минуту: сайт считает такое подозрением и отвечает
        блокировкой, а блокировка стоит анкеты, а не запроса.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        monkeypatch.setattr(people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        _stale(door, KEY, PEOPLE_FLOOR_SEC - 2)
        answer = (await people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['cached'] is True and answer['error'] == ''
        assert answer['refreshing'] is False, 'кнопка пробила жёсткий пол'

    async def test_floor_follows_the_site_setting(self, door, monkeypatch):
        """⚠ Пол задаёт **настройка сайта**, а не константа кода.

        Предел у сайтов разный: один терпит заход раз в четверть минуты, другой
        считает подозрением и заход в минуту. Проверяем обе стороны одним снимком
        одного возраста — меняется только настройка, и от неё зависит исход.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        monkeypatch.setattr(people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        door.seconds['cache_floor'] = 60
        _stale(door, KEY, 30)

        answer = (await people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['refreshing'] is False, 'снимок 30 с при поле 60 с пробил пол'

    async def test_lower_floor_lets_the_button_through(self, door, monkeypatch):
        """Обратная сторона: пол опустили — кнопка работает.

        Без этой проверки пол мог бы оказаться бесконечным, и кнопка «обновить»
        молча перестала бы работать вовсе.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        door.seconds['cache_floor'] = 15
        _stale(door, KEY, 30)

        answer = (await people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['refreshing'] is True, 'снимок 30 с при поле 15 с не обновился'

    async def test_online_freshness_follows_the_site_setting(self, door, monkeypatch):
        """⚠ Выдержка ленты «Все онлайн» — тоже настройка, и своя.

        В этой ленте решается, кому можно писать (`online.py`), а состав
        сети меняется на глазах: её просят освежать чаще прочих списков. Пол при
        этом общий — он про предел сайта, а не про вид списка.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        # Лента онлайна лежит по ключу **сайта**, а не анкеты (`_fill` кладёт по
        # анкете), поэтому первый заход делаем ручкой сайта.
        monkeypatch.setattr(people, '_site_profile', _site_profile())
        await people.people_site_list(
            SITE['id'], 'online', fresh=False, user=None)
        await _refreshed(SITE_KEY)

        monkeypatch.setattr(people, '_snapshot', _snapshot(fails='сюда ходить не должны'))
        # Снимку три минуты: при выдержке 10 минут он свежий, при 2 — уже нет.
        door.seconds['online_check_every'] = 600
        _stale(door, SITE_KEY, 180)

        answer = (await people.people_site_list(
            SITE['id'], 'online', fresh=False, user=None))['result']

        assert answer['refreshing'] is False, 'снимок 3 мин при выдержке 10 мин обновился'

        # ⚠ Состаривать **второй раз нельзя**: `_stale` отнимает секунды от уже
        # состаренного снимка, и 180 + 180 перевалило бы за умолчание в 300 —
        # обновление завелось бы и без настройки, а тест зеленел бы впустую.
        # Проверено: так он и зеленел, пока не убрал повторный вызов.
        #
        # Сторож повторных попыток при этом снять надо: попытку записал первый
        # заход, а проверяем мы выдержку снимка.
        people._refresh_at.clear()
        door.seconds['online_check_every'] = 120

        answer = (await people.people_site_list(
            SITE['id'], 'online', fresh=False, user=None))['result']

        assert answer['refreshing'] is True, 'снимок 3 мин при выдержке 2 мин не обновился'

    async def test_button_works_right_after_a_run(self, door, monkeypatch):
        """⚠ Кнопка «обновить» не ждёт выдержки между попытками.

        Иначе она молча ничего не делала: минуту назад прошёл удачный прогон,
        человек видит список полуторной минуты и нажимает «⟳» — а обновление не
        заводится, потому что выдержка (пять минут) ещё не кончилась. Ни вращения,
        ни новых имён; кнопка выглядит сломанной.

        Выдержку кнопка обходит по своему смыслу, пол — нет: он про предел сайта.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        runs = []

        async def _count(profile, kind):
            runs.append(kind)

            return list(PEOPLE)

        monkeypatch.setattr(people, '_snapshot', _count)
        # Минута с прошлой попытки: выдержка (пять минут) не кончилась, пол
        # (пятнадцать секунд) — давно. Отодвигаем и снимок, и время попытки: с
        # точки зрения ручки это одно событие — прогон, который минуту назад лёг
        # в кэш.
        _age(door, KEY, 60)
        people._refresh_at[KEY] -= 60
        answer = (await people_list(849, 'favorite', fresh=True, user=None))['result']

        assert answer['refreshing'] is True, 'кнопка ничего не завела'
        assert answer['people'] == PEOPLE, 'кнопка убрала список, пока снимает новый'

        await _refreshed(KEY)

        assert len(runs) == 1


class TestCacheKeeps:
    """Снимок хранится неделю и переживает перезапуск процесса."""

    async def test_snapshot_kept_for_a_week(self, door, monkeypatch):
        """Срок годности — неделя. Не «сколько он годен», а «сколько его есть чем
        заменить»: пока сценарий не принёс новый, старый — всё, что мы знаем."""
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        assert door.redis.ttl[KEY] == 7 * 24 * 3600

    async def test_week_old_snapshot_is_still_shown(self, door, monkeypatch):
        """Недельный снимок показывается, а не заменяется словом «гружу…»."""
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        async def _never(profile, kind):
            await asyncio.sleep(3600)

        monkeypatch.setattr(people, '_snapshot', _never)
        _stale(door, KEY, 6 * 24 * 3600)

        answer = (await people_list(849, 'favorite', fresh=False, user=None))['result']

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
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        await _fill()

        door.keep = 24 * 3600
        _stale(door, KEY, 2 * 24 * 3600)

        answer = (await people_list(849, 'favorite', fresh=False, user=None))['result']

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

        monkeypatch.setattr(people, 'redis_conn_get', lambda: _Broken())
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))

        answer = await _fill()

        assert answer['people'] == PEOPLE


class TestOfflineIsNotAsked:
    """⚠⚠ Анкете не в сети список с сайта не снимают.

    **Написано по живому следу в журнале.** Открытая страница «Профили» зовёт эту
    ручку на каждое открытие карточки, а решение «идти или нет» принималось только
    по возрасту снимка:

        21:39:52 Diana | Избранные с сайта не снялись: вход отложен на 2388 с

    Анкета была офлайн вторые сутки, а панель всё равно шла за её избранными — то
    есть за входом под спящей учёткой. Лестница пауз вход не пустила и написала эту
    строку; но самой попытки быть не должно.

    ⚠ Разница с лентой «Все онлайн» существенная: та про **сайт** и снимается от
    лица любой работающей девушки, а избранные и диалоги — данные **этой** анкеты,
    и прочитать их можно, только войдя под ней.
    """

    @pytest.fixture
    def offline(self, door, monkeypatch):
        """Та же анкета, но спящая."""
        async def _profile(profile_id):
            return {**PROFILE, 'status': 'offline'}

        monkeypatch.setattr(people, 'profile_row', _profile)
        # Токена нет: он — единственное, что делает поход бесплатным.
        monkeypatch.setattr(people, 'site_api_has_token', lambda one: False)

        return door

    async def test_offline_profile_is_not_snapped(self, offline, monkeypatch):
        """Снимка нет, анкета спит — на сайт не идём и говорим почему."""
        monkeypatch.setattr(people, '_snapshot',
                            _snapshot(fails='сюда ходить не должны'))

        answer = (await people_list(849, 'favorite', user=None))['result']

        assert answer['refreshing'] is False, 'завели обновление спящей анкете'
        assert 'не в сети' in answer['error']

    async def test_the_old_snapshot_is_still_shown(self, offline, monkeypatch):
        """⚠ Прежний снимок отдаём: он честнее пустоты.

        Человек смотрит, кого девушка набрала за смену, — список позавчерашний, но
        это тот самый список, и пометка рядом объясняет, почему он не свежий.
        """
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))
        monkeypatch.setattr(people, 'profile_row',
                            lambda one: _answer_profile(PROFILE))
        await _fill()

        monkeypatch.setattr(people, 'profile_row',
                            lambda one: _answer_profile({**PROFILE, 'status': 'offline'}))
        _stale(offline, KEY)
        monkeypatch.setattr(people, '_snapshot',
                            _snapshot(fails='сюда ходить не должны'))

        answer = (await people_list(849, 'favorite', user=None))['result']

        assert answer['cached'] is True and answer['people'], 'снимок потерян'
        assert 'не в сети' in answer['error']

    async def test_the_button_does_not_override_it(self, offline, monkeypatch):
        """⚠ Кнопка «обновить» этого не обходит — в отличие от выдержки.

        Выдержка про нашу бережливость, а здесь про цену для сайта: щелчок её не
        отменяет.
        """
        monkeypatch.setattr(people, '_snapshot',
                            _snapshot(fails='сюда ходить не должны'))

        answer = (await people_list(849, 'favorite', fresh=True,
                                           user=None))['result']

        assert answer['refreshing'] is False

    async def test_a_live_token_makes_it_free(self, door, monkeypatch):
        """⚠ Даром — можно: пока токен цел, поход не стоит ни входа, ни риска.

        Девушка ушла со смены час назад, токен живёт двенадцать часов, а человек
        смотрит, кого она набрала. Запрещать тут нечего.
        """
        async def _profile(profile_id):
            return {**PROFILE, 'status': 'offline'}

        async def _api_ready(site_id):
            return True

        monkeypatch.setattr(people, 'profile_row', _profile)
        monkeypatch.setattr(people, '_api_ready', _api_ready)
        monkeypatch.setattr(people, 'site_api_has_token', lambda one: True)
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))

        answer = (await people_list(849, 'favorite', user=None))['result']

        assert answer['refreshing'] is True, 'отказали там, где поход бесплатен'

    async def test_the_site_feed_is_not_touched(self, offline, monkeypatch):
        """⚠ Лента сайта под правило не попадает: она не про эту анкету.

        Её анкету выбирает сам сервер — и выбирает **только из тех, кто в сети**
        (`_site_profile`); второе условие здесь всё сломало бы, потому что строка
        оттуда приходит без поля `status`.
        """
        monkeypatch.setattr(people, '_site_profile', _site_profile())
        monkeypatch.setattr(people, '_snapshot', _snapshot(PEOPLE))

        answer = (await people.people_site_list(
            343, 'online', user=None))['result']

        assert answer['refreshing'] is True, 'ленту сайта перестали снимать'


def _answer_profile(row: dict):
    """Готовая корутина со строкой анкеты — для подмены `profile_row`."""
    async def _got():
        return dict(row)

    return _got()


async def _fill(profile_id: int = PROFILE['id'], kind: str = 'favorite') -> dict:
    """Первый заход целиком: спросить, дождаться фона, спросить снова.

    ⚠ **Ручка не ждёт прогона никогда** (`api.md`, §3.3): первый ответ приходит
    пустым, со `refreshing`, а снимок кладёт фоновая задача. Поэтому «получить
    список» — это два запроса и ожидание между ними, и тест повторяет ровно то, что
    делает страница.
    """
    key = people._key(kind, profile_id=profile_id)

    await people_list(profile_id, kind, fresh=False, user=None)
    await _refreshed(key)

    return (await people_list(profile_id, kind, fresh=False, user=None))['result']


async def _retry(door, profile_id: int = PROFILE['id'], kind: str = 'favorite') -> dict:
    """Второй заход по устаревшему снимку: состарить, спросить, дождаться фона,
    спросить снова.

    Отдельно от `_fill`, потому что снимок здесь уже есть: первый ответ уходит с
    ним, а увидеть итог обновления (новый список или жалобу) можно только в
    следующем — том, за которым страница вернётся по кругу опроса.
    """
    key = people._key(kind, profile_id=profile_id)

    _stale(door, key)
    await people_list(profile_id, kind, fresh=False, user=None)
    await _refreshed(key)

    return await people_list(profile_id, kind, fresh=False, user=None)


async def _refreshed(key: str) -> None:
    """Дожидается фонового обновления, если оно заведено.

    Ключ сторожа — та же строка, что и ключ снимка в Redis: списки бывают по анкете
    и по сайту, и два вида ключей разошлись бы (`_key` в `people.py`).
    """
    task = people._refreshing.get(key)
    if task:
        await task


def _stale(door, key: str, seconds: float = PEOPLE_FRESH_SEC + 1) -> None:
    """Состаривает снимок и снимает выдержку между попытками.

    Оба разом, потому что оба нужны, чтобы следующий запрос дошёл до прогона:
    свежий снимок отдаётся без сайта, а сторож повторных попыток держит пять минут
    после **любой** предыдущей попытки — включая ту, которой снимок и положили.
    Разбирались бы тесты с этим по отдельности — половина проверяла бы не то
    правило, которое назвала.
    """
    _age(door, key, seconds)
    people._refresh_at.clear()


def _age(door, key: str, seconds: float) -> None:
    """Состаривает снимок в кэше — иначе тест ждал бы настоящие пять минут."""
    snapshot = json.loads(door.redis.store[key])
    snapshot['at'] -= seconds
    door.redis.store[key] = json.dumps(snapshot, ensure_ascii=False)
