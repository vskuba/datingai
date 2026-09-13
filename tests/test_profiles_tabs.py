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

import os
import re
import shutil
import subprocess
import tempfile

import pytest

pytestmark = pytest.mark.page
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

        for tab in ('chat', 'fav', 'mail', 'stats'):
            assert f'data-tab="{tab}"' in page, f'вкладка {tab} пропала'

        # ⚠ Состав менялся: «Письма входящие» была пятой вкладкой и ушла внутрь
        # «Писем» подвкладками (почта у анкеты одна), а «Фавориты» выделились из
        # «Чата», где были третьей колонкой и делили ширину с диалогами.
        assert 'data-tab="mail_in"' not in page, \
            'вкладка входящих осталась — она переехала в подвкладки «Писем»'


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
        # ⚠ Про `mailing_on` тут больше не спрашиваем: отбор рассылки его не
        # читает вовсе — в неё идут все включённые анкеты, которые сейчас в сети
        # (`dating_mailing._girls`). Проверка на флаг сторожила бы отменённое
        # правило и падала бы при каждом верном изменении.
        assert 'TAB_ROWS' in page

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

        # ⚠ Строку счёта собирает `tablePaint` и отдаёт её `pagerRender` пятым
        # доводом; номер страницы и «всё на одной странице» пейджер дописывает
        # сам. Здесь нас занимает только знаменатель — он остаётся за списком.
        footer_match = re.search(r'const counted\s*=(.*?);', body, re.S)
        assert footer_match, (
            'счёт строк для подвала пейджера не нашёлся в tablePaint'
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


class TestPageScriptIsValidJs:
    """Страница отдаёт исполнимый скрипт, а не только правильную разметку.

    ⚠ **Класс находок, который текстовые проверки не видят по построению.** Все
    тесты выше сравнивают **текст** отданной страницы — узел на месте, атрибут
    стоит, слово встречается. Ни один из них не выполняет `<script>`: страница со
    сломанным синтаксисом внутри модуля отдастся тем же кодом 200, с той же
    разметкой, и все текстовые тесты позеленеют — а скрипт не запустится в
    браузере ни на одной вкладке.

    Не гипотеза: ровно такой случай был в этой работе. План предписывал второй
    `const rows` в той же области видимости `tablePaint` — `SyntaxError`, и сюита
    его не увидела бы. Поймал исполнитель, читая код перед правкой, а не тест.

    Модуль на странице не единственный — их четыре (общие импортированные модули
    заворачиваются в свои теги, плюс собственный скрипт страницы), и нужен именно
    **самый большой**: он и есть скрипт `dating_profile.html`, порядка 92 КБ,
    остальные — короткие обёртки.
    """

    async def test_script_has_no_syntax_error(self, auth_client):
        node = shutil.which('node')
        if not node:
            pytest.skip('node не найден в PATH — проверка синтаксиса скрипта пропущена')

        page = (await auth_client.get(PAGE)).text

        # ⚠ Регулярка тега — `<script[^>]*>`, а не голое `<script>`: без учёта
        # атрибутов `<script type="module">` не нашёлся бы вовсе, а на странице
        # именно такой модульный тег и несёт нужный код.
        blocks = re.findall(r'<script[^>]*>(.*?)</script>', page, re.S)
        assert blocks, 'на странице не нашлось ни одного <script>'

        # Самый большой блок — скрипт страницы; остальные — короткие обёртки
        # общих модулей и разметка шапки панели.
        script = max(blocks, key=len)
        assert len(script) > 10000, (
            'самый большой <script> подозрительно мал — похоже, найден не тот блок')

        # ⚠ Расширение — `.mjs`, а не `.js`: в скрипте есть `import`, и с `.js`
        # `node --check` разобрал бы его как CommonJS и упал на самом импорте,
        # а не на настоящей ошибке (если она есть).
        fd, path = tempfile.mkstemp(suffix='.mjs')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(script)

            result = subprocess.run([node, '--check', path],
                                    capture_output=True, text=True)
            assert result.returncode == 0, (
                f'скрипт страницы не проходит `node --check`: {result.stderr}')
        finally:
            os.remove(path)


class TestDialogsButtonIsGone:
    """⚠ «Загрузить диалоги» в шапке больше нет — и не должно вернуться.

    Кнопка входила на сайт браузером соседа и снимала список тех, с кем у анкеты
    есть переписка: сорок секунд на нажатие. Нужна она была, пока диалоги брались
    оттуда; теперь список строится из своей базы (`/chat/dialogs`), а сообщения
    приносит круг воркера сам.

    ⚠ **Тест сторожит не кнопку, а её обвязку.** Вместе с ней ушли три вещи, и
    каждая была мертва без нажатия: признак «настроен ли импорт» (он решал, красить
    ли кнопку), значок «с ним есть переписка» в лентах и счёт диалогов из её
    снимка. Вернись любая — она снова стояла бы пустой рядом с полным списком.

    Заодно здесь остался след старой беды: кнопка держала ключ `busy` на время
    прогона, и собранный заново из `openId` он снимал **чужой** ключ — свой
    оставался навсегда, а оба круга обновления начинаются с `if (busy.size)
    return`, и страница молча замолкала до перезагрузки. Кнопки нет, а приём
    «номер запоминаем один раз» остался в `peopleLoad`.
    """

    async def test_the_button_is_absent(self, auth_client):
        """⚠ Ищем **кнопку**, а не слова. Имя её осталось в комментариях — там
        объяснено, почему её нет и что ушло вместе с ней, и это знание нужнее
        буквальной чистоты: вернуть кнопку проще, чем заново дознаться, зачем её
        убрали."""
        page = (await auth_client.get(PAGE)).text

        assert 'id="dialogsBtn"' not in page, 'кнопка вернулась в шапку'
        assert '>Загрузить диалоги' not in page, 'кнопка вернулась в шапку'
        assert 'function chatDialogsBind' not in page, 'обработчик кнопки вернулся'

    async def test_its_dead_wiring_is_gone_too(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert 'chatPanelReady' not in page, (
            'признак «настроен ли импорт» снова спрашивается — он решал только '
            'цвет удалённой кнопки'
        )
        assert 'dialogHas' not in page, (
            'значок «есть переписка» вернулся: без кнопки снимок диалогов никто '
            'не наполняет, и значок не появится никогда'
        )

    async def test_the_dialog_count_comes_from_the_handle(self, auth_client):
        """⚠ Счёт диалогов — из ответа `/chat/dialogs`, а не из снимка сайта.

        Прежде он брался у списка, который снимала кнопка; без нажатия снимка не
        было, и счётчик стоял пустым **рядом с полным списком**. Считали одно,
        показывали рядом с другим.
        """
        page = (await auth_client.get(PAGE)).text

        assert 'dialogsCount()' not in page, 'счёт снова берётся из снимка сайта'
        # ⚠ Ищем `data.contacts_total` **в подписи счётчика**, а не целую строку
        # присваивания: подпись с тех пор стала текстом («всего диалогов: N»), и
        # проверка на точное `count.textContent = data.contacts_total` падала на
        # оформлении, хотя источник числа не менялся. Условие теста — источник.
        assert 'всего диалогов: ${data.contacts_total' in page, \
            'счёт диалогов не из ответа ручки'


class TestTabsSurviveReload:
    """⚠ Открытая вкладка переживает перезагрузку — и вкладка, и подвкладка писем.

    Страницу обновляют посреди работы: посмотреть, добрал ли круг пул, ушло ли
    письмо, вернуться из чужой вкладки браузера. Возвращаясь каждый раз на «Чат»,
    панель заставляла бы щёлкать по одному и тому же.

    Проверяется по разметке, а не браузером: правило здесь — «значение читается из
    хранилища и пишется туда при переключении», а это видно в исходнике. Живьём
    проверено отдельно, всеми четырьмя вкладками.
    """

    async def test_tab_is_read_from_storage(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert "localStorage.getItem(WORK_TAB_KEY)" in page, \
            'вкладка не читается из хранилища — перезагрузка сбросит её на «Чат»'
        assert "localStorage.setItem(WORK_TAB_KEY, workTab)" in page, \
            'переключение вкладки не запоминается'

    async def test_mail_subtab_is_read_from_storage(self, auth_client):
        """Подвкладка «Рассылка / Доставка» — своим ключом: вкладки называются
        по-разному и переключают разное."""
        page = (await auth_client.get(PAGE)).text

        assert "localStorage.getItem(MAIL_TAB_KEY)" in page
        assert "localStorage.setItem(MAIL_TAB_KEY, mailTab)" in page
        assert "'dating.profile.tab'" in page and "'dating.profile.mailtab'" in page, \
            'ключи слились в один — переключение одной вкладки двигало бы другую'

    async def test_unknown_tab_falls_back(self, auth_client):
        """⚠ Значение из хранилища сверяется со списком известных.

        Ключ переживает и переименование вкладки, и её удаление, а `workTab`,
        указывающий в никуда, — это пустая страница без единой нажатой кнопки.
        """
        page = (await auth_client.get(PAGE)).text

        assert 'WORK_TABS.includes(' in page, 'вкладка из хранилища не сверяется'
        assert 'MAIL_TABS.includes(' in page, 'подвкладка из хранилища не сверяется'

    async def test_work_is_painted_after_sites_arrive(self, auth_client):
        """⚠ Работу перерисовываем **после того, как узнали сайт**.

        Разметка работы выбирается по сайту (`workTemplate`), а первый
        `workPaint()` идёт до запроса списка сайтов: без сайта он ничего не рисует
        и сам больше не возвращается — круг перерисовки трогает только список, а
        `openSet` зовут при выборе анкеты, которого при загрузке ещё не было.

        Пока вкладка при загрузке всегда была «Чат», это не было видно: без
        выбранной анкеты она и так показывает подсказку. С запомненной вкладкой
        стало видно сразу — «Письма рассылка» живут без анкеты, и человек
        возвращался на пустое место вместо пула.
        """
        page = (await auth_client.get(PAGE)).text

        start = page.find('async function sitesLoad()')
        assert start != -1, 'загрузка сайтов не нашлась — разметка изменилась'
        end = page.find('async function profilesLoad()', start)
        assert end != -1
        body = page[start:end]

        assert 'workPaint()' in body, (
            'после загрузки сайтов работа не перерисовывается: запомненная вкладка '
            '«Письма рассылка» откроется пустой')


class TestPoolLinksToCompanion:
    """⚠ Имя адресата в карточке письма — ссылка на его карточку у нас.

    Письмо читают, чтобы решить, отправлять ли его, и первый вопрос — «кому это».
    Ответ лежит в карточке собеседника: описание, из которого агент писал, город,
    возраст, свежесть съёма.
    """

    async def test_name_is_a_link_to_our_page(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert 'function companionLink(' in page, 'ссылки на карточку адресата нет'

        # ⚠ Адрес собирается из двух кусков (`${BASE}/admin/dating` и хвост с
        # доводами), и проверять его одной строкой нельзя: перенос строки в
        # шаблоне ломал бы тест, ничего не ломая в панели. Смотрим обе половины
        # внутри самой функции.
        start = page.find('function companionLink(')
        body = page[start:start + 900]

        assert '/admin/dating' in body, 'ссылка ведёт не на страницу «Анкеты»'
        assert '?companion=' in body, 'ссылка не называет собеседника'

    async def test_link_carries_uid_and_site(self, auth_client):
        """⚠ Довод — `uid`, номер человека на сайте, а не наш `id` карточки: письмо
        знает первое, и оно не меняется. Сайт — отдельно: `uid` уникален внутри
        сайта, а ключ выбранного сайта общий на весь раздел."""
        page = (await auth_client.get(PAGE)).text

        assert 'companion=${encodeURIComponent(uid)}&site=${siteId()}' in page, \
            'ссылка потеряла номер человека или сайт'

    async def test_click_on_the_name_does_not_open_the_letter(self, auth_client):
        """Карточка щёлкается целиком, и окно письма, всплывшее за спиной под новой
        вкладкой, человек нашёл бы, только вернувшись обратно."""
        page = (await auth_client.get(PAGE)).text

        start = page.find(".mail-card-to')")
        assert start != -1, 'обработчик ссылки не найден'

        assert 'stopPropagation' in page[start:start + 400], \
            'щелчок по имени всплывает до карточки и открывает окно письма'


class TestReplyPoolWatchesSending:
    """⚠ Пул ответов сам показывает ход отправки и сам пустеет.

    Круг отправки уносит письма из пула без участия страницы. Пока обновления не
    было, человек видел ушедшие карточки до тех пор, пока не переключит вкладку, —
    и решал, что отправка не работает.
    """

    async def test_pool_has_a_refresh_loop(self, auth_client):
        """Круг обновления заведён и заводится **по надобности**."""
        page = (await auth_client.get(PAGE)).text

        assert 'function replyWatch(' in page, 'круга обновления пула нет'
        assert 'DATING_REPLY_WATCH_MS' in page, 'частота обновления не названа'

    async def test_sending_needs_three_conditions(self, auth_client):
        """⚠ «Идёт отправка» — это выключатель, смена **и непустой пул**.

        Без последнего крутилка висела бы над пустым пулом весь рабочий день:
        «идёт отправка», а отправлять нечего.
        """
        page = (await auth_client.get(PAGE)).text
        start = page.index('function replySending(')
        body = page[start:start + 400]

        assert 'reply_send_on' in body, 'выключатель не спрашивается'
        assert 'mailScheduleLive()' in body, 'смена не спрашивается'
        assert "status === 'draft'" in body, 'непустой пул не спрашивается'

    async def test_spinner_says_what_is_happening(self, auth_client):
        """⚠ Повод остался один — отправка, и слово одно.

        Прежде их было два: «пишу» над набором и «отправляю» над кругом. Набор
        ушёл на сторону сервера (`dating_reply_round.py`), и страница больше не
        знает, пишется ли ответ прямо сейчас. Соврать «пишу…» хуже, чем
        промолчать: человек ждал бы карточку, которой никто не обещал.
        """
        page = (await auth_client.get(PAGE)).text

        assert "'отправляю…'" in page
        assert "'пишу…'" not in page


class TestGirlNameIsALink:
    """Имя и лицо выбранной анкеты в шапке ведут на её карточку.

    ⚠ С «Профилей» постоянно уходят туда: там пароль, расписание и выключатели.

    ⚠ **Лицо — тоже ссылка**: по аватарке щёлкают первой, она крупнее и заметнее
    слова, и мёртвая картинка рядом с живой ссылкой читается как поломка.
    """

    async def test_name_links_to_the_card(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert 'work-who-link' in page, 'имя не ссылка'
        assert '/admin/dating?girl=' in page, 'ссылка ведёт не на карточку анкеты'

    async def test_the_face_links_to_the_card_too(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert 'id="workFaceLink"' in page, 'лицо не обёрнуто ссылкой'
        # Обёртка, а не сама картинка: пустая ссылка без содержимого всё равно
        # занимала бы место и ловила щелчки, ведя в никуда.
        start = page.index('id="workFaceLink"')
        assert 'target="_blank"' in page[start:start + 200], \
            'лицо открывается не новой вкладкой'

    async def test_the_counter_lives_outside_the_name(self, auth_client):
        """⚠ Счётчик собеседников — **своим узлом**, а не внутрь имени.

        Пока его дописывал `chatLoad`, он затирал имя целиком — и вместе с ним
        ссылку: на «Чате» имя переставало быть ссылкой через секунду после
        появления, и щёлкать было некуда.
        """
        page = (await auth_client.get(PAGE)).text

        assert 'id="workCount"' in page, 'нет узла под счётчик'
        assert "workWhoEl.textContent = " not in page, \
            'имя снова перезаписывают текстом — ссылка на анкету пропадёт'

    async def test_link_opens_in_a_new_tab(self, auth_client):
        """⚠ Новой вкладкой: человек читает письма, и уводить его со списка на
        полпути значит терять место, до которого он долистал.

        ⚠ Ищем **у самой ссылки имени** (`work-who-link`), а не рядом с адресом:
        адрес теперь собирается отдельной переменной (`куда`) — её делят имя и
        лицо, — и соседство с ней ничего не значит.

        ⚠⚠ **И проверяем пару целиком, а не одно имя.** Ровно это и сломалось:
        правка сняла `target` **только с имени**, лицо рядом его сохранило — и
        два узла с одним адресом и одной подсказкой «в новой вкладке» повели себя
        по-разному. Половинчатая правка проходила мимо соседнего теста, потому что
        тот смотрит лишь на лицо.
        """
        page = (await auth_client.get(PAGE)).text
        start = page.index('class="work-who-link"')

        assert 'target="_blank"' in page[start - 100:start + 200], \
            'имя открывается не новой вкладкой, хотя подсказка обещает её'

        # ⚠ `rel="noopener"` без `target` — примета той самой половинчатой правки:
        # сам по себе он ничего не значит и остаётся в разметке следом от того,
        # что убрали.
        assert 'rel="noopener"' in page[start:start + 200], \
            'у ссылки имени пропал rel="noopener"'


class TestListsAreSortedByStatusThenName:
    """⚠ Списки: **сперва в сети, потом по алфавиту** — и анкеты, и собеседники.

    Список анкет читают, чтобы найти работу, а работа есть у тех, кто на сайте:
    круги рассылки, инвайтов и переписки с избранными берут только их. При
    сортировке по логину живая анкета оказывалась двадцатой строкой среди спящих,
    и найти её можно было только поиском.

    ⚠ **Алфавит — по имени, а не по логину.** Логин это почта
    (`natalypetranetskaya67@bigmir.net`), и упорядоченный по ней список для
    человека случаен: в строке он видит имя.
    """

    async def test_profile_query_sorts_by_status_and_name(self, auth_client):
        """Порядок задаёт **запрос**, а не страница: список приходит страницами,
        и сортировка на клиенте переставляла бы людей внутри страницы, оставляя
        их на чужих местах между ними."""
        import inspect

        from src.uvicorn.routers.dating import dating_profile

        source = inspect.getsource(dating_profile)

        assert "ORDER BY g.status = 'online' DESC" in source, \
            'анкеты в сети больше не поднимаются наверх'
        assert "JSON_EXTRACT(g.profile, '$.name')" in source, \
            'алфавит снова по логину, а не по имени'

    async def test_people_widgets_sort_without_a_locale(self, auth_client):
        """⚠ Ленты сортирует страница — снимок приходит в порядке сайта.

        Локаль при этом **не задаётся**: с `'ru'` кириллица встаёт перед
        латиницей, и на сайте с английскими именами один «Нурлан» оказывался
        первой карточкой ленты. Поймано снимком страницы.
        """
        page = (await auth_client.get(PAGE)).text

        assert 'function peopleSorted' in page, 'ленты больше не сортируются'
        assert "localeCompare(String(b.name || '').trim(), undefined," in page, \
            'вернулась локаль в сравнении имён — кириллица уедет в начало'
        assert 'peopleSorted(state.people)' in page, \
            'сортировка есть, но списки рисуются мимо неё'


class TestReplyChangesRepaintBothPlaces:
    """⚠ Пул ответов показан в **двух** местах, и перерисовать надо оба.

    Ответ лежит в одном списке (`mailReply`), но виден и на «Письмах → Входящие»,
    и в форме под разговором на «Фаворитах → Диалоги». Пока перерисовывались
    только «Входящие», нажатие «Убрать из пула» на «Фаворитах» ничего не меняло на
    экране: ответ уходил из базы, а форма оставалась с текстом — до перезагрузки
    страницы. Ровно это и заметили.

    ⚠ Сторожим по исходнику отданной страницы: браузера у сюиты нет, а щелчок по
    кнопке воспроизводится только в нём.
    """

    async def test_drop_and_save_go_through_the_common_repaint(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert 'function replyShown' in page, \
            'общей перерисовки пула ответов нет — места разойдутся'
        assert 'replyShown(box);' in page, 'удаление из пула рисует не через неё'
        assert page.count('replyShown(') >= 3, \
            'общую перерисовку зовут не отовсюду, где ответ меняется'

    async def test_repaint_touches_both_editors(self, auth_client):
        """Обе формы — и входящих, и фаворитов — в одной перерисовке."""
        page = (await auth_client.get(PAGE)).text

        start = page.find('function replyShown')
        body = page[start:start + 500]

        assert 'mailInReplyEditRefresh()' in body, \
            'форма под письмом на «Входящих» не перерисовывается'
        assert 'favTalkEditRefresh()' in body, \
            'форма под разговором на «Фаворитах» не перерисовывается'

    async def test_repaint_touches_the_thread_too(self, auth_client):
        """⚠ Ленту разговора тоже, и формы мало.

        Ответ показан дважды: полем правки под разговором и репликой в самой ленте,
        с пометкой «в пуле». Убрали его из пула — исчезало поле, а реплика с чипсом
        оставалась висеть, будто ответ на месте; уходила она только с перезагрузкой
        страницы. Поймано на «Фаворитах → Диалоги».
        """
        page = (await auth_client.get(PAGE)).text

        start = page.find('function replyShown')
        body = page[start:start + 500]

        assert 'replyThreadRefresh()' in body, 'лента разговора не перечитывается'
        assert 'function replyThreadRefresh' in page, \
            'перечитывания ленты нет вовсе'

    async def test_modal_closes_only_when_it_is_a_modal(self, auth_client):
        """⚠ Прежде здесь стояло `if (box) modalClose()`, а `box` — узел формы
        **фаворитов**, не модалки: закрывалось окно, которого нет, и перерисовка
        не делалась вовсе. Теперь окно закрывается, только если правка шла из него.
        """
        page = (await auth_client.get(PAGE)).text

        assert "box.closest('.modal')" in page, \
            'окно закрывается вслепую — немодальная правка снова потеряет перерисовку'


class TestDeliveryLooksLikeDialogs:
    """«Доставка» инвайтов: обе стороны с лицом и номером, как в «Диалогах».

    ⚠ **Проверяем исходный текст страницы, а не отрисовку.** Строки рисует JS по
    ответу ручки, и поднимать браузер ради разметки дороже, чем сообщает. Что
    отрисовка сходится, проверено вживую headless-браузером при заведении.
    """

    async def test_delivery_row_uses_the_dialog_helper(self, auth_client):
        """Тот же `talkSide`, что у диалогов, — а не второй набор разметки.

        Вопрос у строки тот же самый («кто кого позвал»), и два разных вида
        одного вопроса человек читает как два разных списка.
        """
        page = (await auth_client.get(PAGE)).text
        window = _delivery_row(page)

        assert 'talkSide(row.profile_name, row.profile_photo, row.profile_uid' in window, \
            'левая сторона строки собрана не общим помощником'
        assert 'talkSide(row.name, row.photo, row.uid' in window, \
            'правая сторона строки собрана не общим помощником'

    async def test_delivery_arrow_is_one_way(self, auth_client):
        """⚠ Односторонняя, в отличие от диалогов: инвайт — первое слово, и
        ответа у него по определению ещё нет. Придёт — разговор уедет в
        «Диалоги», где стрелка двусторонняя."""
        page = (await auth_client.get(PAGE)).text

        assert "'<span class=\"talk-swap\">→</span>'" in _delivery_row(page)


def _delivery_row(page: str) -> str:
    """Кусок страницы, где собирается строка доставки: от «кто кому» до конца `</div>`.

    ⚠ **Ищем по границам, а не «столько-то знаков вперёд».** Было второе, и первая
    же правка строки — имена стали чипсами отбора — увела вторую половину за окно:
    оба теста покраснели, хотя проверяемое стояло на месте. Окно с числом знаков
    ломается от любого комментария рядом.
    """
    start = page.index('class="mail-sent-who talk"')

    return page[start:page.index("+ '</div>';", start)]


class TestDeliveryFly:
    """Прыгающие конверты у слова «Доставка»: сколько ушло за пять минут."""

    def test_tab_has_a_place_for_them(self):
        """Узел — **внутри кнопки**: стоя рядом, он ломал бы полосу при переносе.

        ⚠ Читаем шаблон с диска, а не по HTTP: разметку работы отдаёт сервер по
        номеру сайта (`dating_work.py`), и живой номер в сюите означал бы, что
        проверка вида зависит от того, какие сайты сегодня заведены.
        """
        import pathlib

        work = pathlib.Path(
            'data/uvicorn/template/admin/profile/site/_base.html').read_text(encoding='utf-8')
        start = work.index('data-chat-tab="sent"')

        assert 'data-slot="inviteFly"' in work[start:start + 200]

    async def test_counted_by_the_feed_and_only_invites(self, auth_client):
        """⚠ По ленте, а не по списку доставки: список грузится, только когда
        открыта его подвкладка, — а знать «панель пишет сейчас» надо с любой.

        ⚠ Неудачи не считаем: они приходят видом `warn`, и конверт за них обещал
        бы отправку, которой не было.
        """
        page = (await auth_client.get(PAGE)).text
        start = page.index('function inviteFlyPaint')
        window = page[start:start + 900]

        assert "row.kind === 'invite'" in window, 'считаются не только инвайты'
        assert 'FLY_WINDOW_SEC' in window, 'окно в пять минут не спрашивается'

    async def test_feed_is_asked_for_the_whole_ring(self, auth_client):
        """⚠ Иначе конверты врут: по умолчанию лента отдаёт десяток, а при минуте
        на реплику первое же чужое событие вытеснило бы инвайт из счёта."""
        page = (await auth_client.get(PAGE)).text

        assert 'limit=${FEED_LIMIT}' in page
        assert 'const FEED_LIMIT = 30;' in page


class TestPoolHighlight:
    """Выбрали человека в ленте «Все онлайн» — в пуле подсветилось всё, что ему
    приготовлено.

    ⚠ Выбрав кого-то, человек спрашивает «а с ним у нас что?». Пул отвечает на это
    молча, если карточки в нём ничем не отличаются.
    """

    async def test_every_pool_card_carries_the_uid(self, auth_client):
        """Без номера в карточке искать её пришлось бы по имени, а имена на сайте
        повторяются.

        Пулов три — инвайты, письма избранным и рассылка, — и номер нужен всем:
        подсветку делает один обход по `.mail-card[data-uid]`.
        """
        page = (await auth_client.get(PAGE)).text

        assert page.count('data-uid="${esc(one.uid') == 2, \
            'инвайты или письма избранным остались без номера в карточке'
        assert page.count('data-uid="${esc(row.uid') == 1, \
            'карточка рассылки осталась без номера'

    async def test_empty_choice_highlights_nothing(self, auth_client):
        """⚠ Иначе снятие выбора зажигало бы весь пул разом: карточки без номера
        сравнялись бы с пустой строкой."""
        page = (await auth_client.get(PAGE)).text
        start = page.index('function poolHit(uid)')

        assert 'Boolean(openUid)' in page[start:start + 200]

    async def test_choice_repaints_in_place(self, auth_client):
        """⚠ Правим классы обходом, а не перерисовкой пула: перерисовка сбросила бы
        прокрутку списка, в котором человек как раз и ищет."""
        page = (await auth_client.get(PAGE)).text
        start = page.index('function poolHitPaint(')
        window = page[start:start + 900]

        assert "querySelectorAll('.mail-card[data-uid]')" in window
        assert 'classList.toggle' in window


class TestPoolHitScroll:
    """Подсвеченную карточку подводим к глазам: пул длинный, и она бывает внизу."""

    async def test_choice_scrolls_the_card_into_view(self, auth_client):
        page = (await auth_client.get(PAGE)).text
        start = page.index('function poolHitPaint(')
        window = page[start:start + 900]

        assert 'scrollIntoView' in window, 'подсветили, но не показали'
        assert "block: 'nearest'" in window, \
            '⚠ `center` дёргал бы список и там, где карточка и так на экране'

    async def test_only_on_choice_not_on_every_repaint(self, auth_client):
        """⚠ Пул перерисовывается своим кругом, и прокрутка на каждый круг уводила
        бы список из-под руки у того, кто его листает."""
        page = (await auth_client.get(PAGE)).text

        assert 'poolHitPaint({scroll: true})' in page, 'выбор не просит прокрутки'
        assert page.count('poolHitPaint({scroll: true})') == 1, \
            'прокрутку просит кто-то ещё, кроме выбора'


class TestPoolFirstUid:
    """Выбранный в ленте уходит первым в очереди набора — обоими кнопками."""

    async def test_invite_button_sends_the_choice(self, auth_client):
        page = (await auth_client.get(PAGE)).text
        start = page.index('/api/dating/invite/generate')

        assert 'first_uid: openUid' in page[start:start + 400]

    async def test_mailing_button_sends_the_choice(self, auth_client):
        page = (await auth_client.get(PAGE)).text
        start = page.index('/api/dating/mailing/generate')

        assert 'first_uid: openUid' in page[start:start + 400]


class TestOnlineCardPriority:
    """Звёздочка «этого первым» на карточке ленты «Все онлайн».

    ⚠ **Отметка durable, а не подсветка выбора.** Выбор карточки живёт в браузере,
    и круг, который набирает пул по расписанию, о нём не знает — он работает в
    воркере. Приоритет же лежит в карточке собеседника, и его слушаются **оба**
    набора: и кнопка «Генерировать», и круг.
    """

    async def test_star_is_on_the_card(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert 'function personCardFirst(' in page, 'звезды нет вовсе'
        assert 'class="person-card-first' in page

    async def test_star_calls_the_priority_handle(self, auth_client):
        """Ручка своя (`PUT /{id}/priority`), а не общая правка карточки: та пишет
        почту и описание разом, и галочка гасла бы при каждом сохранении текста."""
        page = (await auth_client.get(PAGE)).text
        start = page.index('async function personCardFirstSet')
        window = page[start:start + 1200]

        assert '/priority`' in window
        assert "{priority: want}" in window

    async def test_missing_card_is_created_by_the_same_click(self, auth_client):
        """⚠ Приоритет живёт в карточке, но «сперва заведи, потом отметь» означало
        бы два действия на одно решение."""
        page = (await auth_client.get(PAGE)).text
        start = page.index('async function personCardFirstSet')
        window = page[start:start + 1200]

        assert 'personCardAdd(uid, star)' in window

    async def test_click_does_not_also_select_the_person(self, auth_client):
        """⚠ Иначе щелчок по звезде уводил бы страницу с того места, где стоял
        человек, ради действия, которое его не касается."""
        page = (await auth_client.get(PAGE)).text
        start = page.index("querySelectorAll('.person-card-first')")
        window = page[start:start + 300]

        assert 'stopPropagation' in window

    async def test_marked_star_is_visible_without_hover(self, auth_client):
        """⚠ Отметка меняет порядок работы круга, и видеть её надо не обводя ленту
        мышью — в отличие от значка «карточка заведена», который лишь сведение."""
        page = (await auth_client.get(PAGE)).text
        start = page.index('.person-card-first.on {')
        window = page[start:start + 200]

        assert 'opacity: 1' in window
