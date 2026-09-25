"""Третья колонка «Фавориты → Диалоги»: пул, чем продолжить переписку.

Вкладка показывала две колонки — список пар и разговор, — и вопрос «чем ответить»
оставался без места: письмо для открытой пары виднелось узкой правкой под
разговором, а весь пул жил на соседней подвкладке. Колонка ставит их рядом, как в
«Диалогах» чата: с кем переписка → что сказано → чем продолжить.

## ⚠⚠ Пул тот же, а не своя копия

Колонка не имеет своей ручки: она рисует **уже загруженный** `favMailPool` —
тот же, что подвкладка «Приставание», — отобранный открытой анкетой. Заведи мы
колонке свою загрузку, две колонки об одном пуле отвечали бы из разных мгновений:
удалил карточку слева, а справа она ещё есть.

Отсюда и главная проверка этого файла — **номер карточки**. Обработчики берут
письмо из общего массива по индексу, поэтому отобранное нельзя перенумеровывать:
перенумеруй — щелчок откроет чужое письмо, и тем вернее, чем сильнее сужен фильтр.
Ошибка эта не видна глазами (карточка открывается, письмо «какое-то»), поэтому
стережётся тестом.
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


class TestTheMarkup:
    """Колонка заведена, и заведена в правильном месте."""

    def test_the_layout_is_three_columns(self):
        assert 'in-cols in-cols-3' in _talks_pane(), \
            'раскладка на три колонки не заведена'

    def test_the_slots_are_there(self):
        pane = _talks_pane()

        for slot in ('fav-talks-pool', 'fav-talks-pool-count',
                     'fav-talks-pool-reload'):
            assert f'data-slot="{slot}"' in pane, f'нет слота {slot}'

    def test_the_pool_stands_right_of_the_thread(self):
        """⚠ Справа от разговора, а не слева.

        Пул открывают, уже прочитав переписку, — то есть последним. Слева он
        отодвигал бы разговор от списка, по которому его выбирают.
        """
        pane = _talks_pane()

        assert (pane.index('data-slot="fav-talks-list"')
                < pane.index('data-slot="fav-talk-thread"')
                < pane.index('data-slot="fav-talks-pool"')), \
            'порядок колонок не «список → разговор → пул»'

    def test_there_is_a_splitter_before_the_pool(self):
        """⚠ Разделитель обязателен: без него колонки читаются как одна.

        Тот же класс, что у чата (`split-initiative`), и это не косметика —
        по нему страница вешает перетаскивание (`splitDrag`). Свой класс остался бы
        без обработчика, и колонку нельзя было бы сузить.
        """
        pane = _talks_pane()

        assert 'split split-initiative' in pane, 'разделителя перед пулом нет'
        assert pane.index('split-initiative') < pane.index('data-slot="fav-talks-pool"')

    def test_the_shared_layout_is_untouched(self):
        """⚠ Общая раскладка `.in-cols` по-прежнему про две колонки.

        Её используют «Письма → Диалоги»: расширь её до трёх — у них появилась бы
        пустая третья.
        """
        page = PAGE.read_text(encoding='utf-8')
        shared = page[page.index('    .in-cols {'):]
        shared = shared[:shared.index('}')]

        assert shared.count('minmax') == 2, 'общую раскладку тронули — сломает соседей'


class TestTheCardKeepsItsNumber:
    """⚠⚠ Отобранное не перенумеровывается — иначе щелчок откроет чужое письмо.

    Это и есть цена решения «рисуем общий массив, а не свой запрос»: обработчик
    берёт письмо как `favMailPool[index]`, а колонка показывает не весь массив.
    Проверяем **саму функцию страницы**, вырезая её из разметки: копия правила в
    тесте разошлась бы с оригиналом при первой же правке.
    """

    def test_the_filtered_cards_carry_original_indexes(self):
        import os
        import shutil
        import subprocess
        import tempfile

        node = shutil.which('node')
        if not node:
            pytest.skip('node не установлен')

        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r'function favTalksPoolPaint\(\) \{.*?\n    \}',
                          page, re.S)

        assert found, 'функция отрисовки колонки не нашлась'

        # ⚠ Подменяем только окружение — слоты, `esc`, вид карточки и привязку
        # обработчиков. Сам отбор и нумерация — настоящие, из разметки.
        probe = (
            'const box = {innerHTML: "", querySelectorAll: () => []};\n'
            'const slot = (name) => name === "fav-talks-pool" ? box : null;\n'
            'const fillSlot = () => {};\n'
            'const esc = (s) => String(s);\n'
            'let openId = 7;\n'
            'const favMailWhy = "";\n'
            # Пул: три письма, и лишь второе с третьим — открытой анкеты.
            'const favMailPool = [\n'
            '  {profile_id: 1, body: "чужое", uid: "a"},\n'
            '  {profile_id: 7, body: "её первое", uid: "b"},\n'
            '  {profile_id: 7, body: "её второе", uid: "c"},\n'
            '];\n'
            'const shown = [];\n'
            'const favMailCard = (one, index) => { shown.push([index, one.body]);'
            ' return ""; };\n'
            'const favMailCardsBind = () => {};\n'
            + found.group(0) + '\n'
            'favTalksPoolPaint();\n'
            # ⚠ Ждём исходные номера 1 и 2, а не 0 и 1: по ним обработчик найдёт
            # письмо в общем массиве.
            'const want = JSON.stringify([[1, "её первое"], [2, "её второе"]]);\n'
            'if (JSON.stringify(shown) !== want) {\n'
            '  console.log("показано " + JSON.stringify(shown) + ", ждали " + want);\n'
            '  process.exit(1);\n'
            '}\n')

        fd, path = tempfile.mkstemp(suffix='.mjs')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(probe)

            result = subprocess.run([node, path], capture_output=True, text=True)

            assert result.returncode == 0, \
                (f'номера карточек перепутаны — щелчок откроет чужое письмо: '
                 f'{result.stdout.strip()}{result.stderr.strip()[:300]}')
        finally:
            os.remove(path)

    def test_without_a_chosen_profile_the_whole_pool_is_shown(self):
        """⚠ Анкета — **фильтр**, а не условие показа.

        Без выбора колонка показывает весь пул сайта: вкладка отвечает на вопрос
        «что вообще происходит», и пустая колонка при полном пуле читалась бы как
        поломка.
        """
        import os
        import shutil
        import subprocess
        import tempfile

        node = shutil.which('node')
        if not node:
            pytest.skip('node не установлен')

        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r'function favTalksPoolPaint\(\) \{.*?\n    \}',
                          page, re.S)

        assert found, 'функция отрисовки колонки не нашлась'

        probe = (
            'const box = {innerHTML: "", querySelectorAll: () => []};\n'
            'const slot = (name) => name === "fav-talks-pool" ? box : null;\n'
            'const fillSlot = () => {};\n'
            'const esc = (s) => String(s);\n'
            # Анкета не выбрана — ровно то состояние, при котором список полон.
            'let openId = 0;\n'
            'const favMailWhy = "";\n'
            'const favMailPool = [{profile_id: 1, body: "a"},'
            ' {profile_id: 7, body: "b"}];\n'
            'const shown = [];\n'
            'const favMailCard = (one, index) => { shown.push(index); return ""; };\n'
            'const favMailCardsBind = () => {};\n'
            + found.group(0) + '\n'
            'favTalksPoolPaint();\n'
            'if (JSON.stringify(shown) !== JSON.stringify([0, 1])) {\n'
            '  console.log("показано " + JSON.stringify(shown));\n'
            '  process.exit(1);\n'
            '}\n')

        fd, path = tempfile.mkstemp(suffix='.mjs')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(probe)

            result = subprocess.run([node, path], capture_output=True, text=True)

            assert result.returncode == 0, \
                (f'без выбранной анкеты показан не весь пул: '
                 f'{result.stdout.strip()}{result.stderr.strip()[:300]}')
        finally:
            os.remove(path)


class TestTheEmptyColumnExplainsItself:
    """⚠⚠ Три пустоты — три разных ответа, и путать их нельзя.

    «Пул пуст» на месте «у этой анкеты писем нет» отправило бы человека проверять
    настройки сайта вместо того, чтобы снять фильтр в списке анкет.
    """

    def test_the_filter_case_says_to_drop_the_filter(self):
        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r'function favTalksPoolPaint\(\) \{.*?\n    \}',
                          page, re.S)

        assert found, 'функция отрисовки колонки не нашлась'
        body = found.group(0)

        assert 'Снимите выбор' in body, \
            'пустота под фильтром не подсказывает, что фильтр можно снять'

    def test_the_empty_pool_case_uses_the_server_reason(self):
        """⚠ Причину пустого пула объясняет **сервер** (`why`), а не страница.

        Своя копия объяснения разошлась бы с ним в первый же день: причин
        несколько — нет затихших разговоров, никто не в сети, снята генерация.
        """
        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r'function favTalksPoolPaint\(\) \{.*?\n    \}',
                          page, re.S)
        body = found.group(0)

        assert 'favMailWhy' in body, 'причина пустоты не берётся с сервера'


class TestTheColumnIsFilledWithoutTheNeighbourTab:
    """⚠⚠ На «Диалоги» заходят минуя «Приставание» — пул обязан подгрузиться сам.

    Иначе колонка стоит пустой, а пустая она читается как «пул пуст», то есть врёт.
    Так же поступают «Диалоги» чата с пулом инициативы.
    """

    def test_showing_the_tab_loads_the_pool(self):
        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r'function favTalksShow\(\) \{.*?\n    \}', page, re.S)

        assert found, 'функция показа вкладки не нашлась'

        assert 'favMailLoad()' in found.group(0), \
            'вкладка не грузит пул — колонка останется пустой'

    def test_the_pool_paint_also_draws_the_column(self):
        """⚠ Одна отрисовка на обе колонки: они об одном пуле.

        Иначе удаление карточки на «Приставании» оставляло бы её в «Диалогах» до
        перезагрузки страницы.
        """
        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r'function favMailPaint\(\) \{.*?\n    \}', page, re.S)

        assert found, 'функция отрисовки пула не нашлась'
        assert 'favTalksPoolPaint()' in found.group(0), \
            'общая отрисовка не трогает третью колонку'
