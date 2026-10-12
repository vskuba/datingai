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


def test_the_edit_lives_in_its_own_tab(page):
    """⚠⚠ Правка — **вкладка**, а не окно по иконке.

    Полей стало 27, и в окне они шли двумя узкими колонками с прокруткой:
    половина параметров была не видна без неё. Вкладке достаётся вся ширина
    карточки.

    ⚠ Иконки ✎ в шапке больше нет намеренно: два входа в одно и то же место
    расходятся в поведении на первой же правке.
    """
    assert "data-tab=\"info\">Информация</button>" in page, 'вкладки «Информация» нет'
    assert "cardTab === 'info' ? infoBlock(data)" in page, 'вкладка ничего не рисует'
    assert 'infoBind(data)' in page, 'поля вкладки ни к чему не привязаны'
    assert 'id="cardEditBtn"' not in page, 'иконка осталась — вход должен быть один'
    assert 'cardEditModal' not in page, 'окно правки осталось в коде'


def test_the_background_probe_leaves_the_form_alone(page):
    """⚠⚠ Фоновый опрос идёт раз в пятнадцать секунд и собирает карточку заново.

    На вкладке это стёрло бы набранное **прямо под руками** и без следа: поля
    строятся из ответа ручки. У окна такой беды не было — оно жило отдельно от
    карточки, и перерисовка его не касалась.
    """
    assert "if (cardTab !== 'info') await profileLoad();" in page, \
        'перерисовка по таймеру затрёт набранное в полях вкладки'


def test_window_has_all_six_fields(page):
    """Шесть полей, и ни одним меньше: окно — единственный вход к ним."""
    for name in ('cardLogin', 'cardUid', 'cardName', 'cardAge', 'cardCity',
                 'cardAbout'):
        assert f'id="{name}"' in page, f'поля {name} в окне нет'


def test_the_tab_calls_the_handle(page):
    """Ручка та, и тело несёт имена, которые она принимает.

    ⚠ Тело собирает **одна** функция `fields()` — её шлют и «Сохранить», и
    «Обновить био». Две копии сборки разошлись бы на первой же новой строке
    формы, и предпросмотр показывал бы не то, что сохранится.
    """
    assert '/card`, fields())' in page, 'вкладка не зовёт PUT …/card'
    assert '/card/bio`, fields())' in page, 'кнопка «Обновить био» не зовёт ручку'
    assert page.count('const fields = () => ({') == 1, \
        'тело запроса собирается не одной функцией'

    for key in ('login:', 'profile_id:', 'name:', 'age:', 'city:', 'about:',
                'parameters:'):
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
    keep = page[page.index('function infoBlock'):page.index('function infoBind')]

    assert keep.count('<b data-move></b>') == 2, \
        'переход называется не у обоих полей ключа'
    assert 'незнакомой' in keep, 'пояснение не говорит, что произойдёт'
    assert 'ключа фактов' in keep, 'про вторую половину ключа не сказано'


def test_forget_link_is_wired(page):
    """Отметка снимаемая: ссылка есть и зовёт свою ручку."""
    assert 'data-forget=' in page, 'ссылки «вернуть сайту» нет'
    assert 'deleteData(' in page and '/card/' in page, 'ссылка ни к чему не ведёт'


def test_backdrop_is_shared_not_copied(css, page):
    """⚠ Подложка одна на все окна раздела, и зовётся общим именем.

    Класс `gallery-view` у окна с почтой и описанием читался бы как ошибка, а
    вторая такая же подложка рядом разъехалась бы с первой на первой правке фона.
    """
    assert '.dating-modal-back {' in css, 'общей подложки в CSS нет'
    assert '.dating-modal-box {' in css, 'общего каркаса окна в CSS нет'
    assert '.gallery-view' not in css, 'прежнее имя подложки осталось'
    assert '.gallery-view' not in page, 'страница держится за прежнее имя подложки'


def test_gallery_window_uses_the_shared_shell(page):
    """Окно галереи переехало на общий каркас, а не осталось своим.

    ⚠ Прежнего `gallery-modal` в разметке больше нет намеренно: класс нёс
    единственную строку `cursor: default`, и она стала умолчанием общей
    подложки. Имя, за которым не стоит ни одного правила, сторожить нечем.
    """
    assert 'dating-modal-box gallery-card' in page, 'окно галереи не на общем каркасе'
    assert 'gallery-modal' not in page, 'мёртвый класс остался в разметке'


def test_about_height_is_in_css_not_rows(css):
    """⚠ Общий слой ставит `textarea { height: 60px }`, и оно бьёт любой `rows`:
    описание в девять строк выходило в одну. Та же грабля уже была у окна снимка."""
    assert '#cardAbout' in css, 'высота описания не задана в CSS'


def test_styles_live_in_the_shared_sheet(css, page):
    """Размеры — в общей таблице стилей раздела, а не в разметке."""
    for name in ('.dating-modal-back', '.dating-modal-box', '.dating-modal-field',
                 '.dating-modal-hint', '.dating-modal-edited', '.card-edit'):
        assert name in css, f'{name} нет в общей таблице стилей'
        assert f'{name} {{' not in page, f'{name} описан в разметке, а не в CSS'


def test_the_shared_modal_namespace_is_not_squatted(css, page):
    """⚠⚠ `modal-*` — пласт общего слоя, и занимать его нельзя.

    `dashboard/css/modal.css` подключён `base.html` **каждой** странице панели и
    объявляет `.modal-back` и `.modal-head`. Наш `dating_page.css` грузится
    позже и при равной специфичности побеждает — то есть правила раздела
    перекрашивали бы общие окна на «Профилях», где зовут общий `modal.js`.

    ⚠ Проверяем и разметку: класс, оставшийся в ней, тянет за собой правило
    общего слоя, которого мы не заказывали.
    """
    import re

    # Свои классы раздела — с приставкой `dating-`, как `.dating-btn` рядом.
    assert '.dating-modal-back {' in css, 'подложка не переименована'
    assert '.dating-modal-box {' in css, 'каркас окна не переименован'

    # ⚠ Ищем объявления правил, а не любые вхождения: слово `modal-` законно
    # встречается в комментариях, объясняющих, почему приставка нужна.
    squatted = re.findall(r'^\.modal-[a-z-]*', css, re.M)
    assert not squatted, f'правила в чужом пласте: {squatted}'

    assert 'class="modal-' not in page, 'класс чужого пласта остался в разметке'
    assert "'modal-back" not in page, 'класс чужого пласта остался в скрипте'


def test_windows_close_on_mousedown_not_click(page):
    """⚠⚠ Выделение текста не должно закрывать окно.

    Отпустив кнопку мыши за краем окна после выделения, человек получает `click`
    с целью «подложка» — и форма с шестью полями закрылась бы без вопроса.
    Общий `modal.js` ловит `mousedown` и сверяет цель именно поэтому
    (`dashboard/js/modal.js:77`).
    """
    assert "back.addEventListener('click'" not in page, \
        'окно закрывается по click — выделение текста его схлопнет'

    # ⚠ Два окна раздела: карусель и описание снимка. Правка персональных данных
    # окном быть перестала — она вкладка, и закрывать там нечего.
    assert page.count("back.addEventListener('mousedown'") == 2, \
        'не все окна переведены на mousedown со сверкой цели'
    assert page.count('e.target === back') >= 2 \
        or page.count('event.target === back') >= 2, \
        'цель события не сверяется — закроется от щелчка внутри окна'


def test_the_carousel_closes_on_escape(page):
    """⚠ Escape — то, что человек пробует первым, не найдя кнопки закрытия.

    ⚠ Окно правки персональных данных сюда больше не входит: оно стало вкладкой,
    и закрывать там нечего. Осталась карусель — у неё Escape есть.

    ⚠ Окно описания снимка Escape не ловит, и так было до этой правки: оно
    закрывается щелчком по подложке и кнопкой. Записано как факт, а не как
    требование — чинить это отдельно от своей задачи я не стал.
    """
    assert "event.key === 'Escape'" in page, 'карусель не закрывается по Escape'


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


def test_the_tab_classes_do_not_squat_a_used_name(css, page):
    """⚠⚠ Класс вкладки не должен совпасть с чужим — это уже стоило поломки.

    Окно правки завели под именем `.card-edit`, а так звалась **иконка** ✎ в
    шапке карточки. Правило окна `width: min(980px, 94vw)` село на кнопку: она
    растянулась на всю шапку (980 px вместо 20), выдавила содержимое и сама стала
    невидимой полосой. Сюита молчала — она читает разметку, а ширину никто не
    считал.

    Проверка механическая: у каждого класса `.info-*` из стиля должен быть
    **ровно один** владелец — и это вкладка. Имя, которое встречается в разметке
    вне вкладки, значит, что его делят двое.
    """
    import re

    body = page[page.index('function infoBlock'):page.index('function infoBind')]
    head = page[:page.index('function infoBlock')]

    for name in sorted({m for m in re.findall(r'\.(info-[a-z-]+)\b', css)}):
        # `info-when` стоит в шапке карточки — это её законное второе место:
        # отметка времени съёма уехала туда вместе с кнопкой.
        if name == 'info-when':
            continue

        assert name not in head, \
            f'класс `{name}` встречается вне вкладки — его делят двое'
        assert name in body or name in page, f'класс `{name}` в стиле мёртв'


def test_the_tab_classes_live_in_the_css(page, css):
    """⚠ Классы вкладки объявлены в стиле, а не придуманы в разметке.

    Разъехавшись, они дают форму без раскладки: 27 полей встают одно под другим
    в одну колонку, и выглядит это как «вёрстка поехала», а не как опечатка.
    """
    import re

    used = set(re.findall(r'info-[a-z-]+', page))
    assert used, 'в разметке вкладки нет ни одного класса `info-*`'

    for one in sorted(used):
        assert f'.{one}' in css, f'класс `{one}` есть в разметке, но не в стиле'


def test_the_tab_grid_is_not_a_fixed_column_count(css):
    """⚠ Колонки считает сетка (`auto-fill`), а не человек числом.

    Карточку открывают и на ноутбуке, и на широком мониторе: «три колонки» на
    первом дали бы поля шириной в 120 пикселей, где не читается ни подпись, ни
    значение.
    """
    assert 'repeat(auto-fill, minmax(' in css, \
        'сетка полей задана фиксированным числом колонок'


def test_the_tab_has_a_draggable_splitter(page, css):
    """⚠⚠ Разделитель колонок двигает **тот же** `splitDrag`, что делит список и
    карточку на странице.

    Своего второго механизма перетаскивания здесь нет и быть не должно: два
    обработчика `pointerdown` на одно дело разъехались бы в мелочах — в границах,
    в сохранении, в том, снимается ли захват указателя.
    """
    assert 'id="splitInfo"' in page, 'разделителя колонок нет'
    assert "splitDrag(split, '--info'" in page, \
        'разделитель не привязан к общему механизму перетаскивания'
    assert '.info-split' in css, 'разделитель не описан в стиле'


def test_the_splitter_is_visible_without_hover(css):
    """⚠⚠ Видна **всегда**, в отличие от разделителей страницы.

    Те стоят между белыми панелями с рамками — граница и так нарисована. Здесь по
    обе стороны пустое поле формы, и невидимый разделитель это возможность, о
    которой никто не узнает.
    """
    import re

    block = re.search(r'\.info-split\s*\{([^}]*)\}', css)
    assert block, 'правила `.info-split` в стиле нет'
    assert 'background' in block.group(1), \
        'у разделителя нет фона — его не видно, пока на него не наведут'


def test_the_splitter_position_is_remembered(page):
    """Ширина колонок переживает перезагрузку: её кладут туда же, где ширина
    списка анкет, — в один ключ раскладки, а не во второй рядом."""
    keep = page[page.index('function layoutSave'):]
    assert "info: style.getPropertyValue('--info')" in keep, \
        'ширина колонок не сохраняется'
    assert "saved.info" in keep, 'сохранённая ширина не восстанавливается'


def test_the_columns_collapse_on_a_narrow_screen(css):
    """⚠ Под 1000 пикселей колонки встают друг под друга, а разделитель прячется:
    делить нечего, а полоса между блоками читалась бы как линейка."""
    import re

    narrow = re.search(r'@media \(max-width: 1000px\)\s*\{(.*?)\n\}', css, re.S)
    assert narrow, 'медиазапроса для узкого экрана нет'
    assert '.info-split { display: none; }' in narrow.group(1), \
        'на узком экране разделитель остаётся висеть между блоками'


def test_the_left_column_holds_all_the_fields(page):
    """⚠⚠ Слева **все** поля — учётки и с сайта, справа только Bio.

    Прежде параметры стояли справа, а Bio не было вовсе. Теперь правая колонка
    отдана итогу: человек правит слева и тут же видит, что из этого соберётся для
    агента. Делить поля между колонками больше незачем — делить надо ввод и
    результат.
    """
    left = page[page.index("'<div class=\"info-left\">'"):page.index("'<div class=\"split info-split\"")]
    assert 'paramShort' in left, 'короткие параметры не в левой колонке'
    assert 'paramLong' in left, 'длинные тексты не в левой колонке'
    assert 'cardLogin' in left and 'cardAbout' in left, 'поля учётки не слева'

    right = page[page.index("'<div class=\"info-right\">'"):page.index("'<div class=\"info-foot\">'")]
    assert 'cardBio' in right, 'Bio не в правой колонке'
    assert 'paramShort' not in right and 'paramLong' not in right, \
        'параметры остались справа — там только итог'


def test_the_refresh_button_lives_in_the_card_header(page, css):
    """⚠⚠ Кнопка чтения панели — **в шапке карточки**, а не в теле вкладки.

    В заголовке «Параметры с сайта» она стояла вплотную к отметке времени съёма и
    наезжала на неё: дата читалась наполовину.
    """
    assert 'id="profileHeadRight"' in page, 'в шапке карточки нет места под кнопку'
    assert '.pane-head-right' in css, 'правая часть шапки не описана в стиле'

    body = page[page.index('function infoBlock'):page.index('function infoBind')]
    assert 'cardParamsGet' not in body, 'кнопка осталась в теле вкладки'


def test_the_button_shows_only_on_its_own_tab(page):
    """⚠ Шапка одна на все вкладки, а кнопка — про параметры анкеты.

    На «Галерее» или «Переменных» она обещала бы действие, которого там нет; хуже
    того, нажатая оттуда она записала бы параметры, не показав результата.
    """
    assert "cardTab !== 'info' ? ''" in page, \
        'кнопка рисуется независимо от вкладки'


def test_the_header_is_cleared_with_the_card(page):
    """⚠ Иначе кнопка висит над «выберите анкету слева» и жмёт панель
    несуществующей анкеты."""
    keep = page[page.index('анкета «${profile.login || currentId}» удалена'):]
    assert "getElementById('profileHeadRight').innerHTML = ''" in keep, \
        'правая часть шапки не чистится вместе с карточкой'


def test_bio_is_shown_but_not_editable(page, css):
    """⚠⚠ Bio — **итог полей слева**, а не отдельное поле.

    Его собирает `agent_bio`, и он же уезжает агенту при каждом ответе в чате и
    при заведении агента у соседа. Правка здесь завела бы второй источник правды:
    «почему агент говорит не то, что в поле» стало бы вопросом без ответа.

    ⚠ `readonly`, а не `disabled`: из отключённого поля нельзя скопировать текст,
    а скопировать его — первое, что делают, сверяя с ответом агента.
    """
    assert 'id="cardBio"' in page, 'поля Bio нет'
    assert 'class="info-bio" id="cardBio" readonly' in page, \
        'Bio можно править — появится второй источник правды'
    assert '.info-bio' in css, 'Bio не описан в стиле'

    send = page[page.index('const send = {'):page.index('putData(`${BASE}')]
    assert 'cardBio' not in send, 'Bio уезжает в сохранение — его никто не ждёт'


def test_bio_comes_assembled_from_the_server():
    """⚠ Собирает его **сервер** тем же `agent_bio`, что уезжает агенту.

    Своя сборка на странице разошлась бы с настоящей на первой правке — и человек
    сверял бы ответ агента с текстом, которого агент не видел.
    """
    from pathlib import Path

    handler = (Path('src/uvicorn/routers/dating/profile_view.py')
               .read_text(encoding='utf-8'))

    assert "'bio': agent_bio(card)" in handler, 'ручка не отдаёт собранное био'


def test_the_bio_button_asks_the_server(page):
    """⚠⚠ Кнопка «Обновить био» шлёт поля **на сервер**, а не собирает текст сама.

    Био собирает `agent_bio` — та же функция, чей результат уезжает агенту. Своя
    сборка на странице разошлась бы с настоящей молча, и человек сверял бы ответ
    агента с текстом, которого агент не видел.

    ⚠ Кнопка ничего не записывает: это предпросмотр «как будет выглядеть био с
    тем, что я сейчас набрал». Сохраняет по-прежнему «Сохранить».
    """
    assert 'id="cardBioGet"' in page, 'кнопки «Обновить био» нет'
    assert '/card/bio`, fields())' in page, 'кнопка не зовёт ручку сборки'

    keep = page[page.index("getElementById('cardBioGet')"):]
    keep = keep[:keep.index('});')]
    assert 'putData' not in keep and 'deleteData' not in keep, \
        'предпросмотр что-то записывает — он должен только показывать'


def test_the_bio_preview_handler_does_not_write():
    """⚠ Ручка собирает и отвечает, но в базу не пишет: `profile_row` только
    проверяет, что анкета есть."""
    from pathlib import Path

    src = Path('src/uvicorn/routers/dating/profile_.py').read_text(encoding='utf-8')
    start = src.index('async def profile_card_bio(')
    body = src[start:src.index('@router.put', start)]

    assert 'agent_bio(' in body, 'ручка собирает био не общей функцией'
    for word in ('profile_card_save', 'UPDATE', 'db.execute'):
        assert word not in body, f'предпросмотр пишет в базу: {word}'


def test_the_save_message_names_what_changed(page):
    """⚠⚠ Подтверждение называет **изменённое**, а не «сохранено».

    Форма на 27 полей: общее подтверждение не отличает «поправил имя» от
    «сохранил, не тронув ничего», и человек не видит, поняли его правку или нет.

    ⚠ Список считает **сервер** — он один знает, что разошлось с базой. Своя
    сверка на странице дала бы второй ответ на тот же вопрос.
    """
    keep = page[page.index("getElementById('cardEditKeep')"):]
    keep = keep[:keep.index('\n    }')]

    assert 'result.changed' in keep, 'страница не берёт список изменённого у сервера'
    assert "'сохранено: '" in keep, 'подтверждение не называет изменённое'
    assert 'изменений не было' in keep, 'нет ответа на «сохранил, не правя»'


def test_the_save_message_is_shown_next_to_the_button(page, css):
    """⚠ Форма длинная, кнопка внизу, а подсказка в шапке страницы — за экраном:
    нажавший её попросту не увидит. Поэтому итог ещё и в подвале формы.

    ⚠ Цветом в той же строке, а не вторым узлом: два места под один ответ — это
    вопрос «а что показано в другом», и рано или поздно они разойдутся.
    """
    keep = page[page.index("getElementById('cardEditKeep')"):]
    keep = keep[:keep.index('\n    }')]

    assert 'why.textContent = said' in keep, 'итог не показан рядом с кнопкой'
    assert 'info-why-ok' in keep, 'подтверждение не отличается от отказа'
    assert '.info-why.info-why-ok' in css, 'цвет подтверждения не задан'
    assert "why.classList.remove('info-why-ok')" in keep, \
        'класс не снимается — отказ после удачи останется зелёным'
