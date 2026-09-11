"""⚠ Список анкет на «Анкетах» заполняет колонку по высоте экрана.

Строк на странице ровно столько, сколько влезает: страницы и прокрутка вместе —
это две навигации сразу, а список, кончающийся на середине колонки при пяти
страницах пейджера, выглядит поломкой. Число считается замером (`rowsFit`), а не
зашито: экран у каждого свой, и «обычного экрана» не существует.

⚠ **Что эти тесты видят, а что нет.** Сюита ходит по HTTP и браузера не имеет —
померить настоящую высоту колонки она не может. Здесь сторожится **механизм**:
что число строк считается, а не стоит числом, и что скрипт страницы вообще
исполним. Заполнение как таковое проверяется глазами и headless-браузером.
"""

import os
import re
import shutil
import subprocess
import tempfile

import pytest

pytestmark = pytest.mark.page
PAGE = '/admin/dating'


class TestListFillsColumn:
    """Число строк — от замера колонки, а не зашитое."""

    async def test_page_size_is_measured(self, auth_client):
        """⚠ Возврат зашитого числа этот тест ловит, и вот почему он нужен.

        Раньше здесь стояло `const PAGE_SIZE = 8` с пояснением «столько влезает
        на обычном экране». На экране 900 пикселей в колонку влезает четырнадцать,
        на 1200 — двадцать: треть колонки пустовала, а пейджер показывал пять
        страниц вместо трёх. Число под «обычный экран» такого экрана не знает.
        """
        page = (await auth_client.get(PAGE)).text

        assert 'function rowsFit(' in page, (
            'замер строк пропал — список снова режется числом, не зависящим от '
            'высоты колонки')
        assert 'perPage' in page, 'счёт строк на странице больше не хранится'

        # ⚠ Именно срез списка, а не любое упоминание: строку с прикидкой
        # (`PAGE_SIZE_GUESS`) трогать не за что — она законна и нужна первой
        # отрисовке, пока мерить ещё нечего.
        assert 'found.slice(profilesPage * PAGE_SIZE,' not in page, (
            'страница снова нарезается зашитым числом вместо замеренного')

    async def test_paint_converges(self, auth_client):
        """Подгонка ограничена счётчиком заходов.

        ⚠ Без потолка она могла бы качаться туда-сюда: пейджер занимает высоту,
        появляется и исчезает вместе с числом страниц, и на неудачном сочетании
        высот строки и подвала перерисовка вызывала бы саму себя без конца.
        """
        page = (await auth_client.get(PAGE)).text

        assert 'function profilesRender(tries' in page, (
            'у отрисовки списка пропал счётчик заходов — подгонка числа строк '
            'может зациклиться')
        assert 'profilesRender(tries - 1)' in page, 'заходы не расходуются'


class TestPageScriptIsValidJs:
    """Страница отдаёт исполнимый скрипт, а не только правильную разметку.

    ⚠ Тот же сторож, что у «Профилей», и по той же причине: страница со сломанным
    синтаксисом внутри модуля отдастся кодом 200, с целой разметкой, и все
    текстовые проверки позеленеют — а в браузере не выполнится ничего. У скрипта
    «Анкет» такой проверки не было вовсе, хотя он больше тысячи строк.
    """

    async def test_script_has_no_syntax_error(self, auth_client):
        node = shutil.which('node')
        if not node:
            pytest.skip('node не найден в PATH — проверка синтаксиса пропущена')

        page = (await auth_client.get(PAGE)).text

        # ⚠ Регулярка тега — `<script[^>]*>`: без учёта атрибутов
        # `<script type="module">` не нашёлся бы вовсе, а именно он несёт код.
        blocks = re.findall(r'<script[^>]*>(.*?)</script>', page, re.S)
        assert blocks, 'на странице не нашлось ни одного <script>'

        script = max(blocks, key=len)
        assert len(script) > 10000, (
            'самый большой <script> подозрительно мал — похоже, найден не тот блок')

        # ⚠ Расширение `.mjs`, а не `.js`: в скрипте есть `import`, и с `.js`
        # `node --check` разобрал бы его как CommonJS и упал на самом импорте.
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


class TestCompanionDeepLink:
    """⚠ Ссылка снаружи открывает нужного собеседника: `?companion=<uid>&site=<id>`.

    По ней приходят из карточки письма на «Профилях»: имя адресата ссылается сюда.
    Открыть надо ровно то, чего просили, — вкладку собеседников, нужный сайт и
    нужного человека.

    Проверяется по разметке: правило здесь — «довод прочитан, вкладка и сайт
    выставлены до первой загрузки, адрес почищен», и это видно в исходнике. Живьём
    проверено отдельно, всеми пятьюдесятью ссылками пула.
    """

    async def test_link_params_are_read(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert "linkParams.get('companion')" in page, 'номер собеседника из адреса не читается'
        assert "linkParams.get('site')" in page, (
            'сайт из адреса не читается: `uid` уникален внутри сайта, и без него '
            'страница открылась бы на том, что был выбран в прошлый раз')

    async def test_tab_and_site_are_set_before_loading(self, auth_client):
        """⚠ **До** первой загрузки, а не после: `sitesLoad` смотрит на `pageTab`,
        чтобы решить, грузить ли собеседников, а `datingSiteFill` — на ключ в
        хранилище, чтобы выбрать сайт. Поставь мы их позже — список приехал бы по
        прежнему сайту, а вкладка осталась бы на «Девушках»."""
        page = (await auth_client.get(PAGE)).text

        start = page.find('let wantCompanionUid')
        assert start != -1, 'разбор ссылки не найден'
        loading = page.find('sitesLoad();', start)
        assert loading != -1, 'загрузка сайтов не нашлась после разбора ссылки'

        block = page[start:loading]
        assert "pageTab = 'companion'" in block, 'вкладка не переключается на собеседников'
        assert 'localStorage.setItem(DATING_SITE_KEY' in block, 'сайт не выставляется'

    async def test_address_is_cleaned(self, auth_client):
        """⚠ Довод в адресе — это «открой», а не «всегда показывай». Не почисти
        мы адрес, обновление страницы через час снова уводило бы к тому же
        человеку, хотя человек давно смотрит другого."""
        page = (await auth_client.get(PAGE)).text

        assert 'history.replaceState' in page, 'адрес не чистится после разбора ссылки'

    async def test_missing_companion_is_said_out_loud(self, auth_client):
        """⚠ Не нашли — говорим словами. Молча открыть первого попавшегося было бы
        хуже всего: человек пришёл читать про конкретного и прочёл бы про чужого,
        не заметив подмены."""
        page = (await auth_client.get(PAGE)).text

        # ⚠ Ищем внутри загрузки списка, а не «где-нибудь рядом»: первое
        # упоминание `wantCompanionUid` — это разбор адреса в начале файла, до
        # которого сообщать ещё не о чем.
        start = page.find('async function companionsLoad()')
        assert start != -1, 'загрузка списка собеседников не найдена'
        end = page.find('function companionsPaint(', start)
        assert end != -1
        body = page[start:end]

        assert 'в списке нет' in body, 'о ненайденном собеседнике не сообщается'
        assert 'wantCompanionUid = ' in body, (
            'просьба не гаснет после разбора: она перебивала бы выбор человека '
            'при каждом обновлении списка')

    async def test_list_page_is_computed_after_measuring(self, auth_client):
        """⚠ Страница списка считается **в отрисовке**, а не при разборе ссылки:
        там `compPerPage` ещё догадка (`PAGE_SIZE_GUESS`), а настоящее число строк
        известно только после замера. Промах виден сразу — список открывается на
        странице, где искомого нет, и так оно и вышло на первой проверке.
        """
        page = (await auth_client.get(PAGE)).text

        assert 'let showCompanionId' in page, 'просьба «пролистай к нему» не заведена'

        start = page.find('function companionsPaint(')
        assert start != -1, 'отрисовка списка не найдена'
        end = page.find('function _окончание(', start)
        assert end != -1
        body = page[start:end]

        assert 'showCompanionId' in body, \
            'страница списка считается вне отрисовки — по догадке о числе строк'
        assert 'showCompanionId = 0' in body, (
            'просьба не гаснет: список возвращался бы на его страницу при каждой '
            'перерисовке, и полистать соседние было бы нельзя')
