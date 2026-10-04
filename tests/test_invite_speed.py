"""Скорость отправки инвайтов: сколько ушло и что будет за двенадцать часов.

## ⚠⚠ Прежний счёт занижал в разы

Число «ушло за 5 минут» бралось из ленты событий — а лента это **кольцо на
тридцать строк** (`feed.FEED_KEEP`), общее для всех видов событий: писем,
диалогов, избранных. На проде за минуту уходит до 98 реплик, то есть в
пятиминутное окно не помещается и десятая часть.

Лента для мигания конвертов годится — важно «идёт ли отправка сейчас». Для счёта
нет: считать надо по своей таблице.

## ⚠ Прогноз упирается в дневную норму

Линейное «×144» обещало бы тысячи там, где норма сотня: круг остановится, дойдя
до неё, и человек, поверивший прогнозу, ждал бы отправки, которой не будет.
"""

import uuid
import pytest
from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'speed-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    # ⚠ Чистим отправленное по сайту: скорость считается по всей таблице, и
    # чужие строки сдвинули бы счёт — тест ловил бы порядок запуска.
    async with mysql_get_db_async() as db:
        await db.execute("DELETE FROM invite WHERE site_id = %s AND status = 'sent'",
                         (STAND_SITE,))
        await db.connection.commit()

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM invite WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


class TestCount:
    """Что считается «ушедшим за пять минут»."""

    async def test_fresh_ones_are_counted(self, girl):
        from src.uvicorn.routers.dating.invite import _speed

        await _sent(girl, minutes_ago=[1, 2, 3])

        assert (await _speed(STAND_SITE))['last_5min'] == 3

    async def test_older_than_five_minutes_are_not(self, girl):
        """⚠ Окно ровно пятиминутное: иначе «текущая скорость» показывала бы
        вчерашнюю."""
        from src.uvicorn.routers.dating.invite import _speed

        await _sent(girl, minutes_ago=[1, 30, 120])

        assert (await _speed(STAND_SITE))['last_5min'] == 1

    async def test_drafts_are_not_counted(self, girl):
        """Черновик никуда не уходил — в скорости отправки ему места нет."""
        from src.uvicorn.routers.dating.invite import _speed

        await _draft(girl)

        assert (await _speed(STAND_SITE))['last_5min'] == 0

    async def test_the_count_is_not_bound_by_the_feed(self, girl):
        """⚠⚠ Ради этого всё и переписано: лента держит тридцать строк, и счёт
        по ней упирался в потолок. Кладём сорок — считаем сорок."""
        from src.uvicorn.routers.dating.invite import _speed

        await _sent(girl, minutes_ago=[1] * 40)

        assert (await _speed(STAND_SITE))['last_5min'] == 40


class TestForecast:
    """Прогноз на двенадцать часов."""

    async def test_it_scales_the_current_rate(self, girl, monkeypatch):
        """⚠ Норму снимаем: иначе тест ловит настройку стенда, а не расчёт."""
        from src.uvicorn.routers.dating.invite import _speed

        async def _no_cap(site_id, key):
            return 0

        monkeypatch.setattr('src.uvicorn.routers.dating.invite.site_seconds', _no_cap)
        await _sent(girl, minutes_ago=[1] * 5)

        got = await _speed(STAND_SITE)

        assert got['per_hour'] == 60, 'пять за пять минут — это шестьдесят в час'
        assert got['forecast_12h'] == 720

    async def test_the_daily_cap_bounds_it(self, girl, monkeypatch):
        """⚠⚠ Прогноз упирается в дневную норму.

        Круг остановится, дойдя до неё, и линейное «×144» обещало бы отправку,
        которой не будет.
        """
        from src.uvicorn.routers.dating import invite

        async def _cap(site_id, key):
            return 100

        monkeypatch.setattr('src.uvicorn.routers.dating.invite.site_seconds', _cap)
        await _sent(girl, minutes_ago=[1] * 10)

        got = await invite._speed(STAND_SITE)

        assert got['capped'] is True
        assert got['forecast_12h'] == got['cap_left'], 'прогноз перескочил норму'
        assert got['cap_left'] == 90, 'остаток нормы считается по ушедшим за сутки'

    async def test_silence_is_zero_not_a_failure(self, girl):
        """Ничего не уходило — ноль, а не пустота: «скорость 0» это ответ."""
        from src.uvicorn.routers.dating.invite import _speed

        got = await _speed(STAND_SITE)

        assert got['last_5min'] == 0 and got['forecast_12h'] == 0

    async def test_the_handle_returns_it(self, auth_client, girl):
        """Скорость приходит **вместе со списком**: два запроса дали бы два
        разных момента времени."""
        got = (await auth_client.get(
            f'/api/dating/invite?site_id={STAND_SITE}')).json()

        assert 'speed' in got
        assert 'last_5min' in got['speed'] and 'forecast_12h' in got['speed']


async def _sent(profile_id: int, minutes_ago: list) -> None:
    """Кладёт отправленные реплики «столько-то минут назад».

    ⚠⚠ **Время считает база (`NOW()`), а не питон.** Машина сюиты живёт в
    Europe/Kyiv, MySQL — в UTC: `datetime.now()` даёт время на три часа вперёд, и
    строка «тридцать минут назад» оказывается для базы **в будущем** — попадая в
    любое окно. Тест показывал три там, где ждали одну.
    """
    async with mysql_get_db_async() as db:
        for ago in minutes_ago:
            await db.execute(
                'INSERT INTO invite (site_id, profile_id, uid, name, body, status,'
                '                    sent_at, created_at)'
                " VALUES (%s, %s, %s, 'Кто-то', 'Hi!', 'sent',"
                '         NOW() - INTERVAL %s MINUTE, NOW() - INTERVAL %s MINUTE)',
                (STAND_SITE, int(profile_id), f'u-{uuid.uuid4().hex[:8]}',
                 int(ago), int(ago)))
        await db.connection.commit()


async def _draft(profile_id: int) -> None:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO invite (site_id, profile_id, uid, name, body, status)'
            " VALUES (%s, %s, %s, 'Кто-то', 'Hi!', 'draft')",
            (STAND_SITE, int(profile_id), f'd-{uuid.uuid4().hex[:8]}'))
        await db.connection.commit()


class TestTheTooltip:
    """Что человек видит, наведя на конверты у слова «Доставка»."""

    def test_the_title_comes_from_the_handle(self):
        """⚠⚠ Число в подсказке — из ручки, а не из ленты.

        Лента это кольцо на тридцать строк, общее для всех видов событий; при
        сотне реплик в минуту она показывала бы тридцать и молчала о разнице.
        """
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        assert 'inviteSpeed = answer.speed' in page, \
            'скорость перестала приезжать вместе со списком'
        assert 'el.title = inviteFlyTitle(fresh);' in page

    def test_the_title_names_all_three(self):
        """В подсказке: ушло за пять минут, темп в час, прогноз на двенадцать."""
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        body = page[page.index('function inviteFlyTitle'):]
        body = body[:body.index('\n    }')]

        assert 'Ушло за 5 минут' in body
        assert 'в час' in body
        assert 'за 12 часов' in body

    def test_a_capped_forecast_says_so(self):
        """⚠ Упёршийся в норму прогноз показывается с оговоркой.

        Круг остановится, дойдя до дневного потолка; голое число обещало бы
        отправку, которой не будет.
        """
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        body = page[page.index('function inviteFlyTitle'):]
        body = body[:body.index('\n    }')]

        assert 's.capped' in body and 'дневную норму' in body

    def test_an_old_page_still_shows_something(self):
        """⚠ Ручка не отдала скорость — подсказка прежняя, а не пустая.

        Между выкладкой сервера и обновлением вкладки у человека проходит время;
        пустая подсказка выглядела бы поломкой.
        """
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        body = page[page.index('function inviteFlyTitle'):]
        body = body[:body.index('\n    }')]

        assert 's.last_5min === undefined' in body


class TestTheNumberNextToEnvelopes:
    """⚠⚠ Число у конвертов — настоящее, а не потолок ленты.

    **Живой вопрос заказчика:** «Вкладка Доставка показывает прыгающие иконки и
    число 30. 30 — что это такое?»

    Это был потолок кольца ленты (`feed.FEED_KEEP`), а не реплики: при темпе в
    четыре сотни за пять минут лента отдаёт тридцать строк и там стоит. Число
    выглядело осмысленным и врало — худший вид ошибки.
    """

    def test_the_count_comes_from_the_handle(self):
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        body = page[page.index('function inviteFlyPaint'):]
        body = body[:body.index('\n    }')]

        assert 'inviteSpeed.last_5min' in body, \
            'число снова из ленты — упрётся в тридцать и будет врать'
        assert 'tab-fly-more">${count}' in body

    def test_the_envelopes_still_come_from_the_feed(self):
        """⚠ Конверты остаются по ленте, и это верно.

        Они отвечают на «идёт ли отправка сейчас» — тридцати строк довольно, а
        ручка пула опрашивается реже, чем лента.
        """
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        body = page[page.index('function inviteFlyPaint'):]
        body = body[:body.index('\n    }')]

        assert 'Math.min(fresh, FLY_MAX)' in body, \
            'конверты стали считаться по ручке — они мигают реже, чем идёт отправка'

    def test_an_old_page_falls_back_to_the_feed(self):
        """Ручка ещё не ответила — показываем счёт ленты, а не пустоту."""
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        body = page[page.index('function inviteFlyPaint'):]
        body = body[:body.index('\n    }')]

        assert 'undefined ? fresh :' in body
