"""Страница «Сокеты»: кто держит соединение с чатом сайта.

Отвечает на вопрос «почему анкета молчит», который по базе не разобрать: адреса
сокета нет вовсе, адрес есть но соединение рвётся, соединение живо но сообщений
нет. В `dating_profile` все три выглядят одинаково.

⚠ Redis подменён: проверяется сведение двух источников, а не работа держателя.
"""

import json
import time

import pytest

from src.dating import dating_socket
from src.dating.dating_socket import (DATING_SOCKET_TTL_SEC, dating_socket_read,
                                      dating_socket_write)


class _FakeRedis:
    """Redis настолько, насколько его читает и пишет снимок."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.broken = False

    def get(self, key):
        if self.broken:
            raise RuntimeError('redis лёг')

        return self.store.get(key)

    def setex(self, key, seconds, value):
        if self.broken:
            raise RuntimeError('redis лёг')

        self.store[key] = value
        self.ttl[key] = int(seconds)


class _Task:
    """Задача держателя: важно только, кончилась она или нет."""

    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


@pytest.fixture
def redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(dating_socket, 'redis_conn_get', lambda: fake)

    return fake


class TestSnapshot:
    """Что кладёт воркер и что читает панель."""

    def test_write_then_read(self, redis):
        dating_socket_write(
            {849: {'ws_url': 'wss://site/ws?token=abcdef123456', 'login': 'diana@x',
                   'task': _Task()}},
            {849: 7}, shift_left=1200)

        snapshot = dating_socket_read()

        assert snapshot['alive'] is True
        assert snapshot['holders'][0]['profile_id'] == 849
        assert snapshot['holders'][0]['login'] == 'diana@x'
        assert snapshot['holders'][0]['messages'] == 7
        assert snapshot['holders'][0]['alive'] is True

    def test_token_is_cut_from_the_url(self, redis):
        """⚠ **Токен сессии наружу не отдаём.**

        По нему можно подключиться к чату анкеты со стороны, а снимок уезжает на
        страницу и оседает в журналах запросов. Хвоста хватает, чтобы отличить
        один адрес от другого при переподключении — ровно за этим на него и
        смотрят.
        """
        dating_socket_write(
            {1: {'ws_url': 'wss://site/ws?token=СЕКРЕТ-ЦЕЛИКОМ', 'login': 'x',
                 'task': _Task()}},
            {}, shift_left=0)

        url = dating_socket_read()['holders'][0]['ws_url']

        assert 'СЕКРЕТ' not in url and 'token=' not in url
        assert url.startswith('wss://site/ws')

    def test_finished_task_is_not_alive(self, redis):
        """Задача кончилась — соединения нет, хотя держатель в списке ещё есть."""
        dating_socket_write({1: {'ws_url': 'wss://x', 'login': 'x', 'task': _Task(done=True)}},
                            {}, shift_left=0)

        assert dating_socket_read()['holders'][0]['alive'] is False

    def test_ttl_outlives_one_round(self, redis):
        """⚠ Снимок живёт дольше круга смены.

        Одного пропущенного круга (сеть моргнула, база задумалась) мало, чтобы
        объявлять держатель мёртвым; двух уже достаточно — значит смена встала.
        """
        from src.dating.dating_chat_hold import DATING_CHAT_HOLD_RESCAN_SEC

        dating_socket_write({}, {}, shift_left=0)

        assert DATING_SOCKET_TTL_SEC > DATING_CHAT_HOLD_RESCAN_SEC * 2 - 1


class TestNoSnapshotIsNotEmpty:
    """⚠ «Воркер молчит» и «сокетов нет» — разные ответы."""

    def test_missing_snapshot_says_not_alive(self, redis):
        """Разница важна: в первом случае разбираться надо со сменой, во втором с
        анкетами. Слей их — и страница показывала бы «сокетов нет» при мёртвом
        воркере, отправляя человека искать не там.
        """
        snapshot = dating_socket_read()

        assert snapshot['alive'] is False and snapshot['holders'] == []

    def test_broken_redis_says_not_alive(self, redis):
        redis.broken = True

        assert dating_socket_read()['alive'] is False

    def test_junk_says_not_alive(self, redis):
        redis.store[dating_socket.DATING_SOCKET_KEY] = 'не json'

        assert dating_socket_read()['alive'] is False

    def test_write_does_not_raise_on_broken_redis(self, redis):
        """⚠ Сбой записи не должен ронять круг: страница переживёт пустой список,
        анкеты без держателя — нет."""
        redis.broken = True

        assert dating_socket_write({}, {}, shift_left=0) is None


class TestHandler:
    """Ручка сводит снимок с анкетами из базы."""

    async def test_profiles_without_url_are_shown(self, redis, rhapsody):
        """⚠ Анкета без адреса сокета — не ошибка, и прятать её нельзя.

        У сайта без чат-сокета (Ladadate) адреса нет ни у одной анкеты: там пишут
        сценарием. Страница отвечает на вопрос «почему анкета молчит», и «адрес не
        снят» — самый частый ответ; спрячь такие строки, и она отвечала бы только
        на половину вопроса.
        """
        from src.uvicorn.routers.dating.dating_socket import dating_socket_list

        answer = (await dating_socket_list(site_id=343, user=None))['result']

        assert answer['sockets'], 'анкеты без адреса пропали из списка'
        assert all('has_url' in s for s in answer['sockets'])

    async def test_counts_are_over_ready_not_all(self, redis, rhapsody):
        """«Держится из готовых», а не «из всех»: анкета без адреса — не недостача."""
        from src.uvicorn.routers.dating.dating_socket import dating_socket_list

        answer = (await dating_socket_list(site_id=343, user=None))['result']

        assert answer['ready'] == sum(1 for s in answer['sockets'] if s['has_url'])
        assert answer['held'] <= answer['ready']

    async def test_alive_flag_reaches_the_page(self, redis, rhapsody):
        """Снимка нет — страница должна это знать, а не показывать пустой список."""
        from src.uvicorn.routers.dating.dating_socket import dating_socket_list

        answer = (await dating_socket_list(site_id=343, user=None))['result']
        assert answer['alive'] is False

        dating_socket_write({}, {}, shift_left=600)
        answer = (await dating_socket_list(site_id=343, user=None))['result']

        assert answer['alive'] is True

    async def test_closed_without_login(self, client):
        """Ручка под входом: в ней логины анкет и куски адресов сокета."""
        assert (await client.get('/api/dating/socket')).status_code == 401

    async def test_page_closed_without_login(self, client):
        answer = await client.get('/admin/dating/socket')

        assert answer.status_code in (401, 302, 307)
