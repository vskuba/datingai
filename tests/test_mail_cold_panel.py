"""Холодное письмо через админ-панель: сайт без API-ручки (Step2Love).

Разведано на живой панели 08.11.2026: у сайта **нет ни ручки, ни сокета**, и
страница `mailbox.php` — единственная дорога, которой агентство пишет письма
руками. За месяц рассылки на таком сайте не ушло ни одного письма: отправка
спотыкалась о проверку `api_base` («у сайта не задан адрес API»), которой здесь
чинить нечем — ручки нет вовсе.

## ⚠⚠ Главный сторож здесь — порядок шагов

Письмо складывается из трёх запросов, и крепятся они друг к другу номерами:

    draftcreate → id   fileupload(id)   messagesend(id)

`fileupload` и `messagesend` без живого черновика поддельная панель **отказывает**
(как живая), поэтому перестановка шагов не проходит тестом, а падает отказом
панели. Проверено сторожем самого сторожа (`TestTheFakeRefusesWithoutDraft`).

## ⚠ Удача и отказ различаются только в JSON

Панель отвечает `200` и там и там; решает `result`. Поэтому отказы проверяются
словом панели в тексте ошибки, а не кодом.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_cold_panel import (DatingPanelColdError,
                                        mail_cold_panel_send,
                                        mail_cold_panel_want)
from src.dating.mail_send import DatingMailSendError, mail_send_one
from tests.panel_fake import (PANEL_FAKE_MAILBOX_PATH, PANEL_FAKE_STATE,
                              PANEL_FAKE_URL, panel)  # noqa: F401 — фикстура

pytestmark = pytest.mark.mail

# Номер выдуманный: живые настройки задевать нельзя (тот же диапазон, что у
# соседних проверок настроек сайта).
SITE = 999_784

# Номер девушки **на сайте** — выдуманный: живые номера анкет задевать нельзя.
GIRL_SITE_ID = 7770001

# Номер мужчины на сайте — адресат. Тоже выдуманный.
MAN_SITE_ID = '8880001'

# Тело письма. Панель требует не короче 300 знаков; тестовое — с запасом, чтобы
# проверка длины (если панель решит её навязать) не меняла исход.
BODY = ('Hello dear, I was thinking about you all evening and decided to write. '
        'Tell me about your day, I would love to hear every little detail about '
        'it, no matter how ordinary it seems to you. Yours, Petra.')

# Кадр письма — байты произвольные: панель их не расшифровывает, а тест сверяет,
# что доехали **они же**.
SHOT = b'\xff\xd8\xff\xe0' + b'test-jpeg-bytes' + b'\xff\xd9'


@pytest.fixture
async def site(auth_client):
    """Step2Love-подобный сайт: панель есть, API-ручки нет, сессия живая."""
    before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
              ['result']['settings'])

    await _settings(auth_client, {})

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET panel_state = %s WHERE site_id = %s',
                         (PANEL_FAKE_STATE, SITE))
        await db.connection.commit()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET panel_state = NULL WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()


@pytest.fixture
async def girl():
    """Анкета-отправительница с номером на сайте: без него панель не узнает,
    от кого письмо."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO profile (site_id, login, password, enabled, profile_id)'
            ' VALUES (%s, %s, %s, 1, %s)',
            (SITE, f'cp-{uuid.uuid4().hex[:8]}', 'x', GIRL_SITE_ID))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        row = await db.fetchone()

    made = int(row['id'])

    yield made

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM profile WHERE id = %s', (made,))
        await db.connection.commit()


class TestTheThreeSteps:
    """⚠⚠ Что и в каком порядке уходит панели."""

    async def test_a_letter_with_a_shot_goes_draft_upload_send(self, site, girl,
                                                               panel):
        done = await mail_cold_panel_send(_letter(girl, attach=True),
                                          _profile(girl))

        assert done['sent'], 'письмо не ушло'
        assert panel.mailbox_seq == ['draftcreate', 'fileupload', 'messagesend'], \
            f'шаги пошли не в том порядке: {panel.mailbox_seq}'

    async def test_the_draft_names_man_woman_and_no_letter(self, site, girl,
                                                          panel):
        """⚠ `from_id` — номер девушки: без него письмо ушло бы «от агентства»."""
        await mail_cold_panel_send(_letter(girl), _profile(girl))

        draft = panel.mailbox['draftcreate']
        assert draft['to_id'] == MAN_SITE_ID
        assert draft['reply_id'] == '0', 'черновик ответа, а не холодное письмо'
        assert draft['from_id'] == str(GIRL_SITE_ID), \
            'панель не узнала, от какой девушки письмо'

    async def test_the_shot_arrives_untouched(self, site, girl, panel):
        await mail_cold_panel_send(_letter(girl, attach=True), _profile(girl))

        upload = panel.mailbox['fileupload']
        assert upload['upload']['bytes'] == SHOT, 'кадр доехал искажённым'
        assert upload['upload']['filename'].endswith('.jpg')
        assert upload['type'] == '1', 'тип письма: 1 — Letter'

    async def test_send_carries_draft_number_and_body(self, site, girl, panel):
        await mail_cold_panel_send(_letter(girl), _profile(girl))

        sent = panel.mailbox['messagesend']
        assert int(sent['id']) == 1, 'отправка ушла не по номеру черновика'
        assert sent['to_id'] == MAN_SITE_ID
        assert sent['text'] == BODY
        assert sent['type'] == '1'

    async def test_a_letter_without_a_shot_skips_the_upload(self, site, girl,
                                                           panel):
        await mail_cold_panel_send(_letter(girl), _profile(girl))

        assert panel.mailbox_seq == ['draftcreate', 'messagesend'], \
            'кадра нет, а панель им спросилась'


class TestRefusals:
    """Слово панели доходит до человека; проверка — до похода на панель."""

    async def test_the_panel_words_land_in_the_error(self, site, girl, panel):
        panel.mailbox_fail = 'messagesend'

        with pytest.raises(DatingPanelColdError,
                           match='you cannot write to this man'):
            await mail_cold_panel_send(_letter(girl), _profile(girl))

    async def test_a_dead_session_is_refused_loudly(self, site, girl, panel):
        """Сессия села: панель отвечает не JSON — и это отказ, а не удача."""
        panel.expired = True

        with pytest.raises(DatingPanelColdError, match='черновик'):
            await mail_cold_panel_send(_letter(girl), _profile(girl))

    async def test_a_man_without_a_number_never_reaches_the_panel(self, site,
                                                                  girl, panel):
        letter = _letter(girl)
        letter['uid'] = 'user@example.com'

        with pytest.raises(DatingPanelColdError, match='не похож на номер'):
            await mail_cold_panel_send(letter, _profile(girl))

        assert panel.mailbox_seq == [], 'панель спросилась зря'

    async def test_a_girl_without_a_site_number_is_refused(self, auth_client,
                                                          site, panel):
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO profile (site_id, login, password, enabled)'
                ' VALUES (%s, %s, %s, 1)',
                (SITE, f'cp-{uuid.uuid4().hex[:8]}', 'x'))
            await db.connection.commit()
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            row = await db.fetchone()

        made = int(row['id'])
        try:
            with pytest.raises(DatingPanelColdError, match='номера на сайте'):
                await mail_cold_panel_send(_letter(made), _profile(made))

            assert panel.mailbox_seq == [], 'панель спросилась зря'
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM profile WHERE id = %s', (made,))
                await db.connection.commit()


class TestTheFakeRefusesWithoutDraft:
    """⚠ Сторож самого сторожа: без черновика панель обязана **ругаться**."""

    async def test_send_without_a_draft_is_refused(self, site, panel):
        from src.dating.panel_fetch import panel_fetch_post

        said = await panel_fetch_post(SITE,
                                      'mailbox.php?com=messagesend',
                                      {'id': '424242', 'to_id': MAN_SITE_ID,
                                       'type': '1', 'subj': '', 'text': BODY})

        assert 'error' in said, 'заглушка приняла отправку без черновика'


class TestWhoGoesByPanel:
    """Кому cold-письма идут панелью, а кому — нет."""

    async def test_cold_pools_of_a_panel_site_go(self, site):
        for table in ('mailing', 'favorite_mail', 'intention_mail'):
            assert await mail_cold_panel_want(SITE, table), table

    async def test_answers_do_not(self, site):
        """У ответов своя дорога: входящее письмо и форма `usermessage`."""
        assert not await mail_cold_panel_want(SITE, 'mail_reply')

    async def test_a_site_with_api_driver_never_asks(self, auth_client):
        """⚠ Ladadate не двигается: у него ручка, и панелью он не пишет."""
        other = SITE + 1
        before = ((await auth_client.get(f'/api/dating/site/{other}')).json()
                  ['result']['settings'])
        await auth_client.put(f'/api/dating/site/{other}', json={'settings': {
            'presence': '{"driver": "ladadate"}', **before}})

        try:
            assert not await mail_cold_panel_want(other, 'mailing')
        finally:
            await auth_client.put(f'/api/dating/site/{other}',
                                  json={'settings': before})


class TestTheDoor:
    """⚠⚠ Развилка — в одной двери на круг и кнопку (`mail_send_one`)."""

    async def test_no_api_a_mailing_letter_goes_by_panel(self, auth_client,
                                                         site, girl, panel):
        mailing_id = await _mailing_row(girl)

        try:
            done = await mail_send_one(await _mailing_row_letter(mailing_id),
                                       _profile(girl), '', '', 'mailing')

            assert done['sent']
            assert panel.mailbox_seq == ['draftcreate', 'messagesend']

            async with mysql_get_db_async() as db:
                await db.execute('SELECT status, answer FROM mailing'
                                 ' WHERE id = %s', (mailing_id,))
                row = await db.fetchone()

            assert row['status'] == 'sent', 'исход не лёг в строку пула'
            assert 'ok' in str(row['answer'])
        finally:
            await _drop_mailing(mailing_id)

    async def test_a_site_with_api_never_asks_the_panel(self, site, girl, panel,
                                                        monkeypatch):
        """⚠⚠ Guard для сайтов с рабочей ручкой: у них дорога не меняется."""
        import src.dating.mail_cold_panel as module

        async def never(*a, **kw):
            raise AssertionError('панель спросилась у сайта с API')

        monkeypatch.setattr(module, 'panel_fetch_post', never)

        with pytest.raises(DatingMailSendError):
            await mail_send_one({'id': 1, 'site_id': SITE,
                                 'profile_id': girl, 'uid': MAN_SITE_ID,
                                 'body': BODY, 'status': 'draft'},
                                _profile(girl), 'https://api.invalid', 'x',
                                'mailing')

    async def test_a_panelless_site_keeps_the_old_error(self, auth_client, girl,
                                                       panel):
        """Без панели и без ручки — прежняя честная ошибка про настройку API."""
        other = SITE + 2
        before = ((await auth_client.get(f'/api/dating/site/{other}')).json()
                  ['result']['settings'])
        await auth_client.put(f'/api/dating/site/{other}', json={'settings': {
            'presence': '{"driver": "goldenbride"}', **before}})

        try:
            with pytest.raises(DatingMailSendError, match='Адрес API сайта'):
                await mail_send_one({'id': 1, 'site_id': other,
                                     'profile_id': girl, 'uid': MAN_SITE_ID,
                                     'body': BODY, 'status': 'draft'},
                                    _profile(girl), '', '', 'mailing')
        finally:
            await auth_client.put(f'/api/dating/site/{other}',
                                  json={'settings': before})


# ── Приватное ────────────────────────────────────────────────────────────────

def _letter(profile_id: int, attach: bool = False) -> dict:
    one = {'site_id': SITE, 'profile_id': profile_id, 'uid': MAN_SITE_ID,
           'body': BODY, 'status': 'draft'}
    if attach:
        one['attach'] = {'name': 'photo.jpg', 'data': SHOT}

    return one


def _profile(profile_id: int) -> dict:
    return {'id': profile_id, 'site_id': SITE}


async def _settings(client, more: dict) -> None:
    """Настройки сайта целиком: панель есть, `api_base` — нет."""
    await client.put(f'/api/dating/site/{SITE}', json={'settings': {
        'admin_url': PANEL_FAKE_URL,
        'presence': '{"driver": "step2love"}', **more}})


async def _mailing_row(profile_id: int) -> int:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mailing (site_id, profile_id, mail_id, uid, name,'
            " body, status) VALUES (%s, %s, NULL, %s, 'Petra', %s, 'draft')",
            (SITE, profile_id, MAN_SITE_ID, BODY))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        row = await db.fetchone()

    return int(row['id'])


async def _mailing_row_letter(mailing_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, uid, body, status'
                         ' FROM mailing WHERE id = %s', (mailing_id,))

        return dict(await db.fetchone())


async def _drop_mailing(mailing_id: int) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM mailing WHERE id = %s', (mailing_id,))
        await db.connection.commit()
