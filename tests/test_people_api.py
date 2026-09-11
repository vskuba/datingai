"""Списки собеседников через API сайта: вход, токен, разбор, отказы.

⚠ **Сайт здесь поддельный** (`site_api_fake.py`), и наружу сюита не ходит вовсе.
Тест против живого ladadate.com зависел бы от того, кто у них сегодня в сети, а
вход под анкетой там — действие: частые заходы сайт считает подозрением и отвечает
блокировкой, а блокировка стоит анкеты, а не запроса.

Заглушка стоит на HTTP, поэтому модуль проверяется целиком: как собирается путь,
как уезжает токен, как код отказа превращается во фразу для журнала и как
протухший токен приводит к повторному входу.
"""

import pytest

from src.dating.dating_people_api import (DatingPeopleApiError, dating_people_api_kinds,
                                          dating_people_api_rows)
from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

pytestmark = pytest.mark.profile
PROFILE = {'id': 849, 'site_id': 343, 'login': 'diana@example.com'}


class TestRows:
    """Что приходит в колонку."""

    async def test_list_comes_in_our_shape(self, site_api):
        """⚠ Поля — те же, что у сценария, и это не совпадение.

        Страница и кэш об источнике не знают и знать не должны: перевести сайт с
        сценария на API — правка одной настройки, а не переписывание колонки.
        """
        rows = await _rows('online')

        assert [row['uid'] for row in rows] == ['812886', '169410']
        assert rows[0]['name'] == 'Vincent pitchen' and rows[0]['age'] == 49
        assert set(rows[0]) == {'uid', 'name', 'age', 'photo', 'photo_uid',
                                'online', 'is_new', 'link'}

    async def test_photo_key_matches_the_scenario(self, site_api):
        """⚠ Ключ по фотографии считается тем же способом, что у сценария.

        На нём держится значок «есть переписка»: номера у человека в кабинете и в
        чате разные, а файл фотографии один. Разойдись эти два способа — значок
        пропал бы у половины карточек, и никто бы не понял почему.
        """
        rows = await _rows('online')

        assert rows[0]['photo_uid'] == '7f23711de44941d4a2f8f9508b86b5de'

    async def test_online_flag_only_for_the_online_list(self, site_api):
        """«В сети» ставится по тому, какой список спросили, а не выдумывается.

        У избранных сайт этого признака не даёт, и серая точка там — правда: мы
        про них этого не знаем.
        """
        assert all(row['online'] for row in await _rows('online'))
        assert not any(row['online'] for row in await _rows('favorite'))

    async def test_missing_photo_is_empty_not_none(self, site_api):
        """Сайт отдаёт `null` — страница ждёт строку и рисует вместо неё букву имени."""
        rows = await _rows('favorite')

        assert rows[0]['photo'] == '' and rows[0]['photo_uid'] == ''

    async def test_duplicates_are_dropped(self, site_api):
        """Один человек — одна строка: показать его дважды хуже, чем потерять дубль."""
        site_api.users['online'] = [{'AccountId': '1', 'Name': 'Один', 'Age': 30},
                                    {'AccountId': '1', 'Name': 'Он же', 'Age': 30}]

        assert len(await _rows('online')) == 1

    async def test_rows_without_id_are_skipped(self, site_api):
        """Без номера человека не опознать, а пустой склеил бы разных в одного."""
        site_api.users['online'] = [{'Name': 'Безымянный', 'Age': 30},
                                    {'AccountId': '7', 'Name': 'Настоящий', 'Age': 31}]

        assert [row['uid'] for row in await _rows('online')] == ['7']

    async def test_unknown_kind_is_refused(self, site_api):
        """Вида, которого API не отдаёт, не выдумываем: `dialog` уходит к сценарию."""
        assert 'dialog' not in dating_people_api_kinds()

        with pytest.raises(DatingPeopleApiError, match='dialog'):
            await _rows('dialog')


class TestToken:
    """⚠ Вход — один на 12 часов, а не на каждый список."""

    async def test_login_happens_once(self, site_api):
        """Токен живёт в Redis: вход на каждый список означал бы три входа в минуту
        там, где хватает одного в сутки."""
        await _rows('online')
        await _rows('favorite')
        await _rows('online')

        logins = [call for call in site_api.calls if call[1].endswith('/auth/login')]

        assert len(logins) == 1, f'входов: {len(logins)}'

    async def test_expired_token_is_replaced_silently(self, site_api):
        """⚠ Первый 401 — это «войди заново», а не отказ.

        Токен протухает посреди рабочего дня: это обычное дело, и колонка не должна
        краснеть из-за него ни на секунду.
        """
        await _rows('online')
        site_api.expire_token()

        rows = await _rows('online')

        assert [row['uid'] for row in rows] == ['812886', '169410']
        assert len([c for c in site_api.calls if c[1].endswith('/auth/login')]) == 2

    async def test_wrong_password_is_told_plainly(self, site_api):
        """А вот второй 401 подряд — уже отказ, и причина названа словами."""
        site_api.password = 'другой'

        with pytest.raises(DatingPeopleApiError) as refusal:
            await _rows('online')

        assert 'неверный логин или пароль' in str(refusal.value)
        assert 'ERROR_USER_PASSWORD_INVALID' in str(refusal.value), 'объяснение сайта потеряно'

    async def test_token_keeps_its_own_lifetime(self, site_api):
        """Срок хранения берётся из самого токена, а не из своей константы: сайт
        вправе выдавать их на сколько угодно, и разойтись с ним молча нельзя."""
        await _rows('online')

        ttl = site_api.redis.ttl['dating:site_api:849']

        assert 11 * 3600 < ttl <= 12 * 3600, f'срок токена {ttl} с'

    async def test_password_is_never_cached(self, site_api):
        """⚠ В Redis лежит только токен.

        Утечка кэша не должна давать возможность войти заново, когда токен
        протухнет.
        """
        await _rows('online')

        assert SITE_API_FAKE_PASSWORD not in ' '.join(site_api.redis.store.values())

    async def test_nothing_to_login_with_is_told_plainly(self, site_api):
        """Пароля нет вовсе — говорим, где его завести.

        У импортированных анкет своего пароля нет, а общий задают настройкой сайта;
        молчаливый 400 от сайта человек прочитал бы как «сайт сломался».
        """
        with pytest.raises(DatingPeopleApiError, match='Пароль анкет для API'):
            await dating_people_api_rows(PROFILE, 'online', SITE_API_FAKE_BASE, '')

        assert not site_api.calls, 'к сайту пошли, не имея пароля'


class TestRefusals:
    """Отказ сайта — фразой для журнала анкеты, а не кодом."""

    @pytest.mark.parametrize('code, words', [
        (403, 'сайт просит подождать'),
        (404, 'сайт не знает такой ручки'),
        (500, 'сайт ответил ошибкой'),
    ])
    async def test_code_turns_into_a_phrase(self, site_api, code, words):
        site_api.refuse(code, {'Error': 'что-то пошло не так'}, on='/users/')

        with pytest.raises(DatingPeopleApiError) as refusal:
            await _rows('online')

        assert words in str(refusal.value)
        assert f'→ {code}' in str(refusal.value), 'кода нет — нечего искать в логах сайта'

    async def test_offline_site_says_so(self, site_api):
        site_api.offline()

        with pytest.raises(DatingPeopleApiError, match='сайт не отвечает'):
            await _rows('online')

    async def test_not_json_is_not_a_crash(self, site_api):
        """Сайт может отдать страницу-заглушку вместо JSON — это ответ, а не сбой
        панели, и разбираются с ним по куску тела."""
        import httpx

        site_api.handle = lambda request: httpx.Response(200, text='<html>под ремонтом</html>')

        with pytest.raises(DatingPeopleApiError, match='не JSON'):
            await _rows('online')

    async def test_broken_redis_does_not_break_the_list(self, site_api):
        """Сбой Redis стоит лишнего входа, а не списка: токен — ускорение."""
        class _Broken:
            def get(self, key):
                raise RuntimeError('redis лёг')

            def set(self, key, value, ex=None, nx=False):
                raise RuntimeError('redis лёг')

            def delete(self, key):
                raise RuntimeError('redis лёг')

        site_api.redis = _Broken()

        assert len(await _rows('online')) == 2


async def _rows(kind: str) -> list:
    return await dating_people_api_rows(PROFILE, kind, SITE_API_FAKE_BASE,
                                        SITE_API_FAKE_PASSWORD)


class TestOneLoginAtATime:
    """⚠ Настоящая ошибка: два одновременных входа сайт бракует как подозрение.

    Токен делился через Redis, но **сам вход** — нет: два вызова, не нашедшие
    токена в один момент, оба шли на `/auth/login`, и сайт отвечал второму
    `400 Login or password invalid`. Тем же кодом, что и на настоящую опечатку в
    пароле, — отличить нельзя, и пять дней это читалось в журнале как «пароль
    неверный» у анкеты, которая тем же паролем работала везде.

    Попадалась гонка только на самой занятой анкете и только на фоновом списке
    избранных: он обновляется отдельной задачей, то есть параллельно всему
    прочему. Тринадцать отказов за пять дней.
    """

    async def test_parallel_calls_log_in_once(self, site_api):
        """Два списка разом — один вход. ⚠ Суть исправления.

        Раньше здесь было два входа, и второй сайт отвергал.
        """
        import asyncio

        await asyncio.gather(_rows('online'), _rows('favorite'))

        logins = [call for call in site_api.calls if call[1].endswith('/auth/login')]

        assert len(logins) == 1, f'одновременных входов: {len(logins)}'

    async def test_the_waiting_one_still_gets_its_list(self, site_api):
        """⚠ Ждавший получает список, а не отказ.

        Замок не должен превращаться в свой род поломки: тот, кто уступил вход,
        обязан дождаться чужого токена и отработать им.
        """
        import asyncio

        first, second = await asyncio.gather(_rows('online'), _rows('favorite'))

        assert [row['uid'] for row in first] == ['812886', '169410']
        assert second, 'ждавший вызов вернулся пустым'

    async def test_lock_is_released_for_the_next_login(self, site_api):
        """Замок снимается после входа, а не висит свой срок.

        ⚠ Иначе первый же протухший токен запирал бы анкету на полминуты: следующий
        вход ждал бы конца чужого замка, которого давно нет.
        """
        await _rows('online')
        site_api.expire_token()

        assert len(await _rows('online')) == 2
        assert 'dating:site_api:lock:849' not in site_api.redis.store, \
            'замок остался висеть после входа'

    async def test_broken_redis_does_not_lock_the_profile(self, site_api):
        """⚠ Redis лёг — входим без замка, а не встаём.

        Без Redis нет и общего токена: каждый вызов и так логинится сам. Запереть
        анкету наглухо из-за недоступного Redis значило бы разменять редкую гонку
        на полный простой.
        """
        class _Broken:
            def get(self, key):
                raise RuntimeError('redis лёг')

            def set(self, key, value, ex=None, nx=False):
                raise RuntimeError('redis лёг')

            def delete(self, key):
                raise RuntimeError('redis лёг')

        site_api.redis = _Broken()

        assert len(await _rows('online')) == 2

    async def test_failed_login_frees_the_lock(self, site_api):
        """⚠ Неудачный вход замок тоже отпускает.

        Иначе одна опечатка в пароле запирала бы анкету на полминуты **после**
        того, как пароль исправили: ждать было бы уже нечего, а замок висел бы.
        """
        site_api.password = 'другой'

        with pytest.raises(DatingPeopleApiError):
            await _rows('online')

        assert 'dating:site_api:lock:849' not in site_api.redis.store, \
            'замок остался висеть после неудачного входа'


class TestWhichPassword:
    """⚠ Настоящая ошибка: колонка избранных ходила не тем паролем.

    Ручка списков выбирала пароль по правилу «свой, если задан», а не по настройке
    анкеты `password_source`. На живой установке это сломало одну анкету из
    тринадцати — 849 (Diana, ID 643694 на сайте):

        Избранные с сайта не снялись: сайт не принял вход: неверный логин или
        пароль: Login or password invalid (…) (/auth/login → 400)

    В её поле пароля лежала строка от прежних времён, а `password_source` стоял
    `site` — человек решил ходить общим паролем агентства. Правило «свой сильнее»
    этого решения не видело: у него нет состояния, и оставленная строка молча
    перебивала выбор. У остальных двенадцати поле было пусто, они падали в общий
    пароль и работали — оттого и выглядело, будто дело в самой анкете.

    Остальные двери сайта (письма, звёздочки, карточка мужчины) спрашивали
    `dating_password_pick` с самого начала; расходилась только эта.
    """

    async def test_site_source_ignores_the_leftover_own_password(self):
        """`password_source: site` — идём общим, даже если в поле что-то лежит."""
        assert await _asked({'id': 849, 'site_id': 343, 'login': 'd@e.com',
                             'password': 'старьё', 'password_source': 'site'}) \
               == 'общий-сайта'

    async def test_own_source_takes_the_profile_password(self):
        """`password_source: own` — идём своим, общий не подглядываем."""
        assert await _asked({'id': 849, 'site_id': 343, 'login': 'd@e.com',
                             'password': 'личный', 'password_source': 'own'}) \
               == 'личный'

    async def test_own_source_without_a_password_does_not_fall_back(self):
        """⚠ Запасного пути нет: выбран `own`, а поле пусто — идём с пустым.

        Подстановка общего вернула бы ту же неявность, от которой и уходим, только
        теперь вопреки явно сделанному выбору. Пустой пароль даст отказ сайта
        словами — и это честнее молчаливого входа под чужим.
        """
        assert await _asked({'id': 849, 'site_id': 343, 'login': 'd@e.com',
                             'password': '', 'password_source': 'own'}) == ''


async def _asked(profile: dict) -> str:
    """Каким паролем ручка списков пошла бы на сайт.

    Зовём `_api_rows` с подменённым транспортом: проверяется **выбор пароля**, а не
    разбор ответа — тем и заняты остальные тесты файла.
    """
    from src.uvicorn.routers.dating import dating_people as router

    got = {}

    async def _setting(site_id, key, default=''):
        return {'api_password': 'общий-сайта', 'api_base': 'http://site'}.get(key, '')

    async def _rows_spy(profile, kind, base, password):
        got['password'] = password

        return []

    real_setting = router.dating_site_setting
    real_rows = router.dating_people_api_rows
    router.dating_site_setting = _setting
    router.dating_people_api_rows = _rows_spy
    try:
        await router._api_rows(profile, 'favorite', 343)
    finally:
        router.dating_site_setting = real_setting
        router.dating_people_api_rows = real_rows

    return got.get('password')
