"""Настройка «Активные ленты» — новый вид настройки, список галочек.

Разметку лент сторожит `test_work_template.py`; здесь — сама настройка: что
разрешает реестр, как значение проходит дверь и как читается обратно. Дверь и
читалка **не могут расходиться**: сохранённое должно читаться тем, что было
сохранено, а не тем, что удобнее прочесть.

Хранение — по HTTP, в живую базу, как в `test_dating_site.py`: подменять базу
значило бы проверять подделку. Номер сайта выдуманный, ключ — из реестра,
значение возвращается в `finally` (ручка переписывает набор целиком).
"""

import pytest

from src.dating.site_setting import (SITE_BOXES_NONE, SITE_KNOWN, site_boxes,
                                     site_boxes_split, site_boxes_values)

pytestmark = pytest.mark.core

KEY = 'mailing_feeds'

# Номера выдуманные — живые значения задеть невозможно (тот же приём, что в
# `test_dating_site.py`; номер свободный, каким сюита настроек не пользуется).
SITE = 999_786


@pytest.fixture
async def kept(auth_client):
    """Возвращает настройки тестового номера такими, какими они были."""
    before = await _settings(auth_client, SITE)

    yield

    await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})


class TestRegistry:
    """Что реестр говорит о новой настройке."""

    def test_entry_shape(self):
        """Настройка — в группе рассылки, галочками, с двумя вариантами.

        Порядок вариантов значит вид вкладки: реестр — единственный источник
        порядка, и переворот его списка перевернул бы ленты на странице.
        """
        spec = SITE_KNOWN[KEY]

        assert spec['group'] == 'mail', 'настройка уехала из вкладки рассылки'
        assert spec['kind'] == 'boxes', 'вид не галочки'
        assert [value for value, _ in spec['options']] == ['online', 'guests']
        assert spec['default'] == 'online', \
            'по умолчанию должна быть только «Все онлайн»: наполнения «Гостей» ещё нет'


class TestValues:
    """Разбор значения — чистая функция, без базы и HTTP."""

    def test_empty_means_registry_default(self):
        assert site_boxes_values(KEY, '') == ['online'], 'незаданное читается не умолчанием'

    def test_none_means_dropped_all(self):
        """`none` — не «не задано», а «снято всё»: первое молча стало бы
        молча же вторым, и снятая лента вернулась бы против воли."""
        assert site_boxes_values(KEY, SITE_BOXES_NONE) == []

    def test_order_comes_from_the_registry(self):
        assert site_boxes_values(KEY, 'guests,online') == ['online', 'guests'], \
            'порядок уехал за порядком приехавших'

    def test_spaces_and_repeats_survive(self):
        assert site_boxes_values(KEY, ' guests , online ,guests') == ['online', 'guests']

    def test_unknown_token_keeps_the_good_ones(self):
        assert site_boxes_values(KEY, 'online,onlin') == ['online'], \
            'опечатка в одном слове унесла годное'

    def test_only_junk_falls_to_default(self):
        """Мусор — не «снято всё»: seed или правка руками не должны выглядеть
        как решение человека. Для решения есть `none`."""
        assert site_boxes_values(KEY, 'whatever') == ['online']

    def test_split_says_what_the_door_should_refuse(self):
        kept, unknown = site_boxes_split(KEY, 'online,onlin')

        assert kept == ['online'] and unknown == ['onlin']
        assert site_boxes_split(KEY, SITE_BOXES_NONE) == ([], [])


class TestDoor:
    """Что проходит дверь `PUT /api/dating/site/{id}`, а что — нет."""

    async def test_csv_comes_back_as_saved(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: 'online,guests'}})

        assert (await _settings(auth_client, SITE))[KEY] == 'online,guests'

    async def test_spaces_are_typed_away(self, auth_client, kept):
        """Через запятую с пробелом — то же значение: человек печатает запрос
        руками, а не только кликает галочки."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: 'online, guests'}})

        assert (await _settings(auth_client, SITE))[KEY] == 'online,guests'

    async def test_registry_order_is_what_gets_stored(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: 'guests,online'}})

        assert (await _settings(auth_client, SITE))[KEY] == 'online,guests', \
            'дверь сохранила приехавший порядок — вид вкладок зависел бы от запроса'

    async def test_all_dropped_stores_the_word(self, auth_client, kept):
        """⚠ Снято всё хранится словом, а не исчезновением ключа: пустая строка
        дверь выбрасывает, а «нет настройки» читается умолчанием — первая же
        лента вернулась бы на вкладку, снятую минуту назад."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: 'none'}})

        assert (await _settings(auth_client, SITE))[KEY] == SITE_BOXES_NONE

    async def test_unknown_word_is_refused(self, auth_client, kept):
        """Дверь отказывает, а не выбрасывает слово молча: опечатку в url надо
        увидеть, а не получить ленты не там, где оставил."""
        answer = await auth_client.put(f'/api/dating/site/{SITE}',
                                       json={'settings': {KEY: 'onlin'}})

        assert answer.status_code == 422, 'дверь приняла значение вне набора'
        assert 'onlin' in answer.json()['detail']

    async def test_empty_key_is_not_stored(self, auth_client, kept):
        """Пустое значение — «не хранить», как у любой настройки; читается тогда
        умолчанием реестра (`TestValues` выше)."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: 'first', 'тест_сюрприз': ''}})
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: ''}})

        assert KEY not in await _settings(auth_client, SITE)


class TestReadBack:
    """⚠ Что сохранила дверь, то и читается."""

    @pytest.mark.parametrize('stored', ['online', 'online,guests', 'guests',
                                        SITE_BOXES_NONE])
    async def test_saved_value_reads_same_set(self, auth_client, kept, stored):
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: stored}})

        assert await site_boxes(SITE, KEY) == site_boxes_values(KEY, stored), \
            f'значение {stored!r} сохранилось не тем, что читается'

    async def test_never_saved_reads_default(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': {}})

        assert await site_boxes(SITE, KEY) == ['online']


async def _settings(auth_client, site_id: int) -> dict:
    return (await auth_client.get(f'/api/dating/site/{site_id}')).json()['result']['settings']
