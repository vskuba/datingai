"""⚠⚠ Перевод текста: общий модуль каркаса плюс дверь к соседу.

Задача завелась у двух панелей (RhapsodyAi — куски промпта, DatingAi — подписи
переменных), и общего в ней всё, кроме двух вещей: **какие бывают метки** и
**чем зовётся перевод**. Обе приходят доводом, а не зашиты — иначе модуль
перестал бы быть переносимым (`dashboard/README.md`, «Что сюда кладут»).

⚠⚠⚠ Правило, ради которого всё и устроено так: **метка до модели не доходит**.
Переведи модель `[[companion_name]]`, и письмо сломается *без единой ошибки* —
метка не подставится, а уедет в текст словами. Поэтому каждая заслоняется, а
после перевода **сверяется**; не вернулась хоть одна — текст не заменяется вовсе.
Чистые функции этого правила сторожит `tests/test_text_translate.mjs`, здесь —
их прогон и границы ручки.
"""

import pathlib
import re
import subprocess

import pytest

pytestmark = pytest.mark.page

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _no_comments(js: str) -> str:
    """Код без пояснений: `//` до конца строки и блоки `/* … */`."""
    js = re.sub(r'/\*.*?\*/', '', js, flags=re.S)

    return re.sub(r'//.*', '', js)


def _no_docstrings(py: str) -> str:
    """Код без тройных строк: пояснения сверяться не должны."""
    return re.sub(r'"""Rtm.*?"""', '', re.sub(r'"""(?:.|\n)*?"""', '', py))


class TestTheSharedModuleHolds:
    """Чистые функции каркаса — прогоном под node."""

    def test_the_rules_hold(self):
        got = subprocess.run(['node', str(ROOT / 'tests/test_text_translate.mjs')],
                             capture_output=True, text=True, cwd=ROOT, timeout=120)

        assert got.returncode == 0, f'сторож модуля перевода красный:\n{got.stdout}\n{got.stderr}'
        assert 'ВСЁ ЗЕЛЁНОЕ' in got.stdout, got.stdout


class TestTheDoorIsTheOnlyWay:
    """⚠ К соседу ходим **одной дверью**: своего HTTP к нему в проекте нет."""

    def test_the_page_asks_our_handle_not_the_neighbour(self):
        """Страница зовёт **нашу** ручку, а не соседскую: токен соседа в
        браузер не уезжает."""
        page = (ROOT / 'data/uvicorn/template/admin/dating/profile.html').read_text()

        assert '/api/panel/translate' in page, 'страница больше не зовёт нашу ручку перевода'

    def test_the_marks_are_masked_by_the_page(self):
        """⚠⚠ Заслонка меток — **довод страницы**, а не умолчание каркаса:
        каркас не знает, какие метки бывают у проекта."""
        page = (ROOT / 'data/uvicorn/template/admin/dating/profile.html').read_text()

        assert 'marks:' in page, 'страница перестала заслонять метки от модели'

    def test_the_shared_module_knows_no_project(self):
        """⚠⚠ В **коде** общего модуля нет ни нашей ручки, ни наших меток:
        зашей их — и он перестанет быть переносимым.

        ⚠ Сверяется именно код: в пояснениях проектное имя законно и даже
        нужно — метка `[[companion_name]]` там пример того, что сломается без
        заслонки. Та же вольность у соседних общих модулей: пример подключения
        `cronicle` называет `AuthPermission.CRONICLE_VIEW`.
        """
        code = _no_comments((ROOT / 'dashboard/js/text_translate.js').read_text())

        for own in ('/api/panel/translate', 'companion_name', 'girl_name', 'dating'):
            assert own not in code, f'в код общего модуля уехало наше: {own}'

    def test_the_shared_handle_knows_no_translator(self):
        """То же про бэкенд: чем переводить — знание установки, и в коде ручки
        имени переводчика быть не должно."""
        code = (ROOT / 'dashboard/src/panel/panel_translate_api.py').read_text()
        code = _no_docstrings(code)

        assert 'rhapsody' not in code.lower(), 'в код общей ручки уехал наш переводчик'

        # ⚠ Импорты только из каркаса и библиотек. `from src.…` — это корень
        # **проекта**, и такой импорт у общего модуля означал бы, что он
        # переносится только вместе с нами. `dashboard.src.…` законно: это он сам.
        ours = [line for line in code.split('\n')
                if line.startswith(('import src.', 'from src.'))]

        assert not ours, f'общая ручка импортирует проектное: {ours}'


class TestTheHandleRefusesWithoutATranslator:
    """⚠ Переводчика не дали — отказ **на сборке**, а не на первом запросе.

    Установка узнает об этом при старте, а не через неделю от человека, у
    которого кнопка молчит.
    """

    def test_building_without_a_translator_raises(self):
        from dashboard.src.panel.panel_translate_api import panel_translate_api_router

        with pytest.raises(ValueError, match='translate'):
            panel_translate_api_router()
