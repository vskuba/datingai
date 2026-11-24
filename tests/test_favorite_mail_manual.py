"""Письмо приставания руками: форма на «Фаворитах → Приставание» кладёт его в пул.

## Что здесь стережётся

* письмо ложится **обычной строкой пула** (`draft`) — уносит его та же отправка;
* адресат — только **её избранный** и не закрывшийся от неё (`block`);
* ⚠⚠ отказ **называет каждого по имени и причине** — страница по номерам из
  двухсот человек в ленте своего не найдёт;
* список «кому ляжет, а кому нет» (`GET /recipients`) — **тот же** ответ, каким
  отказывает ручка: на странице своего признака нет;
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


@pytest.fixture
async def someone(online_girl):
    """Избранник с именем: звезда, состояние и запрет — доводом.

    Убирает за собой **все три** строки. Анкета стенда тест переживает, а
    `companion` и `block` уборкой по анкете не находятся: у первой нет
    `profile_id`, у второй уборка идёт по сайту.
    """
    made: list[str] = []

    async def add(uid: str, name: str, *, state: str = 'done',
                  closed: bool = False) -> str:
        await _sql('INSERT INTO companion (site_id, uid, name, photo)'
                   ' VALUES (%s, %s, %s, %s)', (TEST_STAND_SITE, uid, name, ''))
        await _sql('INSERT INTO favorite (site_id, profile_id, uid, state)'
                   ' VALUES (%s, %s, %s, %s)', (TEST_STAND_SITE, online_girl, uid, state))
        if closed:
            await _sql('INSERT INTO block (site_id, profile_id, uid)'
                       ' VALUES (%s, %s, %s)', (TEST_STAND_SITE, online_girl, uid))
        made.append(uid)

        return uid

    yield add

    if made:
        marks = ', '.join(['%s'] * len(made))
        await _sql(f'DELETE FROM companion WHERE site_id = %s AND uid IN ({marks})',
                   (TEST_STAND_SITE, *made))
        await _sql(f'DELETE FROM favorite WHERE profile_id = %s AND uid IN ({marks})',
                   (online_girl, *made))
        await _sql(f'DELETE FROM block WHERE profile_id = %s AND uid IN ({marks})',
                   (online_girl, *made))


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


async def test_mailable_verdict(online_girl, someone):
    """⚠⚠ Кому ляжет и почему нет — одним списком: он же и отказ, и подсказка.

    Тот же ответ отдаёт `POST /manual`, поэтому страница показывает решённое
    здесь, а не сочиняет признак второй раз.
    """
    from src.dating.favorite_mail import (FAVORITE_MAIL_WHY_BLOCK,
                                          favorite_mail_mailable)

    closed = await someone(f'man-{uuid.uuid4().hex[:8]}', 'Benoit', closed=True)
    star = await someone(f'man-{uuid.uuid4().hex[:8]}', 'Harry')
    already = await someone(f'man-{uuid.uuid4().hex[:8]}', 'Otto', state='already')

    rows = await favorite_mail_mailable(online_girl)
    got = {one['uid']: one for one in rows}

    assert set(got) == {closed, star, already}
    assert [one['name'] for one in rows] == ['Benoit', 'Harry', 'Otto']

    assert got[closed]['mailable'] is False
    assert got[closed]['why'] == FAVORITE_MAIL_WHY_BLOCK

    # ⚠ `already` — «звезда стояла у сайта, мы её только переписали»: по тому же
    # набору (`FAVORITE_STATES`) пул набирает круг, и отказать ему здесь значило
    # бы запретить то, что уходит каждый день.
    assert got[star]['mailable'] is True and got[star]['why'] == ''
    assert got[already]['mailable'] is True and got[already]['why'] == ''


async def test_refusal_names_every_one(online_girl, someone):
    """⚠⚠ Отказ называет **каждого** по имени и причине — живой случай 11 ноября.

    Из пяти выбранных по ленте двое были закрыты, письмо — всё или ничего — не
    легло никому, а в отказе стояли два номера, которые человек ещё должен был
    найти в двухстах именах ленты.
    """
    from src.dating.favorite_mail import (DatingFavoriteMailManualError,
                                          FAVORITE_MAIL_WHY_BLOCK,
                                          FAVORITE_MAIL_WHY_FAVORITE,
                                          favorite_mail_manual)

    closed = await someone(f'man-{uuid.uuid4().hex[:8]}', 'Benoit', closed=True)
    good = await someone(f'man-{uuid.uuid4().hex[:8]}', 'Harry')

    with pytest.raises(DatingFavoriteMailManualError) as got:
        await favorite_mail_manual(online_girl, [good, closed, 'nobody'], 'Hi')

    words = str(got.value)
    assert f'Benoit — {FAVORITE_MAIL_WHY_BLOCK}' in words
    assert f'nobody — {FAVORITE_MAIL_WHY_FAVORITE}' in words
    assert 'письмо не легло никому' in words

    # ⚠ Имя, а не номер: карточка есть — показывать номер незачем.
    assert closed not in words

    rows, _ = await _sql('SELECT id FROM favorite_mail WHERE profile_id = %s',
                         (online_girl,))
    assert rows == []


async def test_routes(auth_client, online_girl, someone):
    uid = f'man-{uuid.uuid4().hex[:8]}'
    await _star(online_girl, uid)
    closed = await someone(f'man-{uuid.uuid4().hex[:8]}', 'Benoit', closed=True)

    ok = await auth_client.post('/api/dating/favorite-mail/manual', json={
        'profile_id': online_girl, 'uids': [uid], 'body': 'Hi there'})
    assert ok.status_code == 200, ok.text
    assert [one['status'] for one in ok.json()['result']] == ['draft']

    bad = await auth_client.post('/api/dating/favorite-mail/manual', json={
        'profile_id': online_girl, 'uids': ['nobody'], 'body': 'Hi'})
    assert bad.status_code == 409
    assert 'nobody — не в избранных' in bad.json()['detail']

    none = await auth_client.post('/api/dating/favorite-mail/manual', json={
        'profile_id': online_girl, 'uids': [], 'body': 'Hi'})
    assert none.status_code == 422

    # Подбор адресатов: годные и негодные одним списком, с причиной.
    got = await auth_client.get('/api/dating/favorite-mail/recipients',
                                params={'profile_id': online_girl})
    assert got.status_code == 200, got.text
    rows = {one['uid']: one for one in got.json()['result']}

    # ⚠ Поля вне `response_model` отбрасываются молча — страница прочла бы
    # `undefined` вместо причины.
    assert set(rows[uid]) == {'uid', 'name', 'photo', 'mailable', 'why'}
    assert rows[uid]['mailable'] is True
    assert rows[closed]['mailable'] is False
    assert rows[closed]['why'] == 'закрылся от неё'

    # Анкета не выбрана — спрашивать не о ком.
    empty = await auth_client.get('/api/dating/favorite-mail/recipients')
    assert empty.status_code == 200 and empty.json() == {'result': []}
