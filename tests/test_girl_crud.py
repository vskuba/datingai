"""Учётки анкет: заведение, правка, удаление и что наружу не отдаётся."""

import uuid

import pytest

# Сайт, к которому привязываются тестовые учётки. Живёт он в RhapsodyAi, и
# проверка его существования — часть контракта: несуществующий даёт 422.
SITE_ID = 19

# Заведомо отсутствующий сайт: номер заведомо больше любого разумного.
SITE_ID_MISSING = 999999


@pytest.fixture
async def girl(auth_client):
    """Одноразовая учётка. Удаляется после теста, даже если тест упал."""
    login = f'test-{uuid.uuid4().hex[:10]}'
    response = await auth_client.post('/api/dating/girl', json={
        'site_id': SITE_ID, 'login': login, 'password': 'secret', 'note': 'автотест'})
    assert response.status_code == 201, response.text[:200]
    girl_id = response.json()['result']

    yield {'id': girl_id, 'login': login}

    await auth_client.delete(f'/api/dating/girl/{girl_id}')


class TestGirlCrud:

    async def test_create_and_list(self, auth_client, girl):
        """Заведённая учётка видна в списке своего сайта."""
        response = await auth_client.get(f'/api/dating/girl?site_id={SITE_ID}')

        assert response.status_code == 200
        logins = [row['login'] for row in response.json()['result']]
        assert girl['login'] in logins

    async def test_password_never_leaves(self, auth_client, girl):
        """Пароля в ответе нет — только признак, что он задан.

        Страница его не показывает, а утечь через журнал, скриншот или чужой глаз
        он не должен.
        """
        response = await auth_client.get(f'/api/dating/girl?site_id={SITE_ID}')
        row = next(r for r in response.json()['result'] if r['login'] == girl['login'])

        assert 'password' not in row
        assert row['has_password'] is True

    async def test_duplicate_login_rejected(self, auth_client, girl):
        """Тот же логин на том же сайте — 409, а не второй такой же."""
        response = await auth_client.post('/api/dating/girl', json={
            'site_id': SITE_ID, 'login': girl['login'], 'password': 'x'})

        assert response.status_code == 409
        assert girl['login'] in response.json()['detail']

    async def test_unknown_site_rejected(self, auth_client):
        """Сайта нет — 422. Проверяет его сосед, своей таблицы сайтов здесь нет."""
        response = await auth_client.post('/api/dating/girl', json={
            'site_id': SITE_ID_MISSING, 'login': f'test-{uuid.uuid4().hex[:8]}',
            'password': 'x'})

        assert response.status_code == 422

    async def test_update_keeps_password_when_empty(self, auth_client, girl):
        """Пустой пароль при правке означает «не трогать», а не «стереть».

        Форма его не показывает, и пустое поле — это «оставить прежний».
        """
        response = await auth_client.put(f'/api/dating/girl/{girl["id"]}', json={
            'site_id': SITE_ID, 'login': girl['login'], 'password': '', 'note': 'правка'})
        assert response.status_code == 200

        rows = (await auth_client.get(f'/api/dating/girl?site_id={SITE_ID}')).json()['result']
        row = next(r for r in rows if r['login'] == girl['login'])

        assert row['has_password'] is True, 'пустое поле стёрло пароль'
        assert row['note'] == 'правка'

    async def test_delete(self, auth_client):
        """Удаление уносит учётку целиком; повтор отвечает 404."""
        login = f'test-{uuid.uuid4().hex[:10]}'
        created = await auth_client.post('/api/dating/girl', json={
            'site_id': SITE_ID, 'login': login, 'password': 'x'})
        girl_id = created.json()['result']

        assert (await auth_client.delete(f'/api/dating/girl/{girl_id}')).status_code == 200
        assert (await auth_client.delete(f'/api/dating/girl/{girl_id}')).status_code == 404

    async def test_missing_girl_is_404(self, auth_client):
        """Ручки несуществующей учётки отвечают 404, а не пустотой."""
        for path in ('log', 'message', 'profile'):
            response = await auth_client.get(f'/api/dating/girl/999999/{path}')
            assert response.status_code == 404, f'{path} ответил {response.status_code}'
