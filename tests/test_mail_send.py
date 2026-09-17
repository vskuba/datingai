"""Отправка письма через API сайта: два шага, и оба обязательны.

⚠⚠ **Наружу не уходит ни одно письмо, и это здесь главное.** `POST /messages/send`
на живом сайте — действие необратимое: письмо получает живой человек, отменить его
нечем. Поэтому весь разговор с сайтом идёт через заглушку (`site_api_fake.py`),
которая подменяет **транспорт** — `httpx` внутри `dating_site_api.py`. Под проверкой
остаётся всё, ради чего модуль написан: порядок шагов, разбор ответа, что уезжает в
теле запроса и что ложится в базу.

Живой отправки в сюите нет и быть не должно. Её человек проверяет сам, точечно,
кнопкой на карточке письма.

## Что здесь ловится

**Порядок.** `CheckId` живёт десять секунд, и отправка без него не принимается. Тест
на «сперва спросили разрешение» имеет смысл только потому, что заглушка чужой
`CheckId` отвергает, — иначе он проходил бы и без первого шага.

**Что письмо не сжигается неудачей.** Не ушло — строка остаётся `draft`, причина в
`error`. `failed` его бы похоронил: уникальный ключ считает и неудавшиеся, и
повторно такое письмо уже не сгенерировать.

**Что ушедшее не уходит второй раз.** Два нажатия на значок — два письма одному
человеку, и это видно ему, а не нам.
"""

import uuid

import pytest

from src.dating.dating_mail_send import (DatingMailSendError, dating_mail_send_one)

from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

pytestmark = pytest.mark.mail
# Сайт для писем — выдуманный: своей таблицы сайтов здесь нет, `site_id` просто
# ключ. Живой пул задеть невозможно в принципе.
SITE = 999_777

# Анкета заводится на живом номере (`rhapsody_fake.py` его знает): заведение сверяет
# сайт, а `dating_mailing.profile_id` — внешний ключ. Тот же развод и та же причина,
# что в `test_mailing.py`.
LIVE_SITE = 343


@pytest.fixture
async def profile(auth_client):
    """Анкета-отправитель со своим паролем — тем, который заглушка считает верным."""
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': LIVE_SITE, 'login': f'send-{uuid.uuid4().hex[:8]}@example.com',
        'password': SITE_API_FAKE_PASSWORD})).json()['result']

    # ⚠ Источник пароля — **свой**: умолчание `site` увело бы вход к общему паролю
    # сайта, которого у выдуманного номера нет, и все тесты падали бы на «нечем
    # войти», ничего не сказав про отправку.
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET password_source = 'own'"
                         ' WHERE id = %s', (made,))
        await db.connection.commit()
        await db.execute('SELECT id, site_id, login, password, password_source'
                         ' FROM profile WHERE id = %s', (made,))
        row = dict(await db.fetchone())

    yield row

    await auth_client.delete(f'/api/dating/profile/{made}')


@pytest.fixture
async def letter(profile):
    """Письмо в пуле — то, что будем отправлять."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mailing'
            ' (site_id, profile_id, mail_id, uid, name, body, status)'
            " VALUES (%s, %s, NULL, '812886', 'Vincent', 'Привет, Vincent!', 'draft')",
            (SITE, profile['id']))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        made = int((await db.fetchone())['id'])

    yield await _row(made)

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM mailing WHERE id = %s', (made,))
        await db.connection.commit()


async def _row(letter_id: int) -> dict:
    """Строка письма из базы — как её видит отправка.

    ⚠ **Поля те же, что даёт ручка** (`_answer` в роутере), включая `name`. Ровно на
    этом первый прогон и споткнулся: помощник брал полей меньше, чем нужно
    вызываемому, и журнал вместо имени писал номер — а в живой панели имя есть.
    Помощник, беднее настоящего, проверяет не то, что работает.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, mail_id, uid, name, body,'
                         ' status, error, sent_at FROM mailing WHERE id = %s',
                         (int(letter_id),))

        return dict(await db.fetchone())


async def _send(letter, profile, site_api=None):
    """Отправка с общим паролем сайта. Своей обёрткой — доводов четыре, и повторять
    их в каждом тесте значило бы прятать за ними то, что тест проверяет."""
    return await dating_mail_send_one(letter, profile, SITE_API_FAKE_BASE,
                                      SITE_API_FAKE_PASSWORD)


class TestSendGoes:
    """Письмо уходит — и уходит то самое."""

    async def test_letter_reaches_the_site(self, site_api, letter, profile):
        await _send(letter, profile)

        assert len(site_api.sent) == 1, 'письмо до сайта не дошло'
        uid, body, _ = site_api.sent[0]
        assert uid == '812886'
        assert body == 'Привет, Vincent!', 'ушло не то, что лежало в пуле'

    async def test_permission_is_asked_first(self, site_api, letter, profile):
        """⚠ Сперва `check-send`, потом `send`. Порядок не украшение: `CheckId`
        живёт десять секунд, и отправка без него не принимается вовсе."""
        await _send(letter, profile)

        paths = [path for _, path in site_api.calls]
        check = next(i for i, p in enumerate(paths) if 'check-send' in p)
        send = next(i for i, p in enumerate(paths) if p.endswith('/messages/send'))

        assert check < send, 'разрешение спросили после отправки'

    async def test_the_permission_we_got_is_the_one_we_send(self, site_api, letter,
                                                            profile):
        """Отправляем **то** разрешение, которое дал сайт. Заглушка чужое отвергает,
        так что тест имеет смысл: без этого он проходил бы и на выдуманном."""
        await _send(letter, profile)

        _, _, used = site_api.sent[0]
        assert used.startswith('check-812886'), 'ушло чужое разрешение'

    async def test_nothing_between_the_two_steps(self, site_api, letter, profile):
        """⚠ Между проверкой и отправкой — **ни одного лишнего запроса к сайту**.

        `CheckId` живёт десять секунд, и всякий шаг между ними тратит этот срок.
        Здесь это видно по вызовам: check и send идут подряд.
        """
        await _send(letter, profile)

        paths = [path for _, path in site_api.calls]
        check = next(i for i, p in enumerate(paths) if 'check-send' in p)

        assert paths[check + 1].endswith('/messages/send'), \
            'между разрешением и отправкой вклинился ещё один запрос'

    async def test_row_becomes_sent(self, site_api, letter, profile):
        await _send(letter, profile)
        row = await _row(letter['id'])

        assert row['status'] == 'sent'
        assert row['sent_at'], 'время отправки не записано'
        assert row['error'] == '', 'у ушедшего письма осталась причина отказа'

    async def test_attachment_fields_are_present_and_empty(self, site_api, letter,
                                                           profile):
        """⚠ Поля вложения шлём **пустыми, а не опускаем**: документация сайта их
        перечисляет, и отсутствующее поле — это «мы не знаем такого», а пустое —
        «вложения нет». Утверждения разные."""
        import json

        from src.dating.dating_mail_send import _payload

        body = _payload('c1', '812886', 'текст')

        assert set(body) == {'CheckId', 'RecipientAccountId', 'Body',
                             'ReplyForMessageId', 'AttachmentName',
                             'AttachmentHash', 'AttachmentFile'}
        assert body['ReplyForMessageId'] is None
        assert body['AttachmentFile'] is None
        # Тело обязано быть JSON-годным: сайт принимает его как JSON.
        json.dumps(body)


class TestSendRefused:
    """Не ушло — и письмо от этого не пропало."""

    async def test_site_refusal_is_told_in_words(self, site_api, letter, profile):
        """403 от сайта — фразой, а не кодом: строка уезжает на карточку письма."""
        site_api.forbid_send('812886')

        with pytest.raises(DatingMailSendError) as got:
            await _send(letter, profile)

        assert 'слишком часто' in str(got.value), 'код отказа не превратился во фразу'
        assert not site_api.sent, 'письмо ушло, хотя разрешения не было'

    async def test_letter_stays_in_the_pool(self, site_api, letter, profile):
        """⚠ **Неудача не сжигает письмо.** Строка остаётся `draft`: причина обычно
        временная, а `failed` его бы похоронил — уникальный ключ считает и
        неудавшиеся, и повторно такое письмо уже не сгенерировать."""
        from src.dating.dating_mail_send import dating_mail_send_mark

        site_api.forbid_send('812886')
        try:
            await _send(letter, profile)
        except DatingMailSendError as e:
            await dating_mail_send_mark(letter, sent=False, error=str(e))

        row = await _row(letter['id'])

        assert row['status'] == 'draft', 'неудача унесла письмо из пула'
        assert 'слишком часто' in row['error'], 'причина не записана в письмо'

    async def test_retry_after_failure_works(self, site_api, letter, profile):
        """И повторить можно: ради этого письмо и остаётся в пуле."""
        site_api.forbid_send('812886')
        with pytest.raises(DatingMailSendError):
            await _send(letter, profile)

        site_api.no_send.clear()
        await _send(await _row(letter['id']), profile)

        assert len(site_api.sent) == 1, 'повторная отправка не прошла'

    async def test_offline_site_says_so(self, site_api, letter, profile):
        site_api.offline()

        with pytest.raises(DatingMailSendError) as got:
            await _send(letter, profile)

        assert 'не отвечает' in str(got.value)

    async def test_ok_status_is_required(self, site_api, letter, profile):
        """⚠ Код 200 с чужим `Status` — тоже неудача.

        Сайт вправе ответить «запрос принял, но не отправил», и считать это успехом
        значило бы объявить ушедшим письмо, которого адресат не увидит.
        """
        site_api.refuse(200, {'Status': 'PENDING', 'Message': 'подождите'},
                        on='/messages/send')

        with pytest.raises(DatingMailSendError) as got:
            await _send(letter, profile)

        assert 'OK' in str(got.value)

    async def test_empty_permission_is_refused(self, site_api, letter, profile):
        """Сайт ответил 200, а `CheckId` пуст — отправлять нечем."""
        site_api.refuse(200, {'Message': 'что-то пошло не так'}, on='check-send')

        with pytest.raises(DatingMailSendError) as got:
            await _send(letter, profile)

        assert 'CheckId' in str(got.value)
        assert not site_api.sent


class TestSendGuards:
    """Проверки до похода на сайт: их видно здесь и сразу."""

    async def test_sent_letter_is_not_sent_twice(self, site_api, letter, profile):
        """⚠ Два нажатия — два письма одному человеку, и это видно ему, а не нам."""
        await _send(letter, profile)

        with pytest.raises(DatingMailSendError) as got:
            await _send(await _row(letter['id']), profile)

        assert 'уже ушло' in str(got.value)
        assert len(site_api.sent) == 1, 'письмо ушло второй раз'

    async def test_empty_body_is_refused(self, site_api, letter, profile):
        with pytest.raises(DatingMailSendError) as got:
            await _send({**letter, 'body': '   '}, profile)

        assert 'пустое' in str(got.value)
        assert not site_api.calls, 'на сайт сходили ради пустого письма'

    async def test_no_addressee_is_refused(self, site_api, letter, profile):
        with pytest.raises(DatingMailSendError):
            await _send({**letter, 'uid': ''}, profile)

        assert not site_api.calls

    async def test_no_api_base_is_told_plainly(self, site_api, letter, profile):
        """Адрес API не задан — говорим про настройку, а не про пустой URL."""
        with pytest.raises(DatingMailSendError) as got:
            await dating_mail_send_one(letter, profile, '', SITE_API_FAKE_PASSWORD)

        assert 'Адрес API' in str(got.value)

    async def test_nothing_to_login_with_is_told_plainly(self, site_api, letter,
                                                         profile):
        """⚠ Пароля нет — говорим про настройку сайта. Молчаливый 400 человек
        прочитал бы как «сайт сломался»."""
        with pytest.raises(DatingMailSendError) as got:
            await dating_mail_send_one(letter, {**profile, 'password': '',
                                                'password_source': 'own'},
                                       SITE_API_FAKE_BASE, '')

        assert 'нет логина или пароля' in str(got.value)


class TestPasswordSource:
    """⚠ Под каким паролем заходим — решает настройка анкеты, а не «свой, если есть».

    Ровно на этом человек поправлял трижды: у анкет стоит «общий пароль сайта», и
    свой у них тоже заполнен — но неверный.
    """

    async def test_site_password_is_used_when_asked(self, site_api, letter, profile):
        """`password_source = site` — берём общий, даже когда свой заполнен."""
        await dating_mail_send_one(
            letter, {**profile, 'password': 'чужой-пароль', 'password_source': 'site'},
            SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD)

        assert len(site_api.sent) == 1, 'вошли не под общим паролем'

    async def test_own_password_is_used_when_asked(self, site_api, letter, profile):
        """`password_source = own` — берём свой, даже когда общий верный."""
        with pytest.raises(DatingMailSendError):
            await dating_mail_send_one(
                letter, {**profile, 'password': 'неверный', 'password_source': 'own'},
                SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD)

        assert not site_api.sent, 'вошли под общим, хотя анкета просила свой'


class TestNoRoundSends:
    """⚠ Кругового отправителя нет, и это проверяется прямо.

    Письма уходят живым людям. Заведись однажды круг, который шлёт сам, — он должен
    появиться осознанно, а не потому, что кто-то позвал отправку из круга набора.
    """

    async def test_mailing_round_does_not_send(self):
        import inspect

        from src.dating import dating_mailing_round

        code = inspect.getsource(dating_mailing_round)

        assert 'dating_mail_send' not in code, \
            'круг набора пула научился отправлять письма'

    async def test_making_the_pool_does_not_send(self):
        import inspect

        from src.dating import dating_mailing

        assert 'dating_mail_send' not in inspect.getsource(dating_mailing), \
            'набор пула научился отправлять письма'


class TestSendEndpoint:
    """Ручка `POST /api/dating/mailing/{id}/send` — то, что зовёт кнопка.

    ⚠ Сюита ходит по HTTP в живое приложение, а заглушка сайта подменяет транспорт
    **в своём процессе**. Поэтому здесь проверяется не поход на сайт, а всё
    остальное: отказы словами, права, коды. Сам разговор с сайтом — выше.
    """

    async def test_unknown_letter_is_404(self, auth_client):
        answer = await auth_client.post('/api/dating/mailing/999999999/send')

        assert answer.status_code == 404

    async def test_refusal_is_409_not_500(self, auth_client, letter):
        """⚠ Отказ сайта — **409**, а не 500. Панель отработала верно и принесла
        причину; пятисотый увёл бы человека искать ошибку у нас.

        Здесь сайт недоступен по-настоящему (адрес API у выдуманного сайта не
        задан), и это законный отказ.
        """
        answer = await auth_client.post(f"/api/dating/mailing/{letter['id']}/send")

        assert answer.status_code == 409, 'отказ отдан не как 409'
        assert answer.json()['detail'], 'причина не названа'

    async def test_reason_is_stored_on_the_letter(self, auth_client, letter):
        """⚠ Причину пишем **в письмо**, а не только в ответ: уведомление гаснет, а
        человек вернётся к карточке через час."""
        await auth_client.post(f"/api/dating/mailing/{letter['id']}/send")
        row = await _row(letter['id'])

        assert row['error'], 'причина отказа не записана в письмо'
        assert row['status'] == 'draft', 'отказ унёс письмо из пула'

    async def test_disabled_profile_is_refused(self, auth_client, letter, profile):
        """⚠ От выключенной анкеты не отправляем: её не ведут вовсе, а отправка —
        действие наружу, самое заметное из всех."""
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET enabled = 0 WHERE id = %s',
                             (profile['id'],))
            await db.connection.commit()
        try:
            answer = await auth_client.post(f"/api/dating/mailing/{letter['id']}/send")

            assert answer.status_code == 409
            assert 'выключена' in answer.json()['detail']
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('UPDATE profile SET enabled = 1 WHERE id = %s',
                                 (profile['id'],))
                await db.connection.commit()

    async def test_operator_may_send(self, dating_client, letter):
        """⚠ Отправка открыта оператору, как и правка письма: с пулом работает он, и
        это та самая работа, ради которой панель открывают. Отказ здесь — про сайт
        (409), а не про права (403)."""
        answer = await dating_client.post(f"/api/dating/mailing/{letter['id']}/send")

        assert answer.status_code != 403, 'оператору отправка закрыта'

    async def test_closed_without_login(self, client, letter):
        answer = await client.post(f"/api/dating/mailing/{letter['id']}/send")

        assert answer.status_code in (401, 307)


class TestDeliveryIsRecorded:
    """⚠ Отправка оставляет след в двух местах, и оба нужны.

    **Журнал анкеты** отвечает на вопрос «что вообще происходило с этой анкетой»:
    его читают, разбираясь в поломке, вперемешку с входами, ответами и прогонами.
    **Вкладка «Доставка»** отвечает на другой — «что с моими письмами»: там только
    они, и там видна судьба каждого.

    Одного мало. Журнал не отвечает «сколько писем не дошло»: письма в нём тонут
    среди прочего. «Доставка» не отвечает «что творилось с анкетой»: она про письма.
    """

    async def test_success_reaches_the_journal(self, site_api, letter, profile):
        await _send(letter, profile)

        rows = await _log(profile['id'])
        mine = [row for row in rows if 'письмо ушло' in row['text']]

        assert mine, 'об ушедшем письме в журнале ни строки'
        assert mine[0]['level'] == 'ok', 'удача записана не как удача'
        assert 'Vincent' in mine[0]['text'], 'в журнале не сказано, кому ушло'

    async def test_failure_reaches_the_journal(self, site_api, letter, profile):
        """⚠ И неудача тоже — **с причиной**. «Не ушло» без причины отправляет
        человека искать её в другом месте, а другого места нет."""
        from src.dating.dating_mail_send import dating_mail_send_mark

        site_api.forbid_send('812886')
        try:
            await _send(letter, profile)
        except DatingMailSendError as e:
            await dating_mail_send_mark(letter, sent=False, error=str(e))

        rows = await _log(profile['id'])
        mine = [row for row in rows if 'письмо не ушло' in row['text']]

        assert mine, 'о неудаче в журнале ни строки'
        assert mine[0]['level'] == 'fail', 'неудача записана не как отказ'
        assert 'слишком часто' in mine[0]['text'], 'причина в журнал не попала'

    async def test_journal_names_the_addressee(self, site_api, letter, profile):
        """Адресат в строке журнала — по имени, а не по номеру: номер в ленте среди
        входов и прогонов не говорит ничего."""
        await _send(letter, profile)

        rows = await _log(profile['id'])

        assert any('Vincent' in row['text'] for row in rows)

    async def test_uid_is_used_when_there_is_no_name(self, site_api, profile):
        """Имени нет — берём номер: строка «письмо ушло: » без адресата бесполезна."""
        from mysql_.mysql_ import mysql_get_db_async

        from src.dating.dating_mail_send import dating_mail_send_mark

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO mailing'
                ' (site_id, profile_id, mail_id, uid, name, body, status)'
                " VALUES (%s, %s, NULL, '999001', '', 'Текст', 'draft')",
                (SITE, profile['id']))
            await db.connection.commit()
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            made = int((await db.fetchone())['id'])

        try:
            await dating_mail_send_mark(await _row(made), sent=True)
            rows = await _log(profile['id'])

            assert any('999001' in row['text'] for row in rows), \
                'безымянный адресат потерялся в журнале'
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM mailing WHERE id = %s', (made,))
                await db.connection.commit()


class TestDeliveryTab:
    """Что показывает вкладка «Доставка» — отбор `status_=delivery`.

    ⚠ **Он шире, чем `sent,failed`.** Неудачная отправка оставляет письмо в пуле
    (`draft` с причиной), чтобы её можно было повторить; спроси вкладка по статусам,
    она молчала бы ровно о тех письмах, ради которых на неё и заходят.
    """

    async def test_sent_letter_shows_up(self, auth_client, site_api, letter, profile):
        await _send(letter, profile)

        rows = await _delivery(auth_client)

        assert letter['id'] in [row['id'] for row in rows], 'ушедшего письма нет'

    async def test_failed_attempt_shows_up(self, auth_client, site_api, letter,
                                           profile):
        """⚠ Главная проверка здесь. Письмо осталось `draft`, и по статусу его от
        нетронутого не отличить — а на «Доставке» оно быть обязано."""
        from src.dating.dating_mail_send import dating_mail_send_mark

        site_api.forbid_send('812886')
        try:
            await _send(letter, profile)
        except DatingMailSendError as e:
            await dating_mail_send_mark(letter, sent=False, error=str(e))

        rows = await _delivery(auth_client)
        mine = next((row for row in rows if row['id'] == letter['id']), None)

        assert mine, 'неудавшаяся отправка на «Доставку» не попала'
        assert mine['status'] == 'draft', 'письмо всё-таки унесли из пула'
        assert 'слишком часто' in mine['error'], 'причина до страницы не доехала'

    async def test_untouched_draft_stays_out(self, auth_client, letter):
        """⚠ А нетронутый черновик — **не показываем**: он ещё не доставлялся, и
        строка о нём на «Доставке» была бы ложью. Их полсотни, и они утопили бы
        то немногое, ради чего вкладку открывают."""
        rows = await _delivery(auth_client)

        assert letter['id'] not in [row['id'] for row in rows], \
            'нетронутый черновик попал в журнал доставки'

    async def test_pool_still_holds_the_failed_letter(self, auth_client, site_api,
                                                      letter, profile):
        """И в пуле оно остаётся: «Доставка» показывает попытку, а не уносит письмо."""
        from src.dating.dating_mail_send import dating_mail_send_mark

        site_api.forbid_send('812886')
        try:
            await _send(letter, profile)
        except DatingMailSendError as e:
            await dating_mail_send_mark(letter, sent=False, error=str(e))

        pool = (await auth_client.get(
            f'/api/dating/mailing?site_id={SITE}&status_=draft')).json()['result']

        assert letter['id'] in [row['id'] for row in pool], 'письмо ушло из пула'

    async def test_sent_letter_leaves_the_pool(self, auth_client, site_api, letter,
                                               profile):
        await _send(letter, profile)

        pool = (await auth_client.get(
            f'/api/dating/mailing?site_id={SITE}&status_=draft')).json()['result']

        assert letter['id'] not in [row['id'] for row in pool], \
            'ушедшее письмо осталось в пуле'


async def _log(profile_id: int) -> list[dict]:
    """Журнал анкеты, свежие сверху."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT level, text FROM log WHERE profile_id = %s'
                         ' ORDER BY id DESC LIMIT 20', (int(profile_id),))

        return [dict(row) for row in await db.fetchall() or []]


async def _delivery(auth_client) -> list[dict]:
    """То, что показывает вкладка «Доставка»."""
    answer = await auth_client.get(f'/api/dating/mailing?site_id={SITE}&status_=delivery')

    return answer.json()['result']
