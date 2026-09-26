"""Строка журнала доставки: одна сетка на три журнала, и узлы её не переполняют.

Журналы доставки — инвайтов, писем избранным и писем — рисует одна выкройка
(`sentRow`). Вид у них расходился, и причина была не в выкройке, а в **сетке**:

    колонок было 5, узлов у избранных 5, у писем 6 (плюс чипс рода)

Шестой узел CSS Grid кладёт в **неявную** колонку — создаёт её молча, по остаточной
ширине. Значит «доставлено» у писем стояло не там, где у избранных: вкладки рядом,
а вид разный.

## ⚠⚠ Почему это проверяется арифметикой, а не снимком

Расхождение в одну колонку глазами не видно: обе строки выглядят «нормально», пока
не положить их рядом. А поймать его можно счётом — колонок в CSS против узлов в
выкройке, — и такая проверка не требует браузера и не зависит от шрифтов.

⚠ Ровно так расхождение и нашлось: не снимком, а подсчётом.

## ⚠ Пустой чипс — часть решения, а не заглушка

Грид раскладывает узлы **по порядку**: пропусти узел — следующий сядет в его
колонку. Поэтому там, где рода нет (избранные, инвайты), ставится пустой `<span>`:
он держит шестую колонку за исходом. Ширины пустой узел не занимает.
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.page

PAGE = (pathlib.Path(__file__).resolve().parents[1]
        / 'data/uvicorn/template/admin/dating/profile.html')


def _grid(selector: str) -> list:
    """Колонки сетки по селектору. ⚠ `minmax(0, 1fr)` — ОДНА колонка, а не две:
    запятая внутри скобок, и наивный `split` считал бы её двумя."""
    page = PAGE.read_text(encoding='utf-8')
    at = page.index(selector)
    block = page[at:page.index('}', at)]
    found = re.search(r'grid-template-columns: ([^;]+);', block)

    return (found.group(1).strip().replace('minmax(0, 1fr)', '1fr').split()
            if found else [])


def _row_nodes() -> list:
    """Узлы верхнего уровня строки — в порядке появления в выкройке."""
    page = PAGE.read_text(encoding='utf-8')
    body = re.search(r'function sentRow\(row, opts\) \{.*?\n    \}', page, re.S)

    assert body, 'выкройка строки не нашлась'

    seen = []
    for one in re.findall(r'<span class="(mail-sent-[a-z]+)', body.group(0)):
        if one not in seen:
            seen.append(one)

    return seen


class TestOneGridForThreeJournals:
    """⚠⚠ Сетка одна: три описания одной строки разошлись бы на первой правке."""

    def test_the_plain_row_has_no_grid_of_its_own(self):
        """⚠ У `.plain` своей сетки быть не должно.

        Она там была — на четыре колонки, — и перекрывала общую для инвайтов и
        избранных: у них исход уезжал в неявную колонку, а у писем нет.
        """
        assert not _grid('.mail-sent-row.plain {'), \
            'у `.plain` снова своя сетка — журналы разойдутся видом'

    def test_the_one_line_row_defines_the_grid(self):
        cols = _grid('.mail-sent-row.plain.one {')

        assert cols, 'общей сетки строки нет вовсе'


class TestNodesFitTheColumns:
    """⚠⚠⚠ Узлов не больше, чем колонок, — иначе грид создаёт неявные молча."""

    def test_the_column_count_matches(self):
        cols = _grid('.mail-sent-row.plain.one {')
        nodes = _row_nodes()

        assert len(nodes) == len(cols), \
            (f'узлов {len(nodes)} на {len(cols)} колонок: '
             f'{", ".join(nodes)} против {" ".join(cols)}')

    def test_the_outcome_is_in_the_last_column(self):
        """⚠ Исход — в последней колонке, у правого края.

        Это и есть то, что расходилось: с пятью колонками он у писем садился в
        шестую неявную, а у избранных в пятую объявленную.
        """
        cols = _grid('.mail-sent-row.plain.one {')
        nodes = _row_nodes()

        assert nodes.index('mail-sent-state') == len(cols) - 1, \
            'исход не в последней колонке — у журналов он встанет по-разному'

    def test_the_text_column_stretches(self):
        """⚠ Растягивается колонка **текста**, а не соседняя: пустота между именами
        и чипсом оторвала бы метку от письма, к которому она относится."""
        cols = _grid('.mail-sent-row.plain.one {')
        nodes = _row_nodes()

        assert cols[nodes.index('mail-sent-text')] == '1fr', \
            'растягивается не колонка текста'


class TestTheEmptyChipHoldsItsColumn:
    """⚠⚠ Там, где рода нет, стоит пустой узел — иначе исход съедет влево."""

    def test_the_chip_is_always_rendered(self):
        page = PAGE.read_text(encoding='utf-8')
        body = re.search(r'function sentRow\(row, opts\) \{.*?\n    \}',
                         page, re.S).group(0)

        assert 'mail-sent-kind empty' in body, \
            'без рода чипс не рисуется вовсе — исход сядет в его колонку'

    def test_the_empty_chip_draws_nothing(self):
        """⚠ Ни рамки, ни заливки, ни полей: узел нужен гриду, а не глазу. Без
        `border: none` от него осталась бы полоска в один пиксель."""
        page = PAGE.read_text(encoding='utf-8')
        at = page.index('.mail-sent-kind.empty {')
        block = page[at:page.index('}', at)]

        for what in ('padding: 0', 'border: none', 'background: none'):
            assert what in block, f'у пустого чипса нет `{what}` — он будет виден'

    def test_the_real_chip_still_has_its_look(self):
        """⚠ Сторож обратного: пустой стиль не должен съесть обычный чипс."""
        page = PAGE.read_text(encoding='utf-8')
        at = page.index('.mail-sent-kind {')
        block = page[at:page.index('}', at)]

        assert 'border-radius: 999px' in block and 'background: #f1f5f9' in block, \
            'обычный чипс потерял вид'


class TestTheRowHasNoTailRules:
    """⚠⚠ Хвостовой строки у записи больше нет — и правил под неё тоже.

    Правило `grid-column: 3 / -1` уводило узел на свою строку под записью. Его
    подопечных не осталось ни одного: `.mail-sent-why` убрали вместе с пометкой
    «осталось в пуле» (26.09.2026), `.mail-sent-said` не рисуется с 13.09, а текст
    реплики в журнале всегда идёт строкой `.one` — и та по весу сильнее.

    ⚠ Проверяется именно **отсутствие**: правило под несуществующий узел читают как
    действующее, и следующий, кто заведёт хвост, получит его в третьей колонке без
    всякого на то решения.
    """

    def test_there_is_no_tail_rule(self):
        page = PAGE.read_text(encoding='utf-8')
        rules = re.findall(
            r'\n    \.mail-sent-row\.plain \.mail-sent-[a-z]+[^{]*\{([^}]*)\}', page)

        assert not any('grid-column: 3 / -1' in one for one in rules), \
            'правило хвостовой строки вернулось — у него нет подопечных'

    def test_the_dead_class_is_gone(self):
        """⚠ `.mail-sent-said` — четыре правила на класс, который не рисуется."""
        page = PAGE.read_text(encoding='utf-8')

        assert '.mail-sent-said {' not in page, \
            'правила мёртвого класса вернулись'
        assert 'class="mail-sent-said' not in page, \
            'класс снова рисуется — тогда правила нужны, а тест нет'

    def test_the_text_still_beats_the_base_rule(self):
        """⚠⚠ `grid-column: auto` у `.one` убирать **нельзя**, хоть хвостового
        правила и не стало: текст перебивает не его, а **базовое** `.mail-sent-text`
        с `grid-column: 2 / -1`. Убери — и реплика уедет со своей колонки на всю
        ширину, то есть строка снова станет двухэтажной.
        """
        page = PAGE.read_text(encoding='utf-8')
        at = page.index('.mail-sent-row.plain.one .mail-sent-text {')
        block = page[at:page.index('}', at)]

        assert 'grid-column: auto' in block, \
            'текст перестал перебивать базовое правило — строка раздвоится'
