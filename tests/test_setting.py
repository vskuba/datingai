"""Настройки панели: ключ → значение и кому это доступно.

Сюита ходит по HTTP, как остальные (`testing.md`): проверяется поведение ручек
целиком — с правами, кодами ответа и записью в базу.

⚠ **Ключи здесь свои, с приставкой `тест_`.** Настройки общие на всю установку, и
тест, взявший осмысленное имя, однажды затрёт живое значение — своё же, но
настоящее. Всё заведённое убирается в `finally`: строка настройки переживает
прогон, а мусор в списке видит потом человек.
"""

import pytest

pytestmark = pytest.mark.core
KEY = 'тест_ключ'
OTHER = 'тест_второй'


@pytest.fixture
async def clean(auth_client):
    """Убирает тестовые ключи до и после прогона."""
    async def _drop():
        for key in (KEY, OTHER):
            await auth_client.delete(f'/api/setting/{key}')

    await _drop()

    yield

    await _drop()


class TestSettingCrud:
    """Завести, переписать, убрать."""

    async def test_new_setting_appears_in_the_list(self, auth_client, clean):
        response = await auth_client.post('/api/setting', json={'key': KEY, 'value': '30'})

        assert response.status_code == 200
        assert await _value(auth_client, KEY) == '30'

    async def test_second_save_rewrites_value(self, auth_client, clean):
        """Тот же ключ — правка, а не вторая строка: ключ уникален, и «завести» с
        «переписать» здесь одно действие."""
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '30'})
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '45'})

        rows = await _rows(auth_client)

        assert len([row for row in rows if row['key'] == KEY]) == 1
        assert await _value(auth_client, KEY) == '45'

    async def test_empty_value_removes_setting(self, auth_client, clean):
        """⚠ Пустое значение удаляет строку, а не записывает пустоту.

        Настройки нет и настройка со значением «ничего» — разные вещи: первое
        означает «действует умолчание кода», второе — «действует пустая строка».
        Спутать их значит получить необъяснимое поведение там, где умолчание было
        осмысленным.
        """
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '30'})
        await auth_client.post('/api/setting', json={'key': KEY, 'value': ''})

        assert await _value(auth_client, KEY) is None

    async def test_delete_removes_setting(self, auth_client, clean):
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '30'})
        response = await auth_client.delete(f'/api/setting/{KEY}')

        assert response.status_code == 200
        assert await _value(auth_client, KEY) is None

    async def test_delete_twice_is_not_an_error(self, auth_client, clean):
        """Повторное удаление — не ошибка: результат тот же, настройки нет.

        Отвечать 404 на удаление уже удалённого значило бы пугать человека тем,
        чего он и добивался.
        """
        await auth_client.post('/api/setting', json={'key': KEY, 'value': '30'})
        await auth_client.delete(f'/api/setting/{KEY}')

        assert (await auth_client.delete(f'/api/setting/{KEY}')).status_code == 200

    async def test_settings_are_sorted_by_key(self, auth_client, clean):
        """Порядок — по ключу: список читают глазами, и порядок вставки в нём не
        значит ничего."""
        await auth_client.post('/api/setting', json={'key': OTHER, 'value': 'б'})
        await auth_client.post('/api/setting', json={'key': KEY, 'value': 'а'})

        keys = [row['key'] for row in await _rows(auth_client)]

        assert keys == sorted(keys)

    async def test_value_survives_special_characters(self, auth_client, clean):
        """В значении бывает JSON и кириллица: тип колонки один на всё, разбор —
        на стороне того, кто настройку читает."""
        value = '{"часы": [1, 2], "текст": "а=б&в"}'
        await auth_client.post('/api/setting', json={'key': KEY, 'value': value})

        assert await _value(auth_client, KEY) == value


class TestSettingRefuses:
    """Что ручка не принимает."""

    async def test_empty_key_is_422(self, auth_client):
        """Ключ — имя настройки; без него строку не найти и не переписать."""
        response = await auth_client.post('/api/setting', json={'key': '', 'value': '1'})

        assert response.status_code == 422

    async def test_blank_key_is_422(self, auth_client):
        """Пробелы ключом не считаются: `key` обрезается, и остаётся пустота."""
        response = await auth_client.post('/api/setting', json={'key': '   ', 'value': '1'})

        assert response.status_code == 422

    async def test_too_long_key_is_422(self, auth_client):
        """Длиннее колонки (`CHAR(50)`) — отказ с внятным текстом, а не молчаливая
        обрезка на стороне MySQL: обрезанный ключ не совпал бы с тем, что читает код."""
        response = await auth_client.post('/api/setting',
                                          json={'key': 'к' * 51, 'value': '1'})

        assert response.status_code == 422


class TestSettingRights:
    """⚠ Только админу: настройка меняет поведение панели для всех."""

    async def test_operator_cannot_read(self, dating_client):
        assert (await dating_client.get('/api/setting')).status_code == 403

    async def test_operator_cannot_write(self, dating_client):
        response = await dating_client.post('/api/setting', json={'key': KEY, 'value': '1'})

        assert response.status_code == 403

    async def test_operator_cannot_delete(self, dating_client):
        assert (await dating_client.delete(f'/api/setting/{KEY}')).status_code == 403

    async def test_page_is_closed_to_operator(self, dating_client):
        """Страница закрыта той же зависимостью, что и ручки: видимость в меню —
        удобство, а не защита."""
        assert (await dating_client.get('/admin/setting')).status_code in (403, 404)

    async def test_stranger_gets_401(self, client):
        assert (await client.get('/api/setting')).status_code == 401


async def _rows(auth_client) -> list:
    return (await auth_client.get('/api/setting')).json()['result']['settings']


async def _value(auth_client, key: str):
    """Значение настройки или None, если её нет."""
    for row in await _rows(auth_client):
        if row['key'] == key:
            return row['value']

    return None
