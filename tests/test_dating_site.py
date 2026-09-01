"""Настройки сайта: наши значения по номеру у соседа.

Сюита ходит по HTTP. Сайты берутся настоящие — список ведёт RhapsodyAi, и
подменять его значило бы проверять не ту склейку, ради которой ручка и написана.

⚠ **Ключи здесь свои, с приставкой `тест_`.** Настройки общие на сайт, и тест,
взявший осмысленное имя, однажды затрёт живое значение. Всё заведённое убирается в
`finally`; настройки соседних ключей при этом не трогаются — ручка переписывает
набор целиком, поэтому тест сперва читает его и возвращает как было.
"""

import pytest

KEY = 'тест_ключ'
OTHER = 'тест_второй'


@pytest.fixture
async def site_id(auth_client):
    """Номер живого сайта. Нет ни одного — тесту нечего настраивать."""
    sites = (await auth_client.get('/api/dating/site')).json()['result']['sites']
    if not sites:
        pytest.skip('у соседа нет сайтов с меткой dating')

    return sites[0]['id']


@pytest.fixture
async def kept(auth_client, site_id):
    """Возвращает настройки сайта такими, какими они были до теста.

    Ручка переписывает набор целиком, и тест, оставивший свой, стёр бы живые
    значения — а они меняют работу всех анкет на сайте.
    """
    before = (await auth_client.get(f'/api/dating/site/{site_id}')).json()['result']['settings']

    yield

    await auth_client.put(f'/api/dating/site/{site_id}', json={'settings': before})


class TestSiteSettings:
    """Завести, переписать, убрать."""

    async def test_list_joins_neighbour_and_us(self, auth_client):
        """⚠ Список ведёт сосед, значения — мы.

        Своей таблицы сайтов здесь нет: имя и адрес приходят от него при каждом
        показе. Сайт без сохранённых настроек виден с пустым набором — иначе его
        нельзя было бы настроить в первый раз.
        """
        sites = (await auth_client.get('/api/dating/site')).json()['result']['sites']

        assert sites, 'список сайтов пуст — сосед недоступен?'
        for site in sites:
            assert {'id', 'name', 'url', 'settings'} <= set(site)
            assert isinstance(site['settings'], dict)

    async def test_saved_value_comes_back(self, auth_client, site_id, kept):
        await auth_client.put(f'/api/dating/site/{site_id}',
                              json={'settings': {KEY: '30'}})

        assert await _value(auth_client, site_id, KEY) == '30'

    async def test_save_rewrites_the_whole_set(self, auth_client, site_id, kept):
        """⚠ Набор переписывается целиком, а не по ключу.

        Страница держит его перед глазами и шлёт то, что на ней. Правка по одному
        ключу означала бы, что удаление — отдельная ручка, а порядок двух правок
        подряд начинает значить.
        """
        await auth_client.put(f'/api/dating/site/{site_id}',
                              json={'settings': {KEY: '1', OTHER: '2'}})
        await auth_client.put(f'/api/dating/site/{site_id}', json={'settings': {KEY: '1'}})

        settings = await _settings(auth_client, site_id)

        assert settings == {KEY: '1'}, 'второй ключ пережил перезапись'

    async def test_empty_value_is_not_stored(self, auth_client, site_id, kept):
        """Пустое значение не хранится: «настройки нет» и «настройка пустая» —
        разные вещи, и путать их значит получать необъяснимое поведение там, где
        умолчание кода было осмысленным."""
        await auth_client.put(f'/api/dating/site/{site_id}',
                              json={'settings': {KEY: '30', OTHER: ''}})

        settings = await _settings(auth_client, site_id)

        assert KEY in settings and OTHER not in settings

    async def test_settings_are_kept_apart_by_site(self, auth_client, kept, site_id):
        """Настройки одного сайта не видны у другого: ключ — его номер."""
        sites = (await auth_client.get('/api/dating/site')).json()['result']['sites']
        if len(sites) < 2:
            pytest.skip('нужен второй сайт')

        other = next(s['id'] for s in sites if s['id'] != site_id)
        await auth_client.put(f'/api/dating/site/{site_id}', json={'settings': {KEY: 'мой'}})

        assert KEY not in await _settings(auth_client, other)

    async def test_unknown_site_has_empty_settings(self, auth_client):
        """Сайт, который ещё не настраивали, — законное состояние, а не 404:
        строка появляется при первом сохранении."""
        answer = await auth_client.get('/api/dating/site/999777')

        assert answer.status_code == 200
        assert answer.json()['result']['settings'] == {}


class TestSiteRefuses:
    """Чего ручка не принимает."""

    async def test_empty_key_is_422(self, auth_client, site_id, kept):
        answer = await auth_client.put(f'/api/dating/site/{site_id}',
                                       json={'settings': {'   ': '1'}})

        assert answer.status_code == 422

    async def test_too_long_key_is_422(self, auth_client, site_id, kept):
        """Длиннее предела — отказ с внятным текстом. Предел тот же, что у настроек
        панели: человек читает их одними глазами."""
        answer = await auth_client.put(f'/api/dating/site/{site_id}',
                                       json={'settings': {'к' * 51: '1'}})

        assert answer.status_code == 422


class TestSiteRights:
    """⚠ Правка — только админу, чтение — всем."""

    async def test_operator_can_read(self, dating_client):
        """Оператору полезно видеть, с чем он работает: прятать это значило бы
        звать админа на каждый вопрос."""
        assert (await dating_client.get('/api/dating/site')).status_code == 200

    async def test_operator_cannot_write(self, dating_client, site_id):
        """Настройка сайта меняет работу всех анкет на нём, а не того, кто её
        открыл."""
        answer = await dating_client.put(f'/api/dating/site/{site_id}',
                                         json={'settings': {KEY: '1'}})

        assert answer.status_code == 403

    async def test_page_is_open_to_operator(self, dating_client):
        """Страница открыта, как остальные в разделе; форму правки она прячет
        сама — форма, которая всегда отказывает, читается как поломка."""
        assert (await dating_client.get('/admin/dating/site')).status_code == 200

    async def test_closed_without_login(self, client):
        assert (await client.get('/api/dating/site')).status_code == 401


async def _settings(auth_client, site_id: int) -> dict:
    return (await auth_client.get(f'/api/dating/site/{site_id}')).json()['result']['settings']


async def _value(auth_client, site_id: int, key: str):
    return (await _settings(auth_client, site_id)).get(key)
