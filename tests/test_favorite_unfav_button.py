"""«Фавориты → Доставка»: красная строка знает звезду «удалить из фаворитов».

Письмо не ушло именно потому, что человек не был или перестал быть избранным у
этой анкеты. Нажатие по звезде велит ручке забыть пару в обе стороны сразу:
сперва погасить звезду на сайте (своей ручки снятия у сайта нет, потому там
жмём мы — сценарий «анкета мужчины: из избранных»), и лишь затем убрать строку
таблицы и черновик приставания. Строка журнала остаётся как была — журнал уже
случившееся.

## ⚠⚠ Что ручка делает и чего не делает

`DELETE /api/dating/favorite?profile_id=&uid=` (`favorite_forget`): сперва гасит
звезду сайта, затем уносят строку `favorite` и черновик, переписка переезжает в
журнал «Письма», а красная строка остаётся здесь. Погасшая на сайте звезда —
заодно и причина, по которой круг больше не вернёт строку: без шага на сайте
«удаление» держалось бы ровно до ближайшей сверки. Отказ прогона — строка
остаётся, человек видит причину. Повторное нажатие не ошибка: `dropped: false` —
тоже успех. Само тело ручки сторожит `test_favorite_forget`; эта страница
сторожит кнопку, уведомитель и то, что она зовёт ровно эту ручку.

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


class TestTheClickCallsTheHandle:
    """Нажатие зовёт ручку — и больше ничего не выдумывает."""

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

    def test_it_calls_the_removal_handle(self):
        """⚠ Ручка ровно эта и ровно одна: пара в доводах, отказ не тонет,
        журнал перезагружается после удачи. Чужих таблиц кнопка не трогает —
        всё удаление живёт за ручкой (`favorite_forget`, `test_favorite_forget`)."""
        body = _func('favSentPaint')

        assert '/api/dating/favorite' in body, 'звезда не зовёт ручку удаления'
        assert 'deleteData' in body, 'звезда не удаляет, а притворяется'
        assert '?profile_id=' in body and 'uid=' in body, \
            'ручка зовётся без пары — кого убирать, неизвестно'
        assert 'favSentLoad()' in body, \
            'после удачи журнал не перечитан: переписка его осталась бы показана'

    def test_the_failure_is_said(self):
        """⚠ Отказ ручки не должен тонуть: человек нажал — человек видит причину."""
        body = _func('favSentPaint')

        assert "'error'" in body, 'отказ ручки не показан'
