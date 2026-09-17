"""Переменные писем: реестр по сайту и значения у анкет.

⚠ **Главное здесь — связка трёх мест.** Переменную заводят на сайте, она
появляется у каждой его анкеты пустой, и письмо, которое на неё ссылается, не
уходит, пока значения нет. Каждое из трёх по отдельности бессмысленно, поэтому
проверяется и связка целиком.

Хранение и права — по HTTP, в живую панель с живой базой.

⚠ **Анкета заводится своя** (случайный логин) и удаляется в конце: значения
привязаны к ней, и чужую трогать нельзя. Сайт при этом **живой**: заведение анкеты
сверяет номер у соседа, и выдуманный он отвергает.

⚠ **Реестр живого сайта не пуст, и рассчитывать на обратное нельзя.** Там лежат
переменные, заведённые людьми (`girl_name`, `companion_name` — миграцией), и
завтра их станет больше. Поэтому проверки смотрят **на свои** имена (`tst…`), а не
на список целиком: тест, ожидающий ровного равенства, ломался бы от каждой новой
переменной в панели. Так и вышло — поймано при заведении базовых двух.
"""

import uuid

import pytest

pytestmark = pytest.mark.mail
# Сайт для реестра — выдуманный: своей таблицы сайтов здесь нет, а реестр к соседу
# не ходит.
SITE = 999_777

# Сайт для анкеты — из поддельного соседа (`rhapsody_fake.py`): заведение учётки
# сверяет номер, и выдуманный отвергает.
LIVE_SITE = 343


@pytest.fixture
async def var(auth_client):
    """Одна переменная, которая убирается за собой."""
    answer = await auth_client.post('/api/dating/mail/var', json={
        'site_id': SITE, 'name': 'tstFirst', 'title': 'Имя', 'default_value': ''})
    var_id = answer.json()['result']['id']

    yield var_id

    await auth_client.delete(f'/api/dating/mail/var/{var_id}')


@pytest.fixture
async def profile_vars(auth_client):
    """Своя анкета на живом сайте и две переменные его реестра.

    Отдаёт `(profile_id, [var_id, …])`. Убирает всё за собой: анкету, переменные и
    значения (последние уходят вместе с переменными).
    """
    made = []
    for name, default in (('tstFirst', ''), ('tstLast', 'Smith')):
        answer = await auth_client.post('/api/dating/mail/var', json={
            'site_id': LIVE_SITE, 'name': name, 'title': name,
            'default_value': default})
        if answer.status_code == 200:
            made.append(answer.json()['result']['id'])

    created = await auth_client.post('/api/dating/profile', json={
        'site_id': LIVE_SITE, 'login': f'var-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})
    profile_id = created.json()['result']

    yield profile_id, made

    await auth_client.delete(f'/api/dating/profile/{profile_id}')
    for var_id in made:
        await auth_client.delete(f'/api/dating/mail/var/{var_id}')


class TestRegistry:
    """Реестр переменных сайта."""

    async def test_created_variable_comes_back(self, auth_client, var):
        rows = (await auth_client.get(
            f'/api/dating/mail/var?site_id={SITE}')).json()['result']

        assert [row['id'] for row in rows] == [var]
        assert rows[0]['name'] == 'tstFirst'

    async def test_duplicate_name_is_refused(self, auth_client, var):
        """⚠ Одноимённых на сайте не бывает: письмо ссылается на переменную по
        имени, и две одинаковых сделали бы ссылку неоднозначной."""
        answer = await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstFirst'})

        assert answer.status_code == 409

    async def test_same_name_on_another_site_is_fine(self, auth_client, var):
        """Реестры у сайтов свои: имя занято внутри сайта, а не во всей панели."""
        answer = await auth_client.post('/api/dating/mail/var', json={
            'site_id': 999_778, 'name': 'tstFirst'})

        assert answer.status_code == 200
        await auth_client.delete(
            f"/api/dating/mail/var/{answer.json()['result']['id']}")

    @pytest.mark.parametrize('name', ['её_имя', '1st', 'a-b', 'a b', ''])
    async def test_bad_name_is_refused(self, auth_client, name):
        answer = await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': name})

        assert answer.status_code == 422

    async def test_name_does_not_change_on_update(self, auth_client, var):
        """⚠ На имя ссылаются шаблоны и значения анкет.

        Переименуй мы его — письма перестали бы находить переменную, а значения
        осиротели бы, причём молча. Правятся только подпись и умолчание.
        """
        await auth_client.put(f'/api/dating/mail/var/{var}', json={
            'site_id': SITE, 'name': 'другоеИмя', 'title': 'новая подпись',
            'default_value': 'Ann'})

        row = (await auth_client.get(
            f'/api/dating/mail/var?site_id={SITE}')).json()['result'][0]

        assert row['name'] == 'tstFirst', 'имя переменной уехало'
        assert row['title'] == 'новая подпись'
        assert row['default_value'] == 'Ann'

    async def test_no_site_gives_nothing(self, auth_client, var):
        """Пустой список, а не все переменные всех сайтов."""
        assert (await auth_client.get('/api/dating/mail/var')).json()['result'] == []

    async def test_missing_variable_is_404(self, auth_client):
        assert (await auth_client.delete(
            '/api/dating/mail/var/999999')).status_code == 404


class TestVarSource:
    """⚠ Две породы переменных: про анкету и про адресата."""

    @pytest.fixture
    async def both(self, auth_client):
        """По одной каждой породы на живом сайте, плюс своя анкета."""
        made = []
        for name, source in (('tstGirl', 'profile'), ('tstWho', 'companion')):
            answer = await auth_client.post('/api/dating/mail/var', json={
                'site_id': LIVE_SITE, 'name': name, 'source': source, 'title': name})
            if answer.status_code == 200:
                made.append(answer.json()['result']['id'])

        created = await auth_client.post('/api/dating/profile', json={
            'site_id': LIVE_SITE, 'login': f'src-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})
        profile_id = created.json()['result']

        yield profile_id

        await auth_client.delete(f'/api/dating/profile/{profile_id}')
        for var_id in made:
            await auth_client.delete(f'/api/dating/mail/var/{var_id}')

    async def test_source_comes_back(self, auth_client, both):
        rows = {row['name']: row for row in (await auth_client.get(
            f'/api/dating/mail/var?site_id={LIVE_SITE}')).json()['result']}

        assert rows['tstGirl']['source'] == 'profile'
        assert rows['tstWho']['source'] == 'companion'

    async def test_live_source_is_accepted(self, auth_client):
        """⚠ Третий источник: значение не лежит нигде — сочиняется при отправке.

        От `companion` он отличается не формально: имя адресата можно посмотреть
        глазами и поправить, а вопрос существует только в момент отправки и у
        каждой пары свой.
        """
        answer = await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstLive', 'source': 'live',
            'title': 'Вопрос — сочиняется при отправке'})
        try:
            assert answer.status_code == 200
            assert answer.json()['result']['source'] == 'live'
        finally:
            await auth_client.delete(
                f"/api/dating/mail/var/{answer.json()['result']['id']}")

    async def test_live_is_not_shown_at_the_profile(self, auth_client, profile_vars):
        """У анкеты его нет по той же причине, что и `companion`: значение зависит
        от получателя. Строка «вопрос у анкеты» означала бы вопрос, один на всех, —
        то есть ровно то, от чего `live` и уводит."""
        profile_id, made = profile_vars
        created = await auth_client.post('/api/dating/mail/var', json={
            'site_id': LIVE_SITE, 'name': 'tstAsk', 'source': 'live', 'title': 'Вопрос'})
        made.append(created.json()['result']['id'])

        names = {row['name'] for row in (await auth_client.get(
            f'/api/dating/profile/{profile_id}/var')).json()['result']}

        assert 'tstAsk' not in names

    async def test_default_source_is_profile(self, auth_client):
        """Переменных про анкету будет больше: имя, возраст, город, занятия. Про
        адресата известно ровно то, что отдал сайт."""
        answer = await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstDefault'})
        try:
            assert answer.json()['result']['source'] == 'profile'
        finally:
            await auth_client.delete(
                f"/api/dating/mail/var/{answer.json()['result']['id']}")

    async def test_companion_is_not_shown_at_the_profile(self, auth_client, both):
        """⚠ У анкеты значения `companion` быть не может: оно зависит от того,
        кому пишем, и появляется в момент отправки. Строка «имя собеседника у
        анкеты» означала бы неправду."""
        names = [row['name'] for row in (await auth_client.get(
            f'/api/dating/profile/{both}/var')).json()['result']]

        assert 'tstGirl' in names, 'переменной про анкету нет в списке'
        assert 'tstWho' not in names, 'переменная про адресата попала к анкете'

    async def test_companion_value_is_not_stored(self, auth_client, both):
        """Пришли его страница или агент — молча отбрасываем, как и незнакомое
        имя: складывать такое значение просто некуда."""
        await auth_client.put(f'/api/dating/profile/{both}/var', json={
            'values': {'tstGirl': 'Diana', 'tstWho': 'John'}})

        rows = {row['name']: row['value'] for row in (await auth_client.get(
            f'/api/dating/profile/{both}/var')).json()['result']}

        assert rows.get('tstGirl') == 'Diana'
        assert 'tstWho' not in rows, 'значение про адресата легло к анкете'

    async def test_letter_may_use_both(self, auth_client, both):
        """Для письма разницы нет: обе пишутся одинаково и обе проверяются по
        реестру. Разный только источник значения."""
        answer = await auth_client.post('/api/dating/mail', json={
            'site_id': LIVE_SITE, 'name': 'тест-обе',
            'body': 'Hello, [[tstWho]]! I am [[tstGirl]].'})

        try:
            assert answer.status_code == 200
            assert answer.json()['result']['vars'] == ['tstWho', 'tstGirl']
        finally:
            if answer.status_code == 200:
                await auth_client.delete(
                    f"/api/dating/mail/{answer.json()['result']['id']}")


class TestProfileValues:
    """Значения у анкеты."""

    async def test_registry_shows_up_empty(self, auth_client, profile_vars):
        """⚠ Заведённая переменная видна у анкеты **пустой строкой**, а не
        отсутствием.

        Именно из-за неё письмо и не уйдёт. Перечисляй мы по своей таблице, такой
        переменной в ответе не было бы вовсе — и страница молчала бы о главном.
        """
        profile_id, _ = profile_vars
        rows = {row['name']: row for row in (await auth_client.get(
            f'/api/dating/profile/{profile_id}/var')).json()['result']}

        assert {'tstFirst', 'tstLast'} <= set(rows), 'своих переменных нет в списке'
        assert rows['tstFirst']['value'] == ''
        assert rows['tstLast']['value'] == ''

    async def test_default_counts_as_filled(self, auth_client, profile_vars):
        """⚠ `filled` — есть ли **чем подставить**: своё значение или умолчание.

        Иначе страница показывала бы «пусто» у переменной, которая на самом деле
        подставится, и человек заполнял бы её зря.
        """
        profile_id, _ = profile_vars
        rows = {row['name']: row for row in (await auth_client.get(
            f'/api/dating/profile/{profile_id}/var')).json()['result']}

        assert rows['tstFirst']['filled'] is False, 'без умолчания — не заполнено'
        assert rows['tstLast']['filled'] is True, 'умолчание должно считаться'

    async def test_saved_value_comes_back(self, auth_client, profile_vars):
        profile_id, _ = profile_vars
        await auth_client.put(f'/api/dating/profile/{profile_id}/var', json={
            'values': {'tstFirst': 'Diana'}, 'filled_by': 'hand'})

        rows = {row['name']: row for row in (await auth_client.get(
            f'/api/dating/profile/{profile_id}/var')).json()['result']}

        assert rows['tstFirst']['value'] == 'Diana'
        assert rows['tstFirst']['filled_by'] == 'hand'

    async def test_unknown_name_is_ignored(self, auth_client, profile_vars):
        """⚠ Принимаются только имена из реестра сайта.

        Реестр ведут на «Письмах», и запись мимо него завела бы переменную,
        которой нет ни в одном списке. Так же поступит и приход агента: он вернёт
        что угодно, а лечь должно только ожидаемое.
        """
        profile_id, _ = profile_vars
        await auth_client.put(f'/api/dating/profile/{profile_id}/var', json={
            'values': {'чужое': 'мусор', 'tstFirst': 'Diana'}})

        names = {row['name'] for row in (await auth_client.get(
            f'/api/dating/profile/{profile_id}/var')).json()['result']}

        assert 'чужое' not in names, 'запись прошла мимо реестра'
        assert {'tstFirst', 'tstLast'} <= names

    async def test_empty_value_erases_the_row(self, auth_client, profile_vars):
        """⚠ Пустое значение стирает строку, а не хранится пустым.

        «Значения нет» и «значение — пустая строка» для письма одно и то же, а две
        записи одного состояния однажды разойдутся.
        """
        profile_id, _ = profile_vars
        await auth_client.put(f'/api/dating/profile/{profile_id}/var',
                              json={'values': {'tstFirst': 'Diana'}})
        await auth_client.put(f'/api/dating/profile/{profile_id}/var',
                              json={'values': {'tstFirst': '   '}})

        rows = {row['name']: row for row in (await auth_client.get(
            f'/api/dating/profile/{profile_id}/var')).json()['result']}

        assert rows['tstFirst']['value'] == ''
        assert rows['tstFirst']['filled_by'] == '', 'строка осталась в базе'

    async def test_saving_twice_updates_not_duplicates(self, auth_client, profile_vars):
        """Агент вернёт значение второй раз — оно обновится, а не задвоится: за
        это отвечает уникальный ключ пары «анкета + имя»."""
        profile_id, _ = profile_vars
        await auth_client.put(f'/api/dating/profile/{profile_id}/var',
                              json={'values': {'tstFirst': 'Diana'}})
        await auth_client.put(f'/api/dating/profile/{profile_id}/var',
                              json={'values': {'tstFirst': 'Anna'},
                                    'filled_by': 'agent'})

        rows = [row for row in (await auth_client.get(
            f'/api/dating/profile/{profile_id}/var')).json()['result']
            if row['name'] == 'tstFirst']

        assert len(rows) == 1
        assert rows[0]['value'] == 'Anna'
        assert rows[0]['filled_by'] == 'agent'

    async def test_missing_profile_is_404(self, auth_client):
        assert (await auth_client.get(
            '/api/dating/profile/999999/var')).status_code == 404


class TestDeleteTakesValues:
    """⚠ Удаление переменной уносит её значения у анкет сайта."""

    async def test_values_go_with_the_variable(self, auth_client, profile_vars):
        """Связь идёт по имени, поэтому `ON DELETE CASCADE` тут невозможен —
        уборку делает код ручки. Оставь мы значения, они жили бы невидимками и
        всплыли бы у переменной с тем же именем, заведённой заново.
        """
        profile_id, made = profile_vars
        await auth_client.put(f'/api/dating/profile/{profile_id}/var',
                              json={'values': {'tstFirst': 'Diana'}})

        await auth_client.delete(f'/api/dating/mail/var/{made[0]}')

        # Заводим ту же переменную заново — значение не должно воскреснуть.
        again = await auth_client.post('/api/dating/mail/var', json={
            'site_id': LIVE_SITE, 'name': 'tstFirst'})
        made[0] = again.json()['result']['id']

        rows = {row['name']: row for row in (await auth_client.get(
            f'/api/dating/profile/{profile_id}/var')).json()['result']}

        assert rows['tstFirst']['value'] == '', 'значение воскресло вместе с именем'

    async def test_template_survives_but_stops_working(self, auth_client, profile_vars):
        """⚠ Шаблон не правится при удалении переменной.

        Лезть в чужой текст и вырезать оттуда куски мы не вправе — тихо испорченный
        шаблон хуже неотправленного письма. Письмо просто перестанет уходить:
        переменную неоткуда взять.
        """
        _, made = profile_vars
        created = await auth_client.post('/api/dating/mail', json={
            'site_id': LIVE_SITE, 'name': 'тест-выживание',
            'body': 'Hi [[tstFirst]]!'})
        mail_id = created.json()['result']['id']

        try:
            await auth_client.delete(f'/api/dating/mail/var/{made[0]}')
            made[0] = 0

            rows = (await auth_client.get(
                f'/api/dating/mail?site_id={LIVE_SITE}')).json()['result']
            found = [row for row in rows if row['id'] == mail_id]

            assert found, 'шаблон исчез вместе с переменной'
            assert found[0]['body'] == 'Hi [[tstFirst]]!', 'текст шаблона правили'
        finally:
            await auth_client.delete(f'/api/dating/mail/{mail_id}')


class TestVarRights:
    """Реестр правит админ, значения — все вошедшие."""

    async def test_operator_reads_the_registry(self, dating_client):
        answer = await dating_client.get(f'/api/dating/mail/var?site_id={SITE}')

        assert answer.status_code == 200

    async def test_operator_cannot_create_a_variable(self, dating_client):
        """⚠ Завести переменную — решение про сайт: она появится у каждой анкеты, и
        письма начнут её требовать."""
        answer = await dating_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tstNope'})

        assert answer.status_code == 403

    async def test_operator_cannot_delete_a_variable(self, dating_client):
        assert (await dating_client.delete(
            '/api/dating/mail/var/1')).status_code == 403

    async def test_operator_fills_values(self, auth_client, dating_client,
                                         profile_vars):
        """⚠ Заполнить значение — работа с конкретной анкетой, ровно та, ради
        которой панель и открывают. Это не настройка установки."""
        profile_id, _ = profile_vars
        answer = await dating_client.put(f'/api/dating/profile/{profile_id}/var',
                                         json={'values': {'tstFirst': 'Diana'}})

        assert answer.status_code == 200

    async def test_closed_without_login(self, client):
        answer = await client.get(f'/api/dating/mail/var?site_id={SITE}')

        assert answer.status_code in (401, 307)


class TestProfilePageTabs:
    """Правая колонка «Анкет» поделена на две вкладки."""

    async def test_page_has_both_tabs(self, auth_client):
        """⚠ Разное по природе: «Общая информация» — как анкету ведут,
        «Переменные» — что о ней знают письма. Одной простынёй переменные тонули
        бы под расписанием и удалением."""
        page = (await auth_client.get('/admin/dating')).text

        assert 'card-tabs' in page
        assert 'Общая информация' in page
        assert 'data-tab="var"' in page

    async def test_work_template_has_the_variables_tab(self, rhapsody):
        """⚠ Реестр переменных переехал в «Профили» → «Письма» подвкладкой.

        Прежде это была своя страница. Искать разметку надо **в шаблоне сайта**, а
        не в самой странице: вкладки приезжают оттуда, отдельным запросом.
        """
        from src.uvicorn.routers.dating.work import work_template

        class _Request:
            def __init__(self):
                self.app = type('App', (), {'state': _state()})()
                self.scope = {'type': 'http'}

        def _state():
            from starlette.templating import Jinja2Templates

            return type('S', (), {'jinja2_template': Jinja2Templates(
                directory='data/uvicorn/template')})()

        answer = await work_template(_Request(), site_id=343, user=None)
        html = answer.body.decode()

        assert 'data-mail-tab="template"' in html
        assert 'data-mail-tab="var"' in html
