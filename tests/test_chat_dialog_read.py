"""Прочитанный разговор перестаёт быть новым, а «нужен ответ» снимает ответ.

## ⚠⚠ Два признака, и они про разное

`is_read` снимает **взгляд**: человек открыл разговор и увидел реплики.
`is_answered` снимает **ответ**: пока последнее слово за собеседником, строка
остаётся в работе, сколько на неё ни смотри.

Прежде выделение в списке рисовал `needs_answer`, и разговор оставался новым
после того, как его прочли: снять выделение было нечем, кроме ответа. Пометки
прочтения по паре не существовало вовсе — была только ручка «всё разом» у карточки
анкеты.

⚠ Порог свежести привезённого из панели проверяется здесь же: он переехал из
константы в настройку сайта, и ноль означает «без порога», а не «ничего не
свежее».
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'read-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _said(profile_id: int, uid: str, direction: str = 'in') -> None:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, is_read)'
            ' VALUES (%s, %s, %s, %s, %s, %s, 0)',
            (int(profile_id), uid, 'Someone', 'привет',
             f'zz-{uuid.uuid4().hex[:12]}', direction))
        await db.connection.commit()


async def _unread(profile_id: int, uid: str) -> int:
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT COUNT(*) AS n FROM message WHERE profile_id = %s'
            " AND sender_uid = %s AND direction = 'in' AND is_read = 0",
            (int(profile_id), uid))

        return int(((await db.fetchone()) or {}).get('n') or 0)


class TestMarkRead:
    """Ручка «разговор прочитан»."""

    async def test_it_marks_the_pair(self, auth_client, girl):
        await _said(girl, 'u-1')
        await _said(girl, 'u-1')

        answer = await auth_client.post(
            f'/api/dating/chat/dialogs/{girl}/u-1/read')

        assert answer.status_code == 200
        assert answer.json()['result']['read'] == 2
        assert await _unread(girl, 'u-1') == 0

    async def test_other_pairs_are_untouched(self, auth_client, girl):
        """⚠ По паре, а не по анкете: человек открыл один разговор и увидел один.

        Ручка `/profile/{id}/read` помечает всё разом — она для карточки анкеты,
        где виден весь список.
        """
        await _said(girl, 'u-1')
        await _said(girl, 'u-2')

        await auth_client.post(f'/api/dating/chat/dialogs/{girl}/u-1/read')

        assert await _unread(girl, 'u-1') == 0
        assert await _unread(girl, 'u-2') == 1, 'чужой разговор пометили за человека'

    async def test_our_own_replies_are_not_touched(self, auth_client, girl):
        """Исходящие «прочитанными» не бывают: их писали мы."""
        await _said(girl, 'u-1', direction='out')

        answer = await auth_client.post(
            f'/api/dating/chat/dialogs/{girl}/u-1/read')

        assert answer.json()['result']['read'] == 0

    async def test_twice_is_not_an_error(self, auth_client, girl):
        """Повторное открытие — обычное дело, и второй раз помечать нечего."""
        await _said(girl, 'u-1')
        await auth_client.post(f'/api/dating/chat/dialogs/{girl}/u-1/read')
        answer = await auth_client.post(
            f'/api/dating/chat/dialogs/{girl}/u-1/read')

        assert answer.json()['result']['read'] == 0


class TestDialogsTellUnread:
    """Список отдаёт непрочитанные отдельно от «нужен ответ»."""

    async def test_unread_comes_with_the_list(self, auth_client, girl):
        await _said(girl, 'u-1')

        got = (await auth_client.get(
            f'/api/dating/chat/dialogs?site_id={STAND_SITE}&profile_id={girl}')
        ).json()['result']

        row = next(one for one in got['contacts'] if one['uid'] == 'u-1')

        assert row['unread'] == 1
        assert row['needs_answer'] is True

    async def test_read_drops_unread_but_keeps_needs_answer(self, auth_client, girl):
        """⚠⚠ Вот ради чего всё: взгляд снимает «новое», но не «нужен ответ».

        Слей их в одно поле — и разговор либо оставался бы новым после прочтения,
        либо гас до того, как на него ответили.
        """
        await _said(girl, 'u-1')
        await auth_client.post(f'/api/dating/chat/dialogs/{girl}/u-1/read')

        got = (await auth_client.get(
            f'/api/dating/chat/dialogs?site_id={STAND_SITE}&profile_id={girl}')
        ).json()['result']

        row = next(one for one in got['contacts'] if one['uid'] == 'u-1')

        assert row['unread'] == 0, 'разговор остался новым после прочтения'
        assert row['needs_answer'] is True, 'взгляд снял «нужен ответ» — рано'


class TestPanelFreshSetting:
    """⚠ Порог свежести привезённого из панели — настройка сайта."""

    def test_it_is_in_the_registry(self):
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        one = SITE_KNOWN['chat_answer_panel_fresh']

        assert one['kind'] == 'number'
        assert one['default'] == '24', 'умолчание разошлось с прежней константой'
        assert 'max' not in one, 'потолок вернулся — срок задаёт тот, кто ведёт сайт'

    def test_zero_means_no_threshold(self):
        """⚠⚠ Ноль — «отвечать независимо от возраста», а не «никогда».

        Подставь мы ноль в `INTERVAL %s SECOND`, условие стало бы «создано в
        будущем», и панельное не отвечалось бы вовсе — ровно наоборот написанному
        в подсказке настройки.
        """
        import inspect

        from src.dating.answer import _waiting_find

        code = inspect.getsource(_waiting_find)

        assert 'panel_fresh <= 0' in code, 'ноль снова попадёт в запрос сроком'

    async def test_the_default_matches_the_constant(self):
        from src.dating.answer import ANSWER_PANEL_FRESH_SEC, _panel_fresh

        # Настройки у стенда нет — берём умолчание реестра, те же сутки.
        assert await _panel_fresh({'site_id': STAND_SITE}) == ANSWER_PANEL_FRESH_SEC
