"""Вкладка «Галерея» в карточке анкеты: три вкладки, привязка, целый скрипт.

## ⚠⚠ Читается файл шаблона, а не ответ панели

Остальные тесты страниц ходят по HTTP — и правильно делают: так ловятся ошибки
самого рендера. Здесь иначе, и причина не в удобстве: поднятая панель бежит ту
ветку, из которой её запустили, и по HTTP этот тест краснел бы у всякого, кто
проверяет ветку, не перезапустив панель. Проверять надо **то, что ветка
поставляет**, а это файл на диске.

Ошибки рендера при этом не теряются: их ловит `test_pages_open` и соседние
тесты, открывающие страницу целиком.

## ⚠ Нарисованная вкладка без привязки тише сломанного теста

Поэтому проверяется не только кнопка, но и ветка `cardTab === 'gallery'`, и то,
что она зовёт загрузку. Кнопка, ни к чему не привязанная, выглядит рабочей и
молчит.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.page

PAGE_FILE = Path('data/uvicorn/template/admin/dating/index.html')
CSS_FILE = Path('data/uvicorn/public/css/dating_page.css')


@pytest.fixture(scope='module')
def page() -> str:
    """Шаблон карточки анкеты как его поставляет ветка."""
    assert PAGE_FILE.exists(), f'шаблона нет: {PAGE_FILE}'

    return PAGE_FILE.read_text(encoding='utf-8')


def test_card_has_three_tabs(page):
    """Три вкладки, и «Галерея» среди них — третьей, после «Переменных»."""
    tabs = re.findall(r"data-tab=\"(main|var|gallery)\">([^<]+)</button>", page)
    names = [name for _, name in tabs]

    assert names.count('Общая информация') >= 1
    assert names.count('Переменные') >= 1
    assert names.count('Галерея') == 1, 'вкладка «Галерея» должна быть ровно одна'

    # Порядок: «Галерея» после «Переменных» в карточке анкеты.
    girl = page[page.index('data-tab="main">Общая информация'):]
    assert girl.index('data-tab="var"') < girl.index('data-tab="gallery"')


def test_gallery_tab_is_wired(page):
    """⚠ Кнопка привязана: есть ветка показа и вызов загрузки."""
    assert "cardTab === 'gallery'" in page, 'ветки показа нет — вкладка пустая'
    assert 'galleryLoad(requestId' in page, 'загрузка не зовётся'
    assert 'id="galleryBox"' in page, 'некуда рисовать'


def test_gallery_asks_its_own_handles(page):
    """Вкладка ходит в свои ручки, а не в чужие."""
    for path in ('/api/dating/gallery/${profileId}',
                 '/api/dating/gallery/folders',
                 '/img/${file.id}?w=400',
                 '/sync'):
        assert path in page, f'нет обращения к {path}'


def test_race_is_guarded(page):
    """⚠ Гонка запросов: ответ по снятой анкете не должен подписать выбранную."""
    block = page[page.index('async function galleryLoad'):page.index('function galleryPaint')]

    assert 'currentId !== profileId' in block, (
        'нет сверки номера анкеты — щелчок по другой подпишет её чужими плитками')


def test_video_opens_viewer_not_us(page):
    """Полный кадр видео отдаёт просмотрщик Диска, а не панель."""
    assert "dataset.kind === 'video'" in page
    assert 'window.open(tile.dataset.view' in page


def test_styles_live_in_the_shared_sheet(page):
    """Размеры — в общей таблице стилей раздела, а не в разметке."""
    css = CSS_FILE.read_text(encoding='utf-8')

    for name in ('.gallery-grid', '.gallery-thumb', '.gallery-group',
                 '.gallery-view', '.gallery-card', '.gallery-why'):
        assert name in css, f'{name} нет в общей таблице стилей'
        assert f'{name} {{' not in page, f'{name} описан в разметке, а не в CSS'


def test_script_has_no_syntax_error(page):
    """Скрипт страницы цел как браузерный модуль.

    ⚠ Выражения Jinja подменяются заглушкой: `node` их не понимает, а на
    синтаксис JS они не влияют. Расширение `.mjs`, иначе `node --check`
    разобрал бы файл как CommonJS и упал бы на первом же `import`.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден в PATH — проверка синтаксиса пропущена')

    blocks = re.findall(r'<script[^>]*>(.*?)</script>', page, re.S)
    script = max(blocks, key=len)
    assert len(script) > 10000, 'найден не тот блок — скрипт страницы крупнее'
    assert 'galleryPaint' in script, 'в найденном блоке нет кода галереи'

    script = re.sub(r'\{\{.*?\}\}', 'stub', script)
    script = re.sub(r'\{%.*?%\}', '', script, flags=re.S)

    fd, path = tempfile.mkstemp(suffix='.mjs')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(script)
        got = subprocess.run([node, '--check', path], capture_output=True, text=True)
        assert got.returncode == 0, f'скрипт не проходит `node --check`: {got.stderr}'
    finally:
        os.remove(path)
