"""Общая лента журнала: два источника одним списком.

Две половины проверки, и разделение не случайное.

**Сборка запроса** (`_query`) проверяется юнит-тестами: это чистая функция, а
именно в ней живут решения, которые легко испортить незаметно — какие колонки
попадают в `UNION`, как отбирается сайт у каждой половины, что делать с уровнем,
которого у прогонов не бывает. Ошибка здесь не падает, а тихо показывает не то.

**Поведение ручки** проверяется по HTTP, как остальная сюита (`testing.md`): коды
отказов, форма ответа, права.

⚠ **Прогоны сценариев здесь не гоняются.** Каждый — сорок секунд живого браузера и
вход на сайт под анкетой; сюита такого не делает (`testing.md`, §2.1). Записи в
базу она тоже не кладёт: своего доступа к базе у неё нет и заводить его ради теста
значило бы расширить её контракт.
"""

from src.uvicorn.routers.dating.dating_log import _query

SITE = 343


class TestQueryShape:
    """Что попадает в запрос ленты."""

    def test_all_joins_two_sources(self):
        """`all` — обе половины через `UNION ALL`.

        Складывается в базе, а не склейкой двух ответов в Python: чтобы показать
        пятьдесят свежих строк, надо знать, сколько взять из каждой таблицы, а
        этого заранее не знает никто.
        """
        sql, params = _query(SITE, 'all', '', '')

        assert 'UNION ALL' in sql
        assert 'dating_log' in sql and 'dating_scenario_run' in sql
        assert params == [SITE, SITE]

    def test_profile_only(self):
        sql, params = _query(SITE, 'profile', '', '')

        assert 'dating_scenario_run' not in sql
        assert params == [SITE]

    def test_scenario_only(self):
        sql, params = _query(SITE, 'scenario', '', '')

        assert 'dating_log' not in sql
        assert params == [SITE]

    def test_both_halves_select_the_same_columns(self):
        """⚠ Иначе `UNION` их просто не сложит.

        У строки журнала анкеты нет ни сценария, ни длительности — недостающее
        подставляется пустым. Это не потеря, а разная природа записей.
        """
        sql, _ = _query(SITE, 'all', '', '')
        halves = sql.split(' UNION ALL ')

        assert len(halves) == 2
        for column in ('source', 'level', 'text', 'created_at', 'profile_id',
                       'profile_name', 'scenario_name', 'step_no', 'ms'):
            assert all(column in half for half in halves), f'колонки {column} нет в обеих половинах'

    def test_site_is_matched_differently_in_each_half(self):
        """И это не оплошность: у прогона сайт записан прямо в строке, а строка
        журнала анкеты знает только анкету — сайт достаётся через неё."""
        sql, _ = _query(SITE, 'all', '', '')

        assert 'p.site_id = %s' in sql, 'журнал анкеты отбирается через саму анкету'
        assert 'r.site_id = %s' in sql, 'у прогона сайт лежит в строке'


class TestQueryFilters:
    """Отбор: уровень и поиск."""

    def test_level_reaches_both_halves(self):
        """`fail` у прогона — это `status = error`.

        Сводится к тем же уровням, что у журнала анкеты: иначе фильтр «только
        отказы» работал бы на одной половине ленты и молчал на другой.
        """
        sql, params = _query(SITE, 'all', 'fail', '')

        assert 'l.level = %s' in sql
        assert 'r.status = %s' in sql
        assert params == [SITE, 'fail', SITE, 'error']

    def test_level_without_runs_empties_that_half(self):
        """⚠ Уровней `info`, `in`, `out` у прогонов не бывает.

        Половина ленты обязана остаться пустой, а не отдать все прогоны подряд:
        иначе «покажи ход работы» приносило бы и то, чего человек не просил.
        """
        sql, params = _query(SITE, 'all', 'info', '')

        assert '1 = 0' in sql
        assert params == [SITE, 'info', SITE]

    def test_search_covers_text_and_profile(self):
        """Ищут и по тексту строки, и по анкете: помнят обычно одно из двух."""
        sql, params = _query(SITE, 'profile', '', 'Diana')

        assert 'l.text LIKE %s' in sql
        assert params.count('%Diana%') == 3

    def test_search_covers_scenario_and_error(self):
        """У прогонов ищется имя сценария и причина отказа: с симптома начинают
        чаще, чем с имени."""
        sql, params = _query(SITE, 'scenario', '', 'занят')

        assert 'r.scenario_name LIKE %s' in sql
        assert 'r.error LIKE %s' in sql
        assert params.count('%занят%') == 4

    def test_name_expression_is_shared(self):
        """Имя анкеты собирается **одним** выражением и для показа, и для поиска.

        Отдельной колонки с именем у анкеты нет — оно лежит в JSON-столбце
        `profile`. Две разошедшиеся копии выражения дали бы «ищу по одному,
        показываю другое».
        """
        sql, _ = _query(SITE, 'profile', '', 'кто-то')

        assert sql.count("JSON_EXTRACT(p.profile, '$.name')") == 2


class TestFeedHandler:
    """Поведение ручки по HTTP."""

    async def test_feed_answers_for_a_real_site(self, auth_client):
        answer = (await auth_client.get('/api/dating/log',
                                        params={'site_id': SITE, 'per_page': 5}))

        assert answer.status_code == 200
        result = answer.json()['result']
        assert {'rows', 'total', 'page', 'pages'} <= set(result)
        assert len(result['rows']) <= 5

    async def test_row_carries_source_and_measurements(self, auth_client):
        """У каждой строки есть, чем отличить прогон от работы по анкете.

        Без `source` их пришлось бы разбирать по тексту, а длительность и шаг —
        половина смысла журнала прогонов: «вход стал идти вдвое дольше» видно
        только рядом лежащими замерами.
        """
        rows = (await auth_client.get('/api/dating/log',
                                      params={'site_id': SITE, 'per_page': 5})
                ).json()['result']['rows']

        if not rows:
            return

        for row in rows:
            assert row['source'] in ('profile', 'scenario')
            assert {'level', 'text', 'created_at', 'scenario_name', 'step_no', 'ms'} <= set(row)

    async def test_page_is_capped(self, auth_client):
        """Лента читается человеком, а не выгружается: тысяча строк за запрос —
        это не чтение."""
        result = (await auth_client.get('/api/dating/log',
                                        params={'site_id': SITE, 'per_page': 5000})
                  ).json()['result']

        assert len(result['rows']) <= 200

    async def test_site_is_required(self, auth_client):
        """Без сайта лента перемешала бы работу по разным сайтам."""
        assert (await auth_client.get('/api/dating/log')).status_code == 422

    async def test_unknown_kind_is_422(self, auth_client):
        response = await auth_client.get('/api/dating/log',
                                         params={'site_id': SITE, 'kind': 'что-то'})

        assert response.status_code == 422

    async def test_closed_without_login(self, client):
        assert (await client.get('/api/dating/log')).status_code == 401

    async def test_open_to_operator(self, dating_client):
        """Оператору лента открыта: журнал — это ответ на «почему анкета не
        отвечает», и держать его за админской дверью значит звать админа на каждый
        разбор."""
        response = await dating_client.get('/api/dating/log', params={'site_id': SITE})

        assert response.status_code == 200
