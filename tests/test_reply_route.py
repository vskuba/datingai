"""Каким путём уходит ответ: развилка в единственной двери отправки.

Отправка ответа одна (`mail_send_one`), а путей теперь два: ручка API сайта под самой
девушкой и форма админ-панели. Здесь проверяется сама развилка — что она стоит **в
одном месте**, слушается настройки, и что запасной путь остаётся рабочим.

## ⚠⚠ Почему развилка внутри `mail_send_one`, а не у вызывающих

Отправку зовут двое: кнопка человека и круг отправки. Развилка у каждого разошлась бы
— и это не предположение, а уже случившаяся беда: номер письма, на которое отвечаем
(`reply_to` → `ReplyForMessageId`), кнопка передавала всегда, а **запрос круга не
выбирал его вовсе**. То есть всё, что уходило кругом, шло не ответом, а отдельным
письмом; собеседник не понимал, на что ему отвечают, а панель агентства числила
письма неотвеченными. Найдено 26.09.2026 при разведке панели, починено здесь же —
и на это стоит отдельная проверка ниже.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_send import DatingMailSendError, mail_send_one
from src.dating.send_round import _oldest
from tests.panel_fake import (PANEL_FAKE_STATE, PANEL_FAKE_URL,
                              panel)  # noqa: F401 — фикстура
from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

pytestmark = pytest.mark.mail

SITE = 999_783
MESSAGE = 826497001

# Адрес API поддельного сайта — **его же**, а не свой похожий: `site_api` подменён
# заглушкой на весь прогон (`conftest`), и она отвечает по своему адресу.
API_BASE = SITE_API_FAKE_BASE


@pytest.fixture
async def stand(auth_client):
    """Сайт с панелью, анкета, её избранный, входящее письмо и ответ в пуле.

    ⚠ Уборка `finally`: падение до `yield` оставило бы строки в общем стенде, и
    покраснели бы соседние проверки счёта — это уже случалось.
    """
    before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
              ['result']['settings'])

    await _settings(auth_client, {})

    girl = int((await auth_client.post('/api/dating/profile', json={
        'site_id': SITE,
        'login': f'rt-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])
    man = f'rt-{uuid.uuid4().hex[:6]}'

    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE site SET panel_state = %s WHERE site_id = %s',
                             (PANEL_FAKE_STATE, SITE))
            await db.execute(
                'INSERT INTO favorite (site_id, profile_id, uid, state)'
                " VALUES (%s, %s, %s, 'done')", (SITE, girl, man))
            await db.execute(
                'INSERT INTO mail_in (site_id, profile_id, message_id, uid, body,'
                ' date_post) VALUES (%s, %s, %s, %s, %s, NOW())',
                (SITE, girl, MESSAGE, man, 'his letter'))
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            in_id = int((await db.fetchone())['id'])
            await db.execute(
                'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid, name,'
                " body, status) VALUES (%s, %s, %s, %s, 'Man', 'мой ответ', 'draft')",
                (SITE, girl, in_id, man))
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            reply_id = int((await db.fetchone())['id'])
            await db.connection.commit()

        yield {'girl': girl, 'man': man, 'in_id': in_id, 'reply_id': reply_id}
    finally:
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': before})
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE site SET panel_state = NULL WHERE site_id = %s',
                             (SITE,))
            for table in ('mail_reply', 'mail_in', 'favorite'):
                await db.execute(f'DELETE FROM {table} WHERE profile_id = %s',
                                 (girl,))
            await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
            await db.connection.commit()

class TestTheFavoriteGoesByPanel:
    """⚠ Настройка `panel` плюс пара в избранных — и ответ уходит формой."""

    async def test_the_panel_gets_the_form(self, stand, panel):
        panel.open(MESSAGE)

        got = await mail_send_one(_letter(stand), _girl(stand), API_BASE,
                                  SITE_API_FAKE_PASSWORD, table='mail_reply')

        assert got['sent'] is True
        assert panel.sent[0]['ToBody'] == 'мой ответ'
        assert panel.sent[0]['MessageId'] == str(MESSAGE)

    async def test_the_outcome_lands_in_the_pool_row(self, stand, panel):
        """⚠ Исход пишет **общий** `mail_send_mark` — тот же, что у пути через API:
        пул один, и две записи исхода разошлись бы в словах."""
        panel.open(MESSAGE)

        await mail_send_one(_letter(stand), _girl(stand), API_BASE,
                            SITE_API_FAKE_PASSWORD,
                            table='mail_reply')
        row = await _status(stand['reply_id'])

        assert row['status'] == 'sent'
        assert row['sent_at'], 'время отправки не записалось'
        assert 'панель' in row['answer'], f"ответ панели не записан: {row['answer']}"

    async def test_the_site_api_is_not_touched(self, stand, panel, site_api):
        """⚠⚠ Панельный путь идёт **вместо** ручки, а не вместе с ней: два письма
        одному человеку — то, от чего вся эта затея и защищает."""
        panel.open(MESSAGE)

        await mail_send_one(_letter(stand), _girl(stand), API_BASE,
                            SITE_API_FAKE_PASSWORD,
                            table='mail_reply')

        assert site_api.sent == [], 'ответ ушёл ещё и ручкой API'


class TestTheStrangerGoesByApi:
    """⚠ Не избранный — обычный путь, какой бы ни стояла настройка."""

    async def test_the_panel_is_not_asked(self, stand, panel, site_api):
        letter = {**_letter(stand), 'uid': 'нет-такого'}

        try:
            await mail_send_one(letter, _girl(stand), API_BASE,
                                SITE_API_FAKE_PASSWORD, table='mail_reply')
        except DatingMailSendError:
            # ⚠ Исход ручки здесь не важен: проверяем, что **в панель не пошли**.
            pass

        assert panel.calls == [], 'за страницей панели пошли для не избранного'


class TestTheFallback:
    """⚠⚠ Панель не смогла — решает настройка, а письмо не теряется молча."""

    async def test_by_default_it_goes_by_api(self, stand, panel, site_api):
        """Форма закрыта, досылка разрешена — уходим ручкой."""
        panel.closed(MESSAGE)

        await mail_send_one(_letter(stand), _girl(stand), API_BASE,
                            SITE_API_FAKE_PASSWORD,
                            table='mail_reply')

        assert panel.sent == [], 'форма ушла в закрытое письмо'
        assert site_api.sent, 'ответ не ушёл вовсе: ни панелью, ни ручкой'

    async def test_the_reply_id_reaches_the_site(self, stand, panel, site_api):
        """⚠⚠⚠ Тот самый `ReplyForMessageId`: без него сайт покажет наш ответ
        отдельным письмом, и собеседник не поймёт, на что ему отвечают."""
        panel.closed(MESSAGE)

        await mail_send_one(_letter(stand), _girl(stand), API_BASE,
                            SITE_API_FAKE_PASSWORD,
                            table='mail_reply')

        assert site_api.payloads[-1].get('ReplyForMessageId') == MESSAGE

    async def test_turned_off_it_stays_in_the_pool(self, stand, panel, site_api,
                                                   auth_client):
        """⚠ «Оставить в пуле» честнее, когда форма закрылась **потому что письмо
        уже отвечено**: досылка ручкой станет вторым письмом человеку."""
        panel.closed(MESSAGE)
        await _settings(auth_client, {'reply_panel_fallback': '0'})

        with pytest.raises(DatingMailSendError, match='панель не приняла'):
            await mail_send_one(_letter(stand), _girl(stand), API_BASE,
                            SITE_API_FAKE_PASSWORD,
                                table='mail_reply')

        row = await _status(stand['reply_id'])
        assert row['status'] == 'draft', 'письмо ушло из пула, хотя не отправлено'
        assert 'нет формы ответа' in row['error'], \
            f"причина не записана: {row['error']}"
        assert site_api.sent == [], 'досылка ручкой прошла при запрете'


class TestTheRoundCarriesTheMessageNumber:
    """⚠⚠⚠ Починка живой беды: круг отправки терял номер письма.

    `mail_reply_send` (кнопка человека) передавал `reply_to` всегда, а запрос круга
    (`_oldest`) поля не выбирал вовсе — то есть **всё, что уходило кругом, шло не
    ответом**. Панель числила неотвеченными письма, на которые ответ ушёл сутки назад.

    ⚠ Проверяется поведением, а не чтением кода: имя столбца в `SELECT` можно
    переписать, и тест должен упасть от того, что номер не доехал, — а не от того,
    что изменилась строка.
    """

    async def test_the_round_picks_the_number_up(self, stand):
        got = await _oldest(SITE, 'mail_reply')

        assert got, 'круг не нашёл ответа в пуле'
        assert int(got['reply_to']) == MESSAGE, \
            'номер письма не доехал до отправки — ответ уйдёт отдельным письмом'

    async def test_other_pools_do_not_get_it(self, stand):
        """⚠ У рассылки и писем избранным номера нет вовсе: они не ответы, и лишний
        `JOIN` к `mail_in` им незачем."""
        got = await _oldest(SITE, 'favorite_mail')

        assert got is None or 'reply_to' not in got

# ── Приватное ────────────────────────────────────────────────────────────────
#
# ⚠ Помощники — **под проверками**, как в `test_invite_delivery.py`: правило проекта
# «приватное ниже публичного» действует и в сюите. Файл читают сверху, и начинать
# чтение с механики стенда значит откладывать то, ради чего его открыли.

async def _settings(client, more: dict) -> None:
    """Настройки сайта — **целиком**, а не одним ключом.

    ⚠⚠ Ручка сайта **заменяет весь набор** («Целиком, а не по ключу»): страница
    держит его перед глазами и шлёт как есть. Пошли мы один ключ — остальные
    стёрлись бы, и `reply_send_source` вернулся бы к умолчанию `api`. Именно так
    проверка «панель не смогла, досылать запрещено» и проходила молча: письмо уходило
    ручкой, потому что панельный путь был сброшен вторым запросом.
    """
    await client.put(f'/api/dating/site/{SITE}', json={'settings': {
        'admin_url': PANEL_FAKE_URL, 'admin_mark': 'Ladies profiles',
        'api_base': API_BASE, 'reply_send_source': 'panel',
        # ⚠ Задержка ответа по длине текста — ноль: файл про **дорогу** письма
        # (панель или ручка API) и про номер, на который оно отвечает. Входящее
        # стенда стоит временем `NOW()`, и при умолчании очередь честно держала бы
        # ответ — тесты краснели бы по чужой причине (`reply_delay.py`).
        'reply_delay_per_10_words': '0', **more}})


def _letter(stand: dict) -> dict:
    """Строка пула, какой её отдаёт круг отправки, — вместе с `reply_to`."""
    return {'id': stand['reply_id'], 'site_id': SITE, 'uid': stand['man'],
            'body': 'мой ответ', 'status': 'draft',
            'profile_id': stand['girl'], 'reply_to': MESSAGE}


def _girl(stand: dict) -> dict:
    """Анкета-отправительница. ⚠ Пароль — тот, который поддельный сайт считает
    верным: запасной путь обязан **дойти до отправки**, иначе проверка «ушло ручкой»
    прошла бы и на сломанном входе."""
    return {'id': stand['girl'], 'site_id': SITE, 'login': 'rt@example.com',
            'password': SITE_API_FAKE_PASSWORD, 'password_source': 'own'}


async def _status(reply_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT status, error, answer, sent_at FROM mail_reply'
                         ' WHERE id = %s', (int(reply_id),))

        return dict(await db.fetchone() or {})
