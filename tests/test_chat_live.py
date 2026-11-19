"""Живое соединение с чатом Luxee: новое в чате забирается сразу, а не по тику.

Сокет подменён: сторож живого HTTP вебсокета не ловит. Форма событий — как
пришла с прода 19.11.2026 при пассивном прослушивании: счётчики `sm_mc_new`,
`server_c_update` с `message.channel.identity/index`, `s_m_update` на прочтение.
"""

import asyncio
import time

import pytest

from src.dating import chat_hold, chat_live
from src.dating.site.site_socket_luxee import site_socket_luxee_listen

pytestmark = pytest.mark.chat


class TestWhatCountsAsNew:
    async def test_signals_and_noise(self):
        calls = []

        async def on_change():
            calls.append(1)

        sock = _Sock([
            _ev('sm_mc_new', {'message': {}}),
            _ev('s_m_update', {'message': {'sid': 'x'}}),
            _ev('server_c_update', {'message': {'channel': {'identity': 'a_1', 'index': 4}}}),
            _ev('server_c_update', {'message': {'channel': {'identity': 'a_1', 'index': 4}}}),
            _ev('server_c_update', {'message': {'channel': {'identity': 'a_1', 'index': 5}}}),
            _ev('server_m_new', {'message': {'message': {'sid': 'y'}}}),
        ])

        await site_socket_luxee_listen(sock, on_change, time.monotonic() + 0.5)

        assert len(calls) == 2, ('сигналом должны быть рост номера диалога и новая '
                                 'реплика, а не счётчики и не прочтение')


class TestTheLockKeepsTheSignal:
    async def test_signal_during_import_reruns_once(self, monkeypatch):
        runs = []
        gate = asyncio.Event()

        async def once(site_id):
            runs.append(site_id)
            if len(runs) == 1:
                await gate.wait()

        monkeypatch.setattr(chat_hold, '_chat_import_site_once', once)
        monkeypatch.setattr(chat_hold, '_chat_locks', {})
        monkeypatch.setattr(chat_hold, '_chat_again', set())

        first = asyncio.create_task(chat_hold._chat_import_site(7))
        await asyncio.sleep(0)
        await chat_hold._chat_import_site(7)
        await chat_hold._chat_import_site(7)
        gate.set()
        await first

        assert runs == [7, 7], 'сигнал во время забора потерялся или задвоился'


class TestTheConnectionComesBack:
    async def test_reconnects_after_a_break_and_wakes(self, monkeypatch):
        woke, attempts = [], []

        class _Driver:
            def live(self):
                return True

            async def journal_live(self, site_id, on_change, stop_at):
                attempts.append(site_id)
                if len(attempts) == 1:
                    raise RuntimeError('сокет закрылся')
                await on_change()
                await asyncio.sleep(0.05)

        async def presence(site_id):
            return {}

        async def wake(site_id):
            woke.append(site_id)

        monkeypatch.setattr(chat_live, 'site_presence', presence)
        monkeypatch.setattr(chat_live, 'site_registry_import', lambda p: _Driver())
        monkeypatch.setattr(chat_live, 'CHAT_LIVE_BACKOFF_MIN', 0.01)

        await chat_live._site_run(999_344, time.monotonic() + 0.3, wake)

        assert len(attempts) >= 2, 'после обрыва соединение не переоткрылось'
        assert woke and set(woke) == {999_344}


class _Sock:
    """Сокет, отдающий события по списку, потом молчащий."""

    def __init__(self, events):
        self.events = list(events)

    async def next(self, timeout):
        if self.events:
            return self.events.pop(0)
        await asyncio.sleep(min(timeout, 0.05))
        raise asyncio.TimeoutError()


def _ev(kind, data):
    return 'message', [{'type': kind, 'data': data}]
