"""Пул ответов: агент пишет ответы на входящие, человек их читает.

⚠ **Наружу сюита не ходит** (`testing.md`): написание ответа — вызов LLM у соседа.
Подменяется `mail_reply` — единственная дорогая часть; всё остальное работает
по-настоящему, с живой базой.

## Что здесь ловится

**Отвечаем на последнее письмо, а не на каждое.** Он мог написать три подряд, не
дождавшись ответа; три ответа в очередь на них — не переписка, а автоответчик.

**Один ответ на письмо.** Уникальный ключ по `mail_in_id`: повторный заход не должен
класть второй ответ, а два ответа подряд одному человеку выглядят поломкой.

**История из двух источников.** Его письма и наши ответы: возьми мы только входящие,
агент не знал бы, что уже сказал, и здоровался бы второй раз.

**Порядок истории.** Агенту она уходит старыми сверху, а обрезается с конца: возьми
мы «первые N», это оказались бы самые старые письма.
"""

import uuid

import pytest

from src.dating import mail_reply as mod
from src.dating.mail_reply import _to_answer, mail_reply_make

pytestmark = pytest.mark.mail

# Сайт для писем — выдуманный: живую переписку задеть невозможно.
SITE = 999_777

# Анкета заводится на живом номере: заведение сверяет сайт у соседа, а
# `mail_in.profile_id` — внешний ключ.
LIVE_SITE = 343


@pytest.fixture
async def profile(auth_client):
    """Анкета и включённая генерация ответов. Каскад убирает за ней всё.

    ⚠ **Генерация включается у сайта, а не у анкеты.** Флаг переехал в
    `site_.reply_make_on` (миграция `20260910120000_...`): пул ответов один
    на сайт, и «отвечаем ли вообще» отвечается один раз. Прежде фикстура ставила
    `reply_on` анкете — после переезда это ничего не включало, и все проверки
    набора молча возвращали ноль.
    """
    from mysql_.mysql_ import mysql_get_db_async

    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': LIVE_SITE, 'login': f'rep-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': True})

    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, login, profile FROM profile'
                         ' WHERE id = %s', (made,))
        row = dict(await db.fetchone())

    yield row

    await auth_client.delete(f'/api/dating/profile/{made}')
    # ⚠ Флаг снимаем за собой: он у **сайта**, а не у анкеты, и каскад его не
    # уберёт. Оставленным он включал бы генерацию соседним проверкам.
    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': False})


@pytest.fixture
def agent(monkeypatch):
    """Подменяет агента: запоминает, что ему отдали, вместо похода к LLM.

    ⚠ Подменяется `answer_mail_reply` — ответ идёт общим `dating_talk`, а не своим
    workflow. Историю собирает **сосед** из `companion_session`, поэтому её здесь
    нет: что панель отсюда её не шлёт, стережёт `test_chat_answer_anyway.py`.
    """
    calls = []

    async def fake(agent_id, profile, uid, language, name=''):
        calls.append({'uid': str(uid), 'language': str(language),
                      'name': str(name),
                      'profile_id': int(profile.get('id') or 0)})

        return f'Ответ №{len(calls)}', ''

    monkeypatch.setattr(mod, 'answer_mail_reply', fake)
    monkeypatch.setattr(mod, '_agent', _fake_agent)

    return calls


async def _fake_agent(site_id: int) -> int:
    """Номер агента без похода к соседу: заведение проверяется не здесь."""
    return 1


async def _in(profile: dict, uid: str, body: str, minutes: int = 0) -> int:
    """Кладёт входящее письмо. `minutes` — насколько оно старше «сейчас»."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in'
            ' (site_id, profile_id, message_id, uid, name, body, date_post)'
            ' VALUES (%s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
            (SITE, int(profile['id']), int(uuid.uuid4().int % 10**9), uid,
             f'Man {uid}', body, int(minutes)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _out(profile: dict, uid: str, body: str, minutes: int = 0,
               status: str = 'sent') -> int:
    """Кладёт **письмо рассылки** — то, что девушка отправила ему сама.

    ⚠ Ради него всё и затевалось: чаще всего разговор начат именно рассылкой, и он
    отвечал на неё, а не написал сам.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mailing'
            ' (site_id, profile_id, mail_id, uid, name, body, status, sent_at)'
            ' VALUES (%s, %s, NULL, %s, %s, %s, %s,'
            '         NOW() - INTERVAL %s MINUTE)',
            (SITE, int(profile['id']), uid, f'Man {uid}', body, status,
             int(minutes)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _replies(site_id: int = SITE) -> list[dict]:
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT mail_in_id, uid, body, status FROM mail_reply'
                         ' WHERE site_id = %s ORDER BY id', (int(site_id),))

        return [dict(row) for row in await db.fetchall() or []]


class TestWhoIsAnswered:
    """Кого берём в работу."""

    async def test_reply_is_written(self, profile, agent):
        await _in(profile, '111', 'Moto is my life')

        got = await mail_reply_make(SITE)
        rows = await _replies()

        assert got['made'] == 1
        assert len(rows) == 1
        assert rows[0]['uid'] == '111'
        assert rows[0]['status'] == 'draft', 'ответ не должен быть отправленным'

    async def test_only_the_last_letter(self, profile, agent):
        """⚠ **Главное здесь.** Он написал три подряд — отвечаем на последнее.

        Три ответа в очередь на три письма это не переписка, а автоответчик; прежние
        письма при этом не теряются — они уйдут в историю.
        """
        await _in(profile, '111', 'первое', minutes=30)
        await _in(profile, '111', 'второе', minutes=20)
        last_id = await _in(profile, '111', 'третье', minutes=10)

        got = await mail_reply_make(SITE)
        rows = await _replies()

        assert got['made'] == 1, 'ответили на каждое письмо'
        assert rows[0]['mail_in_id'] == last_id

    async def test_each_companion_gets_one(self, profile, agent):
        """А вот у разных собеседников — свой ответ каждому: это разные разговоры."""
        await _in(profile, '111', 'привет от первого')
        await _in(profile, '222', 'привет от второго')

        got = await mail_reply_make(SITE)

        assert got['made'] == 2
        assert {row['uid'] for row in await _replies()} == {'111', '222'}

    async def test_answered_letter_is_skipped(self, profile, agent):
        """⚠ Один ответ на письмо: повторный заход не кладёт второй."""
        await _in(profile, '111', 'Moto is my life')

        await mail_reply_make(SITE)
        again = await mail_reply_make(SITE)

        assert again['made'] == 0, 'написали второй ответ на то же письмо'
        assert len(await _replies()) == 1

    async def test_generation_off_is_skipped(self, auth_client, profile, agent):
        """⚠ Выключенная генерация — писем не берём вовсе.

        Решение человека, и оно про **весь сайт**: пул ответов один, и вопрос
        «отвечаем ли» задаётся один раз. Прежде флаг стоял у анкеты — включать его
        приходилось сорок раз, а забытая молчала.
        """
        await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                              json={'reply_make_on': False})
        await _in(profile, '111', 'Moto is my life')

        assert (await mail_reply_make(SITE))['made'] == 0
        assert not agent, 'агента позвали при выключенной генерации'

    async def test_letter_without_body_is_skipped(self, profile, agent):
        """Текста нет — отвечать не на что: тело ещё не забрали с сайта."""
        await _in(profile, '111', '')

        assert (await mail_reply_make(SITE))['made'] == 0
        assert not agent

    async def test_disabled_profile_is_skipped(self, auth_client, profile, agent):
        """Выключенную анкету не ведут вовсе — и ответы от неё не готовят."""
        from mysql_.mysql_ import mysql_get_db_async

        await _in(profile, '111', 'Moto is my life')
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET enabled = 0 WHERE id = %s',
                             (profile['id'],))
            await db.connection.commit()
        try:
            assert (await mail_reply_make(SITE))['made'] == 0
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('UPDATE profile SET enabled = 1 WHERE id = %s',
                                 (profile['id'],))
                await db.connection.commit()


class TestThread:
    """Переписка для показа человеку."""

    async def test_thread_has_all_three_sources(self, profile, agent):
        """Его письма, наши ответы и письма рассылки — в одном разговоре."""
        from src.dating.mail_reply import mail_reply_thread

        await _out(profile, '111', 'Чем увлекаешься?', minutes=40)
        await _in(profile, '111', 'Moto is my life', minutes=30)
        await mail_reply_make(SITE)

        thread = await mail_reply_thread(int(profile['id']), '111')

        assert [one['kind'] for one in thread] == ['mailing', 'in', 'reply']
        assert [one['from'] for one in thread] == ['her', 'him', 'her']

    async def test_thread_marks_the_kind(self, profile, agent):
        """⚠ Письмо рассылки помечено: девушка его не писала — оно ушло шаблоном, и
        путать его с ответом человек не должен."""
        from src.dating.mail_reply import mail_reply_thread

        await _out(profile, '111', 'Чем увлекаешься?', minutes=40)
        thread = await mail_reply_thread(int(profile['id']), '111')

        assert thread[0]['kind'] == 'mailing'
        assert thread[0]['id'], 'номер реплики не приехал — по нему отсеивают текущий'

    async def test_thread_is_oldest_first(self, profile, agent):
        from src.dating.mail_reply import mail_reply_thread

        await _in(profile, '111', 'первое', minutes=30)
        await _in(profile, '111', 'второе', minutes=10)

        thread = await mail_reply_thread(int(profile['id']), '111')

        assert [one['text'] for one in thread] == ['первое', 'второе']


class TestRefusal:
    """Отказ агента."""

    async def test_agent_refusal_stops_the_pass(self, profile, monkeypatch):
        """⚠ Обрывает **весь заход**: сосед занят, и следующие письма упрутся в то
        же самое. Круг вернётся через свой срок.

        ⚠ Отказ теперь приходит **второй половиной ответа**, а не исключением:
        `answer_mail_reply` отдаёт `(текст, ошибка)`, как и остальной чат.
        """
        calls = []

        async def broken(agent_id, profile, uid, language, name=''):
            calls.append(1)

            return '', 'браузер соседа занят'

        monkeypatch.setattr(mod, 'answer_mail_reply', broken)
        monkeypatch.setattr(mod, '_agent', _fake_agent)

        await _in(profile, '111', 'первое')
        await _in(profile, '222', 'второе')

        got = await mail_reply_make(SITE)

        assert got['made'] == 0
        assert len(calls) == 1, 'после отказа продолжили дёргать агента'
        assert got['reasons'], 'причина не названа'

    async def test_a_missing_agent_stops_the_pass(self, profile, monkeypatch):
        """⚠ Агент не завёлся — то же самое: следующие письма упрутся в то же."""
        from src.dating.mail_agent import DatingMailAgentError

        async def no_agent(site_id):
            raise DatingMailAgentError('у сайта нет метки для агента')

        monkeypatch.setattr(mod, '_agent', no_agent)
        await _in(profile, '111', 'первое')

        got = await mail_reply_make(SITE)

        assert got['made'] == 0
        assert got['reasons'], 'причина не названа'

    async def test_empty_answer_is_not_saved(self, profile, monkeypatch):
        async def empty(agent_id, profile, uid, language, name=''):
            return '   ', ''

        monkeypatch.setattr(mod, 'answer_mail_reply', empty)
        monkeypatch.setattr(mod, '_agent', _fake_agent)
        await _in(profile, '111', 'Moto is my life')

        got = await mail_reply_make(SITE)

        assert got['made'] == 0
        assert await _replies() == []


class TestEndpoint:
    """Ручки пула ответов."""

    async def test_pool_is_site_wide(self, auth_client, profile, agent):
        """⚠ **Пул общий на сайт**, и ручка спрашивается по сайту, а не по анкете:
        читает ответы один человек и подряд."""
        await _in(profile, '111', 'Moto is my life')
        await mail_reply_make(SITE)

        rows = (await auth_client.get(
            f'/api/dating/mail-in/reply?site_id={SITE}')).json()['result']

        assert len(rows) == 1
        assert rows[0]['girl'], 'имя анкеты не приехало'
        assert rows[0]['in_body'] == 'Moto is my life', \
            'письмо, на которое отвечаем, не приехало — сверить ответ не с чем'

    async def test_no_site_is_empty(self, auth_client):
        answer = await auth_client.get('/api/dating/mail-in/reply')

        assert answer.status_code == 200
        assert answer.json()['result'] == []

    async def test_make_without_site_is_refused(self, auth_client):
        answer = await auth_client.post('/api/dating/mail-in/reply/make')

        assert answer.status_code == 422

    async def test_operator_may_read(self, dating_client):
        answer = await dating_client.get(f'/api/dating/mail-in/reply?site_id={SITE}')

        assert answer.status_code == 200


class TestNoSending:
    """⚠ Наружу ответы сами не уходят.

    Отправка — отдельное решение (выключатель «отвечать» у сайта), и появиться она
    должна осознанно, а не потому, что кто-то позвал её из набора.
    """

    def test_making_does_not_send(self):
        import inspect

        assert 'mail_send' not in inspect.getsource(mod), \
            'набор пула ответов научился отправлять письма'


class TestGenerationSwitchIsSiteWide:
    """⚠ Выключатель генерации — у сайта, а не у анкеты, и это переезд.

    Прежде решение принималось по анкете (`profile_.reply_on`). Отбор был
    честным — «у этой отвечаем, у той пишем руками», — но включать приходилось
    сорок раз, а забытая анкета молчала, и причину искали в поломке.

    Добил его вид страницы: выключатель стоит в полосе подвкладок рядом с
    «отправкой ответов», а та про сайт. Два соседних флажка, живущих по разным
    правилам, объяснить нельзя — один заперт без выбранной анкеты, второй
    нажимается всегда.

    Миграция — `20260910120000_dating_site_reply_make_on.sql`.
    """

    async def test_handle_reads_both_flags(self, auth_client):
        """⚠ Оба флага одним ответом: они пара «готовим / шлём» и стоят рядом.

        Приди они порознь, панель успевала бы показать один включённым, а второй
        ещё нет.
        """
        got = (await auth_client.get(
            f'/api/dating/site/{SITE}/reply')).json()['result']

        assert 'reply_make_on' in got and 'reply_send_on' in got

    async def test_switch_survives_a_reread(self, auth_client):
        """Включили — читается включённым: флаг лежит в базе, а не в памяти."""
        try:
            await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                                  json={'reply_make_on': True})

            got = (await auth_client.get(
                f'/api/dating/site/{SITE}/reply')).json()['result']

            assert got['reply_make_on'] is True
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                                  json={'reply_make_on': False})

    async def test_generation_does_not_touch_sending(self, auth_client):
        """⚠ Соседний флаг не задевается. Пара «готовим / шлём» — два решения:
        пул можно копить и править молча, а уходят ответы живым людям."""
        try:
            await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                                  json={'reply_make_on': True})

            got = (await auth_client.get(
                f'/api/dating/site/{SITE}/reply')).json()['result']

            assert got['reply_send_on'] is False, 'включили отправку заодно'
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                                  json={'reply_make_on': False})

    async def test_operator_may_switch(self, dating_client):
        """⚠ **Выключатель занятия — право оператора**, а не только админа.

        Граница проходит по «устройство против работы»: адреса, пароли и частоты
        остались за админом (страница «Настройки сайтов»), а «готовим ли ответы» —
        это распорядок работы того, кто ведёт анкеты.
        """
        try:
            answer = await dating_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                                             json={'reply_make_on': True})

            assert answer.status_code == 200
        finally:
            await dating_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                                    json={'reply_make_on': False})

    async def test_closed_without_login(self, client):
        answer = await client.put(f'/api/dating/site/{SITE}/reply-make-on',
                                  json={'reply_make_on': True})

        assert answer.status_code in (401, 307)


class TestPendingReplyStandsLast:
    """⚠ Неотправленный ответ — в конце разговора, а не по времени написания.

    Разговор это то, что стороны сказали друг другу; черновик из пула никто не
    видел, и место ему внизу, где он и ждёт отправки.

    Поймано глазами на живой переписке: ответ, написанный в 15:28, встал между
    письмами 12:29 и 20:32 — будто девушка его отправила, а собеседник продолжил
    разговор, не заметив.
    """

    async def test_draft_is_at_the_end(self, auth_client, profile, agent):
        """Черновик стоит последним, даже когда написан раньше свежих писем."""
        from src.dating.mail_reply import (mail_reply_make,
                                                  mail_reply_thread)

        await _in(profile, '111', 'Moto is my life')
        await mail_reply_make(SITE)
        # Письмо **позже** черновика: по времени тот встал бы посередине.
        await _in(profile, '111', 'and horses too', minutes=30)

        thread = await mail_reply_thread(profile['id'], '111')

        assert thread[-1]['kind'] == 'reply' and thread[-1]['status'] == 'draft', \
            'черновик не в конце разговора'

    async def test_others_keep_their_order(self, auth_client, profile, agent):
        """⚠ Прочие реплики идут по времени, старые сверху.

        Правило про черновик не должно перетасовать разговор: его читают сверху
        вниз, как любую переписку.
        """
        from src.dating.mail_reply import (mail_reply_make,
                                                  mail_reply_thread)

        await _in(profile, '111', 'первое')
        await mail_reply_make(SITE)
        await _in(profile, '111', 'второе', minutes=30)

        thread = await mail_reply_thread(profile['id'], '111')
        his = [one['at'] for one in thread if one['from'] == 'him']

        assert his == sorted(his), 'его письма перетасованы'

    async def test_sent_reply_goes_by_delivery_time(self, auth_client, profile,
                                                    agent):
        """⚠ У ушедшего ответа время **отправки**, а не сочинения.

        Между ними бывает час: по времени написания ответ вставал бы в разговор
        раньше, чем собеседник его получил.
        """
        from mysql_.mysql_ import mysql_get_db_async
        from src.dating.mail_reply import (mail_reply_make,
                                                  mail_reply_thread)

        await _in(profile, '111', 'Moto is my life')
        await mail_reply_make(SITE)

        # Отправлен на два часа позже, чем написан.
        async with mysql_get_db_async() as db:
            await db.execute(
                "UPDATE mail_reply SET status = 'sent',"
                ' sent_at = created_at + INTERVAL 2 HOUR'
                ' WHERE profile_id = %s AND uid = %s', (profile['id'], '111'))
            await db.connection.commit()

        reply = [one for one in await mail_reply_thread(profile['id'], '111')
                 if one['kind'] == 'reply'][0]

        assert reply['status'] == 'sent'
        # Час сочинения в разговоре не участвует — в нём стоит час доставки.
        letter = [one for one in await mail_reply_thread(profile['id'], '111')
                  if one['kind'] == 'in'][0]

        assert reply['at'] > letter['at'], 'ответ встал раньше письма, на которое он'
