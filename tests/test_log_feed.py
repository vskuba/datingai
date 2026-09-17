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

import pytest

from src.uvicorn.routers.dating.log import _query, _run_text

pytestmark = pytest.mark.page
SITE = 343


class TestQueryShape:
    """Что попадает в запрос ленты."""

    def test_all_joins_two_sources(self):
        """`all` — обе половины через `UNION ALL`.

        Складывается в базе, а не склейкой двух ответов в Python: чтобы показать
        пятьдесят свежих строк, надо знать, сколько взять из каждой таблицы, а
        этого заранее не знает никто.
        """
        sql, params = _query(SITE, 0, 'all', '', '')

        assert 'UNION ALL' in sql
        assert 'FROM log' in sql and 'FROM scenario_run' in sql
        assert params == [SITE, SITE]

    def test_profile_only(self):
        sql, params = _query(SITE, 0, 'profile', '', '')

        assert 'FROM scenario_run' not in sql
        assert params == [SITE]

    def test_scenario_only(self):
        """⚠ Ищем `FROM log`, а не голое `log`, и это не педантизм.

        После того как таблицы потеряли префикс `dating_`, имя стало коротким —
        и `log` находится внутри `p.login`, который в запросе есть всегда. Тест
        падал, хотя код был верен: он проверял подстроку, а не таблицу.
        """
        sql, params = _query(SITE, 0, 'scenario', '', '')

        assert 'FROM log' not in sql
        assert params == [SITE]

    def test_both_halves_select_the_same_columns(self):
        """⚠ Иначе `UNION` их просто не сложит.

        У строки журнала анкеты нет ни сценария, ни длительности — недостающее
        подставляется пустым. Это не потеря, а разная природа записей.
        """
        sql, _ = _query(SITE, 0, 'all', '', '')
        halves = sql.split(' UNION ALL ')

        assert len(halves) == 2
        for column in ('source', 'level', 'text', 'created_at', 'profile_id',
                       'profile_name', 'scenario_name', 'step_no', 'ms'):
            assert all(column in half for half in halves), f'колонки {column} нет в обеих половинах'

    def test_site_is_matched_differently_in_each_half(self):
        """И это не оплошность: у прогона сайт записан прямо в строке, а строка
        журнала анкеты знает только анкету — сайт достаётся через неё."""
        sql, _ = _query(SITE, 0, 'all', '', '')

        assert 'p.site_id = %s' in sql, 'журнал анкеты отбирается через саму анкету'
        assert 'r.site_id = %s' in sql, 'у прогона сайт лежит в строке'


class TestQueryFilters:
    """Отбор: уровень и поиск."""

    def test_level_reaches_both_halves(self):
        """`fail` у прогона — это `status = error`.

        Сводится к тем же уровням, что у журнала анкеты: иначе фильтр «только
        отказы» работал бы на одной половине ленты и молчал на другой.
        """
        sql, params = _query(SITE, 0, 'all', 'fail', '')

        assert 'l.level = %s' in sql
        assert 'r.status = %s' in sql
        assert params == [SITE, 'fail', SITE, 'error']

    def test_info_finds_sent_runs(self):
        """`info` — это ход работы, и отметка «запрос ушёл» тоже он.

        Прогон в состоянии `sent` ещё не имеет итога; ложить его в `ok` значило бы
        сказать, что он отработал, а в `fail` — что упал. Ни то ни другое.
        """
        sql, params = _query(SITE, 0, 'all', 'info', '')

        assert 'r.status = %s' in sql
        assert params == [SITE, 'info', SITE, 'sent']

    def test_chat_levels_empty_the_run_half(self):
        """⚠ Уровни `in` и `out` — сообщения чата, у прогонов их не бывает.

        Половина ленты обязана остаться пустой, а не отдать все прогоны подряд:
        иначе «покажи входящие» приносило бы и то, чего человек не просил.
        """
        sql, params = _query(SITE, 0, 'all', 'in', '')

        assert '1 = 0' in sql
        assert params == [SITE, 'in', SITE]

    def test_search_covers_text_and_profile(self):
        """Ищут и по тексту строки, и по анкете: помнят обычно одно из двух."""
        sql, params = _query(SITE, 0, 'profile', '', 'Diana')

        assert 'l.text LIKE %s' in sql
        assert params.count('%Diana%') == 3

    def test_search_covers_scenario_and_error(self):
        """У прогонов ищется имя сценария и причина отказа: с симптома начинают
        чаще, чем с имени."""
        sql, params = _query(SITE, 0, 'scenario', '', 'занят')

        assert 'r.scenario_name LIKE %s' in sql
        assert 'r.error LIKE %s' in sql
        assert params.count('%занят%') == 4

    def test_name_expression_is_shared(self):
        """Имя анкеты собирается **одним** выражением и для показа, и для поиска.

        Отдельной колонки с именем у анкеты нет — оно лежит в JSON-столбце
        `profile`. Две разошедшиеся копии выражения дали бы «ищу по одному,
        показываю другое».
        """
        sql, _ = _query(SITE, 0, 'profile', '', 'кто-то')

        assert sql.count("JSON_EXTRACT(p.profile, '$.name')") == 2


class TestProfileScope:
    """Тот же сборщик собирает и журнал одной анкеты."""

    def test_profile_filter_reaches_both_halves(self):
        """⚠ Прогон — одна строка в базе, показанная в двух местах.

        Журнал анкеты на «Профилях» и общая лента «Журналов» складывают одни и те
        же две таблицы; разница только в отборе. Дублировать прогон в `log`
        значило бы держать две записи об одном событии, которые разойдутся при
        первой же правке текста.
        """
        sql, params = _query(0, 849, 'all', '', '')

        assert 'l.profile_id = %s' in sql, 'журнал анкеты не отбирается по ней самой'
        assert 'r.profile_id = %s' in sql, 'прогоны не отбираются по анкете'
        assert params == [849, 849]

    def test_no_scope_does_not_break_the_query(self):
        """Без отбора половина отдала бы всё подряд, а пустой `WHERE` — это
        синтаксическая ошибка. Оба вызывающих отбор задают, но полагаться на это в
        сборке запроса нельзя: третий об этом правиле не узнает."""
        sql, params = _query(0, 0, 'all', '', '')

        assert 'WHERE 1 = 1' in sql
        assert params == []


class TestRunWording:
    """Текст строки прогона собирает сервер, а не страница."""

    def test_sent(self):
        """Первая из пары: запрос ушёл, ответа ещё нет."""
        assert _run_text({'level': 'info', 'scenario_name': 'вход'}) \
            == 'Отправлен запрос на сценарий «вход»'

    def test_answer_carries_duration(self):
        """Вторая из пары — с длительностью: «вход стал идти вдвое дольше» видно
        только рядом лежащими замерами."""
        text = _run_text({'level': 'ok', 'scenario_name': 'вход', 'ms': 38300})

        assert text == 'Получен ответ на сценарий «вход» — 38.3 с'

    def test_failure_names_the_step(self):
        text = _run_text({'level': 'fail', 'scenario_name': 'вход', 'step_no': 7,
                          'text': 'нет кнопки'})

        assert text == 'Сценарий «вход» встал на шаге 7: нет кнопки'

    def test_step_zero_means_it_never_started(self):
        """⚠ Шаг 0 — не «встал на первом», а «не начинался»: сосед не ответил,
        вкладка не открывалась. «Встал на шаге 0» было бы враньём про место
        отказа."""
        text = _run_text({'level': 'fail', 'scenario_name': 'вход', 'step_no': 0,
                          'text': 'браузер соседа занят'})

        assert 'не запустился' in text
        assert 'шаге 0' not in text


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


class TestFaceInTheFeed:
    """⚠⚠ Лицо анкеты рядом с именем — потому что имена повторяются.

    На сайте две Дианы, и в ленте они различались только памятью читающего:
    строки одинаковые, понять, чья работа, можно было лишь открыв «Анкеты».
    Лицо узнаётся быстрее имени, поэтому стоит первым.

    ⚠ **Пустое фото — обычное дело, а не пробел в данных**: анкету просто ещё не
    читали с сайта. Страница рисует пустую клетку того же размера — иначе имена
    ездят влево-вправо и столбец перестаёт читаться сверху вниз.
    """

    async def test_the_feed_returns_a_photo(self, auth_client):
        rows = (await auth_client.get('/api/dating/log',
                                      params={'site_id': SITE, 'per_page': 5})
                ).json()['result']['rows']

        if not rows:
            pytest.skip('журнал стенда пуст')

        assert 'profile_photo' in rows[0], \
            'фото не приходит — в ленте снова не отличить двух девушек с одним именем'

    async def test_the_requests_tab_returns_it_too(self, auth_client):
        """Вкладка «Запросы» — та же беда и то же лекарство."""
        rows = (await auth_client.get('/api/dating/request',
                                      params={'site_id': SITE, 'per_page': 5})
                ).json()['result']['rows']

        if not rows:
            pytest.skip('журнал запросов стенда пуст')

        assert 'profile_photo' in rows[0]

    def test_both_feeds_share_one_renderer(self):
        """⚠ Рисует лицо **общий** помощник, а не две копии в разметке.

        Второй экземпляр разошёлся бы с первым на первой же правке — и первым
        потерялся бы `referrerpolicy`, без которого сайты знакомств отвечают
        `403` на запрос с чужим `Referer`, и аватарки пусты при верных адресах.
        """
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/log.html').read_text(encoding='utf-8')
        view = pathlib.Path(
            'data/uvicorn/public/js/dating/view.js').read_text(encoding='utf-8')

        assert 'export function face(' in view
        assert page.count('face(r.profile_photo') == 2, \
            'лицо рисуется не в обеих лентах'
        assert 'referrerpolicy' in view
