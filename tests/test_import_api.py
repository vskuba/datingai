"""Ручки импорта: права, отказы и признак готовности.

Сам импорт сюита **не гоняет**: он водит браузер соседа, ходит на живой сайт
знакомств под учёткой агентства и заводит анкеты в базу. Это то же правило, по
которому не трогают `/login` и `/check` (`testing.md`, §2.2) — минуты работы и
чужие данные.

Проверяется то, что можно проверить без сайта: закрыта ли ручка без входа, что
отвечает признак готовности и как ручка отказывает на неверном вводе. Правила
слияния лежат отдельно и проверяются начисто — `test_import_merge.py`.
"""

import os

# Сайт, у которого сценария импорта заведомо нет: признак готовности обязан
# сказать «нет» и объяснить, чего не хватает.
SITE_ID_NO_IMPORT = int(os.getenv('SITE_ID', '19'))

# Номер, которого нет в закладках соседа вовсе.
SITE_ID_MISSING = 999999


class TestImportReady:
    """Признак «импорт настроен» — им страница решает, гасить ли кнопку."""

    async def test_closed_without_session(self, client):
        """Без входа не пускает: настройка чужого сайта — не общее сведение."""
        response = await client.get('/api/dating/import/ready?site_id=1')

        assert response.status_code == 401

    async def test_no_site_is_not_ready(self, auth_client):
        """Сайт не выбран — «не готово» с причиной, а не отказ ручки: страница
        открывается до всякого выбора, и 4xx на ровном месте выглядел бы поломкой.
        """
        result = (await auth_client.get('/api/dating/import/ready')).json()['result']

        assert result['ready'] is False
        assert result['why']

    async def test_site_without_scenario_is_not_ready(self, auth_client):
        """У сайта нет сценария импорта — кнопка гаснет, и причина сказана словами.

        Признак берётся у соседа, а не из своей настройки: сценарий заводят там, и
        своя галочка разъехалась бы с ним в день переименования.
        """
        response = await auth_client.get(f'/api/dating/import/ready?site_id={SITE_ID_NO_IMPORT}')

        assert response.status_code == 200, response.text[:200]
        result = response.json()['result']
        assert result['ready'] is False
        assert 'сценар' in result['why'].lower(), result['why']

    async def test_unknown_site_is_not_ready(self, auth_client):
        """Несуществующий сайт — тоже «не готово», а не 502: у соседа его просто
        нет, и это ответ, а не сбой связи."""
        result = (await auth_client.get(
            f'/api/dating/import/ready?site_id={SITE_ID_MISSING}')).json()['result']

        assert result['ready'] is False


class TestImportProfile:
    """Сам импорт — только отказы: удачный путь водит браузер и трогает живой сайт."""

    async def test_closed_without_session(self, client):
        response = await client.post('/api/dating/import/profile?site_id=1', json={})

        assert response.status_code == 401

    async def test_no_site_is_422(self, auth_client):
        response = await auth_client.post('/api/dating/import/profile', json={})

        assert response.status_code == 422

    async def test_site_without_scenario_is_422(self, auth_client):
        """Нет сценария — 422 с тем, что делать человеку, а не 500.

        Сайт выбран правильный, просто импортировать его пока нечем; чинить это в
        соседней панели, и текст отказа должен так и говорить.
        """
        response = await auth_client.post(
            f'/api/dating/import/profile?site_id={SITE_ID_NO_IMPORT}', json={})

        assert response.status_code == 422, response.text[:200]
        assert 'сценар' in response.json()['detail'].lower()
