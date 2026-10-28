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

    # ⚠ Подложка и каркас окна зовутся общими именами: их делят кадр галереи,
    # правка снимка и окно персональных данных анкеты. Своё у галереи осталось
    # только то, что и правда её — ширина окна и высоты полей.
    for name in ('.gallery-grid', '.gallery-thumb', '.gallery-group',
                 '.dating-modal-back', '.dating-modal-box', '.gallery-card',
                 '.gallery-why', '.gallery-drop'):
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


def test_carousel_scrolls_through_photos(page):
    """⚠ Кадр листается: стрелки, клавиши и счёт позиции.

    Прежде просмотр показывал один снимок и закрывался по щелчку — вернуться к
    соседнему можно было только закрыв и открыв заново.
    """
    block = page[page.index('function galleryFrame('):page.index('function galleryModal(')]

    assert 'gallery-step' in block, 'нет кнопок листания'
    assert "'ArrowRight'" in block and "'ArrowLeft'" in block, 'клавиши не слушаются'
    assert "'Escape'" in block, 'Esc перестал закрывать'
    assert 'из ${photos.length}' in block, 'не видно, какой кадр по счёту'


def test_carousel_takes_photos_only(page):
    """⚠ Видео в ленту не берётся: его полный кадр отдаёт просмотрщик Диска."""
    block = page[page.index('function galleryPhotos('):page.index('function galleryFrame(')]

    assert "kind === 'image'" in block, 'в ленту попадёт видео, которое нечем показать'


def test_carousel_preloads_neighbours(page):
    """Соседние кадры подгружаются заранее — иначе стрелка показывает пустоту."""
    block = page[page.index('function galleryFrame('):page.index('function galleryModal(')]

    assert 'new Image()' in block, 'нет предзагрузки соседей'


def test_carousel_clicks_do_not_close_it(page):
    """⚠ Нажатие по стрелке и по кадру не закрывает просмотр.

    Подложка закрывается щелчком, и без остановки всплытия каждое листание
    выбрасывало бы человека из карусели.
    """
    block = page[page.index('function galleryFrame('):page.index('function galleryModal(')]

    assert 'stopPropagation' in block


def test_carousel_styles_live_in_the_sheet(page):
    """Размеры карусели — в общей таблице стилей, а не в разметке."""
    css = CSS_FILE.read_text(encoding='utf-8')

    for name in ('.gallery-carousel', '.gallery-frame', '.gallery-step'):
        assert name in css, f'{name} нет в общей таблице стилей'
        assert f'{name} {{' not in page, f'{name} описан в разметке, а не в CSS'


def test_info_button_is_not_buried_under_the_caption(page):
    """⚠⚠ Кнопка описания не должна уходить под полосу имени.

    Было: кнопка занимала 4–28 px снизу, полоса имени 0–17, и 13 пикселей из
    24 оказывались под подписью — `elementFromPoint` в середине кнопки
    возвращал `gallery-name`, то есть нажатие доставалось не ей.

    Держится это на двух вещах, и проверяем обе: высота полосы задана
    переменной, а отступ кнопки считается от неё же. Разойдись они — кнопка
    снова уедет под подпись.
    """
    css = CSS_FILE.read_text(encoding='utf-8')

    assert '--gallery-name-h' in css, 'высота полосы не названа переменной'
    assert 'calc(var(--gallery-name-h)' in css, (
        'отступ кнопки задан мимо переменной — величины разойдутся')

    strip = css[css.index('.gallery-name {'):css.index('}', css.index('.gallery-name {'))]
    assert 'pointer-events: none' in strip, (
        'подпись ловит нажатия, хотя это текст, а не кнопка')


def test_the_buttons_say_they_are_working(page):
    """⚠⚠ Нажатие обязано отзываться — молчание читается как поломка.

    Обе кнопки прежде говорили только об отказе (`alert`), а об удаче — ничем:
    список перерисовывался тем же самым, и человек жал повторно, думая, что
    промахнулся. Обход Диска при этом идёт секундами, и всё это время кнопка
    выглядела нетронутой.
    """
    assert 'galleryBusy(' in page, 'кнопки не показывают, что заняты'
    assert 'Обхожу Диск…' in page, 'обход идёт молча'
    assert 'Сохраняю…' in page, 'сохранение идёт молча'

    walk = page[page.index('function galleryBind'):]
    assert 'showNotification' in walk[:4000], \
        'об удаче по-прежнему не сообщается'
    assert 'alert(' not in walk[:4000], \
        'alert останавливает страницу — об удаче так не говорят'


def test_the_walk_result_is_spelled_out(page):
    """⚠ Числа обхода показываются словами, а не теряются.

    «Ничего нового» и «обход не дошёл» для человека выглядят одинаково, если не
    сказать вслух: ручка возвращает `found`/`added`/`gone`/`back`, и они нужны
    именно затем.
    """
    assert 'function galleryWalkSays' in page, 'итог обхода не пересказывается'

    says = page[page.index('function galleryWalkSays'):]
    for field in ('found', 'added', 'gone', 'back'):
        assert field in says[:900], f'число «{field}» не показывается'


def test_the_buttons_explain_what_they_do(page):
    """⚠ «Сохранить» и «Обновить» делают разное, и по названиям это не читается.

    Первая запоминает папку у анкеты и на Диск не ходит; вторая идёт на Диск и
    пересобирает список файлов. Их путали.
    """
    head = page[page.index('function galleryHead'):]
    head = head[:head.index('function galleryGroups')]

    assert 'title="Запомнить выбранную папку' in head, \
        'у «Сохранить» нет подсказки'
    assert 'title="Обойти папку на Диске' in head, \
        'у «Обновить» нет подсказки'
    assert '<b>Сохранить</b>' in head and '<b>Обновить</b>' in head, \
        'под кнопками не сказано, чем они отличаются'


def test_the_tag_click_rebuilds_the_field_in_dictionary_order(page):
    """⚠⚠ Щелчок по тегу **пересобирает** поле, а не дописывает в конец.

    Порядок держим словарный: так теги одного снимка сравнимы глазом с тегами
    соседнего. Проверено живым нажатием — набор при двойном щелчке возвращается,
    а строка нормализуется: `dress, indoor, smiling, studio` становится
    `dress, indoor, studio, smiling` (место идёт раньше настроения).

    ⚠ Дописывание в конец выглядело бы так же в коде и ломало бы именно это:
    порядок стал бы порядком нажатий, разным у каждого снимка.
    """
    box = page[page.index('async function galleryTagBox'):]
    box = box[:box.index('function galleryModal')]

    assert 'flatMap' in box, 'поле не пересобирается по словарю'
    assert 'now.has(one)' in box, 'выбранные отбираются не по словарю'
    assert '+= ' not in box and 'push(tag)' not in box, \
        'тег дописывается в конец — порядок станет порядком нажатий'


def test_the_tag_box_repaints_after_a_click(page):
    """⚠ После щелчка подсветка перерисовывается: иначе поле и слова разойдутся.

    Проверено живым нажатием: горящих было 4, стало 5, после повторного щелчка
    снова 4.
    """
    box = page[page.index('async function galleryTagBox'):]
    box = box[:box.index('function galleryModal')]

    # ⚠ Третий зов — ссылкой, без скобок (`addEventListener('input', paint)`),
    # поэтому считать `paint()` мало: так выглядят только два первых.
    assert box.count('paint()') >= 2, \
        'перерисовка не зовётся ни после щелчка, ни при первом показе'
    assert "field.addEventListener('input', paint)" in box, \
        'правка руками не подсвечивает слова — набранное разойдётся с горящим'


def test_the_trash_can_asks_before_it_deletes(page):
    """⚠⚠ Удаление записи необратимо, и вопрос задаётся **всегда**.

    Та же договорённость, что у очистки пула (`pool.js`): необратимое без
    вопроса — это промах мышью ценой чужой работы. Здесь цена измеримая:
    у снимка с описанием повторный разбор стоит вызова модели.
    """
    box = page[page.index('async function galleryDrop'):]
    box = box[:box.index('function galleryFile')]

    assert 'confirm(' in box, 'запись удаляется без вопроса'
    assert 'deleteData(' in box, 'строка убирается не своей дверью'


def test_the_question_warns_that_the_file_comes_back(page):
    """⚠⚠ Вопрос обязан назвать возврат, а не только «исчезнет».

    Файл, оставшийся в индексируемой папке, вернёт ближайший обход — уже без
    описания и тегов. Скажи кнопка только «исчезнет» — человек счёл бы возврат
    поломкой, а повторную трату на модель не заметил бы вовсе. Обещание кнопки
    держит тест двери `test_a_live_file_comes_back_on_the_next_walk`.
    """
    box = page[page.index('async function galleryDrop'):]
    box = box[:box.index('function galleryFile')]
    question = box[box.index('confirm('):box.index(')) return')]

    assert 'вернёт' in question, 'вопрос молчит о возврате файла обходом'
    assert 'описания' in question, 'вопрос молчит о потере описания'


def test_the_trash_can_does_not_open_the_frame(page):
    """⚠ Щелчок по корзинке не доходит до плитки — иначе поверх вопроса
    распахнулась бы карусель.

    Тот же приём, что у кнопки описания: `stopPropagation` на своей кнопке.
    """
    box = page[page.index(".querySelectorAll('.gallery-drop')"):]
    box = box[:box.index('async function galleryDrop')]

    assert 'stopPropagation' in box, 'щелчок дойдёт до плитки и откроет кадр'


def test_the_trash_can_is_opposite_the_info_button(page):
    """⚠⚠ Корзинка и кнопка описания — в разных углах, и это не вкусовщина.

    Действия разной цены рядом — промах, который здесь стоит вызова модели
    заново. Проверяем по сторонам в стилях: одна у левого края, другая у
    правого, высота у обеих от той же переменной полосы имени.
    """
    css = CSS_FILE.read_text(encoding='utf-8')
    drop = css[css.index('.gallery-drop {'):css.index('}', css.index('.gallery-drop {'))]
    info = css[css.index('.gallery-info {'):css.index('}', css.index('.gallery-info {'))]

    assert 'left:' in drop and 'right:' not in drop, 'корзинка не у левого края'
    assert 'right:' in info and 'left:' not in info, 'кнопка описания не у правого'
    assert 'calc(var(--gallery-name-h)' in drop, \
        'корзинка уедет под подпись — отступ задан мимо переменной'


def test_folders_are_tabs_not_a_stack(page):
    """⚠⚠ Папки показываются **вкладками**, а не списком подряд.

    Списком `intim` оказывался под двумя десятками плиток `public`: чтобы его
    увидеть, надо было знать, что он там есть, и прокрутить. Вкладка называет
    имя и число файлов сразу.
    """
    box = page[page.index('function galleryGroups'):]
    box = box[:box.index('function galleryGroupShown')]

    assert 'card-tabs gallery-tabs' in box, 'группы рисуются не вкладками'
    assert 'gallery-count' in box, 'на вкладке нет числа файлов'
    assert '.gallery-group-head' not in box, 'остался заголовок списка'


def test_only_the_open_folder_is_drawn(page):
    """Сетка рисует одну группу — открытую, а не все подряд.

    Иначе вкладки были бы украшением над тем же самым списком.
    """
    box = page[page.index('function galleryGroups'):]
    box = box[:box.index('function galleryGroupShown')]

    assert 'groups.find(' in box, 'рисуются все группы, а не выбранная'


def test_the_open_folder_survives_a_repaint_but_not_a_new_profile(page):
    """⚠ Выбор переживает перерисовку и **не** переживает смену анкеты.

    Перерисовка бывает от обхода Диска и удаления плитки — сбрасывать на ней
    значило бы возвращать человека в `public` посреди работы. А раскладка у
    каждой девушки своя, и «остаться на intim» после перехода читалось бы как
    выбор, которого он не делал.
    """
    shown = page[page.index('function galleryGroupShown'):]
    shown = shown[:shown.index('function galleryTile')]
    assert 'names.includes(galleryGroup)' in shown, \
        'выбранная папка не переживает перерисовку'
    assert 'names[0]' in shown, 'нет возврата к первой группе'

    load = page[page.index('async function galleryLoad'):]
    load = load[:load.index('function galleryPaint')]
    assert "galleryGroup = ''" in load, 'смена анкеты не сбрасывает папку'


def test_switching_a_tab_does_not_refetch(page):
    """⚠ Вкладка переключает показ, а не источник: данные уже в руках.

    Поход за ними заново стоил бы двух запросов на каждое нажатие — и мигания
    сетки там, где ничего не изменилось.
    """
    box = page[page.index(".querySelectorAll('.gallery-tabs .card-tab')"):]
    box = box[:box.index(".querySelectorAll('.gallery-drop')")]

    assert 'galleryPaint()' in box, 'после переключения сетка не перерисована'
    assert 'galleryLoad(' not in box, 'переключение вкладки лезет на сервер'


def test_the_badge_hides_when_there_are_no_photos(page):
    """⚠⚠ Значок показывается только у анкет с галереей.

    Ноль у остальных дал бы столбец нулей на девятнадцать строк, в котором
    четыре нужных числа перестают быть заметными. Отсутствие значка и есть
    ответ «фотографий нет».
    """
    box = page[page.index('function galleryBadge'):]
    box = box[:box.index('// Сколько строк влезает')]

    assert "if (!all) return ''" in box, 'значок рисуется и при нулевой галерее'
    assert 'gallery:' in box, 'в значке нет подписи, по которой его ищут глазами'


def test_the_badge_shows_described_out_of_total(page):
    """Два числа: разобрано из всего — иначе непонятно, ждать ли описаний."""
    box = page[page.index('function galleryBadge'):]
    box = box[:box.index('// Сколько строк влезает')]

    assert '${done}/${all}' in box, 'показывается не «разобрано из всего»'
    assert 'row.gallery_described' in box, 'число разобранных не читается из строки'


def test_the_badge_counts_come_from_the_server(page):
    """⚠ Страница не ходит за галереей каждой анкеты — числа приходят со списком.

    Иначе список из девятнадцати строк стоил бы девятнадцати запросов ради
    двух чисел в каждой.
    """
    box = page[page.index('function galleryBadge'):]
    box = box[:box.index('// Сколько строк влезает')]

    assert 'getData(' not in box, 'значок лезет на сервер за своими числами'


def test_the_badge_style_lives_in_the_page(page):
    """Разобранная целиком и недоразобранная различаются видом, а не только
    числом: «работа кончилась» должно читаться с одного взгляда."""
    assert '.prof-gallery.done' in page, 'нет отдельного вида у разобранной галереи'
    assert '.prof-off + .prof-gallery' in page, \
        'два значка рядом разъедутся: оба тянут margin-left: auto'
