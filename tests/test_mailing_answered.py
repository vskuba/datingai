"""Рассылка не уходит тому, кто ответил письмом: с ним работает «приставание».

## ⚠⚠ Что было не так

Рассылка — **холодное обращение к незнакомому**: один шаблон на сайт, метки вместо
имени, ни слова из переписки. Ответившему она не нужна и не должна приходить: у
него уже есть своё занятие — «приставание» (`favorite_mail`), которое пишет по
разговору и от той же девушки.

Поймано на проде 04.10.2026, пара Yulia ↔ Riano, и минуты здесь важнее слов:

| Когда | Что |
|-------|-----|
| 13:18:57 | он ответил в чате |
| 13:21:33 | **сочинено** письмо рассылки |
| 13:25:41 | пришло его письмо |
| 13:30:54 | ушёл наш ответ на письмо |
| 14:10:04 | **ушла рассылка** — следом за ответом, в ту же нить |

Отсюда и главная проверка файла: мало отсеять пару на наборе пула — между
сочинением и отправкой у рассылки проходят **часы**, и письмо он может прислать в
любую из них. Правило стоит в четырёх местах одним условием
(`pool_pair.pool_mail_answered_where`): набор, очередь отправки, уборка пула и
кнопка.

⚠ **Пара, а не человек.** Он написал одной нашей анкете и не знает второй — для
второй это законное холодное обращение, ровно как у первой реплики в чат и у
запрета `block`.
"""

import uuid

import pytest

from src.dating import send_round as round_
from src.dating.mailing import _pairs
from src.dating.pool import pool_sweep_mail_answered
from src.dating.send_round import send_round_run, send_why

pytestmark = pytest.mark.mail

# Выдуманный сайт: у соседа его нет, живую отправку задеть невозможно. ⚠ Свой, а
# не общий с `test_send_round.py`: их пулы и счётчики не должны видеть друг друга.
SITE = 999_785

# Сайт для анкеты: заведение сверяет номер у поддельного соседа.
LIVE_SITE = 343

# Смена во все сутки — чтобы проверки не зависели от часа прогона. ⚠ `24:00`, а не
# `23:45`: правая граница смены строгая, и четверть часа перед полуночью иначе
# красит сюиту каждый вечер (тот же довод, что в `test_send_round.py`).
ALWAYS = [{'from': '00:00', 'to': '24:00'}]


@pytest.fixture
async def site(auth_client):
    """Сайт с включённой отправкой рассылки. Убирает за собой всё."""
    from mysql_.mysql_ import mysql_get_db_async

    before = (await auth_client.get(
        f'/api/dating/site/{SITE}/mailing-schedule')).json()['result']

    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                          json={'schedule': ALWAYS})

    async with mysql_get_db_async() as db:
        await db.execute('SELECT is_gone FROM site WHERE site_id = %s', (SITE,))
        gone = int(((await db.fetchone()) or {}).get('is_gone') or 0)
        # ⚠ Метку «ушёл» снимаем: сайта с выдуманным номером у соседа нет, фоновая
        # сверка честно её ставит, а круг такие пропускает — писать некому.
        await db.execute(
            'INSERT INTO site (site_id, is_gone, mailing_send_on)'
            ' VALUES (%s, 0, 1) ON DUPLICATE KEY UPDATE is_gone = 0,'
            ' mailing_send_on = 1', (SITE,))
        await db.connection.commit()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                          json={'schedule': before['schedule']})
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET is_gone = %s, mailing_send_on = 0'
                         ' WHERE site_id = %s', (gone, SITE))
        await db.connection.commit()


@pytest.fixture
async def girl(auth_client):
    """Анкета-отправитель. Каскад убирает за ней письма и входящие."""
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': LIVE_SITE, 'login': f'ans-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    yield made

    await auth_client.delete(f'/api/dating/profile/{made}')


@pytest.fixture
async def other_girl(auth_client):
    """Вторая анкета — ею проверяется, что правило про **пару**, а не про человека."""
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': LIVE_SITE, 'login': f'ans2-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    yield made

    await auth_client.delete(f'/api/dating/profile/{made}')


@pytest.fixture
def post(monkeypatch):
    """Подменяет **поход на сайт**: считает письма вместо отправки.

    ⚠ Подменяется `mail_send_one` в модуле круга, а не транспорт: круг зовёт её
    напрямую, и заглушка ниже по стеку оставила бы возможность промаха.
    """
    sent = []

    async def fake(letter, profile, base, password, table='mailing'):
        sent.append({'id': int(letter['id']), 'table': table})

        from src.dating.mail_send import mail_send_mark

        await mail_send_mark(letter, sent=True, table=table)

    monkeypatch.setattr(round_, 'mail_send_one', fake)
    monkeypatch.setattr(round_, '_sent_at', {})
    # ⚠ Остуды нет: очередь спрашивает список остывших получателей, а живого Redis
    # у сюиты с хоста нет. Про саму остуду есть свой файл (`test_mail_wait.py`).
    monkeypatch.setattr(round_, 'mail_wait_uids', lambda site_id: [])

    return sent


async def _draft(profile_id: int, uid: str, table: str = 'mailing') -> int:
    """Кладёт черновик в пул напрямую: набор требует адресатов в сети."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            f'INSERT INTO {table} (site_id, profile_id, uid, name, body, status)'
            f" VALUES (%s, %s, %s, 'Тест', 'Текст письма', 'draft')",
            (SITE, int(profile_id), uid))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _incoming(profile_id: int, uid: str) -> int:
    """Его письмо этой анкете — то самое, после которого рассылки не бывает."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in (site_id, profile_id, message_id, uid, name,'
            " body, date_post) VALUES (%s, %s, %s, %s, 'Riano', 'Привет', NOW())",
            (SITE, int(profile_id), int(uuid.uuid4().int % 1_000_000_000), uid))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


class TestTheQueueSkipsHim:
    """⚠⚠ Главное: проверка стоит **перед отправкой**, а не только на наборе.

    Между сочинением и отправкой у рассылки проходят часы, и письмо он мог
    прислать в любую из них — именно так и вышло у Yulia с Riano.
    """

    async def test_a_letter_to_an_answered_pair_stays(self, site, girl, post):
        """Он ответил письмом — лежавшая карточка рассылки не уходит."""
        await _incoming(girl, 'riano')
        await _draft(girl, 'riano')

        assert (await send_round_run(0))['sent'] == 0
        assert post == [], 'рассылка ушла тому, кто уже ответил письмом'

    async def test_a_stranger_gets_his_letter_instead(self, site, girl, post):
        """⚠⚠ И это главное свойство: отказ одному не запирает очередь.

        Условие стоит **в запросе**, а не отсевом после выборки. Круг берёт из
        пула одно письмо за заход; отсей мы такое после — заход кончился бы
        ничем, и сайт молчал бы весь срок, имея письма незнакомым.
        """
        await _incoming(girl, 'riano')
        # Карточка ответившего ложится **первой**: `ORDER BY m.id` выдал бы её,
        # не умей запрос её пропустить.
        answered = await _draft(girl, 'riano')
        stranger = await _draft(girl, 'stranger')

        await send_round_run(0)

        assert len(post) == 1, 'заход ушёл ни с чем или унёс лишнее'
        assert post[0]['id'] == stranger, \
            f'круг взял письмо ответившего (#{answered}) вместо незнакомого'

    async def test_another_girl_still_writes_him(self, site, girl, other_girl,
                                                 post):
        """⚠ Правило про **пару**: он ответил этой, а второй не писал вовсе.

        Для второй это законное холодное обращение — тот же довод, что у запрета
        `block` и у первой реплики в чат.
        """
        await _incoming(girl, 'riano')
        await _draft(girl, 'riano')
        free = await _draft(other_girl, 'riano')

        await send_round_run(0)

        assert [one['id'] for one in post] == [free], \
            'ответ одной анкете отнял человека у всех прочих'

    async def test_other_pools_are_not_touched(self, site, girl, post):
        """⚠ Ответ и приставание — это и есть письма отвечавшему.

        Правило гасит только рассылку: ответ пишется **на его письмо**, а
        приставание — по переписке. Загляни оно в чужие пулы, занятие «Фаворитов»
        умерло бы целиком.
        """
        from mysql_.mysql_ import mysql_get_db_async

        await _incoming(girl, 'riano')
        await _draft(girl, 'riano')
        pestering = await _draft(girl, 'riano', 'favorite_mail')

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE site SET favorite_mail_send_on = 1'
                             ' WHERE site_id = %s', (SITE,))
            # ⚠ Письма избранным уходят только от анкеты в сети — своё правило
            # круга, и без него этот тест молчал бы по чужой причине.
            await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                             (int(girl),))
            await db.connection.commit()

        try:
            await send_round_run(0)

            assert [one['table'] for one in post] == ['favorite_mail'], \
                'рассылочное правило задело чужой пул'
            assert post[0]['id'] == pestering
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('UPDATE site SET favorite_mail_send_on = 0'
                                 ' WHERE site_id = %s', (SITE,))
                await db.connection.commit()


class TestThePoolIsSwept:
    """⚠⚠ Черновик, который отправка больше не возьмёт, обязан уйти из пула.

    Иначе он держит потолок (`mailing_pool_max`) вечно, а расходоваться ему
    нечем — ровно та беда, что уже случалась дважды: с черновиками ушедших анкет и
    с инвайтами ответившим («100 черновиков при потолке 100, живых 17»).
    """

    async def test_the_answered_draft_is_deleted(self, site, girl):
        from mysql_.mysql_ import mysql_get_db_async

        await _incoming(girl, 'riano')
        answered = await _draft(girl, 'riano')
        stranger = await _draft(girl, 'stranger')

        assert await pool_sweep_mail_answered(SITE) == 1

        async with mysql_get_db_async() as db:
            await db.execute('SELECT id FROM mailing WHERE id IN (%s, %s)',
                             (answered, stranger))
            left = {int(row['id']) for row in await db.fetchall() or []}

        assert left == {stranger}, 'уборка снесла не то, что надо'

    async def test_the_reply_in_the_pool_survives(self, site, girl):
        """⚠ Уборка — **только про рассылку**: его письмо ждёт ответа, и стереть
        черновик ответа значило бы оставить человека без него."""
        from mysql_.mysql_ import mysql_get_db_async

        mail_in_id = await _incoming(girl, 'riano')

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO mail_reply (site_id, profile_id, uid, name, body,'
                " status, mail_in_id) VALUES (%s, %s, 'riano', 'Riano',"
                " 'Ответ', 'draft', %s)", (SITE, int(girl), mail_in_id))
            await db.connection.commit()

        await pool_sweep_mail_answered(SITE)

        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM mail_reply WHERE site_id = %s'
                ' AND profile_id = %s', (SITE, int(girl)))
            left = int(((await db.fetchone()) or {})['n'])

        assert left == 1, 'уборка рассылки унесла черновик ответа'

    async def test_an_unknown_site_is_a_quiet_zero(self):
        """Ноль сайта — ноль работы, а не очистка всего."""
        assert await pool_sweep_mail_answered(0) == 0


class TestTheIdleReasonSaysSo:
    """⚠⚠ Молчащий пул обязан объяснять себя — то же правило, что у остуды и паузы.

    Пул полон, ошибок нет, а отправка молчит: без строки это читается как поломка,
    и такое молчание уже стоило времени.
    """

    async def test_the_reason_names_the_answer(self, site, girl):
        await _incoming(girl, 'riano')
        await _draft(girl, 'riano')

        why = await send_why(SITE, 'mailing', 0)

        assert 'ответил письмом' in why, why

    async def test_silence_when_there_is_someone_to_write(self, site, girl):
        """Есть кому писать — сказать нечего, даже если рядом лежит отказник."""
        await _incoming(girl, 'riano')
        await _draft(girl, 'riano')
        await _draft(girl, 'stranger')

        assert await send_why(SITE, 'mailing', 0) == ''


class TestThePoolIsNotFilledForHim:
    """Набор пула знает то же правило: сочинять письмо, которое не уйдёт, незачем.

    ⚠ Проверяется на `_pairs` — без базы и без агента: вопрос здесь чисто о том,
    какие тройки вообще предлагаются.
    """

    GIRLS = [{'id': 1}, {'id': 2}]
    PEOPLE = [{'uid': 'riano'}]
    TEMPLATES = [{'id': 10}, {'id': 20}]

    def test_the_answered_girl_is_taken_away(self):
        """Ему отвечала анкета №1 — от неё писем больше нет, ни одним шаблоном."""
        got = list(_pairs(self.GIRLS, self.PEOPLE, self.TEMPLATES, set(),
                          answered={(1, 'riano')}))

        assert got, 'адресат выпал целиком, а не одной анкетой'
        assert all(girl['id'] != 1 for girl, _, _ in got), got

    def test_nobody_writes_him_when_all_answered(self):
        """Ответил обеим — холодных обращений к нему не осталось вовсе."""
        got = list(_pairs(self.GIRLS, self.PEOPLE, self.TEMPLATES, set(),
                          answered={(1, 'riano'), (2, 'riano')}))

        assert got == []

    def test_other_people_are_untouched(self):
        """⚠ Отнимается анкета у **этой пары**, а не человек у сайта."""
        people = [{'uid': 'riano'}, {'uid': 'stranger'}]
        got = list(_pairs(self.GIRLS, people, self.TEMPLATES, set(),
                          answered={(1, 'riano'), (2, 'riano')}))

        assert {person['uid'] for _, _, person in got} == {'stranger'}
