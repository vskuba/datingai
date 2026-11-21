"""Фото-подарок и снимки в чате Luxee.

Владелец 21.11.2026: девушке просто хочется прислать снимок — намерение
`she_just_send_photo`, после 2–3 его сообщений, 30%. «Можно ли» решаем мы
фактами (`photo_gift.py`), «хочется ли» — жребий соседа. Снимок уходит вместе
с ответом одним сообщением, а окно разговора показывает его превью.

⚠ Наружу сюита не ходит: сокет, Диск и драйвер подменяются.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import photo_gift as gift_mod
from src.dating.site import site_import_luxee as imp
from src.dating.site.site_socket_luxee import site_socket_luxee_media_item

pytestmark = pytest.mark.chat

LUXEE = {'driver': 'luxee'}


@pytest.fixture
def open_all(monkeypatch):
    """Всё разрешено, кроме того, что проверяет тест: флаг, пределы, медиа, галерея."""
    from src.dating.site.site_chat_luxee import DatingSiteChatLuxee

    async def _allowed(site_id):
        return True

    async def _seconds(site_id, key):
        return {'photo_gift_per_day': 2, 'photo_gift_gap_hours': 3}[key]

    async def _fresh(*args, **kwargs):
        return [{'id': 1, 'file_id': 'f-1'}]

    async def _open(self, *args):
        return True

    monkeypatch.setattr(gift_mod, 'chat_intention_do_allowed', _allowed)
    monkeypatch.setattr(gift_mod, 'site_seconds', _seconds)
    monkeypatch.setattr(gift_mod, 'companion_gallery_fresh', _fresh)
    monkeypatch.setattr(DatingSiteChatLuxee, 'chat_photo_open', _open)
    gift_mod._OPEN.clear()


async def _said(profile_id: int, uid: str, n: int) -> None:
    async with mysql_get_db_async() as db:
        for _ in range(n):
            await db.execute(
                'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
                '  message_uid, direction, created_at)'
                " VALUES (%s, %s, 'Mark', 'hi', %s, 'in', NOW() - INTERVAL 5 MINUTE)",
                (int(profile_id), uid, f'zz-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()


async def _profile(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s', (int(profile_id),))
        return dict(await db.fetchone())


class TestWhenAGiftIsAllowed:
    async def test_not_where_photos_cannot_go_to_chat(self, girl):
        assert not await gift_mod.photo_gift_due(await _profile(girl),
                                                 {'driver': 'ladadate'}, 'u-g')

    async def test_after_enough_of_his_messages(self, girl, open_all):
        await _said(girl, 'u-g3', 3)

        assert await gift_mod.photo_gift_due(await _profile(girl), LUXEE, 'u-g3')

    async def test_not_after_one(self, girl, open_all):
        await _said(girl, 'u-g1', 1)

        assert not await gift_mod.photo_gift_due(await _profile(girl), LUXEE, 'u-g1'), \
            'порог 2–3 его сообщения, а не одно'

    async def test_not_soon_after_a_photo(self, girl, open_all):
        await _said(girl, 'u-gp', 5)
        async with mysql_get_db_async() as db:
            await db.execute(
                "INSERT INTO gallery_file (profile_id, file_id, path, name, mime, kind)"
                " VALUES (%s, %s, 'photo/public/a.jpg', 'a.jpg', 'image/jpeg', 'image')",
                (girl, f'gift-{uuid.uuid4().hex[:10]}'))
            shot = int(db.lastrowid)
            await db.execute(
                'INSERT INTO companion_gallery_file (profile_id, site_id, uid, gallery_file_id, sent_at)'
                ' SELECT %s, site_id, %s, %s, NOW() - INTERVAL 1 HOUR FROM profile WHERE id = %s',
                (girl, 'u-gp', shot, girl))
            await db.connection.commit()

        assert not await gift_mod.photo_gift_due(await _profile(girl), LUXEE, 'u-gp'), \
            'пауза 3 часа от прошлого снимка'

    async def test_closed_media_means_no(self, girl, open_all, monkeypatch):
        from src.dating.site.site_chat_luxee import DatingSiteChatLuxee

        async def _closed(self, *args):
            return False

        monkeypatch.setattr(DatingSiteChatLuxee, 'chat_photo_open', _closed)
        await _said(girl, 'u-gc', 3)

        assert not await gift_mod.photo_gift_due(await _profile(girl), LUXEE, 'u-gc')


class TestTheGiftRidesWithTheAnswer:
    async def test_a_marked_exchange_gets_a_photo(self, girl, monkeypatch):
        async def _pick(*args, **kwargs):
            return {'file': {'id': 7, 'file_id': 'drive-7', 'name': 'sea.jpg'}}

        async def _bytes(file_id, width):
            return b'jpeg', 'image/jpeg'

        monkeypatch.setattr(gift_mod, 'companion_gallery_pick', _pick)
        monkeypatch.setattr(gift_mod, 'google_drive_bytes', _bytes)
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO chat_intention (site_id, profile_id, uid, intention, message_id,'
                "  context, status) SELECT site_id, id, 'u-at', %s, 424242,"
                """  '{"tags": "outdoor, smiling"}', 'waiting' FROM profile WHERE id = %s""",
                (gift_mod.PHOTO_GIFT_INTENTION, girl))
            await db.connection.commit()

        got = await gift_mod.photo_gift_attach(await _profile(girl), 424242, 'u-at')

        assert got['raw'] == b'jpeg' and got['file']['id'] == 7 and got['intention_id']

    async def test_an_unmarked_exchange_has_none(self, girl):
        assert await gift_mod.photo_gift_attach(await _profile(girl), 424243, 'u-at') == {}


class TestTheChatShowsThePhoto:
    def test_media_becomes_full_addresses(self):
        got = imp.site_import_luxee_media(
            {'media': {'a': {'thumbnail': '/thumb/x.jpg', 'src': '/src/x.jpg'}}})

        assert got == [{'thumb': 'https://img.luxee.date/thumb/x.jpg',
                        'src': 'https://img.luxee.date/src/x.jpg'}]

    def test_caption_without_the_photo_word(self):
        """Слово «[photo]» разводило подпись с нашей строкой — близнец двоился."""
        assert imp.site_import_luxee_text(
            {'body': 'hi***', 'bodyOrigin': 'hi...', 'media': [{'src': '/src/x'}]}) == 'hi...'
        assert imp.site_import_luxee_text({'body': '', 'media': [{'src': '/src/x'}]}) == '[photo]'

    def test_deleted_by_the_panel_is_skipped(self):
        message = {'author': {'uid': 5}, 'memberStatus': {'5': {'status': 4}}}

        assert imp.site_import_luxee_deleted(message)

    def test_the_media_item_uses_bare_names(self):
        """Путь целиком — `/thumb//thumb/…` и `403`: битая картинка у собеседника."""
        item = site_socket_luxee_media_item(
            {'entity_id': 'k', 'src': '/src/a.jpg', 'preview': '/preview/a.jpg',
             'thumbnail': '/thumb/a.jpg', 'uid': 1})

        assert item['isStorage'] is True and item['index'] == 'k'
        assert {item['file'][k] for k in ('src', 'preview', 'thumbnail')} == {'a.jpg'}

    async def test_the_window_gets_media(self, girl):
        from src.dating.send import send_message_remember
        from src.uvicorn.routers.dating.profile_view import profile_view_messages

        shot = [{'thumb': 'https://img/t.jpg', 'src': 'https://img/s.jpg'}]
        await send_message_remember(girl, 'u-med', 'look', uuid.uuid4().hex, media=shot)

        got = (await profile_view_messages(girl, uid='u-med', user=None))['result']

        assert got[0]['media'] == shot


class TestOnlyThePanelSocketCarriesPhotos:
    async def test_other_transports_refuse_without_sending(self, girl, monkeypatch):
        from src.dating import send as send_mod

        async def _transport(site_id, key):
            return 'http'

        async def _never(*args, **kwargs):
            raise AssertionError('текстом молча уйти не должно')

        monkeypatch.setattr(send_mod, 'site_setting', _transport)
        monkeypatch.setattr(send_mod, '_send_http', _never)
        monkeypatch.setattr(send_mod, 'online_check', lambda s, u: {'online': True})

        got = await send_mod.send_message(await _profile(girl), {'send': {}}, 'u-x', 'hi',
                                          photo={'raw': b'x', 'kind': 'image/jpeg'})

        assert got['ok'] is False and got['no_media'] is True
