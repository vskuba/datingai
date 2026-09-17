"""Модуль страницы обязан **запускаться**, а не только разбираться.

⚠⚠ **Написано по живой поломке, которую сюита пропустила целиком.** Правка
добавила окно времени в статистику, и `let statsHours` прочитал константу
`STATS_HOURS_KEY` семью строками выше её объявления. Синтаксис безупречен,
`node --check` молчит — а в браузере это `ReferenceError: can't access lexical
declaration before initialization`, и валит он **весь** модуль: страница
«Профили» открылась пустой. Увидел это заказчик, в консоли.

Здесь страница запускается в пустом DOM с заглушками (`tests/js/page_boot.mjs`).
Проверяется ровно одно: модуль дошёл до конца, не бросив. Этого хватает, чтобы
поймать целый род ошибок, невидимых разбору:

* чтение `const`/`let` до объявления;
* вызов функции, объявленной ниже через `const`;
* опечатку в имени, которая срабатывает на верхнем уровне.

⚠ **Не замена браузеру.** Что нарисовано и что случится по нажатию, отсюда не
видно — на это есть снимки стенда и глаза. Здесь только «страница вообще
завелась».
"""

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.page

# Страницы со своим модулем. ⚠ Список руками, а не обходом каталога: у шаблонов
# сайта (`profile/site/*.html`) своего скрипта нет, и запускать в них нечего.
PAGES = ('data/uvicorn/template/admin/dating/profile.html',
         'data/uvicorn/template/admin/dating/index.html')

HARNESS = 'tests/js/page_boot.mjs'


@pytest.mark.parametrize('page', PAGES)
def test_the_module_boots(page):
    """Модуль исполняется до конца. Упал — страница не откроется вовсе."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node не установлен — стенд загрузки пропускается')

    got = subprocess.run([node, HARNESS, page], capture_output=True, text=True,
                         timeout=60)

    assert got.returncode == 0, \
        f'{page} не загрузился:\n{got.stderr.strip() or got.stdout.strip()}'


def test_the_harness_catches_a_dead_zone(tmp_path):
    """⚠ Проверка самой проверки: стенд обязан ловить чтение `const` до объявления.

    Без этого он мог бы молча «проходить» на чём угодно — и мы верили бы зелёному
    там, где страница мертва. Поломку вносим ту же, что была живьём.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('node не установлен — стенд загрузки пропускается')

    page = tmp_path / 'broken.html'
    page.write_text(
        '<script type="module">\n'
        '  let early = SOME_KEY;\n'
        "  const SOME_KEY = 'x';\n"
        '  console.log(early, SOME_KEY);\n'
        '</script>', encoding='utf-8')

    got = subprocess.run([node, HARNESS, str(page)], capture_output=True, text=True,
                         timeout=60)

    assert got.returncode != 0, 'стенд не заметил чтения до объявления'
    assert 'before initialization' in (got.stderr + got.stdout)
