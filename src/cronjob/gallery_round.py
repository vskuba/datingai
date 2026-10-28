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
from src.dating.gallery_vision import gallery_vision_run
from src.dating.round import round_sites, round_sites_run
from src.dating.site_setting import site_seconds

# Ключ слота темпа. ⚠ Метится `{id}` — номером **сайта**: чужая метка осталась
# бы в ключе текстом, то есть одним общим слотом на все сайты (`round.md`).
GALLERY_ROUND_SLOT = 'dating:gallery:{id}'

# Настройки сайта, которыми правят заход.
GALLERY_EVERY_KEY = 'gallery_every_minutes'
GALLERY_BATCH_KEY = 'gallery_vision_batch'

# ⚠⚠ **Запас на дрожание планировщика, и без него «раз в час» означало бы «раз
# в два часа».** Слот считает «пора» строго: прошедшее с прошлого захода должно
# **дотянуть** до срока (`was - every >= left` в `round_slot_due`). Планировщик
# бьёт раз в час, но не по атомным часам: на проде заходы приходили в 20:20:20 и
# в 08:20:29 — разброс в секунды. Сойдись срок ровно с тиком, и тик, пришедший
# на секунду раньше, застал бы отметку живой — заход пропущен, следующий через
# час. Половина заходов исчезала бы молча, а настройка «1 час» врала бы человеку.
#
# ⚠⚠ Минута, а не пять: запас обязан покрыть дрожание планировщика (секунды) и
# при этом **не съедать разрешение настройки**. Пока срок задавали часами, пять
# минут ничего не стоили; с минутами они означали бы, что «20 минут» и «15
# минут» — одно и то же при четвертьчасовом тике, то есть настройка молча
# округляла бы вниз выбор человека.
GALLERY_EVERY_SLACK = 60

# Минута секундами — та же мера, в которой слот считает срок.
GALLERY_MINUTE = 60


async def gallery_round_run() -> dict:
    """Один заход: обойти папки анкет каждого сайта, затем разобрать пачку снимков.

    Returns:
        `{'sites': int, 'profiles': int, 'found': int, 'added': int,
          'gone': int, 'failed': int, 'vision': {...}}`.

    ⚠ **Отказ по одной анкете не уносит заход.** У одной девушки папка может
    быть не выбрана или переименована владельцем — состояние обычное, — и
    останавливаться из-за неё значило бы не обойти остальных.

    ## ⚠⚠ Темп держит слот, а не расписание

    Заход приходит от планировщика раз в час (`worker.md`, §3.0.1), а настройка
    сайта `gallery_every_minutes` умеет **разредить** его: сайт, чей слот ещё не истёк,
    пропускается целиком. Ускорить она не может — чаще, чем стучит планировщик,
    заход не случится, и подсказка настройки говорит это словами.

    ⚠ Слот ставится **на сайт**, а не на анкету: Диск у всех один, и разрежать
    надо походы к нему, а не работу по девушкам.
    """
    out = {'sites': 0, 'profiles': 0, 'found': 0, 'added': 0, 'gone': 0,
           'failed': 0, 'vision': {'took': 0, 'done': 0, 'failed': 0}}

    rows = await round_sites(what='галерея')
    done = await round_sites_run(rows, work=_site_work, every=_every,
                                 key=GALLERY_ROUND_SLOT, what='галерея',
                                 keep_going=True)

    for _, got in done:
        if not got:
            continue
        out['sites'] += 1
        for key in ('profiles', 'found', 'added', 'gone', 'failed'):
            out[key] += got[key]
        for key in ('took', 'done', 'failed'):
            out['vision'][key] += got['vision'][key]

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
            logger_info(f"галерея: сайтов {got['sites']}, анкет {got['profiles']}, "
                        f"файлов {got['found']}, "
                        f"новых {got['added']}, ушло {got['gone']}, "
                        f"разобрано {got['vision']['done']}, "
                        f"неудач {got['failed'] + got['vision']['failed']}")
    except Exception as err:  # noqa: BLE001 — заданию нельзя падать молча
        logger_exception(f'галерея: заход не удался: {err}')
    finally:
        # Пул закрываем сами: задание живёт один прогон, и оставленные
        # соединения копились бы по одному на каждый час.
        await mysql_pool_close()


async def _site_work(row, minutes: int) -> dict:
    """Заход по одному сайту: его анкеты, затем его же пачка разбора.

    ⚠ Пачка разбора берётся **по сайту** (`gallery_vision_batch`): общая
    выборка отдавала бы одному сайту место другого — молча и в пользу того, у
    кого номера анкет меньше.
    """
    site_id = int(row['site_id'] if isinstance(row, dict) else row)
    got = {'profiles': 0, 'found': 0, 'added': 0, 'gone': 0, 'failed': 0}

    for profile in await _profiles(site_id):
        one = await gallery_index_sync(profile['id'],
                                       gallery_folder_name(profile), site_id)
        got['profiles'] += 1
        if not one['ok']:
            got['failed'] += 1
            continue
        for key in ('found', 'added', 'gone'):
            got[key] += one[key]

    batch = await site_seconds(site_id, GALLERY_BATCH_KEY)
    got['vision'] = (await gallery_vision_run(batch, site_id) if batch
                     else {'took': 0, 'done': 0, 'failed': 0})

    return got


async def _every(site_id: int) -> int:
    """Срок сайта: сколько секунд не возвращаться к его папкам.

    Настройка задана **в минутах** (`gallery_every_minutes`), а слот считает
    секундами — перевод здесь, одним местом.

    ⚠⚠ Из срока вычитается `GALLERY_EVERY_SLACK`: без запаса срок, совпавший с
    шагом планировщика, пропускал бы каждый второй заход — см. объяснение у
    константы.
    """
    minutes = await site_seconds(int(site_id), GALLERY_EVERY_KEY)

    return max(int(minutes) * GALLERY_MINUTE - GALLERY_EVERY_SLACK, 1)


async def _profiles(site_id: int) -> list[dict]:
    """Анкеты этого сайта, чьи папки обходим: включённые и **с выбранной папкой**.

    ⚠ Берём **включённые**, а не «в сети»: фотографии лежат на Диске и от того,
    вошла ли девушка на сайт, не зависят. Привязка к `status` означала бы, что
    галерея спящей анкеты не обновляется никогда.

    ⚠⚠ **Невыбранная папка — не работа, а её отсутствие.** Прежде пустую
    колонку подменял логин анкеты, и заход честно искал на Диске папку
    `luchinskaolena@gmail.com`: два запроса к Диску на анкету ради заведомого
    «нет», восемнадцать запросов в час на нашем же проде. Отбор здесь, а не
    пропуском внутри: заход не должен и начинаться там, где работы нет.
    """
    async with mysql_get_db_async() as db:
        await db.execute("SELECT id, login, gallery_folder FROM profile "
                         " WHERE site_id = %s AND enabled = 1 "
                         "   AND gallery_folder <> '' ORDER BY id", (int(site_id),))

        return [dict(row) for row in (await db.fetchall() or [])]


if __name__ == '__main__':
    asyncio.run(main())
