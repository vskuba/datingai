"""Имя собеседника в инвайте — жребием, а доля задаётся настройкой сайта.

## ⚠⚠ Жребий бросает СОСЕД, а доля живёт настройкой сайта

«Вставляй имя изредка» LLM понимает как «вставляй всегда»: вероятность у неё не
считается, она стилистическая. Значит выбирать должен кто-то снаружи её — и с
26.09.2026 это шаг `dice` в workflow `dating_invite_one`, а не панель.

Панель отдаёт **долю в процентах**, и проверяется здесь именно она: настройка
прочитана, границы соблюдены, ноль отличён от «не задано». Что дальше по ней
бросают жребий — предмет LLM-тестов у соседа
(`tests_llm/dating_invite_write/test_prompt.py`).

⚠ Проценты **строкой**: значение уезжает в описание жребия, где доли заданы
числами, и превращать его по дороге значило бы делать это там, где не видно.

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
    """Проценты из формы → проценты для жребия у соседа."""

    async def test_the_default_is_ten_percent(self, percent):
        from src.dating.invite import _man_percent

        assert await _man_percent(STAND_SITE) == '10'

    async def test_the_setting_wins(self, percent):
        from src.dating.invite import _man_percent

        await percent(30)

        assert await _man_percent(STAND_SITE) == '30' 

    async def test_zero_means_no_names(self, percent):
        """⚠⚠ Ноль — законное значение, а не «настройки нет».

        Человек выбрал это в форме; подмени мы его умолчанием, имена появлялись
        бы там, где их запретили.
        """
        from src.dating.invite import _man_percent

        await percent(0)

        assert await _man_percent(STAND_SITE) == '0' 

    @pytest.mark.parametrize('value', [7, 23, 100])
    async def test_any_number_works(self, percent, value):
        """⚠⚠ Любое число, а не выбор из набора.

        Набор отвечает на вопрос «какие значения осмысленны», и для сроков это
        верно: «15 секунд или 5 минут» — разные решения, а «17 секунд» не значит
        ничего. Здесь же осмысленна **любая** доля: 7% так же законны, как 10%.
        """
        from src.dating.invite import _man_percent

        await percent(value)

        assert await _man_percent(STAND_SITE) == str(value)

    async def test_the_share_never_exceeds_a_hundred(self, percent):
        """⚠ Вторая страховка — в самом чтении, а не только в форме.

        Правило проекта: границы числа проверяет и чтение, форма не единственный
        вход. Сотня — предел: доля больше неё у соседа означала бы «всегда», то
        есть тихо выключенный жребий вместо отказа.
        """
        from src.dating.invite import _man_percent

        await percent(100)

        assert await _man_percent(STAND_SITE) == '100' 

    async def test_a_broken_database_gives_the_default(self, monkeypatch):
        """⚠ «Имён не будет вовсе» — решение, и принимать его из-за недоступной
        базы нельзя."""
        from src.dating import invite

        def _broken(*args, **kwargs):
            raise RuntimeError('база отвалилась')

        monkeypatch.setattr('src.uvicorn.routers.dating.site_.site_seconds', _broken)

        assert await invite._man_percent(STAND_SITE) == \
            str(invite.INVITE_MAN_NAME_PERCENT)


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

        assert 'await _man_percent(site_id)' in inspect.getsource(invite._templates)


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



class TestTimeIsGone:
    """⚠⚠ Доля времени убрана вместе со временем — 26.09.2026.

    Прежде здесь был класс `TestTimeShare`: доля `invite_time_percent`, флаг
    `with_time`, проверка, что он доезжает до агента. Всего этого больше нет — ни
    настройки, ни секции в промпте, ни шага `datetime_current_get` у соседа.

    ⚠ Довод, по которому время заводили, стоит знать: «доброе утро» в девять вечера
    выдаёт робота вернее всего остального, и в пуле прода лежал шаблон «My thoughts
    have turned sideways tonight» — модель писала про вечер, не зная, который час.
    Мера была в доле, потому что при показе всегда она поминала время в **94
    инвайтах из 202 за сутки**.

    Класс оставлен сторожем обратного: вернуть время «наполовину» нельзя — нужны
    четыре вещи разом, и любая одна без остальных ломает заход.
    """

    async def test_the_setting_is_gone(self):
        """⚠ Настройки в реестре нет: она обещала бы влияние, которого не будет."""
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        assert 'invite_time_percent' not in SITE_KNOWN

    async def test_the_panel_sends_nothing_about_time(self):
        """⚠⚠ И в присылке ничего про время: у соседа эти переменные не объявлены,
        а ручка отвергает вызов целиком — «Переменные … не объявлены». То есть
        лишняя переменная не потеряется молча, заход упадёт."""
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent.invite_template)
        for gone in ('invite_time', 'datetime_current_get_zone',
                     'invite_country'):
            assert f"'{gone}'" not in code, \
                f'панель снова шлёт {gone} — вызов упадёт у соседа'
