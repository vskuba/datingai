"""Круг ответов: пул набирается сам, пока включена генерация.

⚠ **Наружу сюита не ходит** (`testing.md`), а набор ответа — это вызов LLM на
каждое письмо. Поэтому здесь проверяется **решение круга**, а не сам набор: кого
он берёт в работу, когда считает, что пора, и что делает с отметкой. Сам набор
проверяется отдельно (`test_mail_reply.py`).

Отсюда приём: подменяется `mail_reply_make` — единственная дорогая часть.
Всё, что до неё, работает по-настоящему, с живой базой.

⚠ **Главное отличие от круга рассылки проверяется прямо**: расписание сайта здесь
не спрашивается. Круг готовит черновик, а уходит он вторым выключателем и по
своим часам — привяжи мы набор к сменам, письмо, пришедшее ночью, ждало бы утра
без всякой причины.
"""

import pytest

from src.dating import reply_round as round_
from src.dating.reply_round import (REPLY_BATCH_DEFAULT,
                                           REPLY_EVERY_DEFAULT,
                                           REPLY_ROUND_KEY, _batch, _due,
                                           _every, _mark, _sites,
                                           reply_round_run)

pytestmark = pytest.mark.mail

# Выдуманный сайт: у соседа его нет, живую генерацию задеть невозможно.
SITE = 999_778


class _FakeRedis:
    """Redis настолько, насколько его читает круг: `get` и `setex`.

    ⚠ Подделка, а не живой Redis, и не ради скорости: сюита идёт **с хоста**, а
    Redis живёт в контейнере под своим именем — снаружи его просто нет. Тот же
    приём и та же причина, что у круга рассылки (`test_mailing_round.py`).
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
    """Пустая память отметок на каждый тест.

    ⚠ Иначе тесты влияли бы друг на друга: первый ставит отметку на пять минут, и
    все следующие видят «ещё не пора».
    """
    fake = _FakeRedis()
    monkeypatch.setattr(round_, 'redis_conn_get', lambda: fake)

    return fake


@pytest.fixture
async def site(auth_client):
    """Сайт с включённой генерацией ответов. Убирает за собой всё, что тронул.

    ⚠ **Метку «ушёл» снимаем сами, и без неё тест не работал бы вовсе.** Сайта с
    выдуманным номером у соседа нет, фоновая сверка честно ставит ему `is_gone`, а
    круг такие пропускает. Ручки «вернуть сайт» здесь нет и быть не должно (список
    ведёт RhapsodyAi), поэтому правим строку снимка напрямую.
    """
    from mysql_.mysql_ import mysql_get_db_async

    before = (await auth_client.get(f'/api/dating/site/{SITE}/reply')).json()['result']

    async with mysql_get_db_async() as db:
        await db.execute('SELECT is_gone FROM site WHERE site_id = %s', (SITE,))
        row = await db.fetchone()
        gone = int((row or {}).get('is_gone') or 0)
        await db.execute('UPDATE site SET is_gone = 0 WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()

    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': True})

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': bool(before.get('reply_make_on'))})

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET is_gone = %s WHERE site_id = %s',
                         (gone, SITE))
        await db.connection.commit()


@pytest.fixture
async def setting(auth_client):
    """Ставит настройку сайта и возвращает прежние на место.

    ⚠ Настройки едут **одним словарём**, а не по ключу: ручка переписывает
    `settings` целиком. Пришли мы один ключ — остальные пропали бы.
    """
    before = (await auth_client.get(
        f'/api/dating/site/{SITE}')).json()['result']['settings']

    async def put(key: str, value: str) -> None:
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {**before, key: value}})

    yield put

    await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})


@pytest.fixture
def made(monkeypatch):
    """Подменяет набор ответов: считает вызовы вместо походов к LLM.

    Итог захода настраивается — `made.result.update(...)`.
    """
    class _Calls(list):
        pass

    calls = _Calls()
    calls.result = {'made': 1, 'skipped': 0, 'reasons': [], 'left': 0}
    calls.boom = False

    async def fake(site_id, limit):
        calls.append({'site_id': int(site_id), 'limit': int(limit)})
        if calls.boom:
            raise RuntimeError('агент не ответил')

        return dict(calls.result)

    monkeypatch.setattr(round_, 'mail_reply_make', fake)

    return calls


# ── Кого берём в работу ──────────────────────────────────────────────────────

async def test_reply_round_takes_site_with_making_on(site, made, clean_mark):
    """Поднятый выключатель — и круг берёт сайт сам, без единого нажатия."""
    got = await reply_round_run()

    assert SITE in [call['site_id'] for call in made]
    assert got['made'] >= 1


async def test_reply_round_skips_site_with_making_off(site, made, clean_mark,
                                                      auth_client):
    """⚠ Выключатель — единственное согласие на набор: снят, и круг проходит мимо."""
    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': False})

    await reply_round_run()

    assert SITE not in [call['site_id'] for call in made]


async def test_reply_round_skips_gone_site(site, made, clean_mark):
    """Сайт, пропавший у соседа, писем не примет — набирать ответы некому."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET is_gone = 1 WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()

    await reply_round_run()

    assert SITE not in [call['site_id'] for call in made]


async def test_reply_round_selects_by_flag_not_by_profiles(site):
    """`_sites` отдаёт сайты, а не анкеты: пул один на сайт."""
    assert SITE in await _sites()


# ── Когда идём ───────────────────────────────────────────────────────────────

async def test_reply_round_skips_second_pass_in_a_row(site, made, clean_mark):
    """Отметка держит срок: тик раз в минуту не превращается в набор раз в минуту."""
    await reply_round_run()
    before = len(made)

    await reply_round_run()

    assert len(made) == before


async def test_reply_round_marks_before_filling(site, made, clean_mark):
    """⚠ Заход длится минутами, и сорвавшийся не должен звать второй.

    Отметку ставим **до** набора: иначе упавший заход оставлял бы сайт открытым,
    и следующий тик через минуту звал бы агента заново — на те же письма.
    """
    made.boom = True

    await reply_round_run()

    assert clean_mark.store.get(REPLY_ROUND_KEY.format(site_id=SITE))


async def test_reply_round_survives_one_site_failure(site, made, clean_mark):
    """Круг обходит остальные сайты дальше — сбой агента это не конец смены."""
    made.boom = True

    got = await reply_round_run()

    assert got == {'sites': 0, 'made': 0}


async def test_reply_round_redis_failure_means_no_work(site, made, clean_mark):
    """⚠ Цена ошибок несимметрична: лишний заход стоит вызовов LLM, пропущенный — ожидания."""
    clean_mark.broken = True

    await reply_round_run()

    assert SITE not in [call['site_id'] for call in made]


async def test_reply_round_mark_ttl_is_the_period_setting(site, made, clean_mark):
    """Redis гасит отметку сам: отдельного «когда истечёт» круг не хранит."""
    await reply_round_run()

    assert (clean_mark.ttl[REPLY_ROUND_KEY.format(site_id=SITE)]
            == REPLY_EVERY_DEFAULT)


def test_reply_round_mark_and_check_share_the_key(clean_mark):
    """`_mark` закрывает, отсутствие отметки открывает — других состояний нет."""
    assert _due(SITE)

    _mark(SITE, 60)

    assert not _due(SITE)


# ── Сколько пишем за раз ─────────────────────────────────────────────────────

async def test_reply_round_writes_a_limited_batch(site, made, clean_mark):
    """⚠ Предел **захода**, а не запаса: заход на три десятка длится полчаса."""
    await reply_round_run()

    assert made[0]['limit'] == REPLY_BATCH_DEFAULT


async def test_reply_round_rejects_batch_out_of_range(site, setting):
    """⚠⚠ Потолок держит **чтение настройки**, а не второе число в круге.

    Настройку правят руками — seed, чужой токен, phpMyAdmin, — и «500» заняло бы
    круг до конца суток. Отдельного `min()` в круге нет намеренно: значение вне
    границ не проходит чтение, и возвращается умолчание.

    ⚠ Прежде границу держал **набор** реестра: 500 не было среди вариантов. Когда
    счётчики перевели на числовой ввод, набор исчез — и этот тест поймал, что
    вместе с ним молча исчезла единственная проверка. Теперь её держат `min`/`max`
    (`site_seconds`).
    """
    await setting('reply_batch', '500')

    assert await _batch(SITE) == REPLY_BATCH_DEFAULT


async def test_reply_round_rejects_negative_batch(site, setting):
    """⚠ И отрицательное: «-5» прошло бы любую проверку «не больше потолка»."""
    await setting('reply_batch', '-5')

    assert await _batch(SITE) == REPLY_BATCH_DEFAULT


async def test_reply_round_batch_size_comes_from_site(site, setting):
    """Значение из набора берётся как есть: предел захода — решение сайта."""
    await setting('reply_batch', '10')

    assert await _batch(SITE) == 10


async def test_reply_round_period_comes_from_setting(site, setting):
    """Как часто набирать — решение сайта, а не константа кода."""
    await setting('reply_every', '900')

    assert await _every(SITE) == 900


async def test_reply_round_period_falls_back_to_default(site):
    """Не задано — пять минут: письма приходят реже, чем стоит спрашивать."""
    assert await _every(SITE) == REPLY_EVERY_DEFAULT


# ── Чем отличается от рассылки ───────────────────────────────────────────────

async def test_reply_round_ignores_site_schedule(site, made, clean_mark,
                                                 auth_client):
    """⚠ Ключевое отличие от круга рассылки, и оно намеренное.

    Расписание рассылки выключено — а ответы всё равно набираются: здесь готовится
    черновик, который никуда не уходит без второго выключателя. Привяжи мы набор к
    сменам, письмо, пришедшее под конец смены, ждало бы ответа до завтра.
    """
    before = (await auth_client.get(
        f'/api/dating/site/{SITE}/mailing-schedule')).json()['result']
    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule-on',
                          json={'mailing_schedule_on': False})
    try:
        await reply_round_run()

        assert SITE in [call['site_id'] for call in made]
    finally:
        await auth_client.put(
            f'/api/dating/site/{SITE}/mailing-schedule-on',
            json={'mailing_schedule_on': before['mailing_schedule_on']})


async def test_reply_round_prepares_but_never_sends(site, made, clean_mark):
    """Круг зовёт только набор. Отправка — другой круг и другой выключатель."""
    import inspect

    source = inspect.getsource(round_)

    assert 'mail_send' not in source
    assert 'reply_send_on' not in source


async def test_reply_round_empty_pass_writes_nothing(made, clean_mark, monkeypatch):
    """Нет сайтов с генерацией — нет и запросов: круг просыпается вхолостую часто."""
    async def no_sites():
        return []

    monkeypatch.setattr(round_, '_sites', no_sites)

    assert await reply_round_run() == {'sites': 0, 'made': 0}
    assert not made


# ── Кнопки больше нет ────────────────────────────────────────────────────────

def test_reply_round_has_no_write_button_in_markup():
    """⚠ Кнопка обещала то, что теперь делает круг, — и осталась бы враньём.

    Проверяем разметку, а не намерение: вернись кнопка копипастом из соседней
    вкладки, человек снова решил бы, что без нажатия ответы не пишутся.
    """
    from pathlib import Path

    markup = Path('data/uvicorn/template/admin/dating/profile/site/_base.html').read_text()

    assert 'in-reply-make' not in markup
