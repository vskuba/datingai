"""Собеседники: карточка человека, которому пишут.

⚠ **Главное здесь — накопитель по требованию, а не зеркало списков.** Людей в
сети на живом сайте сто с лишним, всего на сайте тысячи; строка появляется, когда
о человеке спросили. Отсюда и правила, которые проверяются ниже: повторный заход
обновляет, а не задваивает; пустое поле ленты не затирает известное; правка
человека переживает заходы ленты.

Хранение — по HTTP, в живую панель с живой базой.

⚠ **Номер сайта выдуманный**, как и в остальной сюите: `site_id` — ключ в чужую
базу, и живые карточки задеть невозможно. Номера людей с приставкой `tst-`: они
свои, и «что завела, то и убрала» держится на этом.
"""

import uuid

import pytest

pytestmark = pytest.mark.mail
SITE = 999_777


@pytest.fixture
async def companion(auth_client):
    """Своя карточка, которая убирается за собой — **со строкой**.

    ⚠ Ручки удаления у карточек нет намеренно: их не удаляют, а накапливают —
    человек на сайте никуда не девается. Сюита поэтому долго обходилась стиранием
    полей: строка оставалась, но пустой и с номером `tst-…`.

    Так копиться и стало: **269 строк за день прогонов**, три из них на живом
    сайте — и те попали человеку на глаза, во вкладку «Собеседники». Пустая
    тестовая карточка ничем не лучше заполненной, если она видна в панели.

    Поэтому строку убираем целиком, прямо из базы. Ручку для этого заводить
    незачем: удаление нужно сюите, а не панели, и появись оно наружу — им бы
    удаляли карточки с описаниями, которые собирали руками.
    """
    uid = f'tst-{uuid.uuid4().hex[:10]}'
    answer = await auth_client.post('/api/dating/companion', json={
        'site_id': SITE, 'uid': uid, 'name': 'Julian', 'age': 75,
        'city': 'Rudkobing', 'country': 'Denmark',
        'photo': 'https://example.com/x.jpg'})

    yield answer.json()['result']

    await _drop(auth_client, SITE, uid)


class TestNamePartsAreStored:
    """⚠⚠ Части имени лежат в карточке и пишутся **вместе** с именем.

    Прежде их выводили из `name` на чтении, и против колонок был прямой довод:
    они разойдутся с именем в первую же правку, и письмо уйдёт с прежним
    обращением. Довод снят не обещанием, а единственностью двери
    (`companion_card.companion_save`) — она пересчитывает части всяким вызовом,
    который меняет имя. Ниже это и проверяется, включая разойдутся-ли.
    """

    async def test_parts_are_filled_on_creation(self, auth_client, companion):
        """«Julian» — одно слово, и первое имя обязано быть."""
        assert companion['firstname'] == 'Julian'
        assert companion['lastname'] == ''

    async def test_two_word_name_splits(self, auth_client):
        uid = f'tst-{uuid.uuid4().hex[:10]}'
        made = (await auth_client.post('/api/dating/companion', json={
            'site_id': SITE, 'uid': uid, 'name': 'Brian Ritter'})).json()['result']

        try:
            assert made['firstname'] == 'Brian'
            assert made['lastname'] == 'Ritter'
        finally:
            await _drop(auth_client, SITE, uid)

    async def test_the_shout_is_tamed_before_splitting(self, auth_client):
        """⚠ Регистр правится **до** разбора, иначе в первое слово уедет крик."""
        uid = f'tst-{uuid.uuid4().hex[:10]}'
        made = (await auth_client.post('/api/dating/companion', json={
            'site_id': SITE, 'uid': uid,
            'name': 'VINCENT PITCHEN'})).json()['result']

        try:
            assert made['name'] == 'Vincent Pitchen'
            assert made['firstname'] == 'Vincent'
        finally:
            await _drop(auth_client, SITE, uid)

    async def test_parts_follow_a_renamed_card(self, auth_client, companion):
        """⚠⚠⚠ Ради этого дверь и одна: имя сменилось — части поехали за ним.

        Разойдись они, письмо звало бы человека прежним именем, и увидеть это
        можно было бы только в тексте у живого получателя.
        """
        again = (await auth_client.post('/api/dating/companion', json={
            'site_id': SITE, 'uid': companion['uid'],
            'name': 'Semen Gorbunkov'})).json()['result']

        assert again['name'] == 'Semen Gorbunkov'
        assert again['firstname'] == 'Semen'
        assert again['lastname'] == 'Gorbunkov'

    async def test_parts_cannot_be_set_from_outside(self, auth_client, companion):
        """⚠ Снаружи части не пишутся — только считаются из имени.

        Дай мы их присылать, появился бы второй способ задать имя, и он разошёлся
        бы с первым.
        """
        from src.dating.companion_card import COMPANION_CARD_COLUMNS

        assert 'firstname' not in COMPANION_CARD_COLUMNS
        assert 'lastname' not in COMPANION_CARD_COLUMNS

    async def test_the_door_is_the_only_writer_of_name(self):
        """⚠⚠ Сторож против второй двери: `name` пишется одним местом.

        Появись второй `INSERT`/`UPDATE` с колонкой `name`, части снова могли бы
        отстать от имени — то самое возражение, ради снятия которого дверь и
        завели.
        """
        import pathlib
        import re

        writers = []
        for path in pathlib.Path('src').rglob('*.py'):
            if path.name == 'companion_card.py':
                continue

            code = path.read_text(encoding='utf-8')
            for chunk in re.findall(
                    r'(?:INSERT[^;\'"]{0,80}INTO\s+companion\b'
                    r'|UPDATE\s+companion\b)[^;]{0,400}', code, re.I):
                if re.search(r'\bname\b', chunk):
                    writers.append(f'{path}: {" ".join(chunk.split())[:70]}')

        assert not writers, 'имя карточки пишут мимо двери: ' + '; '.join(writers)


class TestCompanionSave:
    """Заведение и обновление из ленты."""

    async def test_created_card_comes_back(self, auth_client, companion):
        rows = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid']}")).json()['result']

        assert [row['id'] for row in rows] == [companion['id']]
        assert rows[0]['name'] == 'Julian'
        assert rows[0]['age'] == 75

    async def test_second_visit_updates_not_duplicates(self, auth_client, companion):
        """Человек на сайте один: повторный заход обновляет строку, а не заводит
        вторую. Держит это уникальный ключ пары «сайт + номер»."""
        await auth_client.post('/api/dating/companion', json={
            'site_id': SITE, 'uid': companion['uid'], 'name': 'Julian B.'})

        rows = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid']}")).json()['result']

        assert len(rows) == 1
        assert rows[0]['name'] == 'Julian B.'

    async def test_empty_field_does_not_erase(self, auth_client, companion):
        """⚠ Лента отдаёт разный набор, и заход без города не должен стирать
        город, снятый прошлым: пустое значение означает «не знаю», а не «сотри»."""
        await auth_client.post('/api/dating/companion', json={
            'site_id': SITE, 'uid': companion['uid'], 'name': 'Julian'})

        row = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid']}")).json()['result'][0]

        assert row['city'] == 'Rudkobing'
        assert row['country'] == 'Denmark'
        assert row['age'] == 75

    async def test_cards_are_kept_apart_by_site(self, auth_client, companion):
        """Один и тот же номер на другом сайте — другой человек."""
        rows = (await auth_client.get(
            '/api/dating/companion?site_id=999778')).json()['result']

        assert companion['uid'] not in [row['uid'] for row in rows]

    async def test_no_site_gives_nothing(self, auth_client, companion):
        """Пустой список, а не все собеседники всех сайтов."""
        assert (await auth_client.get('/api/dating/companion')).json()['result'] == []


class TestCompanionAbout:
    """Описание и почта — то, что заполняет человек."""

    async def test_new_card_has_no_about(self, auth_client, companion):
        """⚠ `about_state = none` — «не собирали», а не «там пусто». Различать
        обязательно: первое повод сходить, второе — повод не ходить второй раз."""
        assert companion['about_state'] == 'none'
        assert companion['has_about'] is False

    async def test_edit_fills_about(self, auth_client, companion):
        answer = await auth_client.put(f"/api/dating/companion/{companion['id']}",
                                       json={'email': 'j@example.com',
                                             'about': 'Любит парусный спорт.'})
        row = answer.json()['result']

        assert row['email'] == 'j@example.com'
        assert row['about'] == 'Любит парусный спорт.'
        assert row['has_about'] is True

    async def test_hand_written_about_counts_as_done(self, auth_client, companion):
        """⚠ Состояние отвечает на вопрос «искали ли подробности», и вписанный
        руками текст — такой же ответ, как снятый браузером: искать второй раз
        незачем."""
        answer = await auth_client.put(f"/api/dating/companion/{companion['id']}",
                                       json={'about': 'Что-то про него.'})

        assert answer.json()['result']['about_state'] == 'done'

    async def test_erased_about_returns_to_none(self, auth_client, companion):
        """Стёрли текст — состояние возвращается: значит, за ним ещё сходят."""
        await auth_client.put(f"/api/dating/companion/{companion['id']}",
                              json={'about': 'Текст'})
        answer = await auth_client.put(f"/api/dating/companion/{companion['id']}",
                                       json={'about': '   '})

        assert answer.json()['result']['about_state'] == 'none'
        assert answer.json()['result']['has_about'] is False

    async def test_feed_does_not_erase_hand_written(self, auth_client, companion):
        """⚠ Ради этого правка и отделена от заведения. Заход ленты повторяется
        при каждом щелчке по человеку, и упоминай он `about` — стирал бы написанное
        каждый раз."""
        await auth_client.put(f"/api/dating/companion/{companion['id']}",
                              json={'email': 'j@example.com', 'about': 'Парусный спорт'})

        await auth_client.post('/api/dating/companion', json={
            'site_id': SITE, 'uid': companion['uid'], 'name': 'Julian', 'age': 76})

        row = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid']}")).json()['result'][0]

        assert row['about'] == 'Парусный спорт', 'лента затёрла описание'
        assert row['email'] == 'j@example.com', 'лента затёрла почту'
        assert row['age'] == 76, 'а вот возраст лента обновить должна'

    async def test_missing_card_is_404(self, auth_client):
        answer = await auth_client.put('/api/dating/companion/999999',
                                       json={'about': 'x'})

        assert answer.status_code == 404


class TestCompanionBio:
    """Что уезжает агенту вместо фактов, пока фактов о человеке ещё нет."""

    async def test_bio_is_built_from_the_card(self, auth_client, companion):
        """⚠⚠ Собирает **сервер**, той же функцией, что уходит в чат и в письма:
        своя сборка на странице разошлась бы с настоящей молча, и человек сверял бы
        ответ агента с текстом, которого агент не видел."""
        answer = await auth_client.post(
            f"/api/dating/companion/{companion['id']}/bio",
            json={'about': 'О себе: любит парусный спорт'})
        assert answer.status_code == 200, answer.text
        bio = answer.json()['result']['bio']

        assert 'Name: Julian' in bio
        assert 'Age: 75' in bio
        assert 'Lives in: Rudkobing, Denmark' in bio
        assert 'About Myself: любит парусный спорт' in bio, \
            'подпись описания не переведена'

    async def test_it_shows_what_is_typed_not_what_is_stored(self, auth_client,
                                                             companion):
        """⚠ Кнопка показывает, во что сложится **набранное**, а не сохранённое:
        её затем и нажимают, что текст ещё не сохранён."""
        await auth_client.put(f"/api/dating/companion/{companion['id']}",
                              json={'about': 'О себе: старое'})

        bio = (await auth_client.post(
            f"/api/dating/companion/{companion['id']}/bio",
            json={'about': 'О себе: новое'})).json()['result']['bio']

        assert 'новое' in bio and 'старое' not in bio

    async def test_it_writes_nothing(self, auth_client, companion):
        """⚠ Это предпросмотр. Записывает `PUT /{id}`, и перепутать их значило бы
        сохранять набранное при каждом взгляде на Bio."""
        await auth_client.put(f"/api/dating/companion/{companion['id']}",
                              json={'about': 'О себе: сохранённое'})
        await auth_client.post(f"/api/dating/companion/{companion['id']}/bio",
                               json={'about': 'О себе: мимолётное'})

        row = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid']}")
               ).json()['result'][0]

        assert row['about'] == 'О себе: сохранённое', 'предпросмотр записал текст'

    async def test_missing_card_is_404(self, auth_client):
        answer = await auth_client.post('/api/dating/companion/999999/bio',
                                        json={'about': 'x'})

        assert answer.status_code == 404


class TestCompanionFind:
    """Поиск режет на сервере: карточек накопятся сотни."""

    async def test_find_by_name(self, auth_client, companion):
        rows = (await auth_client.get(
            f'/api/dating/companion?site_id={SITE}&find=Julian')).json()['result']

        assert companion['uid'] in [row['uid'] for row in rows]

    async def test_find_by_uid(self, auth_client, companion):
        rows = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid'][:8]}")).json()['result']

        assert companion['uid'] in [row['uid'] for row in rows]

    async def test_nothing_found(self, auth_client, companion):
        rows = (await auth_client.get(
            f'/api/dating/companion?site_id={SITE}&find=zzzнетникого')).json()['result']

        assert rows == []


class TestCompanionOnline:
    """Точка «сейчас на сайте» — из снимка ленты, а не походом на сайт.

    ⚠ Лента «Все онлайн» снимается своим кругом и живёт в Redis; сходи мы за ней
    ради списка карточек, открытие вкладки стоило бы сорока секунд браузера.
    """

    async def test_answer_has_both_flags(self, auth_client, companion):
        """⚠ Полей два, а не одно.

        «Не в сети» и «не знаем» — разные ответы: снимка может не быть вовсе или он
        протух. Слей мы их в `online: False`, панель врала бы про каждого, кого не
        было в последнем снимке.
        """
        rows = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid']}"
        )).json()['result']

        assert 'online' in rows[0]
        assert 'online_known' in rows[0]

    async def test_unknown_site_is_not_online(self, auth_client, companion):
        """У выдуманного сайта снимка нет — значит «не знаем», и точки не будет.

        ⚠ `online` при этом `False`: точку рисуем **только когда уверены**. Серая
        точка читалась бы как «оффлайн», а мы не знаем.
        """
        rows = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid']}"
        )).json()['result']

        assert rows[0]['online'] is False
        assert rows[0]['online_known'] is False, 'снимок взялся ниоткуда'

    async def test_three_states_are_shown(self, auth_client):
        """⚠ Состояний **три**, и третье — молчание.

        Зелёное «в сети», серое «не в сети» и **ничего**, когда лента устарела:
        рисовать в этом случае серое было бы неправдой — оно читается как «его
        нет», а мы просто не смотрели. Различает их `online_known`.

        Серого поначалу не было вовсе: рассудили, что «нет» и «не знаем» глазом
        неразличимы. Список от этого выглядел так, будто статуса нет вообще, — на
        что человек и указал.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'state-dot' in page, 'точки состояния нет'
        assert 'state-word' in page, 'состояния словами нет'
        assert 'row.online_known' in page, 'третье состояние не различается'

    async def test_state_is_words_and_a_dot_together(self, auth_client):
        """⚠ Слово и точка стоят **рядом**, а не в разных местах карточки.

        Точка была на аватарке: казалось, что там её видно и в списке, и на крупном
        фото. Но фото есть не у всех — у карточки без него она висела в пустом сером
        квадрате, — а когда слово переехало к номеру, знак и подпись разошлись.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'в сети' in page and 'не в сети' in page
        assert 'comp-face-wrap' in page and 'position: relative' not in \
            page.split('.comp-face-wrap')[1][:60], 'точка снова висит на аватарке'

    async def test_girls_show_the_same_state(self, auth_client):
        """⚠ Состояние показывают **обе** вкладки, и одним знаком.

        Вопрос у них общий («на сайте ли человек»), а источник разный: у анкеты это
        её присутствие, которое держит воркер, у собеседника — снимок ленты. Вид от
        этого не меняется: два набора знаков для одного вопроса человеку пришлось
        бы разгадывать дважды.
        """
        page = (await auth_client.get('/admin/dating')).text

        # Строку девушки рисует свой код — проверяем, что и он берёт общий класс.
        assert "row.status === 'online' ? ' on' : ''" in page, \
            'у девушек нет состояния'
        assert 'prof-login' in page


class TestCompanionDrop:
    """Удаление карточки.

    ⚠ **Прежде удаления не было, и это было осознанным решением**: карточки
    накапливают, а не удаляют — человек на сайте никуда не девается. Решение
    оказалось неполным: карточка заводится по щелчку в ленте и сбором описания, то
    есть иногда по ошибке или о том, кто больше не нужен, — а список читают
    глазами.
    """

    async def test_card_is_gone(self, auth_client, companion):
        await auth_client.delete(f"/api/dating/companion/{companion['id']}")

        rows = (await auth_client.get(
            f"/api/dating/companion?site_id={SITE}&find={companion['uid']}"
        )).json()['result']

        assert rows == []

    async def test_unknown_is_404(self, auth_client):
        answer = await auth_client.delete('/api/dating/companion/99999999')

        assert answer.status_code == 404

    async def test_letters_survive(self, auth_client, companion):
        """⚠ **Готовые письма переживают удаление карточки.**

        `mailing.uid` — просто строка, внешнего ключа на карточку нет: письмо
        помнит, кому оно, само по себе. Иначе удаление карточки уносило бы готовую
        рассылку — а это минуты работы агента.
        """
        from mysql_.mysql_ import mysql_get_db_async

        # Анкета нужна настоящая: у письма внешний ключ на неё. ⚠ Но на
        # **стенде**, а не на живом сайте: анкета сюиты не должна попадать в круги
        # воркера (`test_mail_in.py`, `STAND_SITE`).
        made = await auth_client.post('/api/dating/profile', json={
            'site_id': 999_343,
            'login': f'drop-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})
        profile_id = made.json()['result']

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO mailing'
                ' (site_id, profile_id, mail_id, uid, name, body, status)'
                " VALUES (%s, %s, NULL, %s, 'Кому', 'Текст', 'draft')",
                (SITE, profile_id, companion['uid']))
            await db.connection.commit()

        try:
            await auth_client.delete(f"/api/dating/companion/{companion['id']}")

            letters = (await auth_client.get(
                f'/api/dating/mailing?site_id={SITE}')).json()['result']

            assert companion['uid'] in [row['uid'] for row in letters], \
                'удаление карточки унесло письмо из пула'
        finally:
            await auth_client.delete(f'/api/dating/profile/{profile_id}')


class TestCompanionRights:
    """Работа с людьми сайта открыта всем вошедшим."""

    async def test_operator_reads(self, dating_client):
        answer = await dating_client.get(f'/api/dating/companion?site_id={SITE}')

        assert answer.status_code == 200

    async def test_operator_saves(self, auth_client, dating_client):
        """⚠ Не админское: это работа с людьми сайта, ровно та, ради которой
        панель и открывают. Карточка заводится щелчком по ленте — потребуй мы
        админа, оператор не смог бы работать вовсе."""
        uid = f'tst-{uuid.uuid4().hex[:10]}'
        answer = await dating_client.post('/api/dating/companion', json={
            'site_id': SITE, 'uid': uid, 'name': 'Оператор завёл'})

        try:
            assert answer.status_code == 200
        finally:
            await _drop(auth_client, SITE, uid)

    async def test_closed_without_login(self, client):
        answer = await client.get(f'/api/dating/companion?site_id={SITE}')

        assert answer.status_code in (401, 307)


@pytest.fixture
async def companion_var(auth_client):
    """Переменная про адресата в реестре тестового сайта.

    ⚠ Заводим свою, а не полагаемся на живой реестр: у выдуманного сайта его нет
    вовсе, а брать переменные настоящего значило бы ставить проверку в зависимость
    от того, что кто-то завёл руками.
    """
    made = await auth_client.post('/api/dating/mail/var', json={
        'site_id': SITE, 'name': 'companion_name', 'source': 'companion',
        'title': 'Имя адресата', 'default_value': ''})
    var_id = made.json()['result']['id'] if made.status_code == 200 else 0

    yield var_id

    if var_id:
        await auth_client.delete(f'/api/dating/mail/var/{var_id}')


class TestCompanionVars:
    """Чем подставятся переменные писем для этого человека.

    ⚠ **Своего хранилища у них нет, и заводить его незачем.** Значение выводится
    из карточки: `companion_name` — это `name` адресата. Храни мы их отдельно, они
    разошлись бы с карточкой в первую же правку, и письмо ушло бы со старым именем.
    """

    async def test_value_comes_from_the_card(self, auth_client, companion,
                                             companion_var):
        rows = (await auth_client.get(
            f"/api/dating/companion/{companion['id']}/var")).json()['result']
        got = {row['name']: row for row in rows}

        assert 'companion_name' in got, 'переменной адресата нет в ответе'
        assert got['companion_name']['value'] == 'Julian'
        assert got['companion_name']['field'] == 'name'
        assert got['companion_name']['from_default'] is False

    async def test_empty_field_is_seen(self, auth_client, companion_var):
        """⚠ Пустое значение — не мелочь: письмо с ним не уйдёт вовсе, и узнать об
        этом лучше здесь, чем по молчаливому пропуску в рассылке.

        ⚠ Заводим **отдельную безымянную карточку**, а не обнуляем имя у готовой:
        пустое значение ленты прежнее не затирает (`companion.py`), и такая
        попытка ничего бы не изменила — проверка молча одобрила бы что угодно.
        """
        uid = f'tst-{uuid.uuid4().hex[:10]}'
        made = await auth_client.post('/api/dating/companion', json={
            'site_id': SITE, 'uid': uid, 'name': ''})
        row = made.json()['result']

        try:
            rows = (await auth_client.get(
                f"/api/dating/companion/{row['id']}/var")).json()['result']
            got = {one['name']: one for one in rows}

            assert got['companion_name']['filled'] is False
            assert got['companion_name']['value'] == ''
        finally:
            await _drop(auth_client, SITE, uid)

    async def test_only_companion_source(self, auth_client, companion,
                                         companion_var):
        """Переменные анкеты сюда не попадают: их значения живут у неё, а не у
        адресата."""
        rows = (await auth_client.get(
            f"/api/dating/companion/{companion['id']}/var")).json()['result']

        assert 'girl_name' not in {row['name'] for row in rows}

    async def test_unknown_companion_is_404(self, auth_client):
        answer = await auth_client.get('/api/dating/companion/99999999/var')

        assert answer.status_code == 404


class TestCompanionPages:
    """Вкладка на «Анкетах» и заведение карточки с «Профилей»."""

    async def test_companions_look_like_girls(self, auth_client):
        """⚠ Раскладка та же, что у «Девушек»: список слева, карточка справа.

        Прежде это была сетка карточек во всю ширину с правкой прямо в карточке —
        и расходилась с соседней вкладкой во всём: там выбирают строку и читают
        подробности справа, здесь надо было раскрывать карточку на месте. Две
        раскладки на одной странице человек разгадывает заново каждый раз.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'id="companionRight"' in page, 'правой колонки у собеседников нет'
        assert 'id="companionWho"' in page, 'нет шапки выбранного'
        assert 'id="companion"' in page, 'нет места под карточку'

    async def test_companion_card_has_three_tabs(self, auth_client):
        """Карточка делится на «Общую информацию», «Информацию» и «Переменные».

        Те же вкладки и в том же порядке, что у анкеты девушки. Разное по природе:
        первое — кто он и что с ним делаем, второе — что мы о нём знаем и что из
        этого видит агент, третье — чем подставятся письма.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'compTab' in page, 'нет состояния вкладок карточки'
        assert 'companionInfo' in page, 'нет вкладки «Информация»'
        assert 'compVarsLoad' in page, 'нет загрузки переменных адресата'
        assert '/var`)).result' in page or "/var'" in page

    async def test_bio_lives_on_the_info_tab(self, auth_client):
        """⚠⚠ Bio — тот текст, что уезжает переменной `companion_bio` вместо
        фактов, пока фактов о человеке нет. Посмотреть на него человеку было негде.

        ⚠ Собирает его **сервер**: своя сборка на странице разошлась бы с
        настоящей молча, и человек сверял бы ответ агента с текстом, которого агент
        не видел.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'id="compBio"' in page, 'поля Bio на вкладке нет'
        assert 'id="compBioGet"' in page, 'кнопки «Обновить био» нет'
        assert '/bio`' in page, 'страница собирает био сама, а не ручкой'

    async def test_editing_moved_off_the_main_tab(self, auth_client):
        """⚠⚠ Почта и описание **переехали** на «Информацию», а не скопированы.

        Два поля правки на один текст — это два его состояния на экране разом, и
        вопрос «какое из них сохранится» не имеет хорошего ответа.
        """
        page = (await auth_client.get('/admin/dating')).text
        main = page.split('function companionMain(row)', 1)[1] \
                   .split('function companionInfo(row)', 1)[0]

        assert 'data-field="about"' not in main, 'описание осталось на «Общей»'
        assert 'data-field="email"' not in main, 'почта осталась на «Общей»'
        assert 'data-flag="priority"' in main, '«Приоритет» ушёл с «Общей»'

    async def test_delete_is_in_the_list(self, auth_client):
        """⚠ Крестик — в **строке списка**, а не на карточке справа.

        Убирают карточку, глядя на список («вот эти двое лишние»), а не разглядывая
        одного человека; заодно не надо открывать карточку ради удаления. Виден при
        наведении на строку — как крестик смены на ленте расписания: карточек
        десятки, и десяток красных кружков превратил бы список в предупреждение.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'data-drop=' in page, 'крестика в строке списка нет'
        assert 'companionDrop' in page
        assert 'id="compDrop"' not in page, 'крестик остался и на карточке'

    async def test_list_is_paginated(self, auth_client):
        """⚠ Карточек накапливаются сотни — список листается страницами.

        Тем же подвалом и тем же способом, что список анкет: число строк подгоняется
        под высоту панели живым замером, а не зашитым числом — высота зависит от
        шрифта и размера лица.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'id="companionsPager"' in page, 'подвала с пейджером нет'
        assert 'compRowsFit' in page, 'число строк не подгоняется под высоту'
        assert 'companionsPage = 0' in page, 'страница не сбрасывается на новом списке'

    async def test_page_tab_survives_reload(self, auth_client):
        """⚠ Вкладка страницы переживает перезагрузку.

        Работая с собеседниками, страницу обновляют — посмотреть свежую ленту,
        вернуться из чужого профиля в соседней вкладке браузера. Сбрасывать её на
        «Девушки» каждый раз значит заставлять человека щёлкать по одному и тому же.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'dating.page.tab' in page, 'вкладка страницы не запоминается'

    async def test_selection_survives_reload(self, auth_client):
        """⚠ Выбранный переживает перезагрузку страницы.

        Карточку открывают, чтобы дописать описание, а описание пишут, глядя в
        чужой профиль на другой вкладке браузера, — и возвращаются сюда
        обновлением. Терять при этом выбранного значило бы искать его в списке
        заново каждый раз.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'dating.companion.open' in page, 'выбор не запоминается'
        assert 'companionRemember' in page

    async def test_first_is_open_by_default(self, auth_client):
        """Никто не выбран — открываем первого: пустая правая колонка рядом с
        полным списком читается как «ещё грузится»."""
        page = (await auth_client.get('/admin/dating')).text

        assert 'if (!companionId && companions.length)' in page, \
            'первая карточка не открывается сама'

    async def test_editing_moved_to_the_right(self, auth_client):
        """⚠ Поля правки — **раскрытыми в правой колонке**, а не за кнопкой в
        списке. В колонке место есть, и прятать два поля за щелчком значило бы
        беречь пустоту; в списке было наоборот — там десяток раскрытых форм
        превращался в частокол.
        """
        page = (await auth_client.get('/admin/dating')).text

        assert 'comp-more' not in page, 'кнопка «Править» осталась в списке'
        assert 'comp-field' in page, 'нет полей правки в карточке'

    async def test_dating_page_has_both_tabs(self, auth_client):
        """⚠ Вкладки про разных людей: «Девушки» — наши учётки, «Собеседники» —
        те, кому пишут. У вторых нет ни настроек, ни расписания, ни входа."""
        page = (await auth_client.get('/admin/dating')).text

        assert 'data-tab="girl"' in page
        assert 'data-tab="companion"' in page
        assert 'id="companionPane"' in page

    async def test_profile_page_creates_the_card(self, auth_client):
        """Карточка заводится щелчком по человеку в ленте «Все онлайн» — это и
        есть «по требованию»."""
        page = (await auth_client.get('/admin/dating/profile')).text

        assert 'companionTouch' in page
        assert '/api/dating/companion' in page


async def _drop(auth_client, site_id: int, uid: str) -> None:
    """Убирает следы теста: строку целиком.

    ⚠ **Через базу, а не ручкой** — её нет и заводить не надо (см. фикстуру
    `companion`). Прецедент тот же, что у пула писем (`test_mailing.py`): когда
    убирать надо, а наружу такого действия быть не должно, сюита ходит в базу.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM companion WHERE site_id = %s AND uid = %s',
                         (int(site_id), str(uid)))
        await db.connection.commit()


class TestPriority:
    """⚠ Приоритет в рассылке — **порядок, а не отбор**.

    Пул набирается до потолка и обрывается на полпути по ленте: кому досталось
    письмо, решал порядок, в каком людей отдал сайт, — для панели случайный.
    Отметка отвечает «этим в первую очередь».

    Сделай мы из неё фильтр, включённая галочка у одного человека молча выключила
    бы рассылку всем прочим — а человек отмечал одного, а не отменял остальных.
    Это и проверяется первым.
    """

    async def test_new_card_has_no_priority(self, auth_client, companion):
        """Выключен по умолчанию: отметка у всех — это отсутствие отметки."""
        row = (await auth_client.get(
            f'/api/dating/companion?site_id={SITE}')).json()['result']
        mine = next(one for one in row if one['id'] == companion['id'])

        assert mine['priority'] is False

    async def test_priority_is_switched(self, auth_client, companion):
        on = await auth_client.put(
            f"/api/dating/companion/{companion['id']}/priority", json={'priority': True})

        assert on.status_code == 200
        assert on.json()['result']['priority'] is True

        off = await auth_client.put(
            f"/api/dating/companion/{companion['id']}/priority", json={'priority': False})

        assert off.json()['result']['priority'] is False

    async def test_unknown_card_is_404(self, auth_client):
        answer = await auth_client.put('/api/dating/companion/999999999/priority',
                                       json={'priority': True})

        assert answer.status_code == 404

    async def test_editing_text_does_not_drop_priority(self, auth_client, companion):
        """⚠ Правка почты и описания **не гасит отметку**.

        Ради этого у приоритета своя ручка: `PUT /{id}` пишет текстовые поля разом, и
        уедь галочка с ними — она слетала бы при каждом сохранении описания, которое
        правят куда чаще.
        """
        await auth_client.put(f"/api/dating/companion/{companion['id']}/priority",
                              json={'priority': True})
        await auth_client.put(f"/api/dating/companion/{companion['id']}",
                              json={'email': 'a@b.c', 'about': 'Играет на трубе'})

        row = (await auth_client.get(
            f'/api/dating/companion?site_id={SITE}')).json()['result']
        mine = next(one for one in row if one['id'] == companion['id'])

        assert mine['priority'] is True, 'правка описания сняла приоритет'
        assert mine['about'] == 'Играет на трубе'


class TestPriorityOrders:
    """Как отметка влияет на набор пула: перестановка ленты, а не её отбор."""

    async def test_marked_go_first(self):
        from src.dating.mailing import _by_priority
        from src.dating import mailing

        people = [{'uid': 'a'}, {'uid': 'b'}, {'uid': 'c'}, {'uid': 'd'}]

        async def marked(site_id):
            return {'c', 'a'}

        real, mailing._marked = mailing._marked, marked
        try:
            got = await _by_priority(1, people)
        finally:
            mailing._marked = real

        assert [one['uid'] for one in got] == ['a', 'c', 'b', 'd']

    async def test_the_mark_travels_in_the_record(self):
        """⚠⚠ **Отметка не только сортирует, но и едет в записи** (`priority`).

        Порядок отвечает «кого взять раньше», а набору инвайтов нужно второе:
        отмеченному пишут **все** свободные девушки, а не одна. Без признака он
        спрашивал бы базу заново на каждого человека в ленте — двести запросов на
        заход.
        """
        from src.dating.mailing import _by_priority
        from src.dating import mailing

        people = [{'uid': 'a'}, {'uid': 'b'}]

        async def marked(site_id):
            return {'a'}

        real, mailing._marked = mailing._marked, marked
        try:
            got = await _by_priority(1, people)
        finally:
            mailing._marked = real

        assert [one['priority'] for one in got] == [True, False]
        # ⚠ Лента лежит в кэше снимка: допиши мы поле на месте, изменилось бы то,
        # что показывает страница.
        assert 'priority' not in people[0], 'запись ленты изменили на месте'

    async def test_nobody_is_dropped(self):
        """⚠ Главное здесь: неотмеченные остаются, и все до одного."""
        from src.dating.mailing import _by_priority
        from src.dating import mailing

        people = [{'uid': str(n)} for n in range(20)]

        async def marked(site_id):
            return {'7'}

        real, mailing._marked = mailing._marked, marked
        try:
            got = await _by_priority(1, people)
        finally:
            mailing._marked = real

        assert len(got) == 20, 'отметка превратилась в фильтр'
        assert {one['uid'] for one in got} == {one['uid'] for one in people}
        assert got[0]['uid'] == '7'

    async def test_order_inside_halves_is_kept(self):
        """⚠ Порядок внутри каждой половины — прежний. Перетасуй мы ленту сверх
        нужного, два набора подряд давали бы разных адресатов без причины."""
        from src.dating.mailing import _by_priority
        from src.dating import mailing

        people = [{'uid': one} for one in 'abcdef']

        async def marked(site_id):
            return {'e', 'b'}

        real, mailing._marked = mailing._marked, marked
        try:
            got = [one['uid'] for one in await _by_priority(1, people)]
        finally:
            mailing._marked = real

        assert got == ['b', 'e', 'a', 'c', 'd', 'f']

    async def test_no_marks_changes_nothing(self):
        from src.dating.mailing import _by_priority
        from src.dating import mailing

        people = [{'uid': one} for one in 'abc']

        async def marked(site_id):
            return set()

        real, mailing._marked = mailing._marked, marked
        try:
            got = await _by_priority(1, people)
        finally:
            mailing._marked = real

        assert got == people, 'без отметок лента переставилась'

    async def test_broken_database_does_not_break_the_pool(self):
        """⚠ Сбой базы возвращает ленту как есть. Приоритет — удобство, и падать
        из-за него значило бы менять письма на порядок писем."""
        from src.dating.mailing import _by_priority
        from src.dating import mailing

        people = [{'uid': one} for one in 'abc']

        async def broken(site_id):
            raise RuntimeError('база лежит')

        real, mailing._marked = mailing._marked, broken
        try:
            got = await _by_priority(1, people)
        finally:
            mailing._marked = real

        assert [one['uid'] for one in got] == ['a', 'b', 'c']

    async def test_marked_are_read_by_site(self, auth_client, companion):
        """Отмеченные спрашиваются **по сайту**: чужие метки в чужой пул не идут."""
        from src.dating.mailing import _marked

        await auth_client.put(f"/api/dating/companion/{companion['id']}/priority",
                              json={'priority': True})

        assert companion['uid'] in await _marked(SITE)
        assert companion['uid'] not in await _marked(SITE + 1)
