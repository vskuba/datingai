"""Карточка «ответ в пуле» называет, когда ответ написан и за сколько.

## ⚠⚠ Зачем

В окне разговора у черновика времени не было вовсе — и намеренно: со своим
`created_at` строка встала бы между репликами и читалась бы как отправленная.
Цена решения вылезла 08.11.2026 на Step2Love: Lance ответил Марине в 18:47:13,
черновик был готов в 18:47:53, а к этой секунде чат уже закрылся. С экрана
вопрос «за сколько мы готовили ответ» не отвечался никак — при том что ответ на
него и объясняет, почему реплика не ушла.

Теперь время едет своим полем (`made_at`, сторожит `test_chat_reply_draft.py`),
а страница пишет его словом «написан» и с промежутком от его реплики.

⚠ Разметка живёт в шаблоне страницы и в её ответе не видна — проверяется
исходник, тем же приёмом, что у строки молчания (`test_companion_link_one.py`).
"""

import pathlib

import pytest

pytestmark = pytest.mark.page

PAGE = (pathlib.Path(__file__).resolve().parents[1]
        / 'data/uvicorn/template/admin/dating/profile.html')


class TestTheDraftSaysWhenItWasWritten:
    """Главное: у карточки есть время и промежуток, и это не метка реплики."""

    def test_the_card_carries_the_time(self):
        body = _body('chatDraftWhen')

        assert 'm.made_at' in body, 'карточка взяла время не своё'
        assert 'написан' in body, \
            'время черновика подписано как у сказанной реплики'

    def test_the_gap_is_counted_from_his_reply(self):
        """⚠ Промежуток — между **его** репликой и черновиком."""
        body = _body('chatDraftWhen')

        assert 'stampMs(m.made_at) - stampMs(heSaidAt)' in body, \
            'промежуток считается не от его реплики'
        assert 'durationWords(' in body, \
            'длительность снова пишется своей лесенкой, а не общей'

    def test_without_his_reply_only_the_time_is_shown(self):
        """⚠ Нет его реплики в показанном куске — промежутка нет.

        «через 0 с» было бы не ответом, а выдумкой: реплика могла уехать за
        край страницы, и считать не от чего.
        """
        body = _body('chatDraftWhen')

        assert 'heSaidAt &&' in body, \
            'промежуток считается и без его реплики'

    def test_the_draft_keeps_no_reply_time(self):
        """⚠⚠ И прежнее решение цело: карточка стоит в конце и без `created_at`.

        Оно и есть то, что легко отменить этой правкой: поставь время реплики —
        строка уедет между сказанным и прочтётся как отправленная.
        """
        body = _body('chatDialogBody')

        assert 'chatDraftWhen(m, heSaidAt)' in body, \
            'время черновика в карточку не попадает'
        assert "rows.find(one => one.direction !== 'out')" in body, \
            'повод черновика ищется не первой входящей репликой'


def _body(name: str) -> str:
    """Тело функции страницы — от объявления до закрывающей скобки её уровня."""
    page = PAGE.read_text(encoding='utf-8')
    got = page[page.index(f'function {name}('):]

    return got[:got.index('\n    }')]
