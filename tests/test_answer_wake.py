"""Импорт принёс свежую реплику — круг ответов зовётся сразу, не ждя тика.

## ⚠⚠ Зачем

Замер прода 09.11.2026, пара Vironika → Lance:

| Момент | Время | Δ |
|--------|-------|---|
| он дописал | 19:49:17 | — |
| импорт панели забрал | 19:49:38 | +22 с |
| **круг ответов проснулся** | 19:50:10 | **+31 с** |
| агент ответил | 19:50:32 | +22 с |
| попытка отправки | 19:50:32 | «нет открытого чата» |

Из 75 секунд работа заняла 22, остальное — ожидание тиков. И эти полминуты
решают: у Step2Love чат живёт, пока собеседник сидит в окне, и к 19:50 он уже
закрылся.

Полминуты брались ниоткуда: импорт кладёт реплику и уходит, а круг ответов
спит свою минуту. Теперь импорт его будит — но **только привезя свежее**
(`fresh`), иначе побудка стала бы вторым кругом с тиком импорта.

⚠ Будим по **сайту**, чей импорт сработал: анкеты прочих сайтов к этой реплике
отношения не имеют, а заход по ним стоит отбора и запросов.

⚠⚠ Заходы не накладываются: побудка и цикл берут один замок. Без него два
захода в одну секунду спросили бы агента дважды об одном и том же.
"""

import asyncio
import inspect

import pytest

from src.dating import chat_hold

pytestmark = pytest.mark.chat


class TestTheImportWakesTheAnswers:
    """Главное: связь есть, и она не даром."""

    def test_the_import_calls_the_wake(self):
        code = inspect.getsource(chat_hold._chat_import_round)

        assert '_answer_wake(' in code, \
            'импорт больше не будит круг ответов'

    def test_it_wakes_only_on_fresh(self):
        """⚠ Побудка — на **свежую** реплику, а не на любой заход.

        Импорт берёт сутки назад, и «привезено 5» почти всегда значит пять
        старых строк, которых у нас не было. Буди мы на них — круг ответов
        ходил бы с тиком импорта, то есть вчетверо чаще без всякого повода.
        """
        code = inspect.getsource(chat_hold._chat_import_round)
        at = code.index('_answer_wake(')
        before = code[:at]

        assert 'fresh' in before.rsplit('if', 1)[-1], \
            'побудка зовётся не по признаку свежести'

    def test_the_wake_takes_the_site(self):
        """⚠ По сайту, а не по всем анкетам разом."""
        got = inspect.signature(chat_hold._answer_wake)

        assert 'site_id' in got.parameters


class TestTheRoundsDoNotOverlap:
    """⚠⚠ Один замок на оба захода — цикл и побудку."""

    def test_both_paths_take_the_lock(self):
        wake = inspect.getsource(chat_hold._answer_wake)
        loop = inspect.getsource(chat_hold._answer_loop)

        assert '_answer_lock' in wake, 'побудка идёт мимо замка'
        assert '_answer_lock' in loop, 'цикл ответов идёт мимо замка'

    def test_the_lock_is_one(self):
        assert isinstance(chat_hold._answer_lock, asyncio.Lock), \
            'замок стал не замком — заходы снова могут наложиться'

    async def test_a_busy_round_does_not_start_a_second(self, monkeypatch):
        """⚠⚠ Пока заход идёт, второй не начинается, а **пропускается**.

        Именно пропускается, а не ждёт очереди: ждущая побудка выполнилась бы
        сразу после текущего захода и спросила бы агента об уже разобранном.
        """
        started = []

        async def _slow(profiles):
            started.append(1)
            await asyncio.sleep(0.2)

            return {'profiles': 0, 'sent': 0, 'silent': 0}

        async def _ready():
            return [{'profile': {'id': 1, 'site_id': 999_343}, 'presence': {}}]

        monkeypatch.setattr(chat_hold, 'auto_round', _slow)
        monkeypatch.setattr(chat_hold, '_profiles_ready', _ready)

        await asyncio.gather(chat_hold._answer_wake(999_343),
                             chat_hold._answer_wake(999_343))

        assert started == [1], f'заходов вышло {len(started)}, а должен один'
