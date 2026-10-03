"""Часовой предел получателя — **не** смерть письма.

## ⚠⚠ Что чинится

Сайт отвечает на `check-send`:

    403 {"Error":"The user's hourly incoming message limit has been exceeded",
         "Reason":"ERROR_MESSAGE_USER_HOUR_IN_LIMIT"}

Это состояние **его** ящика, и через час оно пройдёт. Панель же понимала отказ как
вечный: причина ложилась в `error`, а строки с причиной круг отправки не берёт
никогда — письмо умирало, и пара «адресат — шаблон» закрывалась навсегда
(`uq_mailing_once` считает и мёртвые строки).

Цена, снятая с прода 03.10.2026: 1463 отказа против 184 разрешений за трое суток,
1392 из них — этот предел. Пул рассылки: 100 черновиков при потолке 100, живых 17.
Набор видел пул полным и не готовил ничего, отправка точила остаток.

## Что здесь проверяется

**Что письмо остаётся чистым.** Главное: `status = 'draft'`, `error = ''` — то
есть круг к нему вернётся сам.

**Что остуда запоминается и действует.** Адресат попадает в список остывающих, и
очередь отправки его обходит; через срок — снова можно.

**Что вечные отказы остудой не считаются.** «Он закрылся» и «не подходите по
возрасту» обязаны по-прежнему доходить до `block.py`: перепутай мы их, запрет
никогда не завёлся бы, и мы писали бы закрывшемуся каждый час.
"""

import inspect
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_send import (DatingMailSendError, DatingMailWaitError,
                                  mail_send_fail, mail_send_one)
from src.dating import mail_wait
from src.dating.mail_wait import (MAIL_WAIT_KEY, MAIL_WAIT_MARKS,
                                  mail_wait_left, mail_wait_mark,
                                  mail_wait_uids)

from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

pytestmark = pytest.mark.mail

# Сайт для писем — выдуманный: своей таблицы сайтов здесь нет, `site_id` просто
# ключ. Тот же развод и та же причина, что в `test_mail_send.py`.
SITE = 999_778

# Анкета заводится на живом номере: заведение сверяет сайт у соседа.
LIVE_SITE = 343

HOURLY = ("сайт отказал: The user's hourly incoming message limit has been "
          'exceeded (ERROR_MESSAGE_USER_HOUR_IN_LIMIT) '
          '(/messages/check-send/812886 → 403)')

AGE = ('сайт отказал: You do not match the criteria of age to send messages to '
       'this user (ERROR_MESSAGE_AGE_CRITERIA) (/messages/check-send/1 → 403)')

DENY = ('сайт отказал: User ignore messages from you (ERROR_MESSAGE_USER_DENY) '
        '(/messages/send → 403)')


class _FakeRedis:
    """Redis настолько, насколько его читает остуда: одно упорядоченное множество.

    ⚠ **Подделка, а не живой Redis**, как и у снимка ленты (`test_pool.py`):
    сюита идёт с хоста, а адрес в `.env` — внутренний адрес контейнера. Проверять
    здесь нечего: сортированное множество это работа Redis, а наше — что мы
    кладём срок и читаем непросроченное.
    """

    def __init__(self):
        self.rows: dict = {}

    def zadd(self, key, mapping):
        self.rows.setdefault(str(key), {}).update(
            {str(k): float(v) for k, v in mapping.items()})

    def zscore(self, key, member):
        return self.rows.get(str(key), {}).get(str(member))

    def zrangebyscore(self, key, low, high):
        low = float('-inf') if low == '-inf' else float(low)
        high = float('inf') if high == '+inf' else float(high)

        return [one for one, score in self.rows.get(str(key), {}).items()
                if low <= score <= high]

    def zremrangebyscore(self, key, low, high):
        for one in self.zrangebyscore(key, low, high):
            self.rows.get(str(key), {}).pop(one, None)

    def expire(self, key, seconds):
        return True

    def delete(self, key):
        self.rows.pop(str(key), None)


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    """Подменяет Redis остуды. Пустой у каждого теста — остуда живёт час."""
    fake = _FakeRedis()
    monkeypatch.setattr(mail_wait, 'redis_conn_get', lambda: fake)

    return fake


@pytest.fixture
async def profile(auth_client):
    """Анкета-отправитель со своим паролем — тем, который заглушка считает верным."""
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': LIVE_SITE, 'login': f'wait-{uuid.uuid4().hex[:8]}@example.com',
        'password': SITE_API_FAKE_PASSWORD})).json()['result']

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET password_source = 'own'"
                         ' WHERE id = %s', (made,))
        await db.connection.commit()
        await db.execute('SELECT id, site_id, login, password, password_source'
                         ' FROM profile WHERE id = %s', (made,))
        row = dict(await db.fetchone())

    # ⚠ Сайт подменяем на выдуманный **после** заведения: остуда ключуется сайтом
    # письма, а живой номер делят с другими тестами.
    row['site_id'] = SITE

    yield row

    await auth_client.delete(f'/api/dating/profile/{made}')


@pytest.fixture
async def letter(profile):
    """Письмо в пуле — то, которое сайт сейчас не возьмёт."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mailing'
            ' (site_id, profile_id, mail_id, uid, name, body, status)'
            " VALUES (%s, %s, NULL, '812886', 'Vincent', 'Привет!', 'draft')",
            (SITE, profile['id']))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        made = int((await db.fetchone())['id'])

    yield await _row(made)

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM mailing WHERE id = %s', (made,))
        await db.connection.commit()


async def _row(letter_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, mail_id, uid, name, body,'
                         ' status, error, sent_at FROM mailing WHERE id = %s',
                         (int(letter_id),))

        return dict(await db.fetchone())


class TestTheRefusalIsRecognised:
    """Приметы: что считается остудой, а что отказом навсегда."""

    def test_the_hourly_limit_is_a_cooldown(self):
        assert mail_wait_mark(SITE, '812886', HOURLY) == 3600, \
            'часовой предел не признан остудой — письма снова будут умирать'

    def test_the_new_user_daily_limit_is_a_day(self):
        """⚠ Срок — слово сайта («daily limit»), а не наша осторожность."""
        said = ('сайт отказал: Exceeded the daily limit to send messages to new '
                'user (ERROR_MESSAGE_NEW_USER_DAILY_LIMIT) (→ 403)')

        assert mail_wait_mark(SITE, '812886', said) == 24 * 3600

    def test_the_age_refusal_is_not_a_cooldown(self):
        """⚠⚠ Возраст девушки через час не изменится.

        Сочти мы его остудой — запрет (`block.py`) не завёлся бы никогда, и
        каждый час уходило бы новое письмо тому, кто его не получит.
        """
        assert mail_wait_mark(SITE, '1', AGE) == 0, \
            'вечный отказ по возрасту принят за остуду'

    def test_the_deny_refusal_is_not_a_cooldown(self):
        assert mail_wait_mark(SITE, '1', DENY) == 0, \
            '«он закрылся» принято за остуду — запрет не заведётся'

    def test_a_plain_failure_is_not_a_cooldown(self):
        assert mail_wait_mark(SITE, '1', 'сайт не отвечает: таймаут') == 0
        assert mail_wait_mark(SITE, '1', '') == 0

    def test_nobody_is_marked_without_a_site_or_a_person(self):
        """⚠ Ключ остуды — сайт, и без него запись ушла бы в чужой список."""
        assert mail_wait_mark(0, '812886', HOURLY) == 0
        assert mail_wait_mark(SITE, '', HOURLY) == 0

    def test_each_mark_carries_its_own_term_and_words(self):
        """⚠⚠ Почему приметы здесь константами, а не настройкой, как у запрета:
        примета несёт срок и слова, а их списком через запятую не записать."""
        for mark, wait, words in MAIL_WAIT_MARKS:
            assert mark.startswith('ERROR_'), mark
            assert wait > 0 and words, mark


class TestTheListWorks:
    """Список остывающих: его читают набор пула и очередь отправки."""

    def test_a_marked_person_is_in_the_list(self):
        mail_wait_mark(SITE, '812886', HOURLY)

        assert '812886' in mail_wait_uids(SITE)
        assert mail_wait_left(SITE, '812886') > 0

    def test_the_list_is_per_site(self):
        """⚠ Предел считает сайт, и список у каждого свой."""
        mail_wait_mark(SITE, '812886', HOURLY)

        assert mail_wait_uids(SITE + 1) == []

    def test_an_unknown_person_waits_nothing(self):
        assert mail_wait_left(SITE, 'nobody') == 0
        assert mail_wait_uids(SITE) == []

    def test_an_expired_cooldown_lets_the_letter_go(self, redis):
        """⚠ Остуда кончается сама, по времени, а не по чьему-то решению."""
        # Срок в прошлом — ровно то, что бывает через час.
        redis.zadd(MAIL_WAIT_KEY.format(site_id=SITE), {'812886': 1.0})

        assert mail_wait_left(SITE, '812886') == 0
        assert mail_wait_uids(SITE) == [], 'истёкшая остуда не вымелась'


class TestTheLetterSurvives:
    """⚠⚠ Ради этого всё и затевалось: письмо остаётся в пуле живым."""

    async def test_the_draft_keeps_no_error(self, site_api, letter, profile):
        site_api.fill_inbox('812886')

        with pytest.raises(DatingMailWaitError):
            await mail_send_one(letter, profile, SITE_API_FAKE_BASE,
                                SITE_API_FAKE_PASSWORD)

        row = await _row(letter['id'])
        assert row['status'] == 'draft', 'письмо похоронено временным отказом'
        assert row['error'] == '', \
            'причина легла в error — круг к письму больше не вернётся'

    async def test_the_person_cools_down(self, site_api, letter, profile):
        site_api.fill_inbox('812886')

        with pytest.raises(DatingMailWaitError):
            await mail_send_one(letter, profile, SITE_API_FAKE_BASE,
                                SITE_API_FAKE_PASSWORD)

        assert '812886' in mail_wait_uids(SITE), \
            'остуда не записалась — следующий заход постучит в ту же дверь'

    async def test_the_words_say_the_letter_is_whole(self, site_api, letter,
                                                     profile):
        """⚠ Человек читает ответ кнопки и по нему решает, что делать. «Не ушло»
        без «письмо цело» он прочтёт как потерю."""
        site_api.fill_inbox('812886')

        with pytest.raises(DatingMailWaitError) as got:
            await mail_send_one(letter, profile, SITE_API_FAKE_BASE,
                                SITE_API_FAKE_PASSWORD)

        assert 'ящик' in str(got.value) and 'цело' in str(got.value), got.value

    async def test_a_real_failure_is_not_a_wait(self, site_api, letter, profile):
        """⚠ Обратная сторона: обычная неудача остудой **не** считается.

        Иначе «не ушло» стало бы неотличимо от «ещё не пробовали»: причину в
        письмо не записал бы никто, и человек не узнал бы, что сайт сломался.

        ⚠ Саму причину пишет не отправка, а дверь исхода (`mail_send_fail`) —
        это проверяет `TestTheOneDoor` ниже.
        """
        site_api.refuse(500, {'Error': 'boom'}, on='check-send')

        with pytest.raises(DatingMailSendError) as got:
            await mail_send_one(letter, profile, SITE_API_FAKE_BASE,
                                SITE_API_FAKE_PASSWORD)

        assert not isinstance(got.value, DatingMailWaitError), \
            'поломка сайта принята за остуду — письмо будет ждать вечно'
        assert mail_wait_uids(SITE) == [], 'остуда записана по чужому отказу'


class TestTheOneDoor:
    """⚠⚠ Решение «портить ли письмо» живёт в одной двери на пять дорог."""

    async def test_the_door_keeps_the_letter_clean(self, letter):
        await mail_send_fail(letter, DatingMailWaitError('подождём'))

        assert (await _row(letter['id']))['error'] == ''

    async def test_the_door_marks_a_real_failure(self, letter):
        await mail_send_fail(letter, DatingMailSendError('сайт сломался'))

        assert (await _row(letter['id']))['error'] == 'сайт сломался'

    async def test_the_door_takes_a_bare_string_too(self, letter):
        """⚠ Строку принимаем: не всякий вызывающий держит исключение в руках."""
        await mail_send_fail(letter, 'просто причина')

        assert (await _row(letter['id']))['error'] == 'просто причина'

    def test_every_sending_path_goes_through_it(self):
        """⚠⚠ Четыре копии `mail_send_mark(sent=False, …)` и были той бедой: с
        остудой правил стало два, и забытая копия хоронила бы письма дальше."""
        for module in ('src.dating.send_round',
                       'src.uvicorn.routers.dating.mailing',
                       'src.uvicorn.routers.dating.favorite_mail',
                       'src.uvicorn.routers.dating.mail_in'):
            code = inspect.getsource(__import__(module, fromlist=['x']))

            assert 'mail_send_fail(' in code, f'{module} шлёт мимо общей двери'
            assert 'mail_send_mark(letter, sent=False' not in code, \
                f'{module} снова пишет причину сам'


class TestTheQueueSkipsThem:
    """⚠ Очередь отправки обходит остывших — **условием в запросе**."""

    def test_the_selection_asks_the_list(self):
        """Круг берёт из пула одно письмо за заход. Отсей он остывшего после
        выборки — заход кончился бы ничем при сорока девяти письмах другим."""
        from src.dating.send_round import _oldest

        code = inspect.getsource(_oldest)

        assert 'mail_wait_uids' in code, 'очередь снова не смотрит на остуду'
        assert 'NOT IN' in code, 'остывшие отсеиваются после выборки, а не в ней'

    def test_the_idle_reason_names_the_cooldown(self):
        """⚠ «Пул полон, а ничего не уходит» — состояние, которое без объяснения
        читается как поломка (`send_why`)."""
        from src.dating.send_round import _cooling_wait

        code = inspect.getsource(_cooling_wait)

        assert 'mail_wait_uids' in code
        assert 'Письма целы' in code


class TestThePoolCountsLiveDraftsOnly:
    """⚠⚠ Потолок пула — это «сколько готово к отправке», а не «сколько лежит».

    83 мёртвых черновика держали пул рассылки полным, и набор не шёл вовсе.
    """

    def test_mailing_counts_only_clean_drafts(self):
        from src.dating.mailing_round import _pool_size

        assert "error = ''" in inspect.getsource(_pool_size), \
            'потолок рассылки снова считает письма с ошибкой'

    def test_favorites_count_only_clean_drafts_for_the_round(self):
        from src.dating.favorite_mail_round import _work

        code = inspect.getsource(_work)

        assert 'ready_only=True' in code, \
            'круг избранных считает место вместе с застрявшими письмами'

    def test_the_page_still_shows_the_whole_pool(self):
        """⚠ Два счёта, и путать их нельзя: человек видит пул целиком, включая
        застрявшее, — это его работа. Правило то же, что у инвайтов."""
        from src.dating.favorite_mail import favorite_mail_pool_left

        code = inspect.getsource(favorite_mail_pool_left)

        assert 'ready_only' in code and "where = \" AND error = ''\"" in code


class TestThePoolsDoNotCollide:
    """⚠⚠ Пауза между письмами одного захода: без неё сайт отвечает «слишком часто».

    Четыре случая за трое суток на проде, и все одинаковы: `check-send` второго
    пула через 64–94 мс после удачной отправки первого.
    """

    def test_the_gap_is_bigger_than_the_measured_threshold(self):
        from src.dating.send_round import SEND_ROUND_GAP_SEC, SEND_ROUND_TICK_SEC

        assert SEND_ROUND_GAP_SEC >= 1.0, 'пауза меньше измеренного порога'
        assert SEND_ROUND_GAP_SEC < SEND_ROUND_TICK_SEC, 'пауза съедает заход'

    def test_the_round_waits_between_pools(self):
        from src.dating.send_round import send_round_run

        code = inspect.getsource(send_round_run)

        assert 'SEND_ROUND_GAP_SEC' in code, 'пулы снова идут подряд'
        assert 'touched' in code, \
            'пауза стоит и перед первым письмом — заход дороже без нужды'


class TestTheRefusalIsToldApart:
    """⚠⚠ Под одним 403 у сайта пять разных событий, и чинятся они по-разному."""

    def test_each_reason_has_its_own_words(self):
        import httpx

        from src.dating.site_api import _refusal

        cases = {
            'ERROR_MESSAGE_USER_HOUR_IN_LIMIT': 'ящик',
            'ERROR_MESSAGE_AGE_CRITERIA': 'возраст',
            'ERROR_MESSAGE_USER_DENY': 'закрылся',
            'ERROR_TOO_MANY_REQUESTS': 'слишком часто',
        }

        for reason, word in cases.items():
            answer = httpx.Response(403, json={'Error': 'x', 'Reason': reason})

            assert word in _refusal('/messages/check-send/1', answer), \
                f'{reason} по-прежнему объясняется чужими словами'

    def test_the_site_code_stays_in_the_string(self):
        """⚠⚠ На коде держатся разборы: `block_deny` ищет своё слово, `mail_wait`
        своё. Замени мы код фразой — оба перестали бы узнавать отказ, и молча."""
        import httpx

        from src.dating.site_api import _refusal

        said = _refusal('/messages/send', httpx.Response(
            403, json={'Error': 'x', 'Reason': 'ERROR_MESSAGE_USER_DENY'}))

        assert 'ERROR_MESSAGE_USER_DENY' in said
