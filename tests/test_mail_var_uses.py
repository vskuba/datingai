"""Где переменная участвует: галочки занятий на вкладке «Переменные».

Появилось из живого случая на проде. Шаблон требовал `companion_last_name`, а
тот по своему определению пуст у любого, чьё имя в одно слово — 44 собеседника
из 99. Письмо с незаполненной переменной не уходит, и рассылка молча пропускала
пары: пул стоял пустым при включённом расписании и живой девушке.

Шаблон правится руками один раз, а пишет их агент — и получает **весь реестр как
обязательный**, то есть вернул бы переменную в следующий же черновик. Галочка
убирает её из этого занятия.

⚠ Имена свои, с приставкой `zz_test_`: реестр общий на сайт, и тест, взявший
осмысленное имя, однажды затрёт живое. Приставка латиницей — имя переменной
проверяется (`dating_mail_var_check`), и кириллица в нём не проходит.
"""

import uuid

import pytest

from src.dating.dating_mail_agent import DATING_MAIL_VAR_USE_WRITE
from src.uvicorn.routers.dating.dating_mail_var import (DATING_MAIL_VAR_USES,
                                                        dating_mail_var_used)

pytestmark = pytest.mark.mail

SITE = 999_777


@pytest.fixture
async def var(auth_client):
    """Переменная стенда. Убирается за собой — реестр общий на сайт."""
    name = f'zz_test_{uuid.uuid4().hex[:8]}'
    made = (await auth_client.post('/api/dating/mail/var', json={
        'site_id': SITE, 'name': name, 'source': 'companion',
        'title': 'Проверочная'})).json()['result']

    yield made

    await auth_client.delete(f"/api/dating/mail/var/{made['id']}")


class TestDefault:
    """Умолчание — участвует везде."""

    def test_missing_key_means_used(self):
        """⚠ Занятие, добавленное завтра, включено у всех прежних переменных.

        Обратное умолчание выключило бы разом весь реестр на всех сайтах: в базе
        у старых строк ключа нет и взяться ему неоткуда.
        """
        assert dating_mail_var_used({}, DATING_MAIL_VAR_USE_WRITE) is True
        assert dating_mail_var_used({'uses': {}}, DATING_MAIL_VAR_USE_WRITE) is True

    def test_false_means_not_used(self):
        assert dating_mail_var_used({'uses': {DATING_MAIL_VAR_USE_WRITE: False}},
                                    DATING_MAIL_VAR_USE_WRITE) is False

    async def test_new_variable_takes_part_everywhere(self, var):
        """Заведённая без галочек участвует во всём: так было до их появления."""
        assert var['uses'] == {key: True for key, _, _ in DATING_MAIL_VAR_USES}


class TestSwitch:
    """Снятая галочка доезжает до базы и обратно."""

    async def test_switch_off_survives_a_round_trip(self, auth_client, var):
        await auth_client.put(f"/api/dating/mail/var/{var['id']}", json={
            'site_id': SITE, 'name': var['name'], 'source': var['source'],
            'title': var['title'], 'default_value': '',
            'uses': {DATING_MAIL_VAR_USE_WRITE: False}})

        rows = (await auth_client.get(
            f'/api/dating/mail/var?site_id={SITE}')).json()['result']
        got = next(row for row in rows if row['id'] == var['id'])

        assert got['uses'][DATING_MAIL_VAR_USE_WRITE] is False

    async def test_switch_back_on_clears_the_mark(self, auth_client, var):
        """Хранится только снятое: вернув галочку, строку возвращаем к умолчанию."""
        body = {'site_id': SITE, 'name': var['name'], 'source': var['source'],
                'title': var['title'], 'default_value': ''}

        await auth_client.put(f"/api/dating/mail/var/{var['id']}",
                              json={**body, 'uses': {DATING_MAIL_VAR_USE_WRITE: False}})
        await auth_client.put(f"/api/dating/mail/var/{var['id']}",
                              json={**body, 'uses': {DATING_MAIL_VAR_USE_WRITE: True}})

        rows = (await auth_client.get(
            f'/api/dating/mail/var?site_id={SITE}')).json()['result']
        got = next(row for row in rows if row['id'] == var['id'])

        assert got['uses'][DATING_MAIL_VAR_USE_WRITE] is True

    async def test_unknown_use_is_dropped(self, auth_client, var):
        """⚠ Ключ приходит из браузера, и незнакомый в базу не ложится: набор
        занятий — условие кода, а не то, что присылает страница."""
        await auth_client.put(f"/api/dating/mail/var/{var['id']}", json={
            'site_id': SITE, 'name': var['name'], 'source': var['source'],
            'title': var['title'], 'default_value': '',
            'uses': {'no_such_use': False}})

        rows = (await auth_client.get(
            f'/api/dating/mail/var?site_id={SITE}')).json()['result']
        got = next(row for row in rows if row['id'] == var['id'])

        assert 'no_such_use' not in got['uses']
        assert got['uses'][DATING_MAIL_VAR_USE_WRITE] is True


class TestRegistry:
    """Список занятий едет странице вместе с переменными."""

    async def test_uses_come_with_the_list(self, auth_client):
        """⚠ Одним ответом: два запроса на один взгляд означали бы карточку без
        галочек в тот миг, пока второй в пути."""
        answer = (await auth_client.get(f'/api/dating/mail/var?site_id={SITE}')).json()

        keys = [one['key'] for one in answer['uses']]

        assert DATING_MAIL_VAR_USE_WRITE in keys
        assert all(one['title'] for one in answer['uses']), 'занятию нужна подпись'

    async def test_uses_come_even_without_a_site(self, auth_client):
        """Без сайта переменных нет, а табличка рисуется — реестр от сайта не зависит."""
        answer = (await auth_client.get('/api/dating/mail/var?site_id=0')).json()

        assert answer['result'] == []
        assert [one['key'] for one in answer['uses']] == \
            [key for key, _, _ in DATING_MAIL_VAR_USES]


class TestAgentSees:
    """Отбор доезжает до агента, который пишет шаблоны."""

    async def test_switched_off_variable_is_not_offered(self, monkeypatch):
        """⚠ Отбор — внутри `dating_mail_write`, а не у вызывающих: их двое
        (кнопка и круг шаблонов), и один однажды забыл бы отфильтровать."""
        from src.dating import dating_mail_agent

        asked = {}

        async def _ask(**kwargs):
            asked.update(kwargs)

            return {'answer': '{"body": "Hi [[a]]", "title": "T"}'}

        monkeypatch.setattr(dating_mail_agent, 'rhapsody_api_chat_ask', _ask)
        monkeypatch.setattr(dating_mail_agent, '_agent_id', _answer(1))
        monkeypatch.setattr(dating_mail_agent, 'dating_mail_agent_language', _answer('en'))

        await dating_mail_agent.dating_mail_write(SITE, [
            {'name': 'a', 'title': 'нужная', 'source': 'profile', 'uses': {}},
            {'name': 'b', 'title': 'лишняя', 'source': 'profile',
             'uses': {DATING_MAIL_VAR_USE_WRITE: False}},
        ])

        names = asked['variables']['mail_var_names']
        must = asked['variables']['mail_var_must']

        assert names == 'a', f'выключенная переменная уехала агенту: {names}'
        assert must == 'a', f'выключенная переменная объявлена обязательной: {must}'


def _answer(value):
    async def _got(*args, **kwargs):
        return value

    return _got
