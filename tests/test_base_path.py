"""Базовый путь: панель должна работать и в корне домена, и под префиксом.

Сейчас `BASE_PATH` пуст везде — у панели своё имя. Проверка остаётся: механизм
живой, и сюита с заданным `BASE_PATH` показывает, что префикс в разметке
проставляется. Смотреть надо именно на разметку — прокси префикс срезает, и
ошибку в путях видно только там.
"""

import pytest

import os
import re

pytestmark = pytest.mark.page
BASE_PATH = os.getenv('BASE_PATH', '')


class TestStaticFreshness:
    """⚠ Браузер не должен показывать вчерашний код.

    Ловилось дважды и одинаково: страница грузила новый HTML со старым модулем и
    падала с «не экспортирует timeFull», хотя экспорт на сервере был.
    """

    async def test_static_asks_to_revalidate(self, client):
        """`Cache-Control: no-cache` — «перепроверь перед использованием».

        Без него Starlette отдаёт только `ETag`, а браузер решает сам и обычно
        кэширует эвристически, по возрасту файла.
        """
        answer = await client.get('/static/js/dating/view.js')

        assert answer.status_code == 200
        assert 'no-cache' in answer.headers.get('cache-control', '')

    async def test_static_links_carry_a_version(self, auth_client):
        """⚠ Одного `no-cache` мало: он работает **при новом запросе**.

        ES-модуль, уже загруженный во вкладку, не перезапрашивается вовсе — и
        обычное обновление страницы не помогает. Поэтому в адресе стоит метка
        правки файла: правка меняет адрес, а другой адрес для браузера другой
        модуль.
        """
        html = (await auth_client.get('/admin/dating/profile')).text
        links = re.findall(r'/static/[\w./-]+', html)

        assert links, 'на странице нет ссылок на статику'
        for link in links:
            assert '?v=' in html[html.index(link):html.index(link) + len(link) + 12], \
                f'{link} без метки версии'

    async def test_version_changes_with_the_file(self, auth_client):
        """Метка — время правки, а не случайное число: одинаковые ответы должны
        давать одинаковые адреса, иначе кэш не работал бы вовсе."""
        first = re.findall(r'/static/js/dating/view\.js\?v=(\d+)',
                           (await auth_client.get('/admin/dating/profile')).text)
        second = re.findall(r'/static/js/dating/view\.js\?v=(\d+)',
                            (await auth_client.get('/admin/dating/profile')).text)

        assert first and first == second, 'метка версии скачет между показами'


class TestBasePath:

    async def test_login_page_paths_match_base(self, client):
        """Ссылки на статику идут с тем же префиксом, под которым живёт панель.

        Если написать их от корня, браузер под `/dating` попросит статику у
        соседа — и получит его страницу вместо нашего css.
        """
        html = (await client.get('/login')).text
        hrefs = re.findall(r'href="([^"]*static[^"]*)"', html)

        assert hrefs, 'на странице входа нет ссылок на статику'
        for href in hrefs:
            assert href.startswith(f'{BASE_PATH}/static/'), href

    async def test_js_knows_base(self, client):
        """Странице передан её префикс — по нему JS строит запросы к API."""
        html = (await client.get('/login')).text
        match = re.search(r"const BASE = '([^']*)'", html)

        assert match, 'константа BASE не проставлена в разметку'
        assert match.group(1) == BASE_PATH

    async def test_static_is_served(self, client):
        """Статика действительно отдаётся.

        Путь **без** `BASE_PATH`: он уже внутри `BASE_URL`. Приписать его второй
        раз — получить `/dating/dating/static/...`; на этом тест и спотыкался.
        Разделение простое: `BASE_URL` отвечает за «куда стучаться», `BASE_PATH` —
        за то, что должно быть написано в разметке.
        """
        response = await client.get('/static/js/api.js')

        assert response.status_code == 200
        assert 'export async function getData' in response.text
