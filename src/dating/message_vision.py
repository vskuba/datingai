"""Снимок в чате: что на нём — словами для агента и подписью в окне разговора.

Владелец 22.11.2026: «нужно добавить описание изображений, как это сделано на
LadaDate». Там присланный письмом снимок описывает зрение (`mail_in_vision.py`),
описание едет агенту и стоит подписью под превью. В чате Luxee снимки ходят
репликами (`message.media`), и до сих пор агент видел вместо них слово
«[photo]»: Kenneth прислал снимок, а девушка отвечала на текст рядом.

## Откуда берётся описание

* **Наш снимок из галереи** — описание уже лежит у файла (`gallery_file.description`):
  его кладёт отправка (`send_message(photo={…, 'about': …})`), зрение не зовётся.
* **Всё прочее** — его снимки и снимки, отправленные оператором прямо из панели, —
  описывает зрение соседа тем же агентом, что галерея и письма
  (`gallery_vision_agent`). Вопрос свой по направлению: про его снимок — «о чём с
  ним говорить», про её — «что она ему показала».

Описание пишется **в сам элемент** `media` полем `about`; неудача считается там
же полем `about_try`. Отдельных колонок нет: снимков в переписке единицы на
миллион строк, и столбец ради них ничего не дал бы.

## ⚠⚠ Когда описывается

Два места, и оба нужны:

1. **Импорт переписки** (`chat_hold._chat_import_site_once`) — **до** побудки
   круга ответов: свежий снимок описан к тому моменту, как агент сядет отвечать.
2. **Сам ответ** (`answer._waiting_tail`) — страховка: импорт мог упасть, зрение
   отказать с первого раза, круг ответов — проснуться своим тиком раньше.

⚠ Двойного вызова зрения нет: на каждую реплику замок в процессе, а
пришедший вторым видит описание уже лёгшим.

⚠ Картинки Luxee **открыты** (`img.luxee.date`, код 200 без cookie), поэтому
файл берётся обычным GET, без сессии анкеты — в отличие от вложений LadaDate.
"""

import asyncio
import base64
import json

from ai.vision.ai_vision import ai_vision_normalize
from http_.http_pool import http_pool_client
from json_.json_ import json_from_string
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.gallery_vision import gallery_vision_agent
from src.dating.json_column import json_column_list
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_chat_ask

# Сколько реплик со снимком описываем за заход импорта.
MESSAGE_VISION_BATCH = 5

# За сколько дней берём неописанное. ⚠ Окно нужно затем, чтобы запрос шёл по
# индексу времени (`idx_dating_message_live`), а не по миллиону строк; старый
# снимок агенту уже не нужен, а в окне разговора он останется без подписи.
MESSAGE_VISION_DAYS = 14

# Сколько раз пробуем один снимок, прежде чем сдаться — по доводу писем
# (`MAIL_IN_VISION_TRIES`): отказы зрения чаще временные.
MESSAGE_VISION_TRIES = 3

# Те же цифры, что у писем и галереи: крупнее модель не читает лучше.
MESSAGE_VISION_WIDTH = 800
MESSAGE_VISION_TIMEOUT = 10
MESSAGE_VISION_MAX = 600

# Что спрашиваем у зрения — по тому, кто прислал.
MESSAGE_VISION_ASK = {
    'in': ('A man sent this photo in a chat to a woman he is talking to. '
           'Describe it in two or three sentences, in English: what is in the '
           'picture, where it seems to be, what he is doing, what mood it has. '
           'Write it so she can answer him about it naturally. If the photo is '
           'explicit, say so plainly. Do not guess his name or his age.'),
    'out': ('A woman sent this photo of herself in a chat to a man. Describe it '
            'in two or three sentences, in English: what she wears, where she '
            'is, her pose and mood.'),
}

# Метка текста, которой импорт заменяет реплику без подписи
# (`site_import_luxee_text`).
MESSAGE_VISION_BARE = '[photo]'

_LOCKS: dict[int, asyncio.Lock] = {}


class DatingMessageVisionError(RuntimeError):
    """Снимок описать не вышло. Текст годен для журнала."""


def message_vision_line(text: str, media) -> str:
    """Реплика для агента: снимок — словами, прямо в строке.

    Args:
        text: текст реплики, как лёг в `message`.
        media: столбец `message.media` (строкой JSON или списком).

    Returns:
        str: `text [photo: описание]`; реплика из одного снимка — одна скобка
        вместо «[photo]». Описания нет — текст как есть.

    ⚠ Строкой, а не заголовком `=== Attached image ===`, как у писем: в чат
    уезжает хвост из нескольких реплик подряд, и блок под одной из них агент
    приписал бы всему хвосту.
    """
    text = str(text or '').strip()
    abouts = [str(x.get('about') or '').strip() for x in json_column_list(media)
              if isinstance(x, dict)]
    shots = ' '.join(f'[photo: {a}]' for a in abouts if a)

    if not shots:
        return text
    if not text or text == MESSAGE_VISION_BARE:
        return shots

    return f'{text} {shots}'


async def message_vision_due(site_id: int = 0, profile_id: int = 0, uid: str = '',
                             limit: int = MESSAGE_VISION_BATCH) -> list[dict]:
    """Реплики со снимком без описания, свежие первыми.

    Args:
        site_id: сайт (для захода импорта); 0 — не сужать.
        profile_id, uid: пара (для ответа); пусто — не сужать.
        limit: сколько вернуть.

    ⚠ «Без описания» решается **в Python**, а не в SQL: снимков в элементе
    бывает несколько, и условие по каждому на языке JSON MySQL вышло бы
    нечитаемым — а строк со снимками в окне единицы.
    """
    where, args = [], []
    if site_id:
        where.append('p.site_id = %s')
        args.append(int(site_id))
    if profile_id:
        where.append('m.profile_id = %s AND m.sender_uid = %s')
        args.extend([int(profile_id), str(uid)[:32]])

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.id, m.profile_id, m.direction, m.media'
            '  FROM message m JOIN profile p ON p.id = m.profile_id'
            ' WHERE m.created_at > NOW() - INTERVAL %s DAY'
            '   AND m.media IS NOT NULL'
            + ''.join(' AND ' + w for w in where) +
            ' ORDER BY m.created_at DESC, m.id DESC',
            (MESSAGE_VISION_DAYS, *args))
        rows = await db.fetchall() or []

    found = []
    for row in rows:
        items = json_column_list(row.get('media'))
        if any(_wants(x) for x in items):
            found.append({**row, 'media': items})
        if len(found) >= int(limit or MESSAGE_VISION_BATCH):
            break

    return found


async def message_vision_run(site_id: int = 0, profile_id: int = 0, uid: str = '',
                             limit: int = MESSAGE_VISION_BATCH) -> dict:
    """Описать пачку снимков. Неудача одного заход не роняет.

    Returns:
        dict: `{'done': N, 'failed': N}`.
    """
    done = failed = 0

    for row in await message_vision_due(site_id, profile_id, uid, limit):
        try:
            if await message_vision_one(row):
                done += 1
        except Exception as e:
            failed += 1
            logger_info(f'[dating] снимок реплики #{row["id"]} не описался: {e}')

    if done or failed:
        logger_info(f'[dating] снимки чата: описано {done}, не вышло {failed}')

    return {'done': done, 'failed': failed}


async def message_vision_one(row: dict) -> bool:
    """Описать снимки одной реплики и записать. True — что-то описано.

    ⚠ Под замком на реплику: импорт и ответ могут прийти к ней одновременно, и
    пришедший вторым перечитывает строку — описание уже лежит.
    """
    message_id = int(row['id'])
    lock = _LOCKS.setdefault(message_id, asyncio.Lock())

    async with lock:
        try:
            items = await _media_read(message_id)
            if not any(_wants(x) for x in items):
                return False

            ask = MESSAGE_VISION_ASK.get(str(row.get('direction')), MESSAGE_VISION_ASK['in'])
            agent = await gallery_vision_agent(int(row['profile_id']))
            done, why = False, ''

            for item in items:
                if not _wants(item):
                    continue
                try:
                    item['about'] = await _describe(agent, ask, item)
                    item.pop('about_try', None)
                    done = True
                except (DatingMessageVisionError, RhapsodyError, RuntimeError) as e:
                    item['about_try'] = int(item.get('about_try') or 0) + 1
                    why = str(e)

            await _media_save(message_id, items)
        finally:
            _LOCKS.pop(message_id, None)

    if why and not done:
        raise DatingMessageVisionError(why)

    return done


# ── Приватное ────────────────────────────────────────────────────────────────


def _wants(item) -> bool:
    """Элементу нужно описание: адрес есть, описания нет, попытки не кончились."""
    return (isinstance(item, dict) and bool(item.get('src') or item.get('thumb'))
            and not str(item.get('about') or '').strip()
            and int(item.get('about_try') or 0) < MESSAGE_VISION_TRIES)


async def _describe(agent: int, ask: str, item: dict) -> str:
    """Скачать снимок и спросить зрение. Возвращает описание."""
    url = str(item.get('src') or item.get('thumb'))

    async with http_pool_client(timeout=MESSAGE_VISION_TIMEOUT,
                                follow_redirects=True) as client:
        answer = await client.get(url)

    if answer.status_code != 200:
        raise DatingMessageVisionError(f'снимок ответил {answer.status_code}')

    try:
        frame = ai_vision_normalize(answer.content, MESSAGE_VISION_WIDTH)
    except Exception as e:
        raise DatingMessageVisionError(f'снимок не читается: {e}') from e

    said = await rhapsody_api_chat_ask(
        agent, ask, {'image': 'data:image/jpeg;base64,' + base64.b64encode(frame).decode()})

    # ⚠ Тот же разбор, что у писем: workflow зрения отвечает объектом
    # `{description, tags}`, но бывает и прямой текст — он годен.
    got = json_from_string(said)
    about = str((got or {}).get('description') or '').strip() if isinstance(got, dict) else ''
    about = about or str(said or '').strip()
    if not about:
        raise DatingMessageVisionError('зрение вернуло пустое описание')

    return about[:MESSAGE_VISION_MAX]


async def _media_read(message_id: int) -> list:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT media FROM message WHERE id = %s', (int(message_id),))
        return json_column_list(((await db.fetchone()) or {}).get('media'))


async def _media_save(message_id: int, items: list) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE message SET media = %s WHERE id = %s',
                         (json.dumps(items, ensure_ascii=False), int(message_id)))
        await db.connection.commit()
