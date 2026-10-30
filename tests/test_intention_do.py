"""Исполнение намерений: обещанный снимок уходит письмом.

## ⚠⚠ Что здесь стережётся и почему именно это

Занятие замыкает цепочку, у которой **три молчаливых способа сломаться**, и
каждый оставляет панель зелёной:

* письмо уходит **без снимка** — собеседник читает «вот моё фото» без фото, а
  обещание при этом закрыто исполненным, и повода проверить нет;
* снимок уходит **второй раз** — отметка легла раньше отправки либо не легла
  вовсе, и человек получает ту же карточку от той же девушки;
* письмо уходит, но **не считается** — пул не в `MAIL_SEND_TABLES`, и пределы
  получателя с паузой отправительницы молча расходятся с правдой.

Наружу при этом не уходит ничего: Диск и API сайта — заглушки
(`drive_fake.py`, `site_api_fake.py`), подменяющие транспорт.
"""

import base64
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.chat_intention import chat_intention_put, chat_intention_rows
from src.dating.companion_gallery import companion_gallery_seen
from src.dating.intention_do import (INTENTION_DO_PAGE, DatingIntentionError,
                                     intention_mail_attach, intention_mail_done,
                                     intention_mail_draft, intention_mail_make,
                                     intention_mail_ready)
from tests.conftest import TEST_STAND_SITE
from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

pytestmark = pytest.mark.chat

PROMISE = 'she_promised_send_photo'


def _uid() -> str:
    return f'id-{uuid.uuid4().hex[:8]}'


@pytest.fixture
async def ready(girl, drive):
    """Анкета с размеченной галереей и открытым намерением с контекстом.

    Yields:
        `{'girl': номер анкеты, 'uid': собеседник, 'intention': строка пула,
        'files': {имя: номер строки gallery_file}}`.

    ⚠ Файлы заводятся **прямо в индексе**, без обхода Диска: обход — работа
    соседнего занятия, и гонять его здесь значило бы проверять чужое.
    """
    uid = _uid()
    files = {}

    async with mysql_get_db_async() as db:
        for name, path, tags in (
                ('evening', 'public/evening.png',
                 '["outdoor", "evening", "smiling"]'),
                ('studio', 'public/studio.png',
                 '["indoor", "studio", "serious"]')):
            await db.execute(
                'INSERT INTO gallery_file (profile_id, file_id, path, name,'
                '  mime, kind, is_gone, tags)'
                " VALUES (%s, %s, %s, %s, 'image/png', 'image', 0, %s)",
                (girl, f'{name}-{uuid.uuid4().hex[:8]}', path, name, tags))
            files[name] = int(db.lastrowid)
        await db.connection.commit()

    await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {
        'letter': 'here is the photo i promised you',
        'tags': 'outdoor, evening, smiling'}})

    got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                    status='open')

    yield {'girl': girl, 'uid': uid, 'intention': got['rows'][0],
           'files': files}

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM gallery_file WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM intention_mail WHERE profile_id = %s', (girl,))
        await db.connection.commit()


class TestTheDraft:
    """Черновик письма по намерению."""

    async def test_it_takes_the_photo_the_tags_point_at(self, ready):
        """Приметы выбирают снимок, а не первый попавшийся."""
        made = await intention_mail_draft(ready['intention'])

        row = await _letter(made)

        assert row['gallery_file_id'] == ready['files']['evening']
        assert row['body'] == 'here is the photo i promised you'
        assert row['status'] == 'draft'

    async def test_the_promise_stays_open_until_it_is_sent(self, ready):
        """⚠ Черновик — ещё не исполнение: обещание закрывает отправка."""
        await intention_mail_draft(ready['intention'])

        got = await chat_intention_rows(TEST_STAND_SITE,
                                        profile_id=ready['girl'], status='open')

        assert len(got['rows']) == 1, 'намерение закрылось раньше отправки'

    async def test_twice_is_the_same_row_not_a_second_letter(self, ready):
        """⚠⚠ Круг просыпается часто, а черновик ждёт человека.

        Без ключа `uq_intention_mail_once` занятие сочиняло бы письмо на каждом
        тике, и к нажатию «отправить» их накопились бы десятки — все одному
        человеку.
        """
        first = await intention_mail_draft(ready['intention'])
        second = await intention_mail_draft(ready['intention'])

        assert first == second

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM intention_mail'
                             ' WHERE profile_id = %s', (ready['girl'],))
            assert int((await db.fetchone())['n']) == 1

    async def test_a_mark_without_a_doer_waits_it_does_not_count_as_done(self):
        """⚠⚠ Неизвестная метка **ждёт в пуле**, а не считается сделанной.

        Метки присылает workflow у соседа, и новая появится раньше своего
        исполнителя. Сочти мы её исполненной — обещание закрылось бы, не будучи
        выполненным, и узнать об этом было бы неоткуда.
        """
        with pytest.raises(DatingIntentionError, match='исполнять нечем'):
            await intention_mail_draft({'id': 1, 'site_id': TEST_STAND_SITE,
                                        'profile_id': 1, 'uid': 'u',
                                        'intention': 'he_promised_to_call',
                                        'context': {'letter': 'hi'}})

    async def test_no_letter_in_the_context_is_refused(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 {PROMISE: {'tags': 'outdoor'}})
        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                        status='open')

        with pytest.raises(DatingIntentionError, match='нет текста письма'):
            await intention_mail_draft(got['rows'][0])

    async def test_no_photo_leaves_the_promise_and_says_why(self, girl):
        """⚠ Галереи нет — черновика тоже: письмо «вот моё фото» без фото хуже
        молчания, и причина обязана быть названа словами."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {
            'letter': 'here it is', 'tags': 'outdoor'}})
        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                        status='open')

        with pytest.raises(DatingIntentionError, match='галереи у анкеты нет'):
            await intention_mail_draft(got['rows'][0])


class TestTheBatch:
    """Заход по сайту: собрать черновики всем, кому есть что."""

    async def test_it_makes_what_it_can_and_counts_the_rest(self, ready):
        got = await intention_mail_make(TEST_STAND_SITE,
                                        profile_id=ready['girl'])

        assert got['made'] == 1
        assert got['skipped'] == 0

    async def test_drafts_waiting_for_a_human_do_not_hide_new_promises(
            self, ready, girl_make, drive):
        """⚠⚠ Полная страница уже собранных **не прячет** работу за собой.

        Отсев идёт после страницы, а `LIMIT` — до него: страница целиком из
        собранных дала бы пустой ответ при живой работе дальше. Случай не
        выдуманный — черновики ждут человека и копятся, пока он не нажмёт
        «Отправить». Та же грабля, что уже была у инициативы, где негодные пары
        съедали окно в двадцать строк.
        """
        # Пятьдесят намерений с готовыми черновиками — ровно страница отбора.
        for _ in range(INTENTION_DO_PAGE):
            await chat_intention_put(ready['girl'], TEST_STAND_SITE, _uid(),
                                     {PROMISE: {'letter': 'older one',
                                                'tags': 'outdoor, evening'}})
        older = await chat_intention_rows(TEST_STAND_SITE,
                                          profile_id=ready['girl'],
                                          status='open', per=1000)
        async with mysql_get_db_async() as db:
            for one in older['rows'][:INTENTION_DO_PAGE]:
                await db.execute(
                    'INSERT IGNORE INTO intention_mail (site_id, profile_id,'
                    '  intention_id, uid, body) VALUES (%s, %s, %s, %s, %s)',
                    (TEST_STAND_SITE, ready['girl'], int(one['id']),
                     str(one['uid']), 'older one'))
            await db.connection.commit()

        got = await intention_mail_make(TEST_STAND_SITE,
                                        profile_id=ready['girl'])

        assert got['made'] >= 1, \
            'страница собранных спрятала работу за собой — занятие заглохло'

    async def test_the_second_pass_finds_nothing_to_do(self, ready):
        """⚠ Уже собранное второй раз не берётся — иначе круг крутился бы на
        одном намерении, пока человек не нажмёт «отправить»."""
        await intention_mail_make(TEST_STAND_SITE, profile_id=ready['girl'])

        again = await intention_mail_make(TEST_STAND_SITE,
                                          profile_id=ready['girl'])

        assert again['made'] == 0

    async def test_unfillable_promises_do_not_block_the_queue(self, ready,
                                                              girl_make, drive):
        """⚠⚠ Обещание, которое исполнить нечем, **не занимает заход навсегда**.

        У открытых намерений порядок по возрасту, и негодное на следующем
        заходе снова окажется первым. Бери круг ровно `INTENTION_DO_BATCH`
        кандидатов — пять таких обещаний заняли бы его целиком, и годные за
        ними не собрались бы никогда. Ни одной ошибки при этом: панель
        докладывает «пропущено 5», и это выглядит работой.
        """
        # Шесть обещаний у анкет без галереи — больше, чем берётся за заход.
        for _ in range(6):
            other = await girl_make()
            await chat_intention_put(other, TEST_STAND_SITE, _uid(),
                                     {PROMISE: {'letter': 'here it is',
                                                'tags': 'outdoor'}})

        got = await intention_mail_make(TEST_STAND_SITE)

        assert got['made'] >= 1, \
            'годное обещание не собралось из-за негодных впереди'

    async def test_a_refusal_names_the_pair_not_just_the_reason(self, girl):
        """⚠ «Снимок не выбрался» без имени пары не говорит ничего: намерений
        в пуле десятки."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {
            'letter': 'here it is', 'tags': 'outdoor'}})

        got = await intention_mail_make(TEST_STAND_SITE, profile_id=girl)

        assert got['made'] == 0 and got['skipped'] == 1
        assert uid in got['why'][0], 'в причине нет пары'


class TestTheAttachment:
    """Снимок с Диска → байты письма."""

    async def test_it_comes_back_as_jpeg(self, ready, drive):
        """⚠⚠ Приводим к JPEG: на Диске лежат PNG по мегабайтам, а base64
        добавляет треть — такое письмо сайт отвергнет."""
        async with mysql_get_db_async() as db:
            await db.execute('SELECT file_id, name FROM gallery_file WHERE id = %s',
                             (ready['files']['evening'],))
            file = dict(await db.fetchone())

        got = await intention_mail_attach(file['file_id'], file['name'])

        assert got['data'][:3] == b'\xff\xd8\xff', 'это не JPEG'
        assert got['name'].endswith('.jpg')

    async def test_the_name_loses_the_png_extension(self, ready, drive):
        """⚠ Содержимое после приведения — JPEG, и `evening.png` в имени
        соврал бы почтовому клиенту собеседника."""
        got = await intention_mail_attach(
            (await _file(ready['files']['evening']))['file_id'], 'evening.png')

        assert got['name'] == 'evening.jpg'

    async def test_a_missing_photo_is_told_in_words(self, ready, drive):
        with pytest.raises(DatingIntentionError, match='нет номера файла'):
            await intention_mail_attach('', 'a.png')


class TestTheLetterIsReady:
    """Строка пула → письмо, готовое к отправке."""

    async def test_the_photo_rides_with_it(self, ready, drive):
        made = await intention_mail_draft(ready['intention'])

        letter = await intention_mail_ready(await _letter(made))

        assert letter['attach']['data'][:3] == b'\xff\xd8\xff'
        assert letter['body'] == ready['intention']['context']['letter']

    async def test_a_vanished_photo_is_refused_not_sent_as_text(self, ready, drive):
        """⚠⚠ Снимок исчез из индекса — **отказ**, а не письмо текстом.

        Тихая отправка закрыла бы обещание исполненным, оставив собеседника с
        «вот моё фото» без фото.
        """
        made = await intention_mail_draft(ready['intention'])
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM gallery_file WHERE id = %s',
                             (ready['files']['evening'],))
            await db.connection.commit()

        with pytest.raises(DatingIntentionError, match='больше нет в индексе'):
            await intention_mail_ready(await _letter(made))


class TestTheMarksAfterSending:
    """Что отмечается, когда письмо ушло."""

    async def test_the_photo_is_remembered_and_the_promise_closed(self, ready):
        made = await intention_mail_draft(ready['intention'])
        letter = await _letter(made)

        await intention_mail_done(letter)

        seen = await companion_gallery_seen(ready['girl'], ready['uid'])
        assert {one['name'] for one in seen} == {'evening'}

        left = await chat_intention_rows(TEST_STAND_SITE,
                                         profile_id=ready['girl'], status='open')
        assert left['rows'] == [], 'обещание осталось открытым'

    async def test_the_next_promise_takes_another_photo(self, ready):
        """⚠⚠ Ради этого память и ведётся: второе обещание той же паре не
        должно принести тот же снимок."""
        made = await intention_mail_draft(ready['intention'])
        await intention_mail_done(await _letter(made))

        await chat_intention_put(ready['girl'], TEST_STAND_SITE, ready['uid'],
                                 {PROMISE: {'letter': 'one more',
                                            'tags': 'outdoor, studio'}})
        got = await chat_intention_rows(TEST_STAND_SITE,
                                        profile_id=ready['girl'], status='open')
        second = await intention_mail_draft(got['rows'][0])

        assert (await _letter(second))['gallery_file_id'] \
            == ready['files']['studio'], 'тот же снимок ушёл второй раз'

    async def test_a_failed_mark_does_not_claim_the_send_failed(self, ready):
        """⚠ Письмо **уже ушло**, и исключение отсюда заставило бы вызывающего
        считать отправку несостоявшейся."""
        await intention_mail_done({'id': 0, 'intention_id': 0, 'profile_id': 0,
                                   'site_id': 0, 'uid': '',
                                   'gallery_file_id': 0})


class TestTheWholeChain:
    """Путь целиком: обещание → черновик → отправка → отметки.

    ## ⚠⚠ Ради этого теста всё и писалось

    Каждое звено проверено порознь выше, а ломается стык: письмо уходит, а
    отметки не ложатся — и тогда обещание висит открытым (круг соберёт второе
    письмо), либо снимок числится непоказанным (следующее обещание пришлёт
    его же). Обе беды молчаливые: панель зелёная, сайт доволен, человек
    получает два одинаковых фото.
    """

    async def test_the_letter_goes_with_the_photo_and_both_marks_land(
            self, ready, drive, site_api):
        from src.dating.mail_send import mail_send_one

        made = await intention_mail_draft(ready['intention'])
        letter = await intention_mail_ready(await _letter(made))

        await mail_send_one(letter, await _profile(ready['girl']),
                            SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD,
                            'intention_mail')

        # 1. Снимок доехал до сайта — вложением, а не словами.
        sent = site_api.payloads[-1]
        assert base64.b64decode(sent['AttachmentFile'])[:3] == b'\xff\xd8\xff'
        assert sent['AttachmentHash'], 'хэш вложения пуст'

        # 2. Письмо отмечено ушедшим.
        assert (await _letter(made))['status'] == 'sent'

        # 3. Обещание закрыто — круг не соберёт второго письма.
        left = await chat_intention_rows(TEST_STAND_SITE,
                                         profile_id=ready['girl'],
                                         status='open')
        assert left['rows'] == [], 'обещание осталось открытым после отправки'

        # 4. Снимок запомнен — следующее обещание возьмёт другой.
        seen = await companion_gallery_seen(ready['girl'], ready['uid'])
        assert {one['name'] for one in seen} == {'evening'}

    async def test_a_refused_letter_keeps_the_promise_open(self, ready, drive,
                                                           site_api):
        """⚠⚠ Сайт отказал — обещание **остаётся**, и снимок не считается
        показанным: иначе отказ стоил бы и письма, и кадра разом."""
        from src.dating.mail_send import DatingMailSendError, mail_send_one

        made = await intention_mail_draft(ready['intention'])
        letter = await intention_mail_ready(await _letter(made))
        site_api.forbid_send(ready['uid'])

        with pytest.raises(DatingMailSendError):
            await mail_send_one(letter, await _profile(ready['girl']),
                                SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD,
                                'intention_mail')

        left = await chat_intention_rows(TEST_STAND_SITE,
                                         profile_id=ready['girl'],
                                         status='open')
        assert len(left['rows']) == 1, 'обещание закрылось при отказе сайта'
        assert await companion_gallery_seen(ready['girl'], ready['uid']) == []


class TestThePoolIsCounted:
    """⚠⚠ Пул обязан входить в списки отправки, иначе пределы врут."""

    def test_it_is_a_letter_that_went_to_a_person(self):
        """`MAIL_SEND_TABLES` — это «что ушло живому человеку»: по нему считают
        паузу отправительницы и «сколько мы ему написали»."""
        from src.dating.mail_send import MAIL_SEND_TABLES

        assert 'intention_mail' in MAIL_SEND_TABLES

    def test_it_is_not_cold_outreach(self):
        """⚠ О снимке просили. Войди пул в холодные, письмо по обещанию съедало
        бы дневной предел настоящего приставания — за вежливость наказали бы
        дважды, та же беда, из-за которой там нет `mail_reply`."""
        from src.dating.mail_send import MAIL_SEND_COLD_TABLES

        assert 'intention_mail' not in MAIL_SEND_COLD_TABLES

    def test_a_ban_sweeps_its_drafts(self):
        """⚠ Запрет писать — ни одно письмо паре больше не уйдёт, и неубранный
        черновик держал бы намерение открытым вечно."""
        from src.dating.block import BLOCK_POOLS, BLOCK_POOLS_MAIL

        assert 'intention_mail' in BLOCK_POOLS
        assert 'intention_mail' in BLOCK_POOLS_MAIL

    def test_the_send_round_knows_it(self):
        """⚠ Свой круг отправки ему не заводили: пауза между письмами одной
        учётки общая, и два круга дали бы `ERROR_TOO_MANY_REQUESTS`."""
        from src.dating.send_round import _POOLS

        assert 'intention_mail' in [one[1] for one in _POOLS]


class TestTheHandles:
    """Ручки вкладки: пул, сбор, отправка, уборка."""

    async def test_the_pool_carries_the_photo_it_will_send(self, ready,
                                                           auth_client):
        """⚠ Страница показывает снимок **из строки пула**, а не свежий отбор:
        уйдёт тот кадр, который лежит, и обещать другой нельзя."""
        await intention_mail_draft(ready['intention'])

        got = (await auth_client.get('/api/dating/chat-intention/mail'
                                     f'?site_id={TEST_STAND_SITE}')).json()

        mine = [one for one in got['result']
                if one['profile_id'] == ready['girl']]
        assert len(mine) == 1
        assert mine[0]['gallery_path'] == 'public/evening.png'
        assert mine[0]['gallery_file'], 'номер файла на Диске не отдан'

    async def test_the_button_collects_like_the_round(self, ready, auth_client):
        got = (await auth_client.post(
            '/api/dating/chat-intention/mail/make'
            f'?site_id={TEST_STAND_SITE}&profile_id={ready["girl"]}')).json()

        assert got['result']['made'] == 1

    async def test_a_site_is_required_to_collect(self, auth_client):
        answer = await auth_client.post(
            '/api/dating/chat-intention/mail/make?site_id=0')

        assert answer.status_code == 400

    async def test_a_sent_letter_is_not_sent_twice(self, ready, auth_client):
        """⚠ Два щелчка по кнопке — одно письмо: второй получает отказ словами,
        а не второе письмо живому человеку."""
        made = await intention_mail_draft(ready['intention'])
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE intention_mail SET status = 'sent'"
                             ' WHERE id = %s', (made,))
            await db.connection.commit()

        answer = await auth_client.post(
            f'/api/dating/chat-intention/mail/{made}/send')

        assert answer.status_code == 409
        assert 'уже ушло' in answer.json()['detail']

    async def test_a_missing_letter_is_404(self, auth_client):
        answer = await auth_client.post(
            '/api/dating/chat-intention/mail/9000000/send')

        assert answer.status_code == 404

    async def test_dropping_keeps_the_promise_open(self, ready, auth_client):
        """⚠⚠ Человек убрал **письмо**, а не отменил обещание: круг соберёт
        его заново, возможно с другим снимком. Закрыть намерение можно только
        исполнив его."""
        made = await intention_mail_draft(ready['intention'])

        answer = await auth_client.delete(
            f'/api/dating/chat-intention/mail/{made}')

        assert answer.status_code == 200

        left = await chat_intention_rows(TEST_STAND_SITE,
                                         profile_id=ready['girl'],
                                         status='open')
        assert len(left['rows']) == 1, 'обещание закрылось вместе с письмом'


async def _letter(letter_id: int) -> dict:
    """Строка пула целиком."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM intention_mail WHERE id = %s',
                         (int(letter_id),))

        return dict(await db.fetchone())


async def _profile(profile_id: int) -> dict:
    """Анкета-отправительница в том виде, в каком её получает отправка."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, login, password, password_source'
                         ' FROM profile WHERE id = %s', (int(profile_id),))

        return dict(await db.fetchone())


async def _file(file_row_id: int) -> dict:
    """Строка индекса галереи."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM gallery_file WHERE id = %s',
                         (int(file_row_id),))

        return dict(await db.fetchone())
