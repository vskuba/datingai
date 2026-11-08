"""Список избранных у сайта без сценария: номера с сайта, лица из карточек.

## ⚠⚠ Зачем

Лента «MY FAVORITES» ходила одной дорогой — сценарием у соседа. У Step2Love
сценариев избранных нет вовсе, и лента писала «не снялось» при живых звёздочках:
08.11.2026 на проде Marina и Anastasiya добавили Lance, сайт их обеих показывал,
а панель — нет.

Дорога без сценария собирается из двух готовых частей:

| Что | Откуда | Почему оттуда |
|-----|--------|---------------|
| **чьи** номера | кадр авторизации сокета **под этой анкетой** | список избранных свой у каждой девушки, и знает его сайт |
| **кто** эти люди | наши карточки `companion` по `uid` | карточка одна на сайт и человека, «чей он» в ней не записано |

⚠ Наружу сюита не ходит (`testing.md`): драйвер подменяется, проверяется сборка
строк и то, какой дорогой пошёл снимок.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.people_known import people_known_rows

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


class _FakeDriver:
    """Драйвер сайта со своей ручкой: отдаёт номера избранных этой анкеты."""

    def __init__(self, uids: set | None = None, handle: bool = True):
        self.handle = handle
        self.list = uids or set()
        self.asked = []

    def by_handle(self) -> bool:
        return self.handle

    async def uids(self, profile: dict) -> set:
        self.asked.append(int(profile.get('id') or 0))

        return set(self.list)


async def _card(uid: str, name: str, photo: str = '', age: int = 0) -> None:
    """Карточка собеседника — то, из чего берутся имя и лицо."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO companion (site_id, uid, name, photo, age)'
            ' VALUES (%s, %s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE name = VALUES(name), photo = VALUES(photo),'
            '                         age = VALUES(age)',
            (STAND_SITE, uid, name, photo, int(age)))
        await db.connection.commit()


class TestTheRowsAreBuiltFromCards:
    """Номера приходят с сайта, остальное — из того, что мы уже знаем."""

    async def test_a_known_man_comes_with_his_name_and_face(self):
        uid = f'fv-{uuid.uuid4().hex[:8]}'
        await _card(uid, 'Lance', photo='/uploades/photos/lance.jpg', age=58)

        got = await people_known_rows(STAND_SITE, [uid])

        assert len(got) == 1, 'строка не собралась'
        assert got[0]['uid'] == uid
        assert got[0]['name'] == 'Lance'
        assert got[0]['age'] == 58
        assert got[0]['photo'].endswith('/uploades/photos/lance.jpg')

    async def test_an_unknown_man_keeps_his_number(self):
        """⚠ Карточки может не быть: человека добавили на сайте руками.

        Выбросить его значило бы показать список короче того, что на сайте, —
        то есть соврать именно там, где человек сверяет панель с сайтом.
        """
        uid = f'fv-{uuid.uuid4().hex[:8]}'

        got = await people_known_rows(STAND_SITE, [uid])

        assert [one['uid'] for one in got] == [uid]
        assert got[0]['name'] == ''

    async def test_the_order_is_the_one_asked_for(self):
        """⚠ Порядок задаёт вызывающий, а не база.

        `IN (…)` вернул бы строки в своём порядке, и лента перетасовывалась бы
        при каждом чтении — список, в котором ищут глазами, этого не прощает.
        """
        first = f'fv-{uuid.uuid4().hex[:8]}'
        second = f'fv-{uuid.uuid4().hex[:8]}'
        await _card(second, 'Second')
        await _card(first, 'First')

        got = await people_known_rows(STAND_SITE, [first, second])

        assert [one['uid'] for one in got] == [first, second]

    async def test_the_row_carries_what_the_page_reads(self):
        """⚠⚠ Поля — те же, что у двух прежних дорог.

        Страница и кэш об источнике не знают и знать не должны: разойдись
        набор полей, лента рисовала бы карточки без лиц и без точки «в сети»
        ровно у одного сайта.
        """
        uid = f'fv-{uuid.uuid4().hex[:8]}'
        await _card(uid, 'Lance')

        got = (await people_known_rows(STAND_SITE, [uid]))[0]

        for key in ('uid', 'name', 'age', 'photo', 'photo_uid', 'online',
                    'is_new', 'link'):
            assert key in got, f'в строке нет поля «{key}»'


class TestTheListTakesTheHandleRoad:
    """Сайт со своей ручкой идёт мимо сценария — иначе всё это незачем."""

    async def test_the_snapshot_asks_the_driver(self, girl_make, monkeypatch):
        from src.uvicorn.routers.dating import people as people_router

        profile_id = await girl_make('favlist', online=True)
        uid = f'fv-{uuid.uuid4().hex[:8]}'
        await _card(uid, 'Lance')

        driver = _FakeDriver({uid})
        monkeypatch.setattr(
            'src.dating.site.site_registry.site_registry_favorite',
            lambda presence=None: driver)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (profile_id,))
            profile = await db.fetchone()

        got = await people_router._snapshot(dict(profile), 'favorite')

        assert driver.asked == [profile_id], \
            'список избранных спросили не у драйвера этой анкеты'
        assert [one['uid'] for one in got] == [uid]

    async def test_other_lists_keep_the_old_road(self, girl_make, monkeypatch):
        """⚠ Ручка отвечает **только за избранных**.

        «Кто в сети» и «Диалоги» она не знает, и подмени мы им дорогу, лента
        онлайна опустела бы молча.
        """
        from src.uvicorn.routers.dating import people as people_router

        profile_id = await girl_make('favlist', online=True)
        driver = _FakeDriver({'u-1'})
        monkeypatch.setattr(
            'src.dating.site.site_registry.site_registry_favorite',
            lambda presence=None: driver)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (profile_id,))
            profile = await db.fetchone()

        with pytest.raises(Exception):
            await people_router._snapshot(dict(profile), 'online')

        assert driver.asked == [], 'ручку позвали за чужим списком'


@pytest.fixture(autouse=True)
async def _sweep():
    """Карточки стенда за собой."""
    yield

    async with mysql_get_db_async() as db:
        await db.execute("DELETE FROM companion WHERE site_id = %s AND uid LIKE 'fv-%%'",
                         (STAND_SITE,))
        await db.connection.commit()
