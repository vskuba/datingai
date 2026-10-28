"""Гости: разбор съёма и разбор страницы.

Лента гостей стоит на двух разборах, и оба молча врут, если их сломать: дата
визита («Вчера», «27-10-2026») и merging текста страницы Step2Love по маркеру
«(ID: …)». Ошиблись в первом — лента показывает не ту свежесть; ошиблись во
втором — гость N получает дату гостя N+1, и лента врёт ровно так же незаметно.
Здесь — чистые функции, без базы и браузера: живые прогоны проверяются кнопкой
на странице, а сюда смотрят на правила.

Диспетчер драйвера (`site_registry_guests`) — тоже здесь: он решает, какой из
файлов `site/site_guests*.py` работает, и опечатка в имени унесла бы ленту на
базовый разбор без единой ошибки в журнале.

С 27.10.2026 сюда же легли тормоза съёма (кнопка обходит остуду, круг — нет) и
дверь записи: у неё два правила, которые на глаз не проверяются вовсе — `COALESCE`
около даты и `created_at` около подтверждения. С 28.10.2026 — и отбор ленты по
анкете: дата и значок под фильтром. Браузера по-прежнему нет: живые
прогоны проверяются кнопкой на странице.
"""

import time
from datetime import date, timedelta

import pytest

from src.dating.guests import guests_visit_day
from src.dating.site.site_guests_step2love import (
    DatingSiteGuestsStep2Love, _guests_page_visits)
from src.dating.site.site_registry import site_registry_guests
from datetime_.datetime_ import datetime_utc_now
from src.uvicorn.routers.dating import people

pytestmark = pytest.mark.core

TODAY = date.today()


class TestVisitDay:
    """Слова про визит — в дату; непонятное — в `None`, а не в догадку."""

    @pytest.mark.parametrize('said, want', [
        ('Вчера', TODAY - timedelta(days=1)),
        ('сегодня', TODAY),
        ('3 дн.', TODAY - timedelta(days=3)),
        ('07.11.2026', date(2026, 11, 7)),
        # формат карточки Step2Love: тегов нет, голая дата через дефис —
        # его этот разбор обязан знать, иначе вся лента стоит с пустыми датами
        ('27-10-2026', date(2026, 10, 27)),
        ('27 окт. 2026', date(2026, 10, 27)),
        ('', None),
        ('Написать сообщение', None),
        ('33-13-2026', None),
    ])
    def test_table(self, said, want):
        assert guests_visit_day(said) == want


class TestPageVisits:
    """Текст страницы Step2Love — в `{uid: дата, возраст}` по маркерам «(ID: …)»."""

    BLOB = ('Кабинет Oksana\n'
            'Graeme\n(ID: 472477)\nВозраст: 65\nSend Intro\n27-10-2026\n'
            'Статус: ищу 12-13-2020 в переписке\n'
            'daniele\n(ID: 1182655)\nВозраст: 52\nSend Intro\n25-10-2026\n')

    def test_first_card(self):
        got = _guests_page_visits(self.BLOB)['472477']

        assert got['visit'] == date(2026, 10, 27), 'дата уехала за «Send Intro»'
        assert got['age'] == 65, 'возраст ушёл из карточки'

    def test_dates_do_not_leak_between_cards(self):
        """Каждый кусок — от своего маркера до следующего: иначе второй гость
        живёт датой первого, и порядок ленты врёт молча."""
        got = _guests_page_visits(self.BLOB)

        assert got['1182655']['visit'] == date(2026, 10, 25)

    def test_body_date_ignored_when_button_date_exists(self):
        """«12-13-2020» в свободном тексте статуса — не дата визита: с кнопкой
        в карточке дата берётся из её ячейки."""
        got = _guests_page_visits(self.BLOB)

        assert got['472477']['visit'] == date(2026, 10, 27)

    def test_card_without_age_is_zero(self):
        got = _guests_page_visits('X\n(ID: 9)\nSend Intro\n24-10-2026\n')

        assert got['9'] == {'visit': date(2026, 10, 24), 'age': 0}

    def test_no_markers_is_empty(self):
        assert _guests_page_visits('пустая страница без карточек') == {}


class TestDriverRows:
    """Драйвер доклеивает разбор страницы к карточкам общего съёма."""

    CARDS = {'guests': [
        {'number': '(ID: 472477)', 'name': 'Graeme', 'link': '/profile/472477/',
         'photo': 'https://mystep2love.com/a/1.gif'},
        {'number': '(ID: 1182655)', 'name': 'daniele',
         'link': '/profile/1182655/', 'photo': ''},
    ]}

    PAGE = ('Graeme\n(ID: 472477)\nВозраст: 65\nSend Intro\n27-10-2026\n'
            'daniele\n(ID: 1182655)\nВозраст: 52\nSend Intro\n25-10-2026\n')

    def test_merge_by_uid(self):
        rows = DatingSiteGuestsStep2Love({}).rows(
            {**self.CARDS, 'guests_text': self.PAGE})

        by = {one['uid']: one for one in rows}
        assert by['472477']['visit'] == date(2026, 10, 27)
        assert by['472477']['age'] == 65
        assert by['1182655']['visit'] == date(2026, 10, 25)

    def test_no_text_step_is_not_a_failure(self):
        """Сценарий без `read_text` — карточки без дат, а не отказ: шаг чтения
        мог отстать от правки, карточки-то на месте."""
        rows = DatingSiteGuestsStep2Love({}).rows(self.CARDS)

        assert [one['uid'] for one in rows] == ['472477', '1182655']
        assert all(one['visit'] is None for one in rows)


class TestRegistry:
    """Кирпичик ленты по `presence.driver`: имя из закладки решает файл."""

    def test_step2love_gets_its_file(self):
        assert site_registry_guests({'driver': 'step2love'}).name == 'step2love'

    def test_unknown_driver_falls_to_base(self):
        assert site_registry_guests({'driver': 'ladadate'}).name == 'base'

    def test_absent_driver_falls_to_base(self):
        """Умолчание — базовый, а не переходный goldenbride: у гостей нет
        истории с константами, базовый — честный ответ «сценарий + общий разбор»."""
        assert site_registry_guests(None).name == 'base'
        assert site_registry_guests({}).name == 'base'


class TestSweepBrakes:
    """Тормоза съёма: что обходит кнопка, что отвечает круг без сценария.

    Браузера, Redis и базы здесь нет: `scenario_find` подменён отказом, слот —
    лямбдой. Смотрим на одно — попадает ли анкета в обход и что видит человек,
    когда снять нечем.
    """

    SITE, PID = 777, 990_002

    @pytest.fixture
    def sweep(self, monkeypatch):
        """Съём сайта без сценария: всё внешнее — подделки, отметки — в список."""
        from src.dating import guests_round

        marked = []
        monkeypatch.setattr(guests_round, '_guests_fail', {})
        monkeypatch.setattr(guests_round, '_guests_told', {})
        monkeypatch.setattr(guests_round, '_guests_site_fail', {})
        monkeypatch.setattr(guests_round, 'round_mem_mark',
                            lambda mem, key: marked.append((id(mem), key)))
        monkeypatch.setattr(guests_round, 'round_slot_due',
                            lambda *a, **k: True)

        async def _seconds(site_id, key):
            return 4

        async def _profiles(site_id):
            return [{'id': self.PID, 'login': 'x', 'proxy_id': 0}]

        async def _presence(site_id):
            return {}

        async def _find(site_id, name):
            return None

        for name, value in (('site_seconds', _seconds),
                            ('guests_site_profiles', _profiles),
                            ('site_presence', _presence),
                            ('scenario_find', _find)):
            monkeypatch.setattr(guests_round, name, value)

        return marked

    async def test_button_outruns_the_cooling(self, sweep):
        """⚠ Кнопка обходит и слот, и остуду: «пять минут назад не вышло» —
        не ответ человеку, который нажал «обновить»."""
        from src.dating import guests_round

        guests_round._guests_fail[self.PID] = time.time()

        got = await guests_round.guests_site_sweep(self.SITE, due_only=False)

        assert (id(guests_round._guests_fail), self.PID) in sweep, \
            'анкета в остуде не попала в ручной съём'
        assert 'сценария' in got['error'], 'кнопке нечего показать человеку'

    async def test_round_respects_the_cooling(self, sweep):
        """Круг — другое дело: остывшая анкета в заход не встаёт, и молчание
        его без нулей и лишнего стрюканья в ту же дверь."""
        from src.dating import guests_round

        guests_round._guests_fail[self.PID] = time.time()

        got = await guests_round.guests_site_sweep(self.SITE)

        assert got == {'profiles': 0, 'guests': 0}
        assert sweep == [], 'остывшая анкета всё-таки встала в круг'

    async def test_site_rests_after_a_broken_run(self, sweep, monkeypatch):
        """⚠⚠ Разбитый сценарий — беда сайтовая: после отказа отдыхает весь сайт.

        Поанкетная остуда при нём живёт ровно один тик: анкет сорок, за тик
        берутся три, и круг перебором списка стучит в панель каждую минуту —
        почти сорок входов в час. 28.10.2026 этим темпом сайт и израсходовал
        лимит попыток входа, и замолчали все ленты разом. Здесь анкеты всегда
        «в срок» — тормозом должен остаться один отдых сайта.
        """
        from src.dating import guests_round

        def _mark(mem, key):
            mem[key] = time.time()

        def _due(mem, key, every):
            if mem is guests_round._guests_site_fail:
                return time.time() - mem.get(key, 0) >= every
            return True                 # поанкетной остуды как бы нет

        monkeypatch.setattr(guests_round, 'round_mem_mark', _mark)
        monkeypatch.setattr(guests_round, 'round_mem_due', _due)

        first = await guests_round.guests_site_sweep(self.SITE)

        assert 'сценария' in first.get('error', ''), \
            'заход, увидевший беду, обязан был о ней сказать'

        second = await guests_round.guests_site_sweep(self.SITE)

        assert second == {'profiles': 0, 'guests': 0}, \
            'сайт с разбитым сценарием не отдохнул и стукнул снова'

    async def test_batch_stops_at_first_failure(self, sweep, monkeypatch):
        """Круг с отказом не добивает остаток пачки: раз сайт отправлен на
        отдых, следующие в пачке — те же входы, заведомо отказные. Кнопка
        (`due_only=False`) — другое дело: там человек просил снять всех."""
        from src.dating import guests_round

        tried = []

        async def _profiles(site_id):
            return [{'id': self.PID, 'login': 'x', 'proxy_id': 0},
                    {'id': self.PID + 1, 'login': 'y', 'proxy_id': 0}]

        async def _find(site_id, name):
            return {'id': 541, 'name': name}

        async def _run(scenario, driver, site_id, one):
            tried.append(one['id'])
            return None

        monkeypatch.setattr(guests_round, 'guests_site_profiles', _profiles)
        monkeypatch.setattr(guests_round, 'scenario_find', _find)
        monkeypatch.setattr(guests_round, '_profile_guests', _run)

        got = await guests_round.guests_site_sweep(self.SITE)

        assert tried == [self.PID], 'после отказа круг добил остаток пачки'
        assert got['profiles'] == 1, 'счёт врёт о числе зашедших в панель'

        tried.clear()
        await guests_round.guests_site_sweep(self.SITE, due_only=False)

        assert len(tried) == 2, 'кнопка не досняла остальных до конца'


class TestSeenDoor:
    """Дверь записи: повторный визит — одна строка, подтверждение — свежее."""

    SITE, PROF, UID = 777, 990_001, '7000001'

    async def _wipe(self):
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM companion_guest'
                             ' WHERE site_id = %s AND profile_id = %s',
                             (self.SITE, self.PROF))
            await db.execute("DELETE FROM companion WHERE site_id = %s"
                             " AND uid = %s", (self.SITE, self.UID))
            await db.connection.commit()

    async def test_reappear_touches_confirmation(self):
        """⚠ Гость, снова видимый на странице, подтверждён сегодня — даже
        если сам визит старый. Иначе лента, которую круг снимает исправно,
        докладывала бы возрастом о дне первого sighting.

        Проверка намеренно с датой в прошлом: `visit_at = NULL` во втором
        заходе сторожит и `COALESCE` — старая дата обязана уцелеть. Проверка
        вокруг `created_at` ловует и частность MySQL: `DEFAULT(created_at)`
        в части `ON DUPLICATE KEY UPDATE` вычисляется нулевой датой, и гость
        переехал бы в начало времён.
        """
        from datetime import date

        from mysql_.mysql_ import mysql_get_db_async

        from src.dating.guests import guests_seen

        await self._wipe()
        try:
            card = {'uid': self.UID, 'name': 'G', 'photo': '', 'age': 0}
            assert await guests_seen(self.SITE, self.PROF,
                                     [dict(card, visit=date(2026, 10, 20))]) == 1

            async with mysql_get_db_async() as db:
                await db.execute(
                    'UPDATE companion_guest SET created_at = %s'
                    ' WHERE site_id = %s AND profile_id = %s',
                    ('2026-01-01 00:00:00', self.SITE, self.PROF))
                await db.connection.commit()

            assert await guests_seen(self.SITE, self.PROF,
                                     [dict(card, visit=None)]) == 1

            async with mysql_get_db_async() as db:
                await db.execute(
                    'SELECT visit_at, created_at FROM companion_guest'
                    ' WHERE site_id = %s AND profile_id = %s',
                    (self.SITE, self.PROF))
                row = (await db.fetchall())[0]

            assert row['visit_at'] == date(2026, 10, 20), \
                'пустая дата стёрла известную'
            assert str(row['created_at']) > '2026-01-01', \
                'подтверждение не двинулось — лента врёт возрастом'
        finally:
            await self._wipe()


class TestFeedFilter:
    """Лента по анкете: выбрана — только её гости, нет — все визиты сайта.

    Правила, которые на глаз не проверяются вовсе: под фильтром дата —
    визита **к ней**, а не самого свежего визита вообще, и значок «скольким
    заходил» остаётся сайтовым — иначе гуляка, закрытый выбором анкеты,
    притворился бы однолюбом.
    """

    SITE, P1, P2 = 778, 990_011, 990_012
    UIDS = ('7000011', '7000012', '7000013')

    async def _wipe(self):
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM companion_guest WHERE site_id = %s',
                             (self.SITE,))
            await db.execute('DELETE FROM companion WHERE site_id = %s',
                             (self.SITE,))
            await db.connection.commit()

    async def _fill(self):
        from src.dating.guests import guests_seen

        def card(number, name):
            return {'uid': number, 'name': name, 'photo': '', 'age': 30}

        # А — только к первой, Б — к обеим (у второй — свежее), В — лишь ко
        # второй: под фильтром по первой его быть не должно.
        await guests_seen(self.SITE, self.P1, [
            dict(card(self.UIDS[0], 'A'), visit=date(2026, 10, 1)),
            dict(card(self.UIDS[1], 'B'), visit=date(2026, 10, 1))])
        await guests_seen(self.SITE, self.P2, [
            dict(card(self.UIDS[1], 'B'), visit=date(2026, 10, 20)),
            dict(card(self.UIDS[2], 'C'), visit=date(2026, 10, 10))])

    async def _people(self, *args):
        from src.dating.guests import guests_feed

        feed = await guests_feed(*args)

        return {one['uid']: one for one in feed['people']}

    async def test_no_profile_shows_every_visit(self):
        """Без анкеты — прежняя лента: все гости сайта, каждый со свежей датой."""
        await self._wipe()
        try:
            await self._fill()
            people = await self._people(self.SITE)

            assert set(people) == set(self.UIDS)
            assert people[self.UIDS[1]]['visit'] == '2026-10-20', \
                'без отбора дата обязана быть самой свежей из всех анкет'
            assert people[self.UIDS[1]]['profiles'] == 2
        finally:
            await self._wipe()

    async def test_profile_shows_only_hers(self):
        """С анкетой — только её гости, дата к ней и прежний сайтовый значок."""
        await self._wipe()
        try:
            await self._fill()
            people = await self._people(self.SITE, self.P1)

            assert set(people) == set(self.UIDS[:2]), \
                'в ленту анкеты попал чужой визит'

            assert people[self.UIDS[1]]['visit'] == '2026-10-01', \
                'дата под фильтром взята с чужой анкеты'
            assert people[self.UIDS[1]]['profiles'] == 2, \
                'выбор анкеты спрятал гуляку за единицей'
        finally:
            await self._wipe()

    async def test_empty_profile_is_empty_feed(self):
        """Анкета без гостей — пустая лента, а не вся чужая."""
        await self._wipe()
        try:
            await self._fill()
            people = await self._people(self.SITE, 990_013)

            assert people == {}, 'анкете без визитов подмешали чужих'
        finally:
            await self._wipe()


class TestFeedCountdown:
    """Лента гостей отдаёт вместе со списком срок до следующего захода круга.

    Число перед иконкой ⟳ считает сервер: расписание у слота круга
    (`mailing_guests_every`), и своя копия срока на странице разъехалась бы
    с кругом на первой правке. Здесь обе стороны числа — есть снимок (срок
    минус его возраст) и снимка нет (круг зовут на первом же тике — «скоро»,
    то есть ноль).
    """

    async def test_snapshot_counts_the_period_left(self, monkeypatch):
        async def _feed(site_id, profile_id=0):
            taken = (datetime_utc_now() - timedelta(minutes=30))
            return {'people': [], 'taken_at': taken.strftime('%Y-%m-%d %H:%M:%S')}

        async def _every(site_id):
            return 4 * 3600                      # настройка сайта: 4 часа

        monkeypatch.setattr(people, 'guests_feed', _feed)
        monkeypatch.setattr(people, 'guests_slot_every_sec', _every)
        monkeypatch.setattr(people, '_online_now', lambda rows, site, kind: rows)

        answer = (await people.people_guests_list(778))['result']

        assert answer['age_sec'] == pytest.approx(1800, abs=2)
        assert answer['next_in_sec'] == pytest.approx(4 * 3600 - 1800, abs=2), \
            'таймер снят не с круга или не от возраста ленты'

    async def test_without_snapshot_is_soon(self, monkeypatch):
        """Ленты ещё нет ни у одной анкеты — круг идёт на первый заход сейчас."""
        async def _feed(site_id, profile_id=0):
            return {'people': [], 'taken_at': ''}

        async def _every(site_id):
            return 4 * 3600

        monkeypatch.setattr(people, 'guests_feed', _feed)
        monkeypatch.setattr(people, 'guests_slot_every_sec', _every)
        monkeypatch.setattr(people, '_online_now', lambda rows, site, kind: rows)

        answer = (await people.people_guests_list(778))['result']

        assert answer['age_sec'] == -1
        assert answer['next_in_sec'] == 0
