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
