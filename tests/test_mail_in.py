"""Входящие письма: забор через API сайта и наш признак «просмотрено».

⚠ **Наружу сюита не ходит** (`testing.md`): весь разговор с сайтом идёт через
заглушку (`site_api_fake.py`), подменяющую транспорт в `dating_site_api.py`.

## Что здесь ловится

**Два запроса вместо одного.** Список (`GET /messages`) отдаёт метаданные без текста,
текст — `GET /messages/{id}`. Сорок писем это сорок запросов за телами, и брать их у
писем, которые уже лежат, значило бы платить походом за то, что не меняется.

**«Прочитано» — наше, а не сайта.** У него такого поля нет вовсе: есть `IsReplied`,
«ответили ли», а прочесть и не ответить — обычное дело. Забор не должен гасить нашу
отметку, иначе прочитанное письмо снова считалось бы новым.

**С какого числа спрашиваем.** У сайта `startDate` по умолчанию — сегодняшняя
полночь: не назови мы дату, вчерашние письма он не отдаст вовсе.
"""

import uuid

import pytest

from src.dating.dating_mail_in import (DatingMailInError, dating_mail_in_fetch,
                                       dating_mail_in_unread, _stamp)

from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

pytestmark = pytest.mark.mail
# Сайт для писем — выдуманный: `site_id` у письма просто число.
SITE = 999_777

# Анкета заводится на **стенде** — закладке, которая есть у соседа, но не помечена
# `dating`. Заведение анкеты сверяет сайт у соседа (выдуманный номер оно отвергает:
# «нет сайта с номером 999777»), а `dating_mail_in.profile_id` — внешний ключ.
#
# ⚠ **Раньше здесь стоял живой 343 (Ladadate), и это стало опасно.** У сайта
# включились круги избранных и инвайтов, а они берут **все включённые анкеты
# сайта** — то есть и тестовую, с фальшивым паролем: вызовы LLM, занятая вкладка
# Chromium и строки отказов под анкетой, которой через секунду не станет.
#
# Нашлось это по сюите: два полных прогона из четырёх дали сбои в разных файлах, а
# по отдельности те же файлы зелёные.
#
# ⚠ **Стенда нет в `dating_site`, и это и есть разведение.** Круги обходят сайты
# по строкам той таблицы; сайта в ней нет — ни один круг про анкету не узнает,
# какие бы флаги ни включили у настоящих сайтов завтра.
STAND_SITE = 999_343


@pytest.fixture
async def profile(auth_client):
    """Анкета-получатель со своим паролем — тем, что заглушка считает верным."""
    from mysql_.mysql_ import mysql_get_db_async

    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE, 'login': f'in-{uuid.uuid4().hex[:8]}@example.com',
        'password': SITE_API_FAKE_PASSWORD})).json()['result']

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE dating_profile SET password_source = 'own'"
                         ' WHERE id = %s', (made,))
        await db.connection.commit()
        await db.execute('SELECT id, site_id, login, password, password_source'
                         ' FROM dating_profile WHERE id = %s', (made,))
        row = dict(await db.fetchone())

    # ⚠ Письма кладём на **выдуманный** сайт, а анкета живёт на стенде: их ничто
    # не связывает, `site_id` у письма просто число. Тот же развод и та же
    # причина, что в `test_mailing.py`.
    row['site_id'] = SITE

    yield row

    await auth_client.delete(f'/api/dating/profile/{made}')


async def _fetch(profile):
    return await dating_mail_in_fetch(profile, SITE_API_FAKE_BASE,
                                      SITE_API_FAKE_PASSWORD)


async def _rows(profile_id: int) -> list[dict]:
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT message_id, uid, name, body, seen, is_replied, date_post'
            ' FROM dating_mail_in WHERE profile_id = %s ORDER BY message_id',
            (int(profile_id),))

        return [dict(row) for row in await db.fetchall() or []]


class TestFetch:
    """Забор писем."""

    async def test_letters_land_with_bodies(self, site_api, profile):
        got = await _fetch(profile)
        rows = await _rows(profile['id'])

        assert got['added'] == 2, 'письма не легли'
        assert len(rows) == 2
        assert rows[0]['name'] == 'Semen'
        assert rows[0]['body'], 'текст письма не забран'

    async def test_body_comes_from_the_second_call(self, site_api, profile):
        """⚠ Текста в списке нет — за ним ходят отдельно, по номеру письма."""
        await _fetch(profile)

        paths = [path for _, path in site_api.calls]

        assert any(p.endswith('/messages') for p in paths), 'список не спрашивали'
        assert sum('/messages/' in p and 'check-send' not in p for p in paths) == 2, \
            'тела писем не спрашивали по одному'

    async def test_known_letters_are_not_refetched(self, site_api, profile):
        """⚠ **Тело у известного письма не спрашиваем второй раз.** Оно не меняется,
        а запрос стоит похода к сайту: сорок писем — сорок запросов на каждый заход.
        """
        await _fetch(profile)
        site_api.calls.clear()

        again = await _fetch(profile)
        paths = [path for _, path in site_api.calls]

        assert again['added'] == 0, 'письма легли второй раз'
        assert not [p for p in paths if '/messages/' in p], \
            'за телами сходили повторно'

    async def test_new_letter_is_added(self, site_api, profile):
        await _fetch(profile)
        site_api.letters.append({
            'MessageId': 300, 'DatePost': '2026-09-09T10:00:00Z',
            'IsReplied': False,
            'User': {'AccountId': '777', 'Name': 'Marco', 'Age': 44,
                     'City': 'Rome', 'Country': 'Italy', 'ProfilePhoto': None}})

        got = await _fetch(profile)

        assert got['added'] == 1
        assert any(row['message_id'] == 300 for row in await _rows(profile['id']))

    async def test_start_date_is_asked(self, site_api, profile):
        """⚠ Дату называем всегда: у сайта `startDate` по умолчанию — сегодняшняя
        полночь, и вчерашние письма он бы не отдал вовсе."""
        await _fetch(profile)

        # ⚠ Смотрим полные адреса, а не пути: `startDate` живёт в запросе, а в
        # пути его нет вовсе.
        assert any('startDate=' in url for url in site_api.urls), \
            'спросили без даты — вчерашние письма потерялись бы'

    async def test_no_api_base_is_told_plainly(self, site_api, profile):
        with pytest.raises(DatingMailInError) as got:
            await dating_mail_in_fetch(profile, '', SITE_API_FAKE_PASSWORD)

        assert 'Адрес API' in str(got.value)

    async def test_site_refusal_is_told_in_words(self, site_api, profile):
        site_api.refuse(403, {'Error': 'Too many requests',
                              'Reason': 'ERROR_TOO_MANY_REQUESTS'}, on='/messages')

        with pytest.raises(DatingMailInError) as got:
            await _fetch(profile)

        assert 'слишком часто' in str(got.value)

    async def test_broken_body_does_not_break_the_pass(self, site_api, profile):
        """⚠ Одно неудачное тело не роняет весь заход: письмо ляжет без текста и
        будет видно в списке, а текст доедет следующим заходом."""
        site_api.refuse(500, {'Error': 'boom'}, on='/messages/201')

        got = await _fetch(profile)
        rows = await _rows(profile['id'])

        assert got['added'] == 2, 'заход оборвался из-за одного тела'
        assert [row for row in rows if not row['body']], 'письмо без тела не легло'


class TestSeen:
    """⚠ «Просмотрено» — наш признак, и забор его не трогает."""

    async def test_fetch_does_not_reset_seen(self, auth_client, site_api, profile):
        """Главная проверка: перечитали письма — прочитанное осталось прочитанным.

        У сайта признака «прочитано» нет вовсе, и затри его забор — каждое обновление
        списка возвращало бы значок «новые письма» на карточку анкеты.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f"/api/dating/mail-in?profile_id={profile['id']}")).json()['result']
        await auth_client.put(f"/api/dating/mail-in/{rows[0]['id']}/seen",
                              json={'seen': True})

        await _fetch(profile)

        after = (await auth_client.get(
            f"/api/dating/mail-in?profile_id={profile['id']}")).json()['result']
        mine = next(one for one in after if one['id'] == rows[0]['id'])

        assert mine['seen'] is True, 'забор сбросил отметку «просмотрено»'

    async def test_replied_is_updated(self, site_api, profile):
        """А вот `IsReplied` обновляем: он меняется на сайте, когда там ответили."""
        await _fetch(profile)
        site_api.letters[0]['IsReplied'] = True

        await _fetch(profile)
        rows = await _rows(profile['id'])

        assert any(row['is_replied'] for row in rows), '«ответили» не обновилось'

    async def test_unread_is_counted_by_profile(self, auth_client, site_api, profile):
        await _fetch(profile)

        assert (await dating_mail_in_unread(SITE)).get(profile['id'])['all'] == 2

        rows = (await auth_client.get(
            f"/api/dating/mail-in?profile_id={profile['id']}")).json()['result']
        await auth_client.put(f"/api/dating/mail-in/{rows[0]['id']}/seen",
                              json={'seen': True})

        assert (await dating_mail_in_unread(SITE)).get(profile['id'])['all'] == 1

    async def test_unread_says_how_many_are_from_favorites(self, site_api, profile):
        """⚠ Счётчик обязан знать, **где** лежат эти письма.

        Письмо от избранного видно только на «Фавориты → Диалоги», от прочих —
        только на «Письма → Входящие» (§3.7.4). Значок в списке анкет вёл всегда во
        «Входящие», и для письма от избранного это был переход в пустоту: человек
        щёлкал по конверту с числом и попадал на «писем пока нет».
        """
        from mysql_.mysql_ import mysql_get_db_async
        from src.dating.dating_favorite import DATING_FAVORITE_STATES

        await _fetch(profile)
        rows = await _rows(profile['id'])
        uid = str(rows[0]['uid'])

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO dating_favorite (site_id, profile_id, uid, state)'
                ' VALUES (%s, %s, %s, %s)'
                ' ON DUPLICATE KEY UPDATE state = VALUES(state)',
                (SITE, profile['id'], uid, DATING_FAVORITE_STATES[0]))
            await db.connection.commit()

        try:
            счёт = (await dating_mail_in_unread(SITE)).get(profile['id'])

            assert счёт['all'] == 2, счёт
            assert счёт['fav'] == 1, 'письмо избранного не отличили от прочих'
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM dating_favorite WHERE profile_id = %s',
                                 (profile['id'],))
                await db.connection.commit()

    async def test_seen_can_be_taken_back(self, auth_client, site_api, profile):
        """Снять отметку тоже можно: письмо открыли мимоходом, а вернуться надо."""
        await _fetch(profile)
        rows = (await auth_client.get(
            f"/api/dating/mail-in?profile_id={profile['id']}")).json()['result']

        await auth_client.put(f"/api/dating/mail-in/{rows[0]['id']}/seen",
                              json={'seen': True})
        back = await auth_client.put(f"/api/dating/mail-in/{rows[0]['id']}/seen",
                                     json={'seen': False})

        assert back.json()['result']['seen'] is False


class TestEndpoint:
    """Ручки вкладки."""

    async def test_no_site_and_no_profile_is_empty(self, auth_client):
        """⚠ Пустой список, а не письма всех сайтов: страница без выбранного сайта
        не должна показывать чужую переписку."""
        answer = await auth_client.get('/api/dating/mail-in')

        assert answer.status_code == 200
        assert answer.json()['result'] == []

    async def test_unknown_profile_is_404(self, auth_client):
        answer = await auth_client.post('/api/dating/mail-in/fetch?profile_id=999999999')

        assert answer.status_code == 404

    async def test_refusal_is_409_not_500(self, auth_client, profile):
        """Сайт не отвечает — 409: панель отработала верно и принесла причину.

        Здесь адреса API у выдуманного сайта нет, и это законный отказ.
        """
        answer = await auth_client.post(
            f"/api/dating/mail-in/fetch?profile_id={profile['id']}")

        assert answer.status_code == 409
        assert answer.json()['detail']

    async def test_operator_may_read(self, dating_client):
        answer = await dating_client.get(f'/api/dating/mail-in?site_id={SITE}')

        assert answer.status_code == 200

    async def test_closed_without_login(self, client):
        answer = await client.get(f'/api/dating/mail-in?site_id={SITE}')

        assert answer.status_code in (401, 307)


class TestStamp:
    """Разбор времени сайта."""

    @pytest.mark.parametrize('given, want', [
        ('2024-01-01T12:00:00Z', '2024-01-01 12:00:00'),
        ('2024-01-01T12:00:00', '2024-01-01 12:00:00'),
        ('2024-01-01T12:00:00.123456', '2024-01-01 12:00:00'),
        ('2024-01-01T12:00:00+03:00', '2024-01-01 12:00:00'),
    ])
    def test_site_time_becomes_mysql_time(self, given, want):
        assert _stamp(given) == want

    @pytest.mark.parametrize('given', ['', None, 'вчера', '2024-01-01'])
    def test_junk_gives_nothing(self, given):
        """⚠ `None`, а не «сейчас»: выдуманная дата дала бы письму чужой возраст, и
        оно встало бы в списке не там, где ему место."""
        assert _stamp(given) is None


class TestListIsSiteWide:
    """⚠ Входящие показываются **по сайту**, а не по выбранной анкете.

    Письма приходят разным девушкам вперемешку, и читают их по времени: «что
    пришло сегодня», а не «что пришло Диане». Пока список висел на выборе, до
    письма надо было сперва угадать, кому оно пришло, — а из левой колонки этого
    не видно.
    """

    async def test_site_list_carries_the_girl(self, auth_client, site_api, profile):
        """⚠ В ответе есть имя анкеты — без него письмо в общем списке ничьё.

        Отправителя мало: рядом лежат письма разных девушек, и «Semen» не говорит,
        чью почту читаешь.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        наши = [row for row in rows if int(row['profile_id']) == profile['id']]

        assert наши, 'письма не попали в список сайта'
        assert 'girl' in наши[0], 'в ответе нет имени анкеты'

    async def test_girl_falls_back_to_login(self, auth_client, site_api, profile):
        """Карточки у анкеты нет — берём логин, а не пустоту.

        ⚠ Логин есть всегда; письмо без «кому» читается как ничьё, и это хуже
        некрасивого адреса вместо имени.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        наше = [row for row in rows if int(row['profile_id']) == profile['id']][0]

        assert наше['girl'] == profile['login'], \
            'без карточки имя должно падать на логин'

    async def test_newest_first(self, auth_client, site_api, profile):
        """⚠ Порядок — по времени письма, а не по времени вставки.

        Письма приезжают пачкой, и `id` отражает порядок ответа сайта. Сортируй
        мы по нему, общий список показывал бы вчерашнее письмо выше сегодняшнего.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        даты = [row['date_post'] for row in rows if row['date_post']]

        assert даты == sorted(даты, reverse=True), 'порядок не по дате письма'

    async def test_without_site_and_profile_it_is_empty(self, auth_client):
        """Ни сайта, ни анкеты — пустой список, а не письма всех сайтов."""
        answer = await auth_client.get('/api/dating/mail-in')

        assert answer.status_code == 200
        assert answer.json()['result'] == []


class TestThreadOfALetter:
    """⚠ Письмо открывается **разговором**, а не одной репликой.

    Письмо читают, чтобы решить, что ответить, — а решить это, не видя, о чём уже
    говорили, нельзя. Прежде окно показывало только само письмо, и предыдущие
    приходилось вспоминать.
    """

    async def test_thread_has_all_three_sources(self, auth_client, site_api, profile):
        """⚠ Та же нить, что у ответа: его письма, наши ответы и **ушедшие**
        письма рассылки.

        Разговор один, и второй способ его собрать разошёлся бы с первым в день,
        когда добавится третий источник реплик. Поэтому ручка зовёт тот же
        `dating_mail_reply_thread`.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        письмо = [row for row in rows if int(row['profile_id']) == profile['id']][0]

        нить = (await auth_client.get(
            f"/api/dating/mail-in/{письмо['id']}/thread")).json()['result']

        assert нить, 'переписка пустая'
        assert {'from', 'text', 'at', 'photo'} <= set(нить[0])

    async def test_thread_is_oldest_first(self, auth_client, site_api, profile):
        """Старые сверху: разговор читают сверху вниз, как любую переписку."""
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        письмо = [row for row in rows if int(row['profile_id']) == profile['id']][0]
        нить = (await auth_client.get(
            f"/api/dating/mail-in/{письмо['id']}/thread")).json()['result']

        когда = [one['at'] for one in нить if one['at']]

        assert когда == sorted(когда), 'порядок не от старых к новым'

    async def test_missing_letter_is_404(self, auth_client):
        answer = await auth_client.get('/api/dating/mail-in/99999999/thread')

        assert answer.status_code == 404

    async def test_count_matches_the_thread(self, auth_client, site_api, profile):
        """⚠ Число на карточке и длина нити — одно и то же.

        Разойдись они, человек открыл бы карточку «5» и увидел три реплики. Оба
        считают три источника: входящие, ответы и ушедшую рассылку.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        письмо = [row for row in rows if int(row['profile_id']) == profile['id']][0]
        нить = (await auth_client.get(
            f"/api/dating/mail-in/{письмо['id']}/thread")).json()['result']

        assert письмо['thread_count'] == len(нить), \
            'счётчик на карточке разошёлся с окном'

    async def test_count_is_in_the_list(self, auth_client, site_api, profile):
        """⚠ Счёт едет **со списком**, а не отдельной ручкой на письмо.

        Число нужно на каждой карточке, и сотня карточек означала бы сотню
        запросов. Сама переписка при этом по-прежнему едет только по открытию.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']

        assert all('thread_count' in row for row in rows)


class TestListIsByConversation:
    """⚠ Строка на **переписку**, а не на письмо.

    Он мог написать три подряд, не дождавшись ответа; три карточки на них — не
    список писем, а один и тот же разговор, размноженный по экрану. Показываем
    последнее письмо пары «анкета — собеседник», прежние видны в окне.

    Правило «последнее в переписке» одно на проект — тем же `NOT EXISTS`, что у
    отбора писем для ответа (`dating_mail_reply._to_answer`).
    """

    async def test_one_row_per_companion(self, auth_client, site_api, profile):
        """Два письма от одного человека — одна строка."""
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        наши = [row for row in rows if int(row['profile_id']) == profile['id']]
        пары = [(row['profile_id'], row['uid']) for row in наши]

        assert len(пары) == len(set(пары)), 'одна переписка показана дважды'

    async def test_the_newest_letter_wins(self, auth_client, site_api, profile):
        """⚠ В строке — **последнее** письмо переписки, а не первое.

        Список читают, чтобы понять, на что отвечать; первое письмо разговора
        отвечает на этот вопрос хуже всего.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        наши = [row for row in rows if int(row['profile_id']) == profile['id']]

        for row in наши:
            нить = (await auth_client.get(
                f"/api/dating/mail-in/{row['id']}/thread")).json()['result']
            его = [one for one in нить if one['from'] == 'him']

            assert его and его[-1]['text'] == row['body'], \
                'в строке не последнее письмо переписки'

    async def test_unseen_counts_the_whole_thread(self, auth_client, site_api,
                                                  profile):
        """⚠ Непрочитанные считаются по разговору, а не по одному письму.

        Последнее могло быть уже прочитано, а до него лежать новые: метка «новое»
        обязана гореть, пока в переписке есть непрочитанное.
        """
        await _fetch(profile)

        rows = (await auth_client.get(
            f'/api/dating/mail-in?site_id={SITE}')).json()['result']
        наши = [row for row in rows if int(row['profile_id']) == profile['id']]

        assert наши and all('unseen_count' in row for row in наши)
        assert any(row['unseen_count'] > 0 for row in наши), \
            'свежезабранные письма не считаются непрочитанными'
