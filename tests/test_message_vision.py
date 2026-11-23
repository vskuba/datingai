"""Снимки в чате — словами: агенту в вопрос и подписью в окне разговора.

Владелец 22.11.2026: «нужно добавить описание изображений, как это сделано на
LadaDate». Агент видел вместо снимка слово «[photo]» (`message_vision.py`).

⚠ Наружу сюита не ходит: зрение и скачивание подменяются.
"""

import json
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import message_vision as mv

pytestmark = pytest.mark.chat

SHOT = [{'thumb': 'https://img/t.jpg', 'src': 'https://img/s.jpg'}]


async def _his_photo(profile_id: int, uid: str, text: str = '[photo]') -> int:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, created_at, media)'
            " VALUES (%s, %s, 'Kenneth', %s, %s, 'in', NOW() - INTERVAL 1 MINUTE, %s)",
            (int(profile_id), uid, text, f'zz-{uuid.uuid4().hex[:12]}', json.dumps(SHOT)))
        await db.connection.commit()
        return int(db.lastrowid)


async def _media(message_id: int) -> list:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT media FROM message WHERE id = %s', (message_id,))
        return json.loads((await db.fetchone())['media'])


@pytest.fixture
def sees(monkeypatch):
    """Зрение отвечает одной фразой и считает вызовы."""
    calls = []

    async def _describe(agent, ask, item):
        calls.append(ask)
        return 'A man holding a fish on a boat.'

    async def _agent(profile_id):
        return 1

    monkeypatch.setattr(mv, '_describe', _describe)
    monkeypatch.setattr(mv, 'gallery_vision_agent', _agent)
    return calls


class TestTheLineForTheAgent:
    def test_a_bare_photo_becomes_its_description(self):
        media = [{**SHOT[0], 'about': 'A man on a boat.'}]

        assert mv.message_vision_line('[photo]', media) == '[photo: A man on a boat.]'

    def test_a_caption_keeps_its_words(self):
        media = json.dumps([{**SHOT[0], 'about': 'A sunset.'}])

        assert mv.message_vision_line('look', media) == 'look [photo: A sunset.]'

    def test_no_description_leaves_the_text(self):
        assert mv.message_vision_line('[photo]', SHOT) == '[photo]'
        assert mv.message_vision_line('hi', None) == 'hi'


class TestDescribing:
    async def test_his_photo_gets_described_once(self, girl, sees):
        shot = await _his_photo(girl, 'u-mv1')

        await mv.message_vision_run(profile_id=girl, uid='u-mv1')
        await mv.message_vision_run(profile_id=girl, uid='u-mv1')

        assert (await _media(shot))[0]['about'] == 'A man holding a fish on a boat.'
        assert len(sees) == 1, 'описанный снимок зрение второй раз не зовёт'
        assert 'A man sent this photo' in sees[0]

    async def test_failures_stop_after_the_limit(self, girl, monkeypatch):
        calls = []

        async def _fail(agent, ask, item):
            calls.append(1)
            raise mv.DatingMessageVisionError('снимок ответил 404')

        async def _agent(profile_id):
            return 1

        monkeypatch.setattr(mv, '_describe', _fail)
        monkeypatch.setattr(mv, 'gallery_vision_agent', _agent)
        shot = await _his_photo(girl, 'u-mv2')

        for _ in range(mv.MESSAGE_VISION_TRIES + 2):
            await mv.message_vision_run(profile_id=girl, uid='u-mv2')

        assert len(calls) == mv.MESSAGE_VISION_TRIES
        assert (await _media(shot))[0]['about_try'] == mv.MESSAGE_VISION_TRIES

    async def test_the_answer_question_carries_the_description(self, girl, sees):
        """Агент получает снимок словами, а не «[photo]»."""
        from src.dating.answer import _waiting_tail

        shot = await _his_photo(girl, 'u-mv3')
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM message WHERE id = %s', (shot,))
            row = dict(await db.fetchone())

        got = await _waiting_tail(girl, 'u-mv3', row)

        assert got == '[photo: A man holding a fish on a boat.]'


class TestOurShotIsDescribedByTheGallery:
    def test_the_sent_media_takes_the_file_description(self):
        from src.dating.send import _media_about

        got = _media_about(SHOT, {'raw': b'x', 'about': 'She smiles in a kitchen.'})

        assert got[0]['about'] == 'She smiles in a kitchen.'
        assert _media_about(SHOT, None) == SHOT

    async def test_our_described_shot_is_not_due(self, girl, sees):
        from src.dating.send import send_message_remember

        await send_message_remember(girl, 'u-mv4', 'look', uuid.uuid4().hex,
                                    media=[{**SHOT[0], 'about': 'She smiles.'}])

        assert await mv.message_vision_due(profile_id=girl, uid='u-mv4') == []
