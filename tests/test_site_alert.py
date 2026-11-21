"""Тревога сайта: мигающий значок на вкладке, причины — подсказкой.

20.11.2026 Ladadate сменил кнопку входа, и сутки не входила ни одна из 21
анкеты; вкладка писала `0/21`, как обычной ночью. Значок отвечает «сломано ли
тут что-то не по нашей воле» (`site_alert.py`).
"""

from pathlib import Path

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.site_alert import site_alert_rows
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.chat

SITE_JS = (Path(__file__).parents[1] / 'data' / 'uvicorn' / 'public' / 'js' / 'dating'
           / 'site.js').read_text(encoding='utf-8')


async def _planned(profile_id: int, failing: bool) -> None:
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE profile SET enabled = 1, schedule_on = 1, status = %s,'
            '  last_error = %s WHERE id = %s',
            ('offline' if failing else 'online',
             'вход не выполнен: нет cookie' if failing else '', int(profile_id)))
        if failing:
            await db.execute(
                "INSERT INTO log (profile_id, level, text) VALUES (%s, 'fail', %s)",
                (int(profile_id), 'Вход не прошёл на шаге 4: Locator.click: Timeout\nCall log'))
        await db.connection.commit()


async def _alerts() -> list:
    async with mysql_get_db_async() as db:
        return (await site_alert_rows(db)).get(TEST_STAND_SITE, [])


class TestLoginsFailing:
    async def test_most_failing_raises_the_alarm(self, girl_make):
        girls = [await girl_make() for _ in range(3)]
        for n, girl in enumerate(girls):
            await _planned(girl, failing=n < 2)

        got = [a for a in await _alerts() if a.startswith('Вход не проходит')]

        assert got and got[0].startswith('Вход не проходит у 2 из 3 анкет'), got
        assert 'шаге 4' in got[0] and 'Call log' not in got[0], \
            'причина — первая строка отказа из журнала'

    async def test_one_failing_is_not_the_site(self, girl_make):
        girls = [await girl_make() for _ in range(3)]
        for n, girl in enumerate(girls):
            await _planned(girl, failing=n < 1)

        assert not [a for a in await _alerts() if a.startswith('Вход не проходит')]


class TestErrorsOfTheSite:
    @pytest.fixture
    async def rows(self):
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO error (source, site_id, message) VALUES'
                " ('idle_probe_alert', %s, 'работа стоит'),"
                " ('probe_alert', %s, 'сайт отвечает 503')", (TEST_STAND_SITE, TEST_STAND_SITE))
            await db.connection.commit()
        yield
        async with mysql_get_db_async() as db:
            await db.execute("DELETE FROM error WHERE source IN ('idle_probe_alert', 'probe_alert')")
            await db.connection.commit()

    async def test_failures_count_idle_does_not(self, rows):
        got = await _alerts()

        assert any('сайт отвечает 503' in a for a in got)
        assert not any('работа стоит' in a for a in got), 'простой — не поломка'

    async def test_the_handle_carries_them(self, rows, auth_client):
        got = (await auth_client.get('/api/site/live')).json()['result']

        assert any('503' in a for a in got[str(TEST_STAND_SITE)]['alerts'])


class TestThePageShowsIt:
    def test_the_icon_takes_the_reasons_as_its_title(self):
        at = SITE_JS.index('site-tab-alert')
        assert "alerts.join" in SITE_JS[at - 200:at + 200]
