"""Круг ответов: пул набирается сам, пока включена генерация.

⚠ **Наружу сюита не ходит** (`testing.md`), а набор ответа — это вызов LLM на
каждое письмо. Поэтому здесь проверяется **решение круга**, а не сам набор: кого
он берёт в работу, когда считает, что пора, и что делает с отметкой. Сам набор
проверяется отдельно (`test_mail_reply.py`).

Отсюда приём: подменяется `dating_mail_reply_make` — единственная дорогая часть.
Всё, что до неё, работает по-настоящему, с живой базой.

⚠ **Главное отличие от круга рассылки проверяется прямо**: расписание сайта здесь
не спрашивается. Круг готовит черновик, а уходит он вторым выключателем и по
своим часам — привяжи мы набор к сменам, письмо, пришедшее ночью, ждало бы утра
без всякой причины.
"""

import pytest

from src.dating import dating_reply_round as round_
from src.dating.dating_reply_round import (DATING_REPLY_BATCH_DEFAULT,
                                           DATING_REPLY_EVERY_DEFAULT,
                                           DATING_REPLY_ROUND_KEY, _batch, _due,
                                           _every, _mark, _sites,
                                           dating_reply_round_run)

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
        await db.execute('SELECT is_gone FROM dating_site WHERE site_id = %s', (SITE,))
        row = await db.fetchone()
        gone = int((row or {}).get('is_gone') or 0)
        await db.execute('UPDATE dating_site SET is_gone = 0 WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()

    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': True})

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': bool(before.get('reply_make_on'))})

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_site SET is_gone = %s WHERE site_id = %s',
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

    monkeypatch.setattr(round_, 'dating_mail_reply_make', fake)

    return calls


# ── Кого берём в работу ──────────────────────────────────────────────────────

async def test_сайт_с_генерацией_попадает_в_круг(site, made, clean_mark):
    """Поднятый выключатель — и круг берёт сайт сам, без единого нажатия."""
    got = await dating_reply_round_run()

    assert SITE in [call['site_id'] for call in made]
    assert got['made'] >= 1


async def test_выключенная_генерация_сайт_не_берёт(site, made, clean_mark,
                                                   auth_client):
    """⚠ Выключатель — единственное согласие на набор: снят, и круг проходит мимо."""
    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': False})

    await dating_reply_round_run()

    assert SITE not in [call['site_id'] for call in made]


async def test_ушедший_сайт_не_берём(site, made, clean_mark):
    """Сайт, пропавший у соседа, писем не примет — набирать ответы некому."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_site SET is_gone = 1 WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()

    await dating_reply_round_run()

    assert SITE not in [call['site_id'] for call in made]


async def test_отбор_идёт_по_флагу_а_не_по_анкетам(site):
    """`_sites` отдаёт сайты, а не анкеты: пул один на сайт."""
    assert SITE in await _sites()


# ── Когда идём ───────────────────────────────────────────────────────────────

async def test_второй_заход_подряд_не_идёт(site, made, clean_mark):
    """Отметка держит срок: тик раз в минуту не превращается в набор раз в минуту."""
    await dating_reply_round_run()
    было = len(made)

    await dating_reply_round_run()

    assert len(made) == было


async def test_отметка_ставится_до_набора(site, made, clean_mark):
    """⚠ Заход длится минутами, и сорвавшийся не должен звать второй.

    Отметку ставим **до** набора: иначе упавший заход оставлял бы сайт открытым,
    и следующий тик через минуту звал бы агента заново — на те же письма.
    """
    made.boom = True

    await dating_reply_round_run()

    assert clean_mark.store.get(DATING_REPLY_ROUND_KEY.format(site_id=SITE))


async def test_срыв_одного_сайта_не_роняет_круг(site, made, clean_mark):
    """Круг обходит остальные сайты дальше — сбой агента это не конец смены."""
    made.boom = True

    got = await dating_reply_round_run()

    assert got == {'sites': 0, 'made': 0}


async def test_сбой_redis_решается_в_сторону_бездействия(site, made, clean_mark):
    """⚠ Цена ошибок несимметрична: лишний заход стоит вызовов LLM, пропущенный — ожидания."""
    clean_mark.broken = True

    await dating_reply_round_run()

    assert SITE not in [call['site_id'] for call in made]


async def test_срок_отметки_и_есть_настройка(site, made, clean_mark):
    """Redis гасит отметку сам: отдельного «когда истечёт» круг не хранит."""
    await dating_reply_round_run()

    assert (clean_mark.ttl[DATING_REPLY_ROUND_KEY.format(site_id=SITE)]
            == DATING_REPLY_EVERY_DEFAULT)


def test_отметка_и_проверка_ходят_парой(clean_mark):
    """`_mark` закрывает, отсутствие отметки открывает — других состояний нет."""
    assert _due(SITE)

    _mark(SITE, 60)

    assert not _due(SITE)


# ── Сколько пишем за раз ─────────────────────────────────────────────────────

async def test_за_заход_пишем_ограниченно(site, made, clean_mark):
    """⚠ Предел **захода**, а не запаса: заход на три десятка длится полчаса."""
    await dating_reply_round_run()

    assert made[0]['limit'] == DATING_REPLY_BATCH_DEFAULT


async def test_значение_вне_набора_не_принимается(site, setting):
    """⚠ Потолок держит **набор реестра**, а не второе число в коде.

    Настройку правят руками, и «500» заняло бы круг до конца смены. Отдельного
    `min()` в круге нет намеренно: значение вне набора не проходит вовсе, и
    возвращается умолчание.
    """
    await setting('reply_batch', '500')

    assert await _batch(SITE) == DATING_REPLY_BATCH_DEFAULT


async def test_сколько_за_заход_решает_сайт(site, setting):
    """Значение из набора берётся как есть: предел захода — решение сайта."""
    await setting('reply_batch', '10')

    assert await _batch(SITE) == 10


async def test_частота_берётся_из_настройки(site, setting):
    """Как часто набирать — решение сайта, а не константа кода."""
    await setting('reply_every', '900')

    assert await _every(SITE) == 900


async def test_без_настройки_частота_по_умолчанию(site):
    """Не задано — пять минут: письма приходят реже, чем стоит спрашивать."""
    assert await _every(SITE) == DATING_REPLY_EVERY_DEFAULT


# ── Чем отличается от рассылки ───────────────────────────────────────────────

async def test_расписание_сайта_не_спрашивается(site, made, clean_mark,
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
        await dating_reply_round_run()

        assert SITE in [call['site_id'] for call in made]
    finally:
        await auth_client.put(
            f'/api/dating/site/{SITE}/mailing-schedule-on',
            json={'mailing_schedule_on': before['mailing_schedule_on']})


async def test_круг_готовит_но_не_отправляет(site, made, clean_mark):
    """Круг зовёт только набор. Отправка — другой круг и другой выключатель."""
    import inspect

    исходник = inspect.getsource(round_)

    assert 'dating_mail_send' not in исходник
    assert 'reply_send_on' not in исходник


async def test_пустой_обход_молчит(made, clean_mark, monkeypatch):
    """Нет сайтов с генерацией — нет и запросов: круг просыпается вхолостую часто."""
    async def нет_сайтов():
        return []

    monkeypatch.setattr(round_, '_sites', нет_сайтов)

    assert await dating_reply_round_run() == {'sites': 0, 'made': 0}
    assert not made


# ── Кнопки больше нет ────────────────────────────────────────────────────────

def test_кнопки_написать_ответы_в_разметке_нет():
    """⚠ Кнопка обещала то, что теперь делает круг, — и осталась бы враньём.

    Проверяем разметку, а не намерение: вернись кнопка копипастом из соседней
    вкладки, человек снова решил бы, что без нажатия ответы не пишутся.
    """
    from pathlib import Path

    разметка = Path('data/uvicorn/template/admin/profile/site/_base.html').read_text()

    assert 'in-reply-make' not in разметка
