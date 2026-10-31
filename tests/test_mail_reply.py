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

    async def fake(agent_id, profile, uid, language, name='', letter=''):
        calls.append({'uid': str(uid), 'language': str(language),
                      'name': str(name),
                      'profile_id': int(profile.get('id') or 0)})

        return f'Ответ №{len(calls)}', '', 0, {}

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
        await db.execute('SELECT mail_in_id, uid, body, status, message_id'
                         ' FROM mail_reply WHERE site_id = %s ORDER BY id',
                         (int(site_id),))

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
        `answer_mail_reply` отдаёт `(текст, ошибка, номер)`, как и остальной чат.
        """
        calls = []

        async def broken(agent_id, profile, uid, language, name='', letter=''):
            calls.append(1)

            return '', 'браузер соседа занят', 0, {}

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
        async def empty(agent_id, profile, uid, language, name='', letter=''):
            return '   ', '', 0, {}

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


class TestMessageIdReachesThePool:
    """⚠⚠ Номер обмена обязан доехать до строки пула, а не только пройти форму.

    Задача 7 научила `_put` принимать довод `message_id`, но ревью поймало дыру
    мутационным тестом: убери колонку из `INSERT`, оставив сигнатуры прежними —
    и вся сюита `mail` остаётся зелёной, потому что существующие тесты проверяют
    лишь то, что вызывающий код не падает на распаковке тройки. Здесь стережём
    само содержимое строки: номер, который отдал агент, должен лежать в колонке
    `mail_reply.message_id` — тем же номером, без которого отправка не сможет
    отметить у соседа, что реплика дошла.
    """

    async def test_message_id_lands_in_the_pool_row(self, profile, monkeypatch):
        """Агент отдал ненулевой номер обмена — он обязан лежать в столбце, а не
        потеряться между сочинением ответа и записью в пул."""
        async def fake(agent_id, profile, uid, language, name='', letter=''):
            return 'Ответ про мотоциклы', '', 4242, {}

        monkeypatch.setattr(mod, 'answer_mail_reply', fake)
        monkeypatch.setattr(mod, '_agent', _fake_agent)

        await _in(profile, '111', 'Moto is my life')
        got = await mail_reply_make(SITE)
        rows = await _replies()

        assert got['made'] == 1
        assert rows[0]['message_id'] == 4242, \
            'номер обмена не доехал до строки пула'

    async def test_silent_agent_leaves_null_not_zero(self, profile, agent):
        """Агент промолчал про номер (фикстура `agent` отдаёт его нулём) — в
        строке обязан лежать `NULL`, а не `0`.

        «Не знаем» и «ноль» — разные вещи: отметка доставки у соседа идёт по
        номеру, и ложный ноль в колонке выглядел бы настоящим обменом №0, а не
        его отсутствием — отметка уйдёт в никуда молча, без ошибки в логах.
        """
        await _in(profile, '111', 'Moto is my life')
        got = await mail_reply_make(SITE)
        rows = await _replies()

        assert got['made'] == 1
        assert rows[0]['message_id'] is None, \
            'отсутствие номера легло нулём, а не NULL'


class TestTheLetterTravelsAsQuestion:
    """⚠⚠ Различие двух почтовых дорог — **сам вопрос**, с 24.10.2026.

    `mail_kind` стал у соседа внутренней переменной (`type='workflow'`), и
    прислать его отсюда теперь значит поймать `400` на весь заход: в сборку
    `_variables_allowed` пропускает только `input` (та же грабля, что стоила
    91 отказа при снятии долей жребия). Ветку сосед вычисляет сам по непустому
    `prompt_user` — значит ответ на письмо обязан везти его текст вопросом, а
    письмо молчащему — пустой вопрос.

    ⚠ Без сторожа ответ уедет **веткой молчащего молча**: сосед посчитает
    `write`, модель вместо ответа напишет «я вспомнила о тебе» — без единой
    ошибки в логах, ровно та тишина, которую письмо и лечит.
    """

    def test_the_door_sends_no_undeclared_name(self):
        """Из двери соседу `mail_kind` вынесен — и вопрос везёт письмо."""
        import inspect

        from src.dating import answer

        asked = inspect.getsource(answer._agent_ask)
        assert "'mail_kind'" not in asked, \
            'необъявленное имя уезжает к соседу — это 400 на весь заход'

        replied = inspect.getsource(answer.answer_mail_reply)
        assert "int(agent_id), str(letter or '')" in replied, \
            'ответ на письмо везёт пустой вопрос: сосед сочтёт его письмом молчащему'

        # ⚠ Проверяется **смысл**, а не написание: с 31.10.2026 тело едет не
        # голым, а через `mail_in_body_for_agent` — к нему снизу дописывается
        # описание присланного снимка. Письмо при этом всё так же обязано
        # оказаться в вызове: без него заход уйдёт веткой письма молчащему.
        source = inspect.getsource(mod)

        assert 'letter=mail_in_body_for_agent(' in source, \
            'круг перестал класть тело его письма в вызов'
        assert "letter.get('body')" in source, \
            'в вызов уезжает не тело письма'


class TestTheThreadIsWhole:
    """⚠⚠ Разговор обязан показывать **всё**, что пара сказала друг другу.

    Источников у него пять, и каждый добавлялся после того, как его отсутствие
    замечали глазами на живой переписке. Последним — письма-исполнения
    (`intention_mail`): агент пообещал в чате, письмо ушло со снимком, а лента
    о нём молчала. У Дианы с Семёном «Намерения → Сделано» показывали три
    письма, а разговор той же пары — три реплики чата и ни одного письма.

    Цена пропуска тут выше обычной: это самая заметная реплика пары, и человек,
    глядя на ленту, решает, что девушка обещание не исполнила.
    """

    async def test_the_promised_letter_is_in_the_thread(self, profile):
        from src.dating.mail_reply import mail_reply_thread

        await _intention(profile, '222', 'Here is that photo you asked for')

        thread = await mail_reply_thread(int(profile['id']), '222')

        assert [one['kind'] for one in thread] == ['intention'], (
            'письмо по обещанию агента выпало из разговора')
        assert thread[0]['from'] == 'her'

    async def test_a_draft_one_is_not_shown(self, profile):
        """⚠ Только ушедшее: черновик собеседник не видел, и в разговоре его
        не было — как и у рассылки с приставанием."""
        from src.dating.mail_reply import mail_reply_thread

        await _intention(profile, '223', 'ещё не ушло', status='draft')

        assert await mail_reply_thread(int(profile['id']), '223') == []

    async def test_it_carries_the_photo_number(self, profile):
        """⚠⚠ Номер файла — **на Диске**, а не ключ строки индекса: превью
        отдаётся по первому, и спутать их легко (оба числа), а видно это
        только пустым квадратом в ленте."""
        from src.dating.mail_reply import mail_reply_thread

        file_row, file_id = await _gallery_file(profile)
        await _intention(profile, '224', 'вот снимок', file_row=file_row)

        thread = await mail_reply_thread(int(profile['id']), '224')

        assert thread[0]['file_id'] == file_id, (
            f"в ленту уехал не номер файла Диска: {thread[0]['file_id']!r}")
        assert thread[0]['profile_id'] == int(profile['id']), (
            'без анкеты страница не соберёт адрес превью галереи')

    async def test_an_incoming_photo_rides_with_its_words(self, profile):
        """⚠ У присланного снимка едут **оба** поля: адрес и описание. Описание
        и есть ответ на «что на картинке» — по превью сюжет не разобрать."""
        from src.dating.mail_reply import mail_reply_thread

        await _in_with_shot(profile, '225', 'look at this',
                            'https://site.test/content/attachments/9/a.jpg',
                            'A man on a boat at sunset.')

        thread = await mail_reply_thread(int(profile['id']), '225')

        assert thread[0]['attach'].endswith('/a.jpg')
        assert thread[0]['attach_about'] == 'A man on a boat at sunset.'

    async def test_letters_without_photos_say_so_plainly(self, profile):
        """⚠ Пустые поля у прочих родов писем — не брак, а ответ «вложений не
        бывает». Страница смотрит на них, и `None` там стал бы вторым видом
        пустоты."""
        from src.dating.mail_reply import mail_reply_thread

        await _out(profile, '226', 'письмо рассылки')

        thread = await mail_reply_thread(int(profile['id']), '226')

        assert thread[0]['attach'] == ''
        assert thread[0]['attach_about'] == ''
        assert thread[0]['file_id'] == ''


async def _intention(profile: dict, uid: str, body: str,
                     status: str = 'sent', file_row: int = 0) -> int:
    """Кладёт письмо-исполнение обещания — то, что ушло по метке агента."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO intention_mail'
            ' (site_id, profile_id, intention_id, uid, name, body, status,'
            '  gallery_file_id, sent_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s,'
            "         IF(%s = 'sent', NOW(), NULL))",
            (SITE, int(profile['id']), int(uuid.uuid4().int % 10**9), uid,
             f'Man {uid}', body, status, int(file_row), status))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _gallery_file(profile: dict) -> tuple[int, str]:
    """Снимок в индексе галереи. Возвращает (ключ строки, номер файла Диска)."""
    from mysql_.mysql_ import mysql_get_db_async

    file_id = f'drive-{uuid.uuid4().hex[:10]}'

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO gallery_file (profile_id, file_id, path, name, mime,'
            "  kind, is_gone) VALUES (%s, %s, 'public/a.jpg', 'a',"
            "  'image/jpeg', 'image', 0)",
            (int(profile['id']), file_id))
        await db.connection.commit()

        return int(db.lastrowid), file_id


async def _in_with_shot(profile: dict, uid: str, body: str, url: str,
                        about: str) -> int:
    """Входящее письмо со снимком, который он прислал, и его описанием.

    ⚠ Пустое описание значит «ещё не описан», и отметка времени тогда **не
    ставится**: по ней и решают, ждать ли письму. Ставь её помощник всегда —
    сторож ожидания зеленел бы вхолостую, проверяя уже описанное.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in'
            ' (site_id, profile_id, message_id, uid, name, body, date_post,'
            '  attachment, attachment_about, attachment_at)'
            " VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s,"
            '         IF(%s = 1, NOW(), NULL))',
            (SITE, int(profile['id']), int(uuid.uuid4().int % 10**9), uid,
             f'Man {uid}', body, url, about, 1 if str(about or '').strip() else 0))
        await db.connection.commit()

        return int(db.lastrowid)


class TestTheAgentSeesThePhoto:
    """⚠⚠ Снимок, присланный письмом, обязан доехать до агента.

    Он присылает фотографию, а в промпт уезжал один текст — и девушка отвечала
    так, будто ничего не приходило. На проде это видно дословно: Mike → Diana,
    13.10.2026, «I send you photo» и следом «oh no...I didn't get any photo».

    ⚠ Описание едет **в самом вопросе**, под текстом письма, а не отдельной
    переменной: снимок — часть того же письма, и своя переменная стоила бы
    объявления у соседа, правки промпта ветки и жёсткого порядка выкладки
    (присылка необъявленного имени — `400` на весь прогон).
    """

    async def test_the_description_rides_under_the_letter(self, profile, agent,
                                                          monkeypatch):
        asked = {}

        async def spy(agent_id, prof, uid, language, name='', letter=''):
            asked['letter'] = letter

            return 'ответ', '', 0, {}

        monkeypatch.setattr('src.dating.mail_reply.answer_mail_reply', spy)

        await _in_with_shot(profile, '301', 'Look what I caught today!',
                            'https://site.test/content/attachments/9/a.jpg',
                            'A man holding a large fish on a river bank.')
        await mail_reply_make(SITE)

        got = asked.get('letter') or ''

        assert 'Look what I caught today!' in got, 'текста письма агенту не дали'
        assert 'large fish' in got, 'агент не узнал про снимок — ответит мимо'
        assert got.index('Look what I caught') < got.index('large fish'), (
            'описание встало выше письма — читается как слова собеседника')

    async def test_a_letter_without_a_photo_is_untouched(self, profile, agent,
                                                         monkeypatch):
        """⚠ Пустое описание ничего не добавляет: заголовок без описания модель
        прочла бы как «снимок был, но про него ничего не известно»."""
        asked = {}

        async def spy(agent_id, prof, uid, language, name='', letter=''):
            asked['letter'] = letter

            return 'ответ', '', 0, {}

        monkeypatch.setattr('src.dating.mail_reply.answer_mail_reply', spy)

        await _in(profile, '302', 'Просто письмо без вложения')
        await mail_reply_make(SITE)

        assert asked.get('letter') == 'Просто письмо без вложения'

    async def test_the_column_is_selected(self, profile, agent, monkeypatch):
        """⚠⚠ Главный сторож: столбец обязан стоять **в запросе отбора**.

        Не выбранный, он до агента не доедет вовсе — и ответ выйдет таким,
        будто снимка не присылали, молча и без единой ошибки. Та же грабля, что
        уже стоила отметок `delivered_at` и столбцов исполнения в круге
        отправки, поэтому проверяется не склейка, а **дорога целиком**.
        """
        from src.dating.mail_reply import _to_answer

        await _in_with_shot(profile, '303', 'hi', 'https://site.test/a.jpg',
                            'Some description here.')

        rows = await _to_answer(SITE)
        ours = [row for row in rows if str(row['uid']) == '303']

        assert ours, 'письмо не попало в отбор'
        assert 'attachment_about' in ours[0], (
            'запрос не выбирает описание снимка — агент его не увидит')
        assert ours[0]['attachment_about'] == 'Some description here.'

    def test_the_mark_says_it_is_not_his_words(self):
        """⚠ Подпись помечает вложение, а не маскируется под письмо: иначе
        модель приняла бы описание за слова собеседника."""
        from src.dating.mail_in import (MAIL_IN_SHOT_TITLE,
                                        mail_in_body_for_agent)

        got = mail_in_body_for_agent('текст', 'описание')

        assert MAIL_IN_SHOT_TITLE in got
        assert MAIL_IN_SHOT_TITLE.startswith('==='), (
            'подпись не отделена рамкой — уедет как слова собеседника')
        assert got.endswith('описание'), (
            'описание стоит не под письмом')


class TestTheAnswerWaitsForThePhoto:
    """⚠⚠⚠ Письмо со снимком не отвечается, пока снимок не описан.

    ## Что случилось на проде 31.10.2026

    Письмо легло в 15:40:53, круг ответов собрал вопрос в **15:41:07**,
    описание легло в **15:41:20** — на тринадцать секунд позже. Агент получил
    голый текст «look at me, am i fine for you?» и ответил... описав снимок из
    **прошлого** письма: похвалил синюю футболку, когда на фотографии была
    клетчатая рубашка.

    Это хуже, чем промолчать о фото: собеседник видит отклик на то, чего он не
    присылал.

    ⚠ Я утверждал, что гонки нет — «круг ответов ходит по своему сроку, к тому
    времени описание успеет лечь». Рассуждение было про промежуток **между**
    заходами, а решает промежуток **внутри** одного: забор кладёт письмо,
    описание идёт следом, и круг ответов успевает вклиниться между ними.
    """

    async def test_a_letter_with_an_undescribed_photo_is_not_taken(self, profile):
        from src.dating.mail_reply import _to_answer

        await _in_with_shot(profile, '401', 'look at me',
                            'https://site.test/content/attachments/9/a.jpg',
                            '')

        rows = await _to_answer(SITE)

        assert not [r for r in rows if str(r['uid']) == '401'], (
            'письмо со снимком ушло к агенту без описания — он ответит на '
            'голый текст либо достанет приметы из прошлого письма')

    async def test_once_described_it_is_taken(self, profile):
        """⚠ Ждём именно описания, а не вообще снимка: описанное идёт в работу
        сразу."""
        from src.dating.mail_reply import _to_answer

        await _in_with_shot(profile, '402', 'look at me',
                            'https://site.test/content/attachments/9/a.jpg',
                            'A man on a boat.')

        rows = await _to_answer(SITE)

        assert [r for r in rows if str(r['uid']) == '402'], (
            'описанное письмо не пошло в работу')

    async def test_a_letter_without_a_photo_is_not_delayed(self, profile):
        """⚠ Ожидание касается **только** писем со снимком: прочие отвечаются
        как прежде."""
        from src.dating.mail_reply import _to_answer

        await _in(profile, '403', 'просто письмо')

        rows = await _to_answer(SITE)

        assert [r for r in rows if str(r['uid']) == '403'], (
            'письмо без вложения задержано ни за что')

    async def test_waiting_is_not_forever(self, profile):
        """⚠⚠ Снимок может не описаться никогда — файл пропал, зрение
        отказало. Без предела такое письмо осталось бы без ответа навсегда, и
        молча: ни ошибки, ни строки в журнале, просто собеседник не дождался.
        """
        from mysql_.mysql_ import mysql_get_db_async

        from src.dating.mail_in_vision import MAIL_IN_VISION_TRIES
        from src.dating.mail_reply import _to_answer

        mail_id = await _in_with_shot(
            profile, '404', 'look at me',
            'https://site.test/content/attachments/9/a.jpg', '')

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE mail_in SET attachment_try = %s'
                             ' WHERE id = %s',
                             (MAIL_IN_VISION_TRIES, mail_id))
            await db.connection.commit()

        rows = await _to_answer(SITE)

        assert [r for r in rows if str(r['uid']) == '404'], (
            'попытки исчерпаны, а письмо всё ещё ждёт — собеседник не '
            'дождётся ответа вовсе')

    def test_both_sides_share_one_rule(self):
        """⚠⚠ Условие ожидания **одно** на очередь описания и на отбор
        ответов. Разойдись они — либо письмо ждёт описания, которого уже не
        будет, либо отвечает, когда оно вот-вот ляжет."""
        import inspect

        from src.dating import mail_in_vision, mail_reply

        assert 'MAIL_IN_VISION_WAIT_SQL' in inspect.getsource(mail_reply), (
            'отбор ответов завёл свою копию правила ожидания')
        assert 'MAIL_IN_VISION_WAIT_SQL.format' in inspect.getsource(
            mail_in_vision), 'очередь описания правило не читает'
