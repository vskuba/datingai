"""Вкладка «Намерения и действия»: вёрстка и привязка.

⚠ Сюита ходит по HTTP и браузера не имеет — щёлкнуть по вкладке она не может.
Поэтому сторожим то, что видно в ответе: порядок узлов и наличие привязки.
Нарисованная, но ни к чему не привязанная вкладка — тот случай, который тише
сломанного теста.

⚠⚠ **Разметка и поведение живут в разных файлах, и спрашивать их надо порознь.**
Кнопка и панель — в шаблоне работы (`profile/site/_base.html`), который приезжает
**своей ручкой** под выбранный сайт; `CHAT_TABS` и загрузка — в каркасе страницы
(`profile.html`). Проверь мы всё одним запросом к `/admin/dating/profile`, половина
тестов зеленела бы на пустом месте: шаблона работы в этом ответе нет вовсе.

⚠ Ручку шаблона зовём **в процессе**, как `test_work_template.py`: она спрашивает
у соседа имя сайта, и тест против живого зависел бы от того, поднят ли он.
"""

import os
import re

import pytest
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from src.uvicorn.routers.dating import work as work_module
from src.uvicorn.routers.dating.work import work_template

pytestmark = pytest.mark.page

PAGE = '/admin/dating/profile'
# Сайт поддельного соседа: у него имя `Ladadate`, и шаблон подбирается по нему.
LADA = 343


def _request() -> Request:
    """Запрос с той же Jinja, что у панели.

    Окружение собирается тем же вызовом и над тем же каталогом, что в
    `main_uvicorn.py`: ручка спрашивает список шаблонов у самой Jinja.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates = Jinja2Templates(directory=os.path.join(root, 'data/uvicorn/template'))

    class _App:
        class state:
            jinja2_template = templates

    return Request({'type': 'http', 'method': 'GET',
                    'path': '/admin/dating/profile/work',
                    'headers': [], 'query_string': b'', 'app': _App})


@pytest.fixture
async def page(auth_client):
    """Каркас страницы «Профили».

    ⚠ Своей фикстурой, а не вместе с `work`: кнопка верхнего ряда живёт в
    каркасе, а панель — в шаблоне работы, и приезжают они разными ручками.
    Спроси мы всё одним запросом, половина сторожей зеленела бы на пустом
    месте.
    """
    return (await auth_client.get(PAGE)).text


@pytest.fixture
async def work(rhapsody, monkeypatch):
    """Разметка работы с анкетой под сайт — так, как её получает браузер."""
    async def _setting(site_id, key, default=''):
        return ''

    monkeypatch.setattr(work_module, 'site_setting', _setting)

    answer = await work_template(_request(), site_id=LADA, user=None)

    return answer.body.decode()


class TestTheTabButton:
    """⚠⚠ Кнопка — **в верхнем ряду страницы**, а не в полосе «Чата».

    Обещание даётся в разговоре, но исполняется письмом, и подвкладкой «Чата»
    занятие пряталось за вкладкой, к которой относится только наполовину.
    Кнопка верхнего ряда живёт в каркасе страницы, а не в шаблоне работы.
    """

    async def test_the_button_is_there(self, page):
        assert 'data-tab="intention"' in page, 'кнопки вкладки нет'
        assert 'Намерения и действия' in page, 'подписи вкладки нет'

    async def test_it_stands_after_the_mail_tab(self, page):
        """⚠ Порядок — порядок чтения: «Письма» отвечают «что мы пишем сами»,
        намерения — «что мы обещали»."""
        # ⚠ Ищем **кнопки**, а не любое упоминание имени: в стилях страницы
        # есть селектор `.work-panel[data-tab="intention"]`, и он стоит выше
        # полосы — сторож на простом `find` сравнивал бы его с кнопками.
        order = re.findall(r'class="page-tab[^"]*"\s+data-tab="(\w+)"', page)

        assert order, 'кнопок вкладок нет — разметка сломана'
        assert order.index('mail') < order.index('intention') < order.index('stats'), (
            f'порядок вкладок не тот: {order}')

    async def test_the_chat_strip_lost_it(self, page, work):
        """⚠ Прежняя подвкладка убрана, а не оставлена вторым входом: два
        места на одно занятие — это вопрос «какое настоящее» на каждом заходе."""
        assert 'data-chat-tab="intention"' not in work, (
            'подвкладка «Чата» осталась вторым входом')
        assert "'intention'" not in page[page.find('const CHAT_TABS'):
                                         page.find('const CHAT_TABS') + 200], (
            'намерения всё ещё в списке подвкладок чата')


class TestThePane:
    """Панель с тремя колонками — в шаблоне работы, своей вкладкой."""

    async def test_the_pane_is_there(self, work):
        assert 'data-tab="intention"' in work, 'панели вкладки нет'
        assert 'data-intention-pane="talk"' in work, 'панели «Диалогов» нет'

    async def test_it_has_three_columns_and_two_splits(self, work):
        """Раскладка общая с «Диалогами» (`in-cols-3`): вкладки об одном, и две
        разные сетки на одну работу разошлись бы с первой правки."""
        # ⚠ Срез кончается на панели «Фаворитов», а не на конце документа: у её
        # «Диалогов» те же разделители (`split-reply`, `split-initiative`), и
        # жадный срез насчитал бы три вместо одного — то есть тест зеленел бы на
        # чужой разметке.
        start = work.find('data-intention-pane="talk"')
        end = work.find('data-intention-pane="sent"', start)
        pane = work[start:end if end != -1 else len(work)]

        assert 'in-cols in-cols-3' in pane, 'раскладка не трёхколонная'
        assert pane.count('data-slot="intention-pairs-list"') == 1
        # ⚠⚠ Средняя колонка — **исполненные**, и три колонки читаются как путь
        # работы: кому обещали → что уже сделано → что ещё ждёт. Прежде здесь
        # стоял материал намерения (переписка письмами пары), а «Сделано»
        # пряталось переключателем справа: пока исполнителя не было, показывать
        # там было нечего.
        assert pane.count('data-slot="intention-done-list"') == 1
        assert pane.count('data-slot="intention-pool-list"') == 1
        assert pane.count('split-reply') == 1 and pane.count('split-initiative') == 1

    async def test_the_done_column_loads_on_its_own(self, work, page):
        """⚠⚠ У исполненных **своя** загрузка, а не переключатель над общим
        списком.

        Прежде оба отбора делили одну колонку и один набор переменных (список,
        страница, счёт), и показать их разом было нельзя. Теперь они стоят
        рядом, и каждому нужна своя страница: пул листают, пока история
        исполненного остаётся на месте.
        """
        assert 'data-slot="intention-done-pager"' in work, (
            'у колонки исполненных нет своего пейджера')
        assert 'intentionDonePage' in page, 'страница исполненных общая с пулом'
        assert "status=done" in page, 'исполненные не отбираются отдельно'
        assert "status=open" in page, 'пул больше не отбирает только открытые'

    async def test_the_switcher_is_gone(self, work, page):
        """⚠ Переключатель убран, а не оставлен вторым входом: два места на
        один вопрос — это «какое настоящее» на каждом заходе."""
        assert 'data-slot="intention-pool-open"' not in work, (
            'переключатель остался в шапке пула')
        assert 'intentionPoolStatus' not in page, (
            'состояние переключателя живёт в скрипте без самого переключателя')

    async def test_every_paged_column_has_a_pager(self, work):
        """⚠ Список, отданный целиком, кончается молча: конец прокрутки читается
        как конец списка. Эта грабля уже стоила трёхсот невидимых переписок.

        ⚠ Средней колонки здесь нет: она показывает **разговор**, а не список.
        У переписки конец — это последнее письмо, и страницы ей не нужны.
        """
        for name in ('intention-pairs-pager', 'intention-pool-pager'):
            assert f'data-slot="{name}"' in work, f'у колонки нет пейджера: {name}'


class TestThePageKnowsTheTab:
    """Поведение — в каркасе страницы, и оно обязано знать про вкладку."""

    async def test_the_name_is_known_to_the_script(self, page):
        """⚠⚠ Главный сторож: кнопка, которой нет в `WORK_TABS`, нарисована и
        мертва — щелчок по ней не покажет ничего, и тест на наличие узла это
        пропустит."""
        assert "const WORK_TABS = ['chat', 'fav', 'mail', 'intention', 'stats']" \
            in page, 'имя вкладки не попало в WORK_TABS'

    async def test_it_counts_as_site_wide(self, page):
        """⚠⚠ Без строки в `siteWide` панель не грузится вовсе, пока не выбрана
        анкета, — и выглядит это как «вкладка не работает». Поймано живым
        браузером: верхняя кнопка была, панелей в DOM ноль."""
        spot = page.find('const siteWide =')

        assert spot > 0 and "workTab === 'intention'" in page[spot:spot + 300], (
            'намерения не в списке вкладок про весь сайт')

    async def test_the_loader_is_wired(self, page):
        """⚠ Сторожим **наличие ветки и вызова**, а не их форму в одну строку:
        ветка обросла вторым вызовом (состояние галочки), и тест на точное
        совпадение строки покраснел бы на верной правке."""
        assert "if (workTab === 'intention')" in page, \
            'вкладка не наполняется — ветки в workPaint нет'
        assert 'intentionShow();' in page, 'колонки вкладки не загружаются'

    async def test_the_reload_buttons_are_wired(self, auth_client):
        """Кнопки ⟳ нарисованы в шаблоне работы, а слушает их страница: без
        привязки они выглядят рабочими и молчат."""
        page = (await auth_client.get(PAGE)).text

        assert 'data-slot="intention-pairs-reload"' in page
        assert 'data-slot="intention-pool-reload"' in page


class TestTheSwitchSitsRight:
    """⚠ Галочка «выполнять действия» — в правом краю полосы вкладок, как
    настройки у почты, инвайтов и фаворитов: человек ищет «где это включается»
    один раз, а не заново на каждой вкладке."""

    async def test_it_is_inside_the_strip(self, work):
        strip = work[work.find('intention-tabs'):work.find('intention-pane')]

        assert 'chat-intention-do-on' in strip, (
            'галочка стоит вне полосы вкладок — своей строкой под ней')
        assert 'mail-tabs-set' in strip, (
            'галочка не в общей коробке настроек: вправо её уводит '
            '`margin-left: auto` именно у неё')

    async def test_it_wears_the_shared_classes(self, work):
        """⚠ Классы `sched-on` и `sched-on-box` — те же, что у соседей: голый
        `<label>` брал размер галочки у браузера, и она выходила крупнее, чем
        у «генерации писем» через вкладку. Одна настройка на панели обязана
        выглядеть одинаково везде."""
        start = work.find('chat-intention-do-on')
        label = work[work.rfind('<label', 0, start):start]

        assert 'sched-on' in label, 'подпись галочки не общего вида'
        assert 'sched-on-box' in work[start - 80:start], (
            'сама галочка не общего вида — размер возьмёт у браузера')

    async def test_the_strip_is_stretched(self, page):
        """⚠⚠ Без растяжки коробка обнимала бы две кнопки, и настройка вставала
        бы вплотную к «Доставке» — то есть читалась бы третьей вкладкой."""
        spot = page.find('.intention-tabs {')

        assert spot > 0 and 'justify-self: stretch' in page[spot:spot + 160], (
            'полоса не растянута — `margin-left: auto` не сработает')


class TestThePoolTellsWhenItRunsAgain:
    """⚠ Срок круга в шапке пула: «сколько ждёт» и «когда добавится» — один
    взгляд. Без него включённая галочка читалась как «включил, а оно не
    работает»: ждать минуту или час — по надписи не понять."""

    async def test_the_handle_sends_it(self, auth_client):
        from tests.conftest import TEST_STAND_SITE

        got = (await auth_client.get(
            f'/api/dating/chat-intention?site_id={TEST_STAND_SITE}')).json()

        assert 'next_sec' in got, 'ручка не отдаёт срок круга'
        assert int(got['next_sec']) >= -1, 'срок не число секунд'

    async def test_it_rides_with_the_list(self, page):
        """⚠ Срок приезжает **вместе со списком**: два запроса дали бы два
        момента времени, и счёт спорил бы со сроком в одном взгляде."""
        assert 'got.next_sec' in page, 'страница не читает срок из ответа списка'
        assert 'intention-pool-next' in page, 'сроку негде показаться'

    async def test_the_text_is_one_pattern(self, page):
        """⚠⚠ Текст стоял **двумя копиями слово в слово** (инвайты и письма
        избранным) и различался одной фразой. Третий пул сделал бы её третьей,
        и разошлись бы они не формой, а правилом: «через 90 минут» у одного и
        «через 1 ч 30 мин» у другого читались бы как разные механизмы.
        """
        assert page.count('Math.floor(mins / 60)') == 1, (
            'выкройка срока снова размножилась копиями')
        assert 'function roundNextText(' in page, 'общей выкройки нет'

    async def test_the_countdown_is_not_a_second_heading(self, page):
        """⚠ Шапка колонки задаёт `uppercase` и жирность всему, что в ней
        лежит, и срок выходил вторым заголовком: «СОБЕРЁТ В БЛИЖАЙШУЮ МИНУТУ»
        рядом с «НАМЕРЕНИЙ: 9» читалось как две подписи равного веса, а
        переносом — как вторая строка заголовка. Сбрасываем, как у пула
        писем (`.mail-pool-busy`)."""
        spot = page.find('[data-slot="intention-pool-next"] {')
        rule = page[spot:page.find('}', spot)]

        assert spot > 0, 'у срока нет своего вида — возьмёт вид заголовка'
        assert 'text-transform: none' in rule and 'font-weight: 400' in rule, (
            f'срок остался заголовком: {rule.strip()}')

    async def test_the_verb_is_a_parameter(self, page):
        """⚠ Намерения круг не **набирает** — пул наполняет прогон агента, — а
        собирает по нему письма. Правь мы текст подстановкой в готовую строку,
        первая же правка выкройки тихо вернула бы чужой глагол."""
        assert "'по сроку круга', 'соберёт')" in page, (
            'глагол намерений не передаётся доводом')
        assert ".replace('наберёт'" not in page, (
            'глагол правится подстановкой в готовой строке')


class TestTheCardCanBeDropped:
    """⚠ Корзинка у карточки намерения: пул чистится руками.

    Намерение, которому собрать контекст было нечем, остаётся открытым
    навсегда — такие и копятся. Без корзинки убрать его можно было только
    исполнением, то есть отправкой письма, которого нет.
    """

    async def test_the_button_is_on_every_card(self, page):
        assert 'class="in-card-drop"' in page, 'корзинки на карточке нет'
        assert 'data-drop="${Number(row.id) || 0}"' in page, (
            'корзинка не несёт номера намерения')

    async def test_it_sits_in_the_corner(self, page):
        """⚠ `absolute` в правом нижнем углу: карточек в колонке десятки, и
        своя строка у каждой кнопки отняла бы у списка экран."""
        spot = page.find('.in-card-drop {')
        rule = page[spot:page.find('}', spot)]

        assert 'position: absolute' in rule and 'right:' in rule and 'bottom:' in rule, (
            f'корзинка не в углу: {rule.strip()}')

    async def test_the_click_does_not_pick_the_pair(self, page):
        """⚠ Карточка целиком кликабельна (выбор пары), и без остановки
        всплытия удаление заодно открывало бы переписку того, кого удаляем."""
        spot = page.find("querySelectorAll('.in-card-drop')")

        assert spot > 0, 'корзинка ни к чему не привязана'
        assert 'event.stopPropagation()' in page[spot:spot + 260], (
            'щелчок по корзинке заодно выбирает пару')

    async def test_both_columns_are_reread(self, page):
        """⚠ Список пар слева считает намерения: оставь мы его прежним,
        счётчик спорил бы с пулом в том же взгляде."""
        spot = page.find('async function intentionDrop(')
        body = page[spot:page.find('\n    }', spot)]

        assert 'intentionPoolLoad();' in body and 'intentionPairsLoad();' in body, (
            'после удаления перечитывается только одна колонка')

    async def test_the_question_names_what_is_lost(self, page):
        """⚠⚠ Вопрос у этой корзинки **другой**, чем у соседней: та убирает
        письмо, оставляя обещание (круг соберёт заново), а эта убирает само
        обещание — вернуть его может только новый разговор."""
        spot = page.find('async function intentionDrop(')
        body = page[spot:page.find('\n    }', spot)]

        assert 'confirm(' in body, 'удаление не спрашивает — а оно необратимо'
        assert 'пообещает снова' in body, (
            'вопрос не говорит, что обещание не вернётся само')


class TestTheDeliveryIsFullWidth:
    """⚠⚠ Журнал доставки — во всю ширину панели, как у писем и инвайтов.

    Строка несёт отправителя, адресата, текст и исход. В колонке (`in-col`)
    от текста оставалась пара слов: замерено на живой странице — 516 пикселей
    из 986. Колонку завели ради вида кнопки ⟳, но вид берут классы шапки, и
    колонка для этого не нужна.
    """

    async def test_it_uses_the_mail_journal_classes(self, work):
        start = work.find('data-intention-pane="sent"')
        pane = work[start:work.find('</div>', work.find('intention-sent-list'))]

        assert 'mail-sent-head' in pane, 'шапка журнала не та, что у соседей'
        assert 'class="mail-sent"' in pane, 'список журнала не во всю ширину'

    async def test_the_column_wrapper_is_gone(self, work):
        """⚠ Именно обёртка и ужимала журнал — сторожим её отсутствие, а не
        присутствие классов: те могли бы стоять внутри колонки."""
        start = work.find('data-intention-pane="sent"')
        pane = work[start:work.find('intention-sent-list', start)]

        assert 'in-col' not in pane, (
            'журнал снова завёрнут в колонку и займёт треть ширины')

    async def test_the_sender_is_named(self, page):
        """⚠⚠ Без имени анкеты журнал писал «без имени»: при девятнадцати
        анкетах это значит «неизвестно, от кого ушло».

        ⚠ Имя берётся **из списка слева**, а не от сервера: журнал по сайту, и
        те же анкеты уже загружены страницей. Девятая копия правила «карточка →
        имя и лицо» на сервере была бы платой за то, что и так под рукой.
        """
        assert 'profile_name: girl.name || girl.login' in page, (
            'строки журнала не получают имени анкеты')


class TestTheDoCheckbox:
    """Галочка «выполнять действия» — в своей панели."""

    async def test_it_is_in_the_strip(self, work):
        assert 'data-slot="chat-intention-do-on"' in work, 'галочки нет'
        assert 'выполнять действия' in work, 'подписи нет'

    async def test_it_lives_in_its_own_panel(self, work):
        """⚠ Галочка про это занятие и больше ни про какое: на полосе «Чата»,
        где она стояла прежде, её больше нет."""
        start = work.find('data-slot="chat-intention-do-on"')
        panel = work.rfind('data-tab="intention"', 0, start)
        chat = work.rfind('data-tab="chat"', 0, start)

        assert panel != -1, 'галочки нет в панели намерений'
        assert panel > chat, 'галочка осталась в панели «Чата»'

    async def test_the_hint_tells_what_it_starts(self, work):
        """⚠⚠ Подсказка переписана: прежняя говорила «занятия, которое
        исполняет намерения, не существует вовсе», и это стало **неправдой** —
        круг сбора писем и отправка есть, живое письмо со снимком уже уходило.

        Обещать меньше, чем делаешь, не безобиднее, чем больше: человек не
        поднимет галочку, думая, что она ни к чему.
        """
        start = work.find('data-slot="chat-intention-do-on"')
        head = work.rfind('<label', 0, start)
        label = work[head:start]

        assert 'не существует' not in label, (
            'подсказка всё ещё отрицает занятие, которое работает')
        assert 'отправля' in label.lower(), (
            'подсказка не говорит, что галочка запускает')

    async def test_the_page_wires_it(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert "slot('chat-intention-do-on')" in page, 'страница её не рисует'
        assert 'chatIntentionDoSet' in page, 'переключение не привязано'
        assert "path: 'chat-intention-do-on'" in page, 'адрес ручки не тот'


class TestTheCardDrawsEveryMark:
    """⚠⚠ Карточка — **один шаблон на все метки**, и это надо стеречь.

    Метки присылает workflow у соседа, а поля контекста у них разные: у фото
    `letter` и `tags`, у истории `letter` и `story`. Знакомых полей ровно два,
    всё прочее рисуется общим списком пар «ключ: значение» — и первая же
    правка карточки «под фото» спрятала бы работу агента по другой метке
    молча, без единой ошибки: поле просто не нарисовалось бы.
    """

    async def test_unknown_fields_go_to_the_general_list(self, auth_client):
        """Поле `story` обязано доехать до экрана: его больше негде увидеть."""
        page = (await auth_client.get(PAGE)).text

        start = page.find('const INTENTION_FIELDS_KNOWN')
        assert start != -1, 'списка знакомых полей больше нет'

        known = page[start:page.find('\n', start)]

        assert "'letter'" in known and "'tags'" in known, (
            f'знакомые поля карточки стали другими: {known}')
        assert "'story'" not in known, (
            'поле истории попало в знакомые — значит у него свой вид, '
            'и общий список его больше не покажет')

        body = page[start:start + 900]

        assert 'INTENTION_FIELDS_KNOWN.includes(key)' in body, (
            'незнакомые поля контекста больше не рисуются — работа агента '
            'по новой метке пропадёт с экрана')

    async def test_the_photo_block_needs_tags(self, auth_client):
        """⚠ Превью снимка висит **на приметах**: у истории их нет, и пустое
        гнездо под фото на её карточке обещало бы вложение, которого не будет.
        """
        page = (await auth_client.get(PAGE)).text
        start = page.find('const INTENTION_FIELDS_KNOWN')
        # ⚠ Ищем до конца разметки карточки, а не окном в N знаков: комментарий
        # рядом удлинился — и окно перестало доставать до блока снимка, хотя
        # блок был на месте. Сторож падал на собственной мерке.
        body = page[start:page.find('function intentionMailThumb', start)]

        assert 'ctx.tags' in body and 'in-card-pick' in body, (
            'блок снимка отвязан от примет — он появится и у письма без фото')

    async def test_sent_letters_are_loaded_too(self, auth_client):
        """⚠⚠ Карточке «Сделано» нужны **ушедшие** письма, а не только пул.

        Страница спрашивала одно умолчание (`draft`), и у исполненного
        намерения письма не находилось никогда: карточка уходила в запасную
        ветку, где снимок **выбирается заново** по приметам. Выходило, что
        лента переписки показывает ушедший кадр, а карточка рядом — другой,
        который уехал бы, случись отправка сейчас.

        Измерено на проде: пул отдавал 0 писем, `delivery` — девять.
        """
        page = (await auth_client.get(PAGE)).text

        start = page.find('async function intentionMailLoad')
        assert start != -1, 'загрузка писем карточек пропала со страницы'

        body = page[start:start + 1400]

        assert 'status_=delivery' in body, (
            'письма ушедших намерений больше не грузятся — карточка '
            '«Сделано» снова покажет снимок, выбранный заново, а не тот, '
            'что вправду ушёл человеку')

    async def test_a_done_intention_never_falls_back_to_a_fresh_pick(
            self, auth_client):
        """⚠⚠⚠ Запасная ветка отбора — **только для неисполненного**.

        У сделанного намерения снимок уже ушёл, и показать его может лишь
        письмо. Ответ отбора говорит «какой выбрался бы сейчас» — про будущее,
        которого не будет: пропади письмо из списка, карточка снова молча
        подменила бы ушедший кадр свежим.
        """
        page = (await auth_client.get(PAGE)).text

        start = page.find("+ (ctx.tags")
        assert start != -1, 'блок снимка карточки пропал со страницы'

        body = page[start:start + 700]

        assert "row.status === 'done'" in body, (
            'карточка исполненного намерения снова может уйти в повторный '
            'отбор — две вкладки покажут разные снимки одного письма')
        assert 'data-photo' in body, (
            'гнездо отбора пропало вовсе — у намерения в пуле, которому '
            'письма ещё нет, исчезнет превью будущего снимка')
