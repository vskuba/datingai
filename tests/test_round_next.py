"""Счётчик «следующий заход через N»: отвечает срок отметки, а не `-1`.

## ⚠⚠ Зачем этот файл

Две такие функции молчали месяцами, и молчали **правдоподобно**: каждая
подставляла единицу в ключ сама — `KEY.format(site_id=…)` на шаблоне с меткой
`{id}`. `.format` бросал `KeyError`, его ловил `except Exception` тремя строками
ниже, писал в журнал и возвращал `-1` — «спросить не вышло».

Снято на проде 11.10.2026:

    favorite_mail_round_next(343) = -1
    invite_round_next(343)        = -1
    ttl ключа как есть            = 1767

То есть страница не показывала срок **нигде** — ни у приставания, ни у инвайтов,
— и выглядело это как «данных нет», а не как поломка.

⚠ Redis здесь **подменён**, а не живой: с хоста он недоступен вовсе
(`REDIS_URL` — имя сети compose, наружу порт не опубликован), а весь код глотает
его сбой молча. На живом Redis тест маскировал бы беду вместо того, чтобы её
ловить.

⚠⚠ Подделка вдобавок записывает **спрошенный ключ**, а он тут и есть сломанное
место: беда случалась до всякого похода в Redis.
"""

import pytest

from round_ import round_slot as slot

from src.dating.favorite_mail_round import (FAVORITE_MAIL_ROUND_KEY,
                                            favorite_mail_round_next)
from src.dating.invite_round import INVITE_ROUND_KEY, invite_round_next
from src.dating.reply_round import REPLY_ROUND_KEY, reply_round_next

pytestmark = pytest.mark.mail

# Номер сайта для проверок. Свой, которого нет ни у кого: ключ собирается из
# него, и чужой номер в проверке ключа ничего бы не доказал.
PROBE_SITE = 999_901

# Срок отметки, секунды.
PROBE_EVERY = 120


class _FakeRedis:
    """Подделка Redis: помнит, какой ключ у неё спросили, и отвечает сроком.

    ⚠⚠ **Записанный ключ — главное здесь, а не ответ.** Беда, ради которой
    написан файл, случалась **до** похода в Redis: ключ собирали `.format`-ом с
    чужой меткой, и `KeyError` глотал `except`. Живой Redis сказал бы только
    «ответ не `-1`»; подделка говорит, **какой именно ключ** спросили, то есть
    проверяет ровно сломанное место.
    """

    def __init__(self, ttl: int = PROBE_EVERY):
        self.asked = []
        self._ttl = int(ttl)

    def ttl(self, key):
        self.asked.append(str(key))

        return self._ttl


@pytest.fixture
def fake(monkeypatch):
    """Подменяет Redis **в модуле слоя**, где его и зовут.

    ⚠ Не в двери `src/dating/round.py`: она лишь ре-экспортирует имена, и
    подмена в ней переставила бы ссылку двери, оставив код слоя при своей —
    тест зеленел бы, ничего не проверяя. Та же оговорка стоит у соседнего
    `tests/test_mailing_round.py`.
    """
    conn = _FakeRedis()
    monkeypatch.setattr(slot, 'redis_conn_get', lambda: conn)

    return conn


class TestNextSeesTheMark:
    """Отметка стоит — счётчик называет её срок, а не «не вышло»."""

    def test_favorite_mail(self, fake):
        assert favorite_mail_round_next(PROBE_SITE) == PROBE_EVERY

    def test_invite(self, fake):
        assert invite_round_next(PROBE_SITE) == PROBE_EVERY

    def test_reply(self, fake):
        assert reply_round_next(PROBE_SITE) == PROBE_EVERY


class TestKeyIsBuiltByTheLayer:
    """⚠⚠ Ключ собирает слой, и метку в нём подставили правильно.

    Это и есть та проверка, которой не было. Прежде каждая функция подставляла
    единицу сама — `KEY.format(site_id=…)` на шаблоне с меткой `{id}`, — и
    падала `KeyError`-ом ещё до Redis. Снаружи это выглядело как «данных нет».
    """

    def test_favorite_mail_key(self, fake):
        favorite_mail_round_next(PROBE_SITE)

        assert fake.asked == [FAVORITE_MAIL_ROUND_KEY.replace('{id}',
                                                              str(PROBE_SITE))]
        assert '{' not in fake.asked[0], 'метка осталась в ключе текстом'

    def test_invite_key(self, fake):
        invite_round_next(PROBE_SITE)

        assert fake.asked == [INVITE_ROUND_KEY.replace('{id}', str(PROBE_SITE))]

    def test_reply_key(self, fake):
        reply_round_next(PROBE_SITE)

        assert fake.asked == [REPLY_ROUND_KEY.replace('{id}', str(PROBE_SITE))]


class TestNoMarkMeansNow:
    """Отметки нет — это «пора», то есть ноль, а не отказ.

    ⚠ `-2` от Redis значит «ключа нет». Прочти его страница как есть, она
    написала бы «не вышло» там, где круг возьмётся на ближайшем тике.
    """

    def test_minus_two_is_zero(self, monkeypatch):
        conn = _FakeRedis(ttl=-2)
        monkeypatch.setattr(slot, 'redis_conn_get', lambda: conn)

        assert favorite_mail_round_next(PROBE_SITE) == 0
        assert invite_round_next(PROBE_SITE) == 0
        assert reply_round_next(PROBE_SITE) == 0


class TestRedisFailure:
    """Redis молчит — `-1`, и врать сроком нельзя.

    ⚠ Сторона ошибки здесь своя, не такая, как у `round_slot_due`: тот при сбое
    отвечает «не пора» (лишний заход дороже пропущенного), а этот — «не знаю».
    Выдуманный срок на странице хуже честного молчания.
    """

    def test_broken_redis_is_minus_one(self, monkeypatch):
        def broken():
            raise RuntimeError('redis лёг')

        monkeypatch.setattr(slot, 'redis_conn_get', broken)

        assert favorite_mail_round_next(PROBE_SITE) == -1
        assert invite_round_next(PROBE_SITE) == -1
        assert reply_round_next(PROBE_SITE) == -1
