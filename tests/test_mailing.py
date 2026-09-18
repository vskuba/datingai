"""Пул писем: подготовленные, но ещё не отправленные.

⚠ **Наружу сюита не ходит** (`testing.md`): ни к соседу, ни на сайт. Поэтому все
проверки здесь — на **выдуманном сайте**, у которого нет ни анкет, ни снимка
онлайна. Генерация на нём честно возвращает «ноль писем» и до агента не доходит:
`live`-переменная спрашивается только у пары «анкета — адресат», а пар нет.

Это не обедняет проверку, а нацеливает её. Ломается в пуле не текст письма —
текст пишет агент, и его проверяет workflow у соседа. Ломается обвязка: кому
видно, что предел считает, не падает ли ручка на пустом месте, переживает ли
правка перезагрузку страницы.

Живьём пул проверен вручную на Goldenbride: 50 писем от анкеты Diana по одному
шаблону, имена подставлены у всех, вопросов получателю 46 разных на 50 писем.
"""

import uuid

import pytest

pytestmark = pytest.mark.mail
# Сайт для шаблонов и генерации — выдуманный: у соседа его нет, анкет с рассылкой
# на нём нет тоже, и генерация честно возвращает ноль писем, не позвав ни агента,
# ни сайт. Живой пул задеть невозможно в принципе.
SITE = 999_777

# Сайт для анкеты — стенд у соседа: заведение учётки сверяет номер, и выдуманный
# отвергает. ⚠ Тот же приём, что в `test_mail_var.py`: `mailing.profile_id`
# — внешний ключ, и без настоящей анкеты письмо в базу не ляжет.
#
# ⚠ **Стенд, а не живой сайт.** Закладка `999343` есть у соседа, но не помечена
# `dating`: заведение анкеты её примет (оно сверяет номер), а в селект сайтов и в
# `site_` она не попадёт — значит ни один круг воркера про эту анкету не
# узнает. Пока здесь стоял живой 343, тестовая анкета попадала в круги избранных и
# инвайтов наравне с рабочими (`test_mail_in.py`, там же подробности).
STAND_SITE = 999_343

# ⚠ **Анкета живёт на стенде, а письма кладём на выдуманный сайт.** Ничто их не
# связывает: `site_id` у письма — просто число, внешнего ключа на нём нет.
#
# Разведены они не для красоты. Пока письма лежали на живом сайте, проверка очистки
# сносила **настоящий пул**: `DELETE ?site_id=343` не разбирает, чьё письмо тестовое
# — и однажды унесла полсотни писем, набранных руками. Сюита не должна трогать то,
# с чем работают люди.


@pytest.fixture
async def profile(auth_client):
    """Анкета выдуманного сайта: письмо без неё в базу не ляжет.

    ⚠ `mailing.profile_id` — внешний ключ с `ON DELETE CASCADE`, и это не
    формальность: письмо от удалённой анкеты отправить невозможно, а лежать в
    пуле ему незачем. Каскад же убирает за этой фикстурой всё разом.
    """
    created = await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE, 'login': f'pool-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})
    made = created.json()['result']

    yield made

    await auth_client.delete(f'/api/dating/profile/{made}')


@pytest.fixture
async def letter(auth_client, profile):
    """Кладёт письмо в пул напрямую и убирает его за собой.

    ⚠ Через базу, а не через `/generate`: генерация требует адресатов в сети —
    снимка ленты, которого у выдуманного сайта нет и не должно быть. Проверять
    правку письма ею значило бы проверять заодно полсотни чужих условий.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mailing'
            ' (site_id, profile_id, mail_id, uid, name, body, status)'
            " VALUES (%s, %s, NULL, 'tst-uid', 'Тестовый', 'Текст письма', 'draft')",
            (SITE, profile))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        row = await db.fetchone()

    made = int(row['id'])

    yield made

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM mailing WHERE id = %s', (made,))
        await db.connection.commit()


@pytest.fixture
async def template(auth_client):
    """Шаблон выдуманного сайта: без него генерация отказывает на входе."""
    created = await auth_client.post('/api/dating/mail', json={
        'site_id': SITE, 'name': f'tst-{uuid.uuid4().hex[:6]}',
        'body': 'Привет!', 'enabled': True})
    made = created.json()['result']['id']

    yield made

    await auth_client.delete(f'/api/dating/mail/{made}')


class TestPoolList:
    """Чтение пула."""

    async def test_unknown_site_is_empty_not_404(self, auth_client):
        """Сайт, которому писем не готовили, — законное состояние, а не ошибка:
        до первого нажатия «Генерировать» пул пуст у всех."""
        answer = await auth_client.get('/api/dating/mailing?site_id=999999')

        assert answer.status_code == 200
        assert answer.json()['result'] == []

    async def test_letter_comes_back(self, auth_client, letter):
        rows = (await auth_client.get(
            f'/api/dating/mailing?site_id={SITE}')).json()['result']

        assert [row['id'] for row in rows if row['id'] == letter] == [letter]

    async def test_sites_keep_their_own(self, auth_client, letter):
        """Пул одного сайта не виден у другого: письма готовятся от анкет сайта
        его же адресатам.

        ⚠ Спрашиваем **соседний** выдуманный номер, а не тот, где письмо лежит:
        иначе проверка искала бы письмо там, куда его и положили, и проходила бы
        всегда.
        """
        rows = (await auth_client.get(
            '/api/dating/mailing?site_id=999778')).json()['result']

        assert letter not in [row['id'] for row in rows]

    async def test_row_says_who_writes_whom(self, auth_client, letter):
        """⚠ Карточка обязана отвечать «кто → кому» без похода в другие ручки:
        пул читают глазами, и письмо без адресата в нём бессмысленно."""
        rows = (await auth_client.get(
            f'/api/dating/mailing?site_id={SITE}')).json()['result']
        row = next(item for item in rows if item['id'] == letter)

        assert row['name'] == 'Тестовый'
        assert row['body'] == 'Текст письма'
        assert row['status'] == 'draft'
        # Шаблон удалён или не найден — говорим словами, а не пустотой: иначе
        # столбец читается как «письмо без шаблона», чего не бывает.
        assert row['mail_name']


class TestPoolEdit:
    """Правка письма — то, ради чего пул и заведён."""

    async def test_edited_body_is_kept(self, auth_client, letter):
        """Человек читает письмо и правит его. ⚠ Правка обязана пережить
        перезагрузку страницы: пул — не черновик в поле ввода."""
        await auth_client.put(f'/api/dating/mailing/{letter}',
                              json={'body': 'Исправленный текст'})

        rows = (await auth_client.get(
            f'/api/dating/mailing?site_id={SITE}')).json()['result']
        row = next(item for item in rows if item['id'] == letter)

        assert row['body'] == 'Исправленный текст'

    async def test_empty_body_is_refused(self, auth_client, letter):
        """Пустое письмо — не правка, а потеря: отправить его нельзя, а
        восстановить прежний текст неоткуда."""
        answer = await auth_client.put(f'/api/dating/mailing/{letter}',
                                       json={'body': ''})

        assert answer.status_code == 422

    async def test_unknown_letter_is_404(self, auth_client):
        answer = await auth_client.put('/api/dating/mailing/99999999',
                                       json={'body': 'Текст'})

        assert answer.status_code == 404

    async def test_letter_can_be_dropped(self, auth_client, letter):
        """Письмо из пула убирают — это законный исход чтения: агент написал не
        то, и отправлять этого адресату не надо."""
        await auth_client.delete(f'/api/dating/mailing/{letter}')

        rows = (await auth_client.get(
            f'/api/dating/mailing?site_id={SITE}')).json()['result']

        assert letter not in [row['id'] for row in rows]


class TestPoolClear:
    """Очистка пула разом."""

    async def test_clear_removes_drafts(self, auth_client, letter):
        await auth_client.delete(f'/api/dating/mailing?site_id={SITE}')

        rows = (await auth_client.get(
            f'/api/dating/mailing?site_id={SITE}')).json()['result']

        assert letter not in [row['id'] for row in rows]

    async def test_clear_says_how_many(self, auth_client, letter):
        """Число убранного называется: «очищено» без числа не отличить от
        «очищать было нечего»."""
        answer = await auth_client.delete(
            f'/api/dating/mailing?site_id={SITE}')

        assert answer.json()['result']['dropped'] >= 1

    async def test_clear_keeps_the_sent(self, auth_client, profile):
        """⚠ **Ушедшее письмо очистка не трогает.** Оно уже случилось, и стереть
        запись значило бы соврать журналу — то же правило, что у удаления по
        одному.
        """
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO mailing'
                ' (site_id, profile_id, mail_id, uid, name, body, status, sent_at)'
                " VALUES (%s, %s, NULL, 'tst-sent', 'Ушло', 'Текст', 'sent', NOW())",
                (SITE, profile))
            await db.connection.commit()
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            sent = int((await db.fetchone())['id'])

        try:
            await auth_client.delete(f'/api/dating/mailing?site_id={SITE}')

            rows = (await auth_client.get(
                f'/api/dating/mailing?site_id={SITE}&status_=sent')).json()['result']

            assert sent in [row['id'] for row in rows], 'очистка стёрла журнал'
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM mailing WHERE id = %s', (sent,))
                await db.connection.commit()

    async def test_clear_without_site_is_refused(self, auth_client):
        """⚠ Без сайта — отказ, а не «очистить у всех»: очистка необратима, и
        промах в одну цифру стоил бы чужого пула."""
        answer = await auth_client.delete('/api/dating/mailing')

        assert answer.status_code == 422

    async def test_clear_of_empty_pool_is_fine(self, auth_client):
        """Пустой пул — не ошибка: кнопку могли нажать дважды."""
        answer = await auth_client.delete('/api/dating/mailing?site_id=999999')

        assert answer.status_code == 200
        assert answer.json()['result']['dropped'] == 0


class TestGirlRowIsWhole:
    """⚠ Анкета из набора нужна **целиком**, а не тремя полями.

    Она уходит не только в письмо: под ней **заходят на сайт** за анкетой
    собеседника, а для входа нужны `site_id`, `password` и `password_source`.

    Их и не было — запрос брал `id`, `login`, `profile`, — и первое же нажатие
    «Генерировать письма» падало с `KeyError: 'site_id'`. Ошибка ждала ровно того
    случая, когда среди адресатов попадётся незнакомый: пока все были известны, на
    сайт никто не ходил, и сюита её не видела.
    """

    async def test_fields_needed_for_login_are_there(self, auth_client, profile):
        """Поля входа приходят из набора, а не добираются вторым запросом."""
        from src.dating.mailing import mailing_girls
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            # ⚠ И статус «в сети»: набор берёт только тех, кто сейчас на сайте
            # (`_girls`). Без этого анкета в набор не попадёт, и проверка ниже
            # молча проверяла бы пустоту.
            await db.execute("UPDATE profile SET mailing_on = 1,"
                             " status = 'online' WHERE id = %s", (profile,))
            await db.connection.commit()

        girls = await mailing_girls(STAND_SITE)
        mine = next((one for one in girls if one['id'] == profile), None)

        assert mine, 'анкета с рассылкой не попала в набор'
        for field in ('site_id', 'login', 'password', 'password_source'):
            assert field in mine, f'нет поля {field} — под такой анкетой не войти'

    async def test_a_sleeping_profile_is_not_taken(self, auth_client, profile):
        """⚠ **Письма готовятся только от той, кто сейчас в сети.**

        Письмо уходит от её имени, и сайт показывает получателю отправителя:
        письмо от отсутствующей девушки он открывает, не имея кому ответить.
        Прежде отбор смотрел только на галочку `mailing_on`, и пул набивался от
        всех учёток с ней — включая спящие.
        """
        from mysql_.mysql_ import mysql_get_db_async

        from src.dating.mailing import mailing_girls

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET mailing_on = 1,"
                             " status = 'online' WHERE id = %s", (profile,))
            await db.connection.commit()

        assert any(one['id'] == profile for one in await mailing_girls(STAND_SITE))

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'offline'"
                             ' WHERE id = %s', (profile,))
            await db.connection.commit()

        assert not [one for one in await mailing_girls(STAND_SITE)
                    if one['id'] == profile], \
            'анкета не в сети, а письма от неё готовятся'

    async def test_row_fits_the_collector(self, auth_client, profile):
        """⚠ Проверяем **тем же способом**, каким пользуется сбор описания: он
        читает `profile['site_id']` первой же строкой, и расхождение полей — это
        падение посреди рассылки, а не отказ на входе."""
        from src.dating.mailing import mailing_girls
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            # ⚠ И статус «в сети»: набор берёт только тех, кто сейчас на сайте
            # (`_girls`). Без этого анкета в набор не попадёт, и проверка ниже
            # молча проверяла бы пустоту.
            await db.execute("UPDATE profile SET mailing_on = 1,"
                             " status = 'online' WHERE id = %s", (profile,))
            await db.connection.commit()

        mine = next(one for one in await mailing_girls(STAND_SITE)
                    if one['id'] == profile)

        # Ровно то, что делает `companion_about_fetch` до похода на сайт.
        assert int(mine['site_id']) == STAND_SITE

        from src.dating.ready import password_pick

        password_pick(mine, 'общий')


class TestCardGetsTheFeed:
    """⚠ Карточку заводит **сбор описания**, и лента в неё не попадала.

    Имя со страницы анкеты снимается, а фотография — нет: она уже есть в ленте
    «Все онлайн», и грузить её второй раз незачем. Но щелчок по ленте бывает не
    всегда: адресата берут из пула.

    Так и вышло на живом прогоне: пять собеседников, у всех описание, и ни у
    одного аватарки — в списке они читались как мусор. Теперь перед походом на сайт
    в карточку кладётся то, что знает лента.
    """

    async def test_feed_fields_are_saved(self, auth_client):
        import uuid

        from src.dating.mailing import _from_feed
        from src.uvicorn.routers.dating.companion import companion_get

        uid = f'tst-{uuid.uuid4().hex[:10]}'
        try:
            await _from_feed(SITE, {'uid': uid, 'name': 'Пауло',
                                    'photo': 'https://x/p.jpg', 'age': 60})
            row = await companion_get(SITE, uid)

            assert row['name'] == 'Пауло'
            assert row['photo'] == 'https://x/p.jpg'
            assert row['age'] == 60
        finally:
            from mysql_.mysql_ import mysql_get_db_async

            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM companion WHERE uid = %s', (uid,))
                await db.connection.commit()

    async def test_empty_feed_does_not_erase(self, auth_client):
        """⚠ Пустое из ленты не пишем: оно затёрло бы снятое со страницы анкеты.

        Лента отдаёт разный набор — у части людей фото не догружается, — и заход
        без него не должен стирать то, что уже знаем.
        """
        import uuid

        from src.dating.mailing import _from_feed
        from src.uvicorn.routers.dating.companion import (
            companion_get, companion_touch)

        uid = f'tst-{uuid.uuid4().hex[:10]}'
        try:
            await companion_touch(SITE, uid, name='Пауло', photo='p.jpg')
            await _from_feed(SITE, {'uid': uid, 'name': '', 'photo': '', 'age': 0})
            row = await companion_get(SITE, uid)

            assert row['name'] == 'Пауло', 'пустое имя из ленты затёрло known'
            assert row['photo'] == 'p.jpg'
        finally:
            from mysql_.mysql_ import mysql_get_db_async

            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM companion WHERE uid = %s', (uid,))
                await db.connection.commit()


class TestPoolStatusFilter:
    """Отбор по состоянию — на нём держится вкладка «Доставка»."""

    async def test_several_statuses_at_once(self, auth_client, profile):
        """⚠ **`sent,failed` через запятую**, и это не украшение: вкладка
        «Доставка» отвечает на вопрос «что с письмами», а он один — ушедшие и не
        ушедшие читают вместе, потому что интересует их судьба.
        """
        from mysql_.mysql_ import mysql_get_db_async

        made = []
        async with mysql_get_db_async() as db:
            for status_, uid in (('sent', 'tst-ok'), ('failed', 'tst-bad')):
                await db.execute(
                    'INSERT INTO mailing'
                    ' (site_id, profile_id, mail_id, uid, name, body, status)'
                    ' VALUES (%s, %s, NULL, %s, %s, %s, %s)',
                    (SITE, profile, uid, 'Кому', 'Текст', status_))
                await db.connection.commit()
                await db.execute('SELECT LAST_INSERT_ID() AS id')
                made.append(int((await db.fetchone())['id']))

        try:
            rows = (await auth_client.get(
                f'/api/dating/mailing?site_id={SITE}&status_=sent,failed'
            )).json()['result']
            got = [row['id'] for row in rows]

            assert made[0] in got and made[1] in got, 'запятая не разобралась'
            assert all(row['status'] in ('sent', 'failed') for row in rows), \
                'в журнал доставки попали черновики'
        finally:
            async with mysql_get_db_async() as db:
                for one in made:
                    await db.execute('DELETE FROM mailing WHERE id = %s', (one,))
                await db.connection.commit()

    async def test_junk_status_shows_everything(self, auth_client, letter):
        """⚠ Опечатка в отборе — не повод показывать ошибку вместо писем: имя
        приходит из адреса, и «покажи всё» здесь ближе к правде, чем пустота."""
        rows = (await auth_client.get(
            f'/api/dating/mailing?site_id={SITE}&status_=выдумка')).json()['result']

        assert letter in [row['id'] for row in rows]


class TestPoolGenerate:
    """Набор пула. ⚠ На выдуманном сайте письмам взяться неоткуда — и ручка
    говорит, чего именно не хватает, вместо того чтобы вернуть пустой ноль.

    Ноль писем и отказ читаются по-разному. «Сделано 0» человек понимает как
    «сходили и ничего не нашлось»; на деле же он не включил рассылку ни одной
    анкете, и подсказка «включают её на «Анкетах»» экономит ему полчаса поисков.
    """

    async def test_empty_choice_means_any_template(self, auth_client, template):
        """⚠ **Пустой выбор — «любой из всех», а не отказ.**

        Прежде здесь стоял 422 «выберите хотя бы один шаблон»: рассудили, что
        молчаливую рассылку всеми человек не заказывал. На деле выходило наоборот —
        выбирать приходилось перед каждым нажатием, хотя обычно всё равно, каким
        шаблоном писать.

        Теперь выбор **сужает** набор, а не открывает его. Отказаться от сужения
        можно кнопкой сброса рядом со списком.
        """
        answer = await auth_client.post('/api/dating/mailing/generate',
                                        json={'site_id': SITE, 'mail_ids': []})

        # 422 остаётся, но по другому поводу: на выдуманном сайте нет анкет с
        # рассылкой. Важно, что дело дошло до них — значит шаблоны нашлись.
        assert 'шаблон' not in answer.json()['detail'], 'пустой выбор снова отказ'

    async def test_no_templates_at_all_is_refused(self, auth_client):
        """Шаблонов нет вовсе — отказ с указанием, где их завести: «сделано 0»
        человек прочитал бы как «сходили и ничего не нашлось»."""
        answer = await auth_client.post('/api/dating/mailing/generate',
                                        json={'site_id': 999_778, 'mail_ids': []})

        assert answer.status_code == 422
        assert 'шаблон' in answer.json()['detail']

    async def test_unknown_template_is_refused(self, auth_client):
        """Номер шаблона с чужого сайта или удалённого — тот же отказ: письма по
        нему не сделать, а молчание выглядело бы как «сделали»."""
        answer = await auth_client.post('/api/dating/mailing/generate',
                                        json={'site_id': SITE,
                                              'mail_ids': [99_999_999]})

        assert answer.status_code == 422

    async def test_site_without_mailing_profiles_is_refused(self, auth_client,
                                                            template):
        """⚠ Нет анкет с включённой рассылкой — отказ с указанием, где включить.

        Отбор тот же, что у списка слева на вкладке «Письма рассылка»: список и
        те, кому готовятся письма, обязаны совпадать, иначе человек видит одно, а
        получает другое.
        """
        answer = await auth_client.post('/api/dating/mailing/generate',
                                        json={'site_id': SITE,
                                              'mail_ids': [template]})

        assert answer.status_code == 422
        assert 'рассылк' in answer.json()['detail']


class TestCardForEveryone:
    """⚠⚠ Карточка заводится **каждому адресату**, а не только опрошенным.

    Это два разных дела, и путать их нельзя: описание стоит похода браузером и
    потому нормируется (`mailing_probe`, пять на заход), а карточка — строка из
    ленты, которая уже в памяти.

    ## ⚠⚠ Ломалось это дважды, и оба раза одинаково

    **Первый раз** карточку заводил сам сбор описания: на живом пуле в 50 писем
    карточка нашлась у 13 адресатов. В карточке письма имя адресата ссылается на
    страницу человека у нас, и ссылка вела в никуда чаще, чем куда-нибудь.

    **Второй раз** — когда личный вопрос стал жребием на письмо. Вызов `_card`
    уехал под `if ask`, и вместе с платным походом перестала заводиться бесплатная
    карточка: на проде 55 карточек на 100 писем в пуле.

    Поэтому бесплатная часть живёт **в `_body`, до жребия и вне всяких условий**, а
    `_card` осталась только про поход. Эти тесты стерегут именно место вызова:
    поймали они обе поломки.

    ⚠ **Наружу сюита не ходит**: сам поход за описанием подменяется.
    """

    def test_the_feed_is_written_in_the_body_not_in_the_probe(self):
        """Заведение карточки стоит в `_body`, а не в `_card`.

        В `_card` его быть не должно вовсе: та зовётся только когда выпал вопрос.
        """
        import inspect

        from src.dating.mailing import _body, _card

        assert 'await _from_feed' in inspect.getsource(_body), \
            'карточка перестала заводиться в сборке письма'
        assert 'await _from_feed' not in inspect.getsource(_card), \
            'заведение карточки вернулось в платную часть — она под жребием'

    def test_the_feed_is_written_before_the_dice(self):
        """⚠ И **раньше жребия**: после него письмо без вопроса уже ушло из функции."""
        import inspect

        code = inspect.getsource(__import__('src.dating.mailing',
                                            fromlist=['_body'])._body)

        assert code.index('await _from_feed') < code.index('if not ask:'), \
            'лента пишется после выхода по «вопрос не выпал» — карточки не будет'

    def test_the_feed_is_written_outside_any_branch(self):
        """⚠ И **вне ветки**: стой вызов внутри `if`, порядок был бы верным, а
        карточки у части адресатов всё равно не появлялось бы. Отступ ровно в
        четыре пробела значит верхний уровень тела функции."""
        import inspect

        line = next(one for one in inspect.getsource(
            __import__('src.dating.mailing', fromlist=['_body'])._body).splitlines()
            if 'await _from_feed' in one)

        assert len(line) - len(line.lstrip()) == 4, \
            'вызов ленты вложен в условие — карточка снова не всем'

    async def test_card_appears_even_without_a_name(self, auth_client):
        """⚠ Карточку заводим и когда лента не дала **ничего**.

        Строка нужна не ради полей: письмо ссылается на страницу адресата, и
        адресат без строки — ссылка в никуда. Пустая карточка честнее её
        отсутствия: «человек есть, о нём пока ничего не знаем» — законный ответ, а
        «такой страницы нет» про того, кому мы прямо сейчас пишем, выглядит
        поломкой.
        """
        from mysql_.mysql_ import mysql_get_db_async

        from src.dating.mailing import _from_feed
        from src.uvicorn.routers.dating.companion import companion_get

        uid = f'tst-{uuid.uuid4().hex[:10]}'
        try:
            await _from_feed(SITE, {'uid': uid})

            assert await companion_get(SITE, uid), 'пустая лента не завела карточку'
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM companion WHERE uid = %s', (uid,))
                await db.connection.commit()

    async def test_feed_fills_what_it_knows(self, auth_client):
        """А что лента знает — попадает в карточку: имя, фото, возраст."""
        from mysql_.mysql_ import mysql_get_db_async

        from src.dating.mailing import _from_feed
        from src.uvicorn.routers.dating.companion import companion_get

        uid = f'tst-{uuid.uuid4().hex[:10]}'
        try:
            await _from_feed(SITE, {'uid': uid, 'name': 'Кевин',
                                    'photo': 'p.jpg', 'age': 58})
            card = await companion_get(SITE, uid)

            assert card['name'] == 'Кевин'
            assert card['photo'] == 'p.jpg'
            assert int(card['age']) == 58
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM companion WHERE uid = %s', (uid,))
                await db.connection.commit()

    async def test_empty_feed_does_not_wipe_what_is_known(self, auth_client):
        """⚠ Второй заход с пустой лентой **не стирает** снятое раньше. Ходим по
        одному человеку не раз (пул набирается кругами), и пустота из ленты не
        должна означать «сотри имя»."""
        from mysql_.mysql_ import mysql_get_db_async

        from src.dating.mailing import _from_feed
        from src.uvicorn.routers.dating.companion import companion_get

        uid = f'tst-{uuid.uuid4().hex[:10]}'
        try:
            await _from_feed(SITE, {'uid': uid, 'name': 'Кевин'})
            await _from_feed(SITE, {'uid': uid})

            assert (await companion_get(SITE, uid))['name'] == 'Кевин', \
                'пустая лента затёрла имя'
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM companion WHERE uid = %s', (uid,))
                await db.connection.commit()


class TestOncePerTemplate:
    """⚠ Правило повтора: **одна анкета — один собеседник — один шаблон**.

    Не «одна анкета одному собеседнику», как было сначала. Разница существенная:
    у сайта пять шаблонов, и запрет на второе письмо вовсе означал бы, что четыре
    из них до знакомого адресата не дойдут никогда — пул встал бы, едва обойдя
    тех, кто онлайн.

    Ключ тот же, что у базы (`uq_dating_mailing_once` по `profile_id, uid,
    mail_id`): проверка в коде и ограничение в базе должны совпадать, иначе одно
    из двух — ложь. Код отсеивает заранее, база ловит гонку двух заходов.
    """

    GIRLS = [{'id': 1}, {'id': 2}]
    TEMPLATES = [{'id': 10}, {'id': 20}]
    PEOPLE = [{'uid': 'a'}, {'uid': 'b'}]

    def test_taken_triple_never_comes_back(self):
        """Занятая тройка не выдаётся. ⚠ Прогонов много: пары собираются
        случайным выбором, и один проход доказал бы разве что везение."""
        from src.dating.mailing import _pairs

        for _ in range(50):
            for girl, template, person in _pairs(self.GIRLS, self.PEOPLE,
                                                 self.TEMPLATES, {(1, 'a', 10)}):
                assert (girl['id'], person['uid'], template['id']) != (1, 'a', 10)

    def test_another_template_to_the_same_person_is_allowed(self):
        """⚠ Второе письмо тому же адресату — **другим шаблоном** — законно.

        Ради этого правило и ослаблено: иначе знакомый собеседник выпадал бы из
        рассылки навсегда после первого же письма.
        """
        from src.dating.mailing import _pairs

        seen = set()
        for _ in range(60):
            for girl, template, person in _pairs(self.GIRLS, [{'uid': 'a'}],
                                                 self.TEMPLATES, {(1, 'a', 10)}):
                seen.add((girl['id'], template['id']))

        assert (1, 10) not in seen
        assert (1, 20) in seen, 'другой шаблон от той же анкеты не предлагается'

    def test_person_with_no_free_triples_is_skipped(self):
        """Когда для адресата занято всё — его не берут вовсе, а не выдают
        письмо, которое база потом отвергнет."""
        from src.dating.mailing import _pairs

        taken = {(girl, 'a', template) for girl in (1, 2) for template in (10, 20)}
        addressees = {person['uid'] for _, _, person
                      in _pairs(self.GIRLS, self.PEOPLE, self.TEMPLATES, taken)}

        assert addressees == {'b'}, 'адресат без свободных сочетаний попал в работу'

    async def test_taken_is_keyed_like_the_database(self, auth_client, letter,
                                                    profile):
        """⚠ Ключ проверки читается из базы **тройкой**, а не парой.

        Разойдись он с `uq_dating_mailing_once` — набор предлагал бы пары, на
        которых вставка падает: пул молча недобирал бы до потолка, и причину
        пришлось бы искать в логе.
        """
        from src.dating.mailing import _taken

        taken = await _taken(SITE)

        assert (profile, 'tst-uid', 0) in taken, 'письмо из пула не попало в занятые'
        assert all(len(key) == 3 for key in taken), 'ключ занятых не тройка'


class TestPoolRights:
    """Пул открыт всем вошедшим: с ним работает оператор."""

    async def test_operator_can_read(self, dating_client):
        answer = await dating_client.get(f'/api/dating/mailing?site_id={SITE}')

        assert answer.status_code == 200

    async def test_operator_can_edit(self, dating_client, letter):
        """⚠ Править письмо оператору **можно**, в отличие от шаблона. Шаблон
        уходит от всех анкет многим людям, письмо — одному человеку, и читает его
        тот, кто с этим человеком работает."""
        answer = await dating_client.put(f'/api/dating/mailing/{letter}',
                                         json={'body': 'Правка оператора'})

        assert answer.status_code == 200

    async def test_closed_without_login(self, client):
        answer = await client.get(f'/api/dating/mailing?site_id={SITE}')

        assert answer.status_code in (401, 307)


@pytest.fixture
async def mailing_girl():
    """Анкета выдуманного сайта, участвующая в рассылке.

    ⚠ **Строкой в базу, а не ручкой.** Заведение анкеты сверяет сайт у поддельного
    соседа, и выдуманный номер оно отвергает, — а нехватка переменных считается по
    `profile_.site_id`, и анкета обязана лежать на том же сайте, где реестр и
    шаблоны. Внешнего ключа на `site_id` нет (`database.md`), так что строка
    ложится.

    ⚠ **И `status = 'online'`**: набор берёт только тех, кто сейчас на сайте, а
    нехватку переменных считают по тому же отбору. Спящая анкета в него не
    попадёт — и проверка «её назвали» молча проверяла бы пустоту.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO profile (site_id, login, password, enabled,'
            " mailing_on, status) VALUES (%s, %s, %s, 1, 1, 'online')",
            (SITE, f'gap-{uuid.uuid4().hex[:8]}@example.com', 'x'))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        row = await db.fetchone()

    made = int(row['id'])

    yield made

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM profile WHERE id = %s', (made,))
        await db.connection.commit()


class TestGapsAreNamed:
    """Анкета в рассылке, у которой не заполнены переменные.

    ⚠ **Заведено по живому случаю, и он стоил получаса раскопок.** Рассылка стояла
    у четырёх анкет, письма шли от одной: у трёх пустовал `girl_name`. Каждая
    выпавшая на них пара пропускалась молча, в журнале круга это выглядело как
    «готово 25, пропущено 25», а на странице — как «включено, а писем нет».
    Ответ лежал в базе, и достать его можно было только запросом.
    """

    async def test_profile_without_values_is_named(self, auth_client, mailing_girl):
        """Анкета без значения названа, и названа **вместе с переменной**."""
        from src.dating.mailing import mailing_gaps

        var = (await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstGap', 'title': 'Имя',
            'default_value': ''})).json()['result']['id']
        mail = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'gap-{uuid.uuid4().hex[:6]}',
            'body': 'Привет, [[tstGap]]!', 'enabled': True})).json()['result']['id']

        try:
            gaps = await mailing_gaps(SITE)

            assert gaps.get(mailing_girl) == ['tstGap'], \
                'анкета без значения не названа или названа без переменной'
        finally:
            await auth_client.delete(f'/api/dating/mail/{mail}')
            await auth_client.delete(f'/api/dating/mail/var/{var}')

    async def test_filled_profile_is_silent(self, auth_client, mailing_girl):
        """Заполнили — и анкета пропадает из ответа: жаловаться больше не на что."""
        from src.dating.mailing import mailing_gaps

        var = (await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstGap', 'title': 'Имя',
            'default_value': ''})).json()['result']['id']
        mail = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'gap-{uuid.uuid4().hex[:6]}',
            'body': 'Привет, [[tstGap]]!', 'enabled': True})).json()['result']['id']

        try:
            await auth_client.put(f'/api/dating/profile/{mailing_girl}/var',
                                  json={'values': {'tstGap': 'Diana'}})

            assert mailing_girl not in await mailing_gaps(SITE), \
                'у заполненной анкеты всё равно нашлась нехватка'
        finally:
            await auth_client.delete(f'/api/dating/mail/{mail}')
            await auth_client.delete(f'/api/dating/mail/var/{var}')

    async def test_companion_is_not_the_profile_business(self, auth_client,
                                                         mailing_girl):
        """⚠ `companion` и `live` в счёт не идут — ровно как в `_body`.

        Их даёт пара «анкета — адресат», а не анкета: первое берётся из карточки
        собеседника, второе сочиняет агент. Посчитай мы их — метка «нет
        переменных» горела бы у всех и всегда, и первая же настоящая нехватка
        потерялась бы среди неё.
        """
        from src.dating.mailing import mailing_gaps

        var = (await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstHis', 'title': 'Его имя',
            'source': 'companion', 'default_value': ''})).json()['result']['id']
        mail = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'gap-{uuid.uuid4().hex[:6]}',
            'body': 'Привет, [[tstHis]]!', 'enabled': True})).json()['result']['id']

        try:
            assert mailing_girl not in await mailing_gaps(SITE), \
                'переменная адресата посчиталась нехваткой анкеты'
        finally:
            await auth_client.delete(f'/api/dating/mail/{mail}')
            await auth_client.delete(f'/api/dating/mail/var/{var}')

    async def test_one_template_is_enough(self, auth_client, mailing_girl):
        """⚠ Нехватка **хотя бы в одном** шаблоне — уже повод сказать.

        Шаблон паре достаётся наугад: анкета, годная для одного из двух, половину
        писем пропустит. Промолчи мы про неё как про «в целом рабочую» — объяснять
        пришлось бы потом, почему писем вдвое меньше ожидаемого.
        """
        from src.dating.mailing import mailing_gaps

        var = (await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstGap', 'title': 'Имя',
            'default_value': ''})).json()['result']['id']
        plain = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'gap-{uuid.uuid4().hex[:6]}',
            'body': 'Привет!', 'enabled': True})).json()['result']['id']
        with_var = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'gap-{uuid.uuid4().hex[:6]}',
            'body': 'Привет, [[tstGap]]!', 'enabled': True})).json()['result']['id']

        try:
            assert mailing_girl in await mailing_gaps(SITE), \
                'нехватка в одном шаблоне из двух осталась незамеченной'
        finally:
            await auth_client.delete(f'/api/dating/mail/{with_var}')
            await auth_client.delete(f'/api/dating/mail/{plain}')
            await auth_client.delete(f'/api/dating/mail/var/{var}')

    async def test_default_of_the_registry_counts(self, auth_client, mailing_girl):
        """Умолчание реестра — тоже значение: письмо с ним уходит.

        ⚠ Иначе метка горела бы у анкеты, которая на деле пишет: переменную с
        умолчанием затем и заводят, чтобы не заполнять её каждой из сотни.
        """
        from src.dating.mailing import mailing_gaps

        var = (await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstGap', 'title': 'Имя',
            'default_value': 'Дорогой друг'})).json()['result']['id']
        mail = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'gap-{uuid.uuid4().hex[:6]}',
            'body': 'Привет, [[tstGap]]!', 'enabled': True})).json()['result']['id']

        try:
            assert mailing_girl not in await mailing_gaps(SITE), \
                'анкета с умолчанием реестра посчитана незаполненной'
        finally:
            await auth_client.delete(f'/api/dating/mail/{mail}')
            await auth_client.delete(f'/api/dating/mail/var/{var}')

    async def test_no_templates_means_nothing_to_say(self, auth_client,
                                                     mailing_girl):
        """Шаблонов нет — и нехватки нет: сказать не о чем, а не «всё плохо».

        ⚠ Пустой ответ здесь честнее выдуманного: без шаблонов не готовится ни
        одно письмо ни у кого, и метка на анкете указала бы не туда.
        """
        from src.dating.mailing import mailing_gaps

        assert await mailing_gaps(SITE) == {}, \
            'на сайте без шаблонов нашлась нехватка у анкеты'


class TestGapsHandle:
    """Ручка `/gaps`: та же нехватка, но словами страницы."""

    async def test_zero_site_is_empty(self, auth_client):
        """Сайт не выбран — пустой ответ, а не нехватка всех сайтов разом."""
        answer = await auth_client.get('/api/dating/mailing/gaps?site_id=0')

        assert answer.status_code == 200
        assert answer.json()['result'] == {}

    async def test_keys_are_strings(self, auth_client, mailing_girl):
        """⚠ Номер анкеты в ответе — строка: ключи JSON строками и приходят.

        Считай страница их числами — сверка `mailGaps[row.id]` не нашла бы ничего,
        и метка не показалась бы никогда.
        """
        var = (await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstGap', 'title': 'Имя',
            'default_value': ''})).json()['result']['id']
        mail = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'gap-{uuid.uuid4().hex[:6]}',
            'body': 'Привет, [[tstGap]]!', 'enabled': True})).json()['result']['id']

        try:
            got = (await auth_client.get(
                f'/api/dating/mailing/gaps?site_id={SITE}')).json()['result']

            assert got.get(str(mailing_girl)) == ['tstGap']
        finally:
            await auth_client.delete(f'/api/dating/mail/{mail}')
            await auth_client.delete(f'/api/dating/mail/var/{var}')

    async def test_operator_can_read(self, dating_client):
        """Метку видит оператор: она про его работу, а не про настройки сайта."""
        answer = await dating_client.get(f'/api/dating/mailing/gaps?site_id={SITE}')

        assert answer.status_code == 200

    async def test_closed_without_login(self, client):
        answer = await client.get(f'/api/dating/mailing/gaps?site_id={SITE}')

        assert answer.status_code in (401, 307)


class TestNameIsAnAddress:
    """⚠ Обращение — **имя**, а не имя с фамилией.

    Сайт отдаёт `name` целиком: «Semen Gorbunkov». Письмо, начинающееся «Hi Semen
    Gorbunkov», читается как рассылка из базы — чем оно и является, и незачем это
    подчёркивать. Поэтому в `…_name` идёт разобранное первое слово, если оно есть.
    """

    def test_full_name_gives_the_first_word(self):
        from src.dating.mailing import _from_person

        assert _from_person('companion_name', {'name': 'Semen Gorbunkov'},
                            {'default_value': ''}) == 'Semen'

    def test_single_word_name_stays_whole(self):
        """⚠ Здесь и видно, почему правится подстановка, а не тексты шаблонов.

        Замени мы `[[companion_name]]` на `[[companion_first_name]]` в готовых
        письмах — адресатам с именем одним словом подставлять стало бы нечего:
        `firstname` у них пуст намеренно (`people_name_parts`), нехватка
        переменной останавливает отправку, и письмо не ушло бы вовсе.
        """
        from src.dating.mailing import _from_person

        assert _from_person('companion_name', {'name': 'Kevin'},
                            {'default_value': ''}) == 'Kevin'

    def test_first_name_variable_still_answers_honestly(self):
        """`companion_first_name` остаётся честным: делить нечего — пусто.

        Он отвечает на вопрос «знаем ли мы имя отдельно», и подменять его целым
        именем значило бы стереть разницу между «знаем» и «просто строка».
        """
        from src.dating.mailing import _from_person

        assert _from_person('companion_first_name', {'name': 'Kevin'},
                            {'default_value': ''}) == ''

    def test_other_fields_are_untouched(self):
        """Правило — только про `…_name`: прочие поля берутся как лежат."""
        from src.dating.mailing import _from_person

        assert _from_person('his_city', {'name': 'Semen Gorbunkov', 'city': 'Kyiv'},
                            {'default_value': ''}) == 'Kyiv'
