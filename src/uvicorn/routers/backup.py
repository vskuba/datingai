"""Бэкап базы: полный дамп, скачивание и восстановление.

Отличие от seed (`seed.py`) не в размере, а в назначении. Seed переносит между
установками то, что завели руками, и ложится поверх живой базы. Бэкап — снимок
**всей** базы на случай аварии, и восстановление его **перезаписывает** всё, что
есть сейчас. Поэтому и разные страницы: перепутать их — потерять день работы.

Снимает и заливает дамп **общий слой** (`py_modules/mysql_/backup_engine.py`):
`mysqldump` из образа, а при его отсутствии — запасной путь на самом pymysql, без
триггеров и процедур. Знать про запасной путь надо до аварии, а не после, поэтому
он назван здесь, хотя живёт не здесь. Здесь остаётся то, что про **нашу**
установку: где лежат файлы, какое имя допустимо, кому можно нажимать.

⚠ Своей копии этой механики тут больше нет, и это важнее, чем экономия строк:
копия жила ещё в PersonaAI и в самом слое — три исполнения одного дампа, которые
расходились бы молча.

Файлы лежат в `data/backups` и в репозиторий не попадают: в дампе вся переписка
и пароли учёток.
"""

import asyncio
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from config.config import config_get
from logging_.logging_ import logger_info
from mysql_.backup_engine import backup_engine_dump, backup_engine_restore
from mysql_.mysql_ import db, host, password, port, user

from src.auth.auth import auth_admin_required

router = APIRouter(prefix='/api/backup', tags=['backup'])

BACKUP_DIR = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')),
    config_get('DATA_DIR', 'data'), 'backups')

BACKUP_EXT = '.sql.gz'

BACKUP_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')


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
        await asyncio.to_thread(backup_engine_dump, path, host, port, user,
                                password, db)
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
        await asyncio.to_thread(backup_engine_restore, path, host, port, user,
                                password, db)
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
