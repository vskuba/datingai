"""Письмо приставания руками: форма на «Фаворитах → Приставание» кладёт его в пул.

## Что здесь стережётся

* письмо ложится **обычной строкой пула** (`draft`) — уносит его та же отправка;
* адресат — только **её избранный** и не закрывшийся от неё (`block`);
* снимок — тем же правилом, что у шаблона (`mail_shot_check`): своя анкета,
  `public`; без отметки свежести шаблонов (`last_shot_at`);
* ⚠⚠ анкета обязана быть в сети: черновик спящей стирает `pool_sweep`, и
  набранное руками пропало бы молча.
"""

import uuid

import pytest

from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.mail


async def _sql(text: str, args: tuple = ()):
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(text, tuple(args))
        rows = await db.fetchall()
        last = int(db.lastrowid or 0)
        await db.connection.commit()

    return [dict(row) for row in (rows or [])], last


async def _star(girl: int, uid: str) -> None:
    await _sql('INSERT INTO favorite (site_id, profile_id, uid, state)'
               " VALUES (%s, %s, %s, 'done')", (TEST_STAND_SITE, girl, uid))


async def _shot(girl: int, path: str) -> int:
    _, last = await _sql(
        'INSERT INTO gallery_file (profile_id, file_id, name, path, mime, kind)'
        " VALUES (%s, %s, 'm.png', %s, 'image/png', 'image')",
        (girl, uuid.uuid4().hex, path))

    return last


@pytest.fixture
async def online_girl(girl_make):
    girl = await girl_make(online=True)
    yield girl
    await _sql('DELETE FROM favorite WHERE profile_id = %s', (girl,))


async def test_letter_lands_in_pool_with_shot(online_girl):
    from src.dating.favorite_mail import favorite_mail_manual

    uid = f'man-{uuid.uuid4().hex[:8]}'
    await _star(online_girl, uid)
    shot = await _shot(online_girl, f'public/man-{uuid.uuid4().hex[:6]}.png')

    [mail_id] = await favorite_mail_manual(online_girl, [uid], ' Hello, I miss you ', shot)

    rows, _ = await _sql('SELECT status, uid, body, gallery_file_id, message_id'
                         ' FROM favorite_mail WHERE id = %s', (mail_id,))
    assert rows == [{'status': 'draft', 'uid': uid, 'body': 'Hello, I miss you',
                     'gallery_file_id': shot, 'message_id': None}]

    # Отметку свежести ставит выбор кадра шаблону, а не ручное письмо.
    got, _ = await _sql('SELECT last_shot_at FROM gallery_file WHERE id = %s', (shot,))
    assert got[0]['last_shot_at'] is None


async def test_letter_without_shot(online_girl):
    from src.dating.favorite_mail import favorite_mail_manual

    uid = f'man-{uuid.uuid4().hex[:8]}'
    await _star(online_girl, uid)

    [mail_id] = await favorite_mail_manual(online_girl, [uid], 'text only')
    rows, _ = await _sql('SELECT gallery_file_id FROM favorite_mail WHERE id = %s',
                         (mail_id,))
    assert rows[0]['gallery_file_id'] == 0


@pytest.mark.parametrize('case', ['stranger', 'blocked', 'intim', 'mark', 'empty'])
async def test_refusals(online_girl, case):
    from src.dating.favorite_mail import (DatingFavoriteMailManualError,
                                          favorite_mail_manual)

    uid = f'man-{uuid.uuid4().hex[:8]}'
    if case != 'stranger':
        await _star(online_girl, uid)
    if case == 'blocked':
        await _sql('INSERT INTO block (site_id, profile_id, uid) VALUES (%s, %s, %s)',
                   (TEST_STAND_SITE, online_girl, uid))

    shot = (await _shot(online_girl, f'intim/man-{uuid.uuid4().hex[:6]}.png')
            if case == 'intim' else 0)
    body = {'mark': 'Hi [[companion_name]]', 'empty': '   '}.get(case, 'Hi')

    with pytest.raises(DatingFavoriteMailManualError):
        await favorite_mail_manual(online_girl, [uid], body, shot)

    rows, _ = await _sql('SELECT id FROM favorite_mail WHERE profile_id = %s',
                         (online_girl,))
    assert rows == []


async def test_asleep_girl_refused(girl_make):
    """Черновик спящей стёр бы `pool_sweep` — отказ сразу, а не молчаливая потеря."""
    from src.dating.favorite_mail import (DatingFavoriteMailManualError,
                                          favorite_mail_manual)

    girl = await girl_make()
    uid = f'man-{uuid.uuid4().hex[:8]}'
    await _star(girl, uid)
    try:
        with pytest.raises(DatingFavoriteMailManualError, match='не в сети'):
            await favorite_mail_manual(girl, [uid], 'Hi')
    finally:
        await _sql('DELETE FROM favorite WHERE profile_id = %s', (girl,))


async def test_several_recipients_card_each(online_girl):
    """Каждому выбранному — своя карточка; повтор адресата схлопывается."""
    from src.dating.favorite_mail import favorite_mail_manual

    one, two = (f'man-{uuid.uuid4().hex[:8]}' for _ in range(2))
    await _star(online_girl, one)
    await _star(online_girl, two)

    ids = await favorite_mail_manual(online_girl, [one, two, one], 'Hi both')

    rows, _ = await _sql('SELECT uid, body FROM favorite_mail WHERE profile_id = %s'
                         ' ORDER BY id', (online_girl,))
    assert len(ids) == 2
    assert rows == [{'uid': one, 'body': 'Hi both'}, {'uid': two, 'body': 'Hi both'}]


async def test_one_stranger_spoils_all(online_girl):
    """⚠⚠ Всё или ничего: годный адресат рядом с чужим не получает письма."""
    from src.dating.favorite_mail import (DatingFavoriteMailManualError,
                                          favorite_mail_manual)

    uid = f'man-{uuid.uuid4().hex[:8]}'
    await _star(online_girl, uid)

    with pytest.raises(DatingFavoriteMailManualError, match='nobody'):
        await favorite_mail_manual(online_girl, [uid, 'nobody'], 'Hi')

    rows, _ = await _sql('SELECT id FROM favorite_mail WHERE profile_id = %s',
                         (online_girl,))
    assert rows == []


async def test_routes(auth_client, online_girl):
    uid = f'man-{uuid.uuid4().hex[:8]}'
    await _star(online_girl, uid)

    ok = await auth_client.post('/api/dating/favorite-mail/manual', json={
        'profile_id': online_girl, 'uids': [uid], 'body': 'Hi there'})
    assert ok.status_code == 200, ok.text
    assert [one['status'] for one in ok.json()['result']] == ['draft']

    bad = await auth_client.post('/api/dating/favorite-mail/manual', json={
        'profile_id': online_girl, 'uids': ['nobody'], 'body': 'Hi'})
    assert bad.status_code == 409

    none = await auth_client.post('/api/dating/favorite-mail/manual', json={
        'profile_id': online_girl, 'uids': [], 'body': 'Hi'})
    assert none.status_code == 422
