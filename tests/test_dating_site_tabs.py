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


def _page(name: str) -> str:
    return (ROOT / f'data/uvicorn/template/admin/dating/{name}.html').read_text(
        encoding='utf-8')


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
            assert set(counted) == {'online', 'profiles', 'whole', 'chat', 'mail'}, (
                f'сайт {site}: форма счётчиков разъехалась — {sorted(counted)}')

    async def test_the_denominator_is_the_working_ones(self, auth_client):
        """⚠⚠ Знаменатель — включённые анкеты, а не все.

        Выключенную увёл человек, и работать она не должна. Поставь мы в
        знаменатель всех — сайт с двумя включёнными из девятнадцати вечно
        показывал бы `2/19`, и семнадцать «недостающих» читались бы как
        поломка.
        """
        got = (await auth_client.get('/api/site/live')).json()['result']

        for site, counted in got.items():
            assert counted['profiles'] <= counted['whole'], (
                f'сайт {site}: работающих больше, чем анкет вообще')
            assert counted['online'] <= counted['profiles'], (
                f'сайт {site}: в сети больше, чем работающих — знаменатель '
                'взят не из тех анкет')

    def test_the_mail_pools_are_not_listed_twice(self):
        """⚠⚠ Пулы писем перечисляет `mail_sent_union`, а не свой запрос.

        Список «откуда письмо уходит человеку» один на проект и живёт у
        отправки; своя копия разошлась бы на первом же новом пуле — и
        разошлась бы молча, в меньшую сторону.
        """
        code = (ROOT / 'src/dating/site_live.py').read_text(encoding='utf-8')

        assert 'mail_sent_union' in code, 'счёт писем перечисляет пулы сам'
        for pool in ('mailing', 'favorite_mail', 'intention_mail'):
            assert f"'{pool}'" not in code, (
                f'имя пула {pool} записано здесь второй раз')

    def test_the_union_marks_are_counted_not_guessed(self):
        """⚠⚠ Число `%s` считается по тексту союза, а не длиной списка пулов.

        Запиши мы его числом — четвёртый пул сломал бы запрос, а `except`
        отдал бы пустоту вместо ошибки. Эта грабля уже стоила окна «почему
        этой паре не пишут».
        """
        code = (ROOT / 'src/dating/site_live.py').read_text(encoding='utf-8')

        assert "sql.count('%s')" in code, (
            'доводы союза считаются не по тексту — новый пул сломает счёт молча')


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
        значка про одно, нарисованные по-разному, читаются как про разное."""
        code = MODULE.read_text(encoding='utf-8')

        assert 'class="pill chat"' in code, 'значок чата нарисован своим классом'
        assert 'class="pill mail"' in code, 'значок почты нарисован своим классом'

    def test_the_blinking_respects_reduced_motion(self):
        """⚠ Мигание приезжает из общего правила и уважает настройку человека
        «меньше движения» — иначе вкладка мигала бы там, где анимацию выключили
        намеренно."""
        css = STYLE.read_text(encoding='utf-8')

        spot = css.find('.pill.chat')
        assert spot > 0 and 'prefers-reduced-motion' in css[spot:spot + 600], (
            'мигание значка не уважает `prefers-reduced-motion`')

    def test_the_tabs_wrap(self):
        """⚠ Сайтов сейчас четыре, но число их задаёт не вёрстка, а закладки у
        соседа: пятый не должен уезжать за край."""
        css = STYLE.read_text(encoding='utf-8')

        spot = css.find('.site-tabs {')
        assert spot > 0, 'оформления вкладок нет'
        assert 'flex-wrap' in css[spot:spot + 200], 'вкладки не переносятся'
