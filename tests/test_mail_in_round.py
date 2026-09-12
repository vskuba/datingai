"""Круг забора писем: входящие приходят в панель сами.

⚠ **Наружу сюита не ходит** (`testing.md`), а забор — это заходы на сайт под
каждой анкетой. Поэтому здесь проверяется **решение круга**: на какие сайты он
идёт, кого обходит внутри сайта, когда считает, что пора, и что делает с отказом
одной анкеты. Сам разбор ответа сайта проверяется отдельно (`test_mail_in.py`).

Отсюда приём: подменяется `dating_mail_in_fetch` — единственное, что ходит наружу.

⚠ **Круг обходит анкеты, а не сайты**, и это не деталь реализации: у сайта нет
ручки «письма всех наших учёток». Сайт решает, пойдём ли и как часто; ходим под
каждой девушкой отдельно.
"""

import pytest

from src.dating import dating_mail_in_round as round_
from src.dating.dating_mail_in_round import (DATING_MAIL_IN_EVERY_DEFAULT,
                                             DATING_MAIL_IN_ROUND_KEY, _due,
                                             _every, _mark, _profiles, _sites,
                                             dating_mail_in_round_run)

pytestmark = pytest.mark.mail

# Выдуманный сайт: у соседа его нет, живой забор задеть невозможно.
SITE = 999_779


class _FakeRedis:
    """Redis настолько, насколько его читает круг: `get` и `setex`.

    ⚠ Подделка, а не живой Redis: сюита идёт **с хоста**, а Redis живёт в
    контейнере под своим именем — снаружи его просто нет.
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.broken = False

    def get(self, key):
        if self.broken:
            raise RuntimeError('Redis лёг')

        return self.store.get(key)

    def setex(self, key, seconds, value):
        if self.broken:
            raise RuntimeError('Redis лёг')

        self.store[key] = value
        self.ttl[key] = int(seconds)


@pytest.fixture
def clean_mark(monkeypatch):
    """Пустая память отметок на каждый тест."""
    fake = _FakeRedis()
    monkeypatch.setattr(round_, 'redis_conn_get', lambda: fake)

    return fake


@pytest.fixture
def only_ours(monkeypatch):
    """⚠⚠ Сужает **отбор** круга до выдуманного сайта.

    Без этого круг честно обходит и живой Ladadate: `mail_check_on` по умолчанию
    поднят у всех сайтов, и первый же прогон сюиты пошёл бы по тринадцати живым
    анкетам. Здесь наружу не ушло бы ничего — поход подменён, — но проверка
    «круг сайт не взял» зеленела бы или краснела по чужим строкам.

    Правило общее и записано в `testing.md`: **тест, зовущий круг, подменяет его
    отбор, а не только действие**. Оно появилось после того, как прогон сюиты
    испортил живую анкету, и этот случай — ровно тот же класс.

    ⚠⚠ **Отбор задаётся, а не фильтруется поверх живого**, и это исправление
    флейка. Раньше фикстура звала настоящий `_sites` и оставляла из ответа наш
    номер — то есть зависела от строки сайта в базе. А строку правит **фоновая
    сверка в контейнере панели**: она спрашивает у соседа список с меткой
    `dating`, не находит там выдуманный номер и честно метит его `is_gone = 1`.
    Заводится сверка от любого показа списка сайтов, не чаще раза в полминуты, —
    и за трёхминутный прогон срабатывает несколько раз.

    Фикстура `site` метку снимает, но **один раз, при подготовке**: попади сверка
    в промежуток между ней и вызовом круга — и круг не увидит сайта вовсе. Тест
    падал на «анкета не попала в круг», хотя круг работал верно. Один раз на
    шесть полных прогонов.

    Тесты, которые проверяют **сам отбор** (снятый выключатель, ушедший сайт),
    эту фикстуру не берут: они зовут настоящий `_sites` и смотрят его ответ.
    """
    async def ours():
        return [{'site_id': SITE}]

    monkeypatch.setattr(round_, '_sites', ours)


@pytest.fixture
def no_pause(monkeypatch):
    """Убирает паузу между анкетами: она про сайт, а не про правила отбора.

    ⚠ Секунда на анкету — защита от «тринадцати входов в одну секунду», и в живом
    круге она нужна. В тесте она только удлиняет прогон: наружу никто не ходит.
    """
    monkeypatch.setattr(round_, 'DATING_MAIL_IN_PAUSE_SEC', 0)


@pytest.fixture
async def site(auth_client):
    """Сайт с включённым забором и адресом API. Убирает за собой всё, что тронул.

    ⚠ **Метку «ушёл» снимаем сами**: сайта с выдуманным номером у соседа нет,
    фоновая сверка честно ставит ему `is_gone`, а круг такие пропускает.

    ⚠ **`api_base` обязателен**: без адреса круг сайт пропускает — ходить некуда.
    """
    from mysql_.mysql_ import mysql_get_db_async

    was = (await auth_client.get(
        f'/api/dating/site/{SITE}')).json()['result']['settings']

    async with mysql_get_db_async() as db:
        await db.execute('SELECT is_gone, mail_check_on FROM dating_site'
                         ' WHERE site_id = %s', (SITE,))
        row = await db.fetchone()
        gone = int((row or {}).get('is_gone') or 0)
        await db.execute(
            'INSERT INTO dating_site (site_id, is_gone, mail_check_on)'
            ' VALUES (%s, 0, 1)'
            ' ON DUPLICATE KEY UPDATE is_gone = 0, mail_check_on = 1', (SITE,))
        await db.connection.commit()

    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {**was, 'api_base': 'https://нет.тест'}})

    # ⚠ Сверяем, что адрес **встал**, и падаем здесь, если нет. Круг без него
    # молча пропускает сайт (`if not base: continue`), и тест падал бы на ассерте
    # «анкета не попала в круг» — то есть жаловался бы на круг, а причина была в
    # настройке. Поймано разовым падением в полном прогоне: за полторы тысячи
    # тестов настройки этого сайта правят многие, и один невезучий порядок
    # оставлял его без адреса.
    after = (await auth_client.get(
        f'/api/dating/site/{SITE}')).json()['result']['settings']
    assert after.get('api_base') == 'https://нет.тест', (
        'адрес API тестовому сайту не записался — круг пропустит его, и '
        f'проверки ниже будут про другое: {after}'
    )

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_site SET is_gone = %s WHERE site_id = %s',
                         (gone, SITE))
        await db.connection.commit()


@pytest.fixture
def fetched(monkeypatch):
    """Подменяет поход на сайт: считает вызовы вместо запросов.

    `fetched.boom` — отказ сайта на каждой анкете.
    """
    class _Calls(list):
        pass

    calls = _Calls()
    calls.result = {'found': 1, 'added': 1}
    calls.boom = False

    async def fake(profile, base, password):
        calls.append({'profile_id': int(profile['id']), 'base': base})
        if calls.boom:
            from src.dating.dating_mail_in import DatingMailInError

            raise DatingMailInError('сайт не ответил')

        return dict(calls.result)

    monkeypatch.setattr(round_, 'dating_mail_in_fetch', fake)

    return calls


@pytest.fixture
async def profile(auth_client):
    """Анкета выдуманного сайта — та, под которой круг пойдёт за письмами.

    ⚠ Заводится на **живом** сайте (343: заведение сверяет номер с соседом), а
    затем переставляется на выдуманный прямой правкой. Иначе круг ходил бы по
    живым анкетам Ladadate — а сюита наружу не ходит.
    """
    import uuid

    from mysql_.mysql_ import mysql_get_db_async

    # ⚠ Стенд, а не рабочий сайт: анкета сюиты не должна попадать в круги
    # воркера (`test_mail_in.py`, `STAND_SITE`). Ниже она всё равно переезжает на
    # выдуманный сайт — но между заведением и переездом успевает пройти круг.
    created = await auth_client.post('/api/dating/profile', json={
        'site_id': 999_343, 'login': f'in-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})
    made = created.json()['result']

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET site_id = %s WHERE id = %s',
                         (SITE, made))
        await db.connection.commit()

    yield made

    await auth_client.delete(f'/api/dating/profile/{made}')


# ── Куда идём ────────────────────────────────────────────────────────────────

async def test_mail_in_round_takes_site_with_check_on(site, profile, fetched, clean_mark,
                                                      no_pause, only_ours):
    """Поднятый выключатель — и письма приходят сами, без единого нажатия."""
    got = await dating_mail_in_round_run()

    assert profile in [call['profile_id'] for call in fetched]
    assert got['added'] >= 1


async def test_mail_in_round_skips_site_with_check_off(site, auth_client):
    """Снятая галочка останавливает **всю** цепочку: отвечать станет не на что.

    ⚠ Спрашиваем **настоящий отбор**, а не гоняем круг с подменённым: подмена
    отвечала бы на вопрос «взял ли круг то, что мы ему дали», а вопрос здесь —
    «попадает ли такой сайт в отбор вообще».
    """
    await auth_client.put(f'/api/dating/site/{SITE}/mail-check-on',
                          json={'mail_check_on': False})

    assert SITE not in [int(row['site_id']) for row in await round_._sites()]


async def test_mail_in_round_skips_gone_site(site):
    """Сайт, пропавший у соседа, писем не отдаст — ходить некуда."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_site SET is_gone = 1 WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()

    assert SITE not in [int(row['site_id']) for row in await round_._sites()]


async def test_mail_in_round_takes_a_live_site_with_the_switch_on(site):
    """Обратная сторона тех же двух: сайт с поднятой галочкой в отбор попадает.

    ⚠ Без неё пара выше зеленела бы и у сломанного отбора, который не берёт
    никого вовсе.
    """
    assert SITE in [int(row['site_id']) for row in await round_._sites()]


async def test_mail_in_round_skips_site_without_api_url(site, profile, fetched, clean_mark,
                                                        auth_client, no_pause, only_ours):
    """⚠ Забор идёт через API сайта: нет `api_base` — некуда стучаться."""
    was = (await auth_client.get(
        f'/api/dating/site/{SITE}')).json()['result']['settings']
    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {**was, 'api_base': ''}})

    await dating_mail_in_round_run()

    assert not fetched


# ── Кого обходим внутри сайта ────────────────────────────────────────────────

async def test_mail_in_round_walks_profile_by_profile(site, profile):
    """⚠ У сайта нет ручки «письма всех наших учёток»: ходим под каждой девушкой."""
    assert profile in [int(row['id']) for row in await _profiles(SITE)]


async def test_mail_in_round_skips_disabled_profile(site, profile, auth_client):
    """Выключенная учётка не работает вовсе — заходить под ней незачем."""
    await auth_client.put(f'/api/dating/profile/{profile}/enabled',
                          json={'enabled': False})

    assert profile not in [int(row['id']) for row in await _profiles(SITE)]


async def test_mail_in_round_ignores_schedule(site, profile, fetched, clean_mark,
                                              auth_client, no_pause, only_ours):
    """⚠ Письмо приходит, когда его написали, а не когда у девушки смена.

    Отвечать на него всё равно будут по часам отправки — но лежать оно к тому
    времени будет уже прочитанным. Привяжи мы забор к сменам, ночная почта ждала
    бы утра, чтобы только начать путь.
    """
    await auth_client.put(f'/api/dating/profile/{profile}/schedule-on',
                          json={'schedule_on': True})

    await dating_mail_in_round_run()

    assert profile in [call['profile_id'] for call in fetched]


async def test_mail_in_round_survives_one_profile_failure(site, profile, fetched,
                                                          clean_mark, no_pause, only_ours):
    """У неё мог протухнуть пароль, а у соседних всё в порядке."""
    fetched.boom = True

    got = await dating_mail_in_round_run()

    assert got['failed'] == 1
    assert got['added'] == 0


# ── Когда идём ───────────────────────────────────────────────────────────────

async def test_mail_in_round_skips_second_pass_in_a_row(site, profile, fetched, clean_mark,
                                                        no_pause, only_ours):
    """Отметка держит срок: тик раз в минуту — не обход раз в минуту."""
    await dating_mail_in_round_run()
    before = len(fetched)

    await dating_mail_in_round_run()

    assert len(fetched) == before


async def test_mail_in_round_marks_before_walking(site, profile, fetched, clean_mark,
                                                  no_pause, only_ours):
    """Обход идёт секундами на анкету, и следующий тик не должен завести второй."""
    fetched.boom = True

    await dating_mail_in_round_run()

    assert clean_mark.store.get(DATING_MAIL_IN_ROUND_KEY.format(site_id=SITE))


async def test_mail_in_round_redis_failure_means_no_work(site, profile, fetched,
                                                         clean_mark, no_pause,
                                                         only_ours):
    """⚠ Лишний обход — тринадцать входов подряд, а частые заходы сайт считает подозрением."""
    clean_mark.broken = True

    await dating_mail_in_round_run()

    assert not fetched


async def test_mail_in_round_mark_ttl_is_the_period_setting(site, profile, fetched, clean_mark,
                                                            no_pause, only_ours):
    """Redis гасит отметку сам: отдельного «когда истечёт» круг не хранит."""
    await dating_mail_in_round_run()

    assert (clean_mark.ttl[DATING_MAIL_IN_ROUND_KEY.format(site_id=SITE)]
            == DATING_MAIL_IN_EVERY_DEFAULT)


async def test_mail_in_round_period_comes_from_setting(site, auth_client):
    """Как часто ходить — решение сайта, а не константа кода."""
    was = (await auth_client.get(
        f'/api/dating/site/{SITE}')).json()['result']['settings']
    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {**was, 'mail_check_every': '900'}})
    try:
        assert await _every(SITE) == 900
    finally:
        await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})


async def test_mail_in_round_period_falls_back_to_default(site):
    """Не задано — пять минут, как у импорта переписки и по той же причине."""
    assert await _every(SITE) == DATING_MAIL_IN_EVERY_DEFAULT


def test_mail_in_round_mark_and_check_share_the_key(clean_mark):
    """`_mark` закрывает, отсутствие отметки открывает — других состояний нет."""
    assert _due(SITE)

    _mark(SITE, 60)

    assert not _due(SITE)


async def test_mail_in_round_empty_pass_writes_nothing(fetched, clean_mark, monkeypatch):
    """Нет сайтов с забором — нет и запросов: круг просыпается вхолостую часто."""
    async def no_sites():
        return []

    monkeypatch.setattr(round_, '_sites', no_sites)

    assert await dating_mail_in_round_run() == {'sites': 0, 'profiles': 0,
                                                'added': 0, 'failed': 0}


# ── Выключатель ──────────────────────────────────────────────────────────────

async def test_mail_in_round_check_is_on_by_default(auth_client):
    """⚠ Единственный выключатель почты, поднятый по умолчанию.

    Три соседних (`mailing_send_on`, `reply_send_on`, `reply_make_on`) лежат по
    нулям, потому что **пишут наружу**. Этот только читает: ничего не отправляет,
    никому не показывает анкету и не делает её онлайн. Выключенный по умолчанию,
    он означал бы панель, которая по умолчанию не показывает почту.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute("SELECT COLUMN_DEFAULT AS умолчание"
                         ' FROM INFORMATION_SCHEMA.COLUMNS'
                         " WHERE TABLE_SCHEMA = DATABASE()"
                         "   AND TABLE_NAME = 'dating_site'"
                         "   AND COLUMN_NAME = 'mail_check_on'")
        row = await db.fetchone()

    assert str((row or {}).get('умолчание')) == '1'


async def test_mail_in_round_handler_reports_on_for_unknown_site(auth_client):
    """⚠ Сайта нет в снимке — отвечаем «включено», а не «выключено».

    Иначе полоса показывала бы снятую галочку там, где круг на самом деле пойдёт.
    """
    answer = await auth_client.get('/api/dating/site/999780/mail-in')

    assert answer.json()['result']['mail_check_on'] is True


async def test_mail_in_round_switch_flips_through_the_handler(site, auth_client):
    """Круглый путь: сняли — прочли снятым, вернули — прочли поднятым."""
    await auth_client.put(f'/api/dating/site/{SITE}/mail-check-on',
                          json={'mail_check_on': False})

    answer = await auth_client.get(f'/api/dating/site/{SITE}/mail-in')

    assert answer.json()['result']['mail_check_on'] is False

    await auth_client.put(f'/api/dating/site/{SITE}/mail-check-on',
                          json={'mail_check_on': True})

    assert SITE in [int(row['site_id']) for row in await _sites()]
