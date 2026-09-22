"""Имя собеседника в инвайте — жребием, а доля задаётся настройкой сайта.

## ⚠⚠ Жребий бросает панель, а не модель

«Вставляй имя изредка» LLM понимает как «вставляй всегда»: вероятность у неё не
считается, она стилистическая. Поэтому доля решается здесь, а модель получает
прямое указание — просить метку или обойтись без неё.

## ⚠ Имя — первое слово

В чате зовут по имени, а не по паспорту: «John» вместо «John Smith». Полное имя в
первой реплике выглядит выпиской из документов.

⚠ **Одно слово — тоже имя.** Здесь была живая беда: `people_name_parts` отвечает
пустотой на имя без пробела (а таких на сайте большинство), и в двадцати трёх
черновиках из тридцати шести у людей осталось сырое `[[companion_name]]`.
"""

import uuid

import pytest
from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def percent():
    """Ставит долю у сайта стенда и убирает за собой."""
    async def _set(value):
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE site SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),'
                ' %s, %s) WHERE site_id = %s',
                ('$.invite_name_percent', str(value), STAND_SITE))
            await db.connection.commit()

    yield _set

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET settings = JSON_REMOVE(settings, %s)'
                         ' WHERE site_id = %s', ('$.invite_name_percent', STAND_SITE))
        await db.connection.commit()


class TestTheShare:
    """Проценты из формы → доля для жребия."""

    async def test_the_default_is_ten_percent(self, percent):
        from src.dating.invite import _man_chance

        assert await _man_chance(STAND_SITE) == pytest.approx(0.10)

    async def test_the_setting_wins(self, percent):
        from src.dating.invite import _man_chance

        await percent(30)

        assert await _man_chance(STAND_SITE) == pytest.approx(0.30)

    async def test_zero_means_no_names(self, percent):
        """⚠⚠ Ноль — законное значение, а не «настройки нет».

        Человек выбрал это в форме; подмени мы его умолчанием, имена появлялись
        бы там, где их запретили.
        """
        from src.dating.invite import _man_chance

        await percent(0)

        assert await _man_chance(STAND_SITE) == 0.0

    @pytest.mark.parametrize('value, share', [(7, 0.07), (23, 0.23), (100, 1.0)])
    async def test_any_number_works(self, percent, value, share):
        """⚠⚠ Любое число, а не выбор из набора.

        Набор отвечает на вопрос «какие значения осмысленны», и для сроков это
        верно: «15 секунд или 5 минут» — разные решения, а «17 секунд» не значит
        ничего. Здесь же осмысленна **любая** доля: 7% так же законны, как 10%.
        """
        from src.dating.invite import _man_chance

        await percent(value)

        assert await _man_chance(STAND_SITE) == pytest.approx(share)

    async def test_the_share_never_exceeds_one(self, percent):
        """⚠ И вторая страховка — в самом переводе процентов в долю.

        `random.random()` не превысит единицы, так что доля больше неё значила бы
        «всегда». До жребия такое не доходит (сервер отвергает >100), но код
        считает сам и на чужую проверку не опирается.
        """
        from src.dating.invite import _man_chance

        await percent(100)

        assert await _man_chance(STAND_SITE) == 1.0

    async def test_a_broken_database_gives_the_default(self, monkeypatch):
        """⚠ «Имён не будет вовсе» — решение, и принимать его из-за недоступной
        базы нельзя."""
        from src.dating import invite

        def _broken(*args, **kwargs):
            raise RuntimeError('база отвалилась')

        monkeypatch.setattr('src.uvicorn.routers.dating.site_.site_seconds', _broken)

        assert await invite._man_chance(STAND_SITE) == pytest.approx(
            invite.INVITE_MAN_NAME_CHANCE)


class TestTheName:
    """Что именно подставляется вместо метки."""

    def test_only_the_first_word(self):
        from src.dating.invite import _first_word

        assert _first_word('John Smith') == 'John'

    def test_one_word_is_a_name_too(self):
        """⚠⚠ Живая беда: 23 черновика из 36 ушли с сырым `[[companion_name]]`,
        потому что разбор имени отвечал пустотой на имя без пробела."""
        from src.dating.invite import _first_word

        assert _first_word('Frank') == 'Frank'

    def test_the_site_field_wins_over_parsing(self):
        """Сайт отдаёт `firstname` — его и берём, не разбирая полное имя."""
        from src.dating.invite import _fill

        got = _fill('Привет, [[companion_name]]!', {'profile': '{}'},
                    {'firstname': 'Vincent', 'name': 'Vincent Vega'})

        assert got == 'Привет, Vincent!'

    def test_a_raw_mark_never_leaves(self):
        """⚠ Не подставилось — метку вырезаем: в скобках она уйдёт человеку."""
        from src.dating.invite import _fill

        got = _fill('Привет, [[companion_name]]!', {'profile': '{}'}, {'name': ''})

        assert '[[' not in got and ']]' not in got


class TestTheSetting:
    """Настройка живёт на странице «Сайты», вкладка «Инвайты и чат»."""

    async def test_the_key_is_registered(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'invite_name_percent' in known
        assert known['invite_name_percent']['default'] == '10'

    async def test_it_lives_with_invites(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        group = {row['key']: row['group'] for row in got['known']}

        assert group['invite_name_percent'] == group['invite_pool_max']

    def test_the_maker_asks_the_setting(self):
        """Набор пула читает настройку, а не константу."""
        import inspect

        from src.dating import invite

        assert 'await _man_chance(site_id)' in inspect.getsource(invite._templates)


class TestTheFieldIsANumber:
    """⚠⚠ Поле числовое, с границами, и границы держит сервер.

    У поля стоят `min` и `max` — браузер не пустит «-5» и «101». Но браузер не
    единственный источник запроса: ручка открыта тому, у кого есть токен, и без
    серверной проверки «-500» прошло бы молча. Для доли это не мелочь:
    отрицательная сравнится с `random.random()` как «никогда», то есть человек
    получил бы тихо выключенную настройку вместо отказа.
    """

    async def test_the_field_says_number(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        spec = next(r for r in got['known'] if r['key'] == 'invite_name_percent')

        assert spec['kind'] == 'number'
        assert spec['min'] == 0 and spec['max'] == 100
        assert spec['options'] == [], 'набор остался — поле нарисуется списком'

    async def test_a_number_is_saved(self, auth_client):
        answer = await auth_client.put(f'/api/dating/site/{STAND_SITE}',
                                        json={'settings': {'invite_name_percent': '7'}})

        assert answer.status_code == 200
        assert answer.json()['result']['settings']['invite_name_percent'] == '7'

    @pytest.mark.parametrize('bad', ['-5', '101', 'много', '3.5'])
    async def test_nonsense_is_refused(self, auth_client, bad):
        """Отказ, а не тихое умолчание: человек должен узнать, что не сохранилось."""
        answer = await auth_client.put(f'/api/dating/site/{STAND_SITE}',
                                        json={'settings': {'invite_name_percent': bad}})

        assert answer.status_code == 422, f'«{bad}» приняли'

    async def test_the_refusal_names_the_setting(self, auth_client):
        """⚠ В отказе — название настройки и границы: без них человек ищет глазами,
        какое из полей формы он испортил."""
        answer = await auth_client.put(f'/api/dating/site/{STAND_SITE}',
                                        json={'settings': {'invite_name_percent': '900'}})

        detail = answer.json()['detail']

        assert 'имени' in detail and '100' in detail, detail

    async def test_other_settings_are_not_touched(self, auth_client):
        """⚠ Проверка только для числовых: остальные бывают строками, JSON и
        расписанием, и «не число» для них — обычное дело."""
        answer = await auth_client.put(
            f'/api/dating/site/{STAND_SITE}',
            json={'settings': {'admin_mark': 'Выход из кабинета'}})

        assert answer.status_code == 200


class TestTimeShare:
    """⚠⚠ Время показываем агенту по жребию, а не всегда.

    **Живой пул:** 94 инвайта из 202 за сутки поминали время — «tonight», «this
    Wednesday», «good morning». Почти каждый второй.

    Это не ошибка модели, а её свойство: дай сведение — сочтёт нужным и вставит.
    То же было с именами («вставляй изредка» → вставляет всегда) и с приёмом
    захода («будь разной» → три «you're trouble» подряд). Просить «поминай
    пореже» бессмысленно: частота у неё не считается, она стилистическая.

    ⚠ **Убрать время совсем нельзя.** «Доброе утро» в девять вечера выдаёт робота
    вернее всего остального — ради этого время и заводили. Речь о доле.
    """

    @pytest.fixture
    async def time_percent(self):
        async def _set(value):
            async with mysql_get_db_async() as db:
                await db.execute(
                    'UPDATE site SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),'
                    ' %s, %s) WHERE site_id = %s',
                    ('$.invite_time_percent', str(value), STAND_SITE))
                await db.connection.commit()

        yield _set

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE site SET settings = JSON_REMOVE(settings, %s)'
                             ' WHERE site_id = %s',
                             ('$.invite_time_percent', STAND_SITE))
            await db.connection.commit()

    async def test_the_default_is_ten_percent(self, time_percent):
        from src.dating.invite import _time_chance

        assert await _time_chance(STAND_SITE) == pytest.approx(0.10)

    async def test_zero_means_never(self, time_percent):
        """Ноль — агент времени не увидит вовсе, и это законный выбор."""
        from src.dating.invite import _time_chance

        await time_percent(0)

        assert await _time_chance(STAND_SITE) == 0.0

    async def test_any_number_works(self, time_percent):
        from src.dating.invite import _time_chance

        await time_percent(35)

        assert await _time_chance(STAND_SITE) == pytest.approx(0.35)

    async def test_the_flag_reaches_the_agent(self, monkeypatch):
        """Флаг уезжает переменной: пустая строка гасит секцию у соседа."""
        from src.dating import mail_agent

        said = {}

        async def _ask(**kwargs):
            said.update(kwargs.get('variables') or {})

            return 'Привет!'

        async def _agent(*args, **kwargs):
            return 7

        monkeypatch.setattr(mail_agent, 'rhapsody_api_chat_ask', _ask)
        monkeypatch.setattr(mail_agent, '_agent_id', _agent)

        await mail_agent.invite_template(STAND_SITE, 'en', with_time=False)
        assert said['invite_time'] == '', 'время всё равно уехало'

        await mail_agent.invite_template(STAND_SITE, 'en', with_time=True)
        assert said['invite_time'] == '1'

    def test_the_maker_rolls_the_dice(self):
        import inspect

        from src.dating import invite

        code = inspect.getsource(invite._templates)

        assert 'with_time=random.random() < await _time_chance(site_id)' in code, \
            'время снова показывается всегда — пул запестрит «tonight»'

    async def test_the_setting_is_a_number(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        spec = next(r for r in got['known'] if r['key'] == 'invite_time_percent')

        assert spec['kind'] == 'number'
        assert spec['min'] == 0 and spec['max'] == 100

    def test_both_shares_use_one_converter(self):
        """⚠⚠ Перевод процентов в долю — **один на весь проект**.

        Здесь стерёгся один модуль: «две копии разошлись бы — где-нибудь забыли
        бы поделить на сто». Опасение подтвердилось в большем масштабе: копий
        оказалось **три** — в инвайтах, в шаблоне рассылки и в письмах
        избранным, — и они действительно разошлись. Две клампили значение сами,
        третья читала через `site_seconds`, то есть соблюдала границы реестра.

        Теперь функция одна (`percent.percent_share`), и сторож смотрит на неё.
        """
        import inspect

        from src.dating import invite

        for fn in (invite._time_chance, invite._man_chance):
            assert 'percent_share(' in inspect.getsource(fn), \
                f'{fn.__name__} снова считает долю сам'
