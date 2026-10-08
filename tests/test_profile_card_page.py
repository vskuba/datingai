"""Окно правки персональных данных: иконка, поля, предупреждения, привязка.

## ⚠⚠ Читается файл шаблона, а не ответ панели

Тот же довод, что в `test_gallery_page.py`: поднятая панель бежит ту ветку, из
которой её запустили, и по HTTP этот тест краснел бы у всякого, кто проверяет
ветку, не перезапустив панель. Проверять надо то, что **ветка поставляет**, а это
файл на диске. Ошибки рендера ловит `test_pages_open`.

## ⚠ Нарисованное поле без привязки тише сломанного теста

Иконка, ни к чему не привязанная, выглядит рабочей и молчит. Поэтому проверяется и
разметка, и то, что окно зовёт ручку, и что после удачи перечитываются **оба**
списка — строка списка и карточка.
"""

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
    assert PAGE_FILE.exists(), f'шаблона нет: {PAGE_FILE}'

    return PAGE_FILE.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def css() -> str:
    return CSS_FILE.read_text(encoding='utf-8')


def test_card_head_has_the_edit_icon(page):
    """Иконка стоит в шапке карточки — там, где показано правимое."""
    assert 'id="cardEditBtn"' in page, 'иконки правки в шапке карточки нет'
    assert 'cardEditModal()' in page, 'иконка ни к чему не привязана'


def test_window_has_all_six_fields(page):
    """Шесть полей, и ни одним меньше: окно — единственный вход к ним."""
    for name in ('cardLogin', 'cardUid', 'cardName', 'cardAge', 'cardCity',
                 'cardAbout'):
        assert f'id="{name}"' in page, f'поля {name} в окне нет'


def test_window_calls_the_handle(page):
    """Ручка та, и тело несёт имена, которые она принимает."""
    assert '/card`, send)' in page or "/card`, send)" in page, \
        'окно не зовёт PUT …/card'
    for key in ('login:', 'profile_id:', 'name:', 'age:', 'city:', 'about:'):
        assert key in page, f'в теле запроса нет {key}'


def test_success_reloads_both_lists(page):
    """⚠ Перечитываются **оба**: правка меняет строку списка (почта, имя,
    возраст, номер) не меньше, чем карточку, а список приходит своей ручкой."""
    keep = page[page.index('cardEditKeep'):]
    assert 'profilesLoad()' in keep, 'список не перечитывается — строка устареет'
    assert 'profileLoad()' in keep, 'карточка не перечитывается'


def test_key_warning_names_both_halves(page):
    """⚠⚠ Предупреждение про ключ фактов стоит под **двумя** полями.

    `girl_name` — имя латиницей строчными, вторая половина того же ключа
    `%girl_id%_%girl_name%`. Предупреди мы только про номер, человек поправил бы
    регистр имени и получил то же последствие, не узнав о нём.
    """
    assert 'id="cardUidWhy"' in page, 'под номером пояснения нет'
    assert 'id="cardNameWhy"' in page, 'под именем пояснения нет'
    assert 'незнакомой' in page, 'пояснение не говорит, что произойдёт'


def test_forget_link_is_wired(page):
    """Отметка снимаемая: ссылка есть и зовёт свою ручку."""
    assert 'data-forget=' in page, 'ссылки «вернуть сайту» нет'
    assert 'deleteData(' in page and '/card/' in page, 'ссылка ни к чему не ведёт'


def test_backdrop_is_shared_not_copied(css, page):
    """⚠ Подложка одна на все окна раздела, и зовётся общим именем.

    Класс `gallery-view` у окна с почтой и описанием читался бы как ошибка, а
    вторая такая же подложка рядом разъехалась бы с первой на первой правке фона.
    """
    assert '.modal-back {' in css, 'общей подложки в CSS нет'
    assert '.modal-box {' in css, 'общего каркаса окна в CSS нет'
    assert '.gallery-view' not in css, 'прежнее имя подложки осталось'
    assert '.gallery-view' not in page, 'страница держится за прежнее имя подложки'


def test_gallery_window_uses_the_shared_shell(page):
    """Окно галереи переехало на общий каркас, а не осталось своим.

    ⚠ Прежнего `gallery-modal` в разметке больше нет намеренно: класс нёс
    единственную строку `cursor: default`, и она стала умолчанием общей
    подложки. Имя, за которым не стоит ни одного правила, сторожить нечем.
    """
    assert 'modal-box gallery-card' in page, 'окно галереи не на общем каркасе'
    assert 'gallery-modal' not in page, 'мёртвый класс остался в разметке'


def test_about_height_is_in_css_not_rows(css):
    """⚠ Общий слой ставит `textarea { height: 60px }`, и оно бьёт любой `rows`:
    описание в девять строк выходило в одну. Та же грабля уже была у окна снимка."""
    assert '#cardAbout' in css, 'высота описания не задана в CSS'


def test_styles_live_in_the_shared_sheet(css, page):
    """Размеры — в общей таблице стилей раздела, а не в разметке."""
    for name in ('.modal-back', '.modal-box', '.modal-field', '.modal-hint',
                 '.modal-edited', '.card-edit'):
        assert name in css, f'{name} нет в общей таблице стилей'
        assert f'{name} {{' not in page, f'{name} описан в разметке, а не в CSS'


def test_script_has_no_syntax_error(page):
    """Скрипт страницы цел как браузерный модуль.

    ⚠ Без этого правка разметки роняет страницу целиком, а тест разметки остаётся
    зелёным: он читает текст, а не исполняет его.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден в PATH — проверка синтаксиса пропущена')

    body = re.search(r'<script type="module">(.*?)</script>', page, re.S)
    assert body, 'модульного скрипта на странице нет'

    # ⚠ Выражения Jinja подменяются заглушкой: `node` их не понимает, а на
    # синтаксис JS они не влияют. Расширение `.mjs`, иначе `node --check` читает
    # файл как обычный скрипт и спотыкается на `import`.
    #
    # ⚠⚠ Заглушка — голое слово `stub`, без кавычек: подстановки на странице
    # стоят **внутри** существующих кавычек (`from '{{ static_url(...) }}';`),
    # и замена на `''` дала бы `from '''';` — разбитый синтаксис при любой
    # разметке. Тот же приём — в `test_gallery_page.py`.
    source = re.sub(r"\{\{.*?\}\}", 'stub', body.group(1))
    source = re.sub(r"\{#.*?#\}", '', source, flags=re.S)
    source = re.sub(r"\{%.*?%\}", '', source, flags=re.S)

    with tempfile.NamedTemporaryFile('w', suffix='.mjs', delete=False,
                                     encoding='utf-8') as tmp:
        tmp.write(source)
        path = tmp.name

    got = subprocess.run([node, '--check', path], capture_output=True, text=True)
    Path(path).unlink(missing_ok=True)

    assert got.returncode == 0, f'скрипт не проходит `node --check`: {got.stderr}'
