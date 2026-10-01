"""Ручки вкладки «Галерея»: что принимаем, кому отдаём, чем отвечаем на отказ.

## ⚠⚠ Что здесь проверяется

Три двери, у каждой своя цена ошибки:

| Дверь | Цена |
|-------|------|
| `PUT /folder` принимает только имя из списка | опечатка сохраняется молча и всплывает отказом при обходе — там её уже не связать с набранным |
| `GET /img` сверяет файл с анкетой | галерея одной девушки отдаёт снимки другой любому вошедшему |
| `PUT /file` ставит `edited_at` | без отметки следующая пачка у модели затрёт правку человека |

Обработчики зовутся **в процессе**: поднятая панель бежит свою ветку, и по HTTP
эти тесты краснели бы не из-за кода (`testing.md` §1.2). Диск — заглушка.
"""

import uuid

import pytest
from fastapi import HTTPException

from mysql_.mysql_ import mysql_get_db_async

from src.dating.gallery_index import gallery_index_profile
from src.uvicorn.routers.dating.gallery import (GalleryFileSave,
                                                GalleryFolderSave,
                                                gallery_file_put,
                                                gallery_folder_put,
                                                gallery_folders, gallery_image,
                                                gallery_show, gallery_sync)
from src.uvicorn.routers.dating.profile_ import profile_delete

pytestmark = pytest.mark.profile

STAND_SITE = 999_343
GIRL_FOLDER = 'girl@example.com'


@pytest.fixture
def tree(drive):
    """Дерево настоящей формы: папка девушки — не лист."""
    drive.add_folder('Dating', 'root-1')
    drive.add_folder(GIRL_FOLDER, 'girl-1', parent='root-1')
    drive.add_folder('photo', 'photo-1', parent='girl-1')
    drive.add_folder('public', 'public-1', parent='photo-1')
    drive.add_file('diana.png', 'file-1', parent='public-1')

    return drive


@pytest.fixture
async def girl(auth_client):
    """Две анкеты: своя и чужая — чтобы проверять, что чужое не отдаётся."""
    made = []
    for _ in range(2):
        made.append(int((await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'gallery-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']))

    yield made

    async with mysql_get_db_async() as db:
        for one in made:
            await db.execute('DELETE FROM gallery_file WHERE profile_id = %s', (one,))
        await db.connection.commit()
    for one in made:
        await profile_delete(one, user=None)


async def test_folders_come_as_list(tree):
    """Список для выбора приезжает именами, без отказа."""
    got = (await gallery_folders(user=None))['result']

    assert got == {'folders': [GIRL_FOLDER], 'why': ''}


async def test_folders_refusal_is_words_not_code(tree):
    """⚠ Диск молчит — отдаём слова и пустой список, а не код отказа.

    Пустой селект без объяснения человек читает как «папок нет» и идёт
    заводить их заново.
    """
    tree.not_shared = True

    got = (await gallery_folders(user=None))['result']

    assert got['folders'] == []
    assert 'расшарил' in got['why']


async def test_folder_must_be_from_the_list(tree, girl):
    """⚠ Имя не из списка отвергается сразу, а не сохраняется молча."""
    with pytest.raises(HTTPException) as refused:
        await gallery_folder_put(girl[0], GalleryFolderSave(folder='мимо-списка'),
                                 user=None)

    assert refused.value.status_code == 422
    assert 'мимо-списка' in refused.value.detail


async def test_folder_from_the_list_is_saved(tree, girl):
    """Имя из списка сохраняется и возвращается карточкой."""
    got = await gallery_folder_put(girl[0], GalleryFolderSave(folder=GIRL_FOLDER),
                                   user=None)

    assert got['result']['folder'] == GIRL_FOLDER

    async with mysql_get_db_async() as db:
        await db.execute('SELECT gallery_folder FROM profile WHERE id = %s', (girl[0],))
        assert ((await db.fetchone()) or {})['gallery_folder'] == GIRL_FOLDER


async def test_empty_folder_returns_to_default(tree, girl):
    """Пустое значение стирает выбор — дальше работает логин анкеты."""
    await gallery_folder_put(girl[0], GalleryFolderSave(folder=GIRL_FOLDER), user=None)
    got = await gallery_folder_put(girl[0], GalleryFolderSave(folder=''), user=None)

    assert got['result']['folder'] == ''


async def test_sync_then_show(tree, girl):
    """Обновили — плитки появились группами."""
    await gallery_folder_put(girl[0], GalleryFolderSave(folder=GIRL_FOLDER), user=None)
    synced = (await gallery_sync(girl[0], user=None))['result']

    assert synced['ok'] is True and synced['found'] == 1

    shown = (await gallery_show(girl[0], user=None))['result']
    assert [g['name'] for g in shown['groups']] == ['public']
    assert shown['folder'] == GIRL_FOLDER


async def test_image_of_a_stranger_is_refused(tree, girl):
    """⚠⚠ Файл чужой анкеты не отдаётся: номер приходит из адреса."""
    await gallery_folder_put(girl[0], GalleryFolderSave(folder=GIRL_FOLDER), user=None)
    await gallery_index_profile(girl[0], GIRL_FOLDER)

    with pytest.raises(HTTPException) as refused:
        await gallery_image(girl[1], 'file-1', w=400, user=None)

    assert refused.value.status_code == 404


async def test_image_width_is_from_the_set(tree, girl):
    """Произвольный размер — `422`: разнобой рвёт кэш браузера и Диска."""
    with pytest.raises(HTTPException) as refused:
        await gallery_image(girl[0], 'file-1', w=399, user=None)

    assert refused.value.status_code == 422


async def test_image_bytes_arrive(tree, girl):
    """Байты превью доезжают, и это картинка, а не страница входа."""
    await gallery_index_profile(girl[0], GIRL_FOLDER)

    answer = await gallery_image(girl[0], 'file-1', w=400, user=None)

    assert answer.body[:8] == b'\x89PNG\r\n\x1a\n'
    assert answer.headers['Cache-Control'].startswith('private')


async def test_login_page_instead_of_bytes_is_a_refusal(tree, girl):
    """⚠ Перенаправление на вход — отказ, а не содержимое.

    Без этой проверки вызывающий сохранил бы HTML формы входа под именем
    картинки и узнал бы об этом, только открыв «битый» файл.
    """
    await gallery_index_profile(girl[0], GIRL_FOLDER)
    tree.login_redirect = True

    with pytest.raises(HTTPException) as refused:
        await gallery_image(girl[0], 'file-1', w=400, user=None)

    assert refused.value.status_code == 502
    assert 'вход' in refused.value.detail


async def test_edit_marks_the_row_as_touched(tree, girl):
    """⚠⚠ Правка ставит `edited_at` — с этого мгновения разбор строку не берёт."""
    await gallery_index_profile(girl[0], GIRL_FOLDER)

    got = await gallery_file_put(girl[0], 'file-1',
                                 GalleryFileSave(description='её платье',
                                                 tags=['lace', ' ', 'indoor']),
                                 user=None)

    assert got['result']['tags'] == ['lace', 'indoor'], 'пустой тег обязан отсеяться'

    async with mysql_get_db_async() as db:
        await db.execute("SELECT description, edited_at FROM gallery_file "
                         " WHERE file_id = 'file-1'")
        row = dict((await db.fetchone()) or {})

    assert row['description'] == 'её платье'
    assert row['edited_at'] is not None


async def test_edit_of_a_stranger_is_refused(tree, girl):
    """Чужой файл не правится — та же проверка, что и у байтов."""
    await gallery_index_profile(girl[0], GIRL_FOLDER)

    with pytest.raises(HTTPException) as refused:
        await gallery_file_put(girl[1], 'file-1', GalleryFileSave(description='чужое'),
                               user=None)

    assert refused.value.status_code == 404
