"""Учётки анкет: заведение, правка, удаление и что наружу не отдаётся.

⚠ **Ручки зовутся в процессе, а не по HTTP.** Заведение и правка сверяют сайт у
соседа (`dating_profile_site`), и по HTTP это означало бы два запроса к RhapsodyAi
на каждую заведённую учётку — то есть сюиту, падающую от его перезапуска. Сосед
здесь поддельный (`rhapsody_fake.py`), а **база настоящая**: проверяется как раз
она — что строка легла, что пароль не уехал наружу, что удаление уносит целиком.

По HTTP остаётся то, что решается до входа в обработчик: вход и 404 на
несуществующую учётку.
"""

import uuid

import pytest
from fastapi import HTTPException

from src.uvicorn.routers.dating.dating_profile import (DatingProfileSave,
                                                       dating_profile_create,
                                                       dating_profile_delete,
                                                       dating_profile_list,
                                                       dating_profile_update)
from src.uvicorn.routers.dating.dating_profile_action import (dating_profile_action_login,
                                                              dating_profile_action_ping)

# Сайт, к которому привязываются тестовые учётки: он есть у поддельного соседа.
SITE_ID = 343

# Заведомо отсутствующий сайт: у соседа такого номера нет, и он отвечает 404.
SITE_ID_MISSING = 999_999


@pytest.fixture
async def profile(rhapsody):
    """Одноразовая учётка в живой базе. Удаляется после теста, даже если тест упал."""
    login = f'test-{uuid.uuid4().hex[:10]}'
    profile_id = await _create(login, password='secret', note='автотест')

    yield {'id': profile_id, 'login': login}

    try:
        await dating_profile_delete(profile_id, user=None)
    except HTTPException:
        # Тест мог удалить её сам — это его право, а не повод краснеть на уборке.
        pass


class TestProfileCrud:

    async def test_create_and_list(self, rhapsody, profile):
        """Заведённая учётка видна в списке своего сайта."""
        assert profile['login'] in [row['login'] for row in await _rows()]

    async def test_password_never_leaves(self, rhapsody, profile):
        """Пароля в ответе нет — только признак, что он задан.

        Страница его не показывает, а утечь через журнал, скриншот или чужой глаз
        он не должен.
        """
        row = await _row(profile['login'])

        assert 'password' not in row
        assert row['has_password'] is True

    async def test_new_profile_is_not_watched(self, rhapsody, profile):
        """Только что заведённая анкета под присмотр не берётся.

        Присмотр ставит успешный вход: лезть в браузер под учёткой, которую ещё ни
        разу не проверял человек, система не должна.
        """
        row = await _row(profile['login'])

        assert row['keep_online'] is False
        assert row['revive_state'] == 'ok'
        assert row['relogin_next_at'] is None

    async def test_duplicate_login_rejected(self, rhapsody, profile):
        """Тот же логин на том же сайте — 409, а не второй такой же."""
        with pytest.raises(HTTPException) as refusal:
            await _create(profile['login'], password='x')

        assert refusal.value.status_code == 409
        assert profile['login'] in str(refusal.value.detail)

    async def test_unknown_site_rejected(self, rhapsody):
        """Сайта нет — 422. Проверяет его сосед, своей таблицы сайтов здесь нет."""
        with pytest.raises(HTTPException) as refusal:
            await _create(f'test-{uuid.uuid4().hex[:8]}', site_id=SITE_ID_MISSING)

        assert refusal.value.status_code == 422

    async def test_unreachable_neighbour_is_502(self, rhapsody):
        """⚠ Лежащий сосед и несуществующий сайт — разные вещи.

        «Нет такого сайта» — ошибка запроса (422), обрыв связи — 502. Смешай их, и
        панель отвечала бы «шлюз не отвечает» на опечатку в номере, а на лежащего
        соседа — «такого сайта нет», отправляя человека искать несуществующую
        причину. С живым соседом этот случай не проверить вовсе.
        """
        rhapsody.offline()

        with pytest.raises(HTTPException) as refusal:
            await _create(f'test-{uuid.uuid4().hex[:8]}')

        assert refusal.value.status_code == 502

    async def test_update_keeps_password_when_empty(self, rhapsody, profile):
        """Пустой пароль при правке означает «не трогать», а не «стереть».

        Форма его не показывает, и пустое поле — это «оставить прежний».
        """
        await _update(profile['id'], profile['login'], password='', note='правка')
        row = await _row(profile['login'])

        assert row['has_password'] is True, 'пустое поле стёрло пароль'
        assert row['note'] == 'правка'

    async def test_role_defaults_to_lady_and_saves(self, rhapsody, profile):
        """Роль отдаётся наружу и правится. Форма заведения её не спрашивает —
        значит по умолчанию женская: анкеты заводят под неё.

        Роль решает, каким сценарием входить (мужской и женский кабинеты — разные
        приложения на одном домене), поэтому чужое значение обязано отлетать, а не
        лечь в базу: с ним учётке достался бы чужой сценарий, который честно упал
        бы по таймауту, потратив минуту.
        """
        assert (await _row(profile['login']))['role'] == 'lady'

        await _update(profile['id'], profile['login'], role='man')

        assert (await _row(profile['login']))['role'] == 'man'

    async def test_alien_role_never_reaches_the_base(self, rhapsody, profile):
        """Чужая роль отлетает на разборе тела, до всякой записи."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DatingProfileSave(site_id=SITE_ID, login=profile['login'], role='admin')

    async def test_delete(self, rhapsody):
        """Удаление уносит учётку целиком; повтор отвечает 404."""
        profile_id = await _create(f'test-{uuid.uuid4().hex[:10]}', password='x')

        await dating_profile_delete(profile_id, user=None)

        with pytest.raises(HTTPException) as refusal:
            await dating_profile_delete(profile_id, user=None)

        assert refusal.value.status_code == 404


class TestProfilePing:
    """Пинг: жив ли вход, прямо сейчас. В отличие от «Войти» браузера не трогает —
    идёт по HTTP с сохранёнными cookie, поэтому его и можно гонять в сюите."""

    async def test_ping_missing_profile_is_404(self, rhapsody):
        with pytest.raises(HTTPException) as refusal:
            await dating_profile_action_ping(999_999, user=None)

        assert refusal.value.status_code == 404

    async def test_ping_without_login_answers_offline(self, rhapsody, profile):
        """Учётка без снятого входа — честный `offline` и 200, а не отказ.

        «Сайт не признал вход» это **ответ**, а не сбой ручки: за ним человек и
        нажимает пинг, и превращать его в 4xx значило бы прятать рабочий исход в
        обработчик ошибок.

        ⚠ До живого сайта опрос при этом не доходит: без cookie он отвечает раньше
        всякой сети.
        """
        result = (await dating_profile_action_ping(profile['id'], user=None))['result']

        assert result['status'] == 'offline'
        assert result['error'], 'причина обязана быть названа'

    async def test_failed_ping_keeps_last_seen(self, rhapsody, profile):
        """Неудачный пинг **не двигает** `last_seen_at`.

        Смысл поля — «когда сайт последний раз признал учётку», а не «когда мы
        стучались». Двигай его каждый опрос, и по колонке «Вход» живая анкета
        перестала бы отличаться от отвалившейся неделю назад.
        """
        await dating_profile_action_ping(profile['id'], user=None)

        assert (await _row(profile['login']))['last_seen_at'] is None, \
            'неудачный пинг подвинул время входа'

    async def test_site_without_presence_is_422(self, rhapsody, profile):
        """Сайт без настроенного опроса — 422 с тем, что чинить.

        Настройка живёт у соседа (`browser_site.presence`), и пинговать без неё
        нечем: молчаливый `offline` соврал бы, что анкета не в сети.
        """
        for site in rhapsody.sites:
            site['presence'] = {}

        with pytest.raises(HTTPException) as refusal:
            await dating_profile_action_ping(profile['id'], user=None)

        assert refusal.value.status_code == 422
        assert 'presence' in str(refusal.value.detail)


class TestProfileLogin:
    """Дверь входа. Сам вход живёт в `src/dating/dating_login.py` и здесь не гоняется:
    он водит браузер соседа и живой сайт знакомств, то есть стоит минуты и требует
    рабочей анкеты. Проверяется то, что ломается при переезде кода, — что ручка
    осталась на месте и доходит до модуля входа."""

    async def test_login_without_password_is_422(self, rhapsody):
        """Учётка без пароля — 422 внятной фразой, а не сбоем.

        Заодно это проверка, что ручка действительно зовёт модуль входа: фраза
        приходит оттуда, из `DatingLoginError`.
        """
        from src.uvicorn.routers.dating.dating_profile_action import DatingProfileLogin

        profile_id = await _create(f'test-{uuid.uuid4().hex[:10]}', password='')
        try:
            with pytest.raises(HTTPException) as refusal:
                await dating_profile_action_login(profile_id, DatingProfileLogin(),
                                                  user=None)

            assert refusal.value.status_code == 422
            assert 'пароль' in str(refusal.value.detail)
        finally:
            await dating_profile_delete(profile_id, user=None)


class TestProfileRoutes:
    """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""

    async def test_missing_profile_is_404(self, auth_client):
        """Ручки несуществующей учётки отвечают 404, а не пустотой."""
        for path in ('log', 'message', 'profile'):
            response = await auth_client.get(f'/api/dating/profile/999999/{path}')

            assert response.status_code == 404, f'{path} ответил {response.status_code}'

    async def test_list_closed_without_session(self, client):
        assert (await client.get('/api/dating/profile')).status_code == 401

    async def test_ping_closed_without_session(self, client):
        """Без сессии не пускает: состояние чужой анкеты — не общее сведение."""
        assert (await client.post('/api/dating/profile/1/ping', json={})).status_code == 401

    async def test_login_closed_without_session(self, client):
        """Без сессии ручка входа не пускает: под чужой анкетой не входят."""
        assert (await client.post('/api/dating/profile/1/login', json={})).status_code == 401


async def _create(login: str, site_id: int = SITE_ID, password: str = '',
                  note: str = '') -> int:
    answer = await dating_profile_create(
        DatingProfileSave(site_id=site_id, login=login, password=password, note=note),
        user=None)

    return int(answer['result'])


async def _update(profile_id: int, login: str, **fields) -> None:
    await dating_profile_update(
        profile_id, DatingProfileSave(site_id=SITE_ID, login=login, **fields), user=None)


async def _rows(site_id: int = SITE_ID) -> list[dict]:
    return (await dating_profile_list(site_id, user=None))['result']


async def _row(login: str) -> dict:
    return next(row for row in await _rows() if row['login'] == login)
