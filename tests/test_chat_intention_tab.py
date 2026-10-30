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
        # ⚠ Средняя колонка показывает **материал** намерения (у обещания фото
        # это переписка письмами), а не список исполненного: решить, годится ли
        # обещанное, можно только рядом с разговором. «Сделано» переехало
        # переключателем в правую колонку — вид не потерян.
        assert pane.count('data-slot="intention-stuff"') == 1
        assert pane.count('data-slot="intention-pool-list"') == 1
        assert pane.count('split-reply') == 1 and pane.count('split-initiative') == 1

    async def test_the_done_view_did_not_vanish_with_its_column(self, work):
        """⚠⚠ Колонку «Сделано» убрали, а вид обязан остаться.

        Исполнителя не было, и колонка стояла пустой всегда — но «убрать
        пустое» легко превращается в «потерять вид». Теперь это переключатель
        в шапке правой колонки, и отбор у него свой (`status=done`).
        """
        assert 'data-slot="intention-pool-open"' in work
        assert 'data-slot="intention-pool-done"' in work

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
