"""Шаблон работы с анкетой: разметка `#work` под конкретный сайт.

Проверяется то, ради чего шаблоны и разделены: **сайты получают разную разметку**, а
имя шаблона, приходящее из базы, не пускает никуда за пределы каталога.

Сюита ходит по HTTP: ручка отдаёт HTML, и разбирать его подстрокой — ровно то, что
делает браузер. Разметку сверяем по контрактным признакам (`data-slot`, классы
блоков), а не по вёрстке целиком: иначе тест падал бы на каждой правке отступов.
"""

import pytest

from src.uvicorn.routers.dating.dating_work import _slug

# Сайты живут у соседа; номера берём из его списка, а не пишем числами — они разные
# на разных установках.
LADA = 'Ladadate'
GOLD = 'Goldenbride'


@pytest.fixture
async def sites(auth_client):
    """Номера сайтов по имени. Нет сайтов — проверять нечего."""
    rows = (await auth_client.get('/api/dating/site')).json()['result']['sites']
    if not rows:
        pytest.skip('у соседа нет сайтов с меткой dating')

    return {row['name']: row['id'] for row in rows}


class TestWorkTemplate:
    """Что отдаёт ручка."""

    async def test_template_has_slots_and_no_log(self, auth_client, sites):
        """⚠ Журнала в шаблоне сайта быть не должно.

        Он про анкету, а не про сайт: одна и та же лента нужна на любом, и держи мы
        её копию в каждом шаблоне — правка журнала означала бы правку всех. Место
        журнала — каркас страницы.
        """
        html = await _html(auth_client, sites[LADA])

        assert 'data-slot="dialogs"' in html, 'нет слота под диалоги'
        assert 'data-slot="setup"' in html, 'нет слота под настройки'
        assert 'chat-log' not in html, 'журнал попал в шаблон сайта'
        assert 'id="log"' not in html

    async def test_every_tab_has_a_panel(self, auth_client, sites):
        """Панели всех четырёх вкладок приходят разом.

        Переключение вкладки тогда — показать одну и снять остальные, без запроса к
        серверу: шаблон один на сайт, и просить его заново на каждый щелчок незачем.
        """
        html = await _html(auth_client, sites[LADA])

        for tab in ('chat', 'setup', 'mail', 'event'):
            assert f'data-tab="{tab}"' in html, f'нет панели вкладки «{tab}»'

    async def test_sites_get_different_markup(self, auth_client, sites):
        """⚠ Ради этого шаблоны и разделены.

        У Goldenbride не заведено сценариев ни под ленту «Все онлайн», ни под
        избранных: оба блока стояли бы навсегда с надписью «не снялось». Шаблон
        сайта убирает их, не трогая ни JS, ни другие сайты.
        """
        if GOLD not in sites:
            pytest.skip('нет сайта Goldenbride')

        lada = await _html(auth_client, sites[LADA])
        gold = await _html(auth_client, sites[GOLD])

        assert 'online-row' in lada and 'people-col' in lada
        assert 'online-row' not in gold and 'people-col' not in gold
        # Диалоги есть у обоих: они из нашей базы и работают на любом сайте.
        assert 'data-slot="dialogs"' in gold

    async def test_site_without_own_template_gets_default(self, auth_client):
        """Новый сайт должен открыться и работать до того, как под него напишут
        свой шаблон: иначе его нельзя было бы даже посмотреть."""
        html = await _html(auth_client, 999_777)

        assert 'data-slot="dialogs"' in html

    async def test_site_is_required(self, auth_client):
        assert (await auth_client.get('/admin/dating/profile/work')).status_code == 422

    async def test_open_to_operator(self, dating_client, sites):
        """Это разметка страницы, которая и так открыта оператору; данных в ней нет."""
        answer = await dating_client.get('/admin/dating/profile/work',
                                         params={'site_id': sites[LADA]})

        assert answer.status_code == 200

    async def test_closed_without_login(self, client, sites):
        answer = await client.get('/admin/dating/profile/work',
                                  params={'site_id': sites[LADA]})

        assert answer.status_code in (401, 307), 'разметка отдалась без входа'


class TestTemplateBySetting:
    """Шаблон можно назначить сайту руками."""

    async def test_setting_wins_over_name(self, auth_client, sites):
        """Настройка `template` сильнее имени сайта: два сайта могут делить один
        шаблон, а имя не всегда годится в имя файла."""
        site = sites[LADA]
        before = (await auth_client.get(f'/api/dating/site/{site}')).json()['result']['settings']

        try:
            await auth_client.put(f'/api/dating/site/{site}',
                                  json={'settings': {**before, 'template': 'goldenbride'}})
            html = await _html(auth_client, site)

            assert 'online-row' not in html, 'назначенный шаблон не подхватился'
        finally:
            await auth_client.put(f'/api/dating/site/{site}', json={'settings': before})

    async def test_missing_template_falls_back(self, auth_client, sites):
        """Назначенный шаблон могли удалить. Молча берём умолчание, а не отдаём
        500: страница откроется и будет работать, а расхождение видно в логе."""
        site = sites[LADA]
        before = (await auth_client.get(f'/api/dating/site/{site}')).json()['result']['settings']

        try:
            await auth_client.put(f'/api/dating/site/{site}',
                                  json={'settings': {**before, 'template': 'такого-нет'}})
            html = await _html(auth_client, site)

            assert 'data-slot="dialogs"' in html
        finally:
            await auth_client.put(f'/api/dating/site/{site}', json={'settings': before})


class TestTemplateNameIsSafe:
    """⚠ Имя шаблона приходит из базы — значит, ему нельзя доверять."""

    async def test_traversal_does_not_read_other_files(self, auth_client, sites):
        """Возьми ручка значение как путь — получили бы чтение чужих шаблонов, а
        дальше и любого файла, до которого дотянется Jinja.

        Проверяем итог, а не механику: что бы ни лежало в настройке, наружу уходит
        шаблон работы с анкетой.
        """
        site = sites[LADA]
        before = (await auth_client.get(f'/api/dating/site/{site}')).json()['result']['settings']

        try:
            for attack in ('../dating_profile', '../../login', '/etc/passwd',
                           '..%2f..%2fbase', '_base'):
                await auth_client.put(f'/api/dating/site/{site}',
                                      json={'settings': {**before, 'template': attack}})
                html = await _html(auth_client, site)

                assert 'data-slot="dialogs"' in html, f'«{attack}» подменил шаблон'
                assert '<html' not in html.lower(), f'«{attack}» отдал целую страницу'
        finally:
            await auth_client.put(f'/api/dating/site/{site}', json={'settings': before})

    def test_base_is_not_a_site_template(self):
        """`_base.html` — основа, а не шаблон сайта: отдать его напрямую значит
        показать разметку с неопределёнными блоками."""
        from src.uvicorn.routers.dating.dating_work import DATING_WORK_HIDDEN

        assert '_base'.startswith(DATING_WORK_HIDDEN)

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


async def _html(auth_client, site_id: int) -> str:
    answer = await auth_client.get('/admin/dating/profile/work',
                                   params={'site_id': site_id})

    assert answer.status_code == 200, answer.text

    return answer.text
