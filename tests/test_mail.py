"""Шаблоны писем: переменные, подстановка, хранение, права.

Разбор и подстановка — в процессе: это чистые функции над строкой, и гонять их по
HTTP значило бы проверять маршрутизацию вместо правил. Хранение и права — по HTTP,
в живую панель с живой базой.

⚠ **Номер сайта выдуманный** (`SITE`), как и в остальной сюите: своей таблицы
сайтов здесь нет, `site_id` — просто ключ в чужую базу. Живые шаблоны задеть
невозможно в принципе.

⚠ **Что завела, то и убрала.** Шаблоны заводятся фикстурой и удаляются в
`finally`: список отбирается по сайту, и оставленный тест испортил бы счёт
следующему.
"""

import pytest

from src.dating.dating_mail import (DATING_MAIL_BODY_MAX, dating_mail_check,
                                    dating_mail_missing,
                                    dating_mail_render, dating_mail_var_check,
                                    dating_mail_vars_found)

pytestmark = pytest.mark.mail

# Имена, заведённые «на сайте» в тех проверках, где реестр нужен, но база — нет.
ЕСТЬ = {'firstName', 'lastName'}

SITE = 999_777


class TestVarsFound:
    """Какие переменные нашлись в тексте."""

    def test_finds_in_order_without_repeats(self):
        found = dating_mail_vars_found(
            'Hi [[his_name]], I am [[her_name]]. Bye, [[his_name]]!')

        assert found == ['his_name', 'her_name']

    def test_spaces_inside_braces_are_fine(self):
        """Человек ставит пробелы не думая, и `[[her_name]]` обязано работать
        так же, как `[[her_name]]`."""
        assert dating_mail_vars_found('[[her_name]]') == ['her_name']

    @pytest.mark.parametrize('text', ['', None, 'без переменных вовсе',
                                      'одинарная [ скобка ]', '[[ ]]', '[[]]'])
    def test_nothing_to_find(self, text):
        """Пустые скобки переменной не считаем: имени в них нет, ругаться не на
        что, подставлять тем более нечего."""
        assert dating_mail_vars_found(text) == []

    @pytest.mark.parametrize('name', ['нет_такой', 'её_имя', 'HisName', 'a b'])
    def test_anything_between_braces_counts_as_an_attempt(self, name):
        """⚠ Ловим **попытку** написать переменную, а не только то, что умеем
        подставить.

        Сперва регулярка была ровно по реестру (`[a-z_][a-z0-9_]*`), и выходило
        наоборот задуманному: `[[её_имя]]` переменной не считалось вовсе, проверка
        молчала, шаблон сохранялся — и скобки уезжали живому человеку в письме как
        есть. Сверяет с реестром `dating_mail_check`, и его дело — отказать.
        """
        assert dating_mail_vars_found('Hi [[%s]]!' % name) == [name]


class TestCheck:
    """Годен ли шаблон. Причина — словами, для человека."""

    def test_good_template_passes(self):
        assert dating_mail_check('Знакомство', 'Hi [[firstName]]!', ЕСТЬ) == ''

    def test_template_without_vars_is_fine(self):
        """Письмо без переменных законно: оно просто уйдёт всем одинаковым."""
        assert dating_mail_check('Общее', 'Hello!', ЕСТЬ) == ''

    @pytest.mark.parametrize('name', ['', '   ', None])
    def test_name_is_required(self, name):
        assert 'название' in dating_mail_check(name, 'Hi!', ЕСТЬ)

    @pytest.mark.parametrize('body', ['', '   ', None])
    def test_body_is_required(self, body):
        assert 'пустым' in dating_mail_check('Имя', body, ЕСТЬ)

    def test_too_long_body_is_refused(self):
        """Предел смысловой, а не колонки: простыню в десять тысяч знаков не
        прочитают, зато разошлют тысяче человек."""
        assert 'длиннее' in dating_mail_check('Имя', 'я' * (DATING_MAIL_BODY_MAX + 1),
                                              ЕСТЬ)

    def test_unknown_variable_is_refused(self):
        """⚠ Ради этого проверка и заведена.

        Опечатка иначе уехала бы живому человеку в письме как есть («Меня зовут
        [[frstName]]»), и заметить её можно было бы только в журнале отправок — то
        есть после рассылки.
        """
        why = dating_mail_check('Имя', 'Меня зовут [[frstName]]', ЕСТЬ)

        assert 'frstName' in why
        # Причина говорит, что поправить: перечисляет то, что есть.
        assert 'firstName' in why

    def test_all_registry_names_are_allowed(self):
        body = ' '.join('[[%s]]' % name for name in ЕСТЬ)

        assert dating_mail_check('Все', body, ЕСТЬ) == ''

    @pytest.mark.parametrize('name', ['нет_такой', 'её_имя', 'FirstName'])
    def test_variable_outside_the_registry_is_refused(self, name):
        """⚠ Кириллица и чужой регистр — тоже отказ, а не молчание.

        Регистр значим: `FirstName` и `firstName` — разные имена, и агент вернёт
        то, которое завели. Молчаливое совпадение «по смыслу» означало бы письмо,
        которое ушло не с тем, что подставили.
        """
        assert name in dating_mail_check('Имя', 'Меня зовут [[%s]]' % name, ЕСТЬ)

    def test_site_without_variables_says_where_to_go(self):
        """У сайта без единой переменной список пуст, и это само по себе ответ:
        их надо сперва завести. Причина говорит, где."""
        why = dating_mail_check('Имя', 'Hi [[firstName]]!', set())

        assert 'Переменные' in why


class TestVarName:
    """Каким может быть имя переменной."""

    @pytest.mark.parametrize('name', ['firstName', 'last_name', 'a', 'x1', '_x'])
    def test_latin_names_are_fine(self, name):
        assert dating_mail_var_check(name) == ''

    @pytest.mark.parametrize('name', ['её_имя', 'имя', '1st', 'a-b', 'a b', 'a.b'])
    def test_anything_else_is_refused(self, name):
        """⚠ Только латиница — и это не вкус.

        Имя уезжает агенту у соседа и возвращается ключом в JSON; на этом пути
        один неверно угаданный кодек превращает `[[её_имя]]` в мусор, который не
        совпадёт ни с чем — молча.
        """
        assert dating_mail_var_check(name) != ''

    @pytest.mark.parametrize('name', ['', '   ', None])
    def test_name_is_required(self, name):
        assert 'имя' in dating_mail_var_check(name)


class TestMissing:
    """Чего не хватает, чтобы письмо ушло."""

    def test_nothing_missing_when_all_filled(self):
        assert dating_mail_missing('Hi [[firstName]]!', {'firstName': 'Diana'}) == []

    def test_absent_value_is_missing(self):
        assert dating_mail_missing('Hi [[firstName]]!', {}) == ['firstName']

    @pytest.mark.parametrize('value', ['', '   ', None])
    def test_empty_value_counts_as_missing(self, value):
        """⚠ Строка есть, но пустая — это «завели, а заполнить забыли».

        Подставлять пустоту нельзя: «Меня зовут , мне  лет» выдаёт рассылку вернее,
        чем что-либо ещё.
        """
        assert dating_mail_missing('Hi [[firstName]]!',
                                   {'firstName': value}) == ['firstName']

    def test_order_follows_the_letter(self):
        """Порядок — как в письме: человек читает его сверху вниз и там же ищет
        пропуск."""
        missing = dating_mail_missing('[[lastName]] и [[firstName]]', {})

        assert missing == ['lastName', 'firstName']

    def test_letter_without_vars_never_misses(self):
        assert dating_mail_missing('Hello!', {}) == []


class TestRender:
    """Подстановка."""

    def test_substitutes_known_names(self):
        text = dating_mail_render('Hi [[firstName]], I am [[lastName]].',
                                  {'firstName': 'John', 'lastName': 'Smith'})

        assert text == 'Hi John, I am Smith.'

    def test_name_without_value_stays_as_is(self):
        """⚠ Скобки остаются, а не превращаются в пустоту.

        Сохранить непонятное виднее, чем стереть: так пропуск виден на месте, где
        он случится. До отправки такое и не доходит — `dating_mail_missing` не даёт
        отправить письмо с незаполненной переменной.
        """
        assert dating_mail_render('[[чужое]]', {}) == '[[чужое]]'
        assert dating_mail_render('[[firstName]]', {'firstName': ''}) == '[[firstName]]'

    def test_plain_brackets_survive(self):
        """⚠ Ради этого формат и закрытый: одиночная скобка в живом тексте не
        должна ронять подстановку, как ронял бы её `str.format`.

        Проверяются обе породы: квадратные — потому что ими пишутся переменные,
        фигурные — потому что ими они писались раньше и в старых текстах остались.
        """
        text = 'Смайлик :{ и [ одна скобка ] и {{было_переменной}}'

        assert dating_mail_render(text, {}) == text

    def test_substitution_is_not_recursive(self):
        """⚠ Агент вернул `[[lastName]]` значением, собеседник вписал такое в имя
        на сайте — это должно остаться текстом. Иначе чужие данные управляли бы
        нашим шаблоном."""
        text = dating_mail_render('Hi [[firstName]]!',
                                  {'firstName': '[[lastName]]', 'lastName': 'секрет'})

        assert text == 'Hi [[lastName]]!'

@pytest.fixture
async def registry(auth_client):
    """Две переменные на тестовом сайте: `firstName` без умолчания, `lastName` с ним.

    ⚠ Нужна почти каждому тесту хранения: шаблон с переменной, которой нет в
    реестре сайта, не сохраняется — в этом вся суть новой модели.
    """
    made = []
    for name, default in (('firstName', ''), ('lastName', 'Smith')):
        answer = await auth_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': name, 'title': name, 'default_value': default})
        if answer.status_code == 200:
            made.append(answer.json()['result']['id'])

    yield

    for var_id in made:
        await auth_client.delete(f'/api/dating/mail/var/{var_id}')


class TestMailStorage:
    """Хранение и отбор — по HTTP, в живую базу."""

    @pytest.fixture
    async def mail(self, auth_client, registry):
        """Свой шаблон, который убирается за собой."""
        answer = await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': 'тест-шаблон', 'body': 'Hi [[firstName]]!'})
        mail_id = answer.json()['result']['id']

        yield mail_id

        await auth_client.delete(f'/api/dating/mail/{mail_id}')

    async def test_created_template_comes_back(self, auth_client, mail):
        rows = (await auth_client.get(
            f'/api/dating/mail?site_id={SITE}')).json()['result']

        assert [row['id'] for row in rows] == [mail]

    async def test_answer_carries_vars(self, auth_client, mail):
        """Разбор переменных считает сервер: он один на всех, и вторая копия
        правил на странице разошлась бы с первой."""
        row = (await auth_client.get(
            f'/api/dating/mail?site_id={SITE}')).json()['result'][0]

        assert row['vars'] == ['firstName']

    async def test_answer_has_no_preview(self, auth_client, mail):
        """⚠ Предпросмотра в ответе **нет**, и это осознанно.

        Поле показывало шаблон с подставленными умолчаниями, а блок под полем
        письма повторял его слово в слово: умолчаний у переменных нет, подставлять
        нечего. Считалось оно при этом на каждый шаблон в каждом ответе — вместе с
        лишним чтением реестра переменных.

        Настоящие значения подставляются в пуле, там их и читают.
        """
        row = (await auth_client.get(
            f'/api/dating/mail?site_id={SITE}')).json()['result'][0]

        assert 'preview' not in row

    async def test_templates_are_kept_apart_by_site(self, auth_client, mail):
        """Шаблоны одного сайта не видны у другого: у сайтов разная аудитория и
        разные правила модерации."""
        rows = (await auth_client.get(
            '/api/dating/mail?site_id=999778')).json()['result']

        assert rows == []

    async def test_no_site_gives_nothing(self, auth_client, mail):
        """⚠ Пустой список, а не все шаблоны всех сайтов: страница без выбранного
        сайта не должна показывать чужое."""
        assert (await auth_client.get('/api/dating/mail')).json()['result'] == []

    async def test_update_changes_the_row(self, auth_client, mail):
        await auth_client.put(f'/api/dating/mail/{mail}', json={
            'site_id': SITE, 'name': 'правленый', 'body': 'Hello!', 'enabled': False})

        row = (await auth_client.get(
            f'/api/dating/mail?site_id={SITE}')).json()['result'][0]

        assert row['name'] == 'правленый'
        assert row['enabled'] is False

    async def test_site_does_not_move_on_update(self, auth_client, mail):
        """⚠ Переезд на другой сайт — не правка текста. У журнала отправок сайт
        свой, и уехавший шаблон рассорил бы его с историей."""
        await auth_client.put(f'/api/dating/mail/{mail}', json={
            'site_id': 999778, 'name': 'тест-шаблон', 'body': 'Hello!'})

        row = (await auth_client.get(
            f'/api/dating/mail?site_id={SITE}')).json()['result'][0]

        assert row['site_id'] == SITE, 'шаблон уехал на другой сайт'

    async def test_deleted_template_is_gone(self, auth_client):
        answer = await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': 'на удаление', 'body': 'Hi!'})
        mail_id = answer.json()['result']['id']

        await auth_client.delete(f'/api/dating/mail/{mail_id}')

        rows = (await auth_client.get(
            f'/api/dating/mail?site_id={SITE}')).json()['result']

        assert mail_id not in [row['id'] for row in rows]

    async def test_missing_template_is_404(self, auth_client):
        assert (await auth_client.delete('/api/dating/mail/999999')).status_code == 404
        assert (await auth_client.put('/api/dating/mail/999999', json={
            'site_id': SITE, 'name': 'x', 'body': 'y'})).status_code == 404


class TestMailIsChecked:
    """Ручка пользуется той же проверкой — и на заведении, и на правке."""

    async def test_unknown_variable_is_refused_on_create(self, auth_client, registry):
        answer = await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': 'опечатка', 'body': 'Hi [[frstName]]'})

        assert answer.status_code == 422
        assert 'frstName' in answer.json()['detail']

    async def test_unknown_variable_is_refused_on_update(self, auth_client, registry):
        """Две проверки разошлись бы, и правкой можно было бы завести то, что не
        пропускает заведение."""
        created = await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': 'годный', 'body': 'Hi!'})
        mail_id = created.json()['result']['id']

        try:
            answer = await auth_client.put(f'/api/dating/mail/{mail_id}', json={
                'site_id': SITE, 'name': 'годный', 'body': 'Hi [[frstName]]'})

            assert answer.status_code == 422
        finally:
            await auth_client.delete(f'/api/dating/mail/{mail_id}')

    async def test_refused_template_is_not_stored(self, auth_client, registry):
        before = (await auth_client.get(
            f'/api/dating/mail?site_id={SITE}')).json()['result']

        await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': 'плохой', 'body': 'Hi [[нет_такой]]'})

        after = (await auth_client.get(
            f'/api/dating/mail?site_id={SITE}')).json()['result']

        assert len(after) == len(before)


class TestMailRights:
    """⚠ Шаблон правит любой вошедший, реестр переменных — только админ.

    Прежде админской была вся правка: письмо уходит от всех анкет сайта многим
    людям, и ошибка в шаблоне — массовая рассылка ерунды от нашего имени. Довод не
    выдержал живого употребления: текст письма правят **первым**, когда рассылка
    идёт плохо, и это работа того, кто ведёт анкеты. Оператор открывал «Письма →
    Шаблоны» и не видел даже кнопки «завести».

    Риск закрыт не правами, а проверкой: шаблон с неизвестной переменной не
    сохранится, письмо с незаполненной — не уйдёт.
    """

    async def test_operator_can_read(self, dating_client):
        answer = await dating_client.get(f'/api/dating/mail?site_id={SITE}')

        assert answer.status_code == 200

    async def test_operator_sees_the_registry(self, dating_client):
        """Реестр переменных читают все: по нему страница объясняет, что вообще
        подставляется."""
        answer = await dating_client.get(f'/api/dating/mail/var?site_id={SITE}')

        assert answer.status_code == 200

    async def test_operator_can_create_and_delete(self, dating_client):
        """Завести и убрать шаблон — работа оператора."""
        made = await dating_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': 'оператор завёл', 'body': 'Hello there!'})

        assert made.status_code == 200, made.text[:200]

        mail_id = made.json()['result']['id']
        try:
            правка = await dating_client.put(f'/api/dating/mail/{mail_id}', json={
                'site_id': SITE, 'name': 'оператор поправил', 'body': 'Hi again!'})

            assert правка.status_code == 200
        finally:
            assert (await dating_client.delete(
                f'/api/dating/mail/{mail_id}')).status_code == 200

    async def test_operator_cannot_touch_the_registry(self, dating_client):
        """⚠ Переменная остаётся за админом, и это не половинчатость.

        Шаблон — текст письма, его правят каждый день. Реестр переменных описывает,
        **из чего** письмо собирается: новое имя надо согласовать с промптом агента
        и со значениями у анкет, и заводится оно раз в полгода.
        """
        answer = await dating_client.post('/api/dating/mail/var', json={
            'site_id': SITE, 'name': 'tst_var', 'title': 'Проба', 'source': 'profile'})

        assert answer.status_code == 403

    async def test_closed_without_login(self, client):
        answer = await client.get(f'/api/dating/mail?site_id={SITE}')

        assert answer.status_code in (401, 307)


class TestMailPage:
    """⚠ Отдельной страницы «Письма» больше нет.

    Шаблоны и переменные переехали подвкладками в «Профили» → «Письма»: текст
    письма правят там же, где смотрят на пул и на входящие. Уходить со страницы
    ради правки шаблона больше не надо, а две страницы со словом «письма» рядом
    в меню заставляли спрашивать «а это которые».
    """

    async def test_old_address_leads_to_the_new_place(self, auth_client):
        """⚠ Старый адрес отвечает переадресацией, а не 404.

        Он прожил месяцы: лежит в закладках, в переписке и на снимках экрана.
        Отказ на нём читался бы как поломка панели, а не как переезд.
        """
        answer = await auth_client.get('/admin/dating/mail', follow_redirects=False)

        assert answer.status_code == 302
        assert answer.headers['location'].endswith('/admin/dating/profile')

    async def test_operator_may_switch_the_work(self, dating_client):
        """⚠ **Оператор щёлкает выключатели занятий, и это исправление.**

        Прежде страница гасила их всем, кроме админа: считалось, что раз
        выключатель про **весь сайт**, а не про открытую анкету, то и правка его —
        дело админа. Довод верен про адреса и частоты (они остались за админом,
        страница «Настройки сайтов»), но неверен про работу: «включить рассылку»,
        «поставить смены», «снять генерацию ответов» — ровно то, ради чего
        оператора анкет и заводят. Он открывал «Профили», видел запертые галочки и
        звал админа щёлкнуть за него.
        """
        page = (await dating_client.get('/admin/dating/profile')).text

        assert 'const CAN_EDIT_SITE = true' in page

    async def test_admin_may_edit(self, auth_client):
        page = (await auth_client.get('/admin/dating/profile')).text

        assert 'const CAN_EDIT_SITE = true' in page

    async def test_variable_braces_are_square(self, auth_client):
        """⚠ Переменные пишутся квадратными скобками, и это не косметика.

        Страницу собирает Jinja, а двойные фигурные — её собственный синтаксис:
        пока переменные писались ими, литерал в коде страницы Jinja пыталась
        вычислить как выражение и роняла разбор файла целиком. Обходилось это
        сборкой скобок из констант (`'{' + '{'`) в каждом месте, где они
        упоминались.

        Проверка держит две вещи разом: что литерал в коде страницы жив (значит,
        Jinja его не тронула) и что костыль убран — вернись он, вернулась бы и
        причина.
        """
        page = (await auth_client.get('/admin/dating/profile')).text

        assert 'placeholder="Hello, [[girl_name]]!"' in page
        assert "const OPEN = " not in page, 'костыль со сборкой скобок вернулся' 

    async def test_menu_has_no_separate_page(self, auth_client):
        """⚠ Пункта «Письма» в шапке больше нет, и это часть переезда.

        Оставленный, он вёл бы на переадресацию — то есть на «Профили», но с
        другим названием. Человек нажимал бы «Письма» и попадал в «Профили»,
        не понимая, куда делась страница.
        """
        page = (await auth_client.get('/admin/dating/profile')).text

        assert '/admin/dating/mail"' not in page
