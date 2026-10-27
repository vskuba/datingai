"""Вкладка «Галерея»: группы из путей, порядок наш, ушедшие не видны.

## ⚠⚠ Что здесь проверяется

Три решения, каждое из которых легко сделать «проще» и тем сломать:

| Решение | Чем плохо «проще» |
|---------|-------------------|
| имена групп — из путей | зашитые `public`/`intim` не покажут третью папку, и никто не поймёт почему |
| порядок задаём мы, `public` первой | алфавит поставил бы `intim` выше — не то, что хочет видеть оператор |
| имя папки: выбранное, иначе логин | без запасного правила вкладка пуста у всех, кому папку не выбирали вручную |

Диск здесь не участвует вовсе: читается индекс в базе. Это и есть смысл
`gallery.py` — страница открывается, когда Диск молчит.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.gallery import gallery_folder_name, gallery_view
from src.uvicorn.routers.dating.profile_ import profile_delete

pytestmark = pytest.mark.profile

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда с разложенным индексом. Убирается за собой."""
    login = f'gallery-{uuid.uuid4().hex[:8]}@example.com'
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE, 'login': login, 'password': 'x'})).json()['result'])

    yield {'id': profile_id, 'login': login}

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM gallery_file WHERE profile_id = %s', (profile_id,))
        await db.connection.commit()
    await profile_delete(profile_id, user=None)


async def _put(profile_id: int, rows: list[tuple]) -> None:
    """Разложить индекс: `(file_id, path, kind, is_gone)`."""
    async with mysql_get_db_async() as db:
        for file_id, path, kind, gone in rows:
            await db.execute(
                'INSERT INTO gallery_file (profile_id, file_id, path, name, mime, '
                '                          kind, is_gone) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (profile_id, file_id, path, path.rpartition('/')[2],
                 'image/png' if kind == 'image' else 'video/mp4', kind, gone))
        await db.connection.commit()


async def test_folder_without_a_pick_stays_empty(girl):
    """⚠⚠ Папку не выбрали — значит её нет, а не «подставим логин».

    Правило «папка зовётся логином» было снято с данных 01.10.2026 и к
    27.10.2026 умерло: на рабочем Диске логином не названа ни одна папка из
    двенадцати. Подставляя логин, панель обещала обход папки, которой нет.
    """
    assert gallery_folder_name({'gallery_folder': '', 'login': girl['login']}) == ''


async def test_chosen_folder_is_taken_as_is(girl):
    """Выбранное человеком — единственный источник имени папки."""
    assert gallery_folder_name({'gallery_folder': 'своя-папка',
                                'login': girl['login']}) == 'своя-папка'


async def test_groups_come_from_paths(girl):
    """⚠ Имена групп — из путей, а не из списка в коде: третья папка видна сама."""
    await _put(girl['id'], [
        ('f-1', 'photo/public/a.png', 'image', 0),
        ('f-2', 'photo/intim/b.png', 'image', 0),
        ('f-3', 'photo/придуманная/c.png', 'image', 0),
    ])

    got = await gallery_view(girl['id'])

    assert [g['name'] for g in got['groups']] == ['public', 'intim', 'придуманная']


async def test_public_goes_first(girl):
    """Порядок наш: `public` сверху, прочие по алфавиту — алфавит дал бы иное."""
    await _put(girl['id'], [
        ('f-1', 'photo/intim/b.png', 'image', 0),
        ('f-2', 'photo/public/a.png', 'image', 0),
    ])

    got = await gallery_view(girl['id'])

    assert [g['name'] for g in got['groups']] == ['public', 'intim']


async def test_gone_files_are_not_shown(girl):
    """Ушедший файл не показывается — но из базы не исчезает (его описание дорого)."""
    await _put(girl['id'], [
        ('f-1', 'photo/public/живой.png', 'image', 0),
        ('f-2', 'photo/public/ушедший.png', 'image', 1),
    ])

    got = await gallery_view(girl['id'])

    assert got['total'] == 1
    assert [f['name'] for f in got['groups'][0]['files']] == ['живой.png']

    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM gallery_file WHERE profile_id = %s',
                         (girl['id'],))
        assert int(((await db.fetchone()) or {}).get('n') or 0) == 2, \
            'ушедший обязан остаться строкой'


async def test_video_gets_viewer_link_and_photo_does_not(girl):
    """Полный кадр видео отдаёт Диск ссылкой; у фото её нет — его проксируем сами."""
    await _put(girl['id'], [
        ('f-1', 'photo/public/a.png', 'image', 0),
        ('f-2', 'photo/public/v.mp4', 'video', 0),
    ])

    files = {f['name']: f for f in (await gallery_view(girl['id']))['groups'][0]['files']}

    assert files['a.png']['view_url'] == ''
    assert files['v.mp4']['view_url'].endswith('/f-2/view')


async def test_empty_gallery_has_no_groups(girl):
    """⚠ Пустых групп не бывает: подпись над пустотой читается как поломка."""
    got = await gallery_view(girl['id'])

    assert got['groups'] == []
    assert got['total'] == 0
    assert got['taken_at'] is None


async def test_tags_arrive_as_list(girl):
    """Теги приезжают списком, а не строкой JSON, каким бы ни был драйвер."""
    await _put(girl['id'], [('f-1', 'photo/public/a.png', 'image', 0)])
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE gallery_file SET tags = %s WHERE file_id = 'f-1'",
                         ('["lace", "indoor"]',))
        await db.connection.commit()

    got = await gallery_view(girl['id'])

    assert got['groups'][0]['files'][0]['tags'] == ['lace', 'indoor']
