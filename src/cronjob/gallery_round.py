"""Задание галереи: обойти Диск и разобрать очередную пачку снимков.

## Зачем заданием, а не кругом воркера

Круги в этом проекте просыпаются раз в минуту и спрашивают «есть ли работа».
Галерее это не подходит: фотографии на Диске меняются раз в недели, а разбор
снимка — разовая трата за файл. Круг, будящийся шестьдесят раз в час ради
«ничего нового», — сторож без предмета.

Поэтому Cronicle, как у уборки логов и трассы workflow у соседа.

## Запуск

Дёргает **сосед**: Cronicle бьёт `POST /cronjob` в его панель, та исполняет
файл по пути. У нас своей ручки `/cronjob` нет, поэтому задание зовёт наш
интерфейс по HTTP — см. `.claude/docs/worker.md`, раздел про Cronicle.

    PYTHONPATH=. python -m src.cronjob.gallery_round

Разумный срок — раз в час. Чаще незачем: Диск не меняется ежеминутно, а пятёрка
снимков за заход — это сознательный предел скорости (`gallery_vision.py`).

## ⚠ Два дела в одном задании, и порядок их не случаен

Сперва обход (что вообще лежит), потом разбор (что на снимках). Наоборот
бессмысленно: разбирать нечего, пока индекс не знает о новых файлах.

## ⚠⚠ Молчит, когда нечего сказать

Доклада «обошли 13 анкет, разобрали 0» не будет: ежечасное «ничего не
изменилось» перестают читать через день, а вместе с ним перестают читать и то
единственное сообщение, ради которого стоило смотреть.
"""

import asyncio

from logging_.logging_ import logger_exception, logger_info
from src.dating.error import error_catch_install  # noqa: E402
from mysql_.mysql_ import mysql_get_db_async, mysql_pool_close

from src.dating.gallery import gallery_folder_name
from src.dating.gallery_index import gallery_index_sync
from src.dating.gallery_vision import GALLERY_VISION_BATCH, gallery_vision_run


async def gallery_round_run(batch: int = GALLERY_VISION_BATCH) -> dict:
    """Один заход: обойти папки включённых анкет, затем разобрать пачку снимков.

    Args:
        batch: сколько снимков отдать модели за этот заход.

    Returns:
        `{'profiles': int, 'found': int, 'added': int, 'gone': int,
          'failed': int, 'vision': {...}}`.

    ⚠ **Отказ по одной анкете не уносит заход.** У одной девушки папки может не
    быть вовсе — это обычное состояние, пока её фотографии не завели, — и
    останавливаться из-за неё значило бы не обойти остальных.
    """
    out = {'profiles': 0, 'found': 0, 'added': 0, 'gone': 0, 'failed': 0}

    for profile in await _profiles():
        got = await gallery_index_sync(profile['id'], gallery_folder_name(profile))
        out['profiles'] += 1
        if not got['ok']:
            out['failed'] += 1
            continue
        for key in ('found', 'added', 'gone'):
            out[key] += got[key]

    out['vision'] = await gallery_vision_run(batch)

    return out


async def main() -> None:
    """Точка входа задания: прогон и строка в журнал, если было что делать."""
    # ⚠ Застава журнала: падение задания прежде видел только `app.log`.
    error_catch_install('cronjob')

    try:
        got = await gallery_round_run()
        # ⚠ Молчим на пустом заходе — см. шапку. Говорим, когда что-то
        # появилось, пропало или разобрано: это и есть новости.
        if got['added'] or got['gone'] or got['vision']['done'] or got['failed']:
            logger_info(f"галерея: анкет {got['profiles']}, файлов {got['found']}, "
                        f"новых {got['added']}, ушло {got['gone']}, "
                        f"разобрано {got['vision']['done']}, "
                        f"неудач {got['failed'] + got['vision']['failed']}")
    except Exception as err:  # noqa: BLE001 — заданию нельзя падать молча
        logger_exception(f'галерея: заход не удался: {err}')
    finally:
        # Пул закрываем сами: задание живёт один прогон, и оставленные
        # соединения копились бы по одному на каждый час.
        await mysql_pool_close()


async def _profiles() -> list[dict]:
    """Анкеты, чьи папки обходим: включённые.

    ⚠ Берём **включённые**, а не «в сети»: фотографии лежат на Диске и от того,
    вошла ли девушка на сайт, не зависят. Привязка к `status` означала бы, что
    галерея спящей анкеты не обновляется никогда.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, login, gallery_folder FROM profile '
                         ' WHERE enabled = 1 ORDER BY id')

        return [dict(row) for row in (await db.fetchall() or [])]


if __name__ == '__main__':
    asyncio.run(main())
