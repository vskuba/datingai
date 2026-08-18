"""Связь с RhapsodyAi: то, чего у этого проекта нет своего.

Проверяем не соседа, а **свою** сторону контракта: что запрос уходит, ответ
разбирается, а его отказ превращается в 502, а не в пустой список.
"""


class TestRhapsodyLink:

    async def test_site_list_comes_from_neighbour(self, auth_client):
        """Список сайтов приходит от соседа: своей таблицы сайтов здесь нет."""
        response = await auth_client.get('/api/site')

        assert response.status_code == 200
        sites = response.json()['result']
        assert isinstance(sites, list) and sites, 'сайтов не пришло — сосед недоступен?'
        assert {'id', 'name', 'url'} <= set(sites[0])

    async def test_site_list_holds_only_dating_sites(self, auth_client):
        """В списке только сайты с меткой `dating`.

        У соседа в закладках лежит и его собственная админка, и рабочие стенды —
        анкету на них не заведёшь, а в селекте они путались. Отбор делает он же,
        по метке; сюда приходит готовое.
        """
        sites = (await auth_client.get('/api/site')).json()['result']

        for site in sites:
            tags = str(site.get('tags') or '').split(',')
            assert 'dating' in tags, f"«{site.get('name')}» без метки dating: {site.get('tags')!r}"

    async def test_site_list_closed_without_login(self, client):
        """Ручка под входом: служебный токен соседа живёт на сервере, и отдавать
        через неё что-либо анониму нельзя."""
        assert (await client.get('/api/site')).status_code == 401

    async def test_health_is_public(self, client):
        """Живность — без входа: её спрашивает compose и внешний опрос."""
        response = await client.get('/health')

        assert response.status_code == 200
        assert response.json()['status'] == 'ok'
