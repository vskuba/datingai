"""Два действия над пулом — одной дверью на все пять.

Пулов на «Профилях» четыре (инвайты, рассылка, письма избранным, инициатива в
чате) и пятый рядом — ответы на письма. Устроены они по-разному и намеренно:
загрузка и показ у каждого свои. А вот «убрать карточку» и «очистить пул» —
одно дело, и пока оно лежало копиями, копии разъехались.

## ⚠⚠ Чем разъехались

**«Её уже нет» показывалось как поломка.** Пул живёт своей жизнью: карточку
уносит отправка или чистка пулов, когда у анкеты кончается смена. Человек жмёт
«×» по тому, чего уже нет, — и его цель достигнута, карточки в пуле нет. Это
знал **один** обработчик из пяти; четверо показывали красную ошибку про
несуществующий номер.

**Необратимое делалось молча.** Очистка пула восстановлению не подлежит:
черновики сочиняются заново и стоят вызовов модели. Спрашивал **один** пул из
трёх.

Правила живут в `js/dating/pool.js`, и эти сторожа стерегут их там же.
"""
import pathlib

import pytest

pytestmark = pytest.mark.page

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = ROOT / 'data/uvicorn/public/js/dating/pool.js'
PAGE = ROOT / 'data/uvicorn/template/admin/dating/profile.html'


def _module() -> str:
    return MODULE.read_text(encoding='utf-8')


def _page() -> str:
    return PAGE.read_text(encoding='utf-8')


class TestEveryPoolGoesThroughTheSameDoor:
    """⚠ Своя рука мимо общей двери — это и есть то, от чего уходили."""

    def test_no_pool_deletes_on_its_own(self):
        """Ни один пул не зовёт `deleteData` своим адресом.

        ⚠ Примета — **адрес ручки пула**, а не слово `deleteData`: оно законно
        живёт на странице у других предметов (шаблоны писем, переменные,
        блокировки), и запрещать его целиком значило бы стеречь не то.
        """
        page = _page()
        for path in ('invite/', 'favorite-mail/', 'mailing/',
                     'chat-initiative/', 'mail-in/reply/'):
            bad = f'deleteData(`${{BASE}}/api/dating/{path}'
            assert bad not in page, \
                f'пул «{path}» снова удаляет своей рукой, мимо `poolDrop`'

    def test_no_pool_clears_on_its_own(self):
        """То же для очистки: адрес с `site_id` идёт только через `poolClear`."""
        page = _page()
        for path in ('invite?', 'favorite-mail?', 'mailing?'):
            bad = f'deleteData(\n                `${{BASE}}/api/dating/{path}'
            assert bad not in page, \
                f'пул «{path}» снова очищается своей рукой, мимо `poolClear`'


class TestAVanishedCardIsNotAFailure:
    """⚠⚠ Цель человека достигнута — карточки в пуле нет."""

    def test_the_rule_lives_in_the_module(self):
        mod = _module()

        assert 'e.status === 404' in mod, \
            'исчезнувшая карточка снова показывается красной ошибкой'
        assert 'POOL_DROP_GONE' in mod, \
            'исход «её уже не было» неотличим от удачи — вызывающий не узнает'

    def test_it_is_the_code_not_the_words(self):
        """⚠ Узнаём по **коду ответа**, а не по тексту.

        Формулировку в роутере однажды поправят, и примета по словам замолчит.
        Текст оставлен запасным — вкладка могла не перечитать `api.js`, — но
        первым стоит код.
        """
        mod = _module()
        at_code = mod.index('e.status === 404')
        at_text = mod.index('/#\\d+ нет')

        assert at_code < at_text, 'текст встал раньше кода — примета хрупкая'


class TestTheIrreversibleIsAsked:
    """⚠⚠ Очистка пула необратима: восстановить нечего, сочиняют заново."""

    @pytest.mark.parametrize('name', ('inviteClear', 'favMailClear',
                                      'mailPoolClear'))
    def test_every_clear_asks(self, name):
        """У каждой очистки есть вопрос — вторым доводом `poolClear`.

        ⚠ Вопрос **свой у каждого пула**: в нём число и склонение («убрать 3
        реплики», «убрать 1 письмо»). Общий текст пришлось бы писать языком
        схемы — «убрать 3 строки», — и доверия он не прибавляет.
        """
        page = _page()
        block = page[page.index(f'async function {name}('):]
        block = block[:block.index('\n    }')]

        assert 'poolClear(' in block, f'{name} очищает мимо общей двери'
        assert 'Убрать из пула' in block, \
            f'{name} очищает пул без вопроса — а восстановить нечего'

    def test_the_module_can_skip_the_question(self):
        """⚠ Пустой `ask` не спрашивает, и это нужно: удаление одной карточки
        из ленты спрашивать незачем — там цена промаха одна строка, которую
        круг сочинит заново."""
        mod = _module()

        assert "ask = ''" in mod, 'вопрос стал обязательным'
        assert 'if (ask && !confirm(ask))' in mod, \
            'пустой вопрос всё равно спрашивает'
