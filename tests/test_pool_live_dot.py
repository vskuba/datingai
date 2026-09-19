"""Все три пула показывают, в сети ли адресат, и делают это одинаково.

## ⚠⚠ Зачем

Пулы стоят рядом, в одной полосе подвкладок: рассылка, инвайты, переписка с
избранными. Точку «в сети» рисовал только первый — и два соседних на один и тот же
вопрос не отвечали вовсе.

Дороже всего это было инвайтам: реплика в чат уходит **только человеку в сети**
(`invite_send_round`), и карточка, которую круг обходит стороной, ничем не
отличалась от застрявшей.

⚠ **Разметка одна на всех — `liveDot`.** Своя копия в каждом пуле разошлась бы
цветом, подписью или правилом про «не знаем» на первой же правке, и человек не знал
бы, какой карточке верить.

⚠⚠ **Проверяется и вызов, и область видимости.** `liveDot` объявлена ниже по файлу,
чем карточки инвайта и избранных: работает это на всплытии объявлений. Окажись
функция в чужом замыкании — пул падал бы `ReferenceError` и не рисовался целиком, а
не «просто остался без точки».
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.page

PAGE = (pathlib.Path(__file__).resolve().parents[1]
        / 'data/uvicorn/template/admin/dating/profile.html')


def _page() -> str:
    return PAGE.read_text(encoding='utf-8')


def _body(name: str) -> str:
    """Тело функции страницы — от объявления до закрывающей скобки её уровня."""
    page = _page()
    got = page[page.index(f'function {name}('):]

    return got[:got.index('\n    }')]


class TestEveryPoolShowsIt:
    """Три пула — три вызова одной и той же `liveDot`."""

    @pytest.mark.parametrize('card', ['inviteCard', 'favMailCard'])
    def test_the_card_calls_it(self, card):
        assert 'liveDot(one)' in _body(card), f'{card} потерял точку «в сети»'

    def test_the_mailing_card_still_has_it(self):
        """Рассылка — та, с которой правило и пошло."""
        assert 'liveDot(row)' in _page()

    def test_there_is_only_one_dot_maker(self):
        """⚠ Копий разметки нет: объявление ровно одно на весь файл."""
        assert len(re.findall(r'\bfunction liveDot\b', _page())) == 1


class TestTheDotTellsThreeThings:
    """⚠⚠ «Не знаем» — не «не в сети»."""

    def test_unknown_draws_nothing(self):
        """Серая точка на устаревшем снимке утверждала бы непроверенное."""
        assert 'if (!row.online_known) return \'\';' in _body('liveDot')

    def test_offline_is_drawn_grey(self):
        body = _body('liveDot')

        assert "row.online ? '' : ' off'" in body
        assert 'не в сети' in body


class TestItIsInScope:
    """⚠⚠ Функция объявлена ниже карточек — держит это всплытие."""

    def test_the_makers_come_before_it(self):
        """Если бы порядок вдруг стал важен, тест бы об этом сказал."""
        page = _page()

        assert page.index('function inviteCard(') < page.index('function liveDot(')
        assert page.index('function favMailCard(') < page.index('function liveDot(')

    def test_all_four_live_at_module_level(self):
        """Всплытие работает только в одной области видимости.

        ⚠ Отступ здесь — признак вложенности: верх модуля в этом файле это четыре
        пробела (там же `import`, а он легален лишь на верхнем уровне). Уйди любая
        из функций в чужое замыкание — отступ вырос бы, а пул упал бы
        `ReferenceError` и не отрисовался целиком.
        """
        page = _page()

        for name in ('liveDot', 'inviteCard', 'favMailCard'):
            место = page.index(f'function {name}(')
            строка = page[page.rindex('\n', 0, место) + 1:место]

            assert строка == '    ', f'{name} уехала в чужую область видимости'
