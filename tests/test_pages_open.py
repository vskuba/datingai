"""⚠ Страницы раздела обязаны открываться: переезд в подпапку тесты не ловят.

Сюита проверяет ручки API, а страницы рендерит Jinja — и сломанный путь к
шаблону виден только при настоящем запросе. Ровно так и было: шесть страниц
переехали в `admin/dating/`, и ошибка в любой из ссылок дала бы 500 на живой
панели, а зелёная сюита ничего бы не сказала.
"""

import pytest

pytestmark = pytest.mark.page

PAGES = ['/admin/dating', '/admin/dating/profile', '/admin/dating/log',
         '/admin/dating/error', '/admin/dating/site', '/admin/dating/socket']


@pytest.mark.parametrize('name', ['schedule.js', 'site.js', 'time.js', 'view.js'])
def test_the_script_is_where_the_page_asks(name):
    """⚠⚠ И скрипты: внутри них относительные импорты.

    Переехав в `js/dating/`, они потеряли соседей: `./api.js` указывал на файл
    уровнем выше, и путь стал `../api.js`. Браузер такую ошибку в журнал сервера
    не пишет — страница просто перестаёт работать, и молча.
    """
    import pathlib
    import re

    room = pathlib.Path('data/uvicorn/public/js/dating')
    text = (room / name).read_text(encoding='utf-8')

    for ref in re.findall(r"from '([^']+)'", text):
        assert (room / ref).resolve().exists(), f'{name} → {ref} не существует'


@pytest.mark.parametrize('url', PAGES)
async def test_the_page_renders(auth_client, url):
    answer = await auth_client.get(url)

    assert answer.status_code == 200, f'{url} → {answer.status_code}'
    assert len(answer.text) > 2000, f'{url} отдал {len(answer.text)} знаков'


class TestFeedRefreshesTheList:
    """⚠⚠ Событие ленты само освежает список, о котором говорит.

    **Живая беда на проде:** приходило уведомление «инвайты готовы», а пул под
    ним стоял прежний — до перезагрузки страницы. Лента при этом опрашивалась
    исправно: за пять минут двенадцать заходов против **двух** у пула.

    Таймер у пула есть (полминуты), и логика его верна — но ждать его незачем:
    событие ленты **уже** означает, что данные в базе новые. Оно и есть точный
    признак, точнее любого срока.
    """

    def test_the_feed_triggers_a_reload(self):
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        assert 'feedRefresh(rows);' in page,             'лента снова не освежает списки — уведомление придёт, а пул не изменится'
        assert 'FEED_FRESH' in page

    def test_the_invite_pool_is_among_them(self):
        """Пул инвайтов — тот самый список, на котором это поймали."""
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        head = page[page.index('const FEED_FRESH'):]
        head = head[:head.index('};')]

        assert 'invite:' in head and 'inviteLoad()' in head

    def test_closed_tabs_are_only_invalidated(self):
        """⚠ Закрытую вкладку не грузим, а помечаем к перезагрузке.

        Грузить под человека ещё четыре списка значило бы четыре запроса на
        каждое событие, из которых три никто не увидит. Но и оставить как есть
        нельзя: пулы читаются лишь при смене сайта, и человек, перейдя на
        вкладку позже, увидел бы прежний список.
        """
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        head = page[page.index('const FEED_FRESH'):]
        head = head[:head.index('};')]

        assert 'invitePoolFor = 0;' in head,             'переход на закрытую вкладку покажет прежний список'
        assert 'favMailPoolFor = 0;' in head

    def test_the_first_paint_loads_nothing(self):
        """⚠ Первая отрисовка не перечитывает: вкладка и так грузит свой список
        при открытии, и заход сюда дал бы второй запрос с тем же ответом."""
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        body = page[page.index('function feedRefresh'):]
        body = body[:body.index('\n    }')]

        assert 'if (feedFirst)' in body

    def test_an_event_fires_once(self):
        """Одно событие — одна перезагрузка: лента отдаёт те же строки каждые
        двадцать секунд, и без памяти список грузился бы на каждый заход."""
        import pathlib

        page = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        assert 'feedFreshSeen' in page
