"""«Фавориты → Доставка»: журнал показывает и приставание, и ответы избранным.

Вкладка «Фавориты» ведёт занятие целиком: кому пишем (лента и пул «Приставание»), о
чём говорим («Диалоги»), что ушло («Доставка»). Ответы на письма стоят в том же
ряду — **третьей колонкой «Диалогов»**, — а их исход был виден только на «Письмах →
Доставка»: работа делается на одной вкладке, а её итог показывается на другой.

## ⚠⚠ Это не совпадение, а устройство

Мера, снятая с прода: **из четырёх ушедших ответов все четыре** — паре, которая есть
в избранных. Иначе и быть не может: звёздочку жмёт круг избранных по входящему письму
(`favorite_round`), ответ пишет круг ответов по **тому же** письму. Один повод — два
занятия, и получатель у них один.

## ⚠ Строка остаётся и в журнале писем, и это правильно

Это **журнал, а не пул**: одно событие видно с двух сторон — «что ушло избранным» и
«что вообще уходило почтой». Двойной работы тут нет, в отличие от двойного **пула**,
которым эту вкладку уже дважды путали (см. оговорку у `favTalkEdit`).

Поэтому здесь два сторожа сразу: строка **пришла** в журнал избранных и **осталась** в
журнале писем.

## ⚠ Ответ незнакомому сюда не попадает

Пары нет в избранных — на этой вкладке его не с чем связать: ни разговора в списке, ни
звёздочки. Такой ответ виден на «Письмах», и только там.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating.favorite_mail import favorite_mail_delivery
from src.uvicorn.routers.dating.mailing import mailing_delivery

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


@pytest.fixture
async def journal(auth_client):
    """Три ушедших: приставание, ответ избранному и ответ незнакомому.

    ⚠ Времена **чередуются** (10, 20, 30 минут назад) — иначе порядок сошёлся бы
    случайно при любом слиянии.

    ⚠ Плюс неудачное приставание (`draft` с причиной): журнал обязан показать и его.
    """
    girl = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'favdlv-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    tag = uuid.uuid4().hex[:6]
    star, alien = f'star-{tag}', f'alien-{tag}'

    async with mysql_get_db_async() as db:
        # Избранный — только первый из двоих.
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            " VALUES (%s, %s, %s, 'done')", (STAND_SITE, girl, star))

        # Приставание избранному — ушло 10 минут назад.
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status, sent_at) VALUES (%s, %s, %s, 'Star', 'пристаю',"
            " 'sent', NOW() - INTERVAL 10 MINUTE)", (STAND_SITE, girl, star))

        # Ответы: избранному (20 минут) и незнакомцу (30 минут).
        for uid, minutes in ((star, 20), (alien, 30)):
            await db.execute(
                'INSERT INTO mail_in (site_id, profile_id, message_id, uid, body,'
                ' date_post) VALUES (%s, %s, %s, %s, %s,'
                ' NOW() - INTERVAL %s MINUTE)',
                (STAND_SITE, girl, uuid.uuid4().int % 10 ** 12, uid,
                 f'письмо от {uid}', minutes + 5))
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            in_id = int((await db.fetchone())['id'])
            await db.execute(
                'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid, name,'
                " body, status, sent_at) VALUES (%s, %s, %s, %s, 'Him', 'отвечаю',"
                " 'sent', NOW() - INTERVAL %s MINUTE)",
                (STAND_SITE, girl, in_id, uid, minutes))

        # Неудачное приставание: осталось в пуле с причиной.
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status, error) VALUES (%s, %s, %s, 'Star', 'пристаю', 'draft',"
            " 'сайт молчит')", (STAND_SITE, girl, star))

        # ⚠ И нетронутый черновик: в журнале доставки ему места нет.
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status) VALUES (%s, %s, %s, 'Star', 'пристаю', 'draft')",
            (STAND_SITE, girl, f'quiet-{tag}'))
        await db.connection.commit()

    yield {'girl': girl, 'tag': tag, 'star': star, 'alien': alien}

    async with mysql_get_db_async() as db:
        for table in ('favorite_mail', 'mail_reply', 'mail_in', 'favorite'):
            await db.execute(f'DELETE FROM {table} WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
        await db.connection.commit()


class TestBothSourcesAreThere:
    """⚠⚠ В журнале два рода строк: приставание и ответ."""

    async def test_the_pestering_is_there(self, journal):
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        kinds = [one['kind'] for one in got['result']]

        assert 'favorite' in kinds, kinds

    async def test_the_reply_to_a_favorite_is_there(self, journal):
        """⚠ Ровно то, чего журналу не хватало: ответ виден там, где его писали."""
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        replies = [one for one in got['result'] if one['kind'] == 'reply']

        assert [one['uid'] for one in replies] == [journal['star']], \
            'ответ избранному в журнал не попал'

    async def test_the_reply_to_a_stranger_is_not(self, journal):
        """⚠ Пары нет в избранных — на этой вкладке его не с чем связать."""
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)

        assert journal['alien'] not in [one['uid'] for one in got['result']], \
            'в журнал избранных приехал ответ незнакомцу'

    async def test_the_kind_comes_from_the_server(self, journal):
        """⚠ Метку рода ставит сервер: строки выглядят одинаково («Diana → Semen»),
        а вещи разные — приставание мы начали сами, ответ написан на его письмо."""
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)

        assert all(one.get('kind') in ('favorite', 'reply')
                   for one in got['result'])

    async def test_the_failed_pestering_is_there(self, journal):
        """⚠ Неудача **оставляет письмо в пуле** (`draft` с причиной): спроси ручка
        по статусам, журнал молчал бы ровно о том, ради чего его открывают."""
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        bad = [one for one in got['result'] if one['status'] == 'draft']

        assert len(bad) == 1 and bad[0]['error'] == 'сайт молчит', bad

    async def test_the_untouched_draft_is_not_delivery(self, journal):
        """Его не отправляли — ему место в пуле, а не в журнале."""
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)

        assert f"quiet-{journal['tag']}" not in [one['uid']
                                                 for one in got['result']]


class TestTheRowStaysInTheMailJournal:
    """⚠⚠ Сторож обратного: журнал писем ответа не потерял.

    Это журналы, а не пулы: одно событие видно с двух сторон. Унеси мы строку на
    «Фаворитов» — «Письма → Доставка» перестали бы отвечать на «что вообще уходило».
    """

    async def test_the_mail_journal_still_shows_both_replies(self, journal):
        got = await mailing_delivery(site_id=STAND_SITE, per=200, user=None)
        mine = [one['uid'] for one in got['result']
                if one['uid'] in (journal['star'], journal['alien'])]

        assert sorted(mine) == sorted([journal['star'], journal['alien']]), \
            'журнал писем потерял ответы'


class TestTheOrderIsByEvent:
    """⚠ Свежие сверху, и время — **события**, а не номера строки."""

    async def test_fresh_on_top(self, journal):
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        when = [str(one.get('sent_at') or one.get('updated_at')
                    or one.get('created_at') or '')
                for one in got['result']]

        assert when == sorted(when, reverse=True), when

    async def test_the_sources_are_interleaved(self, journal):
        """⚠⚠ Родам не место кучками: слияние, отдающее сперва все приставания, а
        потом все ответы, прошло бы проверку «оба источника есть» и соврало бы о том,
        что происходило по времени.

        Здесь приставание ушло 10 минут назад, ответ — 20, неудача записана сейчас:
        порядок обязан идти по времени, а не по таблицам.
        """
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        kinds = [one['kind'] for one in got['result']]

        assert kinds.index('reply') > 0, \
            'ответ встал выше свежего приставания — слияние идёт по таблицам'


class TestThePaging:
    """⚠ Счёт и страницы — по **двум** таблицам, иначе пейджер соврёт."""

    async def test_the_total_counts_both(self, journal):
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)

        # приставание ушедшее + неудачное + ответ избранному
        assert got['total'] == 3, got['total']

    async def test_pages_split_the_journal(self, journal):
        first = await favorite_mail_delivery(site_id=STAND_SITE, per=2, page=1,
                                             user=None)
        second = await favorite_mail_delivery(site_id=STAND_SITE, per=2, page=2,
                                             user=None)

        assert first['pages'] == 2 and len(first['result']) == 2
        assert len(second['result']) == 1

    async def test_the_slice_matches_the_whole(self, journal):
        """⚠⚠ Сторож **среза слияния**: страница 1 плюс страница 2 равны первым трём
        строкам полного журнала. Ошибись в приёме — страница молча покажет не те
        строки, и заметить это можно только такой сверкой."""
        whole = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        first = await favorite_mail_delivery(site_id=STAND_SITE, per=2, page=1,
                                             user=None)
        second = await favorite_mail_delivery(site_id=STAND_SITE, per=2, page=2,
                                             user=None)

        def keys(got):
            return [(one['kind'], one['id']) for one in got['result']]

        assert keys(first) + keys(second) == keys(whole)[:3]

    async def test_a_page_past_the_end_gives_the_last(self, journal):
        """⚠ Страницу поправляем, а не отвергаем: журнал мог укоротиться, пока
        человек листал, и отказ читался бы как поломка."""
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=2, page=99,
                                           user=None)

        assert got['page'] == got['pages'] and got['result']

    async def test_no_site_gives_the_pager_fields(self, journal):
        """⚠ Поля пейджера есть и в пустом ответе: `pages: undefined` нарисовало бы
        пейджер из одной мёртвой страницы."""
        got = await favorite_mail_delivery(site_id=0, user=None)

        assert got == {'result': [], 'page': 1, 'pages': 1, 'total': 0}


class TestTheProfileIsAFilter:
    """⚠ Анкета — фильтр, как у списка разговоров на той же вкладке."""

    async def test_the_filter_narrows_both_sources(self, journal):
        mine = await favorite_mail_delivery(site_id=STAND_SITE,
                                            profile_id=journal['girl'],
                                            per=100, user=None)
        other = await favorite_mail_delivery(site_id=STAND_SITE,
                                             profile_id=journal['girl'] + 10 ** 6,
                                             per=100, user=None)

        assert {one['kind'] for one in mine['result']} == {'favorite', 'reply'}
        assert other['result'] == [] and other['total'] == 0

    async def test_the_filter_is_counted_too(self, journal):
        """⚠⚠ Счёт обязан слушать тот же фильтр, что и выборка: иначе пейджер
        обещает страницы, на которых ничего нет."""
        mine = await favorite_mail_delivery(site_id=STAND_SITE,
                                            profile_id=journal['girl'],
                                            per=2, user=None)

        assert mine['total'] == 3 and mine['pages'] == 2


class TestTheRowIsReadyToDraw:
    """⚠ Строка приезжает готовой: общая выкройка журнала не считает ничего сама."""

    async def test_both_sides_are_named(self, journal):
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)

        for one in got['result']:
            assert 'profile_name' in one and 'profile_photo' in one, one.keys()
            assert one.get('name') is not None

    async def test_the_reply_carries_the_letter_it_answers(self, journal):
        """⚠ Окно записи показывает «На его письмо»: ответ читают, сверяя с вопросом,
        и без него «Sure, let's meet» не отличить от несуразицы."""
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        reply = next(one for one in got['result'] if one['kind'] == 'reply')

        assert reply['in_body'] == f"письмо от {journal['star']}"


class TestThePageAsksTheNewHandler:
    """⚠ Сторож со стороны страницы: журнал зовёт **свою** ручку и просит чипс рода.

    Сервер может сливать источники сколько угодно — если страница по-прежнему
    спрашивает пул с `status_=delivery`, человек не увидит ни одного ответа. А
    попросив слияние без `kind`, увидит их **без метки**: строки «Diana → Semen»
    одинаковы, и приставание не отличить от ответа.
    """

    def test_the_journal_asks_the_delivery_handler(self):
        import pathlib
        import re

        page = (pathlib.Path(__file__).resolve().parents[1]
                / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text(encoding='utf-8')
        body = re.search(r'async function favSentLoad\(\) \{.*?\n    \}',
                         page, re.S).group(0)

        assert '/api/dating/favorite-mail/delivery' in body, \
            'журнал избранных снова спрашивает пул — ответов в нём не будет'
        assert 'status_=delivery' not in body, \
            'остался прежний отбор по состоянию: слияния он не даёт'

    def test_the_rows_ask_for_the_kind_chip(self):
        import pathlib
        import re

        page = (pathlib.Path(__file__).resolve().parents[1]
                / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text(encoding='utf-8')
        body = re.search(r'function favSentPaint\(\) \{.*?\n    \}',
                         page, re.S).group(0)

        assert 'kind: true' in body, \
            'чипс рода не просят — ответ не отличить от приставания'

    def test_the_window_names_the_kind(self):
        """⚠ И заголовок окна тоже: «Письмо избранному» над ответом на его же письмо
        соврал бы о том, кто начал разговор."""
        import pathlib
        import re

        page = (pathlib.Path(__file__).resolve().parents[1]
                / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text(encoding='utf-8')
        body = re.search(r'function favSentOpen\(index\) \{.*?\n    \}',
                         page, re.S).group(0)

        assert "row.kind === 'reply'" in body, 'окно не различает род письма'
        assert 'Ответ избранному' in body, 'у ответа прежний заголовок письма'
