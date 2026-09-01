"""Шаблон работы с анкетой: разметка `#work` под конкретный сайт.

Проверяется то, ради чего шаблоны и разделены: **сайты получают разную разметку**, а
имя шаблона, приходящее из базы, не пускает никуда за пределы каталога.

Разметку сверяем по контрактным признакам (`data-slot`, классы блоков), а не по
вёрстке целиком: иначе тест падал бы на каждой правке отступов.

⚠ **Ручку зовём в процессе, а не по HTTP.** Она спрашивает у соседа имя сайта, и
тест против живого зависел бы от того, поднят ли он, — а имя сайта тут и вовсе
подставляется поддельным (`rhapsody_fake.py`). Настройка сайта лежит в MySQL и
подменяется здесь же: проверяем выбор шаблона, а не чтение настройки.

По HTTP остаётся то, что решается **до** входа в обработчик: вход и отсутствие
сайта.
"""

import os

import pytest
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from src.uvicorn.routers.dating import dating_work
from src.uvicorn.routers.dating.dating_work import _slug, dating_work_template

# Сайты у поддельного соседа: у первого имя `Ladadate`, у второго `Goldenbride` —
# по ним и подбирается файл шаблона.
LADA = 343
GOLD = 19

# Сайт, которого у соседа нет вовсе: его шаблон должен быть умолчанием.
UNKNOWN = 999_777


@pytest.fixture
def work(rhapsody, monkeypatch):
    """Зовёт ручку шаблона так, как это делает браузер, но в процессе.

    Настройка `template` задаётся тестом: она из базы, а базой тут ничего не
    проверяется.
    """
    chosen = {'value': ''}

    async def _setting(site_id, key, default=''):
        return chosen['value']

    monkeypatch.setattr(dating_work, 'dating_site_setting', _setting)

    async def _call(site_id: int, template: str = '') -> str:
        chosen['value'] = template
        answer = await dating_work_template(_request(), site_id=site_id, user=None)

        return answer.body.decode()

    _call.rhapsody = rhapsody

    return _call


class TestWorkTemplate:
    """Что отдаёт ручка."""

    async def test_template_has_slots_and_no_log(self, work):
        """⚠ Журнала в шаблоне сайта быть не должно.

        Он про анкету, а не про сайт: одна и та же лента нужна на любом, и держи мы
        её копию в каждом шаблоне — правка журнала означала бы правку всех. Место
        журнала — каркас страницы.
        """
        html = await work(LADA)

        assert 'data-slot="dialogs"' in html, 'нет слота под диалоги'
        assert 'data-slot="setup"' in html, 'нет слота под настройки'
        assert 'chat-log' not in html, 'журнал попал в шаблон сайта'
        assert 'id="log"' not in html

    async def test_every_tab_has_a_panel(self, work):
        """Панели всех четырёх вкладок приходят разом.

        Переключение вкладки тогда — показать одну и снять остальные, без запроса к
        серверу: шаблон один на сайт, и просить его заново на каждый щелчок незачем.
        """
        html = await work(LADA)

        for tab in ('chat', 'setup', 'mail', 'event'):
            assert f'data-tab="{tab}"' in html, f'нет панели вкладки «{tab}»'

    async def test_sites_get_different_markup(self, work):
        """⚠ Ради этого шаблоны и разделены.

        У Goldenbride не заведено сценариев ни под ленту «Все онлайн», ни под
        избранных: оба блока стояли бы навсегда с надписью «не снялось». Шаблон
        сайта убирает их, не трогая ни JS, ни другие сайты.
        """
        lada = await work(LADA)
        gold = await work(GOLD)

        assert 'online-row' in lada and 'people-col' in lada
        assert 'online-row' not in gold and 'people-col' not in gold
        # Диалоги есть у обоих: они из нашей базы и работают на любом сайте.
        assert 'data-slot="dialogs"' in gold

    async def test_site_without_own_template_gets_default(self, work):
        """Новый сайт должен открыться и работать до того, как под него напишут
        свой шаблон: иначе его нельзя было бы даже посмотреть."""
        html = await work(UNKNOWN)

        assert 'data-slot="dialogs"' in html

    async def test_unreachable_neighbour_still_gives_markup(self, work):
        """⚠ Лежащий сосед страницу не закрывает.

        Разметка `#work` от него не зависит: он нужен только чтобы узнать имя сайта
        и подобрать по нему файл. Не ответил — берём умолчание, а не 502.
        """
        work.rhapsody.offline()

        html = await work(LADA)

        assert 'data-slot="dialogs"' in html


class TestTemplateBySetting:
    """Шаблон можно назначить сайту руками."""

    async def test_setting_wins_over_name(self, work):
        """Настройка `template` сильнее имени сайта: два сайта могут делить один
        шаблон, а имя не всегда годится в имя файла."""
        html = await work(LADA, template='goldenbride')

        assert 'online-row' not in html, 'назначенный шаблон не подхватился'

    async def test_missing_template_falls_back(self, work):
        """Назначенный шаблон могли удалить. Молча берём умолчание, а не отдаём
        500: страница откроется и будет работать, а расхождение видно в логе."""
        html = await work(LADA, template='такого-нет')

        assert 'data-slot="dialogs"' in html


class TestTemplateNameIsSafe:
    """⚠ Имя шаблона приходит из базы — значит, ему нельзя доверять."""

    @pytest.mark.parametrize('attack', ['../dating_profile', '../../login', '/etc/passwd',
                                        '..%2f..%2fbase', '_base'])
    async def test_traversal_does_not_read_other_files(self, work, attack):
        """Возьми ручка значение как путь — получили бы чтение чужих шаблонов, а
        дальше и любого файла, до которого дотянется Jinja.

        Проверяем итог, а не механику: что бы ни лежало в настройке, наружу уходит
        шаблон работы с анкетой.
        """
        html = await work(LADA, template=attack)

        assert 'data-slot="dialogs"' in html, f'«{attack}» подменил шаблон'
        assert '<html' not in html.lower(), f'«{attack}» отдал целую страницу'

    def test_base_is_not_a_site_template(self):
        """`_base.html` — основа, а не шаблон сайта: отдать его напрямую значит
        показать разметку с неопределёнными блоками."""
        assert '_base'.startswith(dating_work.DATING_WORK_HIDDEN)

    def test_slug_of_site_name(self):
        """Имя сайта в имя файла: `Ladadate` → `ladadate`."""
        assert _slug('Ladadate') == 'ladadate'
        assert _slug('  GoldenBride ') == 'goldenbride'
        assert _slug('Two Words') == 'two-words'

    def test_slug_refuses_anything_odd(self):
        """Не годится в имя — пустая строка, а не искажённое: подобрать шаблон по
        искажённому имени всё равно не выйдет."""
        for name in ('../etc/passwd', 'сайт', 'a/b', 'a.b', '', None, 'x' * 65):
            assert _slug(name) == '', f'«{name}» прошло как имя файла'


class TestWorkRoute:
    """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""

    async def test_site_is_required(self, auth_client):
        assert (await auth_client.get('/admin/dating/profile/work')).status_code == 422

    async def test_closed_without_login(self, client):
        answer = await client.get('/admin/dating/profile/work')

        assert answer.status_code in (401, 307), 'разметка отдалась без входа'


def _request() -> Request:
    """Запрос с той же Jinja, что у панели.

    Окружение собирается тем же вызовом и над тем же каталогом, что в
    `main_uvicorn.py`: ручка спрашивает список шаблонов у самой Jinja
    (`loader.list_templates`), и собери мы его иначе — проверяли бы не то, что
    работает в панели.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates = Jinja2Templates(directory=os.path.join(root, 'data/uvicorn/template'))

    class _App:
        class state:
            jinja2_template = templates

    return Request({'type': 'http', 'method': 'GET', 'path': '/admin/dating/profile/work',
                    'headers': [], 'query_string': b'', 'app': _App})
