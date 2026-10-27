"""Индекс файлов галереи: что лежит у девушки на Диске — в нашу таблицу.

Страница галереи читает **базу**, а не Диск. Отсюда и скорость, и то, что
вкладка работает, когда Диск молчит, и главное — место, куда лечь описанию и
тегам от модели: добывать их заново при каждом открытии значило бы платить
моделью за уже известное.

Диск опрашивает этот модуль, его зовёт задание Cronicle и кнопка «Обновить».

**Дверь к Диску — в общем слое** (`py_modules/google_drive_`): доступ к Google
Диску ничего не знает про анкеты и нужен любому проекту. Здесь — только то, что
про анкеты: чья это папка, что считать той же фотографией, что делать с
пропавшей.

⚠⚠ **Запись не трогает `description`, `tags` и `edited_at`.** Индексация знает
про файлы, а не про их смысл. Затирая описание обходом, мы платили бы модели
второй раз за тот же снимок, а правку человека теряли бы совсем — и он,
однажды это заметив, перестал бы править вообще.

⚠⚠ **Пропавший файл метится, а не удаляется.** Пропасть он может от моргания
Диска или снятого доступа, а за строкой стоит вызов модели. Вернулся с тем же
номером — зажигается обратно вместе с описанием.

Устройство доступа и грабли Диска — `py_modules/docs/google_drive.md`.
"""

from config.config import config_get
from google_drive_.google_drive_ import (GOOGLE_DRIVE_FOLDER_MIME,
                                         google_drive_list)
from google_drive_.google_drive_walk import google_drive_folder, google_drive_walk
from mysql_.mysql_ import mysql_get_db_async

from src.dating.error import ERROR_DRIVE, error_clear, error_write

# Общая папка, внутри которой лежат папки девушек. ⚠ Значением годится и имя, и
# **номер** папки, и ссылка на неё: разбирает их общий слой. Чужую папку —
# расшаренную нам, а не нашу — задают номером: имя у неё живёт до первого
# переименования владельцем, а переименование приходит молча, отказом «папки
# нет» посреди работающей галереи.
GALLERY_ROOT_NAME = 'GOOGLE_DRIVE_FOLDER'

# Что берём в индекс. ⚠⚠ **Расширением, а не типом файла**: так это и задано
# человеком, а на живом Диске 27.10.2026 они совпали во всех 1065 файлах
# одиннадцати папок — расхождения нет ни одного. Расширение при этом надёжнее:
# Диск иногда отдаёт снимку `application/octet-stream`, и по типу такой файл
# выпал бы из галереи.
#
# ⚠⚠ **Это единственный список, и он же выключатель видео.** В папках лежат не
# только снимки: 939 .jpg, 4 .png, 1 .jpeg — и рядом 39 .mp4, 34 .mov, 6 .txt,
# .pdf, .download. Галерея показывает **только изображения** — решение
# владельца от 27.10.2026; показ видео в коде остался (плитка со ссылкой в
# просмотрщик Диска, `gallery.py`), и вернуть его — это вернуть сюда строку,
# а не писать заново.
#
# ⚠ Чужое не удаляется, а гаснет: пропавшее из обхода метится `is_gone`, как
# и всё остальное, — за строкой могли стоять описание и правка руками.
GALLERY_INDEX_EXT = ('.jpg', '.jpeg', '.png')

# Фразы отказа. Рядом с проверкой, чтобы не разошлись с тем, что видит человек.
GALLERY_WHY_NO_ROOT = 'Диск не настроен: не задана общая папка'
GALLERY_WHY_NOT_SHARED = ('папку не расшарили на сервисный аккаунт — '
                          'аккаунту не видно ни одного файла')
GALLERY_WHY_NO_ROOT_FOLDER = ('общей папки «{folder}» аккаунту не видно — '
                              'проверьте имя или номер в настройке')
GALLERY_WHY_NO_FOLDER = 'папки «{folder}» в общей папке нет'


async def gallery_index_folders() -> list[str]:
    """Имена папок внутри общей — список, из которого человек выбирает папку девушки.

    Один запрос первого уровня, без обхода вглубь: на вкладке нужен выбор, а не
    содержимое.

    Returns:
        Имена по алфавиту, **без краевых пробелов**. Пустой список значит
        «общая папка пуста», а не «доступа нет» — второе поднимает отказ.

    Raises:
        RuntimeError: Диск не настроен, не расшарен или молчит — словами из
            `GALLERY_WHY_*` и из общего слоя.

    ⚠⚠ **Пробелы срезаются здесь, у источника списка.** Имена папок придумывают
    люди, и лишний пробел у них обычное дело (на живом Диске 27.10.2026 — три
    имени из двенадцати). А выбранное имя сохраняется обрезанным: его обрезает
    и браузер, и сама ручка выбора. Отдай список как есть — выбранная человеком
    папка не совпала бы с собой же при сохранении, и вкладка ответила бы «папки
    «…» на Диске нет» на имя, которое сама же и показала.

    ⚠ На обходе это не сказывается: `google_drive_folder` ищет имя прощая
    пробелы (`docs-common/google_drive.md`, §4).
    """
    root = await gallery_index_root()
    rows = await google_drive_list(f"'{root}' in parents and trashed = false")
    return sorted(row['name'].strip() for row in rows
                  if row['mimeType'] == GOOGLE_DRIVE_FOLDER_MIME)


def gallery_index_takes(name: str) -> bool:
    """Берём ли этот файл Диска в галерею — по расширению имени.

    Args:
        name: имя файла, как его отдал Диск.

    Returns:
        `True` для изображений (`GALLERY_INDEX_EXT`), `False` для всего
        прочего — текста рядом со снимками, PDF, видео.

    ⚠ **Чистая, без похода на Диск и в базу**: то же правило спрашивает и
    обход, и его проверка. Своя копия «а это картинка?» в соседнем месте
    разошлась бы с этой на первой же правке списка.
    """
    return str(name or '').lower().endswith(GALLERY_INDEX_EXT)


async def gallery_index_root() -> str:
    """Номер общей папки на Диске по тому, что задано в окружении.

    Значением годится имя папки, её номер или ссылка на неё — разбирает их
    `google_drive_folder` (`docs-common/google_drive.md`, §4).

    ⚠⚠ **Отличает «не расшарили» от «папки с таким именем нет».** У сервисного
    аккаунта оба случая выглядят пустым ответом, и разница видна только лишним
    вопросом: настроенному доступу видно хоть что-то. Без этой проверки человек
    пошёл бы переименовывать папки, которых аккаунт не видит вовсе.
    """
    name = str(config_get(GALLERY_ROOT_NAME, '') or '')
    if not name:
        raise RuntimeError(GALLERY_WHY_NO_ROOT)

    found = await google_drive_folder(name)
    if found:
        return found[0]['id']

    # Пусто. Спрашиваем, видно ли аккаунту вообще что-нибудь: это и есть
    # признак, различающий два одинаковых с виду отказа.
    if not await google_drive_list('trashed = false'):
        raise RuntimeError(GALLERY_WHY_NOT_SHARED)
    raise RuntimeError(GALLERY_WHY_NO_ROOT_FOLDER.format(folder=name))


async def gallery_index_profile(profile_id: int, folder: str) -> dict:
    """Обойти папку девушки и свести её содержимое в `gallery_file`.

    Args:
        profile_id: чья галерея.
        folder: имя её папки внутри общей; годится и номер, если его вписали в
            колонку руками — выбор на вкладке предлагает только имена.

    Returns:
        `{'found': int, 'added': int, 'gone': int, 'back': int}` — что застали и
        что изменилось. Числа нужны ленте событий и ручке «Обновить».

    Raises:
        RuntimeError: Диск не настроен, не расшарен, молчит, или папки девушки
            нет — словами.

    ⚠ Описание, теги и отметку правки не трогает — см. шапку модуля.

    ⚠⚠ **Папка ищется внутри общей, и номер это правило не обходит**: номер из
    чужого конца Диска за совпадение не считается. Иначе одна описка в поле
    «папка на Диске» утащила бы в галерею анкеты чужие фотографии.
    """
    root = await gallery_index_root()
    found = await google_drive_folder(folder, parent=root)
    if not found:
        raise RuntimeError(GALLERY_WHY_NO_FOLDER.format(folder=folder))

    seen = {item['id']: item for item in await google_drive_walk(found[0]['id'])
            if gallery_index_takes(item.get('name', ''))}

    async with mysql_get_db_async() as db:
        await db.execute('SELECT file_id, is_gone FROM gallery_file '
                         ' WHERE profile_id = %s', (profile_id,))
        known = {row['file_id']: row for row in (await db.fetchall() or [])}

        added = back = 0
        for file_id, item in seen.items():
            was = known.get(file_id)
            if was is None:
                added += 1
            elif was['is_gone']:
                back += 1
            await _row_save(db, profile_id, item)

        # Чего не застали — гасим. ⚠ Именно `UPDATE`, а не `DELETE`: за строкой
        # стоит вызов модели и, возможно, правка руками.
        gone = [fid for fid, row in known.items()
                if fid not in seen and not row['is_gone']]
        if gone:
            marks = ', '.join(['%s'] * len(gone))
            await db.execute(f'UPDATE gallery_file SET is_gone = 1 '
                             f' WHERE profile_id = %s AND file_id IN ({marks})',
                             (profile_id, *gone))
        await db.connection.commit()

    return {'found': len(seen), 'added': added, 'gone': len(gone), 'back': back}


async def gallery_index_sync(profile_id: int, folder: str) -> dict:
    """То же, что `gallery_index_profile`, но отказ — словами в ответе, а не броском.

    Для ручки и задания: им нужно не исключение, а «что показать человеку».

    ⚠⚠ **В «Журналы → Ошибки» идёт не всякий отказ, а только тот, что не про
    анкету** — правило журнала отказов (`src/dating/error.py`). «Папки этой
    девушки на Диске нет» — про анкету и состояние обычное: фотографии ей
    просто ещё не завели. Пиши мы и его, один заход задания по полусотне анкет
    клал бы в журнал полсотни строк подряд, затирая друг друга, — а настоящий
    отказ источника («не расшарили», «Диск молчит», «нет ключа») в этом шуме
    было бы не разглядеть.

    Проверено на живом заходе: 53 анкеты, папка на Диске одна — 52 отказа
    «папки нет» и один успех.
    """
    try:
        got = await gallery_index_profile(profile_id, folder)
    except RuntimeError as err:
        if _about_source(str(err)):
            await error_write(ERROR_DRIVE, str(err))

        return {'ok': False, 'why': str(err)}

    await error_clear(ERROR_DRIVE)

    return {'ok': True, 'why': '', **got}


def _about_source(why: str) -> bool:
    """Отказ про **источник**, а не про конкретную анкету?

    Различие решает, попадёт ли строка в журнал отказов. Считаем по тому, чего
    в причине **нет**: всё, кроме «папки такой-то нет», — беда общая (ключ,
    доступ, молчание Диска), и о ней человеку надо знать один раз и внятно.

    ⚠ Признаком служит сама фраза, а не класс исключения: фразы заданы
    константами рядом, и проверка здесь ломается вместе с ними — то есть
    заметно, а не молча.
    """
    return not why.startswith(GALLERY_WHY_NO_FOLDER.split('{')[0])


async def _row_save(db, profile_id: int, item: dict) -> None:
    """Завести или обновить строку файла, не касаясь его смысла.

    ⚠ Ключ — `file_id`: файл, перенесённый человеком из `public` в `intim`,
    остаётся тем же файлом и описание переезжает с ним. Будь ключом путь,
    перенос выглядел бы как «старый пропал, новый появился», и за описание мы
    заплатили бы модели второй раз.

    ⚠ `is_gone = 0` в обновлении — это возвращение пропавшего: строка со всем
    накопленным зажигается обратно.
    """
    meta = item.get('imageMediaMetadata') or item.get('videoMediaMetadata') or {}
    values = (
        profile_id, item['id'], item.get('path', ''), item.get('name', ''),
        item.get('mimeType', ''), _kind(item.get('mimeType', '')),
        int(item.get('size') or 0), item.get('md5Checksum', ''),
        int(meta.get('width') or 0), int(meta.get('height') or 0),
    )
    # ⚠ Форма с алиасом (`AS fresh`), а не `VALUES(col)`: MySQL 8.0.20 объявил
    # вторую устаревшей и предупреждает на **каждой** строке. Соседний код
    # проекта пока пишет по-старому, и это терпимо там, где вставка одиночная;
    # здесь обход кладёт сотни строк за заход, и журнал утонул бы в
    # предупреждениях. Остальные места переведём, когда их и так будут править.
    await db.execute(
        'INSERT INTO gallery_file '
        '       (profile_id, file_id, path, name, mime, kind, size, md5, width, height) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS fresh '
        'ON DUPLICATE KEY UPDATE '
        '  path = fresh.path, name = fresh.name, mime = fresh.mime, '
        '  kind = fresh.kind, size = fresh.size, md5 = fresh.md5, '
        '  width = fresh.width, height = fresh.height, is_gone = 0',
        values)


def _kind(mime: str) -> str:
    """Род файла для плитки: показываем фото и видео, прочее только считаем."""
    if mime.startswith('image/'):
        return 'image'
    return 'video' if mime.startswith('video/') else 'other'
