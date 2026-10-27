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

from datetime import datetime, timedelta

from datetime_.datetime_ import datetime_utc_now
from mysql_.mysql_ import mysql_get_db_async
from setting_.setting_state import setting_state_clear, setting_state_patch

from tests.drive_fake import DRIVE_FAKE_ROOT

from src.cronjob.cronjob import CRONJOB_JOBS, CRONJOB_LAST_KEY
from src.dating.gallery import GALLERY_ROUND_JOB
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
    drive.add_folder(DRIVE_FAKE_ROOT, 'root-1')
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

    assert got['folders'] == [GIRL_FOLDER]
    assert got['why'] == ''


async def test_folders_refusal_is_words_not_code(tree):
    """⚠ Диск молчит — отдаём слова и пустой список, а не код отказа.

    Пустой селект без объяснения человек читает как «папок нет» и идёт
    заводить их заново.
    """
    tree.not_shared = True

    got = (await gallery_folders(user=None))['result']

    assert got['folders'] == []
    assert 'расшарил' in got['why']


@pytest.fixture
async def round_clean():
    """Отметка заходов задания — общая строка `setting`, и за собой её убираем.

    ⚠ Иначе тест про «заходов не было» зеленел бы или краснел в зависимости от
    того, что оставил предыдущий, — а это худший вид связи между тестами.
    """
    await setting_state_clear(CRONJOB_LAST_KEY)

    yield

    await setting_state_clear(CRONJOB_LAST_KEY)


async def test_round_without_a_single_run_says_so(tree, round_clean):
    """⚠⚠ «Заходов не было» — это не «скоро будет», и путать их нельзя.

    На установке, где задание не завели в расписание планировщика, обещание
    «возьмём через час» повторялось бы каждый час, а фотографии не появлялись
    бы никогда.
    """
    got = (await gallery_folders(user=None))['result']['round']

    assert got['last_at'] == ''
    assert got['next_at'] == ''
    assert got['late'] is False


async def test_round_counts_from_the_last_run(tree, round_clean):
    """Следующий заход считается **от прошлого**, а не от зашитого расписания.

    ⚠ Расписанием распоряжается Cronicle, и второй его записи у нас нет: смени
    он минуту — предсказание поедет за ним само.
    """
    was = datetime_utc_now() - timedelta(minutes=10)
    await setting_state_patch(CRONJOB_LAST_KEY,
                              {GALLERY_ROUND_JOB: {'at': was.isoformat(timespec='seconds'),
                                                   'ok': True}})

    got = (await gallery_folders(user=None))['result']['round']

    assert got['late'] is False
    assert (datetime.fromisoformat(got['next_at'])
            - datetime.fromisoformat(got['last_at'])).total_seconds() \
        == got['every_sec']


async def test_round_tells_when_the_job_stops_coming(tree, round_clean):
    """Задание молчит дольше двух шагов — говорим это, а не зовём «через час»."""
    was = datetime_utc_now() - timedelta(hours=9)
    await setting_state_patch(CRONJOB_LAST_KEY,
                              {GALLERY_ROUND_JOB: {'at': was.isoformat(timespec='seconds'),
                                                   'ok': True}})

    assert (await gallery_folders(user=None))['result']['round']['late'] is True


async def test_round_is_told_even_when_drive_is_silent(tree, round_clean):
    """⚠ Диск молчит — срок захода всё равно отвечается.

    Заход задания к доступу на Диск отношения не имеет, а человек, увидев
    красную строку, первым делом спрашивает «когда оно попробует снова».
    """
    tree.not_shared = True

    got = (await gallery_folders(user=None))['result']

    assert got['why'], 'отказ Диска обязан остаться'
    assert 'round' in got, 'срок захода пропал вместе с папками'


def test_the_job_name_matches_the_scheduler_list():
    """⚠⚠ Имя задания у галереи и в списке планировщика — одно и то же.

    Разойдись они, страница вечно показывала бы «обход ещё не заходил» при
    исправно работающем задании: отметка легла бы под другим ключом.
    """
    assert GALLERY_ROUND_JOB in CRONJOB_JOBS


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
                                                 tags=['dress', ' ', 'indoor']),
                                 user=None)

    assert got['result']['tags'] == ['dress', 'indoor'], 'пустой тег обязан отсеяться'

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


async def test_tags_handle_gives_the_whole_dictionary(auth_client):
    """⚠⚠ Словарь отдаёт **сервер**, а не своя копия на странице.

    Список закрытый: тег не из него ручка правки молча выбросит. Держи страница
    свою копию — она разошлась бы с кодом на первой правке, и человек выбирал бы
    теги, которые не сохранятся. Та же грабля, что уже была с подразделами
    настроек сайта.

    ⚠ `name` в ответе — **подпись человеку**, а не ключ группы: ключ латиницей
    (`clothes`), подпись лежит в `GALLERY_VISION_GROUP_TITLES`. Прежде здесь
    сверялись ключи, потому что ключ и был подписью — одно значение работало
    обоими сразу, что правило §1.2 и запрещает. Порядок проверяется по-прежнему.
    """
    from src.dating.gallery_vision import (GALLERY_VISION_GROUP_TITLES,
                                           GALLERY_VISION_TAGS,
                                           GALLERY_VISION_TAGS_ALL)

    got = (await auth_client.get('/api/dating/gallery/tags')).json()['result']
    groups = got['groups']

    assert len(groups) == len(GALLERY_VISION_TAGS)
    assert [one['name'] for one in groups] \
        == list(GALLERY_VISION_GROUP_TITLES.values()), \
        'порядок или подписи групп разошлись со словарём'
    assert all(one['name'] for one in groups), \
        'группа приехала без подписи — окно покажет человеку пустую строку'

    flat = [tag for one in groups for tag in one['tags']]
    assert flat == list(GALLERY_VISION_TAGS_ALL), \
        'ручка отдаёт не тот список, по которому идёт отсев'


async def test_the_page_asks_the_server_for_tags():
    """⚠ Страница ходит за словарём в ручку, а не носит его в себе."""
    import pathlib

    page = (pathlib.Path(__file__).resolve().parents[1]
            / 'data/uvicorn/template/admin/dating/index.html'
            ).read_text(encoding='utf-8')

    assert '/api/dating/gallery/tags' in page, 'страница не спрашивает словарь'
    assert 'gallery-tag' in page, 'теги не рисуются выбором'
