"""Ручное письмо из «Фавориты → Диалоги» — ответом на его письмо, если оно ждёт.

## ⚠⚠ Что чинится

Кнопка «Отправить» под разговором уносила текст **новым письмом** ручкой API, а
его письмо оставалось в панели агентства «Unanswered»: панель считает отвеченным
только ответ своей формой «Reply» — так отвечает агент (`mail_reply_panel.py`).
Правило владельца 28.11.2026: сперва неотвеченное письмо в панели, и только если
его нет — прежняя дорога.

⚠ Ни одно письмо наружу не уходит: панель подменена, отправка — тоже.
"""
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import mail_reply_manual as manual
from src.dating.mail_send import DatingMailSendError

pytestmark = pytest.mark.mail


def _page(message_id: int) -> str:
    """Страница письма в панели с открытой формой ответа."""
    return (f'<form><input type="hidden" name="MessageId" value="{message_id}">'
            '<textarea name="ToBody"></textarea>'
            '<button name="buttonReply" value="Reply">Reply</button></form>')


async def _incoming(profile_id: int, uid: str, message_id: int,
                    days_ago: int = 0) -> int:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in (site_id, profile_id, message_id, uid, name, body,'
            ' date_post) VALUES ((SELECT site_id FROM profile WHERE id = %s),'
            " %s, %s, %s, 'Loren', 'Hi beautiful', NOW() - INTERVAL %s DAY)",
            (profile_id, profile_id, message_id, uid, days_ago))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _site(profile_id: int) -> int:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT site_id FROM profile WHERE id = %s', (profile_id,))

        return int((await db.fetchone())['site_id'])


@pytest.fixture
def panel(monkeypatch):
    """Панель: `open` — номера писем, на которые форма ответа ещё открыта."""
    state = {'due': True, 'open': set(), 'asked': [], 'down': False}

    async def due(site_id, profile_id, uid):
        return state['due']

    async def fetch(site_id, path, *args, **kwargs):
        if state['down']:
            raise manual.DatingPanelFetchError('панель не отвечает')
        number = int(str(path).rsplit('/', 1)[-1])
        state['asked'].append(number)

        return _page(number) if number in state['open'] else '<p>answered</p>'

    monkeypatch.setattr(manual, 'mail_reply_panel_due', due)
    monkeypatch.setattr(manual, 'panel_fetch_html', fetch)

    return state


class TestTheTargetIsAskedFromThePanel:
    async def test_an_open_letter_is_the_target(self, girl, panel):
        uid = f'm{uuid.uuid4().hex[:8]}'
        number = int(uuid.uuid4().int % 900_000_000) + 1
        letter = await _incoming(girl, uid, number)
        panel['open'].add(number)

        got = await manual.mail_reply_manual_target(await _site(girl), girl, uid)

        assert got and int(got['id']) == letter

    async def test_the_newest_open_letter_wins(self, girl, panel):
        uid = f'm{uuid.uuid4().hex[:8]}'
        old_no, new_no = (int(uuid.uuid4().int % 900_000_000) + 1 for _ in range(2))
        await _incoming(girl, uid, old_no, days_ago=3)
        fresh = await _incoming(girl, uid, new_no)
        panel['open'] |= {old_no, new_no}

        got = await manual.mail_reply_manual_target(await _site(girl), girl, uid)

        assert int(got['id']) == fresh

    async def test_answered_in_the_panel_means_no_target(self, girl, panel):
        """Форма закрыта — панель считает письмо отвеченным: ручной текст
        уходит прежней дорогой, письмом избранному."""
        uid = f'm{uuid.uuid4().hex[:8]}'
        number = int(uuid.uuid4().int % 900_000_000) + 1
        await _incoming(girl, uid, number)

        assert await manual.mail_reply_manual_target(await _site(girl), girl, uid) is None
        assert panel['asked'] == [number], 'панель не спросили — решили по флагу базы'

    async def test_a_site_without_panel_replies_keeps_the_old_road(self, girl, panel):
        uid = f'm{uuid.uuid4().hex[:8]}'
        number = int(uuid.uuid4().int % 900_000_000) + 1
        await _incoming(girl, uid, number)
        panel['open'].add(number)
        panel['due'] = False

        assert await manual.mail_reply_manual_target(await _site(girl), girl, uid) is None
        assert panel['asked'] == []

    async def test_a_silent_panel_stops_rather_than_guesses(self, girl, panel):
        """Панель не открылась — новым письмом наугад не шлём: это и есть беда."""
        uid = f'm{uuid.uuid4().hex[:8]}'
        await _incoming(girl, uid, int(uuid.uuid4().int % 900_000_000) + 1)
        panel['down'] = True

        with pytest.raises(DatingMailSendError):
            await manual.mail_reply_manual_target(await _site(girl), girl, uid)


class TestThePutReplacesTheAgentDraft:
    async def test_the_manual_reply_takes_the_letter(self, girl, panel):
        uid = f'm{uuid.uuid4().hex[:8]}'
        number = int(uuid.uuid4().int % 900_000_000) + 1
        letter = await _incoming(girl, uid, number)
        site_id = await _site(girl)
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid, name,'
                " body, status, message_id) VALUES (%s, %s, %s, %s, 'Loren',"
                " 'agent text', 'draft', 777)", (site_id, girl, letter, uid))
            await db.connection.commit()
        panel['open'].add(number)

        target = await manual.mail_reply_manual_target(site_id, girl, uid)
        got = await manual.mail_reply_manual_put(site_id, girl, target, 'my own words')

        assert got['reply_to'] == number, 'ответ уйдёт не на то письмо панели'
        assert got['message_id'] is None, 'ручному тексту приписан обмен агента'
        async with mysql_get_db_async() as db:
            await db.execute('SELECT body, status FROM mail_reply WHERE mail_in_id = %s',
                             (letter,))
            rows = await db.fetchall()
        assert [(r['body'], r['status']) for r in rows] == [('my own words', 'draft')]


class TestTheButtonTakesTheReplyRoad:
    """Кнопка «Отправить»: есть ждущее письмо — уходит **ответом** (`mail_reply`)."""

    async def _call(self, monkeypatch, girl, target, gallery=0):
        from fastapi import HTTPException  # noqa: F401
        from src.uvicorn.routers.dating import favorite_mail as route

        sent, history = [], []

        async def fake_target(site_id, profile_id, uid):
            return target

        async def fake_send(letter, profile, base, password, table='mailing'):
            sent.append((table, letter['reply_to'], letter['body']))
            return {'sent': True}

        async def fake_history(letter, table):
            history.append(table)

        monkeypatch.setattr(route, 'mail_reply_manual_target', fake_target)
        monkeypatch.setattr(route, 'mail_send_one', fake_send)
        monkeypatch.setattr(route, 'mail_send_history', fake_history)

        payload = route.DatingFavoriteMailReply(profile_id=girl, uid=target['uid'] if target else 'x',
                                                body='my own words', gallery_file_id=gallery)

        return await route.favorite_mail_reply(payload, {'login': 'test'}), sent, history

    async def test_an_unanswered_letter_gets_a_reply(self, girl, monkeypatch):
        uid = f'm{uuid.uuid4().hex[:8]}'
        number = int(uuid.uuid4().int % 900_000_000) + 1
        letter = await _incoming(girl, uid, number)

        got, sent, history = await self._call(
            monkeypatch, girl,
            {'id': letter, 'uid': uid, 'name': 'Loren', 'message_id': number, 'reply_id': None})

        assert got['status'] == 'sent' and got['kind'] == 'reply', got
        assert sent == [('mail_reply', number, 'my own words')], \
            'ручной текст ушёл не ответом на его письмо'
        assert history == ['mail_reply'], 'агент не узнает, что ему ответили руками'

    async def test_a_photo_stops_the_panel_reply(self, girl, monkeypatch):
        from fastapi import HTTPException

        uid = f'm{uuid.uuid4().hex[:8]}'
        number = int(uuid.uuid4().int % 900_000_000) + 1
        letter = await _incoming(girl, uid, number)

        with pytest.raises(HTTPException) as caught:
            await self._call(monkeypatch, girl,
                             {'id': letter, 'uid': uid, 'name': 'Loren',
                              'message_id': number, 'reply_id': None}, gallery=5)

        assert caught.value.status_code == 409
        assert 'только текст' in str(caught.value.detail)
