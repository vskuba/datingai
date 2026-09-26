"""«Письма → Доставка»: журнал сливает два источника и листается страницами.

Журнал показывает всё, что ушло от сайта, а уходит из **двух** таблиц: рассылка
(`mailing`) и ответы на входящие (`mail_reply`). Получатель их не различает, и
журнал обязан показывать оба.

## ⚠⚠ Почему ручка одна, а не две

Страница брала их двумя запросами и сортировала у себя. Со страницами так нельзя:
«вторая страница рассылки» плюс «вторая страница ответов» — **не** вторая страница
журнала, и число страниц по такому слиянию посчитать нечем. Отсюда одна ручка: она
и сливает, и считает.

⚠ Прежде обе ручки отдавали по `LIMIT 300` без счёта, и всё, что дальше, не
показывалось **молча** — как это уже было у «Фаворитов» дважды.

## ⚠ Что здесь стережётся в первую очередь

**Срез слияния.** Приём такой: чтобы отдать первые `page * per` строк журнала,
довольно взять по столько же свежих из каждой таблицы, слить и отрезать кусок.
Ошибись в нём — страница молча покажет не те строки, и заметить это можно только
сверкой с полным списком. Поэтому здесь есть проверка «страница 1 плюс страница 2
равны первым шести строкам полного журнала».
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating.mailing import (MAILING_DELIVERY_PER,
                                                       mailing_delivery)

pytestmark = pytest.mark.mail

# Стенд у соседа: заведение анкеты сверяет номер, а круги воркера про него не знают.
STAND_SITE = 999_343


@pytest.fixture
async def journal(auth_client):
    """Журнал из двух источников, перемешанных по времени.

    Три письма рассылки и три ответа, и времена **чередуются** — иначе слияние
    «проверялось» бы на данных, где любой порядок сошёлся бы случайно.

    ⚠ Плюс по одной неудачной строке в каждой таблице (`draft` с причиной): журнал
    обязан показать и их — ради них на вкладку и заходят.
    """
    girl = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'dlv-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    tag = uuid.uuid4().hex[:6]

    async with mysql_get_db_async() as db:
        # ⚠ Письмо рассылки требует шаблон (`mail_id`) — заводим свой.
        await db.execute(
            'INSERT INTO mail (site_id, name, body, enabled)'
            " VALUES (%s, %s, 'Привет!', 1)", (STAND_SITE, f'tpl-{tag}'))
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        mail_id = int((await db.fetchone())['id'])

        # Чередуем: рассылка на 10, 30, 50 минут назад, ответы — на 20, 40, 60.
        for step, minutes in enumerate((10, 30, 50)):
            await db.execute(
                'INSERT INTO mailing (site_id, profile_id, mail_id, uid, name,'
                " body, status, sent_at) VALUES (%s, %s, %s, %s, %s, 'letter',"
                " 'sent', NOW() - INTERVAL %s MINUTE)",
                (STAND_SITE, girl, mail_id, f'ml{step}-{tag}', 'Letter', minutes))

        for step, minutes in enumerate((20, 40, 60)):
            # ⚠ Ответу нужно входящее письмо: `mail_in_id` — на что отвечаем.
            await db.execute(
                'INSERT INTO mail_in (site_id, profile_id, message_id, uid, body,'
                ' date_post) VALUES (%s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
                (STAND_SITE, girl, uuid.uuid4().int % 10 ** 12, f'rp{step}-{tag}',
                 'his letter', minutes + 5))
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            in_id = int((await db.fetchone())['id'])
            await db.execute(
                'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid, name,'
                " body, status, sent_at) VALUES (%s, %s, %s, %s, %s, 'reply',"
                " 'sent', NOW() - INTERVAL %s MINUTE)",
                (STAND_SITE, girl, in_id, f'rp{step}-{tag}', 'Reply', minutes))

        # По неудаче в каждую таблицу: осталась в пуле с причиной.
        await db.execute(
            'INSERT INTO mailing (site_id, profile_id, mail_id, uid, name, body,'
            " status, error) VALUES (%s, %s, %s, %s, 'Bad', 'letter', 'draft',"
            " 'сайт молчит')", (STAND_SITE, girl, mail_id, f'mlx-{tag}'))
        await db.execute(
            'INSERT INTO mail_in (site_id, profile_id, message_id, uid, body,'
            ' date_post) VALUES (%s, %s, %s, %s, %s, NOW())',
            (STAND_SITE, girl, uuid.uuid4().int % 10 ** 12, f'rpx-{tag}', 'his'))
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        bad_in = int((await db.fetchone())['id'])
        await db.execute(
            'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid, name,'
            " body, status, error) VALUES (%s, %s, %s, %s, 'Bad', 'reply',"
            " 'draft', 'сайт молчит')", (STAND_SITE, girl, bad_in, f'rpx-{tag}'))

        # ⚠ И нетронутый черновик: в журнале доставки ему места нет.
        await db.execute(
            'INSERT INTO mailing (site_id, profile_id, mail_id, uid, name, body,'
            " status) VALUES (%s, %s, %s, %s, 'Untouched', 'letter', 'draft')",
            (STAND_SITE, girl, mail_id, f'mlq-{tag}'))
        await db.connection.commit()

    yield {'girl': girl, 'tag': tag, 'mail_id': mail_id}

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM mailing WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM mail_reply WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM mail_in WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM mail WHERE id = %s', (mail_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
        await db.connection.commit()


def _keys(got: dict) -> list:
    """Строки как пары «род и номер» — ими сверяются страницы между собой."""
    return [(one['kind'], one['id']) for one in got['result']]


class TestBothSourcesAreThere:
    """⚠ Журнал показывает оба источника: иначе половина ушедшего пропадает."""

    async def test_letters_and_replies_both_come(self, journal):
        got = await mailing_delivery(site_id=STAND_SITE, per=100, user=None)
        kinds = {one['kind'] for one in got['result']}

        assert kinds == {'mailing', 'reply'}, kinds

    async def test_the_kind_comes_from_the_server(self, journal):
        """⚠ Метку рода ставит сервер, а не угадывает страница.

        Карточки выглядят одинаково («Diana → Semen»), а вещи разные: первое ушло
        незнакомому по шаблону, второе написано на его письмо.
        """
        got = await mailing_delivery(site_id=STAND_SITE, per=100, user=None)

        assert all(one.get('kind') in ('mailing', 'reply')
                   for one in got['result'])

    async def test_the_failed_ones_are_there(self, journal):
        """⚠ Неудача **оставляет письмо в пуле** (`draft` с причиной): спроси ручка
        по статусам, журнал молчал бы ровно о том, ради чего открыт."""
        got = await mailing_delivery(site_id=STAND_SITE, per=100, user=None)
        bad = [one for one in got['result'] if one['status'] == 'draft']

        assert len(bad) == 2, bad
        assert {one['kind'] for one in bad} == {'mailing', 'reply'}

    async def test_the_untouched_draft_is_not_delivery(self, journal):
        """Его не отправляли — ему место в пуле, а не в журнале."""
        got = await mailing_delivery(site_id=STAND_SITE, per=100, user=None)

        assert f"mlq-{journal['tag']}" not in [one['uid'] for one in got['result']]


class TestTheOrderIsByEvent:
    """⚠ Свежие сверху, и время — **события**, а не номера строки."""

    async def test_fresh_on_top(self, journal):
        got = await mailing_delivery(site_id=STAND_SITE, per=100, user=None)
        when = [str(one.get('sent_at') or one.get('created_at') or '')
                for one in got['result']]

        assert when == sorted(when, reverse=True), when[:4]

    async def test_sources_are_interleaved(self, journal):
        """⚠⚠ Родá **перемешаны**, а не идут блоками.

        Это и есть проверка слияния по существу: склей ручка списки один за другим —
        порядок по времени сломался бы, а счёт и число страниц остались бы верными.
        Заметить такое можно только здесь.
        """
        got = await mailing_delivery(site_id=STAND_SITE, per=100, user=None)
        # Берём только ушедшие: у них времена заданы фикстурой с чередованием.
        kinds = [one['kind'] for one in got['result'] if one['status'] == 'sent']

        assert len(set(kinds)) == 2, kinds
        # Хоть одна смена рода внутри последовательности — значит списки слиты, а
        # не приклеены друг к другу.
        assert any(a != b for a, b in zip(kinds, kinds[1:])), \
            f'родá идут блоками — слияния нет: {kinds}'


class TestPages:
    """Страницы считаются по всему журналу и не пересекаются."""

    async def test_the_count_is_the_whole_journal(self, journal):
        """⚠⚠ Считаем **свои строки и арифметику**, а не абсолютное число.

        Журнал — по сайту, а стенд общий: другие файлы сюиты оставляют на нём свои
        письма, и `total == 8` краснело бы от соседа, ничего не проверяя по существу
        (`testing.md`, «Общий счётчик стенда — источник плавающих отказов»).

        Проверяется то, что действительно про эту ручку: свои восемь строк в журнале
        есть, страница не длиннее заказанного, а число страниц отвечает счёту.
        """
        got = await mailing_delivery(site_id=STAND_SITE, per=3, user=None)
        whole = await mailing_delivery(site_id=STAND_SITE, per=200, user=None)

        mine = [one for one in whole['result']
                if str(one['uid']).endswith(journal['tag'])]

        # Шесть ушедших и две неудачные; нетронутый черновик не в счёт.
        assert len(mine) == 8, [one['uid'] for one in mine]
        assert len(got['result']) == 3, 'страница длиннее заказанного'
        assert got['pages'] == max(1, -(-got['total'] // 3)), \
            f"страниц {got['pages']} при счёте {got['total']}"
        assert got['total'] >= 8, got['total']

    async def test_pages_do_not_overlap(self, journal):
        first = _keys(await mailing_delivery(site_id=STAND_SITE, per=3, page=1,
                                             user=None))
        second = _keys(await mailing_delivery(site_id=STAND_SITE, per=3, page=2,
                                              user=None))

        assert set(first) & set(second) == set()

    async def test_all_pages_together_equal_the_journal(self, journal):
        """⚠⚠⚠ **Главная проверка приёма**: все страницы подряд равны журналу.

        Приём такой: чтобы отдать первые `page * per` строк, берём по столько же
        свежих из каждой таблицы, сливаем и режем. Ошибка тут молчит — страница
        показывает не те строки, и заметить это можно только сверкой.

        ⚠⚠ **Проверять надо ВСЕ страницы, а не первые две.** Первая версия сверяла
        две, и подмена `take = page * per` на `take = per` её прошла: на первой
        странице они равны, на второй шесть строк из двух таблиц случайно сошлись.
        Поймал поломку другой тест — «последняя страница пуста», то есть окольно.
        Теперь ловит эта, и прямо: дальние страницы — ровно то место, где `take`
        и решает.
        """
        whole = _keys(await mailing_delivery(site_id=STAND_SITE, per=200, user=None))

        by_pages = []
        page = 1
        while True:
            got = await mailing_delivery(site_id=STAND_SITE, per=3, page=page,
                                         user=None)
            by_pages.extend(_keys(got))
            if page >= got['pages']:
                break
            page += 1

        assert by_pages == whole, \
            (f'страницы не складываются в журнал: по страницам {len(by_pages)} '
             f'строк, целиком {len(whole)}; первое расхождение — '
             f'{next((i for i, (a, b) in enumerate(zip(by_pages, whole)) if a != b),
                     "нет, но длины разные")}')

    async def test_a_page_past_the_end_comes_back(self, journal):
        """⚠ Правим, а не отвергаем: журнал мог укоротиться, пока человек листал."""
        got = await mailing_delivery(site_id=STAND_SITE, per=3, page=99, user=None)

        assert got['page'] == got['pages'] and got['result']

    async def test_the_default_page_size_is_shared(self):
        """⚠ То же число, что у двух соседних журналов доставки: вкладки выглядят
        одинаково и листаются одним пейджером."""
        assert MAILING_DELIVERY_PER == 25

    async def test_the_empty_site_answers_with_pager_fields(self):
        """⚠ Поля пейджера есть и в пустом ответе: страница читает их без проверок,
        и `pages: undefined` нарисовало бы пейджер из одной мёртвой страницы."""
        got = await mailing_delivery(site_id=0, user=None)

        assert got['pages'] == 1 and got['page'] == 1 and got['total'] == 0


class TestTheReplyRowIsWhole:
    """⚠⚠ У ответа обе стороны с лицом — как у писем рассылки.

    `_reply_answer` служит и «Входящим», где сторону девушки читают как
    `girl`/`girl_photo`, а общая строка журнала — как `profile_name`/`profile_photo`.
    Без приведения на границе лицо девушки в журнале пропадало бы **молча**: имя
    есть, фото пусто.
    """

    async def test_the_reply_has_profile_fields(self, journal):
        got = await mailing_delivery(site_id=STAND_SITE, per=100, user=None)
        reply = next(one for one in got['result'] if one['kind'] == 'reply')

        assert 'profile_name' in reply and 'profile_photo' in reply
        assert reply['profile_name'] == reply['girl'], \
            'имя анкеты у ответа разошлось с тем, что читают «Входящие»'

    async def test_the_letter_has_them_too(self, journal):
        """У рассылки эти поля кладёт общий кусок (`pool_pair_girl`) — сверяем, что
        оба источника отвечают одними именами."""
        got = await mailing_delivery(site_id=STAND_SITE, per=100, user=None)
        letter = next(one for one in got['result'] if one['kind'] == 'mailing')

        assert 'profile_name' in letter and 'profile_photo' in letter


class TestOnePatternForThreeJournals:
    """⚠⚠⚠ Строку рисует **одна** выкройка на три журнала, а не три копии.

    Копий было три — инвайты, письма избранным, письма, — и они разошлись: у писем
    не стало ни лиц, ни времени неудачи, ни обрезки текста, зато время уехало
    вправо, а причина встала отдельной строкой под каждой записью. Человек, переходя
    между вкладками, читал их как три разных списка.
    """

    def _page(self) -> str:
        import pathlib

        return (pathlib.Path(__file__).resolve().parents[1]
                / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text(encoding='utf-8')

    def test_there_is_exactly_one_row_markup(self):
        page = self._page()

        assert page.count('mail-sent-row plain one') == 1, \
            'разметка строки журнала снова в нескольких местах'

    def test_all_three_journals_use_it(self):
        page = self._page()

        for what in ('sentRows(inviteSent', 'sentRows(favSent', 'sentRows(mailSent'):
            assert what in page, f'журнал рисует строки сам: нет {what}'

    def test_the_differences_are_named_options(self):
        """⚠ Различия остались, но каждое — довод с именем, а не своя копия.

        `kind` — два источника в одном журнале, `pick` — отбор по имени у
        растущего журнала инвайтов.

        ⚠ Довода `pool` («осталось в пуле») больше нет: пометку убрали, и довод
        остался бы обещанием, которого выкройка не исполняет.
        """
        page = self._page()

        assert 'kind: true' in page, \
            'своего довода у журнала писем нет — значит чипс рода потерян'
        # ⚠ Довода `pool` больше нет: пометку «осталось в пуле» убрали 26.09.2026.
        # Проверяем это прямо — иначе тест молчал бы о её возврате.
        assert 'pool: true' not in page, \
            'довод `pool` вернулся, а пометку под него убрали'
        assert 'pick: (what, value)' in page, \
            'отбор инвайтов потерян при сведении к общей выкройке'

    def test_the_mail_journal_has_a_pager(self):
        import pathlib

        base = (pathlib.Path(__file__).resolve().parents[1]
                / 'data/uvicorn/template/admin/dating/profile/site/_base.html'
                ).read_text(encoding='utf-8')

        assert 'data-slot="mail-sent-pager"' in base, 'подвала с пейджером нет'
