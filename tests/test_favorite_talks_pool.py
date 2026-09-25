"""Третья колонка «Фавориты → Диалоги»: пул **ответов** на их письма.

Вкладка показывала две колонки — список пар и разговор, — и вопрос «что ещё ждёт
ответа» оставался без места: работа была видна только по одной паре, под открытым
разговором. Колонка ставит список рядом, как «Инициатива» в «Диалогах» чата: с кем
переписка → что сказано → что ещё не ушло.

## ⚠⚠⚠ Главное здесь: это пул ОТВЕТОВ, а не «Приставания»

Пулы разные, и в этом занятии их путали **дважды**. Первый раз — в правке под
разговором: она брала письмо из `favMailPool`, и человек чистил пул приставаний, а
форма пустела, хотя ответ в переписке лежал (предупреждение живёт у `favTalkEdit`).
Второй раз — в этой самой колонке: она была заведена как второй список пула
приставаний, то есть как та же работа в двух местах.

Разница по существу: приставание пишут **первыми** тому, кто замолчал — у него своя
подвкладка, свой круг и свой потолок. Ответ пишут **на его письмо**, и лежит он в
`mail_reply` — том же пуле, что на «Письмах → Входящие».

⚠ Отсюда все проверки ниже: источник колонки — `mailReply`, и никакого `favMailPool`
в ней быть не должно.

## ⚠ Править ответ — в одном месте

Колонка это **список работы**: щелчок по карточке открывает переписку, а правят
ответ под ней. Ответ один на письмо, и две формы для него разошлись бы — поэтому у
карточки нет ни поля ввода, ни кнопок отправки и удаления.
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.page

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/uvicorn/template/admin/dating/profile/site/_base.html'
PAGE = ROOT / 'data/uvicorn/template/admin/dating/profile.html'


def _talks_pane() -> str:
    """Разметка панели «Диалоги» фаворитов — только она, без соседей."""
    text = BASE.read_text(encoding='utf-8')
    found = re.search(r'<div class="fav-pane off" data-fav-pane="talks">.*?'
                      r'(?=<div class="fav-pane off" data-fav-pane="block">)',
                      text, re.S)

    assert found, 'панели «Диалоги» фаворитов не нашлось'

    return found.group(0)


def _func(name: str) -> str:
    """Тело функции страницы — из разметки, а не переписанное в тест."""
    page = PAGE.read_text(encoding='utf-8')
    found = re.search(r'function ' + name + r'\(\w*\) \{.*?\n    \}', page, re.S)

    assert found, f'функция {name} не нашлась'

    return found.group(0)


class TestItIsTheReplyPool:
    """⚠⚠⚠ Не «Приставание». Та же ошибка в этом занятии делалась дважды."""

    def test_the_source_is_the_reply_pool(self):
        body = _func('favReplyPoolPaint')

        assert 'mailReply' in body, 'колонка не берёт пул ответов'

    def test_the_pressing_pool_is_not_used(self):
        """⚠ `favMailPool` — пул приставаний, и его здесь быть не должно вовсе.

        Возьми колонка его — на вкладке появился бы второй список того, что уже
        целиком показано на подвкладке «Приставание», и человек правил бы одну
        работу в двух местах.
        """
        body = _func('favReplyPoolPaint')

        assert 'favMailPool' not in body, \
            'колонка снова показывает пул приставаний, а не ответы'

    def test_the_head_says_replies(self):
        """⚠ Проверяем **видимую** разметку, вырезав комментарии.

        В комментариях «Приставание» упомянуто законно и нужно — там объяснено,
        почему это другой пул. А вот в тексте, который читает человек, его быть не
        должно: подпись колонки задаёт, чем он её считает.
        """
        visible = re.sub(r'\{#.*?#\}', '', _talks_pane(), flags=re.S)

        assert 'Ответы в пуле' in visible, 'колонка названа не ответами'
        assert 'Приставание' not in visible, \
            'в «Диалогах» снова видно слово «Приставание» — это другой пул'

    def test_only_drafts_are_shown(self):
        """⚠ Ушедшее — вопрос «Доставки», в пуле работы его нет."""
        body = _func('favReplyPoolPaint')

        assert "status === 'draft'" in body, 'колонка показывает и ушедшие ответы'


class TestTheMarkup:
    """Колонка заведена, и заведена в правильном месте."""

    def test_the_layout_is_three_columns(self):
        assert 'in-cols in-cols-3' in _talks_pane(), \
            'раскладка на три колонки не заведена'

    def test_the_slots_are_there(self):
        pane = _talks_pane()

        for slot in ('fav-reply-pool', 'fav-reply-count', 'fav-reply-reload'):
            assert f'data-slot="{slot}"' in pane, f'нет слота {slot}'

    def test_the_pool_stands_right_of_the_thread(self):
        """⚠ Справа от разговора, а не слева.

        Список смотрят, уже прочитав открытую переписку. Слева он отодвигал бы
        разговор от списка пар, по которому его выбирают.
        """
        pane = _talks_pane()

        assert (pane.index('data-slot="fav-talks-list"')
                < pane.index('data-slot="fav-talk-thread"')
                < pane.index('data-slot="fav-reply-pool"')), \
            'порядок колонок не «список → разговор → ответы»'

    def test_there_is_a_splitter_before_the_pool(self):
        """⚠ Разделитель обязателен, и класс у него общий с чатом.

        По классу страница вешает перетаскивание (`splitDrag`): свой остался бы без
        обработчика, и колонку нельзя было бы сузить.
        """
        pane = _talks_pane()

        assert 'split split-initiative' in pane, 'разделителя перед пулом нет'
        assert pane.index('split-initiative') < pane.index('data-slot="fav-reply-pool"')

    def test_the_shared_layout_is_untouched(self):
        """⚠ Общая раскладка `.in-cols` по-прежнему про две колонки.

        Её используют «Письма → Входящие»: расширь её до трёх — у них появилась бы
        пустая третья.
        """
        page = PAGE.read_text(encoding='utf-8')
        shared = page[page.index('    .in-cols {'):]
        shared = shared[:shared.index('}')]

        assert shared.count('minmax') == 2, 'общую раскладку тронули — сломает соседей'


class TestTheCardOpensTheTalk:
    """⚠⚠ Карточка **открывает переписку**, а не правит ответ на месте.

    Ответ один на письмо, и править его двумя способами нельзя: под разговором уже
    есть поле, «Сохранить», «Отправить» и «Убрать из пула». Второе место для того же
    ответа разошлось бы с первым — это ровно та беда, от которой в этом занятии уже
    уходили.
    """

    def test_the_card_has_no_buttons(self):
        body = _func('favReplyCard')

        for what in ('mail-card-send', 'mail-card-drop', 'textarea'):
            assert what not in body, \
                f'на карточке ответа появилось «{what}» — правка переехала из-под разговора'

    def test_the_click_opens_the_pair(self):
        body = _func('favReplyPoolPaint')

        assert 'favTalkPairOpen' in body, 'щелчок по карточке не открывает переписку'

    def test_the_pair_is_opened_by_row_not_by_index(self):
        """⚠⚠ Открываем **строкой пула**, а не номером в списке переписок.

        Пара может лежать на другой его странице, и по номеру её было бы не найти:
        щелчок молча не делал бы ничего — либо, хуже, открывал бы чужую переписку.
        """
        body = _func('favReplyPoolPaint')

        assert 'favTalkSelect' not in body, \
            'колонка открывает переписку по номеру в списке — она может быть не на нём'
        assert 'profile_id: one.profile_id' in body and 'uid: one.uid' in body, \
            'в открытие не передана сама пара'

    def test_the_edit_under_the_talk_survives_a_pair_off_the_list(self):
        """⚠ Правка под разговором обязана перерисоваться и для такой пары.

        Она искала строку **в списке**, и для пары с другой его страницы не находила
        ничего — то есть показывала «ответа в пуле нет» над лежащим там ответом.
        """
        body = _func('favTalkEditRefresh')

        assert 'favTalkRow' in body, \
            'правка под разговором снова ищет пару только в списке'


class TestTheEmptyColumnExplainsItself:
    """⚠⚠ Три пустоты — три разных ответа, и путать их нельзя.

    «Ответов нет» на месте «у этой анкеты их нет» отправило бы человека проверять
    выключатель генерации вместо того, чтобы снять фильтр в списке анкет.
    """

    def test_the_filter_case_says_to_drop_the_filter(self):
        body = _func('favReplyPoolPaint')

        assert 'Снимите выбор' in body, \
            'пустота под фильтром не подсказывает, что фильтр можно снять'

    def test_the_empty_pool_case_names_the_round(self):
        """⚠ Пустой пул объясняем **кругом и выключателем**: это и есть причина."""
        body = _func('favReplyPoolPaint')

        assert 'генерация ответов' in body, \
            'пустой пул не объяснён — молчащая колонка читается как поломка'


class TestTheColumnKeepsUp:
    """⚠⚠ Колонка и правка под разговором живут одним пулом — и обновляются вместе."""

    def test_showing_the_tab_loads_the_replies(self):
        """⚠ На «Диалоги» заходят минуя «Письма → Входящие»: пул грузится сам.

        Иначе колонка стоит пустой, а пустая она читается как «ответов нет».
        """
        body = _func('favTalksShow')

        assert 'mailReplyLoad()' in body, \
            'вкладка не грузит пул ответов — колонка останется пустой'

    def test_the_pool_load_repaints_the_column(self):
        """⚠ Одна загрузка — обе отрисовки: иначе список справа и правка под
        разговором расходятся на глазах."""
        body = _func('mailReplyLoad')

        assert 'favReplyPoolPaint()' in body, \
            'загрузка пула не трогает колонку'

    def test_opening_a_pair_repaints_the_column(self):
        """⚠ Открытая пара в колонке подсвечена, и подсветка переезжает с выбором."""
        body = _func('favTalkPairOpen')

        assert 'favReplyPoolPaint()' in body, 'подсветка открытой пары не переезжает'


class TestTheFilterFollowsTheTab:
    """⚠ Анкета — фильтр, как и для двух соседних колонок этой вкладки.

    Пул ответов **общий на сайт** намеренно (`mail_reply_list`: письма приходят разным
    девушкам, а читает ответы один человек подряд). Но вкладка живёт отбором, и
    колонка, его не слушающая, отвечала бы не на тот вопрос, что соседние.
    """

    def test_the_profile_filters_the_cards(self):
        body = _func('favReplyPoolPaint')

        assert 'openId' in body, 'колонка не слушает выбранную анкету'

    def test_without_a_profile_the_whole_pool_is_shown(self):
        """⚠ Без выбора — весь пул сайта: вкладка отвечает на «что вообще ждёт»."""
        body = _func('favReplyPoolPaint')

        assert '!openId ||' in body, \
            'без выбранной анкеты колонка показывает не весь пул'
