"""Пагинация двух списков разговоров: «Чат → Диалоги» и «Фавориты → Диалоги».

## ⚠⚠ Что здесь стережётся

Обе вкладки показывают пары «анкета ↔ собеседник», стоят рядом и выглядят
одинаково — а листались по-разному, и обе неправильно:

* **у фаворитов пагинации не было вовсе.** Ручка отдавала `LIMIT 300` без счёта:
  пока переписок было три десятка, разница между «все» и «первые триста» не
  проявлялась. На живом сайте список уехал за край экрана, и всё, что дальше, не
  показывалось **молча** — прокрутка доезжала до конца, и конец выглядел концом
  списка;
* **у чата страницы были, а листать было нечем.** Ручка честно отдавала `page` и
  `pages`, но кнопки рисовались в `#contactsPager` — подвале другой области
  страницы, которой на этой вкладке нет вовсе. Выходило худшее из двух: дальше
  пятидесяти диалогов не показывалось, и перейти на вторую страницу было некуда.

⚠ **Ручки зовём в процессе, а не по HTTP**: они только читают базу. Анкеты
заводятся на стенде (`STAND_SITE`), а не на рабочем сайте — иначе их подхватили
бы круги воркера.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating import chat as chat_module
from src.uvicorn.routers.dating import favorite_mail as favorite_module
from src.uvicorn.routers.dating.chat import CHAT_DIALOGS_PAGE, chat_dialogs
from src.uvicorn.routers.dating.favorite_mail import (
    FAVORITE_MAIL_THREADS_PAGE, favorite_mail_threads)

pytestmark = pytest.mark.chat

# Стенд у соседа: заведение анкеты сверяет номер, а круги воркера про него не
# знают — его нет в `site_`.
STAND_SITE = 999_343

# Сколько пар заводим.
PAIRS = 3

# Размер страницы для проверок **границы**.
#
# ⚠⚠ **Поджимаем страницу, а не заводим полторы сотни пар.** Настоящий предел —
# полсотни, и чтобы получить вторую страницу честными данными, пришлось бы
# завести 51 разговор с письмами на каждом прогоне. Обе ручки читают свою
# константу **при вызове** (`size = CHAT_DIALOGS_PAGE`), так что подмена на два
# проверяет ровно ту же арифметику — счёт, число страниц, смещение — на трёх
# строках вместо полутора сотен.
#
# ⚠ Без этого проверки границы зелены **впустую**: при трёх парах страница одна,
# и «за предел отдаём последнюю» не отличить от «страниц не бывает вовсе».
PAGE_SMALL = 2


@pytest.fixture
async def profile(auth_client):
    """Одна анкета на стенде: пары вешаются на неё."""
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE, 'login': f'page-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    yield int(made)

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM favorite WHERE profile_id = %s', (int(made),))
        await db.execute('DELETE FROM mail_in WHERE profile_id = %s', (int(made),))
        await db.connection.commit()

    await auth_client.delete(f'/api/dating/profile/{made}')


class TestChatDialogsPaging:
    """«Чат → Диалоги»: страницы считаются и не пересекаются."""

    async def test_a_page_is_not_longer_than_the_limit(self, chat_pairs):
        got = (await chat_dialogs(site_id=STAND_SITE, page=1, user=None))['result']

        assert len(got['contacts']) <= CHAT_DIALOGS_PAGE, \
            f'на странице {len(got["contacts"])} строк при пределе {CHAT_DIALOGS_PAGE}'

    async def test_the_count_is_the_whole_list_not_the_page(self, chat_pairs):
        """⚠⚠ Счёт — про весь список, а не про страницу.

        Им подписан заголовок колонки («всего диалогов: N») и по нему страница
        считает число страниц. Отдай мы здесь длину страницы — человек читал бы
        «всего 50» при трёхстах, а пейджер показывал бы одну страницу.
        """
        got = (await chat_dialogs(site_id=STAND_SITE, page=1, user=None))['result']

        assert got['contacts_total'] >= PAIRS, \
            f'счёт {got["contacts_total"]} меньше заведённых пар ({PAIRS})'

    async def test_a_page_beyond_the_last_gives_the_last(self, chat_pairs):
        """⚠ За предел — последняя страница, а не пустая.

        Пустой список на седьмой странице читается как «диалоги кончились», хотя
        кончились они на третьей. Поджимает сервер, а не страница: она номера
        страниц не считает.
        """
        got = (await chat_dialogs(site_id=STAND_SITE, page=999, user=None))['result']

        assert got['page'] == got['pages'], \
            f'за пределом отдана страница {got["page"]} из {got["pages"]}'
        assert got['contacts'], 'последняя страница пуста'

    async def test_the_freshest_is_on_top(self, chat_pairs):
        """⚠ Порядок — по свежести разговора.

        Первую строку страница открывает сама, и открывать она должна ту, где
        шевелилось последним.
        """
        got = (await chat_dialogs(site_id=STAND_SITE, page=1, user=None))['result']

        when = [str(one.get('last_at') or '') for one in got['contacts']]

        assert when == sorted(when, reverse=True), \
            f'порядок не по свежести: {when[:4]}'


class TestFavoriteThreadsPaging:
    """«Фавориты → Диалоги»: то же самое, и раньше этого не было вовсе."""

    async def test_a_page_is_not_longer_than_the_limit(self, favorite_pairs):
        got = await favorite_mail_threads(site_id=STAND_SITE, page=1, user=None)

        assert len(got['result']) <= FAVORITE_MAIL_THREADS_PAGE, \
            (f'на странице {len(got["result"])} строк при пределе'
             f' {FAVORITE_MAIL_THREADS_PAGE}')

    async def test_the_count_is_the_whole_list(self, favorite_pairs):
        got = await favorite_mail_threads(site_id=STAND_SITE, page=1, user=None)

        assert got['total'] >= PAIRS, \
            f'счёт {got["total"]} меньше заведённых пар ({PAIRS})'

    async def test_the_count_matches_the_pages(self, favorite_pairs):
        """⚠⚠ Счёт и страницы считает **один** кусок SQL, и это проверяется.

        Разойдись условия отбора у счёта и у списка — число страниц перестало бы
        отвечать содержимому, и последняя выходила бы пустой. Ровно поэтому
        `pairs_join`/`pairs_where` в ручке общие на оба запроса.
        """
        got = await favorite_mail_threads(site_id=STAND_SITE, page=1, user=None)

        size = FAVORITE_MAIL_THREADS_PAGE
        expected = max(1, (got['total'] + size - 1) // size)

        assert got['pages'] == expected, \
            f'страниц {got["pages"]}, а по счёту {got["total"]} их {expected}'

    async def test_a_page_beyond_the_last_gives_the_last(self, favorite_pairs):
        got = await favorite_mail_threads(site_id=STAND_SITE, page=999, user=None)

        assert got['page'] == got['pages'], \
            f'за пределом отдана страница {got["page"]} из {got["pages"]}'
        assert got['result'], 'последняя страница пуста'

    async def test_the_freshest_is_on_top(self, favorite_pairs):
        got = await favorite_mail_threads(site_id=STAND_SITE, page=1, user=None)

        when = [str(one.get('last_at') or '') for one in got['result']]

        assert when == sorted(when, reverse=True), \
            f'порядок не по свежести: {when[:4]}'

    async def test_the_card_still_comes_with_the_row(self, favorite_pairs):
        """⚠ Имя и лицо присоединяются **к странице**, а не ко всему отбору.

        Перенося `LEFT JOIN` за пределы общего куска, легко потерять их вовсе:
        счёт при этом остался бы верным, а список — безымянным. Проверяется тем,
        что поля вообще приходят.
        """
        got = await favorite_mail_threads(site_id=STAND_SITE, page=1, user=None)

        row = got['result'][0]

        for field in ('name', 'photo', 'profile_name', 'letters', 'last_side'):
            assert field in row, f'в строке нет поля {field}'


class TestThePageBoundary:
    """⚠⚠ Граница страницы — то, ради чего всё и делалось.

    Проверки выше на трёх парах зелены и при одной странице: они говорят «не
    больше предела» и «счёт верен», но не отвечают на вопрос «а вторая страница
    вообще бывает». Здесь предел поджат до двух (`PAGE_SMALL`), и вторая страница
    появляется на тех же трёх строках.
    """

    @pytest.fixture(autouse=True)
    def _small_page(self, monkeypatch):
        """Предел страницы на время проверок границы — у обеих ручек сразу.

        ⚠ Подменяем **атрибут модуля**, а не импортированное имя: ручки читают
        константу при вызове, и подмена локальной копии их не коснулась бы.
        """
        monkeypatch.setattr(chat_module, 'CHAT_DIALOGS_PAGE', PAGE_SMALL)
        monkeypatch.setattr(favorite_module, 'FAVORITE_MAIL_THREADS_PAGE',
                            PAGE_SMALL)

    async def test_chat_splits_into_pages(self, chat_pairs):
        got = (await chat_dialogs(site_id=STAND_SITE, page=1, user=None))['result']

        assert got['pages'] > 1, \
            f'три диалога при пределе {PAGE_SMALL} уместились в одну страницу'
        assert len(got['contacts']) == PAGE_SMALL, \
            f'на первой странице {len(got["contacts"])} строк вместо {PAGE_SMALL}'

    async def test_chat_pages_do_not_overlap(self, chat_pairs):
        """⚠ Смещение считается верно: строка не попадает на две страницы.

        Ошибка на единицу в `OFFSET` показала бы последнюю строку первой
        страницы первой же строкой второй — и человек читал бы один разговор
        дважды, не понимая, почему их стало больше.
        """
        first = (await chat_dialogs(site_id=STAND_SITE, page=1,
                                    user=None))['result']['contacts']
        second = (await chat_dialogs(site_id=STAND_SITE, page=2,
                                     user=None))['result']['contacts']

        keys_first = {(one['profile_id'], one['uid']) for one in first}
        keys_second = {(one['profile_id'], one['uid']) for one in second}

        assert keys_first & keys_second == set(), \
            'один диалог попал на обе страницы'

    async def test_chat_pages_cover_everything(self, chat_pairs):
        """⚠⚠ И ни одна строка не потерялась между страницами.

        Обратная сторона предыдущей проверки, и без неё та зелена при любом
        смещении: два **непересекающихся** куска можно получить, потеряв середину.
        """
        seen = set()
        page, pages = 1, 2

        while page <= pages:
            got = (await chat_dialogs(site_id=STAND_SITE, page=page,
                                      user=None))['result']
            pages = got['pages']
            seen |= {(one['profile_id'], one['uid']) for one in got['contacts']}
            page += 1

        assert len(seen) >= PAIRS, \
            f'страницы вместе дали {len(seen)} диалогов из {PAIRS}'

    async def test_favorites_split_into_pages(self, favorite_pairs):
        got = await favorite_mail_threads(site_id=STAND_SITE, page=1, user=None)

        assert got['pages'] > 1, \
            f'три переписки при пределе {PAGE_SMALL} уместились в одну страницу'
        assert len(got['result']) == PAGE_SMALL, \
            f'на первой странице {len(got["result"])} строк вместо {PAGE_SMALL}'

    async def test_favorites_pages_do_not_overlap(self, favorite_pairs):
        first = (await favorite_mail_threads(site_id=STAND_SITE, page=1,
                                             user=None))['result']
        second = (await favorite_mail_threads(site_id=STAND_SITE, page=2,
                                              user=None))['result']

        keys_first = {(one['profile_id'], one['uid']) for one in first}
        keys_second = {(one['profile_id'], one['uid']) for one in second}

        assert keys_first & keys_second == set(), \
            'одна переписка попала на обе страницы'

    async def test_favorites_pages_cover_everything(self, favorite_pairs):
        seen = set()
        page, pages = 1, 2

        while page <= pages:
            got = await favorite_mail_threads(site_id=STAND_SITE, page=page,
                                              user=None)
            pages = got['pages']
            seen |= {(one['profile_id'], one['uid']) for one in got['result']}
            page += 1

        assert len(seen) >= PAIRS, \
            f'страницы вместе дали {len(seen)} переписок из {PAIRS}'

    async def test_favorites_keep_the_order_across_pages(self, favorite_pairs):
        """⚠ Свежесть держится **сквозь** страницы, а не внутри каждой.

        Сортировка стоит до `LIMIT`, и проверка на одной странице этого не
        доказывает: отсортируй мы страницу после выборки, каждая была бы
        упорядочена, а список целиком — нет.
        """
        when = []

        for page in (1, 2):
            got = await favorite_mail_threads(site_id=STAND_SITE, page=page,
                                              user=None)
            when += [str(one.get('last_at') or '') for one in got['result']]

        assert when == sorted(when, reverse=True), \
            f'сквозной порядок нарушен: {when}'


# ── Приватное ────────────────────────────────────────────────────────────────

@pytest.fixture
async def chat_pairs(profile):
    """Несколько диалогов чата с разным временем последней реплики.

    ⚠ **Входящая обязательна у каждой пары**: список показывает только те, где
    есть ответ (`HAVING SUM(direction = 'in')`). С одной исходящей строка не
    попала бы в список вовсе, и проверки молча мерили бы пустоту.
    """
    uids = [f'u{uuid.uuid4().hex[:8]}' for _ in range(PAIRS)]

    async with mysql_get_db_async() as db:
        for step, uid in enumerate(uids):
            for direction in ('out', 'in'):
                await db.execute(
                    'INSERT INTO message'
                    ' (profile_id, sender_uid, sender_name, message_uid, text,'
                    '  direction, is_answered, created_at)'
                    ' VALUES (%s, %s, %s, %s, %s, %s, 1,'
                    '         NOW() - INTERVAL %s MINUTE)',
                    (profile, uid, '', uuid.uuid4().hex, 'x', direction, step))
        await db.connection.commit()

    yield uids

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile,))
        await db.connection.commit()


@pytest.fixture
async def favorite_pairs(profile):
    """Несколько переписок с избранными: звёздочка плюс входящее письмо.

    ⚠ **Нужно и то, и другое.** В список идут пары из `favorite`, у которых есть
    письма: без звёздочки пара не избранная, без письма — разговора нет.
    """
    uids = [f'f{uuid.uuid4().hex[:8]}' for _ in range(PAIRS)]

    async with mysql_get_db_async() as db:
        for step, uid in enumerate(uids):
            await db.execute(
                'INSERT INTO favorite (profile_id, site_id, uid, state)'
                " VALUES (%s, %s, %s, 'done')",
                (profile, STAND_SITE, uid))
            # ⚠ `message_id` — номер письма **на сайте**, и он обязателен: без
            # него строка не вставится вовсе (`NOT NULL` без умолчания).
            await db.execute(
                'INSERT INTO mail_in'
                ' (site_id, profile_id, message_id, uid, body, date_post)'
                ' VALUES (%s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
                (STAND_SITE, profile, uuid.uuid4().int % 10 ** 12, uid,
                 'hello', step))
        await db.connection.commit()

    yield uids

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM favorite WHERE profile_id = %s', (profile,))
        await db.execute('DELETE FROM mail_in WHERE profile_id = %s', (profile,))
        await db.connection.commit()
