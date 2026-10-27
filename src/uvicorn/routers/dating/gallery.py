"""Ручки вкладки «Галерея»: папки списком, плитки из базы, байты превью.

Механика живёт в `src/dating/gallery.py` и `gallery_index.py`, здесь — только
двери: кто вправе, что принимаем, чем отвечаем на отказ.

⚠ **Папку выбирают из списка, а не вписывают.** `PUT /folder` принимает только
имя, которое есть на Диске (`GET /folders`). Свободный ввод возвращал бы
человека к отказу «папки «X» нет» — ровно к тому, ради избавления от которого
выбор и затеян.

⚠⚠ **Превью проксируется, а не отдаётся ссылкой.** `thumbnailLink` требует
наших полномочий и живёт часы: вставленный в `<img src>`, у браузера оператора
он ответит страницей входа Google. Байты берёт сервер, токен наружу не уходит
ни в каком виде.

⚠⚠ **Номер файла проверяется принадлежностью анкете.** Без этого
`/{чужая}/img/{наш_файл}` отдавал бы фотографии одной девушки под номером
другой — и отдавал бы любому вошедшему.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from mysql_.mysql_ import mysql_get_db_async

from google_drive_.google_drive_bytes import (GOOGLE_DRIVE_THUMB_SIZES,
                                              google_drive_bytes)

from src.auth.auth import AuthUser, auth_required
from src.dating.gallery import (gallery_folder_name, gallery_round_when,
                                gallery_view)
from src.dating.gallery_index import (gallery_index_folders, gallery_index_sync)
from src.dating.gallery_vision import (GALLERY_VISION_GROUP_TITLES,
                                       GALLERY_VISION_TAGS,
                                       gallery_vision_tags_keep)
from src.uvicorn.routers.dating.profile_ import profile_row

router = APIRouter(prefix='/api/dating/gallery', tags=['dating'])

# Сколько браузеру держать превью. Час: за это время человек успевает
# пролистать галерею и вернуться, а сменившаяся на Диске фотография приедет под
# новым номером — ключ кэша сменится вместе с ним.
GALLERY_CACHE_SECONDS = 3600

GALLERY_DESCRIPTION_MAX = 4000

# Сколько тегов принимаем в одной правке. ⚠ Это предел **формы**, а не словаря:
# он стоит у `Field`, чтобы чужой запрос не прислал тысячу строк. Какие теги
# вообще бывают, решает словарь (`GALLERY_VISION_TAGS`, их 42).
GALLERY_TAGS_MAX = 40

# ⚠⚠ `GALLERY_TAG_MAX` (длина одного тега) отсюда убран, и это не недосмотр:
# обрезать теги больше нечего. Словарь закрытый, и всё, чего в нём нет,
# отбрасывается целиком (`gallery_vision_tags_keep`) — обрезанный до шестидесяти
# знаков чужой тег остался бы таким же чужим, только неузнаваемым.


class GalleryFolderSave(BaseModel):
    """Выбранная папка девушки. Пусто — вернуться к правилу по умолчанию."""

    folder: str = Field('', max_length=255)


class GalleryFileSave(BaseModel):
    """Описание и теги, правленные человеком."""

    description: str = Field('', max_length=GALLERY_DESCRIPTION_MAX)
    tags: list[str] = Field(default_factory=list, max_length=GALLERY_TAGS_MAX)


@router.get('/tags', include_in_schema=False)
async def gallery_tags(user: AuthUser = Depends(auth_required)):
    """Словарь тегов группами — его показывает окно правки снимка.

    ## ⚠⚠ С сервера, а не своим списком на странице

    Словарь закрытый: тег не из него отбрасывается и при разборе, и при правке
    руками. Держи страница свою копию — она разошлась бы с кодом на первой же
    правке, и человек выбирал бы теги, которые ручка молча выбросит. Та же
    грабля, что уже была с подразделами настроек сайта.

    ⚠ Группы отдаём как есть: они для глаза, и порядок их — порядок словаря.

    ⚠ `name` — **подпись человеку**, а не ключ: ключ латиницей (`clothes`), и
    страница показывает именно подпись. Прежде ключ был русским и работал обоими
    сразу; разводить их пришлось по правилу §1.2, и ответ ручки от этого не
    изменился ни на поле.
    """
    return {'result': {'groups': [
        {'name': GALLERY_VISION_GROUP_TITLES.get(key, key), 'tags': list(tags)}
        for key, tags in GALLERY_VISION_TAGS.items()]}}


@router.get('/folders', include_in_schema=False)
async def gallery_folders(user: AuthUser = Depends(auth_required)):
    """Имена папок внутри общей — список, из которого выбирают папку девушки.

    Отказ Диска отдаём **словами и с кодом 200**: вкладка обязана объяснить,
    что случилось, а не показать пустой селект. Пустой селект человек читает
    как «папок нет», и идёт заводить их заново.

    ⚠ Тем же ответом едет `round` — когда обход возьмёт новые файлы в работу.
    Отдельной ручкой это был бы третий запрос вкладки ради одной строки, а
    вопрос тот же самый: «что сейчас с Диском».

    ⚠⚠ **Срок считается и при отказе Диска.** Заход задания к доступу на Диск
    отношения не имеет, а человек, увидев красную строку, первым делом
    спрашивает «когда оно попробует снова».
    """
    when = await gallery_round_when()
    try:
        return {'result': {'folders': await gallery_index_folders(), 'why': '',
                           'round': when}}
    except RuntimeError as err:
        return {'result': {'folders': [], 'why': str(err), 'round': when}}


@router.get('/{profile_id}', include_in_schema=False)
async def gallery_show(profile_id: int, user: AuthUser = Depends(auth_required)):
    """Плитки анкеты группами — из индекса, без похода на Диск.

    ⚠ Анкеты нет — `404`, а не пустая галерея: «нет анкеты» и «нет фотографий»
    требуют от человека разного.
    """
    profile = await profile_row(profile_id)
    folder = gallery_folder_name(profile)

    return {'result': await gallery_view(profile_id, folder)}


@router.put('/{profile_id}/folder', include_in_schema=False)
async def gallery_folder_put(profile_id: int, payload: GalleryFolderSave,
                             user: AuthUser = Depends(auth_required)):
    """Выбрать папку девушки — **только из тех, что есть на Диске**.

    Пустое значение стирает выбор: дальше работает правило по умолчанию (папка
    зовётся логином анкеты).

    ⚠ Имя сверяется со списком, а не принимается на веру. Иначе опечатка
    сохранялась бы молча и всплывала отказом «папки нет» при следующем обходе —
    то есть там, где её уже не связать с набранным.
    """
    await profile_row(profile_id)
    folder = payload.folder.strip()

    if folder:
        try:
            known = await gallery_index_folders()
        except RuntimeError as err:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=str(err)) from err
        if folder not in known:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f'папки «{folder}» на Диске нет')

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET gallery_folder = %s WHERE id = %s',
                         (folder, profile_id))
        await db.connection.commit()

    return {'result': {'folder': folder}}


@router.post('/{profile_id}/sync', include_in_schema=False)
async def gallery_sync(profile_id: int, user: AuthUser = Depends(auth_required)):
    """Переиндексировать папку прямо сейчас — кнопка «Обновить».

    Отказ словами в теле, а не кодом: причина («не расшарили», «папки нет»)
    должна доехать до человека целиком.
    """
    profile = await profile_row(profile_id)

    return {'result': await gallery_index_sync(profile_id,
                                               gallery_folder_name(profile))}


@router.get('/{profile_id}/img/{file_id}', include_in_schema=False)
async def gallery_image(profile_id: int, file_id: str, w: int = 400,
                        user: AuthUser = Depends(auth_required)):
    """Байты превью — проксируем, потому что ссылка Диска чужому браузеру не отдастся.

    Args:
        w: **длинная сторона** превью из закрытого набора. Произвольная —
            `422`: разнобой размеров рвёт кэш и браузера, и самого Диска.

    ⚠ `file_id` обязан принадлежать **этой** анкете: номер приходит из адреса,
    и без проверки одна галерея отдавала бы снимки другой.
    """
    if w not in GOOGLE_DRIVE_THUMB_SIZES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f'ширина не из набора {GOOGLE_DRIVE_THUMB_SIZES}')

    row = await _file_row(profile_id, file_id)
    if row['kind'] == 'other':
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='у этого файла превью не бывает')

    try:
        # ⚠ Просим **по номеру файла**, а не по сохранённой ссылке: та живёт
        # часы, и хранить её в индексе значило бы отдавать протухшие адреса.
        # Слой умеет оба пути (`GOOGLE_DRIVE_THUMB_BY_ID`).
        raw, kind = await google_drive_bytes(file_id, w)
    except RuntimeError as err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=str(err)) from err

    return Response(content=raw, media_type=kind or 'image/jpeg',
                    headers={'Cache-Control':
                             f'private, max-age={GALLERY_CACHE_SECONDS}'})


@router.put('/{profile_id}/file/{file_id}', include_in_schema=False)
async def gallery_file_put(profile_id: int, file_id: str, payload: GalleryFileSave,
                           user: AuthUser = Depends(auth_required)):
    """Правка описания и тегов человеком.

    ⚠⚠ Проставляет `edited_at`, и с этого мгновения строку **не трогает
    разбор**. Иначе следующая пачка у модели затёрла бы исправленное, и человек,
    однажды это заметив, перестал бы править вовсе.

    ⚠⚠ **Теги сверяются со словарём — тем же, что у разбора.** Форма не
    единственный вход, и чужой тег здесь был бы ровно так же бесполезен, как
    выдуманный моделью: он никогда не совпадёт в отборе, и снимок молча выпадет
    из выборки. Правило одно на оба входа — `gallery_vision_tags_keep`.
    """
    await _file_row(profile_id, file_id)
    tags = gallery_vision_tags_keep(payload.tags)

    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE gallery_file SET description = %s, tags = CAST(%s AS JSON), '
            '       edited_at = NOW(), described_error = %s '
            ' WHERE profile_id = %s AND file_id = %s',
            (payload.description.strip(), _json_tags(tags), '', profile_id, file_id))
        await db.connection.commit()

    return {'result': {'description': payload.description.strip(), 'tags': tags,
                       'edited': True}}


async def _file_row(profile_id: int, file_id: str) -> dict:
    """Строка файла этой анкеты или `404`.

    ⚠ Условие **и по анкете, и по файлу**: номер файла приходит из адреса, и
    проверка одного лишь его существования отдала бы чужую фотографию.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT file_id, name, kind, is_gone FROM gallery_file '
                         ' WHERE profile_id = %s AND file_id = %s',
                         (profile_id, file_id))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='файла нет в галерее этой анкеты')

    return dict(row)


def _json_tags(tags: list) -> str:
    """Теги в вид JSON-столбца."""
    return json.dumps(tags, ensure_ascii=False)
