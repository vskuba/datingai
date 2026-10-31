"""Снимок, присланный собеседником: что на нём — словами для агента.

Собеседник присылает фотографию письмом, а агент её не видит: в промпт уезжает
один текст. Отвечать на «вот я на рыбалке» словами «спасибо за письмо» — ровно
то, по чему человек узнаёт автомат. Здесь снимок превращается в строку
описания, и она едет агенту вместе с письмом.

## Что происходит

1. Берём письмо, у которого есть вложение и нет описания.
2. Качаем файл **под сессией анкеты-получательницы** (`site_session.py`) —
   иначе сайт отвечает 404 даже на правильный адрес.
3. Приводим к сжатому JPEG и отдаём зрению у соседа.
4. Пишем описание в `mail_in.attachment_about`.

## ⚠⚠ Почему не внутри забора писем

Забор ходит по всем анкетам сайта и держит круг; вызов зрения стоит секунд, а
письмо с вложением — редкость (одно из двадцати четырёх на проде). Описывай мы
внутри забора, каждое такое письмо задерживало бы разбор всей почты сайта.

⚠ Гонки при этом нет: описание нужно **кругу ответов**, а он сочиняет ответ по
своему сроку `reply_every` — к тому времени описание успевает лечь.

## ⚠ Чего здесь нет

**Своей дороги к модели.** Зрение живёт у соседа и зовётся тем же агентом, что
разбирает галерею: это один и тот же вопрос «что на снимке», и второй агент на
сайт отличался бы от первого лишь тем, что о нём забыли бы при правке промпта.

⚠ Тегов тоже нет: по ним отбирают **наши** снимки для отправки, а присланный
отбирать не из чего — нужен текст, который прочтёт агент.
"""

import base64

from ai.vision.ai_vision import ai_vision_normalize
from json_.json_ import json_from_string
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.gallery_vision import gallery_vision_agent
from src.dating.site_session import DatingSiteSessionError, site_session_get
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_chat_ask

# Сколько снимков описываем за заход. Столько же, сколько у галереи: вызов
# зрения стоит секунд, а занятие фоновое — у него нет причин выигрывать гонку
# у чата.
MAIL_IN_VISION_BATCH = 5

# До скольких пикселей по длинной стороне сжимаем кадр перед отправкой модели.
# ⚠ Та же цифра, что у галереи: крупнее модель не читает лучше, а трафик растёт
# вчетверо. ⚠⚠ Приведение к RGB-JPEG обязательно не ради веса: локальные
# vision-серверы не читают RGBA-PNG вовсе и отвечают `400 Failed to load image`.
MAIL_IN_VISION_WIDTH = 800

# Сколько ждём файл с сайта, секунд. Картинка, а не страница: больше значило бы
# держать весь заход на одном молчащем файле.
MAIL_IN_VISION_TIMEOUT = 10

# Сколько знаков описания храним. ⚠ Предел есть затем, что текст уезжает в
# промпт агента: простыня на полстраницы отняла бы место у самого письма.
MAIL_IN_VISION_MAX = 600

# Что спрашиваем у зрения.
#
# ⚠⚠ Вопрос **свой, а не галерейный**. Там снимок описывают для каталога
# агентства — чтобы отобрать по приметам; здесь его описывают для разговора:
# агенту надо знать, что человек показал и о чём с ним говорить. Промпт при
# этом остаётся у соседа (умолчание переменной `prompt`), а отсюда едет только
# вопрос — тем же правилом, что у галереи.
MAIL_IN_VISION_ASK = (
    'A man sent this photo to a woman he is writing to. Describe it in two or '
    'three sentences, in English: what is in the picture, where it seems to be, '
    'what he is doing, what mood it has. Write it so she can answer him about '
    'it naturally. Do not guess his name or his age.')


class DatingMailInVisionError(RuntimeError):
    """Снимок описать не вышло. Текст годен для журнала."""


async def mail_in_vision_due(limit: int = MAIL_IN_VISION_BATCH) -> list:
    """Письма со снимком, который ещё не описан.

    Args:
        limit: сколько строк вернуть. Ноль — `MAIL_IN_VISION_BATCH`.

    Returns:
        list: строки `mail_in` вместе с сессией анкеты — тем, чем качать файл.

    ⚠ Порядок — **свежие первыми**: описание нужно кругу ответов, а он отвечает
    на новое письмо. Старое, до которого руки не дошли, подождёт.

    ⚠ Берём только анкеты с сессией: без неё файл не забрать, и такая строка
    заняла бы место в пачке впустую.
    """
    want = int(limit or 0) or MAIL_IN_VISION_BATCH

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT i.id, i.profile_id, i.site_id, i.uid, i.attachment,'
            '       p.login, p.storage_state'
            '  FROM mail_in i JOIN profile p ON p.id = i.profile_id'
            " WHERE i.attachment <> '' AND i.attachment_at IS NULL"
            "   AND p.storage_state IS NOT NULL AND p.storage_state <> ''"
            ' ORDER BY i.date_post DESC, i.id DESC LIMIT %s', (want,))

        return list(await db.fetchall() or [])


async def mail_in_vision_run(limit: int = MAIL_IN_VISION_BATCH) -> dict:
    """Описать пачку присланных снимков.

    Args:
        limit: сколько за заход.

    Returns:
        dict: `{'done': сколько описано, 'failed': сколько не вышло}`.

    ⚠ Неудача одного снимка заход не роняет: сайт мог убрать файл, модель —
    отказать. Строка остаётся неописанной и попадёт в следующую пачку.
    """
    done = failed = 0

    for row in await mail_in_vision_due(limit):
        try:
            await mail_in_vision_one(row)
            done += 1
        except (DatingMailInVisionError, DatingSiteSessionError,
                RhapsodyError, RuntimeError) as e:
            failed += 1
            logger_info(f'[dating] снимок письма #{row["id"]} не описался: {e}')

    if done or failed:
        logger_info(f'[dating] присланные снимки: описано {done}, '
                    f'не вышло {failed}')

    return {'done': done, 'failed': failed}


async def mail_in_vision_one(row: dict) -> str:
    """Описать один снимок и записать описание. Возвращает текст.

    Args:
        row: строка из `mail_in_vision_due`.

    Raises:
        DatingMailInVisionError: файл не забрался, не читается или зрение
            вернуло пустоту.
        RhapsodyError: сосед молчит или отказал.
    """
    frame = await mail_in_vision_frame(row)

    answer = await rhapsody_api_chat_ask(
        await gallery_vision_agent(int(row['profile_id'])), MAIL_IN_VISION_ASK,
        {'image': 'data:image/jpeg;base64,' + base64.b64encode(frame).decode()})

    # ⚠ Зрение у соседа отвечает объектом JSON (`description`, `tags`) — тем
    # же, что и галерее: workflow один. Берём из него описание, а не весь
    # ответ: теги здесь не нужны, а сырой JSON агент прочёл бы как данные, а не
    # как рассказ о снимке.
    got = json_from_string(answer)
    about = str((got or {}).get('description') or '').strip()

    if not about:
        # ⚠ Ответ без объекта — не всегда брак: модель иногда отвечает прямо
        # текстом. Он годен, и терять его из-за формы было бы расточительно.
        about = str(answer or '').strip()

    if not about:
        raise DatingMailInVisionError('зрение вернуло пустое описание')

    about = about[:MAIL_IN_VISION_MAX]
    await _about_save(int(row['id']), about)

    return about


async def mail_in_vision_frame(row: dict) -> bytes:
    """Скачать вложение и привести к сжатому JPEG.

    Args:
        row: строка с `attachment` и сессией анкеты (`storage_state`).

    Returns:
        bytes: готовый кадр для модели.

    Raises:
        DatingMailInVisionError: сайт не отдал файл либо это не изображение.
        DatingSiteSessionError: у анкеты нет живой сессии.

    ⚠⚠ Качаем **под сессией анкеты**, и это обязательно: файлы вложений закрыты,
    тот же адрес без cookie отвечает `404`. Сессия уже есть — её держит
    присутствие, и второго входа ради картинки делать не надо.
    """
    url = str(row.get('attachment') or '').strip()
    if not url:
        raise DatingMailInVisionError('у письма нет адреса вложения')

    answer = await site_session_get(row, url, MAIL_IN_VISION_TIMEOUT)

    if answer.status_code != 200:
        raise DatingMailInVisionError(
            f'сайт ответил {answer.status_code} на вложение')

    try:
        return ai_vision_normalize(answer.content, MAIL_IN_VISION_WIDTH)
    except Exception as e:
        raise DatingMailInVisionError(
            f'вложение не читается как изображение: {e}') from e


# ── Приватное ────────────────────────────────────────────────────────────────


async def _about_save(mail_in_id: int, about: str) -> None:
    """Записать описание и отметку времени.

    ⚠ Отметка ставится **явно**, а не через `updated_at`: повторная неудача
    пишет то же самое, MySQL видит совпадение и строку не трогает — письмо
    вечно оставалось бы неописанным. Та же грабля, что у галереи.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE mail_in SET attachment_about = %s, attachment_at = NOW()'
            ' WHERE id = %s', (str(about or ''), int(mail_in_id)))
        await db.connection.commit()
