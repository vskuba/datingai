"""Отправка в чат Luxee: её реплика новее повода отменяет наш ответ — но не наша.

⚠⚠ Сервер возвращает наш быстрый ответ в сокете **от лица девушки**, и
проверка «девушка ответила сама» обязана его узнать. Узнавала по скелету
(«только буквы и цифры»), а от «:)» скелет пуст: Irina → Alma Hawker,
25.11.2026 — ответ агента отменялся каждые пять минут нашим же «:)».
"""

import pytest

from src.dating.site import site_socket_luxee as luxee

pytestmark = pytest.mark.chat

GIRL, MAN, SINCE = 101, '1333496', 1_700_000_000_000


def _opened(*bodies):
    """Диалог, каким его отдаёт `cl_c_messages`: реплики её стороны после повода."""
    return {'message': [{'author': {'uid': GIRL}, 'body': body,
                         'created': SINCE + 5000 + i}
                        for i, body in enumerate(bodies)]}


@pytest.fixture
def sock_says(monkeypatch):
    """Подмена запроса к сокету: отвечает заданным диалогом, отправку не ждёт."""
    def install(answer):
        async def fake_ask(sock, kind, data):
            return answer
        monkeypatch.setattr(luxee, 'site_socket_luxee_ask', fake_ask)
    return install


async def _send(ours):
    """Зовём до отправки: дальше идёт живой сокет, он здесь не нужен."""
    try:
        return await luxee.site_socket_luxee_send(
            None, GIRL, 'id-1', 'ответ агента', man=MAN, since_ms=SINCE,
            ours_texts=ours)
    except Exception:
        # Прошла проверку и пошла слать в поддельный сокет — значит не отменена.
        return {'skipped': False}


class TestOurQuickLineIsNotHers:

    async def test_signs_only_quick_line_does_not_cancel(self, sock_says):
        """⚠⚠⚠ Наш «:)» — не её ответ."""
        sock_says(_opened(':)'))

        got = await _send([':)'])

        assert not got.get('skipped'), f'наш же «:)» отменил ответ: {got}'

    async def test_word_quick_line_does_not_cancel(self, sock_says):
        """Прежняя защита цела: «hi» узнаётся скелетом."""
        sock_says(_opened('Hi!'))

        got = await _send(['hi'])

        assert not got.get('skipped')

    async def test_her_own_signs_line_still_cancels(self, sock_says):
        """⚠ Обратная сторона: её «?» при нашем «:)» — её ответ."""
        sock_says(_opened('?'))

        got = await _send([':)'])

        assert got.get('skipped'), 'живой ответ девушки не остановил агента'


class TestTheKey:

    def test_signs_differ(self):
        assert luxee._twin_key('!!!') != luxee._twin_key('???')

    def test_blank_is_nothing(self):
        assert luxee._twin_key('  ') == ''

    def test_signs_trimmed(self):
        assert luxee._twin_key(' :) ') == luxee._twin_key(':)')
