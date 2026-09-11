"""Диалоги по всему сайту: `GET /api/dating/chat/dialogs`.

Вкладка «Чат» показывает разговоры **всего сайта**, а выбранная анкета работает
фильтром. Здесь проверяется то, что этим и решается: кто попадает в список, чем
подписан собеседник и откуда берётся его лицо.

⚠ **Ручку зовём в процессе, а не по HTTP**: она только читает базу, а разговор с
соседом ей не нужен вовсе. Анкеты заводятся на стенде (`STAND_SITE`), а не на
рабочем сайте — иначе их подхватили бы круги воркера (`test_mail_in.py`).
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating.dating_chat import dating_chat_dialogs

pytestmark = pytest.mark.chat
# Стенд у соседа: заведение анкеты сверяет номер, а круги воркера про него не
# знают — его нет в `dating_site`.
STAND_SITE = 999_343


async def _say(profile_id: int, uid: str, direction: str, text: str) -> None:
    """Реплика в переписке. Своей функцией: строк в тестах много, а полей у них
    восемь, и восьмой раз переписанный `INSERT` разошёлся бы с остальными.

    ⚠ **Номер реплики на сайте обязателен**, хотя в тестах он ничего не значит.
    У `dating_message` уникальный ключ `(profile_id, message_uid)`, а умолчание
    столбца — пустая строка: две реплики одной анкеты без номера сшибаются на нём
    (`Duplicate entry '21694-'`). Вторая реплика в разговоре — обычное дело, и без
    своего номера её не вставить вовсе.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_message'
            ' (profile_id, sender_uid, sender_name, message_uid, text, direction,'
            '  is_answered)'
            ' VALUES (%s, %s, %s, %s, %s, %s, 1)',
            (int(profile_id), str(uid), '', uuid.uuid4().hex, text, direction))
        await db.connection.commit()


@pytest.fixture
async def talk(auth_client):
    """Анкета с собеседником: наша реплика и его ответ.

    ⚠ **Реплики две, и вторая обязательна.** Список показывает только те пары,
    где есть входящая (`HAVING SUM(direction = 'in')`): разговор начинается с
    ответа. С одной исходящей строка в список не попала бы вовсе — и половина
    здешних проверок молча проверяла бы пустоту.

    Первая при этом остаётся исходящей: карточку собеседника заводит именно
    такой случай — мы написали, `dating_contact` не завёлся.
    """
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE, 'login': f'talk-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']
    uid = f'u{uuid.uuid4().hex[:8]}'

    await _say(made, uid, 'out', 'hi')
    await _say(made, uid, 'in', 'hello')

    yield {'profile_id': int(made), 'uid': uid}

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_companion WHERE site_id = %s AND uid = %s',
                         (STAND_SITE, uid))
        await db.connection.commit()

    await auth_client.delete(f'/api/dating/profile/{made}')


async def _rows(profile_id: int = 0) -> list[dict]:
    got = await dating_chat_dialogs(site_id=STAND_SITE, profile_id=profile_id,
                                    user=None)

    return got['result']['contacts']


async def _companion(uid: str, name: str, photo: str) -> None:
    """Карточка собеседника по сайту — то, что знает панель о человеке."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_companion (site_id, uid, name, photo)'
            ' VALUES (%s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE name = VALUES(name), photo = VALUES(photo)',
            (STAND_SITE, uid, name, photo))
        await db.connection.commit()


class TestCompanionCard:
    """⚠ Настоящая ошибка: собеседник без имени и без лица.

    В списке висели две строки «без имени» и с пустым квадратом вместо
    фотографии — при том, что и имя, и адрес фотографии лежали в базе рядом, в
    `dating_companion`.

    Запрос джойнил только `dating_contact` — собеседниц **этой анкеты**. Строки
    там нет у диалога, где говорили только мы: отправка кладёт реплику в
    `dating_message`, а контакт не заводит.

    ⚠ **Видно это было по странному признаку**: стоило выбрать анкету слева, и
    лица появлялись. Появлялись они не из базы — страница подставляла их из
    снимка списка `dialog`, а тот грузится только под выбранной анкетой. То есть
    данные брались из памяти браузера вместо той самой таблицы, где и лежат.
    """

    async def test_name_and_photo_come_from_the_companion_card(self, talk):
        await _companion(talk['uid'], 'Tall_Good_Man', 'https://site/photo.jpg')

        row = next(r for r in await _rows() if r['uid'] == talk['uid'])

        assert row['name'] == 'Tall_Good_Man', 'имя не подхватилось из карточки'
        assert row['photo'] == 'https://site/photo.jpg', 'фото не подхватилось'

    async def test_the_card_is_used_without_a_selected_profile(self, talk):
        """⚠ **Без фильтра — тот же ответ, что с фильтром.**

        Ровно это и было сломано: без выбранной анкеты лица пропадали, потому что
        подставляла их страница, а не запрос.
        """
        await _companion(talk['uid'], 'Will', 'https://site/will.jpg')

        без = next(r for r in await _rows() if r['uid'] == talk['uid'])
        с_фильтром = next(r for r in await _rows(talk['profile_id'])
                          if r['uid'] == talk['uid'])

        assert без['name'] == с_фильтром['name'] == 'Will'
        assert без['photo'] == с_фильтром['photo']

    async def test_contact_of_the_profile_wins_over_the_site_card(self, talk):
        """Контакт анкеты свежее: он приходит вместе с чатом, а карточка сайта
        помнит человека с первого раза."""
        await _companion(talk['uid'], 'Старое имя', 'https://site/old.jpg')

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO dating_contact (profile_id, uid, name, photo)'
                ' VALUES (%s, %s, %s, %s)',
                (talk['profile_id'], talk['uid'], 'Новое имя',
                 'https://site/new.jpg'))
            await db.connection.commit()

        row = next(r for r in await _rows() if r['uid'] == talk['uid'])

        assert row['name'] == 'Новое имя'
        assert row['photo'] == 'https://site/new.jpg'

    async def test_empty_contact_field_falls_through_to_the_card(self, talk):
        """⚠ Пустая строка — не значение, и `COALESCE` тут не годится.

        У `dating_contact.photo` пустая строка — обычное дело: контакт заводится
        по списку с сайта, где фотографии может не быть. Считай мы её значением,
        до карточки сайта запрос не дошёл бы никогда — а лицо там есть.
        """
        await _companion(talk['uid'], 'Semen', 'https://site/semen.jpg')

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO dating_contact (profile_id, uid, name, photo)'
                " VALUES (%s, %s, 'Semen', '')",
                (talk['profile_id'], talk['uid']))
            await db.connection.commit()

        row = next(r for r in await _rows() if r['uid'] == talk['uid'])

        assert row['photo'] == 'https://site/semen.jpg', 'пустое фото заслонило карточку'


class TestOnlyAnswered:
    """⚠ Диалог начинается с ответа: пара без входящих реплик — не диалог.

    Правило завелось по инвайтам. Ушедшая первая реплика ложится в
    `dating_message` обычной исходящей, и в списке становилась карточкой
    разговора из одного сообщения: из трёх строк настоящей была одна. Ушедшие
    инвайты не теряются — у них своя подвкладка «Доставка».
    """

    async def test_our_word_alone_is_not_a_dialog(self, auth_client):
        made = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'lone-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']
        uid = f'u{uuid.uuid4().hex[:8]}'
        try:
            await _say(made, uid, 'out', 'Hey Brian, how are you?')

            assert not [r for r in await _rows() if r['uid'] == uid], \
                'инвайт без ответа показан диалогом'
        finally:
            await auth_client.delete(f'/api/dating/profile/{made}')

    async def test_the_answer_makes_it_a_dialog(self, auth_client):
        """⚠ **Отвеченный инвайт — обычный диалог.** Правило отсекает молчание,
        а не первую реплику: пришёл ответ — разговор пошёл."""
        made = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'said-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']
        uid = f'u{uuid.uuid4().hex[:8]}'
        try:
            await _say(made, uid, 'out', 'Hey Brian, how are you?')
            assert not [r for r in await _rows() if r['uid'] == uid]

            await _say(made, uid, 'in', 'good, thanks')

            row = next(r for r in await _rows() if r['uid'] == uid)
            assert int(row['messages']) == 2, 'счёт реплик должен считать обе'
        finally:
            await auth_client.delete(f'/api/dating/profile/{made}')

    async def test_the_count_matches_the_list(self, talk):
        """⚠ Счёт и список отбирают одинаково: пейджер рисуется по счёту, и
        разойдись они — последняя страница выходила бы пустой."""
        got = await dating_chat_dialogs(site_id=STAND_SITE, profile_id=0, user=None)

        assert int(got['result']['contacts_total']) >= 1
        assert len(got['result']['contacts']) <= int(got['result']['contacts_total'])


class TestFilter:
    """Анкета — фильтр, а не условие показа."""

    async def test_without_a_profile_the_whole_site_is_listed(self, talk):
        assert any(r['uid'] == talk['uid'] for r in await _rows())

    async def test_another_profile_filters_the_talk_out(self, talk, auth_client):
        другая = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE, 'login': f'other-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']
        try:
            assert not [r for r in await _rows(int(другая))
                        if r['uid'] == talk['uid']]
        finally:
            await auth_client.delete(f'/api/dating/profile/{другая}')

    async def test_the_girl_side_comes_with_the_row(self, talk):
        """⚠ Сторона девушки едет **в строке**, а не берётся из выбора слева.

        Список идёт по всему сайту, и строки принадлежат разным анкетам: возьми
        страница выбранную, щелчок по чужой строке открыл бы окно под ней — то
        есть не тот разговор, а при отправке написал бы от не той девушки.
        """
        row = next(r for r in await _rows() if r['uid'] == talk['uid'])

        assert row['profile_id'] == talk['profile_id']
        assert 'profile_name' in row and 'profile_photo' in row
