"""Бэкап базы: полный дамп, скачивание и восстановление.

Отличие от seed (`seed.py`) не в размере, а в назначении. Seed переносит между
установками то, что завели руками, и ложится поверх живой базы. Бэкап — снимок
**всей** базы на случай аварии, и восстановление его **перезаписывает** всё, что
есть сейчас. Поэтому и разные страницы: перепутать их — потерять день работы.

Дамп снимает `mysqldump` из образа (`default-mysql-client` стоит в
`docker/uvicorn/dockerfile`). Если утилиты вдруг нет, есть запасной путь на
самом pymysql — он беднее (не переносит триггеры и процедуры), и знать об этом
надо до аварии, а не после.

Пароль уходит в утилиту через `MYSQL_PWD`, а не аргументом: аргументы видны в
`ps` любому, кто зашёл в контейнер.

Файлы лежат в `data/backups` и в репозиторий не попадают: в дампе вся переписка
и пароли учёток.
"""

import asyncio
import gzip
import io
import os
import re
import subprocess
from datetime import datetime, timezone

import pymysql
import pymysql.converters
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from config.config import config_get
from logging_.logging_ import logger_info
from mysql_.mysql_ import db, host, mysql_conn_get, password, port, user

from src.auth.auth import auth_admin_required

router = APIRouter(prefix='/api/backup', tags=['backup'])

BACKUP_DIR = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')),
    config_get('DATA_DIR', 'data'), 'backups')

BACKUP_EXT = '.sql.gz'

BACKUP_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')

# Снять дамп быстрее, чем залить его обратно: заливка проигрывает весь файл
# построчно и на живой базе идёт в разы дольше. Отсюда и разные потолки.
BACKUP_DUMP_TIMEOUT = 300
BACKUP_RESTORE_TIMEOUT = 600


@router.get('/file')
async def backup_file_list(user_=Depends(auth_admin_required)):
    """Что уже снято. Свежие сверху — по имени, в нём дата."""
    return {'result': _backup_files()}


@router.post('/create')
async def backup_create(user_=Depends(auth_admin_required)):
    """Снимает дамп всей базы и кладёт его сжатым в `data/backups`."""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
    name = f'backup_{stamp}{BACKUP_EXT}'
    path = os.path.join(BACKUP_DIR, name)

    try:
        await asyncio.to_thread(_backup_dump, path)
    except Exception as e:
        # Недописанный файл убираем: в списке он неотличим от целого, а
        # восстановиться из него нельзя — выяснится это в худший момент.
        if os.path.exists(path):
            os.remove(path)
        logger_info(f'[backup] дамп не снялся: {type(e).__name__}: {e}')
        raise HTTPException(status_code=500, detail=f'дамп не снялся: {e}')

    logger_info(f'[backup] {user_}: снят {name}')

    return {'result': _backup_file_info(name)}


@router.post('/restore/{name}')
async def backup_restore(name: str, user_=Depends(auth_admin_required)):
    """Заливает дамп обратно. **Перезаписывает базу целиком.**"""
    path = _backup_path(name)

    try:
        await asyncio.to_thread(_backup_restore, path)
    except Exception as e:
        logger_info(f'[backup] {name} не восстановился: {type(e).__name__}: {e}')
        raise HTTPException(status_code=500, detail=f'не восстановился: {e}')

    logger_info(f'[backup] {user_}: восстановлен {name}')

    return {'result': f'Бэкап {name} восстановлен'}


@router.get('/download/{name}')
async def backup_download(name: str, user_=Depends(auth_admin_required)):
    """Отдаёт файл целиком. Скачанный дамп — та же база: не в общие каталоги."""
    return FileResponse(_backup_path(name), filename=name, media_type='application/gzip')


@router.delete('/{name}')
async def backup_delete(name: str, user_=Depends(auth_admin_required)):
    """Удаляет дамп. Последний оставшийся удалить нельзя.

    Это не забота о порядке в списке: «почистил старьё» и «остался без единой
    копии» выглядят одинаково ровно до того дня, когда копия понадобится.
    """
    path = _backup_path(name)

    if len(_backup_files()) < 2:
        raise HTTPException(status_code=409, detail='это единственный бэкап — снимите новый до удаления')

    os.remove(path)
    logger_info(f'[backup] {user_}: удалён {name}')

    return {'result': 'удалено'}


# ── Приватное ────────────────────────────────────────────────────────────────

def _backup_path(name: str) -> str:
    """Путь к файлу по имени из запроса. Чужое имя — 400, отсутствующее — 404."""
    if not BACKUP_NAME_RE.match(name) or not name.endswith(BACKUP_EXT):
        raise HTTPException(status_code=400, detail='недопустимое имя файла')

    path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail='файла нет')

    return path


def _backup_files() -> list[dict]:
    """Список дампов, свежие сверху."""
    if not os.path.exists(BACKUP_DIR):
        return []

    return [_backup_file_info(name)
            for name in sorted(os.listdir(BACKUP_DIR), reverse=True)
            if name.endswith(BACKUP_EXT)]


def _backup_file_info(name: str) -> dict:
    stat = os.stat(os.path.join(BACKUP_DIR, name))

    return {
        'name': name,
        'size': stat.st_size,
        'modified': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
    }


def _backup_dump(path: str) -> None:
    """Снимает дамп: сначала штатной утилитой, при её отсутствии — драйвером."""
    try:
        _backup_dump_mysqldump(path)
    except FileNotFoundError:
        logger_info('[backup] mysqldump не найден — снимаем драйвером, без триггеров и процедур')
        _backup_dump_pymysql(path)


def _backup_dump_mysqldump(path: str) -> None:
    """Штатный путь. `--single-transaction` — чтобы снимок был согласованным и
    не запирал таблицы: панель в это время продолжает работать."""
    env = os.environ.copy()
    env['MYSQL_PWD'] = password

    result = subprocess.run(
        ['mysqldump', f'--host={host}', f'--port={port}', f'--user={user}',
         '--single-transaction', '--routines', '--triggers',
         '--default-character-set=utf8mb4', db],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=BACKUP_DUMP_TIMEOUT)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip())

    with gzip.open(path, 'wb', compresslevel=6) as archive:
        archive.write(result.stdout)


def _backup_dump_pymysql(path: str) -> None:
    """Запасной путь: DDL и строки таблица за таблицей. Триггеров и процедур в
    нём не будет — в этой базе их нет, но полагаться на это в общем случае
    нельзя."""
    connection = mysql_conn_get()
    try:
        buffer = io.StringIO()
        buffer.write('-- Backup (pymysql fallback)\n')
        buffer.write(f'-- Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")} UTC\n')
        buffer.write(f'-- Database: {db}\n\n')
        buffer.write('SET NAMES utf8mb4;\n')
        buffer.write('SET FOREIGN_KEY_CHECKS = 0;\n\n')

        with connection.cursor() as cursor:
            cursor.execute('SHOW TABLES')
            tables = [list(row.values())[0] for row in cursor.fetchall()]

        for table in tables:
            with connection.cursor() as cursor:
                cursor.execute(f'SHOW CREATE TABLE `{table}`')
                create = list(cursor.fetchone().values())[1]

            buffer.write(f'-- TABLE: {table}\n')
            buffer.write(f'DROP TABLE IF EXISTS `{table}`;\n')
            buffer.write(create + ';\n\n')

            with connection.cursor() as cursor:
                cursor.execute(f'SELECT * FROM `{table}`')
                rows = cursor.fetchall()

            if not rows:
                continue

            columns = list(rows[0].keys())
            values = ['  (' + ', '.join(_backup_value(row[c]) for c in columns) + ')' for row in rows]
            buffer.write(f'INSERT INTO `{table}` ({", ".join(f"`{c}`" for c in columns)}) VALUES\n')
            buffer.write(',\n'.join(values) + ';\n\n')

        buffer.write('SET FOREIGN_KEY_CHECKS = 1;\n')

        with gzip.open(path, 'wb', compresslevel=6) as archive:
            archive.write(buffer.getvalue().encode('utf-8'))
    finally:
        connection.close()


def _backup_value(value) -> str:
    """Значение в SQL-литерал. Экранирует драйвер — см. `seed.py`."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "'" + pymysql.converters.escape_string(value.decode('utf-8', errors='replace')) + "'"

    return "'" + pymysql.converters.escape_string(str(value)) + "'"


def _backup_restore(path: str) -> None:
    """Распаковывает и подаёт клиенту MySQL на вход.

    Клиент обязателен: без него дамп снимается, а восстановиться из него нечем —
    и обнаруживается это ровно тогда, когда восстановление и требуется.
    """
    with gzip.open(path, 'rb') as archive:
        sql = archive.read()

    env = os.environ.copy()
    env['MYSQL_PWD'] = password

    try:
        result = subprocess.run(
            ['mysql', f'--host={host}', f'--port={port}', f'--user={user}',
             '--default-character-set=utf8mb4', db],
            input=sql, stderr=subprocess.PIPE, env=env, timeout=BACKUP_RESTORE_TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError('клиента mysql нет в образе — залить дамп нечем')

    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip())
