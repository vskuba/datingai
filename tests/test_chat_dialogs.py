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

from src.uvicorn.routers.dating.chat import chat_dialogs, chat_unread

pytestmark = pytest.mark.chat
# Стенд у соседа: заведение анкеты сверяет номер, а круги воркера про него не
# знают — его нет в `site_`.
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
            'INSERT INTO message'
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
        await db.execute('DELETE FROM companion WHERE site_id = %s AND uid = %s',
                         (STAND_SITE, uid))
        await db.connection.commit()

    await auth_client.delete(f'/api/dating/profile/{made}')


async def _rows(profile_id: int = 0) -> list[dict]:
    got = await chat_dialogs(site_id=STAND_SITE, profile_id=profile_id,
                                    user=None)

    return got['result']['contacts']


async def _companion(uid: str, name: str, photo: str) -> None:
    """Карточка собеседника по сайту — то, что знает панель о человеке."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO companion (site_id, uid, name, photo)'
            ' VALUES (%s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE name = VALUES(name), photo = VALUES(photo)',
            (STAND_SITE, uid, name, photo))
        await db.connection.commit()


class TestCompanionCard:
    """⚠ Настоящая ошибка: собеседник без имени и без лица.

    В списке висели две строки «без имени» и с пустым квадратом вместо
    фотографии — при том, что и имя, и адрес фотографии лежали в базе рядом, в
    `companion`.

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

        plain = next(r for r in await _rows() if r['uid'] == talk['uid'])
        filtered = next(r for r in await _rows(talk['profile_id'])
                        if r['uid'] == talk['uid'])

        assert plain['name'] == filtered['name'] == 'Will'
        assert plain['photo'] == filtered['photo']

    async def test_contact_of_the_profile_wins_over_the_site_card(self, talk):
        """Контакт анкеты свежее: он приходит вместе с чатом, а карточка сайта
        помнит человека с первого раза."""
        await _companion(talk['uid'], 'Старое имя', 'https://site/old.jpg')

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO contact (profile_id, uid, name, photo)'
                ' VALUES (%s, %s, %s, %s)',
                (talk['profile_id'], talk['uid'], 'Новое имя',
                 'https://site/new.jpg'))
            await db.connection.commit()

        row = next(r for r in await _rows() if r['uid'] == talk['uid'])

        assert row['name'] == 'Новое имя'
        assert row['photo'] == 'https://site/new.jpg'

    async def test_empty_contact_field_falls_through_to_the_card(self, talk):
        """⚠ Пустая строка — не значение, и `COALESCE` тут не годится.

        У `contact.photo` пустая строка — обычное дело: контакт заводится
        по списку с сайта, где фотографии может не быть. Считай мы её значением,
        до карточки сайта запрос не дошёл бы никогда — а лицо там есть.
        """
        await _companion(talk['uid'], 'Semen', 'https://site/semen.jpg')

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO contact (profile_id, uid, name, photo)'
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
        got = await chat_dialogs(site_id=STAND_SITE, profile_id=0, user=None)

        assert int(got['result']['contacts_total']) >= 1
        assert len(got['result']['contacts']) <= int(got['result']['contacts_total'])


class TestFilter:
    """Анкета — фильтр, а не условие показа."""

    async def test_without_a_profile_the_whole_site_is_listed(self, talk):
        assert any(r['uid'] == talk['uid'] for r in await _rows())

    async def test_another_profile_filters_the_talk_out(self, talk, auth_client):
        other = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE, 'login': f'other-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']
        try:
            assert not [r for r in await _rows(int(other))
                        if r['uid'] == talk['uid']]
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_the_girl_side_comes_with_the_row(self, talk):
        """⚠ Сторона девушки едет **в строке**, а не берётся из выбора слева.

        Список идёт по всему сайту, и строки принадлежат разным анкетам: возьми
        страница выбранную, щелчок по чужой строке открыл бы окно под ней — то
        есть не тот разговор, а при отправке написал бы от не той девушки.
        """
        row = next(r for r in await _rows() if r['uid'] == talk['uid'])

        assert row['profile_id'] == talk['profile_id']
        assert 'profile_name' in row and 'profile_photo' in row


class TestUnreadBadge:
    """⚠ Значок у вкладки и в строке анкеты: **реплики**, а не пары.

    Прежний счётчик «ждёт ответа» считал **пары** и почти всегда показывал «1»,
    прыгая даже на прочитанном. Значок считает **непрочитанные входящие**: три
    подряд от одного человека — «3». Отдельная ручка `GET /unread` несёт это по
    всем анкетам сайта разом — значок висит в списке анкет, и ходить в `/dialogs`
    за числом значило бы тянуть страницу диалогов.
    """

    async def test_dialogs_carry_unread_and_last_in(self, talk):
        """`/dialogs` несёт число непрочитанных и время последнего входящего."""
        got = await chat_dialogs(site_id=STAND_SITE, profile_id=0, user=None)

        assert int(got['result']['unread']) == 1, \
            'входящая реплика не посчитана непрочитанной'
        assert got['result']['last_in_at'], 'нет времени последнего входящего'

    async def test_unread_counts_three_incoming_as_three(self, talk):
        """Три подряд от одного человека — «3», а не «1» как у пар."""
        await _say(talk['profile_id'], talk['uid'], 'in', 'second')
        await _say(talk['profile_id'], talk['uid'], 'in', 'third')

        got = await chat_unread(site_id=STAND_SITE, user=None)
        entry = got['result'].get(str(talk['profile_id']))

        assert entry and int(entry['n']) == 3, \
            f'три непрочитанные реплики посчитаны как {entry}'

    async def test_unread_names_who_wrote_last(self, talk, auth_client):
        """⚠⚠ Значок несёт **от кого** последняя реплика, а не только «когда».

        По значку человек приходит к этой реплике, а список диалогов
        отсортирован по свежести **любой** строки, включая наши исходящие: у
        STELA последним шевелился разговор с Simon (наша реплика), а значок
        звал к письму Peter Pan — и открывался Simon. Приходилось жать второй
        раз.
        """
        other = f'u{uuid.uuid4().hex[:8]}'
        await _say(talk['profile_id'], other, 'in', 'я позже всех')
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE message SET created_at = NOW() + INTERVAL 1 MINUTE'
                ' WHERE profile_id = %s AND sender_uid = %s',
                (talk['profile_id'], other))
            await db.connection.commit()

        entry = (await chat_unread(site_id=STAND_SITE, user=None))['result'] \
            .get(str(talk['profile_id']))

        assert entry and entry.get('last_in_uid') == other, (
            'значок зовёт не к тому, кто написал последним: '
            f"{entry and entry.get('last_in_uid')} вместо {other}")

    async def test_the_same_second_breaks_by_row_not_by_name(self, talk):
        """⚠⚠ У `datetime` точность в секунду, а собеседник пишет очередями.

        Без разрыва ничьей по номеру строки две реплики одной секунды
        разрешались бы по алфавиту номера собеседника — значок звал бы к
        случайному из двоих.
        """
        early, late = 'u000000zz', 'u999999aa'   # алфавит и порядок спорят
        await _say(talk['profile_id'], late, 'in', 'первая')
        await _say(talk['profile_id'], early, 'in', 'вторая, она же последняя')
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE message SET created_at = NOW() + INTERVAL 1 MINUTE'
                ' WHERE profile_id = %s AND sender_uid IN (%s, %s)',
                (talk['profile_id'], early, late))
            await db.connection.commit()

        entry = (await chat_unread(site_id=STAND_SITE, user=None))['result'] \
            .get(str(talk['profile_id']))

        assert entry and entry.get('last_in_uid') == early, (
            'ничья одной секунды разрешилась по алфавиту, а не по порядку: '
            f"{entry and entry.get('last_in_uid')}")

    async def test_unread_is_empty_without_a_site(self):
        got = await chat_unread(site_id=0, user=None)

        assert got['result'] == {}

    async def test_outgoing_alone_does_not_answer(self, auth_client):
        """Исходящие в значок не попадают: он про **входящие**."""
        made = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE, 'login': f'out-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']
        uid = f'u{uuid.uuid4().hex[:8]}'
        try:
            await _say(made, uid, 'out', 'hello there')

            got = await chat_unread(site_id=STAND_SITE, user=None)

            assert str(made) not in got['result'], \
                'исходящая реплика завела строку в значок входящих'
        finally:
            await auth_client.delete(f'/api/dating/profile/{made}')

    async def test_read_reply_still_answers_with_time(self, talk):
        """⚠ Прочитанное не исчезает из снимка: счёт ноль, время живёт.

        После прочтения значок не гаснет, а пульсирует двенадцать часов от
        `last_in_at` — окно решает страница, сервер несёт время. Без строки с
        нулём и отметкой пульс было бы не от чего отсчитать.
        """
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE message SET is_read = 1'
                ' WHERE profile_id = %s AND sender_uid = %s AND direction = %s',
                (talk['profile_id'], talk['uid'], 'in'))
            await db.connection.commit()

        entry = (await chat_unread(site_id=STAND_SITE, user=None))['result'] \
            .get(str(talk['profile_id']))

        assert entry is not None, 'прочитанная анкета исчезла из снимка'
        assert int(entry['n']) == 0, 'прочитанное всё ещё считается непрочитанным'
        assert entry['last_in_at'], 'прочитанное потеряло отметку времени'
