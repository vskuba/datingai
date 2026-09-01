"""Настройки сайта на нашей стороне: ключ → значение по номеру у соседа.

Сам сайт живёт в RhapsodyAi — там его имя, адрес, метки и `presence`. Здесь только
то, что относится к **нашей** работе с ним и у соседа хранить негде.

## ⚠ Это не копия списка сайтов

Правило проекта в силе: своей таблицы сайтов здесь нет (`CLAUDE.md`). Имени и
адреса в `dating_site` нет — они приходят от соседа при каждом показе. Хранится
только номер как ключ и наши значения; вторая копия имени разошлась бы с первой в
тот день, когда сайт переименуют.

Отсюда форма ответа: ручка складывает **список соседа** и **наши настройки**, а не
отдаёт одно вместо другого. Сайт без сохранённых настроек в списке всё равно есть —
с пустым набором.

## Почему JSON, а не колонка на настройку

Настройки сайта только заводятся, и какие понадобятся, сейчас не знает никто.
Колонка под каждую означала бы миграцию на каждую. Когда список устоится, частые
переедут в свои колонки — тогда будет что переносить.

## Права

Правка — только администратору: настройка сайта меняет работу всех анкет на нём, а
не того, кто её открыл. Чтение открыто всем вошедшим: оператору полезно видеть, с
чем он работает, и прятать это значило бы звать админа на каждый вопрос.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_admin_required, auth_required
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_list

router = APIRouter(prefix='/api/dating/site', tags=['dating'])

# Длина ключа настройки. Та же, что у настроек панели (`setting.key`): человек
# читает их одними глазами, и разные пределы в двух местах пришлось бы помнить.
DATING_SITE_KEY_MAX = 50


class DatingSiteSave(BaseModel):
    """Настройки сайта целиком: страница шлёт весь набор, а не по одной."""

    settings: dict[str, str] = Field(default_factory=dict)


@router.get('', include_in_schema=False)
async def dating_site_list(user: AuthUser = Depends(auth_required)):
    """Сайты соседа вместе с нашими настройками.

    Returns:
        dict: `sites` — записи `id`, `name`, `url`, `settings`.

    Raises:
        HTTPException: 502 — сосед не ответил.

    Список ведёт **сосед**: сайт, которого у него нет, показывать незачем, даже
    если наши настройки для него в базе остались. Обратное тоже верно — сайт без
    настроек виден с пустым набором, иначе его нельзя было бы настроить в первый раз.
    """
    try:
        sites = await rhapsody_api_site_list()
    except RhapsodyError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    saved = await _settings_all()

    return {'result': {'sites': [{'id': int(site.get('id') or 0),
                                  'name': str(site.get('name') or ''),
                                  'url': str(site.get('url') or ''),
                                  'settings': saved.get(int(site.get('id') or 0), {})}
                                 for site in sites]}}


@router.get('/{site_id}', include_in_schema=False)
async def dating_site_get(site_id: int, user: AuthUser = Depends(auth_required)):
    """Настройки одного сайта. Нет строки — пустой набор, а не 404.

    Сайт, который ещё не настраивали, — законное состояние, а не ошибка: строка
    появляется при первом сохранении.
    """
    return {'result': {'site_id': site_id,
                       'settings': (await _settings_all()).get(site_id, {})}}


@router.put('/{site_id}', include_in_schema=False)
async def dating_site_save(site_id: int, payload: DatingSiteSave,
                           user: AuthUser = Depends(auth_admin_required)):
    """Переписывает настройки сайта целиком.

    ⚠ **Целиком, а не по ключу.** Страница держит весь набор перед глазами и шлёт
    то, что на ней; правка по одному ключу означала бы, что удаление — отдельная
    ручка, а порядок двух правок подряд начинает значить.

    Raises:
        HTTPException: 422 — пустой или слишком длинный ключ.
    """
    clean = {}
    for key, value in (payload.settings or {}).items():
        key = str(key).strip()
        if not key:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail='ключ не может быть пустым')
        if len(key) > DATING_SITE_KEY_MAX:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f'ключ длиннее {DATING_SITE_KEY_MAX} знаков: «{key[:60]}…»')

        # Пустое значение выбрасывается, а не хранится: «настройки нет» и
        # «настройка пустая» — разные вещи, и путать их значит получать
        # необъяснимое поведение там, где умолчание кода было осмысленным. То же
        # правило, что у настроек панели (`api.md`, §6.2).
        if str(value) != '':
            clean[key] = str(value)

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_site (site_id, settings) VALUES (%s, %s)'
            ' ON DUPLICATE KEY UPDATE settings = VALUES(settings)',
            (int(site_id), json.dumps(clean, ensure_ascii=False)))
        await db.connection.commit()

    return {'result': {'site_id': site_id, 'settings': clean}}


async def dating_site_setting(site_id: int, key: str, default: str = '') -> str:
    """Одна настройка сайта — для кода, которому она понадобится.

    Отдельной функцией, а не запросом на месте: настройки читают из разных мест, и
    разбор JSON, размазанный по коду, разойдётся в мелочах — например, в том, что
    делать с отсутствующей строкой.
    """
    return (await _settings_all()).get(int(site_id), {}).get(str(key), default)


# ── Приватное ────────────────────────────────────────────────────────────────

async def _settings_all() -> dict[int, dict]:
    """Все сохранённые настройки: номер сайта → набор.

    Одним запросом, а не по сайту: строк здесь единицы (сайтов у соседа десятки), и
    запрос на каждый сайт при показе списка означал бы десяток запросов ради
    десятка строк.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT site_id, settings FROM dating_site')
        rows = await db.fetchall()

    found = {}
    for row in rows:
        raw = row['settings']
        try:
            # MySQL отдаёт JSON строкой, а не разобранным словарём — разбираем сами.
            # Битую строку пропускаем: настройка одного сайта не должна ронять
            # список всех.
            value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except ValueError as e:
            logger_info(f"[dating] настройки сайта #{row['site_id']} не разобрались: {e}")
            continue

        found[int(row['site_id'])] = value if isinstance(value, dict) else {}

    return found
