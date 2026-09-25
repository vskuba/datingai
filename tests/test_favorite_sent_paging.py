"""Журнал доставки писем избранным: страницы. «Фавориты → Доставка».

## ⚠⚠ Что здесь стережётся

Вкладка стояла **без пагинации вовсе**: ручка отдавала `LIMIT 300` без счёта, а
подвала с пейджером у неё не было. Пока журнал был короче, разница между «все» и
«первые триста» не проявлялась — а дальше трёхсот писем не показывалось **вовсе и
молча**: прокрутка доезжала до конца, и конец выглядел концом журнала.

⚠ Ту же беду в этом же занятии уже проходили дважды — у «Фаворитов → Диалоги»
(`test_dialogs_paging.py`) и у «Доставки инвайтов» (`test_invite_delivery.py`).
Третий раз она приехала в журнал доставки, потому что страницы заводили списку
разговоров, а не журналу рядом с ним.

## ⚠ Почему проверки про число, а не про вид

Отбор по анкете и число страниц считает **сервер**. Отдай он «последние триста», а
страница отбери среди них — «все письма от Дианы» означало бы «те из них, что
попали в эти триста», причём молча: журнал выглядел бы полным.

⚠ Счёт в шапке (`total`) — про **весь** журнал, а исходы «ушло/не ушло» страница
считает по присланной странице и так и подписывает. Иначе «ушло 24» при ста
семидесяти читалось бы как потеря ста сорока шести.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

# Стенд у соседа: заведение анкеты сверяет номер, а круги воркера про него не
# знают — его нет в `site_`.
STAND_SITE = 999_343


@pytest.fixture
async def journal(auth_client):
    """Две анкеты и журнал: по три ушедших письма каждой, трём собеседникам.

    Плюс одно неудачное, оставшееся в пуле с причиной, и один нетронутый
    черновик: `delivery` обязан показать первое и не показать второй.
    """
    girls = []
    for _ in range(2):
        girls.append(int((await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'fdlv-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']))

    async with mysql_get_db_async() as db:
        for girl in girls:
            for uid, name in (('fz-a', 'Adrian'), ('fz-b', 'Brian'),
                              ('fz-c', 'Carl')):
                await db.execute(
                    'INSERT INTO favorite_mail (site_id, profile_id, uid, name,'
                    " body, status, sent_at)"
                    " VALUES (%s, %s, %s, %s, 'привет', 'sent', NOW())",
                    (STAND_SITE, girl, uid, name))

        # Неудачное: осталось в пуле с причиной.
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status, error)"
            " VALUES (%s, %s, 'fz-d', 'Dominic', 'привет', 'draft', 'сайт молчит')",
            (STAND_SITE, girls[0]))
        # И нетронутый черновик: его в журнале доставки быть не должно.
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status) VALUES (%s, %s, 'fz-e', 'Erik', 'привет', 'draft')",
            (STAND_SITE, girls[1]))
        await db.connection.commit()

    yield girls

    async with mysql_get_db_async() as db:
        for girl in girls:
            await db.execute('DELETE FROM favorite_mail WHERE profile_id = %s',
                             (girl,))
            await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
        await db.connection.commit()


async def _ask(client, per: int = 25, page: int = 1, profile_id: int = 0) -> dict:
    return (await client.get('/api/dating/favorite-mail',
                             params={'site_id': STAND_SITE,
                                     'status_': 'delivery',
                                     'page': page, 'per': per,
                                     'profile_id': profile_id})).json()


class TestPages:
    """Страницы считаются по всему журналу, а не по присланному."""

    async def test_the_count_is_the_whole_journal(self, auth_client, journal):
        """⚠ Семь строк: шесть ушедших и одна неудачная. Нетронутый черновик — нет."""
        got = await _ask(auth_client, per=2)

        assert got['total'] == 7, got['total']
        assert got['pages'] == 4
        assert len(got['result']) == 2

    async def test_the_second_page_is_other_rows(self, auth_client, journal):
        """⚠ Смещение настоящее: страницы не пересекаются.

        Без `OFFSET` вторая страница отдала бы те же строки, и пейджер выглядел бы
        работающим — кнопки нажимаются, список не меняется.
        """
        first = await _ask(auth_client, per=2, page=1)
        second = await _ask(auth_client, per=2, page=2)

        assert {row['id'] for row in first['result']} \
            & {row['id'] for row in second['result']} == set()

    async def test_a_page_past_the_end_comes_back(self, auth_client, journal):
        """⚠ Правим, а не отвергаем: журнал мог укоротиться, пока человек листал.

        Пустой список на девяносто девятой странице читается как «писем больше
        нет», хотя они кончились на четвёртой.
        """
        got = await _ask(auth_client, per=2, page=99)

        assert got['page'] == got['pages'] and got['result']

    async def test_the_pages_match_the_count(self, auth_client, journal):
        """⚠⚠ Счёт и страницы считает **один** кусок SQL.

        Разойдись условия у счёта и у выборки — число страниц перестало бы
        отвечать содержимому, и последняя выходила бы пустой. Поэтому счёт идёт
        обёрткой вокруг **того же** запроса, вместе с его `LEFT JOIN`-ами.
        """
        got = await _ask(auth_client, per=3)

        assert got['pages'] == max(1, -(-got['total'] // 3)), \
            f'страниц {got["pages"]} при счёте {got["total"]}'


class TestFilterAndPagesTogether:
    """⚠ Анкета — фильтр, и страницы считаются **после** него."""

    async def test_the_count_shrinks_with_the_filter(self, auth_client, journal):
        whole = await _ask(auth_client, per=2)
        one = await _ask(auth_client, per=2, profile_id=journal[1])

        assert one['total'] < whole['total'], \
            f'фильтр не сузил счёт: {one["total"]} из {whole["total"]}'
        assert one['total'] == 3, one['total']

    async def test_the_filtered_rows_are_hers_only(self, auth_client, journal):
        got = await _ask(auth_client, per=100, profile_id=journal[0])

        assert {row['profile_id'] for row in got['result']} == {journal[0]}


class TestWhatGetsIn:
    """Что считается доставкой — то же правило, что у инвайтов."""

    async def test_the_untouched_draft_is_not_delivery(self, auth_client, journal):
        """Его не отправляли — ему место в пуле, а не в журнале."""
        got = await _ask(auth_client, per=100)

        assert 'fz-e' not in [row['uid'] for row in got['result']]

    async def test_the_failed_one_is(self, auth_client, journal):
        """⚠ Неудача **оставляет письмо в пуле** (`draft` с причиной): спроси
        вкладка по статусам, она молчала бы ровно о том, ради чего открыта."""
        got = await _ask(auth_client, per=100)

        assert 'fz-d' in [row['uid'] for row in got['result']]


class TestPoolIsNotBroken:
    """⚠⚠ Пул читают **целиком**: страницы завели журналу, а не ему.

    Умолчание `per=0` означает «все», и это то же соглашение, что у инвайтов.
    Сделай мы страницу обязательной — пул писем молча обрезался бы двадцатью
    пятью, а кнопка «отправить» осталась бы у тех, кто на первой странице.
    """

    async def test_the_pool_still_comes_whole(self, auth_client, journal):
        got = (await auth_client.get('/api/dating/favorite-mail',
                                     params={'site_id': STAND_SITE,
                                             'status_': 'draft'})).json()

        assert len({row['uid'] for row in got['result']}) == 2, got['result']

    async def test_the_pool_is_not_cut_to_a_page(self, auth_client, journal):
        """⚠ И число страниц у пула — одна: он пришёл целиком."""
        got = (await auth_client.get('/api/dating/favorite-mail',
                                     params={'site_id': STAND_SITE,
                                             'status_': 'draft'})).json()

        assert got['pages'] == 1, got['pages']


class TestThePagerIsOnThePage:
    """⚠⚠ Страницы у ручки **и** подвал в разметке — иначе листать нечем.

    Ровно так уже ломались «Диалоги» чата: ручка честно отдавала `page` и `pages`,
    а кнопки рисовались в подвале другой области страницы — которой на той вкладке
    нет вовсе. Выходило худшее из двух: дальше первой страницы не показывалось, и
    перейти на вторую было некуда.
    """

    async def test_the_work_template_has_the_pager(self, auth_client):
        page = (await auth_client.get('/admin/dating/profile/work',
                                      params={'site_id': STAND_SITE})).text

        assert 'data-slot="fav-sent-pager"' in page, 'подвала с пейджером нет'

    async def test_the_empty_site_still_answers_with_pager_fields(self, auth_client):
        """⚠ Пустой ответ несёт те же поля: страница читает их без проверок.

        `pages: undefined` нарисовало бы пейджер из одной мёртвой страницы вместо
        пустого подвала.
        """
        got = (await auth_client.get('/api/dating/favorite-mail',
                                     params={'site_id': 0,
                                             'status_': 'delivery'})).json()

        assert got['pages'] == 1 and got['page'] == 1 and got['total'] == 0
