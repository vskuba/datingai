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
async def work(rhapsody, monkeypatch):
    """Разметка работы с анкетой под сайт — так, как её получает браузер."""
    async def _setting(site_id, key, default=''):
        return ''

    monkeypatch.setattr(work_module, 'site_setting', _setting)

    answer = await work_template(_request(), site_id=LADA, user=None)

    return answer.body.decode()


class TestTheTabButton:
    """Кнопка в полосе «Чата» — в шаблоне работы."""

    async def test_the_button_is_there(self, work):
        assert 'data-chat-tab="intention"' in work, 'кнопки вкладки нет'
        assert 'Намерения и действия' in work, 'подписи вкладки нет'

    async def test_it_stands_after_the_block_tab(self, work):
        """⚠ Порядок — конец пути «Чата»: позвали → ушло ли → поговорили →
        этим не пишем → а вот что наобещали."""
        block = work.find('data-chat-tab="block"')
        mine = work.find('data-chat-tab="intention"')

        assert block != -1 and mine != -1, 'вкладок нет — разметка сломана'
        assert block < mine, 'вкладка стоит раньше «Не пишем»'


class TestThePane:
    """Панель с тремя колонками — там же."""

    async def test_the_pane_is_there(self, work):
        assert 'data-chat-pane="intention"' in work, 'панели вкладки нет'

    async def test_it_has_three_columns_and_two_splits(self, work):
        """Раскладка общая с «Диалогами» (`in-cols-3`): вкладки об одном, и две
        разные сетки на одну работу разошлись бы с первой правки."""
        # ⚠ Срез кончается на панели «Фаворитов», а не на конце документа: у её
        # «Диалогов» те же разделители (`split-reply`, `split-initiative`), и
        # жадный срез насчитал бы три вместо одного — то есть тест зеленел бы на
        # чужой разметке.
        start = work.find('data-chat-pane="intention"')
        end = work.find('data-tab="fav"', start)
        pane = work[start:end if end != -1 else len(work)]

        assert 'in-cols in-cols-3' in pane, 'раскладка не трёхколонная'
        assert pane.count('data-slot="intention-pairs-list"') == 1
        assert pane.count('data-slot="intention-done-list"') == 1
        assert pane.count('data-slot="intention-pool-list"') == 1
        assert pane.count('split-reply') == 1 and pane.count('split-initiative') == 1

    async def test_every_column_has_a_pager(self, work):
        """⚠ Список, отданный целиком, кончается молча: конец прокрутки читается
        как конец списка. Эта грабля уже стоила трёхсот невидимых переписок."""
        for name in ('intention-pairs-pager', 'intention-done-pager',
                     'intention-pool-pager'):
            assert f'data-slot="{name}"' in work, f'у колонки нет пейджера: {name}'


class TestThePageKnowsTheTab:
    """Поведение — в каркасе страницы, и оно обязано знать про вкладку."""

    async def test_the_name_is_known_to_the_script(self, auth_client):
        """⚠⚠ Главный сторож: кнопка, которой нет в `CHAT_TABS`, нарисована и
        мертва — щелчок по ней не покажет ничего, и тест на наличие узла это
        пропустит."""
        page = (await auth_client.get(PAGE)).text

        assert "const CHAT_TABS = ['invite', 'sent', 'talk', 'block', 'intention']" \
            in page, 'имя вкладки не попало в CHAT_TABS'

    async def test_the_loader_is_wired(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert "if (chatTab === 'intention') intentionShow();" in page, \
            'подвкладка не наполняется — ветки в chatSubShow нет'

    async def test_the_reload_buttons_are_wired(self, auth_client):
        """Кнопки ⟳ нарисованы в шаблоне работы, а слушает их страница: без
        привязки они выглядят рабочими и молчат."""
        page = (await auth_client.get(PAGE)).text

        assert 'data-slot="intention-pairs-reload"' in page
        assert 'data-slot="intention-pool-reload"' in page
