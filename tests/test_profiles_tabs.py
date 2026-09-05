"""⚠ Вкладки «Профилей» стоят над обеими колонками, а не внутри правой.

Каждая вкладка включает в себя обе колонки: слева список анкет по своему отбору,
справа работа с выбранной. Раньше вкладки жили в шапке правой панели, и список
слева был один на все четыре занятия — а список, нужный чату, не тот же, что нужен
рассылке писем.

⚠ **Разметку собирает шаблон, поведение — скрипт в нём же.** Сюита ходит по HTTP и
браузера не имеет: щёлкнуть по вкладке она не может. Поэтому сторожим то, что видно
в ответе, — порядок узлов и наличие привязки. Нарисованная, но ни к чему не
привязанная вкладка — тот случай, который тише сломанного теста.
"""

import re

PAGE = '/admin/dating/profile'


class TestTabsAboveColumns:
    """Полоса вкладок — своя строка страницы, до колонок."""

    async def test_tabs_are_above_columns(self, auth_client):
        """⚠ Проверяем **порядок**, а не наличие: «подняты на уровень выше» и есть
        порядок. Полоса, оставшаяся внутри правой панели, тест проходила бы по
        наличию узла и провалила бы задачу.
        """
        page = (await auth_client.get(PAGE)).text
        tabs = page.find('id="pageTabs"')
        profiles = page.find('class="pane prof-pane"')
        work = page.find('class="pane work-pane"')

        assert tabs != -1, 'полосы вкладок нет на странице'
        assert profiles != -1 and work != -1, 'колонки не нашлись — разметка сломана'
        assert tabs < profiles, 'полоса вкладок стоит после списка анкет'
        assert tabs < work, 'полоса вкладок стоит внутри правой панели или после неё'

    async def test_old_tabs_are_gone(self, auth_client):
        """Прежнего имени не осталось: `work-tabs` вне панели работы — ложь, и
        следующий читатель искал бы полосу внутри правой колонки."""
        page = (await auth_client.get(PAGE)).text

        assert 'work-tabs' not in page, 'осталось прежнее имя полосы вкладок'

    async def test_all_four_tabs_present(self, auth_client):
        """Четыре вкладки на месте. Потерять одну при переносе легко, и пропажу
        никто не заметит: страница откроется на «Чате» и будет выглядеть целой."""
        page = (await auth_client.get(PAGE)).text

        for tab in ('chat', 'mail', 'mail_in', 'event'):
            assert f'data-tab="{tab}"' in page, f'вкладка {tab} пропала'


class TestTabStateIsWired:
    """Состояние вкладок привязано, а не только нарисовано.

    ⚠ Урок предыдущей работы: разметка без привязки — вкладка нарисованная и
    мёртвая. Щёлкнуть по ней сюита не может (браузера нет), поэтому сторожим
    наличие самого механизма: карты отборов и словаря выбранных анкет.
    """

    async def test_tab_filter_exists(self, auth_client):
        """Отбор вкладки: без него список слева один на все четыре занятия."""
        page = (await auth_client.get(PAGE)).text

        assert 'TAB_ROWS' in page, 'карты отборов нет — списки вкладок одинаковы'
        assert 'mailing_on' in page, 'отбор рассылки не смотрит на флаг'

    async def test_open_by_tab_exists(self, auth_client):
        """Своя выбранная анкета у каждой вкладки."""
        page = (await auth_client.get(PAGE)).text

        assert 'openByTab' in page, 'выбранная анкета общая на все вкладки'


class TestTabReviewFindings:
    """Находки ревью третьей задачи: счётчик пейджера и смена сайта.

    Оба бага прошли мимо предыдущих тестов и ручной проверки — воспроизводятся
    без браузера, поэтому сторожим их так же, как выше: по исходному тексту
    отданной страницы, а не по поведению в DOM.
    """

    async def test_pager_footer_denominator_is_tab_not_site(self, auth_client):
        """Строка над пейджером («N анкет — всё на одной странице») сравнивала
        найденное с `profiles.length` — то есть со **всем сайтом**, а не со
        списком активной вкладки. На «Письмах рассылка» при одной анкете с
        флагом и сорока на сайте она говорила «1 из 40 анкет», выдавая отбор
        вкладки за поиск — ровно то, от чего предостерегает соседний счётчик
        тремя строками выше.

        Знаменатель у обоих счётчиков один — список вкладки (`tabRows()`,
        внутри `tablePaint` это `inTab`), а не `profiles`.

        ⚠ Сторожим не одну текстовую форму (`found.length === profiles.length`),
        а сам инвариант: имена переменных для списка вкладки и найденного
        достаём из кода (`const X = tabRows()`, `const Y = shownRows()`), а
        затем требуем, чтобы в строке подвала пейджера каждое `.length`
        считалось от одной из них двоих. Тот же регресс, переписанный с
        обратным порядком операндов или через переменную-алиас на `profiles`,
        тест тоже ловит — а не только буквальную подстроку из прошлого отчёта.
        """
        page = (await auth_client.get(PAGE)).text

        fn_start = page.find('function tablePaint')
        assert fn_start != -1, 'tablePaint не нашлась на странице'
        # Окно с запасом: тело функции рисует ещё и разметку строк списка
        # (шаблонная строка на добрых пять тысяч символов) между объявлением
        # `inTab`/`found` и подвалом пейджера, до которого мы здесь добираемся.
        body = page[fn_start:fn_start + 8000]

        tab_match = re.search(r'const (\w+)\s*=\s*tabRows\(\);', body)
        assert tab_match, 'tablePaint не берёт список вкладки через tabRows()'
        in_tab_var = tab_match.group(1)

        found_match = re.search(r'const (\w+)\s*=\s*shownRows\(\);', body)
        assert found_match, 'tablePaint не берёт найденное через shownRows()'
        found_var = found_match.group(1)

        footer_match = re.search(r'counted\.textContent\s*=(.*?);', body, re.S)
        assert footer_match, (
            'подвал пейджера («N анкет — всё на одной странице») не нашёлся '
            'в tablePaint'
        )
        footer_expr = footer_match.group(1)

        lengths = re.findall(r'(\w+)\.length', footer_expr)
        assert lengths, 'в подвале пейджера не нашлось ни одного счётчика .length'

        strangers = sorted(set(lengths) - {in_tab_var, found_var})
        assert not strangers, (
            f'подвал пейджера считает .length от {strangers}, а не от списка '
            f'вкладки ({in_tab_var}) или найденного ({found_var}) — '
            'знаменатель мог снова утечь на весь сайт (profiles.length), '
            'просто под другим именем переменной'
        )
        assert lengths.count(in_tab_var) >= 2, (
            f'подвал пейджера должен и сравнивать с длиной списка вкладки '
            f'({in_tab_var}), и подставлять её же в знаменатель — иначе '
            'вернулась находка 1 из ревью задачи 3'
        )

    async def test_site_change_resets_selection_on_every_tab(self, auth_client):
        """Смена сайта обязана чистить выбранную анкету у **всех** вкладок.

        `openSet(0)` в одиночку обнуляет только `openByTab[workTab]` —
        активную вкладку. Номера анкет прежнего сайта остаются в словаре у
        трёх остальных. Человек переключается на одну из них — `openId`
        становится чужим номером; правая колонка покажет пустоту, но
        `dialogsStateSet()` проверяет только истинность `openId`, а не
        наличие анкеты в списке, и включит «Загрузить диалоги» — нажатие
        погонит сценарий входа под анкетой другого сайта.

        ⚠ Сторожим не слово `openByTab` (оно есть и в пояснительном комментарии
        над правкой — исчезни рабочая строка, комментарий его всё равно
        оставит), а структуру самого выражения обнуления: цикл по ключам
        `openByTab`, каждому из которых присваивается 0.
        """
        page = (await auth_client.get(PAGE)).text
        start = page.find("siteEl.addEventListener('change'")
        end = page.find('importBtn.addEventListener', start)
        assert start != -1 and end != -1, 'обработчик смены сайта не найден на странице'
        handler = page[start:end]

        reset_re = re.compile(
            r'Object\.keys\(openByTab\)\.forEach\(\s*\w+\s*=>\s*\{\s*'
            r'openByTab\[\w+\]\s*=\s*0\s*;?\s*\}\s*\)'
        )
        assert reset_re.search(handler), (
            'обработчик смены сайта не обнуляет каждый ключ openByTab циклом '
            '(Object.keys(openByTab).forEach(...) с присваиванием 0) — значит, '
            'чистит выбор через один openSet(0), то есть только у активной '
            'вкладки; у остальных трёх останутся номера анкет прежнего сайта. '
            'Упоминание слова "openByTab" в соседнем комментарии тест не '
            'засчитывает — нужен именно рабочий код.'
        )
