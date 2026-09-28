"""«Фавориты → Доставка»: красная строка знает звезду «удалить из фаворитов».

Письмо не ушло именно потому, что человек не был или перестал быть избранным у
этой анкеты. Человек снял звезду там, где она стоит, а журнал не умел это
показать. Теперь умеет: звезда в правой колонке красной строки, а нажатие по ней
подтверждает состоявшееся общим уведомителем панели.

## ⚠⚠ Это показ, а не правка

Кнопка ничего не меняет и не удаляет: строка журнала — уже случившееся (то же
правило, что у окна записи, см. `test_favorite_sent_modal`), а удаление живёт
там, где звёздочка, не здесь. Здесь только общий `showNotification` — молча
подтвердить то, что уже случилось.

⚠ Два прочих журнала (`inviteSentPaint`, `mailSentPaint`) кнопку не просили и не
получают: она стоит только там, где звезда и гаснет. Сеткой и числом колонок
ведает сторож `test_sent_row_grid` — он же считает и этот седьмой узел.
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


class TestTheButtonIsOnTheRedRowOnly:
    """Кнопка есть красной строке «Фаворитов» и только ей."""

    def test_the_button_exists_in_the_layout(self):
        """⚠ `one.remove && !ok`: на зелёной строке удалять нечего, занятие
        удалось — и звезда висела бы вопросом без ответа."""
        body = _func('sentRow')

        assert 'one.remove && !ok' in body, \
            'кнопка рисуется не только на красной строке или не рисуется вовсе'
        assert 'mail-sent-unfav' in body, 'в выкройке нет колонки действия'

    def test_the_journals_without_remove_get_nothing(self):
        """⚠ Журналы без `remove: true` не должны просить кнопку вообще: там
        седьмая колонка пустует, и это верно."""
        for name in ('inviteSentPaint', 'mailSentPaint'):
            assert 'remove' not in _func(name), \
                f'{name} завёл чужую колонку действия'


class TestTheClickOnlyConfirms:
    """Нажатие подтверждает уже случившееся — и больше ничего."""

    def test_the_click_is_bound_after_paint(self):
        """⚠ Обработчик висит **после** отрисовки строки: они переписываются
        целиком при каждом приходе журнала."""
        body = _func('favSentPaint')

        assert "querySelectorAll('.mail-sent-unfav')" in body, \
            'нажатие по звезде не привязано к строкам журнала'

    def test_the_click_does_not_open_the_window(self):
        """⚠ Вся строка открывает окно записи; нажатие по звезде не должно
        тащить его следом за уведомлением."""
        body = _func('favSentPaint')

        assert 'e.stopPropagation()' in body, \
            'звезда открыла бы окно поверх уведомления'

    def test_the_notification_is_the_shared_one(self):
        """⚠ Общий уведомитель на всю панель, kind `success`, имя — из той же
        строки журнала: второго способа сказать об успехе у страницы нет."""
        body = _func('favSentPaint')

        assert 'showNotification' in body, 'уведомления нет'
        assert "'success'" in body, 'уведомление не об успехе'
        assert 'успешно удалён' in body, 'уведомление молчит, что фаворит удалён'

    def test_it_calls_nothing(self):
        """⚠⚠ Ни ручки, ни перезагрузки журнала: показ не правит. Заодно —
        чужая таблица сюда не должна приехать никакой ручкой (см. окно записи)."""
        body = _func('favSentPaint')

        assert '/api/' not in body, 'звезда позвала ручку — правку задним числом'
        assert 'favSentLoad' not in body, 'звезда перезагрузила журнал'
