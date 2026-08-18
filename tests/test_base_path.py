"""Базовый путь: панель должна работать и в корне домена, и под префиксом.

Локально `BASE_PATH` пуст, на проде — `/dating`. Проверка идёт по тому, что
отдано в разметке: прокси срезает префикс, и ошибку в путях видно только там.
"""

import os
import re

BASE_PATH = os.getenv('BASE_PATH', '')


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
