"""Управление людьми: учётки, роли и токены доступа.

Страница «Настройки → Пользователи». Проверяется не разметка, а поведение ручек:
что заводится, что отвергается и — главное — чего сделать **нельзя**. Запреты
здесь не педантизм: каждый закрывает состояние, из которого через панель нет
выхода. Понизивший себя администратор теряет доступ к этой самой странице, и
вернуть роль ему будет некому.
"""

import uuid

import pytest

from tests.conftest import ADMIN_USERNAME

# Пароль тестовых учёток: длиннее минимума, чтобы проверялось правило, а не он.
PASSWORD = 'test-password-123'


def _name() -> str:
    """Уникальное имя в разрешённом алфавите: латиница, цифры, дефис."""
    return f'test-{uuid.uuid4().hex[:10]}'


@pytest.fixture
async def user(auth_client):
    """Одноразовая учётка оператора. Убирается после теста, даже если он упал."""
    username = _name()
    response = await auth_client.post('/api/users', json={
        'username': username, 'password': PASSWORD, 'role': 'dating'})
    assert response.status_code == 201, response.text[:200]
    created = response.json()['result']

    yield created

    await auth_client.delete(f'/api/users/{created["id"]}')


class TestUsersList:

    async def test_admin_sees_itself(self, auth_client):
        """Список показывает учётки с ролями — в нём есть и сам администратор."""
        response = await auth_client.get('/api/users')

        assert response.status_code == 200
        users = response.json()['result']['users']
        me = next(u for u in users if u['username'] == ADMIN_USERNAME)
        assert me['role'] == 'admin'

    async def test_password_hash_never_leaves(self, auth_client, user):
        """Хеша пароля в ответе нет.

        Он бесполезен для страницы и полезен тому, кто его утащит: bcrypt
        перебирается офлайн, без единого запроса к панели.
        """
        users = (await auth_client.get('/api/users')).json()['result']['users']
        row = next(u for u in users if u['username'] == user['username'])

        assert 'password_hash' not in row
        assert 'password' not in row


class TestUsersCreate:

    async def test_created_user_can_log_in(self, auth_client, user, client):
        """Заведённой учёткой действительно входят.

        Проверка сквозная: строка в базе, которой нельзя войти, — не учётка.
        """
        response = await client.post('/auth/login', json={
            'username': user['username'], 'password': PASSWORD})

        assert response.status_code == 200

    async def test_duplicate_name_is_refused(self, auth_client, user):
        """Имя занято — 422, а не вторая учётка с тем же логином."""
        response = await auth_client.post('/api/users', json={
            'username': user['username'], 'password': PASSWORD, 'role': 'dating'})

        assert response.status_code == 422

    async def test_short_password_is_refused(self, auth_client):
        """Короткий пароль не принимается: панель торчит в интернет."""
        response = await auth_client.post('/api/users', json={
            'username': _name(), 'password': 'x', 'role': 'dating'})

        assert response.status_code == 422

    @pytest.mark.parametrize('username', ['', 'a', 'вася', 'two words', 'x' * 51])
    async def test_bad_name_is_refused(self, auth_client, username):
        """Имя уходит в журналы и списки, поэтому без пробелов и юникода."""
        response = await auth_client.post('/api/users', json={
            'username': username, 'password': PASSWORD, 'role': 'dating'})

        assert response.status_code == 422

    async def test_unknown_role_is_refused(self, auth_client):
        """Роли ровно две. Опечатка в третьей не должна заводить бесправную учётку."""
        response = await auth_client.post('/api/users', json={
            'username': _name(), 'password': PASSWORD, 'role': 'superuser'})

        assert response.status_code == 422


class TestUsersUpdate:

    async def test_password_changes(self, auth_client, user, client):
        """Новый пароль работает, старый — нет."""
        response = await auth_client.put(f'/api/users/{user["id"]}',
                                         json={'password': 'another-password-456'})
        assert response.status_code == 200

        assert (await client.post('/auth/login', json={
            'username': user['username'], 'password': 'another-password-456'})).status_code == 200
        assert (await client.post('/auth/login', json={
            'username': user['username'], 'password': PASSWORD})).status_code == 401

    async def test_empty_password_means_untouched(self, auth_client, user, client):
        """Пустое поле — «не трогать», а не «поставить пустой пароль».

        Иначе смена одной роли молча обнуляла бы пароль, и человек узнавал бы об
        этом при следующем входе.
        """
        response = await auth_client.put(f'/api/users/{user["id"]}',
                                         json={'password': '', 'role': 'admin'})
        assert response.status_code == 200

        assert (await client.post('/auth/login', json={
            'username': user['username'], 'password': PASSWORD})).status_code == 200

    async def test_role_changes(self, auth_client, user):
        """Роль меняется и видна в списке."""
        await auth_client.put(f'/api/users/{user["id"]}', json={'role': 'admin'})

        users = (await auth_client.get('/api/users')).json()['result']['users']
        assert next(u for u in users if u['id'] == user['id'])['role'] == 'admin'

    async def test_short_password_is_refused(self, auth_client, user):
        """Правило длины одно и то же — и при заведении, и при смене."""
        response = await auth_client.put(f'/api/users/{user["id"]}', json={'password': 'x'})

        assert response.status_code == 422

    async def test_unknown_user_is_404(self, auth_client):
        response = await auth_client.put('/api/users/99999999', json={'role': 'admin'})

        assert response.status_code == 404


class TestUsersSelfProtection:
    """Запреты, ради которых страница вообще пишется аккуратно."""

    async def test_cannot_change_own_role(self, auth_client):
        """Понизив себя, администратор теряет доступ к этой самой странице.

        Вернуть роль было бы некому: страница ролей — админская. Меняет роль
        другой администратор, как везде, где роль можно потерять.
        """
        me = (await auth_client.get('/auth/me')).json()['result']

        response = await auth_client.put(f'/api/users/{me["id"]}', json={'role': 'dating'})

        assert response.status_code == 422

    async def test_cannot_delete_self(self, auth_client):
        """Человек остался бы без учётки в середине сеанса."""
        me = (await auth_client.get('/auth/me')).json()['result']

        response = await auth_client.delete(f'/api/users/{me["id"]}')

        assert response.status_code == 422

    async def test_own_password_change_is_allowed(self, auth_client):
        """Пароль себе менять можно — теряется только он, и знает его сам человек.

        Запрет тут был бы вредным: сменить свой пароль иначе негде.
        """
        me = (await auth_client.get('/auth/me')).json()['result']

        response = await auth_client.put(f'/api/users/{me["id"]}', json={'password': ''})

        assert response.status_code == 200


class TestUsersDelete:

    async def test_deleted_user_cannot_log_in(self, auth_client, client):
        """Удалённой учёткой не входят, и в списке её нет."""
        username = _name()
        created = (await auth_client.post('/api/users', json={
            'username': username, 'password': PASSWORD, 'role': 'dating'})).json()['result']

        assert (await auth_client.delete(f'/api/users/{created["id"]}')).status_code == 200

        users = (await auth_client.get('/api/users')).json()['result']['users']
        assert username not in [u['username'] for u in users]
        assert (await client.post('/auth/login', json={
            'username': username, 'password': PASSWORD})).status_code == 401

    async def test_unknown_user_is_404(self, auth_client):
        response = await auth_client.delete('/api/users/99999999')

        assert response.status_code == 404


class TestUsersToken:
    """Токены: ими ходят скрипты, у которых нет и не может быть сессии."""

    async def test_issued_token_works(self, auth_client, user, client):
        """Выданный токен пускает — и приходит с ролью своего владельца."""
        issued = (await auth_client.post(f'/api/users/{user["id"]}/token',
                                         json={'note': 'автотест'})).json()['result']

        response = await client.get('/auth/me',
                                    headers={'Authorization': f'Bearer {issued["token"]}'})

        assert response.status_code == 200
        assert response.json()['result']['username'] == user['username']

    async def test_full_token_is_shown_once(self, auth_client, user):
        """В списке — только хвост и пометка, целиком токен виден при выдаче.

        Иначе открытая страница показывала бы действующие ключи всем, кто стоит
        за спиной, а скриншот раздела настроек раздавал бы их насовсем.
        """
        issued = (await auth_client.post(f'/api/users/{user["id"]}/token',
                                         json={'note': 'автотест'})).json()['result']

        users = (await auth_client.get('/api/users')).json()['result']['users']
        row = next(u for u in users if u['id'] == user['id'])
        token = next(t for t in row['tokens'] if t['id'] == issued['id'])

        assert 'token' not in token
        assert token['note'] == 'автотест'
        assert issued['token'].endswith(token['tail'])

    async def test_revoked_token_stops_working(self, auth_client, user, client):
        """Отозванный токен перестаёт пускать сразу — он читается на каждый запрос."""
        issued = (await auth_client.post(f'/api/users/{user["id"]}/token',
                                         json={'note': 'автотест'})).json()['result']
        headers = {'Authorization': f'Bearer {issued["token"]}'}
        assert (await client.get('/auth/me', headers=headers)).status_code == 200

        assert (await auth_client.delete(f'/api/users/token/{issued["id"]}')).status_code == 200

        assert (await client.get('/auth/me', headers=headers)).status_code == 401

    async def test_tokens_die_with_their_user(self, auth_client, client):
        """Удалили учётку — её токены больше не пускают.

        Иначе выданный скрипту ключ пережил бы человека, которому принадлежал.
        """
        created = (await auth_client.post('/api/users', json={
            'username': _name(), 'password': PASSWORD, 'role': 'dating'})).json()['result']
        issued = (await auth_client.post(f'/api/users/{created["id"]}/token',
                                         json={'note': 'автотест'})).json()['result']
        headers = {'Authorization': f'Bearer {issued["token"]}'}
        assert (await client.get('/auth/me', headers=headers)).status_code == 200

        await auth_client.delete(f'/api/users/{created["id"]}')

        assert (await client.get('/auth/me', headers=headers)).status_code == 401

    async def test_token_for_unknown_user_is_404(self, auth_client):
        response = await auth_client.post('/api/users/99999999/token', json={'note': 'x'})

        assert response.status_code == 404
