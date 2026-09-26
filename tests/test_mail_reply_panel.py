"""Ответ на письмо через админ-панель: контракт формы и правила выбора пути.

Разведано на живой панели Ladadate 26.09.2026, и здесь закреплено то, что нельзя
проверить иначе как контрактом: **какие поля уходят в форму** и **что считается
удачей**. Живьём это стоит письма живому человеку, а ошибка в имени кнопки —
необратимой порчи чужой переписки.

## ⚠⚠⚠ Главный сторож здесь один

В форме панели две кнопки:

    <button name="buttonReply"  value="Reply">Reply</button>
    <input  name="buttonIgnore" value="Ignore Message">

Промах в имени — это не «не отправилось», а **«письмо проигнорировано»**: панель
закроет его без ответа, и вернуть это нельзя. Поэтому `buttonIgnore` есть в поддельной
странице и проверяется, что он **не уходит никогда**.

## ⚠ Почему удача не по коду ответа

Панель отвечает `200` и на удачу, и на отказ, а на удачную форму умеет ответить
`302`. Признак один: **форма ответа закрылась**. Проверяется и обратное — форма
осталась открытой, значит не ушло.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_reply_panel import (DatingPanelReplyError,
                                         MAIL_REPLY_PANEL_BUTTON,
                                         mail_reply_panel_can,
                                         mail_reply_panel_due,
                                         mail_reply_panel_fallback,
                                         mail_reply_panel_open,
                                         mail_reply_panel_send)
from tests.panel_fake import (PANEL_FAKE_OPEN, PANEL_FAKE_STATE, PANEL_FAKE_URL,
                              panel)  # noqa: F401 — фикстура

pytestmark = pytest.mark.mail

# Номер выдуманный: живые настройки задевать нельзя. Тот же диапазон, что у соседних
# проверок настроек сайта.
SITE = 999_782

# Номер письма в панели. ⚠ У живой панели это тот же номер, что у нас в
# `mail_in.message_id` — сверено на проде, 2 из 2.
MESSAGE = 826504459


@pytest.fixture
async def site(auth_client):
    """Сайт с заданной панелью и живой сессией. Что завела, то и убрала."""
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


class TestTheFormWeSend:
    """⚠⚠⚠ Что уходит в форме — и что не уходит никогда."""

    async def test_the_three_fields_go(self, site, panel):
        panel.open(MESSAGE)

        await mail_reply_panel_send(SITE, MESSAGE, 'мой ответ')

        assert len(panel.sent) == 1, 'форма ушла не один раз'
        assert panel.sent[0]['MessageId'] == str(MESSAGE)
        assert panel.sent[0]['ToBody'] == 'мой ответ'
        assert panel.sent[0]['buttonReply'] == 'Reply'

    async def test_the_ignore_button_never_goes(self, site, panel):
        """⚠⚠⚠ Необратимое действие: `buttonIgnore` закрывает письмо без ответа.

        Он есть на самой странице (и в поддельной тоже — иначе проверять было бы
        нечего), но в нашей форме его быть не должно ни при каком исходе.
        """
        panel.open(MESSAGE)

        await mail_reply_panel_send(SITE, MESSAGE, 'мой ответ')

        assert 'buttonIgnore' not in panel.sent[0], \
            'в форму уехала кнопка «Ignore Message» — письмо закроется без ответа'

    async def test_the_form_goes_multipart(self, site, panel):
        """⚠ Форма панели объявлена `multipart/form-data` — отправляем так же.

        Разбор заглушки падает на строку полей, и тогда `MessageId` доедет, а
        проверка ниже — нет: `parse_qsl` вернёт те же имена. Поэтому смотрим на
        **саму кодировку** через отсутствие искажений в тексте с пробелами.
        """
        panel.open(MESSAGE)

        await mail_reply_panel_send(SITE, MESSAGE, 'ответ с пробелами и +плюсом')

        assert panel.sent[0]['ToBody'] == 'ответ с пробелами и +плюсом', \
            'текст исказился — форма ушла не многочастной'

    async def test_the_body_is_trimmed(self, site, panel):
        panel.open(MESSAGE)

        await mail_reply_panel_send(SITE, MESSAGE, '  мой ответ \n')

        assert panel.sent[0]['ToBody'] == 'мой ответ'

    async def test_an_empty_body_never_reaches_the_panel(self, site, panel):
        """⚠ Проверка **до** похода: пустой ответ виден здесь и сразу."""
        panel.open(MESSAGE)

        with pytest.raises(DatingPanelReplyError, match='пустой'):
            await mail_reply_panel_send(SITE, MESSAGE, '   ')

        assert panel.sent == [], 'пустая форма всё равно ушла в панель'


class TestTheReplyPageDecides:
    """⚠⚠ Перед отправкой читаем страницу: форма пропадает у отвеченных писем."""

    async def test_a_closed_message_is_refused(self, site, panel):
        """Проверено на живых номерах: у писем от 23.09 и 09.09 формы нет вовсе."""
        panel.closed(MESSAGE)

        with pytest.raises(DatingPanelReplyError, match='нет формы ответа'):
            await mail_reply_panel_send(SITE, MESSAGE, 'мой ответ')

        assert panel.sent == [], 'форма ушла на письмо, которое уже отвечено'

    async def test_an_unknown_message_is_refused(self, site, panel):
        with pytest.raises(DatingPanelReplyError, match='нет формы ответа'):
            await mail_reply_panel_send(SITE, 111, 'мой ответ')

    async def test_no_number_is_refused_without_a_request(self, site, panel):
        """⚠ Ноль вместо номера — у входящего, пришедшего не из письма."""
        with pytest.raises(DatingPanelReplyError, match='нет номера'):
            await mail_reply_panel_open(SITE, 0)

        assert panel.calls == [], 'за страницей пошли, не имея номера'

    async def test_the_page_must_carry_our_number(self):
        """⚠⚠ Сверяется **номер на странице**: путь принимает любое число, и опечатка
        увела бы ответ в чужую переписку."""
        page = PANEL_FAKE_OPEN.format(message_id=MESSAGE)

        assert mail_reply_panel_can(page, MESSAGE) is True
        assert mail_reply_panel_can(page, MESSAGE + 1) is False, \
            'страница чужого письма сошла за нашу'

    async def test_a_page_without_the_button_is_not_open(self):
        page = PANEL_FAKE_OPEN.format(message_id=MESSAGE).replace(
            f'name="{MAIL_REPLY_PANEL_BUTTON}"', 'name="somethingElse"')

        assert mail_reply_panel_can(page, MESSAGE) is False


class TestSuccessIsConfirmedByRereading:
    """⚠⚠ Удачу решает закрывшаяся форма, а не код ответа."""

    async def test_the_page_is_read_again_after_sending(self, site, panel):
        panel.open(MESSAGE)

        await mail_reply_panel_send(SITE, MESSAGE, 'мой ответ')

        gets = [one for one in panel.calls if one[0] == 'GET']
        assert len(gets) == 2, \
            f'страницу читали {len(gets)} раза вместо двух — до и после отправки'

    async def test_a_form_left_open_means_it_did_not_go(self, site, panel):
        """⚠ Панель ответила `200`, а форма осталась — значит не ушло.

        Ровно этим ошиблись у инвайтов: «сайт принял кадр» приняли за «реплика
        дошла», и панель числила ушедшим то, чего у собеседника не было.
        """
        panel.open(MESSAGE)
        panel.close_on_reply = False

        with pytest.raises(DatingPanelReplyError, match='по-прежнему ждёт'):
            await mail_reply_panel_send(SITE, MESSAGE, 'мой ответ')

    async def test_a_redirect_after_sending_is_not_a_failure(self, site, panel):
        """⚠⚠ Живая панель умеет ответить на форму `302` на список.

        При чтении переадресация — отказ («увела на вход»), при отправке — обычный
        ответ: отличить их по коду нельзя, и решает перечитывание.
        """
        panel.open(MESSAGE)
        panel.redirect_on_post = True

        got = await mail_reply_panel_send(SITE, MESSAGE, 'мой ответ')

        assert got['sent'] is True

    async def test_a_dead_session_speaks_plainly(self, site, panel):
        """⚠ Протухшая сессия — своя причина, а не «форма не закрылась»."""
        panel.open(MESSAGE)
        panel.expired = True

        with pytest.raises(DatingPanelReplyError, match='увела на вход'):
            await mail_reply_panel_send(SITE, MESSAGE, 'мой ответ')

    async def test_a_broken_panel_speaks_plainly(self, site, panel):
        panel.open(MESSAGE)
        panel.broken = 500

        with pytest.raises(DatingPanelReplyError, match='500'):
            await mail_reply_panel_send(SITE, MESSAGE, 'мой ответ')


class TestWhoGoesByPanel:
    """⚠⚠ Два условия: настройка сайта **и** пара в избранных."""

    async def test_a_favorite_goes_by_panel(self, site, girl_and_man):
        girl, man = girl_and_man

        assert await mail_reply_panel_due(SITE, girl, man) is True

    async def test_a_stranger_goes_by_api(self, site, girl_and_man):
        """⚠ Панельный путь заведён под избранных: у прочих ответ — обычной ручкой."""
        girl, _ = girl_and_man

        assert await mail_reply_panel_due(SITE, girl, 'нет-такого') is False

    async def test_the_setting_decides_too(self, site, girl_and_man, auth_client):
        girl, man = girl_and_man
        await _settings(auth_client, {'reply_send_source': 'api'})

        assert await mail_reply_panel_due(SITE, girl, man) is False

    async def test_the_default_is_the_old_way(self, auth_client):
        """⚠⚠ Умолчание — `api`: выкладка ничего не меняет, пока человек не
        переключит. Запасной путь остаётся рабочим по умолчанию, а не «на всякий»."""
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert known['reply_send_source']['default'] == 'api'
        assert known['reply_panel_fallback']['default'] == '1'

    async def test_both_keys_are_registered(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        keys = {row['key'] for row in got['known']}

        assert {'reply_send_source', 'reply_panel_fallback'} <= keys


class TestTheFallback:
    """⚠ Что делать, когда панель не смогла, — решает настройка, а не код."""

    async def test_by_default_we_send_by_api(self, site):
        assert await mail_reply_panel_fallback(SITE) is True

    async def test_it_can_be_turned_off(self, site, auth_client):
        await _settings(auth_client, {'reply_panel_fallback': '0'})

        assert await mail_reply_panel_fallback(SITE) is False


@pytest.fixture
async def girl_and_man(auth_client):
    """Анкета и её избранный. ⚠ Уборка `finally`: падение до `yield` оставило бы
    строки в стенде, и соседние проверки счёта покраснели бы (проходили)."""
    girl = int((await auth_client.post('/api/dating/profile', json={
        'site_id': SITE,
        'login': f'pnl-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])
    man = f'pnl-{uuid.uuid4().hex[:6]}'

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO favorite (site_id, profile_id, uid, state)'
                " VALUES (%s, %s, %s, 'done')", (SITE, girl, man))
            await db.connection.commit()

        yield girl, man
    finally:
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM favorite WHERE profile_id = %s', (girl,))
            await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
            await db.connection.commit()


# ── Приватное ────────────────────────────────────────────────────────────────

async def _settings(client, more: dict) -> None:
    """Настройки сайта — **целиком**, а не одним ключом.

    ⚠⚠ Ручка сайта **заменяет весь набор**: пошли мы один ключ — остальные стёрлись
    бы, и `admin_url` пропал вместе с ними. Проверка при этом продолжала бы
    проходить, но уже по другой причине — «панели у сайта нет» вместо «настройка
    говорит `api`».
    """
    await client.put(f'/api/dating/site/{SITE}', json={'settings': {
        'admin_url': PANEL_FAKE_URL, 'admin_mark': 'Ladies profiles',
        'reply_send_source': 'panel', **more}})
