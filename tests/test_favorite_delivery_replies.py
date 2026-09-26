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

## ⚠⚠ Журналы делят ответы, а не показывают каждый своё

Пары из звёздочек — здесь, все прочие — в «Письмах». Вместе это все ответы ровно по
одному разу.

Сперва было иначе: строка честно попадала в оба журнала — «это журнал, а не пул», одно
событие с двух сторон. Человек это отменил, и довод его сильнее: ответ избранному ищут
на «Фаворитах», а второй список того же самого лишь заставляет сверять их между собой.

Поэтому здесь два сторожа сразу: строка **пришла** в журнал избранных и **ушла** из
журнала писем — а третий проверяет, что по дороге она не потерялась совсем.

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


class TestTheJournalsSplitTheReplies:
    """⚠⚠⚠ Два журнала **делят** ответы: ни одного дважды, ни одного нигде.

    Сперва было иначе: строка честно попадала в оба журнала — «это журнал, а не
    пул», одно событие с двух сторон. Человек это отменил: ответ избранному ищут на
    «Фаворитах», а второй список того же самого лишь заставляет сверять их между собой.

    ## ⚠ Почему проверок три, а не одна

    «Не дважды» и «не нигде» — разные беды, и вторая хуже. Двойной показ виден глазом:
    человек замечает знакомую строку и спрашивает. Пропажа не видна вовсе — письмо
    ушло, а журнал о нём молчит, и узнать об этом можно только из базы.

    Отсюда третья проверка, самая важная: **сумма половин равна всем ответам**.
    Разойдись правила половин хоть на одну пару — она выпадет из обоих журналов.
    """

    async def test_the_favorite_reply_is_only_in_the_favorites(self, journal):
        got = await mailing_delivery(site_id=STAND_SITE, per=200, user=None)

        assert journal['star'] not in [one['uid'] for one in got['result']], \
            'ответ избранному снова виден и в журнале писем'

    async def test_the_stranger_reply_is_only_in_the_mail(self, journal):
        """⚠ Обратная сторона: ответ незнакомцу из «Писем» не пропал.

        Половины разделены **знаком одного условия**, и ошибка в нём убрала бы из
        журнала писем все ответы разом, а не только избранных.
        """
        fav = await favorite_mail_delivery(site_id=STAND_SITE, per=200, user=None)
        mail = await mailing_delivery(site_id=STAND_SITE, per=200, user=None)

        assert journal['alien'] not in [one['uid'] for one in fav['result']], \
            'ответ незнакомцу приехал в журнал избранных'
        assert journal['alien'] in [one['uid'] for one in mail['result']], \
            'журнал писем потерял ответ незнакомцу'

    async def test_the_halves_add_up_to_everything(self, journal):
        """⚠⚠⚠ Сумма половин = все ответы сайта, и каждый ровно раз.

        Считается **по базе**, а не по ожидаемым числам: тест, сверяющий журналы лишь
        между собой, прошёл бы и при двух одинаково неверных правилах.
        """
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id FROM mail_reply WHERE site_id = %s'
                "   AND (status IN ('sent', 'failed')"
                "        OR (status = 'draft' AND error <> ''))",
                (STAND_SITE,))
            whole = {int(row['id']) for row in (await db.fetchall()) or []}

        fav = await favorite_mail_delivery(site_id=STAND_SITE, per=500, user=None)
        mail = await mailing_delivery(site_id=STAND_SITE, per=500, user=None)

        stars = [one['id'] for one in fav['result'] if one['kind'] == 'reply']
        others = [one['id'] for one in mail['result'] if one['kind'] == 'reply']

        assert not (set(stars) & set(others)), \
            f'одни и те же ответы в обоих журналах: {set(stars) & set(others)}'
        assert set(stars) | set(others) == whole, \
            ('ответы потерялись между журналами: '
             f'{whole - (set(stars) | set(others))}')
        assert len(stars) + len(others) == len(whole), \
            'ответ показан дважды внутри одного журнала'


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


class TestTheReplyHasATimeOfOutcome:
    """⚠⚠ У неудачного ответа есть **своё** время, а не время сочинения.

    До миграции `20260926200000` в `mail_reply` был один столбец времени — `sent_at`,
    «когда ушло», — и у неудачи его нет и быть не может: отметку снимает сама неудача.
    Показ падал до `created_at`, то есть до минуты, когда черновик **сочинили**, а
    между сочинением и сорвавшейся отправкой бывают часы: пул набирает один круг,
    уносит письма другой по своему расписанию.

    ⚠ Ровно та же беда была у писем избранным, и её уже чинили (`20260922100000`).
    Здесь она вернулась с другой стороны — ответы приехали в тот же журнал.
    """

    async def test_the_column_allows_null_without_a_default(self):
        """⚠⚠⚠ Главное в миграции — **вид** столбца, а не его наличие.

        Заведённый `NOT NULL DEFAULT CURRENT_TIMESTAMP`, он получает от `ALTER` время
        самой миграции **всем накопленным строкам**. Так уже вышло у писем избранным
        четыре дня назад: письмо, не ушедшее 21 сентября в 16:02, показалось как
        «22 сентября 08:47». Пара к тому времени лежала в «Не пишем», и красная строка
        со свежей датой читается одним способом — «код опять пишет запрещённому».

        Неизвестно — значит `NULL`: показ сам возьмёт `created_at`.
        """
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT IS_NULLABLE, COLUMN_DEFAULT, EXTRA'
                ' FROM information_schema.COLUMNS'
                ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s'
                '   AND COLUMN_NAME = %s', ('mail_reply', 'updated_at'))
            col = await db.fetchone()

        assert col, 'столбца `updated_at` у `mail_reply` нет — миграция не дошла'
        assert col['IS_NULLABLE'] == 'YES', \
            'столбец не допускает NULL — ложную отметку будет нечем снять'
        assert col['COLUMN_DEFAULT'] is None, \
            'у столбца появилось умолчание — накопленные строки получат дату миграции'
        assert 'on update' in str(col['EXTRA']).lower(), \
            'нет `ON UPDATE CURRENT_TIMESTAMP` — отметку придётся писать руками'

    async def test_a_failed_reply_carries_the_time(self, journal):
        """То, что видит человек: у красной строки журнала есть дата."""
        async with mysql_get_db_async() as db:
            await db.execute(
                "UPDATE mail_reply SET status = 'failed', sent_at = NULL,"
                " error = 'сайт отверг' WHERE profile_id = %s AND uid = %s",
                (journal['girl'], journal['star']))
            await db.connection.commit()

        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        bad = next(one for one in got['result'] if one['kind'] == 'reply')

        assert not bad['sent_at'], 'у неудачи не должно быть времени отправки'
        assert bad['updated_at'], \
            'времени исхода нет — колонка в журнале останется пустой'

    async def test_a_sent_reply_still_shows_its_send_time(self, journal):
        """⚠ Обратная сторона: у ушедшего ответа показывается именно отправка."""
        got = await favorite_mail_delivery(site_id=STAND_SITE, per=100, user=None)
        ok = next(one for one in got['result']
                  if one['kind'] == 'reply' and one['status'] == 'sent')

        assert ok['sent_at'], 'у ушедшего ответа пропало время отправки'

    def test_the_order_key_is_the_same_in_three_places(self):
        """⚠⚠ Один ключ времени в SQL ответов, в SQL писем и в срезе слияния.

        По этому ключу зовущий **режет страницу** из двух слитых списков: разойдись
        порядок в запросе с порядком в срезе — страница молча показала бы не те
        строки, и заметить это можно только сверкой с полным журналом.
        """
        import inspect

        from src.dating import mail_reply as domain
        from src.uvicorn.routers.dating import favorite_mail as fav
        from src.uvicorn.routers.dating import mailing as mail

        assert 'COALESCE(r.sent_at, r.updated_at, r.created_at)' in \
            inspect.getsource(domain.mail_reply_delivery), \
            'ответы снова сортируются без времени исхода'
        assert 'COALESCE(m.sent_at, m.updated_at, m.created_at)' in \
            inspect.getsource(fav.favorite_mail_delivery), \
            'письма избранным сортируются другим ключом'

        for code in (inspect.getsource(fav.favorite_mail_delivery),
                     inspect.getsource(mail.mailing_delivery)):
            assert "one.get('updated_at')" in code, \
                'срез слияния не знает про время исхода — порядок разойдётся с SQL'
