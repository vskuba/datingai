"""Смена держателя: кто её ставит и что бывает, когда ставить некому.

⚠ **Проверяется постановка, а не сама смена.** Смена — это шесть часов живых
сокетов; здесь важно другое: не остаётся ли установка вовсе без держателя. Такое
уже случалось, и молча.

Redis и очередь подменены: `rq` живёт в своём контейнере, а сюита идёт с хоста.
Подделываются они на уровне модуля — то есть под проверкой остаётся всё, ради чего
модуль написан: замок, его срок и решение «ставить или нет».
"""

import asyncio

import pytest

from shift_ import shift_
from src.worker.dating import chat_worker

pytestmark = pytest.mark.chat
class _FakeRedis:
    """Redis настолько, насколько его читает смена: ключ с `nx` и его срок.

    ⚠ `nx` подделывается по-настоящему: на нём держится single-flight, и заглушка,
    отвечающая успехом всегда, разрешала бы двух держателей на те же сокеты.
    """

    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None

        self.store[key] = value

        return True

    def delete(self, key):
        self.store.pop(key, None)

    def exists(self, key):
        return 1 if key in self.store else 0


class _FakeQueue:
    """Очередь `rq`: считает поставленные задания."""

    def __init__(self):
        self.jobs: list = []

    def enqueue(self, func, **kwargs):
        self.jobs.append(func)

        return type('Job', (), {'id': f'job-{len(self.jobs):04d}-fake'})()


@pytest.fixture
def worker(monkeypatch):
    """Поддельные Redis и очередь; сторож — быстрый.

    ⚠ Сроки сторожа ужимаются намеренно: живой ждёт до двухсот секунд, и тест,
    честно отсидевший их, никто не станет прогонять.
    """
    redis = _FakeRedis()
    queue = _FakeQueue()

    # ⚠ Подменяем **в слое**: механика смены уехала в `shift_.shift_`, и
    # подмена по месту прежнего жительства не действовала бы вовсе — тест
    # зеленел бы, молча ходя в живой Redis. Правило общее: патчить там, где
    # имя читают, а не там, где оно когда-то лежало.
    monkeypatch.setattr(shift_, 'redis_conn_get', lambda: redis)
    monkeypatch.setattr(shift_, 'redis_queue_get', lambda name: queue)

    # ⚠ А шаг сторожа — у нас: он довод проекта, и слой получает его вызовом.
    # Патч действует, потому что число читается в момент `chat_worker_recover`.
    monkeypatch.setattr(chat_worker, 'CHAT_WORKER_KEEP_STEP', 0.01)

    # ⚠ Сторож от прошлого теста не должен доживать до этого: он бесконечный, и
    # задача-долгожитель ставила бы смену в чужую очередь — разбираться, чья она,
    # стало бы негде.
    if chat_worker._keep_task and not chat_worker._keep_task.done():
        chat_worker._keep_task.cancel()

    monkeypatch.setattr(chat_worker, '_keep_task', None)

    return type('Worker', (), {'redis': redis, 'queue': queue})()


async def _settle(worker, tries: int = 200) -> None:
    """Даёт сторожу доработать: ждём смену либо его сдачу.

    ⚠ Ждём **очередь**, а не задачу сторожа. Проверять `_recover_task` значило бы
    проверять устройство исправления: не будь его вовсе, тест падал бы с «None
    нельзя await» — и молчал бы о том, что установка осталась без держателя.
    """
    for _ in range(tries):
        if worker.queue.jobs:
            return

        await asyncio.sleep(0.01)


class TestRecoverOnStart:
    """Постановка смены при старте uvicorn."""

    async def test_free_key_starts_the_shift(self, worker):
        """Смены нет — ставим сразу, без всякого ожидания."""
        await chat_worker.chat_worker_recover()

        assert len(worker.queue.jobs) == 1

    async def test_living_shift_is_not_doubled(self, worker):
        """⚠ Идущую смену трогать нельзя: два держателя одной учётки — это два
        соединения, и сайт вышибает оба.

        Ключ здесь продлевается сам собой (заглушка его не гасит), то есть ведёт
        себя как живая смена.
        """
        worker.redis.store[chat_worker._FLIGHT_KEY] = '1'

        await chat_worker.chat_worker_recover()
        await _settle(worker, tries=40)

        assert worker.queue.jobs == [], 'поставлена вторая смена поверх живой'


class TestAbandonedShift:
    """⚠⚠ Настоящий отказ: час без держателя, и панель об этом не знает.

    Смена ставит себе следующую сама — но лишь дожив до конца работы. Перезапуск
    контейнера воркера обрывает её на полуслове: `finally` не выполняется, ключ
    гаснет по сроку, и поставить новую становится некому. Прежде uvicorn пробовал
    ровно один раз, при собственном старте, и к моменту беды был занят другим.

    Итог: ни держателя сокетов, ни круга расписания, ни рассылки, ни ленты. В
    журнале `rq` при этом только `AbandonedJobError`, а панель выглядит рабочей.
    """

    async def test_expired_key_gets_a_new_shift(self, worker):
        """Ключ погас — сторож ставит смену. ⚠ Суть исправления.

        Здесь ключ занят уже **на момент старта**: uvicorn поднялся в те самые
        полторы минуты, пока догорает ключ прерванной смены. Раньше он в этом
        случае не ставил ничего и уходил навсегда.
        """
        worker.redis.store[chat_worker._FLIGHT_KEY] = '1'

        await chat_worker.chat_worker_recover()

        # Прерванная смена ключ не продлевает — он гаснет по сроку. Здесь это и
        # изображается: ключ убирается, пока сторож ждёт.
        await asyncio.sleep(0.02)
        worker.redis.delete(chat_worker._FLIGHT_KEY)

        await _settle(worker)

        assert len(worker.queue.jobs) == 1, 'смена не поднялась после погасшего ключа'

    async def test_start_is_not_held_by_the_watch(self, worker, monkeypatch):
        """⚠ Сторож не задерживает старт сервера.

        Иначе лечение оказалось бы хуже болезни: панель не поднималась бы, пока
        сторож ходит своими кругами.
        """
        worker.redis.store[chat_worker._FLIGHT_KEY] = '1'

        # Срок здесь **живой**, а не ужатый: проверяется именно то, что ожидание
        # ушло в фон, а не то, что оно короткое. ⚠ Через `monkeypatch`, а не
        # присваиванием: прямая правка модуля пережила бы тест и ужала бы сроки
        # соседним — или, наоборот, растянула их до настоящих.
        monkeypatch.setattr(chat_worker, 'CHAT_WORKER_KEEP_STEP', 5)

        started = asyncio.get_event_loop().time()
        await chat_worker.chat_worker_recover()
        spent = asyncio.get_event_loop().time() - started

        await chat_worker.chat_worker_keep_stop()

        assert spent < 1, f'старт задержан на {spent:.1f} с'

    async def test_living_shift_keeps_the_watch_quiet(self, worker):
        """Ключ жив — сторож молчит, сколько бы ни ходил.

        Живая смена это норма, а не событие: сторож заглядывает дважды в минуту, и
        строка на каждый заход утопила бы журнал вместе с той единственной, ради
        которой он заведён.
        """
        worker.redis.store[chat_worker._FLIGHT_KEY] = '1'

        await chat_worker.chat_worker_recover()
        await _settle(worker, tries=40)

        assert worker.queue.jobs == [], 'смена поставлена поверх живой'

    async def test_worker_restarted_alone_gets_the_shift_back(self, worker):
        """⚠⚠ Перезапустили **один воркер**, спустя время после старта uvicorn.

        Тот самый случай, что вскрылся живьём и стоил часа простоя, — и тот
        самый, который разовый сторож при старте не чинит. Смена к этому моменту
        давно идёт: uvicorn своё дело сделал и, будь сторож разовым, ушёл бы
        навсегда.

        Здесь она обрывается **позже** старта, и вернуть её обязан всё тот же
        сторож.
        """
        await chat_worker.chat_worker_recover()

        assert len(worker.queue.jobs) == 1, 'смена не поднялась при старте'

        # Смена идёт: ключ занят и продлевается. Сторож ходит вхолостую.
        await asyncio.sleep(0.05)

        assert len(worker.queue.jobs) == 1, 'сторож поставил вторую смену поверх живой'

        # А теперь контейнер воркера перезапустили: смена оборвана на полуслове,
        # `finally` не выполнился, ключ гаснет по сроку.
        worker.redis.delete(chat_worker._FLIGHT_KEY)

        for _ in range(200):
            if len(worker.queue.jobs) > 1:
                break

            await asyncio.sleep(0.01)

        assert len(worker.queue.jobs) == 2, 'смена не вернулась после перезапуска воркера'


class TestRedisFailure:
    """⚠ Недоступный Redis не должен мешать старту сервера: чат — не его условие."""

    async def test_start_survives_broken_redis(self, monkeypatch):
        class _Broken:
            def set(self, *args, **kwargs):
                raise RuntimeError('redis лёг')

            def exists(self, key):
                raise RuntimeError('redis лёг')

            def delete(self, key):
                raise RuntimeError('redis лёг')

        monkeypatch.setattr(shift_, 'redis_conn_get', lambda: _Broken())

        await chat_worker.chat_worker_recover()

    async def test_broken_queue_frees_the_key(self, worker, monkeypatch):
        """⚠ Замок занят, а задания нет — чат заперся бы до истечения ключа.

        Постановка обязана вернуть ключ, если очередь отказала: иначе одна
        временная беда с `rq` стоила бы полутора минут без держателя.
        """
        class _Broken:
            def enqueue(self, *args, **kwargs):
                raise RuntimeError('очередь недоступна')

        monkeypatch.setattr(shift_, 'redis_queue_get', lambda name: _Broken())

        with pytest.raises(RuntimeError):
            chat_worker.chat_worker_enqueue()

        assert chat_worker._FLIGHT_KEY not in worker.redis.store, \
            'ключ остался занят после сорванной постановки'
