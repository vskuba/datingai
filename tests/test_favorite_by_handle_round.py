"""Круг избранных работает и у сайта, где API нет вовсе.

## ⚠⚠ Зачем

Занятие «ответил — в избранные» держится на двух дверях, и обе уже разведены по
драйверу сайта: список берёт `favorite_uids`, звезду жмёт `favorite_add`. У
Step2Love обе ведут не в API, а в его собственную ручку и в кадр авторизации
сокета (`site/site_favorite.py`).

А круг до них не доходил: `_work` первым делом спрашивал `api_base` и без него
уходил ни с чем. То есть на сайте, у которого API нет и не будет, занятие не
работало **никак** — при полностью готовых дверях. Проверено на проде 08.11.2026:
у восьми анкет Step2Love в избранных ноль человек, при том что отвечавшие были.

⚠ **Наружу сюита не ходит** (`testing.md`): драйвер подменяется, проверяется
отбор и то, кого круг позвал, а не живое нажатие звезды.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import favorite_round

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


# ⚠⚠ Что ручка читает из строки анкеты. Номер на сайте нужен кадру сокета
# (`chat_socket_open`), состояние входа — его cookie; без них дорога отвечает «у
# анкеты нет номера на сайте». Список именно здесь, а не в голове: запрос круга
# выбирал пять колонок, и ни одной из этих — заход 08.11.2026 на проде дал три
# отказа подряд при включённом занятии и живых анкетах.
FAVORITE_HANDLE_NEEDS = ('profile_id', 'storage_state')


class _FakeDriver:
    """Драйвер сайта со своей ручкой: помнит, кого просили зажечь.

    ⚠ Заодно сторожит **строку**: ручка берёт её целиком, и недостающий
    столбец виден только по тому, чего в ней нет.
    """

    def __init__(self, handle: bool = True, uids: set | None = None):
        self.handle = handle
        self.already = uids or set()
        self.added = []
        self.rows = []

    def by_handle(self) -> bool:
        return self.handle

    async def uids(self, profile: dict) -> set:
        self.rows.append(dict(profile))

        return set(self.already)

    async def add(self, profile: dict, uid: str) -> dict:
        self.rows.append(dict(profile))
        self.added.append(str(uid))

        return {'ok': True, 'error': ''}

    async def forget(self, profile: dict, uid: str) -> dict:
        return {'ok': True, 'error': ''}


async def _said(profile_id: int, uid: str) -> None:
    """Его входящая реплика — тот самый повод зажечь звезду."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction) VALUES (%s, %s, %s, %s, %s, %s)',
            (int(profile_id), uid, 'Someone', 'привет',
             f'zz-{uuid.uuid4().hex[:12]}', 'in'))
        await db.connection.commit()


async def _settings(site_id: int, api_base: str) -> None:
    """Пишет сайту `api_base` — пустая строка значит «API у сайта нет»."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE site SET settings = JSON_SET(COALESCE(settings, %s),'
            " '$.api_base', %s) WHERE site_id = %s",
            ('{}', str(api_base), int(site_id)))
        await db.connection.commit()


def _driver(monkeypatch, driver: _FakeDriver) -> None:
    """Подменяет выбор драйвера избранных — на обе дороги разом.

    ⚠ Патч стоит у **реестра**, а не у вызывающих: и круг, и `favorite.py`
    спрашивают его отложенным импортом внутри функции, то есть видят подмену
    оба. Поставь её у одного — второй ушёл бы на живой сайт.
    """
    monkeypatch.setattr('src.dating.site.site_registry.site_registry_favorite',
                        lambda presence=None: driver)


class TestASiteWithItsOwnHandleNeedsNoApi:
    """Главное: `api_base` перестал быть пропуском в круг."""

    async def test_the_round_lights_a_star_without_api_base(
            self, girl_make, monkeypatch):
        """Сайта с ручкой круг не пропускает мимо — звезда нажата."""
        await _settings(STAND_SITE, '')
        profile_id = await girl_make('fav', online=True)
        await _said(profile_id, 'u-handle-1')

        driver = _FakeDriver()
        _driver(monkeypatch, driver)

        got = await favorite_round._work({'site_id': STAND_SITE}, 0)

        assert got is not None, 'круг ушёл с сайта, у которого есть своя ручка'
        assert driver.added == ['u-handle-1'], \
            f'звезду не нажали: {driver.added}'
        assert got['added'] == 1

    async def test_the_row_carries_what_the_handle_reads(
            self, girl_make, monkeypatch):
        """⚠⚠⚠ Строка анкеты обязана нести то, чем ручка ходит на сайт.

        Ручке нужен номер анкеты на сайте и состояние входа: из первого
        собирается кадр сокета, из второго — cookie. Запрос круга выбирал пять
        колонок и ни одной из этих, и дорога отвечала «у анкеты нет номера на
        сайте» — занятие было включено, анкеты в сети, звёздочек ноль (прод,
        08.11.2026: три отказа подряд).

        Та же грабля, что уже стоила отметок обмена у писем: не выбрал столбец
        запрос — до дела он не доедет.
        """
        await _settings(STAND_SITE, '')
        profile_id = await girl_make('fav', online=True)
        await _said(profile_id, 'u-handle-4')

        driver = _FakeDriver()
        _driver(monkeypatch, driver)

        await favorite_round._work({'site_id': STAND_SITE}, 0)

        assert driver.rows, 'ручку не позвали вовсе'
        for key in FAVORITE_HANDLE_NEEDS:
            assert key in driver.rows[0], \
                f'в строке анкеты нет «{key}» — ручке не с чем идти на сайт'

    async def test_a_site_without_api_and_without_a_handle_is_skipped(
            self, girl_make, monkeypatch):
        """⚠ Прежнее правило цело: нет ни API, ни ручки — идти нечем.

        Иначе круг жал бы звёздочки сценарием, не зная, кто там уже есть, — а
        кнопка на сайте переключатель, и такое нажатие звезду **снимет**.
        """
        await _settings(STAND_SITE, '')
        profile_id = await girl_make('fav', online=True)
        await _said(profile_id, 'u-handle-2')

        driver = _FakeDriver(handle=False)
        _driver(monkeypatch, driver)

        got = await favorite_round._work({'site_id': STAND_SITE}, 0)

        assert got is None, 'круг пошёл на сайт, до которого ему нечем дойти'
        assert driver.added == []

    async def test_a_site_with_api_still_goes_the_old_road(
            self, girl_make, monkeypatch):
        """⚠ Сайт с API ведёт себя как прежде — правка его не касается."""
        await _settings(STAND_SITE, 'https://example.com/api')
        profile_id = await girl_make('fav', online=True)
        await _said(profile_id, 'u-handle-3')

        asked = []

        async def _uids(profile, base, password):
            asked.append(base)

            return set()

        added = []

        async def _add(profile, uid, password='', reason=''):
            added.append(str(uid))

            return {'state': 'done', 'error': ''}

        monkeypatch.setattr(favorite_round, 'favorite_uids', _uids)
        monkeypatch.setattr(favorite_round, 'favorite_add', _add)

        got = await favorite_round._work({'site_id': STAND_SITE}, 0)

        assert got is not None
        assert asked == ['https://example.com/api'], \
            'адрес API до списка избранных не доехал'
        assert added == ['u-handle-3']


@pytest.fixture(autouse=True)
async def _clean_site_setting():
    """Возвращает сайту стенда прежний вид: `api_base` ставят сами тесты."""
    yield

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE site SET settings = JSON_REMOVE(settings, '$.api_base')"
            ' WHERE site_id = %s', (STAND_SITE,))
        await db.connection.commit()
