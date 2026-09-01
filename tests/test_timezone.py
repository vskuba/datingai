"""Часовой пояс панели: настройка `timezone` и её путь до страницы.

Времена в базе и в контейнере лежат **в UTC** (`TZ` в compose не задан), а человек
сверяет метку с часами на стене. Сдвигает их страница, а смещение приезжает ей
готовым числом в разметке — эту цепочку и проверяем: настройка → контекст шаблона →
`window.DATING_TZ`.

Сам сдвиг делает `dating_time.js` в браузере; проверен он в живом Chrome (запись
журнала `07:56:03` из ручки показалась как `10:56:03`), а не здесь — здесь нет
браузера, который исполнил бы модуль.

⚠ **Пояс — настройка одна на установку.** Тест её меняет, поэтому прежнее значение
запоминается и возвращается в `finally`: иначе прогон оставил бы панель в чужом
поясе, и следующий человек увидел бы неверное время.
"""

import pytest

from datetime_.datetime_ import datetime_offset_hours

KEY = 'timezone'


@pytest.fixture
async def timezone_kept(auth_client):
    """Возвращает пояс, каким он был до теста."""
    before = None
    for row in (await auth_client.get('/api/setting')).json()['result']['settings']:
        if row['key'] == KEY:
            before = row['value']

    yield

    if before is None:
        await auth_client.delete(f'/api/setting/{KEY}')
    else:
        await auth_client.post('/api/setting', json={'key': KEY, 'value': before})


class TestTimezoneReachesThePage:
    """Настройка доезжает до разметки — числом часов, а не строкой."""

    async def test_offset_is_in_the_markup(self, auth_client, timezone_kept):
        """⚠ В разметке, а не запросом.

        Спроси страница смещение отдельным запросом — первые кадры успели бы
        нарисоваться со старым, и человек видел бы, как время прыгает у него на
        глазах. Второй довод: ручка настроек закрыта админом, а правильное время
        нужно и оператору.
        """
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '+3'})

        page = (await auth_client.get('/admin/dating/profile')).text

        assert 'window.DATING_TZ = 3;' in page

    async def test_negative_offset(self, auth_client, timezone_kept):
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '-5'})

        assert 'window.DATING_TZ = -5;' in (await auth_client.get('/admin/dating/profile')).text

    async def test_utc_prefix_is_understood(self, auth_client, timezone_kept):
        """`UTC+2` — то же, что `+2`: человек пишет пояс как привык, и панель не
        должна требовать одной формы записи."""
        await auth_client.post('/api/setting', json={'key': KEY, 'value': 'UTC+2'})

        assert 'window.DATING_TZ = 2;' in (await auth_client.get('/admin/dating/profile')).text

    async def test_garbage_means_utc(self, auth_client, timezone_kept):
        """Мусор в настройке — ноль, то есть «показываем как есть».

        Не отказ и не пустая страница: неверно набранный пояс должен стоить
        неверного времени, а не потери доступа к панели.
        """
        await auth_client.post('/api/setting', json={'key': KEY, 'value': 'Europe/Kyiv'})

        assert 'window.DATING_TZ = 0;' in (await auth_client.get('/admin/dating/profile')).text

    async def test_no_setting_means_utc(self, auth_client, timezone_kept):
        """Пояс не задан — показываем UTC, как контейнер и живёт.

        Умолчание не «+3» и не пояс машины: сервер стоит в UTC, времена в базе
        лежат в UTC, и молча сдвигать показанное значит врать про то, чего человек
        не просил.
        """
        await auth_client.delete(f'/api/setting/{KEY}')

        assert 'window.DATING_TZ = 0;' in (await auth_client.get('/admin/dating/profile')).text

    async def test_every_page_gets_the_offset(self, auth_client, timezone_kept):
        """Пояс на **всех** страницах, а не только там, где о нём вспомнили.

        Контекст собирается одной функцией (`_dashboard_page`) именно затем:
        забытая страница показывала бы время на три часа назад, и понять это можно
        было бы только сверив её с соседней.
        """
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '+3'})

        for path in ('/', '/admin/dating', '/admin/dating/profile', '/admin/dating/log',
                     '/admin/dating/error', '/admin/setting', '/admin/seed',
                     '/admin/users', '/admin/backup'):
            page = (await auth_client.get(path)).text

            assert 'window.DATING_TZ = 3;' in page, f'{path} без часового пояса'

    async def test_operator_pages_get_the_offset_too(self, dating_client, auth_client,
                                                     timezone_kept):
        """Оператору пояс тоже нужен, хотя настройки ему закрыты.

        Ровно поэтому смещение и уезжает в разметку: спроси страница ручку
        `/api/setting`, оператор получил бы 403 и остался с временем UTC.
        """
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '+3'})

        assert 'window.DATING_TZ = 3;' in (await dating_client.get('/admin/dating')).text


class TestTimezoneStatic:
    """Модуль сдвига отдаётся страницам."""

    async def test_time_module_is_served(self, client):
        response = await client.get('/static/js/dating_time.js')

        assert response.status_code == 200
        assert 'timeShift' in response.text

    async def test_view_module_uses_the_shift(self, client):
        """Общие помощники (`seenText`, `logStamp`) сдвигают время, а не резают
        сырую строку: иначе журнал показывал бы UTC, а колонки рядом — местное."""
        response = await client.get('/static/js/dating_view.js')

        assert "import {timeShift} from './dating_time.js';" in response.text


class TestOffsetHours:
    """Разбор смещения — общей библиотекой (`py_modules/datetime_`)."""

    def test_forms_of_the_same_offset(self):
        assert datetime_offset_hours('+3') == 3
        assert datetime_offset_hours('3') == 3
        assert datetime_offset_hours('UTC+3') == 3
        assert datetime_offset_hours(3) == 3

    def test_negative(self):
        assert datetime_offset_hours('-2') == -2
        assert datetime_offset_hours('UTC-2') == -2

    def test_garbage_is_zero(self):
        """Мусор и пустота — ноль: неверная настройка не должна ронять страницу."""
        assert datetime_offset_hours('') == 0
        assert datetime_offset_hours(None) == 0
        assert datetime_offset_hours('Europe/Kyiv') == 0
