"""Строка пула инвайтов несёт «в сети ли собеседник».

## ⚠⚠ Зачем

Пул рассылки рисовал точку у адресата, пул инвайтов — нет, хотя ему она нужнее:
письмо уходит кому угодно, а **реплика в чат — только человеку в сети**
(`invite_send_round`). Без точки карточка, которую круг обходит стороной, ничем не
отличалась от застрявшей, и оставалось гадать, что чинить.

Считает **сервер** тем же `online_show`, что рассылка и карточки собеседников: три
места отвечают на один вопрос, и своя копия правила разошлась бы с остальными на
первой же правке срока годности снимка.

⚠ **Полей два.** «Не в сети» и «не знаем» — разные ответы: снимок ленты мог
устареть, и серая точка на нём утверждала бы то, чего мы не проверяли. Страница на
`online_known = false` не рисует ничего.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating.invite import _answer

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


def _row(**more) -> dict:
    got = {'id': 1, 'site_id': STAND_SITE, 'profile_id': 1, 'uid': 'u-1',
           'name': 'Brian', 'photo': '', 'body': 'привет', 'status': 'draft',
           'error': '', 'sent_at': None, 'updated_at': None, 'created_at': None,
           'profile_card': '{}', 'profile_login': 'girl@example.com'}
    got.update(more)

    return got


class TestTheRowTellsOnline:
    """Два поля в каждой строке пула."""

    def test_both_fields_are_there(self, monkeypatch):
        monkeypatch.setattr('src.uvicorn.routers.dating.invite.online_show',
                            lambda site_id, uid: {'online': True, 'known': True})

        got = _answer(_row())

        assert got['online'] is True
        assert got['online_known'] is True

    def test_offline_is_known(self, monkeypatch):
        monkeypatch.setattr('src.uvicorn.routers.dating.invite.online_show',
                            lambda site_id, uid: {'online': False, 'known': True})

        got = _answer(_row())

        assert got['online'] is False
        assert got['online_known'] is True, 'знаем, что ушёл, — это не «не знаем»'

    def test_unknown_is_not_offline(self, monkeypatch):
        """⚠⚠ Ради чего поля два: молчание снимка — не «его нет»."""
        monkeypatch.setattr('src.uvicorn.routers.dating.invite.online_show',
                            lambda site_id, uid: {'online': False, 'known': False})

        got = _answer(_row())

        assert got['online_known'] is False

    def test_it_asks_about_this_person(self, monkeypatch):
        """Спрашиваем про **адресата** и его сайт, а не про анкету."""
        asked = []
        monkeypatch.setattr('src.uvicorn.routers.dating.invite.online_show',
                            lambda site_id, uid: asked.append((site_id, uid))
                            or {'online': True, 'known': True})

        _answer(_row(uid='u-42'))

        assert asked == [(STAND_SITE, 'u-42')]


class TestTheListCarriesIt:
    """Поля доезжают до страницы, а не остаются в помощнике."""

    async def test_the_pool_answers_with_them(self, auth_client, girl):
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO invite (site_id, profile_id, uid, name, body, status)'
                " VALUES (%s, %s, %s, %s, %s, 'draft')",
                (STAND_SITE, girl, f'u-{uuid.uuid4().hex[:8]}', 'Brian', 'привет'))
            await db.connection.commit()

        got = (await auth_client.get(
            f'/api/dating/invite?site_id={STAND_SITE}')).json()['result']
        mine = [one for one in got if int(one['profile_id']) == girl]

        assert mine, 'своей строки в пуле не нашли'
        assert 'online' in mine[0] and 'online_known' in mine[0]
