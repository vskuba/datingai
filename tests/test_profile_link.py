"""Ссылка на анкету девушки на самом сайте: шаблон сайта плюс её номер.

Сборка адреса проверяется **без базы** (`dating_profile_link_build`), а карточка —
в процессе с настоящей базой: ссылку рисует страница по полю `site_url`, и
проверять надо то самое поле, а не догадку о нём.

⚠ **Номер сайта — стенд** (`999_343`), не живой. Тест заводит анкету и пишет ей
номер на сайте; на живом сайте такой анкеты нет и быть не должно.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_profile_link import (DATING_PROFILE_LINK_SETTING,
                                            dating_profile_link,
                                            dating_profile_link_build)
from src.uvicorn.routers.dating import dating_site
from src.uvicorn.routers.dating.dating_profile import dating_profile_delete
from src.uvicorn.routers.dating.dating_profile_view import dating_profile_view_card
from src.uvicorn.routers.dating.dating_site import dating_site_list

pytestmark = pytest.mark.profile

# Стенд: закладка без метки `dating`, кругам не видна (`conftest.site_snapshot`).
STAND_SITE = 999_343

# Шаблон того вида, что вписывают на «Сайтах». Настоящий у Ladadate такой же.
PATTERN = 'https://stand.invalid/profile/{ID}'


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда с номером на сайте. Убирается за собой."""
    uid = str(uuid.uuid4().int % 1_000_000)
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'link-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    # Номер кладёт импорт с сайта; здесь пишем его сами — ради него всё и затеяно.
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                         ('{"name": "Diana", "site_uid": "%s"}' % uid, profile_id))
        await db.connection.commit()

    yield {'id': profile_id, 'uid': uid}

    await dating_profile_delete(profile_id, user=None)


def _settings(rows: dict):
    """Подменённое чтение настроек сайтов: номер → набор."""
    async def _all():
        return dict(rows)

    return _all


class TestBuild:
    """Подстановка номера в шаблон. Чистая функция — базы здесь нет."""

    def test_number_takes_the_place_of_the_mark(self):
        assert dating_profile_link_build(PATTERN, '643694') \
            == 'https://stand.invalid/profile/643694'

    def test_pattern_without_the_mark_gives_nothing(self):
        """⚠ Адрес без `{ID}` вёл бы **на одну и ту же страницу** у всех анкет.

        Это хуже отсутствующей ссылки: человек щёлкает по номеру Дианы и попадает
        на чужую анкету, а понять, что ссылка врёт, можно только сверив номер в
        адресной строке.
        """
        assert dating_profile_link_build('https://stand.invalid/profile/', '643694') == ''

    @pytest.mark.parametrize('pattern, uid', [
        ('', '643694'),
        (PATTERN, ''),
        ('', ''),
        (None, None),
    ])
    def test_missing_half_gives_nothing(self, pattern, uid):
        """Пусто — обычный исход: у сайта нет шаблона либо у анкеты нет номера."""
        assert dating_profile_link_build(pattern, uid) == ''

    @pytest.mark.parametrize('pattern', [
        'javascript:alert(1)/{ID}',
        'JavaScript:alert(1)/{ID}',
        'data:text/html,{ID}',
        'ftp://stand.invalid/{ID}',
    ])
    def test_only_http_is_allowed(self, pattern):
        """⚠ Значение уезжает в `href`, и `javascript:` там — выполняемый код.

        Настройку правит только админ, но «только админ» говорит о том, кто её
        вписал, а не о том, кто откроет карточку.
        """
        assert dating_profile_link_build(pattern, '643694') == ''

    def test_number_is_encoded(self):
        """Номер приходит из JSON анкеты, куда его положил разбор чужой страницы:
        «там цифры» — это ожидание, а не проверка. Незакодированная кавычка
        разорвала бы `href` и превратила бы остаток строки в разметку."""
        assert dating_profile_link_build(PATTERN, '6"><b>4') \
            == 'https://stand.invalid/profile/6%22%3E%3Cb%3E4'


class TestFromSetting:
    """Шаблон берётся из настройки сайта, а не откуда-нибудь ещё."""

    async def test_setting_makes_the_link(self, monkeypatch):
        monkeypatch.setattr(dating_site, '_settings_all',
                            _settings({STAND_SITE: {DATING_PROFILE_LINK_SETTING: PATTERN}}))

        assert await dating_profile_link(STAND_SITE, '643694') \
            == 'https://stand.invalid/profile/643694'

    async def test_site_without_the_pattern_gives_nothing(self, monkeypatch):
        """Умолчание настройки пустое: сайт, которому шаблон не вписали, ссылки
        не получает — и это не поломка, а «ещё не настроили»."""
        monkeypatch.setattr(dating_site, '_settings_all', _settings({}))

        assert await dating_profile_link(STAND_SITE, '643694') == ''


class TestCard:
    """Карточка анкеты отдаёт готовый адрес полем `site_url`."""

    async def test_card_carries_the_link(self, rhapsody, girl, monkeypatch):
        monkeypatch.setattr(dating_site, '_settings_all',
                            _settings({STAND_SITE: {DATING_PROFILE_LINK_SETTING: PATTERN}}))

        card = (await dating_profile_view_card(girl['id'], user=None))['result']

        assert card['site_url'] == f'https://stand.invalid/profile/{girl["uid"]}'

    async def test_card_without_the_pattern_says_empty(self, rhapsody, girl, monkeypatch):
        """⚠ Поле есть всегда, пустое или нет: страница проверяет значение, а
        отсутствующий ключ она прочитала бы как `undefined` — и это тот же «нет
        ссылки», но выясняется он не сразу и не там."""
        monkeypatch.setattr(dating_site, '_settings_all', _settings({}))

        card = (await dating_profile_view_card(girl['id'], user=None))['result']

        assert card['site_url'] == ''

    async def test_profile_without_a_number_gets_no_link(self, rhapsody, auth_client,
                                                         monkeypatch):
        """Анкета, заведённая руками, номера на сайте не имеет — вести ссылке
        некуда. У Goldenbride сейчас так у всех сорока."""
        monkeypatch.setattr(dating_site, '_settings_all',
                            _settings({STAND_SITE: {DATING_PROFILE_LINK_SETTING: PATTERN}}))

        profile_id = int((await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'link-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result'])
        try:
            card = (await dating_profile_view_card(profile_id, user=None))['result']

            assert card['site_url'] == ''
        finally:
            await dating_profile_delete(profile_id, user=None)


class TestKnownSetting:
    """Настройка стоит в реестре: страница «Сайты» рисует её сама."""

    async def test_pattern_is_a_free_field_of_the_work_group(self, rhapsody, monkeypatch):
        """Свободное поле, а не набор: адрес у каждого сайта свой, и предложить
        его списком нельзя."""
        monkeypatch.setattr(dating_site, '_settings_all', _settings({}))
        known = {row['key']: row
                 for row in (await dating_site_list(user=None))['result']['known']}

        assert DATING_PROFILE_LINK_SETTING in known, 'настройки нет на странице «Сайты»'
        assert known[DATING_PROFILE_LINK_SETTING]['group'] == 'work'
        assert known[DATING_PROFILE_LINK_SETTING]['options'] == []
        assert known[DATING_PROFILE_LINK_SETTING]['default'] == ''
        assert known[DATING_PROFILE_LINK_SETTING]['secret'] is False, \
            'адрес анкеты — не секрет: его видит любой, кто открыл сайт'
