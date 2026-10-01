"""Разбор снимка моделью: что изображено и какими тегами это назвать.

Описание и теги добываются **один раз на файл** и ложатся в индекс
(`gallery_file`). Повтор — только когда снимок на Диске сменился: контрольная
сумма разошлась с записанной.

**Зрение живёт у соседа**, как и все прочие обращения к модели: панель зовёт
`POST /chat/ask` с workflow `dating_gallery_describe`, а тот — инструмент над
`ai_vision_json`. Своей дороги к модели здесь нет и быть не должно.

⚠⚠ **Правленое человеком не берётся никогда.** Непустой `edited_at` значит
«здесь были руки», и следующая пачка его не тронет. Иначе человек, однажды
увидев, что его правку затёрли, перестал бы править вовсе.

⚠ **За заход не больше пяти снимков.** Предел назван человеком и имеет смысл:
вызов зрения стоит секунд, а разбор — работа фоновая, у неё нет причин
выигрывать гонку у чата.

⚠⚠ **Модели уходит сжатый кадр, а не оригинал.** Снимок приводится к RGB-JPEG
со стороной не больше 1600 (`ai_vision_normalize`): 8,5 МБ PNG становятся 275 КБ.
Заодно снимается чужая грабля — локальные vision-серверы **не читают RGBA-PNG**
и отвечают `400 Failed to load image`, а на Диске лежат именно PNG.
"""

import base64
import json

from ai.vision.ai_vision import ai_vision_normalize
from google_drive_.google_drive_bytes import google_drive_bytes
from json_.json_ import json_from_string
from mysql_.mysql_ import mysql_get_db_async

from src.dating.agent import agent_site
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_agent_find,
                                       rhapsody_api_chat_ask)

# Сколько снимков разбираем за один заход. Решение человека.
GALLERY_VISION_BATCH = 5

# Какой кадр просим у Диска под разбор. Не оригинал: 800 по длинной стороне —
# это ~700 КБ против 8,5 МБ, а модели для описания платья и обстановки большего
# не нужно. Полный кадр существует для человека, а не для неё.
GALLERY_VISION_WIDTH = 800

# Имя workflow у соседа. ⚠ Точное, с приставкой `dating_`: сосед ищет его по
# совпадению имени, и расхождение выглядит как «агент молчит».
GALLERY_VISION_WORKFLOW = 'dating_gallery_describe'

# Что просим у модели. ⚠ **Поля названы прямо здесь**: не назовёшь — модель
# придумает свои, и каждый раз другие, а колонки заполнять будет нечем.
GALLERY_VISION_PROMPT = (
    'Это фотография девушки с сайта знакомств. Опиши её для картотеки агентства.\n'
    'Верни объект JSON с полями:\n'
    '  description — один абзац: кто в кадре, во что одета, где находится, '
    'какое настроение и план съёмки;\n'
    '  tags — список коротких тегов на английском: одежда, место, настроение, '
    'план съёмки, время суток.\n'
    'Нечитаемого и неизвестного не выдумывай.'
)


async def gallery_vision_due(limit: int = GALLERY_VISION_BATCH) -> list[dict]:
    """Снимки, которые пора разобрать: ещё не разобранные либо сменившиеся.

    Args:
        limit: сколько строк вернуть.

    Returns:
        Строки индекса с номером анкеты и файла.

    ⚠ Условие **исключает правленое руками** и ушедшее. Видео тоже не берётся:
    у него разбирать нечего — превью это первый кадр, и описывать по нему
    видеозапись значило бы выдавать догадку за знание.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, profile_id, file_id, name, md5, described_md5 '
            '  FROM gallery_file '
            ' WHERE is_gone = 0 AND kind = %s AND edited_at IS NULL '
            '   AND (described_at IS NULL OR described_md5 <> md5) '
            ' ORDER BY described_at IS NOT NULL, id LIMIT %s', ('image', int(limit)))

        return [dict(row) for row in (await db.fetchall() or [])]


async def gallery_vision_run(limit: int = GALLERY_VISION_BATCH) -> dict:
    """Разобрать очередную пачку снимков. Итог — числами для журнала задания.

    Returns:
        `{'took': int, 'done': int, 'failed': int}`.

    Не бросает: отказ по одному снимку не должен уносить остальную пачку —
    причина ложится в `described_error` и строка остаётся на следующий заход.
    """
    rows = await gallery_vision_due(limit)
    done = failed = 0

    for row in rows:
        try:
            await gallery_vision_one(row)
            done += 1
        except (RuntimeError, RhapsodyError, ValueError) as err:
            await _error_save(row['id'], str(err))
            failed += 1

    return {'took': len(rows), 'done': done, 'failed': failed}


async def gallery_vision_one(row: dict) -> dict:
    """Разобрать один снимок и записать описание с тегами.

    Args:
        row: строка индекса — нужны `id`, `profile_id`, `file_id`, `md5`.

    Returns:
        `{'description': str, 'tags': list}` — то, что записали.

    Raises:
        RuntimeError: Диск не отдал кадр, либо ответ агента не разобрался.
        RhapsodyError: сосед молчит или отказал.

    ⚠ Записывается и `described_md5` — тот снимок, который разобрали. По нему
    и решается, пора ли разбирать заново: имя файла может не меняться годами,
    а содержимое — смениться.
    """
    raw, _ = await google_drive_bytes(row['file_id'], GALLERY_VISION_WIDTH)
    frame = ai_vision_normalize(raw)

    answer = await rhapsody_api_chat_ask(
        await _agent_id(row['profile_id']), GALLERY_VISION_PROMPT,
        {'workflow': GALLERY_VISION_WORKFLOW,
         'image': 'data:image/jpeg;base64,' + base64.b64encode(frame).decode()})

    got = json_from_string(answer)
    if not got:
        raise RuntimeError(f'агент ответил не объектом JSON: {answer[:200]}')

    description = str(got.get('description') or '').strip()
    tags = [str(tag).strip() for tag in (got.get('tags') or []) if str(tag).strip()]
    if not description:
        raise RuntimeError('агент вернул объект без описания')

    await _result_save(row['id'], description, tags, row['md5'])

    return {'description': description, 'tags': tags}


async def _agent_id(profile_id: int) -> int:
    """Агент сайта, которому принадлежит анкета.

    Агент один на сайт, как и у прочих занятий: метка собирается из имени
    сайта-закладки (`agent_site`), и своего агента на каждую девушку здесь не
    заводится.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT site_id FROM profile WHERE id = %s', (profile_id,))
        site_id = int(((await db.fetchone()) or {}).get('site_id') or 0)

    found = await rhapsody_api_agent_find(f'dating_gallery_{await agent_site(site_id)}')
    if not found:
        raise RuntimeError('агент разбора фотографий у соседа не заведён')

    return int(found.get('id') or 0)


async def _result_save(row_id: int, description: str, tags: list, md5: str) -> None:
    """Записать разбор: описание, теги, отметку времени и разобранную сумму."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE gallery_file SET description = %s, tags = CAST(%s AS JSON), '
            '       described_at = NOW(), described_md5 = %s, described_error = %s '
            ' WHERE id = %s',
            (description, json.dumps(tags, ensure_ascii=False), md5, '', row_id))
        await db.connection.commit()


async def _error_save(row_id: int, why: str) -> None:
    """Записать причину неудачи, не трогая прежнее описание.

    ⚠ `described_at` не ставится: строка обязана вернуться в очередь следующим
    заходом. Проставь мы отметку, неудача выглядела бы как разбор.
    """
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE gallery_file SET described_error = %s WHERE id = %s',
                         (why[:500], row_id))
        await db.connection.commit()
