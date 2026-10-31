"""Вкладки сайтов в шапке раздела: имя, кто в сети, работа за полсуток.

Прежде тут стоял селект: он отвечал на один вопрос — «какой сайт открыт», — и
отвечал молча. Чтобы узнать, где идёт жизнь, человек перебирал сайты по
очереди. Вкладка отвечает сразу на три.

## ⚠⚠ Что здесь стережётся в первую очередь

**Совместимость с тем, что уже написано.** Выбранный сайт страницы читают как
`siteEl.value` и слушают событием `change` — сорок пять мест в шести файлах.
Контейнер вкладок отвечает теми же двумя именами; разойдись это, половина
раздела перестала бы видеть смену сайта — и перестала бы **молча**, показывая
анкеты прежнего сайта под именем нового.

⚠ Браузера у сюиты нет, и отрисовку она не меряет. Здесь проверяется
**механизм**: что счётчики считаются сервером, что разметка страниц переведена,
что модуль не растерял имён. Вид проверен глазами и headless-браузером.
"""

import json
import pathlib
import re

import pytest

pytestmark = pytest.mark.page

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = ROOT / 'data/uvicorn/public/js/dating/site.js'
STYLE = ROOT / 'data/uvicorn/public/css/dating_page.css'

# Страницы раздела, у которых в шапке стоит выбор сайта.
PAGES = ('profile', 'index', 'log', 'error', 'site', 'socket')

# Адрес «Профилей»: там живёт карта «куда вести» (`FEED_WHERE`).
PAGE_PROFILE = '/admin/dating/profile'


def _page(name: str) -> str:
    return (ROOT / f'data/uvicorn/template/admin/dating/{name}.html').read_text(
        encoding='utf-8')


def _block(css: str, head: str) -> str:
    """Тело одного правила CSS по его заголовку.

    ⚠ Ищем до ближайшей `}`, а не регуляркой по всему файлу: соседнее правило
    с похожим началом иначе утащило бы проверку к себе, и сторож молча стерёг
    бы не то.
    """
    spot = css.find(head)

    assert spot > 0, f'правила `{head}` в оформлении нет'

    return css[spot:css.find('}', spot)]


class TestEveryPageUsesTabs:
    """Все шесть страниц раздела перешли на вкладки, и ни одна не отстала."""

    @pytest.mark.parametrize('name', PAGES)
    def test_the_select_is_gone(self, name):
        """⚠ `<select>` в шапке не остался: ключ выбора у страниц один, и
        страница со старым селектом писала бы в него, не показывая счётчиков —
        расхождение, которое человек заметил бы не сразу."""
        head = _page(name)
        spot = head.find('id="siteSelect"')

        assert spot > 0, f'{name}: узла выбора сайта нет вовсе'
        assert '<select id="siteSelect"' not in head, (
            f'{name}: шапка всё ещё рисует селект')
        assert 'class="site-tabs"' in head, f'{name}: контейнера вкладок нет'

    @pytest.mark.parametrize('name', PAGES)
    def test_it_calls_the_tabs(self, name):
        assert 'datingSiteTabs(siteEl, BASE)' in _page(name), (
            f'{name}: вкладки не загружаются')
        assert 'datingSiteFill(' not in _page(name), (
            f'{name}: осталась старая загрузка селекта')

    @pytest.mark.parametrize('name', PAGES)
    def test_the_failure_is_not_an_option_tag(self, name):
        """⚠ Отказ рисовался `<option>` — внутри `div` он не виден вовсе, и
        «сайты не пришли» превратилось бы в пустую шапку без объяснения."""
        head = _page(name)

        assert '<option value="">сайты не пришли</option>' not in head, (
            f'{name}: отказ рисуется тегом, который в контейнере не покажется')


class TestTabsBehaveLikeTheSelect:
    """⚠⚠ Контейнер отвечает `value` и `change` — на этом держится весь раздел."""

    def test_value_is_defined(self):
        code = MODULE.read_text(encoding='utf-8')

        assert "Object.defineProperty(el, 'value'" in code, (
            'у контейнера нет свойства `value` — страницы читают выбранный '
            'сайт именно им, и получат `undefined`')

    def test_change_is_dispatched(self):
        code = MODULE.read_text(encoding='utf-8')

        assert "new Event('change'" in code, (
            'вкладки не бросают `change` — три страницы раздела слушают '
            'именно его и смену сайта не заметят')

    def test_the_property_is_defined_once(self):
        """⚠ `datingSiteTabs` зовут и при первой загрузке, и при обновлении
        списка; второе `defineProperty` на том же узле — отказ `TypeError`."""
        code = MODULE.read_text(encoding='utf-8')

        assert "getOwnPropertyDescriptor(el, 'value')" in code, (
            'повторный вызов уронит страницу на `defineProperty`')

    def test_the_pick_survives_repaint(self):
        """⚠⚠ Вкладки переписываются целиком на каждом обновлении счётчиков.
        Не запомни мы обработчик выбора — щелчок работал бы ровно до первого
        тика, а потом переставал: молча, и выглядело бы «панель подвисла»."""
        code = MODULE.read_text(encoding='utf-8')

        assert '_datingOnPick = onPick' in code, 'обработчик выбора теряется'
        assert '_datingTabsWire(el, sites, el._datingOnPick)' in code, (
            'после обновления счётчиков вкладки не переподключаются')


class TestTheCountersAreCountedByTheServer:
    """Счёт — на сервере, и теми же выражениями, что у остальной панели."""

    async def test_the_handle_answers(self, auth_client):
        got = (await auth_client.get('/api/site/live')).json()

        assert 'result' in got and isinstance(got['result'], dict), (
            'счётчики отдаются словарём «сайт → числа»: страница кладёт их к '
            'своему списку сайтов, и список здесь означал бы второй порядок')
        assert int(got.get('hours') or 0) > 0, 'срок счёта не назван'

    async def test_every_site_has_the_whole_shape(self, auth_client):
        """⚠ Форма одна у всех: сайт без работы отдаёт нули, а не пропадает —
        иначе вкладка осталась бы без чисел и выглядела недогруженной."""
        got = (await auth_client.get('/api/site/live')).json()['result']

        for site, counted in got.items():
            assert set(counted) == {'online', 'profiles', 'whole', 'chat',
                                    'mail', 'intention'}, (
                f'сайт {site}: форма счётчиков разъехалась — {sorted(counted)}')

    async def test_the_denominator_is_every_profile(self, auth_client):
        """⚠⚠ Знаменатель — **все анкеты сайта**, а не включённые.

        Прежде здесь стояли включённые, и довод был такой: выключенную увёл
        человек, работать она не должна. Довод оказался неверным, и показала
        это живая страница: у Ladadate девятнадцать анкет, включённых ноль —
        вкладка писала `0/0` **рядом со списком из девятнадцати строк**.

        Список рядом — та самая мера, с которой человек сверяет вкладку, и два
        числа про одно и то же, спорящие на одном экране, хуже любого из них по
        отдельности.

        ⚠ Числитель по той же причине считается **без оглядки на `enabled`**:
        разойдись множества, `online` оказался бы больше `whole` при первой же
        выключенной, но живой анкете.
        """
        got = (await auth_client.get('/api/site/live')).json()['result']

        for site, counted in got.items():
            assert counted['profiles'] <= counted['whole'], (
                f'сайт {site}: включённых больше, чем анкет вообще')
            assert counted['online'] <= counted['whole'], (
                f'сайт {site}: в сети больше, чем анкет вообще — числитель и '
                'знаменатель считают разные множества')

    def test_the_tab_shows_the_whole_count(self):
        """⚠⚠ На вкладке стоит `whole`, а не `working`: именно это число
        человек сверяет с заголовком списка слева."""
        code = MODULE.read_text(encoding='utf-8')

        assert '${online}/${whole}' in code, (
            'на вкладке не общее число анкет — оно разойдётся со списком')

    def test_the_numerator_ignores_enabled(self):
        """⚠ Числитель и знаменатель обязаны считать одно множество."""
        code = (ROOT / 'src/dating/site_live.py').read_text(encoding='utf-8')

        assert "SUM(status = 'online') AS online" in code, (
            'в сети считаются только включённые, а в знаменателе — все')

    def test_only_the_incoming_is_counted(self):
        """⚠⚠ Значки считают **входящее** — реплики и письма от собеседников.

        Сперва считались обе стороны, и довод был такой: разговор это обмен.
        Довод оказался неверным, и показал это прод: у Ladadate за полсуток 20
        исходящих реплик против 6 входящих, а писем 95 при **нуле** входящих —
        значки докладывали о нашей собственной работе.

        Значок отвечает «сколько людям есть что ответить». Исходящее делает
        круг, и его число растёт само по себе — хоть в пустоту, хоть в тишину.
        """
        code = (ROOT / 'src/dating/site_live.py').read_text(encoding='utf-8')

        assert "m.direction = 'in'" in code, (
            'в счёт реплик идут и наши исходящие')
        assert 'mail_sent_union' not in code, (
            'в счёт писем идут и наши исходящие')

    def test_the_chat_count_hides_twins(self):
        """⚠⚠ Наше исходящее лежит в `message` дважды — своей строкой и
        отражением из панели, — и то же бывает со **входящим**: реплика
        приходит сокетом и второй раз импортом.

        Правило одно на всех, кто показывает человеку счёт реплик; своя копия
        разошлась бы на первой правке. Грабля уже случалась: значок диалогов
        говорил 61 при шестидесяти видимых.

        ⚠ Ищем вызов **внутри запроса реплик**, а не имя где угодно в файле:
        с проверкой «есть ли слово» сторож оставался зелёным, когда вызов
        убрали, а импорт сверху остался. Проверено диверсией.
        """
        code = (ROOT / 'src/dating/site_live.py').read_text(encoding='utf-8')
        body = code[code.find('async def _chat'):code.find('async def _mail')]

        assert "message_twin_skip('m')" in body, (
            'счётчик реплик не прячет близнецов из панели и завысит число')


class TestTheIntentionBadge:
    """⚠ Четвёртый значок: сколько обещаний агент дал за полсуток.

    Два соседних отвечают «что пришло от людей», этот — «что мы наобещали».
    Вопрос соседний, но свой: обещание рождается в разговоре, а ждёт работы
    уже в пуле, и заметить его надо так же быстро.
    """

    async def test_the_handle_counts_it(self, girl, auth_client):
        """⚠ Сторож **считает живое обещание**, а не смотрит на ключ: ключ
        приезжает из `SITE_LIVE_EMPTY` даже когда счётчик не зовут вовсе, и
        проверка на его наличие зеленела на снятом вызове. Проверено
        диверсией."""
        from src.dating.chat_intention import chat_intention_put
        from tests.conftest import TEST_STAND_SITE

        before = (await auth_client.get('/api/site/live')).json()['result']
        was = int((before.get(str(TEST_STAND_SITE)) or {}).get('intention') or 0)

        await chat_intention_put(girl, TEST_STAND_SITE, 'u-live-probe',
                                 {'she_promised_send_photo': {}})

        after = (await auth_client.get('/api/site/live')).json()['result']
        now = int((after.get(str(TEST_STAND_SITE)) or {}).get('intention') or 0)

        assert now == was + 1, (
            f'свежее обещание не попало в счёт: было {was}, стало {now}')

    def test_the_sign_is_its_own(self):
        """⚠ Силуэт различает значки прежде цвета: круг не спутать с конвертом
        и пузырём даже боковым зрением."""
        code = MODULE.read_text(encoding='utf-8')
        signs = code[code.find('DATING_COUNT_SIGNS'):]

        assert 'intention:' in signs[:signs.find('};')], 'у значка нет рисунка'
        assert '<circle' in signs[:signs.find('};')], (
            'рисунок намерения не круглый — силуэтом его не отличить')

    def test_it_leads_to_its_own_tab(self):
        code = MODULE.read_text(encoding='utf-8')
        spot = code.find("intention: {tab:")

        assert spot > 0, 'значок никуда не ведёт'
        assert "tab: 'intention'" in code[spot:spot + 120], (
            'значок обещаний ведёт не на свою вкладку')

    def test_the_feed_kind_is_not_empty(self):
        """⚠⚠ Лента про намерения не пишет, но вид события им всё равно нужен:
        им ведёт щелчок, когда человек **уже на «Профилях»**. Оставь мы `feed`
        пустым — оттуда значок уходил бы в никуда, а с других страниц работал.
        Молча.
        """
        code = MODULE.read_text(encoding='utf-8')
        spot = code.find("intention: {tab:")

        assert "feed: 'intention'" in code[spot:spot + 140], (
            'у значка пустой вид события — с «Профилей» щелчок не сработает')

    async def test_the_page_knows_that_kind(self, auth_client):
        """⚠ Карта «куда вести» одна на оба входа, и вид обязан быть в ней."""
        page = (await auth_client.get(PAGE_PROFILE)).text
        spot = page.find('const FEED_WHERE = {')

        assert "intention: ['intention', 'talk']" in page[spot:spot + 900], (
            'страница не знает вида события намерений')

    def test_the_blink_is_declared_not_branched(self):
        """⚠ Мигание записано в карте, а не развилкой по имени вида: третий
        значок превратил бы `kind === 'mail'` в лестницу."""
        code = MODULE.read_text(encoding='utf-8')

        # ⚠ Ищем развилку **в выражении класса**, а не слово где угодно:
        # в пояснении рядом оно названо прямым текстом, и сторож на простом
        # `not in` краснел бы на верном коде. Та же грабля уже была со
        # `stopPropagation`.
        assert "? `pill ${kind}${go.beat ? ' beat' : ''}`" in code, (
            'мигание решается не картой — с четвёртым значком развилка по '
            'имени станет лестницей')

    def test_its_glow_matches_its_colour(self):
        """⚠ Янтарная волна под индиговой пилюлей читается как чужая, будто
        мигает не она."""
        css = STYLE.read_text(encoding='utf-8')

        assert '.pill.intention.beat' in css, 'у значка нет своего мигания'
        assert 'alarm-intention' in css, 'отсвет мигания взят у почты'


class TestTheBadgesLeadSomewhere:
    """⚠⚠ Значок — не только число, но и дверь: реплики ведут на «Чат →
    Диалоги», письма — на «Фавориты → Диалоги».

    Число отвечает «сколько», а человек, увидев его, идёт смотреть **что
    именно**. Без двери он шёл туда руками: страница, вкладка, подвкладка,
    да ещё и сайт выбрать заново — четыре нажатия на вопрос, заданный одним.
    """

    def test_both_badges_are_links(self):
        """⚠ Ссылкой, а не обработчиком: адрес видно в строке состояния до
        нажатия, его можно открыть в новой вкладке, и он не зависит от того,
        что страница запомнила в прошлый раз."""
        code = MODULE.read_text(encoding='utf-8')

        assert '<a class="${cls}" href=' in code, (
            'значок нарисован не ссылкой — адреса у него нет')
        assert '/admin/dating/profile?site=' in code, (
            'ссылка не несёт сайт: значок зовёт к работе своего сайта, а '
            'открыт мог быть другой')

    def test_the_grey_badge_leads_too(self):
        """⚠⚠ «Писем нет» — это ответ, за которым идут в тот же список, чтобы
        убедиться. Веди туда только мигающий — и значок переставал бы работать
        ровно тогда, когда на него смотрят спокойно."""
        code = MODULE.read_text(encoding='utf-8')
        body = code[code.find('function _datingCount('):]
        body = body[:body.find('\n}')]

        assert 'href' in body and 'count ?' not in body.split('href')[1][:200], (
            'адрес зависит от непустого счёта — серый значок никуда не ведёт')

    def test_the_destination_map_is_shared_with_the_feed(self):
        """⚠⚠ Значок несёт **вид события ленты**, а не свою пару вкладок:
        карта «куда вести» (`FEED_WHERE`) остаётся одна на оба входа. Разойдись
        они — щелчок по строке ленты и по значку уводили бы в разные места."""
        code = MODULE.read_text(encoding='utf-8')

        assert "feed: 'talk'" in code and "feed: 'favtalk'" in code, (
            'значок не называет вид события — страница не сможет позвать '
            '`feedGo` и заведёт вторую карту назначений')

    def test_the_badge_click_does_not_pick_the_site(self):
        """⚠ Значок ведёт в другое место, и выбирать заодно сайт значило бы
        делать два дела на одно нажатие."""
        code = MODULE.read_text(encoding='utf-8')

        assert "if (event.target.closest('[data-go]')) return;" in code, (
            'щелчок по значку заодно выбирает сайт')

    def test_the_bubbling_is_not_stopped(self):
        """⚠⚠ Всплытие рвать нельзя: страница «Профили» ловит тот же щелчок
        выше по дереву, чтобы открыть место без перезагрузки.

        Это уже было: глушение всплытия на значке отнимало у неё событие, и
        перехват молча не срабатывал — ссылка уводила браузер на ту же
        страницу заново. Поймано живым браузером, тесты были зелёными.

        ⚠ Ищем **вызов**, а не слово: первая запись сторожа спотыкалась о
        собственное пояснение в коде, где имя приёма названо прямым текстом.
        """
        code = MODULE.read_text(encoding='utf-8')

        assert '.stopPropagation()' not in code, (
            'всплытие заглушено — перехват страницы не получит событие')


class TestThePaletteAgrees:
    """⚠⚠ Палитра сайтов — в CSS, число цветов знает ещё и `site.js`.

    Скажи в коде больше, чем объявлено блоков, и сайт с лишним номером
    останется без переменных: нарисуется запасным цветом, одинаковым с
    соседом. Молча — отрисовка не падает, а два сайта одного цвета это ровно
    то, против чего палитра заводилась.
    """

    def test_the_count_is_the_same_on_both_sides(self):
        css = STYLE.read_text(encoding='utf-8')
        code = MODULE.read_text(encoding='utf-8')

        declared = len(re.findall(r'\[data-hue="(\d+)"\]', css))
        said = re.search(r'DATING_SITE_HUES\s*=\s*(\d+)', code)

        assert said, 'в `site.js` нет числа цветов — палитра считается на месте'
        assert declared == int(said.group(1)), (
            f'цветов объявлено {declared}, а код считает '
            f'{said.group(1)}: лишний номер нарисуется запасным цветом')

    def test_the_numbers_run_without_a_hole(self):
        """⚠ Номера идут подряд от нуля: цвет берётся остатком от деления, и
        пропуск в середине оставил бы один сайт из восьми серым."""
        css = STYLE.read_text(encoding='utf-8')
        got = sorted(int(n) for n in re.findall(r'\[data-hue="(\d+)"\]', css))

        assert got == list(range(len(got))), f'номера цветов с дырой: {got}'

    def test_every_colour_brings_the_whole_set(self):
        """⚠ Три переменные у каждого цвета: заливка, чернила и рамка. Забудь
        одну — `var(--ink, …)` возьмёт запасное, то есть чужой цвет сайта."""
        css = STYLE.read_text(encoding='utf-8')

        for block in re.findall(r'\[data-hue="\d+"\]\s*\{([^}]*)\}', css):
            for name in ('--tint', '--ink', '--edge'):
                assert name in block, f'в цвете нет `{name}`: {block.strip()}'

    def test_the_colours_differ(self):
        """⚠⚠ Смысл палитры — различие: два одинаковых оттенка это не
        «почти незаметно», а два сайта, которых не отличить вовсе."""
        css = STYLE.read_text(encoding='utf-8')
        inks = re.findall(r'\[data-hue="\d+"\][^}]*--ink:\s*([^;]+);', css)

        assert len(inks) == len(set(i.strip() for i in inks)), (
            f'цвета повторяются: {inks}')

    def test_the_hue_sits_on_the_tab_and_on_the_body(self):
        """⚠⚠ Два места, и это не дубль: на вкладке номер красит её саму, на
        теле — подвкладки, которые про список сайтов не знают ничего."""
        code = MODULE.read_text(encoding='utf-8')

        assert 'data-hue="${_datingSiteHue(sites, id)}"' in code, (
            'вкладка не несёт своего цвета — различимы будут только открытые')
        assert 'document.body.dataset.hue' in code, (
            'цвет не доходит до тела: подвкладки останутся в прежнем индиго')

    def test_the_hue_follows_a_pick_made_by_code(self):
        """⚠⚠ Сайт меняют и щелчком, и `el.value = …` из кода. Покрась мы
        только щелчок — страница осталась бы в цвете прежнего сайта, то есть
        соврала бы ровно о том, ради чего цвет и введён."""
        code = MODULE.read_text(encoding='utf-8')
        pick = code[code.find('function _datingTabsPick'):]

        assert '_datingTabsTint(el)' in pick[:pick.find('\n}')], (
            'выбор сайта кодом не перекрашивает страницу')

    def test_the_colour_comes_from_the_place_not_the_id(self):
        """⚠⚠ Номер в базе дал бы один цвет двум сайтам, разошедшимся на
        восемь — и дал бы молча, на живых данных."""
        code = MODULE.read_text(encoding='utf-8')

        assert 'findIndex' in code, (
            'цвет считается не по месту в списке: номера в базе столкнутся')

    def test_the_sub_tabs_read_the_colour(self):
        """Подвкладки берут цвет открытого сайта — и страницы раздела, и
        вкладки карточки анкеты."""
        css = STYLE.read_text(encoding='utf-8')
        page = _page('index')

        spot = css.find('.page-tab.on {')
        assert spot > 0 and 'var(--tint' in css[spot:spot + 300], (
            'подвкладка страницы не берёт цвет сайта')
        assert 'var(--ink' in page[page.find('.card-tab.on'):][:200], (
            'вкладка карточки анкеты не берёт цвет сайта')

    def test_the_spare_pair_stays_readable(self):
        """⚠⚠ Запасные значения держатся **парой**: нет цвета — заливка индиго
        и буквы белым, прежний вид. Разойдись они, до загрузки списка
        подвкладка вышла бы белым по белому."""
        css = STYLE.read_text(encoding='utf-8')
        spot = css.find('.page-tab.on {')
        block = css[spot:css.find('}', spot)]

        assert 'var(--tint, #4f46e5)' in block and 'var(--ink, #fff)' in block, (
            'запасные заливка и буквы разошлись — вкладка станет нечитаемой '
            f'до загрузки списка сайтов: {block.strip()}')


class TestTheLookIsShared:
    """⚠ Значки счётчиков — те же, что у работы на карточке анкеты."""

    def test_the_badges_reuse_the_profile_classes(self):
        """Одно состояние на панели обязано выглядеть одинаково везде: два
        значка про одно, нарисованные по-разному, читаются как про разное.

        ⚠ Класс теперь собирается (`pill ${kind}`), а не стоит в разметке
        строкой: у значка два состояния — живое и тихое. Стережём то, что от
        этого не изменилось: вида ровно два и зовут их `chat` и `mail`.
        """
        code = MODULE.read_text(encoding='utf-8')

        assert "`pill ${kind}" in code, 'значок нарисован не общим классом `pill`'
        for kind in ("'chat'", "'mail'"):
            assert f"_datingCount({kind}" in code, (
                f'значка {kind} на вкладке нет')

    def test_the_blinking_respects_reduced_motion(self):
        """⚠ Мигание приезжает из общего правила и уважает настройку человека
        «меньше движения» — иначе вкладка мигала бы там, где анимацию выключили
        намеренно.

        ⚠ Ищем **правило**, а не первое упоминание: `.pill.chat` стоит и в
        пояснении сверху, и оттуда до правила с настройкой далеко — сторож
        стерёг бы комментарий.
        """
        css = STYLE.read_text(encoding='utf-8')
        spot = css.find('.pill.chat {')

        assert spot > 0 and 'prefers-reduced-motion' in css[spot:spot + 600], (
            'мигание значка не уважает `prefers-reduced-motion`')

    def test_the_tabs_scroll_rather_than_wrap(self):
        """⚠ Сайтов сейчас четыре, но число их задаёт не вёрстка, а закладки у
        соседа. Перенос второй строкой сломал бы ряды таблицы — счётчики уехали
        бы под чужие имена, — поэтому лишнее уходит в прокрутку."""
        css = STYLE.read_text(encoding='utf-8')
        block = _block(css, '.site-tabs {')

        assert 'overflow-x' in block, 'лишние сайты уедут за край без прокрутки'
        assert 'flex-wrap' not in block, (
            'перенос вернулся: счётчики встанут под чужими именами')


class TestTheTabIsATableOfTwoRows:
    """⚠⚠ Колонка — сайт, ряд — вид сведений. Счётчики под именем, не рядом.

    Числа приезжают своей ручкой через секунду после имён. Стой они в строку,
    `Ladadate` превращался бы в `Ladadate 13/19 💬27 ✉8` — вдвое шире, — и
    соседние вкладки разъезжались под курсором. Ряд под именем меняет высоту
    колонки на ноль.
    """

    def test_the_counters_live_in_their_own_row(self):
        code = MODULE.read_text(encoding='utf-8')

        assert 'site-tab-counts' in code, 'ряда счётчиков нет'
        spot = code.find('site-tab-name')
        assert 0 < spot < code.find('site-tab-counts'), (
            'счётчики нарисованы раньше имени — ряды перепутаны')

    def test_the_columns_are_equal(self):
        """⚠⚠ `grid-auto-columns: 1fr` вместе с `width: max-content` даёт всем
        колонкам ширину самой широкой. Это и фиксирует имена: сами по себе они
        разной длины, и ряд разноразмерных плиток читается как случайный."""
        block = _block(STYLE.read_text(encoding='utf-8'), '.site-tabs {')

        assert 'grid-auto-columns: 1fr' in block, 'колонки не равной ширины'
        assert 'max-content' in block, (
            'без `width: max-content` равные колонки растянутся на всю шапку')

    def test_everything_is_centred(self):
        """Равные колонки с прижатым влево текстом выглядят хуже неравных."""
        css = STYLE.read_text(encoding='utf-8')

        assert 'text-align: center' in _block(css, '.site-tab {'), (
            'имя сайта не по центру колонки')
        assert 'justify-content: center' in _block(css, '.site-tab-counts {'), (
            'значки не по центру колонки')

    def test_a_third_row_is_cheap(self):
        """⚠ Ряды заведены **рядами сетки**, а не вёрсткой внутри кнопки:
        будущий третий ряд выравнивается по всем колонкам сам. Внутри кнопки он
        поехал бы у той колонки, где содержимое выше."""
        css = STYLE.read_text(encoding='utf-8')

        assert 'grid-template-rows' in _block(css, '.site-tabs {'), (
            'ряды не заданы сеткой — третий придётся равнять руками')
        assert 'subgrid' in _block(css, '.site-tab {'), (
            'колонка не делит ряды с таблицей: вся вкладка перестанет быть '
            'одним нажимаемым элементом либо ряды разъедутся')

    def test_the_zero_is_drawn_not_hidden(self):
        """⚠⚠ Исчезающий значок менял бы ширину колонки на каждом тике — ровно
        то, ради чего счётчики и съехали под имя. Серый ноль отвечает «тихо», а
        отсутствие значка не отвечает ничего."""
        code = MODULE.read_text(encoding='utf-8')

        assert 'pill off' in code, 'нулевой счёт рисуется пустотой'
        assert "? `<span class=\"pill chat\">" not in code, (
            'значок чата всё ещё прячется при нуле')

    def test_the_numbers_have_reserved_room(self):
        """⚠⚠ Вторая половина того же решения: без `min-width` колонка ехала на
        пять пикселей, когда `0/0` становился `13/19`."""
        css = STYLE.read_text(encoding='utf-8')

        assert 'min-width' in _block(css, '.site-tab-live {'), (
            'под счёт анкет не зарезервировано место')
        assert 'min-width' in _block(css, '.site-tab-counts .pill {'), (
            'под числа значков не зарезервировано место')

    def test_the_mail_blinks_only_on_the_tab(self):
        """⚠ На карточке анкеты тот же значок почты отвечает на другой вопрос —
        «у неё N писем», — и девятнадцать мигающих карточек были бы шумом.
        Поэтому мигание почты это **модификатор**, а не правило `.pill.mail`."""
        css = STYLE.read_text(encoding='utf-8')
        code = MODULE.read_text(encoding='utf-8')

        assert 'animation' not in _block(css, '.pill.mail {'), (
            'почта замигала везде, включая карточки анкет')
        assert '.pill.beat' in css and 'beat' in code, (
            'на вкладке почта не мигает при непустом счёте')

    def test_the_blinking_respects_reduced_motion(self):
        """⚠ Новое мигание уважает настройку «меньше движения» наравне со
        старым: заведи его мимо правила, и выключенная анимация вернулась бы
        одним значком."""
        css = STYLE.read_text(encoding='utf-8')
        spot = css.find('.pill.beat {')

        assert spot > 0 and 'prefers-reduced-motion' in css[spot:spot + 400], (
            'мигание почты не уважает `prefers-reduced-motion`')

    def test_the_signs_are_drawings_not_emoji(self):
        """⚠⚠ Знаки — рисунком, и красит их `color`.

        Эмодзи `color` не слушаются: они цветные сами по себе, и покрасить их
        можно только фильтром. На тихом значке это и подвело — замер с живой
        панели: самый тёмный пиксель у пузыря чата был 42, у конверта 127,
        вдвое бледнее. Конверт Noto почти целиком **белый** с тонким контуром,
        и обесцвеченный он сливался с подложкой, пока пузырь рядом оставался
        виден. Затемнением это не лечится: `brightness` жмёт и белую заливку,
        то есть знак становится пятном вместо рисунка.

        ⚠ Это про **нашу вёрстку**, а не про текст снаружи: приехавшие эмодзи
        приводит к одному виду `text_emoji_same` общего слоя, и к тому, чем
        панель рисует свои значки, она отношения не имеет.
        """
        code = MODULE.read_text(encoding='utf-8')

        assert 'stroke="currentColor"' in code, (
            'знак не слушается `color` — тихое состояние его потеряет')
        for kind in ('mail', 'chat'):
            assert f'{kind}:' in code[code.find('DATING_COUNT_SIGNS'):], (
                f'у значка {kind} нет своего рисунка')

    def test_both_signs_weigh_the_same(self):
        """⚠ Толщину обводки задаём мы, а не то, как шрифт нарисовал свой знак:
        прежде конверт был тоньше пузыря, и ряд выглядел небрежно независимо от
        цвета. Одна ширина на оба — значит одна запись в коде."""
        code = MODULE.read_text(encoding='utf-8')

        assert code.count('stroke-width=') == 1, (
            'толщина обводки задана не одним местом — знаки разойдутся по весу')

    def test_the_quiet_sign_is_darker_than_its_number(self):
        """⚠⚠ Рисунок отвечает «какая это строка — почта или чат», и ответ
        нужен всегда; число отвечает «сколько», и ноль вправе отступить. Одним
        светлым тоном на оба знак пропадал: замер показал контраст 83 против
        155 после правки."""
        css = STYLE.read_text(encoding='utf-8')

        assert 'color' in _block(css, '.pill.off .pill-sign {'), (
            'у тихого знака нет своего тона — он сольётся с подложкой')

    def test_the_live_mail_is_stronger_on_the_tab(self):
        """⚠ На вкладке живая почта насыщеннее, чем на карточке анкеты: общий
        `#fef3c7` почти белый, и на белом фоне шапки значок терялся рядом со
        значком чата. На карточке бледный тон уместен — там значков по два на
        девятнадцать строк. Разный вопрос — разный вес."""
        css = STYLE.read_text(encoding='utf-8')
        tab = _block(css, '.site-tab-counts .pill.mail {')
        card = _block(css, '.pill.mail {')

        assert 'background' in tab and tab != card, (
            'почта на вкладке красится общим бледным тоном и сливается с фоном')
