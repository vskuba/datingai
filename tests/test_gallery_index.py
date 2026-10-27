"""Индекс галереи: обход Диска в таблицу и всё, что в нём легко сломать.

## ⚠⚠ Что здесь на самом деле проверяется

Не «строки появились» — это видно и глазами. Проверяются четыре обещания, у
каждого из которых есть цена, если оно нарушится:

| Обещание | Цена нарушения |
|----------|----------------|
| описание и теги переживают обход | платим модели второй раз, правку человека теряем совсем |
| пропавший файл метится, а не удаляется | моргание Диска стирает накопленное навсегда |
| «не расшарили» ≠ «папки нет» | человек переименовывает папки, которых аккаунт не видит |
| дерево обходится вглубь | папка девушки не лист, и обход детей увидит ноль картинок |

Диск — заглушка (`drive_fake`), она подменяет **транспорт**; база настоящая, но
анкета своя, с доменом `@example.com` (`testing.md` §1.1.1).
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from tests.drive_fake import DRIVE_FAKE_ROOT, DRIVE_FAKE_ROOT_SETTING

from src.dating.error import ERROR_DRIVE
from src.dating.gallery_index import (GALLERY_WHY_NOT_SHARED,
                                      gallery_index_folders,
                                      gallery_index_profile,
                                      gallery_index_sync)
from src.uvicorn.routers.dating.profile_ import profile_delete

pytestmark = pytest.mark.profile

STAND_SITE = 999_343

ROOT = DRIVE_FAKE_ROOT
GIRL_FOLDER = 'girl@example.com'


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда. Убирается за собой — вместе со строками галереи."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'gallery-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM gallery_file WHERE profile_id = %s', (profile_id,))
        await db.connection.commit()
    await profile_delete(profile_id, user=None)


# Номер настоящего вида — 33 знака. Короткий (`root-1`) на номер не похож, и
# дверь к Диску по нему справедливо не пойдёт: вид решает, **спрашивать ли**.
DRIVE_ID = '1TfgC8S7mBKUAiz8AxTq4Z3xHjXjTIyZe'


@pytest.fixture
def tree_by_id(drive):
    """Общая папка, которую **нельзя** найти по имени из настройки.

    Повторяет живое положение дел: папка чужая, назвал её владелец, и в
    настройке у нас стоит её номер. Имя при этом ещё и с краевыми пробелами —
    как на настоящем Диске (снято 27.10.2026).
    """
    drive.add_folder(' 1. Модели_фото_данные _все_сайты ', DRIVE_ID)
    drive.add_folder(GIRL_FOLDER, 'girl-9', parent=DRIVE_ID)
    drive.add_folder('photo', 'photo-9', parent='girl-9')
    drive.add_file('diana.png', 'file-9', parent='photo-9')

    return drive


@pytest.fixture
def tree(drive):
    """Дерево, как его раскладывают люди: папка девушки — не лист.

    Повторяет настоящую раскладку, снятую с живого Диска 01.10.2026:
    `Dating/<логин>/photo/{public,intim}`.
    """
    drive.add_folder(ROOT, 'root-1')
    drive.add_folder(GIRL_FOLDER, 'girl-1', parent='root-1')
    drive.add_folder('photo', 'photo-1', parent='girl-1')
    drive.add_folder('public', 'public-1', parent='photo-1')
    drive.add_folder('intim', 'intim-1', parent='photo-1')
    drive.add_file('diana.png', 'file-1', parent='public-1', md5='md5-первый')
    drive.add_file('vid.mp4', 'file-2', parent='intim-1', mime='video/mp4')

    return drive


async def test_folders_for_choosing(tree):
    """Список для выбора — папки первого уровня, а не содержимое."""
    assert await gallery_index_folders() == [GIRL_FOLDER]


async def test_walk_goes_deep(tree, girl):
    """⚠ Папка девушки не лист: обход обязан дойти до файлов через два уровня."""
    got = await gallery_index_profile(girl, GIRL_FOLDER)

    assert got['found'] == 2, 'оба файла лежат глубже прямых детей'
    assert got['added'] == 2

    async with mysql_get_db_async() as db:
        await db.execute('SELECT path, kind FROM gallery_file WHERE profile_id = %s '
                         ' ORDER BY path', (girl,))
        rows = [(r['path'], r['kind']) for r in (await db.fetchall() or [])]

    assert rows == [('photo/intim/vid.mp4', 'video'),
                    ('photo/public/diana.png', 'image')]


async def test_second_pass_adds_nothing(tree, girl):
    """Повторный обход не плодит строк — ключом служит номер файла, не путь."""
    await gallery_index_profile(girl, GIRL_FOLDER)
    again = await gallery_index_profile(girl, GIRL_FOLDER)

    assert again == {'found': 2, 'added': 0, 'gone': 0, 'back': 0}


async def test_pages_are_joined(tree, girl):
    """Ответ двумя страницами склеивается: `pageToken` обходится до конца."""
    tree.split_pages = True

    got = await gallery_index_profile(girl, GIRL_FOLDER)

    assert got['found'] == 2, 'вторая страница потерялась'


async def test_moved_file_keeps_its_meaning(tree, girl):
    """⚠⚠ Файл переехал в другую папку — описание едет с ним, а не добывается заново.

    Ключ строки — номер файла на Диске. Будь ключом путь, перенос выглядел бы
    как «старый пропал, новый появился», и за описание мы заплатили бы модели
    второй раз.
    """
    await gallery_index_profile(girl, GIRL_FOLDER)
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE gallery_file SET description = 'её описание' "
                         " WHERE file_id = 'file-1'")
        await db.connection.commit()

    # Переносим на Диске: тот же номер, другая папка.
    tree.children['public-1'].remove('file-1')
    tree.children['intim-1'].append('file-1')
    await gallery_index_profile(girl, GIRL_FOLDER)

    async with mysql_get_db_async() as db:
        await db.execute("SELECT path, description FROM gallery_file "
                         " WHERE file_id = 'file-1'")
        row = dict((await db.fetchone()) or {})

    assert row['path'] == 'photo/intim/diana.png', 'путь обязан обновиться'
    assert row['description'] == 'её описание', 'описание обязано пережить перенос'


async def test_gone_is_marked_not_deleted(tree, girl):
    """⚠⚠ Пропавший гаснет, а вернувшийся зажигается — вместе с описанием."""
    await gallery_index_profile(girl, GIRL_FOLDER)
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE gallery_file SET description = 'дорогое описание' "
                         " WHERE file_id = 'file-1'")
        await db.connection.commit()

    tree.children['public-1'].remove('file-1')
    assert (await gallery_index_profile(girl, GIRL_FOLDER))['gone'] == 1

    async with mysql_get_db_async() as db:
        await db.execute("SELECT is_gone, description FROM gallery_file "
                         " WHERE file_id = 'file-1'")
        row = dict((await db.fetchone()) or {})
    assert row['is_gone'] == 1, 'строка обязана остаться, а не исчезнуть'
    assert row['description'] == 'дорогое описание'

    tree.children['public-1'].append('file-1')
    assert (await gallery_index_profile(girl, GIRL_FOLDER))['back'] == 1

    async with mysql_get_db_async() as db:
        await db.execute("SELECT is_gone, description FROM gallery_file "
                         " WHERE file_id = 'file-1'")
        row = dict((await db.fetchone()) or {})
    assert row['is_gone'] == 0
    assert row['description'] == 'дорогое описание'


async def test_edited_by_hand_survives(tree, girl):
    """Правка человека переживает обход — её не трогает ни одно поле записи."""
    await gallery_index_profile(girl, GIRL_FOLDER)
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE gallery_file SET description = 'правлено руками', "
                         "edited_at = NOW() WHERE profile_id = %s", (girl,))
        await db.connection.commit()

    await gallery_index_profile(girl, GIRL_FOLDER)

    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM gallery_file '
                         ' WHERE profile_id = %s AND edited_at IS NOT NULL '
                         "   AND description = 'правлено руками'", (girl,))
        kept = int(((await db.fetchone()) or {}).get('n') or 0)

    assert kept == 2, 'обход стёр правку человека'


async def test_not_shared_says_so(tree, girl):
    """⚠⚠ «Не расшарили» и «папки нет» — разные фразы.

    У сервисного аккаунта оба случая выглядят пустым ответом Диска. Скажи
    панель «папки нет», человек пошёл бы переименовывать папки, которых
    аккаунту не видно вовсе.
    """
    tree.not_shared = True

    got = await gallery_index_sync(girl, GIRL_FOLDER)

    assert got['ok'] is False
    assert got['why'] == GALLERY_WHY_NOT_SHARED


async def test_missing_folder_names_it(tree, girl):
    """Папки девушки нет — отказ называет ту папку, которую искали."""
    got = await gallery_index_sync(girl, 'её-папки-тут-нет')

    assert got['ok'] is False
    assert 'её-папки-тут-нет' in got['why']
    assert got['why'] != GALLERY_WHY_NOT_SHARED, 'это другой отказ'


async def test_root_by_id(tree_by_id, monkeypatch):
    """⚠⚠ Общая папка задана **номером** — имени такого в настройке нет вовсе.

    Так и настроен прод: папка чужая, её ведёт владелец, и переименование с
    его стороны приходит молча. По имени галерея в этот день ослепла бы.
    """
    _root_setting(monkeypatch, DRIVE_ID)

    assert await gallery_index_folders() == [GIRL_FOLDER]


async def test_root_by_link(tree_by_id, monkeypatch):
    """Ссылка из адресной строки годится как есть: номер из неё достают за нас."""
    _root_setting(monkeypatch,
                  f'https://drive.google.com/drive/folders/{DRIVE_ID}?usp=sharing')

    assert await gallery_index_folders() == [GIRL_FOLDER]


async def test_root_name_survives_stray_spaces(tree_by_id, monkeypatch):
    """Имя с краевыми пробелами — живой случай, и настройка его переживает.

    Человек перепишет имя из Диска глазами, пробелов не увидит и не наберёт.
    """
    _root_setting(monkeypatch, '1. Модели_фото_данные _все_сайты')

    assert await gallery_index_folders() == [GIRL_FOLDER]


async def test_girl_folder_with_stray_space(tree, girl):
    """⚠⚠ Имя папки девушки в списке и в сохранённом значении — **одно и то же**.

    Пробел в имени папки ставит человек, а обрезают его и браузер, и ручка
    сохранения. Отдай список как есть — выбранная папка не совпала бы с собой
    же при сохранении, а обход после сохранения её бы не нашёл.
    """
    tree.items['girl-1']['name'] = f' {GIRL_FOLDER} '

    assert await gallery_index_folders() == [GIRL_FOLDER], 'список обязан быть обрезан'
    assert (await gallery_index_profile(girl, GIRL_FOLDER))['found'] == 2


async def test_id_from_outside_is_refused(tree, girl):
    """⚠⚠ Номер папки, лежащей **вне общей**, за папку девушки не считается.

    Иначе одна описка в поле «папка на Диске» утащила бы в галерею анкеты
    чужие фотографии с чужого конца Диска — молча и с видом удачи.
    """
    tree.add_folder('чужая папка', '1OutsideFolderAAAAAAAAAAAAAAAAAAAA')
    tree.add_file('чужое.png', 'file-outside', parent='1OutsideFolderAAAAAAAAAAAAAAAAAAAA')

    got = await gallery_index_sync(girl, '1OutsideFolderAAAAAAAAAAAAAAAAAAAA')

    assert got['ok'] is False, 'папка не внутри общей — брать её нельзя'
    assert got['why'] != GALLERY_WHY_NOT_SHARED, 'это другой отказ'


async def test_token_is_asked_once(tree, girl):
    """Токен берётся из кэша: два обхода — один поход за токеном."""
    await gallery_index_profile(girl, GIRL_FOLDER)
    await gallery_index_profile(girl, GIRL_FOLDER)

    tokens = [url for url in tree.calls if 'oauth2.googleapis.com' in url]

    assert len(tokens) == 1, f'за токеном сходили {len(tokens)} раз вместо одного'


async def test_missing_folder_is_not_a_source_failure(tree, girl):
    """⚠⚠ «Папки нет» не идёт в журнал отказов, а «не расшарили» — идёт.

    Журнал отказов — для того, что **не про анкету** (правило `error.py`).
    У девушки без фотографий папки и не должно быть; пиши мы это в журнал,
    один заход задания по полусотне анкет клал бы туда полсотни строк подряд,
    затирая друг друга, — и настоящий отказ источника в этом шуме утонул бы.

    Проверено на живом заходе: 53 анкеты, папка одна, 52 отказа «папки нет».
    """
    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM error WHERE source = %s', (ERROR_DRIVE,))
        await db.connection.commit()

    # Про анкету: в журнал не идёт.
    assert (await gallery_index_sync(girl, 'папки-нет'))['ok'] is False
    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM error WHERE source = %s',
                         (ERROR_DRIVE,))
        assert int(((await db.fetchone()) or {}).get('n') or 0) == 0, \
            'отказ про анкету засорил журнал источников'

    # Про источник: идёт.
    tree.not_shared = True
    assert (await gallery_index_sync(girl, GIRL_FOLDER))['ok'] is False
    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM error WHERE source = %s',
                         (ERROR_DRIVE,))
        assert int(((await db.fetchone()) or {}).get('n') or 0) == 1, \
            'отказ источника обязан попасть в журнал'
        await db.execute('DELETE FROM error WHERE source = %s', (ERROR_DRIVE,))
        await db.connection.commit()


def _root_setting(monkeypatch, value: str) -> None:
    """Подменить настройку общей папки на время одного теста.

    По умолчанию её пиннит фикстура Диска (`DRIVE_FAKE_ROOT`); здесь она
    задаётся иначе — номером, ссылкой, именем с пробелами.
    """
    monkeypatch.setattr('src.dating.gallery_index.config_get',
                        lambda name, default='': (value
                                                  if name == DRIVE_FAKE_ROOT_SETTING
                                                  else default))
