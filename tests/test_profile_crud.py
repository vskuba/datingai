"""Учётки анкет: заведение, правка, удаление и что наружу не отдаётся."""

import uuid

import pytest

# Сайт, к которому привязываются тестовые учётки. Живёт он в RhapsodyAi, и
# проверка его существования — часть контракта: несуществующий даёт 422.
SITE_ID = 19

# Заведомо отсутствующий сайт: номер заведомо больше любого разумного.
SITE_ID_MISSING = 999999


@pytest.fixture
async def profile(auth_client):
    """Одноразовая учётка. Удаляется после теста, даже если тест упал."""
    login = f'test-{uuid.uuid4().hex[:10]}'
    response = await auth_client.post('/api/dating/profile', json={
        'site_id': SITE_ID, 'login': login, 'password': 'secret', 'note': 'автотест'})
    assert response.status_code == 201, response.text[:200]
    profile_id = response.json()['result']

    yield {'id': profile_id, 'login': login}

    await auth_client.delete(f'/api/dating/profile/{profile_id}')


class TestProfileCrud:

    async def test_create_and_list(self, auth_client, profile):
        """Заведённая учётка видна в списке своего сайта."""
        response = await auth_client.get(f'/api/dating/profile?site_id={SITE_ID}')

        assert response.status_code == 200
        logins = [row['login'] for row in response.json()['result']]
        assert profile['login'] in logins

    async def test_password_never_leaves(self, auth_client, profile):
        """Пароля в ответе нет — только признак, что он задан.

        Страница его не показывает, а утечь через журнал, скриншот или чужой глаз
        он не должен.
        """
        response = await auth_client.get(f'/api/dating/profile?site_id={SITE_ID}')
        row = next(r for r in response.json()['result'] if r['login'] == profile['login'])

        assert 'password' not in row
        assert row['has_password'] is True

    async def test_new_profile_is_not_watched(self, auth_client, profile):
        """Только что заведённая анкета под присмотр не берётся.

        Присмотр ставит успешный вход: лезть в браузер под учёткой, которую ещё ни
        разу не проверял человек, система не должна.
        """
        response = await auth_client.get(f'/api/dating/profile?site_id={SITE_ID}')
        row = next(r for r in response.json()['result'] if r['login'] == profile['login'])

        assert row['keep_online'] is False
        assert row['revive_state'] == 'ok'
        assert row['relogin_next_at'] is None

    async def test_duplicate_login_rejected(self, auth_client, profile):
        """Тот же логин на том же сайте — 409, а не второй такой же."""
        response = await auth_client.post('/api/dating/profile', json={
            'site_id': SITE_ID, 'login': profile['login'], 'password': 'x'})

        assert response.status_code == 409
        assert profile['login'] in response.json()['detail']

    async def test_unknown_site_rejected(self, auth_client):
        """Сайта нет — 422. Проверяет его сосед, своей таблицы сайтов здесь нет."""
        response = await auth_client.post('/api/dating/profile', json={
            'site_id': SITE_ID_MISSING, 'login': f'test-{uuid.uuid4().hex[:8]}',
            'password': 'x'})

        assert response.status_code == 422

    async def test_update_keeps_password_when_empty(self, auth_client, profile):
        """Пустой пароль при правке означает «не трогать», а не «стереть».

        Форма его не показывает, и пустое поле — это «оставить прежний».
        """
        response = await auth_client.put(f'/api/dating/profile/{profile["id"]}', json={
            'site_id': SITE_ID, 'login': profile['login'], 'password': '', 'note': 'правка'})
        assert response.status_code == 200

        rows = (await auth_client.get(f'/api/dating/profile?site_id={SITE_ID}')).json()['result']
        row = next(r for r in rows if r['login'] == profile['login'])

        assert row['has_password'] is True, 'пустое поле стёрло пароль'
        assert row['note'] == 'правка'

    async def test_role_defaults_to_lady_and_saves(self, auth_client, profile):
        """Роль отдаётся наружу и правится. Форма заведения её не спрашивает —
        значит по умолчанию женская: анкеты заводят под неё.

        Роль решает, каким сценарием входить (мужской и женский кабинеты — разные
        приложения на одном домене), поэтому чужое значение обязано отлетать, а не
        лечь в базу: с ним учётке достался бы чужой сценарий, который честно упал
        бы по таймауту, потратив минуту.
        """
        rows = (await auth_client.get(f'/api/dating/profile?site_id={SITE_ID}')).json()['result']
        assert next(r for r in rows if r['login'] == profile['login'])['role'] == 'lady'

        saved = await auth_client.put(f'/api/dating/profile/{profile["id"]}', json={
            'site_id': SITE_ID, 'login': profile['login'], 'role': 'man'})
        assert saved.status_code == 200

        rows = (await auth_client.get(f'/api/dating/profile?site_id={SITE_ID}')).json()['result']
        assert next(r for r in rows if r['login'] == profile['login'])['role'] == 'man'

        junk = await auth_client.put(f'/api/dating/profile/{profile["id"]}', json={
            'site_id': SITE_ID, 'login': profile['login'], 'role': 'admin'})
        assert junk.status_code == 422

    async def test_delete(self, auth_client):
        """Удаление уносит учётку целиком; повтор отвечает 404."""
        login = f'test-{uuid.uuid4().hex[:10]}'
        created = await auth_client.post('/api/dating/profile', json={
            'site_id': SITE_ID, 'login': login, 'password': 'x'})
        profile_id = created.json()['result']

        assert (await auth_client.delete(f'/api/dating/profile/{profile_id}')).status_code == 200
        assert (await auth_client.delete(f'/api/dating/profile/{profile_id}')).status_code == 404

    async def test_missing_profile_is_404(self, auth_client):
        """Ручки несуществующей учётки отвечают 404, а не пустотой."""
        for path in ('log', 'message', 'profile'):
            response = await auth_client.get(f'/api/dating/profile/999999/{path}')
            assert response.status_code == 404, f'{path} ответил {response.status_code}'


class TestProfilePing:
    """Пинг: жив ли вход, прямо сейчас. В отличие от «Войти» браузера не трогает —
    идёт по HTTP с сохранёнными cookie, поэтому его и можно гонять в сюите."""

    async def test_ping_closed_without_session(self, client):
        """Без сессии не пускает: состояние чужой анкеты — не общее сведение."""
        response = await client.post('/api/dating/profile/1/ping', json={})

        assert response.status_code == 401

    async def test_ping_missing_profile_is_404(self, auth_client):
        response = await auth_client.post('/api/dating/profile/999999/ping', json={})

        assert response.status_code == 404

    async def test_ping_without_login_answers_offline(self, auth_client, profile):
        """Учётка без снятого входа — честный `offline` и 200, а не отказ.

        «Сайт не признал вход» это **ответ**, а не сбой ручки: за ним человек и
        нажимает пинг, и превращать его в 4xx значило бы прятать рабочий исход в
        обработчик ошибок.
        """
        response = await auth_client.post(f'/api/dating/profile/{profile["id"]}/ping', json={})

        assert response.status_code == 200, response.text[:200]
        result = response.json()['result']
        assert result['status'] == 'offline'
        assert result['error'], 'причина обязана быть названа'

    async def test_failed_ping_keeps_last_seen(self, auth_client, profile):
        """Неудачный пинг **не двигает** `last_seen_at`.

        Смысл поля — «когда сайт последний раз признал учётку», а не «когда мы
        стучались». Двигай его каждый опрос, и по колонке «Вход» живая анкета
        перестала бы отличаться от отвалившейся неделю назад.
        """
        await auth_client.post(f'/api/dating/profile/{profile["id"]}/ping', json={})

        rows = (await auth_client.get(f'/api/dating/profile?site_id={SITE_ID}')).json()['result']
        row = next(r for r in rows if r['login'] == profile['login'])

        assert row['last_seen_at'] is None, 'неудачный пинг подвинул время входа'


class TestProfileLogin:
    """Дверь входа. Сам вход живёт в `src/dating/dating_login.py` и здесь не гоняется:
    он водит браузер соседа и живой сайт знакомств, то есть стоит минуты и требует
    рабочей анкеты. Проверяется то, что ломается при переезде кода, — что ручка
    осталась на месте, закрыта и доходит до модуля входа."""

    async def test_login_closed_without_session(self, client):
        """Без сессии ручка входа не пускает: под чужой анкетой не входят."""
        response = await client.post('/api/dating/profile/1/login', json={})

        assert response.status_code == 401

    async def test_login_without_password_is_422(self, auth_client):
        """Учётка без пароля — 422 внятной фразой, а не сбоем.

        Заодно это проверка, что ручка действительно зовёт модуль входа: фраза
        приходит оттуда, из `DatingLoginError`.
        """
        login = f'test-{uuid.uuid4().hex[:10]}'
        created = await auth_client.post('/api/dating/profile', json={
            'site_id': SITE_ID, 'login': login, 'password': ''})
        profile_id = created.json()['result']

        try:
            response = await auth_client.post(f'/api/dating/profile/{profile_id}/login', json={})

            assert response.status_code == 422, response.text[:200]
            assert 'пароль' in response.json()['detail']
        finally:
            await auth_client.delete(f'/api/dating/profile/{profile_id}')
