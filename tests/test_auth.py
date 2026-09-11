"""Вход в панель: кого пускают, кого нет и что видно без входа."""

import pytest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USERNAME

pytestmark = pytest.mark.core
class TestAuth:

    async def test_login_ok(self, client):
        """Верная пара — сессия и имя пользователя в ответе."""
        response = await client.post('/auth/login', json={
            'username': ADMIN_USERNAME, 'password': ADMIN_PASSWORD})

        assert response.status_code == 200
        assert response.json()['result']['username'] == ADMIN_USERNAME

    async def test_login_wrong_password(self, client):
        """Неверный пароль — 401 без подробностей о том, что именно не сошлось.

        Разные ответы на «нет такого пользователя» и «неверный пароль» полезны
        только тому, кто перебирает логины.
        """
        response = await client.post('/auth/login', json={
            'username': ADMIN_USERNAME, 'password': 'no-such-password'})

        assert response.status_code == 401
        assert 'неверный' in response.json()['detail'].lower()

    async def test_login_unknown_user(self, client):
        """Несуществующий логин отвечает тем же, что и неверный пароль."""
        response = await client.post('/auth/login', json={
            'username': 'nobody-here', 'password': 'whatever'})

        assert response.status_code == 401

    @pytest.mark.parametrize('payload,expected', [
        ({'username': 'admin', 'password': ''}, 'пароль'),
        ({'username': 'admin'}, 'пароль'),
        ({}, 'логин'),
    ])
    async def test_validation_is_human_readable(self, client, payload, expected):
        """Пустое поле объясняется словами, а не списком объектов pydantic.

        Этот текст попадает прямо в форму входа, и «String should have at least
        1 character» в ней читать некому.
        """
        response = await client.post('/auth/login', json=payload)

        assert response.status_code == 422
        detail = response.json()['detail']
        assert isinstance(detail, str), 'ошибка пришла не строкой, а структурой'
        assert expected in detail.lower()

    async def test_api_closed_without_login(self, client):
        """Клиенту API отвечаем 401, а не редиректом на форму.

        Редирект на HTML сказал бы «всё хорошо, вот вам страница входа», и клиент
        разобрал бы её как ответ.
        """
        response = await client.get('/api/dating/profile')

        assert response.status_code == 401

    async def test_page_redirects_without_login(self, client):
        """Браузеру — редирект на вход, с возвратом туда, куда шли."""
        response = await client.get('/', headers={'Accept': 'text/html'})

        assert response.status_code == 302
        assert '/login' in response.headers['location']

    async def test_logout_closes_session(self, auth_client):
        """После выхода прежняя сессия больше не работает."""
        assert (await auth_client.get('/auth/me')).status_code == 200

        response = await auth_client.post('/auth/logout')
        assert response.status_code == 200

        assert (await auth_client.get('/auth/me')).status_code == 401

    async def test_logout_twice_is_not_an_error(self, auth_client):
        """Повторный выход не ошибка: результат тот же — сессии нет."""
        assert (await auth_client.post('/auth/logout')).status_code == 200
        assert (await auth_client.post('/auth/logout')).status_code == 200
