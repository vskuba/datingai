"""Seed SQL: выгрузка выбранных таблиц в файл и обратная заливка.

Зачем он здесь. Схему накатывает миграция (`migrations/0001.sql`), а данные в
свежей базе берутся неоткуда: пользователи панели, учётки анкет, привязки к
сайтам — всё это заводят руками. Seed переносит их между установками одним
файлом, не трогая схему.

Чем отличается от бэкапа (`backup.py`): seed — это **выбранные таблицы** и
`INSERT IGNORE`, то есть его можно применить поверх живой базы, ничего не
затерев. Бэкап — вся база целиком и полная перезапись; путать их дорого.

Что здесь намеренно иначе, чем у RhapsodyAi:

* **выбор таблиц не хранится на сервере.** У соседа он лежит в таблице
  `setting`; заводить её здесь ради одного флажка — платить схемой за галочку.
  Выбор помнит страница (`localStorage`), а сервер каждый раз получает список
  явно, с запросом;
* **имена таблиц сверяются со списком базы.** Они попадают в текст запроса
  (параметром имя таблицы не передать), и брать их из тела запроса на веру
  значило бы отдать наружу выполнение произвольного SQL;
* **выгрузка идёт в отдельном потоке.** В этом же процессе висят чат-сокеты
  анкет, и таблица на сотню тысяч строк, прочитанная в цикле событий, оборвала
  бы их все.

Файлы лежат в `data/seeds` и в репозиторий не попадают: в `dating_girl`
хранятся пароли от учёток на сайтах, и место такому файлу — рядом с базой, а не
в истории git.
"""

import asyncio
import os
import re
from datetime import datetime, timezone

import pymysql
import pymysql.converters
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config.config import config_get
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_conn_get

from src.auth.auth import auth_admin_required

router = APIRouter(prefix='/api/seed', tags=['seed'])

# Куда кладутся файлы. Каталог общий с бэкапами по родителю (`data`), потому что
# он и так примонтирован в контейнер: класть дампы внутрь слоя образа значит
# терять их при первой же пересборке.
SEED_DIR = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')),
    config_get('DATA_DIR', 'data'), 'seeds')

SEED_EXT = '.sql'

# Таблицы, которых в списке нет вовсе. Это состояние миграций (`yoyo`): его
# ведёт сам мигратор, и залитое из чужой установки оно означает «схема уже
# накачена» на базе, где её нет.
SEED_TABLE_HIDDEN = ('_yoyo_log', '_yoyo_migration', '_yoyo_version', 'yoyo_lock')

# Таблицы, которые в списке есть, но по умолчанию не отмечены: их наполняет
# работа панели, а не человек. Переписку и журнал переносят между установками
# редко и осознанно — а вот случайно утащить в seed чужие диалоги легко.
SEED_TABLE_RUNTIME = ('dating_contact', 'dating_log', 'dating_message')

# Имя файла приходит из запроса и уходит в путь. Разрешаем ровно то, что сами и
# порождаем: буквы, цифры, дефис, подчёркивание, точка — и ни одного разделителя
# каталогов.
SEED_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')


class SeedGenerate(BaseModel):
    """Какие таблицы выгружать."""

    tables: list[str] = Field(..., min_length=1, description='Имена таблиц текущей базы')


@router.get('/table')
async def seed_table_list(user=Depends(auth_admin_required)):
    """Таблицы базы, сгруппированные по первому слову имени.

    Группы — не украшение: таблиц немного, но `user*` и `dating*` это разные
    вещи, и выгружают их обычно порознь.

    У каждой таблицы есть `default` — отмечать ли её при первом заходе. Решает
    это сервер, а не страница: какие данные заводят руками, а какие копит сама
    работа панели, известно здесь.
    """
    groups = await asyncio.to_thread(_seed_groups)

    return {'result': groups}


@router.get('/file')
async def seed_file_list(user=Depends(auth_admin_required)):
    """Что уже выгружено. Свежие сверху — по имени, в нём дата."""
    return {'result': _seed_files()}


@router.post('/generate')
async def seed_generate(data: SeedGenerate, user=Depends(auth_admin_required)):
    """Выгружает выбранные таблицы в новый файл.

    Имена сверяются со списком базы до того, как попасть в запрос: чужого имени
    в списке нет, а значит и выполниться из него ничего не может.
    """
    known = {table['name'] for group in await asyncio.to_thread(_seed_groups) for table in group['tables']}
    tables = sorted({str(name).strip() for name in data.tables})

    unknown = [name for name in tables if name not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f'нет таких таблиц: {", ".join(unknown)}')

    os.makedirs(SEED_DIR, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
    name = f'seed_{stamp}{SEED_EXT}'
    path = os.path.join(SEED_DIR, name)

    try:
        await asyncio.to_thread(_seed_write, path, tables)
    except Exception as e:
        if os.path.exists(path):
            os.remove(path)
        logger_info(f'[seed] выгрузка не удалась: {type(e).__name__}: {e}')
        raise HTTPException(status_code=500, detail=f'выгрузка не удалась: {e}')

    logger_info(f'[seed] {user}: выгружен {name} ({", ".join(tables)})')

    return {'result': _seed_file_info(name)}


@router.post('/apply/{name}')
async def seed_apply(name: str, user=Depends(auth_admin_required)):
    """Заливает файл в текущую базу.

    `INSERT IGNORE` внутри файла: строки с занятыми ключами не трогаются, и
    повторное применение ничего не портит.
    """
    path = _seed_path(name)

    try:
        await asyncio.to_thread(_seed_apply, path)
    except Exception as e:
        logger_info(f'[seed] {name} не применился: {type(e).__name__}: {e}')
        raise HTTPException(status_code=500, detail=f'не применился: {e}')

    logger_info(f'[seed] {user}: применён {name}')

    return {'result': f'Seed {name} применён'}


@router.get('/download/{name}')
async def seed_download(name: str, user=Depends(auth_admin_required)):
    """Отдаёт файл целиком. В нём пароли учёток — обращаться как с базой."""
    return FileResponse(_seed_path(name), filename=name, media_type='application/sql')


@router.delete('/{name}')
async def seed_delete(name: str, user=Depends(auth_admin_required)):
    """Удаляет файл. Последний — тоже: это выгрузка, а не единственная копия
    данных, и держать её насильно незачем (в отличие от бэкапа)."""
    os.remove(_seed_path(name))
    logger_info(f'[seed] {user}: удалён {name}')

    return {'result': 'удалено'}


# ── Приватное ────────────────────────────────────────────────────────────────

def _seed_path(name: str) -> str:
    """Путь к файлу по имени из запроса. Чужое имя — 400, отсутствующее — 404."""
    if not SEED_NAME_RE.match(name) or not name.endswith(SEED_EXT):
        raise HTTPException(status_code=400, detail='недопустимое имя файла')

    path = os.path.join(SEED_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='файла нет')

    return path


def _seed_groups() -> list[dict]:
    """Таблицы базы по группам. Читается из схемы, а не из списка в коде: список
    в коде расходится со схемой ровно в тот день, когда о нём забывают."""
    connection = mysql_conn_get()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES '
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
                'ORDER BY TABLE_NAME')
            tables = [str(row['TABLE_NAME']) for row in cursor.fetchall()]
    finally:
        connection.close()

    groups: dict[str, list[dict]] = {}
    for table in tables:
        if table in SEED_TABLE_HIDDEN:
            continue
        groups.setdefault(table.split('_')[0], []).append(
            {'name': table, 'default': table not in SEED_TABLE_RUNTIME})

    return [{'key': key, 'label': key, 'tables': names} for key, names in sorted(groups.items())]


def _seed_files() -> list[dict]:
    """Список выгрузок, свежие сверху."""
    if not os.path.exists(SEED_DIR):
        return []

    return [_seed_file_info(name)
            for name in sorted(os.listdir(SEED_DIR), reverse=True)
            if name.endswith(SEED_EXT)]


def _seed_file_info(name: str) -> dict:
    stat = os.stat(os.path.join(SEED_DIR, name))

    return {
        'name': name,
        'size': stat.st_size,
        'modified': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    }


def _seed_write(path: str, tables: list[str]) -> None:
    """Пишет выгрузку. Вызывается в отдельном потоке — внутри обычный pymysql."""
    connection = mysql_conn_get()
    try:
        parts = [
            '-- Seed SQL (DatingAi)\n'
            f'-- Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")} UTC\n'
            f'-- Tables: {", ".join(tables)}\n\n'
            'SET NAMES utf8mb4;\n'
            'SET FOREIGN_KEY_CHECKS = 0;\n'
        ]
        parts += [_seed_table_sql(connection, table) for table in tables]
        parts.append('\nSET FOREIGN_KEY_CHECKS = 1;\n')

        with open(path, 'w', encoding='utf-8') as file:
            file.write('\n'.join(parts))
    finally:
        connection.close()


def _seed_table_sql(connection, table: str) -> str:
    """Одна таблица одним `INSERT IGNORE`. Пустая — только заголовок."""
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT * FROM `{table}`')
        rows = cursor.fetchall()

    lines = [f'\n-- TABLE: {table}']
    if not rows:
        lines.append('-- (пусто)')

        return '\n'.join(lines)

    columns = list(rows[0].keys())
    values = ['  (' + ', '.join(_seed_value(row[column]) for column in columns) + ')' for row in rows]

    lines.append(f'INSERT IGNORE INTO `{table}` ({", ".join(f"`{c}`" for c in columns)}) VALUES')
    lines.append(',\n'.join(values) + ';')

    return '\n'.join(lines)


def _seed_value(value) -> str:
    """Значение в SQL-литерал. Экранирует драйвер, а не мы: своё экранирование
    рано или поздно не учитывает какой-нибудь символ, и файл рвётся посередине."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "'" + pymysql.converters.escape_string(value.decode('utf-8', errors='replace')) + "'"

    return "'" + pymysql.converters.escape_string(str(value)) + "'"


def _seed_apply(path: str) -> None:
    """Заливает файл. Всё одной транзакцией: половина применённого seed хуже,
    чем неприменённый, — понять, что именно доехало, потом нечем."""
    with open(path, 'r', encoding='utf-8') as file:
        content = file.read()

    connection = mysql_conn_get()
    try:
        with connection.cursor() as cursor:
            for statement in _seed_split(content):
                cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _seed_split(sql: str) -> list[str]:
    """Режет файл на команды по `;`.

    Не `split(';')`: точка с запятой встречается внутри значений — в биографии
    анкеты, в тексте сообщения, — и наивная разрезка рвёт файл посреди строки.
    Поэтому разделитель считается разделителем только вне кавычек.
    """
    statements, buffer, quote = [], [], None
    index = 0

    while index < len(sql):
        char = sql[index]

        if quote and char == '\\':
            buffer.append(char)
            index += 1
            if index < len(sql):
                buffer.append(sql[index])
                index += 1
            continue

        if char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == ';' and quote is None:
            statement = ''.join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    statement = ''.join(buffer).strip()
    if statement:
        statements.append(statement)

    return statements
