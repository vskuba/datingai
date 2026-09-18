"""Пул инвайтов набирается от одной девушки за заход, и она меняется по кругу.

## ⚠⚠ Зачем

Шаблон один на заход (`invite._template`), а девушек в сети бывает дюжина. Прежде
они шли по очереди внутри одного захода — и один и тот же текст уходил от разных
имён в одну минуту. Со стороны сайта это читается как агентство вернее всего
остального: разные девушки, одна реплика, одно время.

Теперь на заход выбирается одна анкета, а разнообразие даёт то, что каждый заход
просит у агента новый шаблон.

⚠ **Очередь, а не голый случай.** Случай без памяти выдаёт одно имя два захода
подряд примерно раз в дюжину — а два захода подряд от одной девушки это ровно та
примета, которую мы убираем.
"""

import pytest

from src.dating import invite
from src.dating.invite import _girl_turn, _turn_read, _turn_write

pytestmark = pytest.mark.chat

# Стенд: круги воркера про него не знают.
STAND_SITE = 999_343

GIRLS = [{'id': 1}, {'id': 2}, {'id': 3}]


class _FakeRedis:
    """Redis настолько, насколько его читает очередь: `get` и `setex`.

    ⚠ Подделка, а не живой Redis, и не ради скорости: сюита идёт снаружи докера, а
    Redis живёт в контейнере под своим именем — снаружи его не видно. Тот же приём
    и та же причина, что у кругов (`test_mailing_round.py`).
    """

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, seconds, value):
        self.store[key] = value


@pytest.fixture(autouse=True)
def memory(monkeypatch):
    """Пустая память очереди на каждый тест.

    ⚠ Иначе тесты влияли бы друг на друга: первый проходит круг до конца, и
    следующий видит его законченным.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(invite, 'redis_conn_get', lambda: fake)

    return fake


class TestOneGirlPerPass:
    """Сколько анкет выходит из жребия."""

    async def test_exactly_one(self):
        got = await _girl_turn(STAND_SITE, GIRLS)

        assert len(got) == 1, got
        assert got[0] in GIRLS

    async def test_a_lonely_girl_is_always_taken(self):
        """⚠ Одна в сети — выбирать не из кого, и отдельной ветки для этого нет:
        круг из одной анкеты кончается на ней же и начинается заново."""
        one = [{'id': 7}]

        for _ in range(4):
            assert await _girl_turn(STAND_SITE, one) == one

    async def test_nobody_online_gives_nothing(self):
        assert await _girl_turn(STAND_SITE, []) == []


class TestTheTurnGoesRound:
    """⚠⚠ Побывавшая не выпадает, пока круг не закончен."""

    async def test_everyone_before_anyone_twice(self):
        """Три захода — три разные девушки."""
        seen = [(await _girl_turn(STAND_SITE, GIRLS))[0]['id'] for _ in range(3)]

        assert sorted(seen) == [1, 2, 3], seen

    async def test_the_circle_starts_over(self):
        """Четвёртый заход начинает новый круг, а не упирается в пустоту."""
        for _ in range(3):
            await _girl_turn(STAND_SITE, GIRLS)

        got = await _girl_turn(STAND_SITE, GIRLS)

        assert len(got) == 1, got
        assert _turn_read(STAND_SITE) == {int(got[0]['id'])}, \
            'память не очистилась — круг не начался заново'

    async def test_a_girl_who_left_does_not_block_the_circle(self):
        """⚠ Анкета ушла из сети посреди круга — остальные всё равно идут.

        Список приходит снаружи и состоит из тех, кто в сети **сейчас**; память же
        помнит номера. Пересечение может опустеть — тогда круг начинается заново, а
        не останавливается.
        """
        await _girl_turn(STAND_SITE, GIRLS)

        # В сети осталась одна, и она уже могла побывать.
        got = await _girl_turn(STAND_SITE, [GIRLS[0]])

        assert got == [GIRLS[0]], got


class TestMemory:
    """Что помнится между заходами."""

    async def test_the_chosen_one_is_remembered(self):
        got = await _girl_turn(STAND_SITE, GIRLS)

        assert int(got[0]['id']) in _turn_read(STAND_SITE)

    def test_an_unreadable_memory_means_a_fresh_circle(self, monkeypatch):
        """⚠ Сбой Redis не останавливает набор: пул важнее ровности очереди."""
        import src.dating.invite as invite

        def _boom():
            raise RuntimeError('redis лёг')

        monkeypatch.setattr(invite, 'redis_conn_get', _boom)

        assert _turn_read(STAND_SITE) == set()


class TestTheButtonWins:
    """⚠ Выбор человека кнопкой жребием не перебивается."""

    def test_a_named_profile_skips_the_turn(self):
        """Человек назвал анкету — жребий поверх его выбора был бы спором с ним."""
        import inspect

        from src.dating.invite import invite_make

        code = inspect.getsource(invite_make)
        turn = code.index('_girl_turn(')
        named = code.index('if profile_id:')

        assert named < turn, 'жребий стоит раньше выбора анкеты'
        assert 'else:' in code[named:turn], \
            'жребий бросается и при выбранной анкете — выбор человека игнорируется'
