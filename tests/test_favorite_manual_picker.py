"""«Письмо руками»: в подборе адресатов закрытому от писем писать нельзя.

Лента «My favorites» над формой — список **сайта**, и про наши запреты (`block`)
он не знает вовсе. Живой случай 11 ноября: человек выбрал по этой ленте пятерых,
двое были закрыты от писем, письмо — всё или ничего — не легло **никому**, а в
отказе стояли два номера вместо двух имён.

## Что здесь стережётся

* ⚠⚠ **правило решает сервер**: страница спрашивает `/api/dating/favorite-mail/recipients`
  и показывает ответ; своей проверки на `block` или на состояние звезды здесь нет
  и не должно быть — вторая копия разошлась бы с первой на первой же правке;
* закрытому — красное кольцо вокруг лица и причина в подсказке;
* щелчок по такому человеку чипса не ставит, «выбрать всех» его пропускает, а
  уже выбранный до ответа сервера чипс снимается;
* лента перекрашивается часто, и отметка держится на уже нарисованных карточках.
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.page

PAGE = (pathlib.Path(__file__).resolve().parents[1]
        / 'data/uvicorn/template/admin/dating/profile.html')


def _func(name: str) -> str:
    """Тело функции страницы — из разметки, а не переписанное в тест."""
    page = PAGE.read_text(encoding='utf-8')
    found = re.search(r'\n    (?:async )?function ' + name
                      + r'\([^)]*\) \{.*?\n    \}', page, re.S)

    assert found, f'функция {name} не нашлась'

    return found.group(0)


class TestTheServerDecides:
    """Страница показывает решённое сервером, а не решает сама."""

    def test_the_page_asks_the_server(self):
        """⚠ Один поход на анкету: ответ тот же, каким отказывает `POST /manual`."""
        body = _func('favManualOffLoad')

        assert '/api/dating/favorite-mail/recipients' in body, \
            'подбор адресатов не спрашивает сервер — значит решает сам'
        assert 'favManualOffFor === profile' in body, \
            'каждая перекраска ленты ходила бы в базу за тем же списком'
        assert 'if (Number(openId) !== profile) return;' in body, \
            'ответ прежней анкеты перекрасил бы ленту новой'

    def test_the_page_invents_no_verdict(self):
        """⚠⚠ Отметка «нельзя» ставится **только** из ответа сервера.

        Своего признака на странице быть не должно: у нас `block` и состояние
        звезды, в ленте сайта ни того, ни другого.
        """
        page = PAGE.read_text(encoding='utf-8')
        made = [line.strip() for line in page.splitlines() if 'favManualOff = ' in line]

        assert made, 'отметки «нельзя» на странице нет вовсе'
        for line in made:
            assert 'new Map()' in line or 'rows' in line, \
                f'отметка выведена на странице, а не получена с сервера: {line}'

        for name in ('favManualOffPaint', 'favManualAdd'):
            assert 'закрылся' not in _func(name), \
                f'{name} сочиняет причину словами — их две записи у одного ответа'


class TestTheStripMarksThem:
    """Закрытый от писем виден в ленте, а не спрятан от неё."""

    def test_the_card_is_marked_and_explained(self):
        """Кольцо — классом, причина — словами сервера в подсказку."""
        body = _func('favManualOffPaint')

        assert "card.classList.add('fav-off')" in body
        assert "card.classList.remove('fav-off')" in body, \
            'с человека, которому снова можно, запрет не снят'
        assert 'card.title = `${card.dataset.base} — ${why}`' in body
        assert 'card.dataset.base === undefined' in body, \
            'подсказка перекрашивается не впервые: причина дописалась бы к причине'

    def test_the_ring_does_not_move_the_card(self):
        """⚠ Кольцо `box-shadow`-ом: `border` прибавил бы пиксели к карточке, и
        лента из двухсот лиц разъехалась бы."""
        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r'\.person-card\.fav-off \.person-face \{[^}]*\}', page, re.S)

        assert found, 'класса `.person-card.fav-off` в разметке нет'
        block = found.group(0)
        assert 'box-shadow' in block, 'кольцо рисуют не тенью'
        assert 'border' not in block, 'кольцо рамкой сдвигает карточку в ленте'
        assert '#' in block, 'кольцо не красное — его не отличить от обычного'

    def test_a_repainted_strip_keeps_the_mark(self):
        """⚠ Лента собирается заново при каждом приходе — отметка возвращается
        вместе с карточками, а не живёт на старых узлах."""
        body = _func('stripPaint')

        assert 'favManualOffPaint()' in body, \
            'после перекраски ленты запрет с карточек сорван'


class TestClickingDoesNotChoose:
    """Нажатие закрытому не делает его адресатом."""

    def test_the_click_is_refused(self):
        """⚠ Отказ **до** чипса: письмо всё или ничего, и один закрытый стоил бы
        письма остальным."""
        body = _func('favManualAdd')

        assert 'favManualOff.get(uid)' in body
        assert body.index('favManualOff.get(uid)') < body.index('favManualTo.push'), \
            'чипс ставится раньше, чем проверен запрет'

    def test_select_all_skips_them(self):
        """«Выбрать всех» считает по ленте, а в ленте они есть."""
        body = _func('favManualAll')

        assert '!favManualOff.has(one.uid)' in body, \
            'одна нажатая ссылка выбрала бы закрытых и оставила письмо всем'

    def test_a_choice_made_too_early_is_dropped(self):
        """⚠⚠ Кто успел получить чипс до ответа сервера, снимается сам: иначе под
        кнопкой лежала мина — отказ всем."""
        body = _func('favManualOffLoad')

        assert 'favManualTo.filter(one => !favManualOff.has(one.uid))' in body
