"""Вкладка «Ошибки» на «Журналах»: все отказы проекта одним списком.

Разделение то же, что у `test_log_feed.py`: сборка запроса проверяется юнит-тестами
(чистая функция, ошибки в ней не падают, а тихо показывают не то), поведение ручки —
по HTTP, как остальная сюита (`testing.md`, §1.2).

⚠ **Главное, что здесь сторожится: третья копия правил не завелась.** Текст строки
про прогон собирает `_run_text` из `log.py`, заголовок общего отказа — тот же
`ERROR_TITLES`, что и на странице «Ошибки». Появится своя копия — лента «Ошибок»
заговорила бы об одном событии не тем словом, что журнал анкеты.
"""

import pytest

from src.dating.error import ERROR_TITLES
from src.uvicorn.routers.dating.error import _feed_query, _feed_row

pytestmark = pytest.mark.page
SITE = 343


class TestQueryShape:
    """Что попадает в запрос ленты отказов."""

    def test_all_joins_four_sources(self):
        """Отказы приходят из четырёх мест — и все четыре складываются в ленту."""
        sql, params = _feed_query(SITE, '', '')

        assert 'UNION ALL' in sql
        for table in ('FROM log', 'FROM scenario_run', 'FROM request', 'FROM error'):
            assert table in sql, f'половины `{table}` в ленте нет'
        assert params == [SITE, SITE, SITE, SITE]

    def test_every_half_carries_the_same_columns(self):
        """⚠ Иначе `UNION` их не сложит.

        Чего-то половине неоткуда взять: у общих отказов нет анкеты, у журнала
        анкеты нет счётчика повторов. Подставляется пустым или нулём — это разная
        природа записей, а не потеря.
        """
        sql, _ = _feed_query(SITE, '', '')
        halves = sql.split(' UNION ALL ')

        assert len(halves) == 4
        for column in ('source', 'text', 'created_at', 'first_at', 'hits',
                       'profile_id', 'profile_name', 'profile_photo'):
            assert all(column in half for half in halves), f'колонки {column} нет во всех половинах'

    def test_only_failures_are_taken(self):
        """Лента берёт из каждой половины только неудачу: к ней приходят за тем,
        что сломалось, а не за всей лентой подряд."""
        sql, _ = _feed_query(SITE, '', '')

        assert "l.level = 'fail'" in sql, 'журнал анкет берётся без отбора по уровню'
        assert "r.status = 'error'" in sql, 'прогоны берутся без отбора по исходу'
        assert 'r.ok = 0' in sql, 'походы наружу берутся без отбора по исходу'

    def test_each_half_keeps_its_own_site_rule(self):
        """⚠ Отбор по сайту у половин разный — как в общей ленте и на «Запросах»:
        строка журнала знает только анкету, походы без сайта (к соседу) и общие
        отказы не относятся ни к одному сайту и прячутся молча, если их обрезать
        жёстким равенством."""
        sql, _ = _feed_query(SITE, '', '')

        assert 'p.site_id = %s' in sql
        assert 'r.site_id = %s' in sql
        assert '(r.site_id = %s OR r.site_id = 0)' in sql
        assert 'site_id IN (%s, 0)' in sql


class TestQueryFilters:
    """Отбор по источнику и поиск."""

    def test_source_narrows_to_one_half(self):
        """Выбранный источник — половина ленты, а не весь список: переключатель
        «откуда» обязан переключать, а не подмешивать остальное."""
        for source, table in (('profile', 'FROM log'), ('scenario', 'FROM scenario_run'),
                              ('request', 'FROM request'), ('system', 'FROM error')):
            sql, params = _feed_query(SITE, source, '')

            assert table in sql
            assert len(sql.split(' UNION ALL ')) == 1
            assert params[:1] == [SITE]

    def test_search_covers_text_and_profile(self):
        """Ищут и по причине, и по анкете — помняют обычно одно из двух."""
        sql, params = _feed_query(SITE, 'profile', 'Diana')

        assert 'l.text LIKE %s' in sql
        assert params.count('%Diana%') == 3


class TestRowText:
    """Текст строки собирается сервером — и теми же словами, что на прежних
    страницах: третья копия правил о прогоне разошлась бы с двумя первыми."""

    def test_run_row_reuses_the_log_wording(self):
        row = _feed_row({'source': 'scenario', 'scenario_name': 'вход',
                         'step_no': 0, 'text': 'браузер соседа занят'})

        assert row['text'] == 'Сценарий «вход» не запустился: браузер соседа занят'

    def test_system_row_carries_the_page_title(self):
        """Человек читает «Список сайтов: сосед не ответил», а не `sites_sync` —
        тот же словарь, что на странице «Ошибки» (`error_rows`)."""
        row = _feed_row({'source': 'system', 'scenario_name': 'sites_sync',
                        'text': 'сосед не ответил', 'hits': 7, 'first_at': '',
                        'created_at': ''})

        assert row['text'].startswith(ERROR_TITLES['sites_sync'])
        assert 'сосед не ответил' in row['text']


class TestFeedHandler:
    """Поведение ручки по HTTP."""

    async def test_feed_answers_for_a_real_site(self, auth_client):
        answer = (await auth_client.get('/api/dating/error/feed',
                                        params={'site_id': SITE, 'per_page': 5}))

        assert answer.status_code == 200
        result = answer.json()['result']
        assert {'rows', 'total', 'page', 'pages', 'sources'} <= set(result)
        assert len(result['rows']) <= 5

    async def test_row_carries_source_and_repeat_counter(self, auth_client):
        rows = (await auth_client.get('/api/dating/error/feed',
                                      params={'site_id': SITE, 'per_page': 5})
                ).json()['result']['rows']

        if not rows:
            return

        for row in rows:
            assert row['source'] in ('profile', 'scenario', 'request', 'system')
            assert {'text', 'created_at', 'first_at', 'hits',
                    'profile_id', 'profile_name', 'profile_photo'} <= set(row)

    async def test_page_is_capped(self, auth_client):
        result = (await auth_client.get('/api/dating/error/feed',
                                        params={'site_id': SITE, 'per_page': 5000})
                  ).json()['result']

        assert len(result['rows']) <= 200

    async def test_site_is_required(self, auth_client):
        assert (await auth_client.get('/api/dating/error/feed')).status_code == 422

    async def test_unknown_source_is_422(self, auth_client):
        response = await auth_client.get('/api/dating/error/feed',
                                         params={'site_id': SITE, 'source': 'что-то'})

        assert response.status_code == 422

    async def test_closed_without_login(self, client):
        assert (await client.get('/api/dating/error/feed')).status_code == 401

    async def test_open_to_operator(self, dating_client):
        """Оператору лента открыта: он первым замечает, что панель показывает
        не то, — и тот же источник отказа виден с любой страницы раздела."""
        response = await dating_client.get('/api/dating/error/feed',
                                           params={'site_id': SITE})

        assert response.status_code == 200
