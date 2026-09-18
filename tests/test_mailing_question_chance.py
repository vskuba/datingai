"""Личный вопрос спрашивается не в каждом письме, и решается это при сборке.

## ⚠⚠ Почему жребий переехал сюда из написания шаблона

Персональный вопрос (`question_to_companion`) — единственная живая переменная
рассылки: её значение считает сосед **отдельным вызовом LLM на каждого
адресата**. Замер на проде, 48 писем одним заходом:

| Шаблон | Писем | Секунд на письмо |
|--------|-------|------------------|
| без метки вопроса | 34 | **0** |
| с меткой вопроса | 14 | **11** |

Сперва долю бросали при написании шаблона: выпало — шаблон получал метку, и
дальше **каждое** письмо по нему платило; не выпало — не платило ни одно. Доля
означала «в скольких шаблонах», а человек читал её как «в скольких письмах» — и
читал правильно: настройка про деньги, а деньги тратятся на письмо.

Теперь шаблон несёт метку всегда, а решение принимается здесь, по каждому письму:
выпало — спрашиваем агента; не выпало — **вырезаем абзац с меткой** и не платим
ничего.

⚠ **Наружу сюита не ходит** (`testing.md`): агент подменяется заглушкой, и она же
считает, сколько раз её позвали.
"""

import pytest

from src.dating import mailing
from src.dating.mail import mail_mark_drop

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

TEMPLATE = {'id': 1, 'body': 'Hi [[companion_name]],\n\n'
                             'Сегодня был длинный день.\n\n'
                             '[[question_to_companion]]'}

VARS = [
    {'name': 'companion_name', 'source': 'companion', 'title': 'имя', 'uses': {}},
    {'name': 'question_to_companion', 'source': 'live', 'title': 'вопрос', 'uses': {}},
]

GIRL = {'id': 1, 'site_id': STAND_SITE, 'login': 'g@example.com', 'profile': '{}'}
PERSON = {'uid': 'u1', 'name': 'Brian Ritter', 'photo': '', 'age': 40}


class TestMarkDrop:
    """Вырезание абзаца с невыпавшей переменной."""

    def test_the_whole_paragraph_goes(self):
        """⚠ Метка стоит **отдельным абзацем** — так требует промпт: её значение
        это целая фраза. Оставь мы пустую строку — письмо выдало бы заготовку."""
        got = mail_mark_drop(TEMPLATE['body'], 'question_to_companion')

        assert got == 'Hi [[companion_name]],\n\nСегодня был длинный день.'

    def test_an_inline_mark_loses_only_itself(self):
        """⚠ Встроенную в фразу метку вырезаем саму: абзац целиком унёс бы полфразы.

        Такое ловит проверка у соседа, но старые шаблоны её не проходили.
        """
        got = mail_mark_drop('Скажи мне вот что: [[question_to_companion]]!', 'question_to_companion')

        assert got == 'Скажи мне вот что: !'

    def test_an_unknown_name_changes_nothing(self):
        assert mail_mark_drop(TEMPLATE['body'], 'нет_такой') == TEMPLATE['body']


class TestTheDiceAreThrownPerLetter:
    """Сколько писем — столько бросков, и столько же вызовов у выпавших."""

    async def test_zero_percent_never_asks(self, monkeypatch, chance):
        asked = _watch(monkeypatch)
        await chance(0)

        body, why = await mailing._body(TEMPLATE, GIRL, PERSON, VARS,
                                        {}, STAND_SITE)

        assert asked == [], 'агента спросили при нулевой доле'
        assert why == '', why
        assert '[[' not in body, body
        assert 'Сегодня был длинный день.' in body

    async def test_hundred_percent_always_asks(self, monkeypatch, chance):
        asked = _watch(monkeypatch)
        await chance(100)

        body, why = await mailing._body(TEMPLATE, GIRL, PERSON, VARS,
                                        {}, STAND_SITE)

        assert len(asked) == 1, asked
        assert why == '', why
        assert 'Чем занимаешься вечерами?' in body

    async def test_the_card_is_not_fetched_when_nothing_won(self, monkeypatch, chance):
        """⚠⚠ Не выпало — **платного не остаётся вовсе**: ни вызова агента, ни
        похода на сайт за карточкой собеседника.

        Карточка дороже, чем кажется: за описанием ходят на сайт, и поход этот
        идёт на человека, а не на заход.
        """
        _watch(monkeypatch)
        went = []
        monkeypatch.setattr(mailing, '_card',
                            lambda *a, **k: went.append(1) or {})
        await chance(0)

        await mailing._body(TEMPLATE, GIRL, PERSON, VARS, {}, STAND_SITE)

        assert went == [], 'за карточкой сходили, хотя спрашивать нечего'

    async def test_a_template_without_the_mark_costs_nothing(self, monkeypatch, chance):
        """Шаблон без живой метки не платит и при ста процентах: платит письмо,
        а не настройка."""
        asked = _watch(monkeypatch)
        await chance(100)

        plain = {'id': 2, 'body': 'Hi [[companion_name]], как ты?'}
        body, why = await mailing._body(plain, GIRL, PERSON, VARS, {}, STAND_SITE)

        assert asked == [] and why == '', (asked, why)
        # ⚠ Подставляется **первое слово** — «Brian», а не «Brian Ritter»:
        # фамилия в обращении звучит как из документа (`mailing._from_person`).
        assert body == 'Hi Brian, как ты?'


@pytest.fixture
async def chance():
    """Ставит долю у сайта стенда и убирает за собой."""
    from mysql_.mysql_ import mysql_get_db_async

    async def _set(value):
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE site SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),'
                ' %s, %s) WHERE site_id = %s',
                ('$.mailing_question_percent', str(value), STAND_SITE))
            await db.connection.commit()

    yield _set

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET settings = JSON_REMOVE(settings, %s)'
                         ' WHERE site_id = %s',
                         ('$.mailing_question_percent', STAND_SITE))
        await db.connection.commit()


def _watch(monkeypatch) -> list:
    """Подменяет агента вопроса и копит, сколько раз его позвали."""
    asked = []

    async def _ask(girl, site_id, card):
        asked.append(site_id)

        return 'Чем занимаешься вечерами?'

    monkeypatch.setattr(mailing, 'mail_question', _ask)
    monkeypatch.setattr(mailing, '_card', _nothing({}))

    return asked


def _nothing(value):
    async def _got(*args, **kwargs):
        return value

    return _got
