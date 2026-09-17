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

    async def test_a_value_outside_the_list_falls_back(self, percent):
        """⚠ Значение вне набора формы отвергается — это делает `site_seconds`.

        Страница таких не предлагает, а в базу их вписывают руками. Реестр
        сверяет значение со списком и отдаёт умолчание, не доводя до жребия:
        «250%» иначе значило бы то же, что 100, но молча.
        """
        from src.dating.invite import _man_chance

        await percent(250)

        assert await _man_chance(STAND_SITE) == pytest.approx(0.10)

    async def test_the_biggest_offered_share_works(self, percent):
        """Половина — самая большая доля, которую предлагает форма.

        ⚠ Больше не предлагаем намеренно: текст пула один на всех, и обращение
        по имени чаще чем в половине реплик выдаёт рамку тому, кто прочитает две
        подряд.
        """
        from src.dating.invite import _man_chance

        await percent(50)

        assert await _man_chance(STAND_SITE) == pytest.approx(0.50)

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

        assert 'await _man_chance(site_id)' in inspect.getsource(invite._template)
