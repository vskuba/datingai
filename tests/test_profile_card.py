"""Анкета с сайта в кэш: что разбирается и в чью строку ложится.

Без сети и базы: и разбор ответа, и выбор строки для записи — правила, а цена
ошибки в них молчаливая. Именно так одна из них и прожила незамеченной: кэш
уходил не туда, функция отвечала «сохранено», страница показывала логин вместо
имени, и выглядело это как «сайт не отдал анкету».

⚠ **`profile` и `card` — разные вещи.** `profile` — учётка в нашей базе
(`profile_`), `card` — анкета, прочитанная с сайта. У них разные
идентификаторы: у первой номер нашей строки, у второй — номер на сайте. Так они
зовутся и в коде страниц (`.profile*` против `.card*`), и в `agent_ensure`.
"""

import json

import pytest

from src.dating.profile_cache import _contact_values, _profile_load
from src.dating.site.site_registry import site_registry_profile

pytestmark = pytest.mark.profile
# Настройка сайта из RhapsodyAi: интересен корень и где искать анкету.
PRESENCE = {'base': 'https://goldenbride.net/', 'profile': {'path': '/api/me', 'field': 'user'}}

# Учётка в нашей базе. Номер строки — 266; на сайте у той же анкеты номер другой.
PROFILE = {'id': 266, 'login': 'natalia@example.com'}

# Драйвер анкеты. Через реестр, а не прямым импортом класса: заодно проверяется,
# что умолчание ведёт к Goldenbride, пока в закладках не проставлен `driver`.
SITE = site_registry_profile(PRESENCE)

# Порядок полей строки `dating_contact` — как в INSERT.
UID, NAME, PHOTO, AGE, CITY, ONLINE = 1, 2, 3, 4, 5, 6


class TestProfileCard:
    """Анкета учётки: разбор ответа сайта и запись в свою строку."""

    async def test_card_is_written_to_our_row_not_site_id(self, db):
        """Кэш ложится в строку **нашей** учётки, а не в номер с сайта.

        Здесь и была поломка: словарь анкеты присваивался в ту же переменную
        `profile`, и `WHERE id` получал `40317` — номер на сайте. Строки с таким
        номером у нас нет, обновление не задевало ничего, а функция отвечала
        «сохранено». В кэше не было ни одной живой анкеты — только посеянные.
        """
        await _profile_load(_client({'user': {'id': 40317, 'firstName': 'Nadezhda'}}),
                            PROFILE, PRESENCE)

        assert db.where_id == [266], 'кэш ушёл не в строку нашей учётки'

    async def test_site_id_is_kept_inside_the_card(self, db):
        """Номер с сайта не теряется — он живёт внутри анкеты, а не в `WHERE`."""
        await _profile_load(_client({'user': {'id': 40317, 'firstName': 'Nadezhda'}}),
                            PROFILE, PRESENCE)

        assert db.cards[0]['id'] == 40317

    async def test_card_without_site_id_falls_back_to_login(self, db):
        """Сайт не дал номера — в анкете остаётся логин, и запись всё равно идёт.

        Прежде логин попадал в `int()` и ронял обновление кэша целиком: вместе с
        анкетой пропадали и собеседницы, потому что исключение уходило наверх.
        """
        await _profile_load(_client({'user': {'firstName': 'Nadezhda'}}), PROFILE, PRESENCE)

        assert db.where_id == [266]
        assert db.cards[0]['id'] == 'natalia@example.com'

    @pytest.mark.parametrize('user,name', [
        ({'firstName': 'Nadezhda', 'name': 'другое'}, 'Nadezhda'),
        ({'name': 'Nadezhda'}, 'Nadezhda'),
        ({}, ''),
    ])
    async def test_name_is_taken_by_known_field_names(self, db, user, name):
        """Имя ищется по нескольким известным полям: сайты зовут их по-разному."""
        await _profile_load(_client({'user': {'id': 1, **user}}), PROFILE, PRESENCE)

        assert db.cards[0]['name'] == name

    @pytest.mark.parametrize('user,city,about', [
        ({'city': 'Киев', 'about': 'текст'}, 'Киев', 'текст'),
        ({'from': 'Киев', 'description': 'текст'}, 'Киев', 'текст'),
        ({}, '', ''),
    ])
    async def test_city_and_about_have_two_names_each(self, db, user, city, about):
        await _profile_load(_client({'user': {'id': 1, **user}}), PROFILE, PRESENCE)

        assert (db.cards[0]['city'], db.cards[0]['about']) == (city, about)

    async def test_refusal_is_not_a_card(self, db):
        """Отказ сайта не должен лечь в кэш как анкета.

        Поле названо настройкой (`field`), и его отсутствие означает «сайт анкету
        не дал». Иначе `{"error": …, "success": false}` прошёл бы как анкета и
        затёр ту, что прочитана со страницы кабинета.
        """
        saved = await _profile_load(_client({'error': 'no access', 'success': False}),
                                    PROFILE, PRESENCE)

        assert saved is False
        assert db.where_id == [], 'в базу ушёл отказ сайта'

    async def test_no_step_no_request(self, db):
        """Сайт без настройки анкеты — не ошибка, просто нечего читать."""
        assert await _profile_load(_client({}), PROFILE, {'base': 'https://x.com'}) is False


class TestContactRow:
    """Строка собеседницы: разбор у драйвера, раскладка по колонкам — у кэша."""

    def test_uid_has_two_known_names(self):
        assert _contact_values({'id': '42'}, 266, SITE)[UID] == '42'
        assert _contact_values({'ladyId': '42'}, 266, SITE)[UID] == '42'

    def test_row_without_uid_is_skipped(self):
        """Ключ таблицы — пара `profile_id, uid`, и пустой `uid` склеил бы всех
        безымянных в одну строку."""
        assert _contact_values({'firstName': 'Semen'}, 266, SITE) is None

    def test_fields_are_cut_to_column_width(self):
        row = _contact_values({'id': 'u' * 100, 'firstName': 'n' * 500,
                               'url': 'https://cdn/' + 'p' * 600, 'from': 'c' * 300},
                              266, SITE)

        assert (len(row[UID]), len(row[NAME]), len(row[PHOTO]), len(row[CITY])) == (32, 190, 500, 190)

    @pytest.mark.parametrize('value,online', [(True, 1), (1, 1), (False, 0), (0, 0), (None, 0)])
    def test_online_becomes_flag(self, value, online):
        assert _contact_values({'id': '42', 'isOnline': value}, 266, SITE)[ONLINE] == online

    def test_age_missing_is_zero(self):
        assert _contact_values({'id': '42'}, 266, SITE)[AGE] == 0

    def test_missing_photo_stays_empty_in_the_row(self):
        """Без фотографии в колонке пусто, а не голый хост.

        Иначе `<img src="https://goldenbride.net/">` попросил бы у сайта главную
        страницу вместо картинки — и так у всех собеседниц разом.
        """
        assert _contact_values({'id': '42'}, 266, SITE)[PHOTO] == ''

    def test_relative_photo_gets_host_in_the_row(self):
        """Тот самый случай: сайт отдаёт `/images/...` для собеседницы без фото,
        и без хоста браузер просил эту картинку у панели."""
        row = _contact_values({'id': '42', 'url': '/images/designNew/Default-image-07-Man.png'},
                              266, SITE)

        assert row[PHOTO] == 'https://goldenbride.net/images/designNew/Default-image-07-Man.png'

    def test_columns_are_in_insert_order(self):
        """Порядок колонок — смысл `_contact_values`.

        Перепутанные местами имя и город ничего не уронят: они просто окажутся не
        в тех колонках у всех собеседниц разом, и заметить это можно только
        глазами на странице.
        """
        row = _contact_values({'id': '42', 'firstName': 'Lena', 'url': 'https://cdn/a.jpg',
                               'age': 28, 'from': 'Киев', 'isOnline': True}, 266, SITE)

        assert row == (266, '42', 'Lena', 'https://cdn/a.jpg', 28, 'Киев', 1)


class TestPhoto:
    """Адрес фотографии: сайт отдаёт то полный, то путь от своего корня."""

    def test_relative_path_gets_host(self):
        """Без хоста браузер попросил бы картинку у панели, где её нет."""
        assert SITE.photo_url({'url': '/images/default.png'}) == \
            'https://goldenbride.net/images/default.png'

    def test_full_url_is_untouched(self):
        photo = 'https://ddev6uq7v65vs.cloudfront.net/lady/42/thumb.jpg'

        assert SITE.photo_url({'url': photo}) == photo

    def test_missing_photo_stays_empty(self):
        """Иначе `<img src="https://goldenbride.net/">` попросил бы у сайта
        главную страницу вместо картинки."""
        assert SITE.photo_url({}) == ''

    def test_photo_has_two_known_names(self):
        assert SITE.photo_url({'photo': '/a.png'}) == 'https://goldenbride.net/a.png'


# ── Обвязка ──────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class _FakeClient:
    """Сайт, отвечающий заготовленным телом."""

    def __init__(self, data):
        self.data = data

    async def get(self, url):
        return _FakeResponse(self.data)

    async def post(self, url, content=None):
        return _FakeResponse(self.data)


def _client(data):
    return _FakeClient(data)


class _FakeDb:
    """База, которая только запоминает, что и куда писали."""

    def __init__(self):
        self.where_id: list = []
        self.cards: list = []

    async def execute(self, sql, args=None):
        self.cards.append(json.loads(args[0]))
        self.where_id.append(args[1])

    @property
    def connection(self):
        return self

    async def commit(self):
        pass


@pytest.fixture
def db(monkeypatch):
    """Подменяет базу. Сам разбор и выбор строки остаются настоящими — они и
    проверяются."""
    from src.dating import profile_cache

    box = _FakeDb()

    class Ctx:
        async def __aenter__(self):
            return box

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(profile_cache, 'mysql_get_db_async', lambda: Ctx())

    return box
