"""⚠⚠ Форма переменной ищет свои поля от **существующей** опоры.

`varSave` падала `ReferenceError: profileEl is not defined` на первом же
действии — имя `profileEl` встречалось на всей странице ровно один раз, в самой
сломанной строке, и нигде не объявлялось.

⚠ Вид беды определялся тем, что бросок случался **до** `try`: ни сохранения, ни
уведомления, ни красной строки под формой — нажатие «Сохранить» не делало
ничего и молчало об этом. Внутри `try` та же ошибка написала бы причину.

Отсюда сторож из двух половин: опоры-призрака нет, а поля ищутся от `workEl` —
той же опоры, от которой ищет вся страница (`slot()`).
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.page


def _page() -> str:
    return (pathlib.Path(__file__).resolve().parents[1]
            / 'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')


class TestTheFormAsksARealRoot:
    def test_there_is_no_ghost_root(self):
        """Опоры `profileEl` на странице нет — и в коде её быть не должно."""
        code = re.sub(r'//.*', '', _page())

        assert 'profileEl' not in code, \
            'вернулась опора profileEl, которой на странице не существует'

    def test_the_checkboxes_are_asked_from_the_page_root(self):
        """Галочки занятий ищутся от `workEl`, как и всё остальное на странице."""
        assert "workEl.querySelectorAll('.var-uses input[data-use]')" in _page(), \
            'галочки занятий ищутся не от workEl — опора разошлась с остальной страницей'

    def test_every_root_used_for_queries_exists(self):
        """⚠ Общая проверка: всё, у чего страница спрашивает `querySelector`,
        где-то объявлено. Ровно эта порода беды и случилась."""
        code = re.sub(r'//.*', '', _page())
        roots = set(re.findall(r'\b([a-zA-Z_][\w]*)\.querySelector(?:All)?\(', code))

        known = set(re.findall(r'\b(?:const|let|var|function)\s+([a-zA-Z_][\w]*)', code))
        known |= {'document', 'doc', 'el', 'row', 'box', 'node', 'root', 'frame',
                  'item', 'form', 'pane', 'wrap', 'card', 'block', 'parent', 'body',
                  'host', 'head', 'table', 'list', 'cell', 'line', 'tab', 'panel'}

        unknown = {r for r in roots if r not in known}

        assert not unknown, f'у этих опор спрашивают узлы, а объявления нет: {sorted(unknown)}'
