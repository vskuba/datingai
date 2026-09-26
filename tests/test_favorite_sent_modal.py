"""«Фавориты → Доставка»: щелчок по строке открывает окно записи.

Журнал показывал строку, и всё — текст письма читали в подсказке, обрезанным по
ширине колонки. У «Писем → Доставка» то же нажатие открывает окно: обе стороны,
состояние, причина, текст целиком. Здесь заведено такое же.

## ⚠⚠ Окно своё, а не `letterOpen`, и разница по существу

Окно писем умеет **править** письмо и убирать его из пула — ручками рассылки
(`/api/dating/mailing/{id}`). Позови его отсюда, и «Сохранить в пуле» ушло бы в
чужую таблицу: письма избранным живут в `favorite_mail`.

⚠ Править здесь и нечего: строка журнала — уже случившееся. Черновик с причиной
отказа правят в пуле «Приставания», где у него своё окно и своя ручка.

## ⚠⚠⚠ Заодно исправлена беда, найденная при заведении

`letterOpen(index, list)` принимал список, а `letterSave`/`letterDrop` читали
`mailPool[index]` — **свой, жёстко**. Пока окно открывали из пула, индексы
совпадали; из журнала доставки индекс уже чужой.

Журнал показывает `delivery` — это `sent`, `failed` **и черновики с причиной
отказа**. У черновика `sent === false`, значит кнопки правки рисовались — и правили
письмо `mailPool` под тем же номером, то есть случайное чужое. Молча: ответ
успешный, карточка перерисована, человек видел сохранение — только не того письма.
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


class TestTheRowOpensAWindow:
    """Нажатие по строке журнала ведёт в окно, а не в никуда."""

    def test_the_click_is_bound(self):
        """⚠ Обработчик висит **после** отрисовки строк: они переписываются целиком
        при каждом приходе журнала, и повешенное на прежние узлы не пережило бы
        первого обновления."""
        body = _func('favSentPaint')

        assert 'favSentOpen' in body, 'щелчок по строке доставки ничего не открывает'
        assert ".querySelectorAll('.mail-sent-row')" in body, \
            'обработчик не привязан к строкам журнала'

    def test_the_window_exists(self):
        assert 'modalOpen' in _func('favSentOpen'), \
            'окно не открывается — функция есть, а окна нет'

    def test_it_takes_the_row_from_its_own_list(self):
        """⚠⚠ Строку берём из `favSent`, а не из чужого массива.

        Ровно этим и была беда у писем: индекс от журнала указывал в пул. Здесь
        список один, и он назван прямо.
        """
        body = _func('favSentOpen')

        assert 'favSent[index]' in body, 'окно читает строку не из своего журнала'


class TestTheWindowIsReadOnly:
    """⚠⚠ Только чтение: строка журнала — уже случившееся."""

    def test_there_are_no_edit_buttons(self):
        body = _func('favSentOpen')

        for what in ('letterSave', 'letterDrop', 'dating-btn', 'Сохранить',
                     'Убрать из пула'):
            assert what not in body, \
                f'в окне журнала появилось «{what}» — правка задним числом'

    def test_the_text_is_readonly(self):
        """⚠ `readonly`, а не поле без кнопок: текст надо дать выделить и
        скопировать, но правка отвечала бы неправдой на «что человек получил»."""
        body = _func('favSentOpen')

        assert 'readonly' in body, 'текст письма в окне можно править'

    def test_it_does_not_touch_the_mailing_api(self):
        """⚠⚠⚠ Ни одной ручки рассылки: письма избранным живут в другой таблице.

        Это и есть довод против переиспользования `letterOpen` — оно правит через
        `/api/dating/mailing/{id}`.
        """
        body = _func('favSentOpen')

        assert '/api/dating/mailing' not in body, \
            'окно избранных зовёт ручку рассылки — чужую таблицу'


class TestItLooksLikeTheNeighbour:
    """⚠ Вид общий с окном письма: три журнала доставки стоят рядом."""

    def test_the_sides_use_the_shared_helper(self):
        """Обе стороны — тем же `letterSides`, что у писем: лицо, имя, номер."""
        assert 'letterSides(row)' in _func('favSentOpen'), \
            'стороны собраны своей разметкой, а не общим помощником'

    def test_the_state_uses_the_shared_helper(self):
        """⚠ Состояние — той же `_state`: «отправлено · 12:31», «отправить не вышло».

        Своя формулировка разошлась бы с соседней вкладкой, и один исход
        назывался бы двумя словами.
        """
        assert '_state(row)' in _func('favSentOpen'), \
            'состояние названо своими словами, а не общими'

    def test_the_site_answer_has_its_own_row(self):
        """⚠⚠ Ответ сайта — строкой, а не подсказкой.

        «Доставлено» это наш вывод из его слов; когда вывод не сходится с тем, что
        видно на сайте, разбираются по самому ответу — и в окне для него есть место,
        в отличие от строки журнала.
        """
        body = _func('favSentOpen')

        assert 'row.answer' in body, 'ответа сайта в окне нет'
        assert 'Ответ сайта' in body, 'ответ сайта показан без подписи'


class TestTheWrongListBugIsFixed:
    """⚠⚠⚠ Сторож от возврата беды: правка обязана идти в тот список, из которого
    открыли окно.

    Верни `mailPool[index]` в `letterSave` — и щелчок по красной строке журнала
    доставки снова будет править случайное письмо пула. Проверяется по исходнику:
    поймать это прогоном нельзя, беда молчит.
    """

    def test_the_list_reaches_the_save(self):
        body = _func('letterSave')

        assert 'list = mailPool' in body, 'список больше не приходит в правку'
        assert 'const row = list[index]' in body, \
            'правка снова берёт строку из `mailPool` по чужому индексу'
        assert 'mailPool[index] =' not in body, \
            'запись снова идёт в `mailPool` — подменит соседнее письмо'

    def test_the_list_reaches_the_drop(self):
        body = _func('letterDrop')

        assert 'const row = list[index]' in body, \
            'удаление снова берёт строку из `mailPool`'
        assert 'mailPool.splice' not in body, \
            'удаление снова режет `mailPool` по чужому индексу'

    def test_the_window_passes_the_list_on(self):
        body = _func('letterOpen')

        assert 'letterSave(index, box, list)' in body, \
            'окно не передаёт список в правку'
        assert 'letterDrop(index, box, list)' in body, \
            'окно не передаёт список в удаление'

    def test_each_list_repaints_itself(self):
        """⚠ Своя отрисовка своему списку: общая `mailPoolPaint` для журнала
        молчала бы — строка осталась бы прежней, и человек решил бы, что правка не
        сохранилась."""
        body = _func('letterSave')

        assert 'list === mailPool' in body and 'mailSentPaint' in body, \
            'после правки перерисовывается только пул'
