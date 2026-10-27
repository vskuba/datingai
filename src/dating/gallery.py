"""Галерея анкеты: что показать на вкладке и как это сгруппировать.

Читает **индекс в базе**, а не Диск. Диск опрашивает `gallery_index.py` по
заданию Cronicle и по кнопке «Обновить»; страница же обязана открываться
быстро и открываться тогда, когда Диск молчит.

⚠ **Группы берутся из путей, а не из зашитых имён.** Раскладку придумывает
человек: сегодня это `photo/public` и `photo/intim`, завтра он заведёт третью
папку. Зашей мы два слова в код — третья не появилась бы, и никто бы не понял
почему.

⚠ Порядок групп задаём мы, а имена — он: `public` первой, остальные по
алфавиту. Снимок, который видят все, важнее того, что показывают изредка.
"""

from datetime import datetime, timedelta

from datetime_.datetime_ import datetime_utc_now
from mysql_.mysql_ import mysql_get_db_async

from src.cronjob.cronjob import cronjob_last
from src.dating.json_column import json_column_list

# Группа, которую показываем первой. Остальные — по алфавиту следом.
#
# ⚠ Это **порядок**, а не отбор: папка с любым другим именем не прячется, а
# встаёт ниже. Отбор именами означал бы, что заведённая человеком папка
# пропадает молча.
GALLERY_GROUP_FIRST = 'public'

# Сколько плиток отдаём на вкладку за раз. Сорок снимков на анкету — обычный
# объём; потолок нужен не ради него, а ради папки, куда однажды высыпали всё.
GALLERY_FILES_MAX = 500

# Чьи заходы считаем своими: имя задания планировщика (`CRONJOB_JOBS`).
# ⚠ Совпадение с тем списком сторожит тест: разойдись имена, страница вечно
# показывала бы «заходов ещё не было» при исправно работающем задании.
GALLERY_ROUND_JOB = 'gallery_round'

# Как часто задание заходит — час (`worker.md`, §3.0.1).
#
# ⚠⚠ **Это оценка, а не расписание.** Расписанием распоряжается Cronicle, и
# второй его записи у нас нет намеренно: две записи одного правила расходятся
# молча. Отсюда и счёт — **от прошлого захода**, а не от «ровно в HH:20»: смени
# планировщик минуту, и предсказание поедет за ним само.
GALLERY_ROUND_EVERY_SEC = 3600

# Насколько задание должно опоздать, чтобы мы сказали «не приходит». Пропуск
# одного захода — ещё не беда (Cronicle мог ждать прошлый), двух — уже повод
# смотреть в расписание.
GALLERY_ROUND_LATE_SEC = GALLERY_ROUND_EVERY_SEC * 2


def gallery_folder_name(profile: dict) -> str:
    """Имя папки девушки на Диске: выбранное человеком либо логин анкеты.

    Args:
        profile: строка анкеты — нужны `gallery_folder` и `login`.

    Returns:
        Имя папки. Пустым не бывает: у анкеты всегда есть логин.

    ⚠ **Логин — не догадка, а правило, подтверждённое данными.** Папки на Диске
    так и названы (снято 01.10.2026: `bogiladiana9021@bigmir.net` — это
    `profile.login`). Колонка `gallery_folder` держит **исключение**: папку,
    названную иначе. Поэтому пустая колонка — обычный случай, а не пробел.
    """
    return str(profile.get('gallery_folder') or '').strip() or str(profile.get('login') or '')


async def gallery_view(profile_id: int, folder: str = '') -> dict:
    """Что показать на вкладке: плитки группами, как их разложил человек.

    Args:
        profile_id: чья галерея.
        folder: имя папки — только чтобы вернуть его странице; данные берутся
            из индекса по номеру анкеты.

    Returns:
        `{'folder': str, 'groups': [{'name': str, 'files': [...]}],
          'total': int, 'taken_at': str|None}`. Ушедшие файлы (`is_gone`) не
        показываются вовсе — но и не удаляются, см. `gallery_index.py`.

    ⚠ **Пустые группы не отдаются.** Папка `intim` без снимков — это не
    «раздел, который забыли наполнить», а просто ничего; подпись над пустотой
    человек читает как поломку.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT file_id, path, name, mime, kind, size, width, height, '
            '       description, tags, described_at, described_error, edited_at, '
            '       updated_at '
            '  FROM gallery_file '
            ' WHERE profile_id = %s AND is_gone = 0 '
            ' ORDER BY path LIMIT %s', (profile_id, GALLERY_FILES_MAX))
        rows = [dict(row) for row in (await db.fetchall() or [])]

    groups: dict[str, list] = {}
    taken_at = None
    for row in rows:
        seen = row.pop('updated_at', None)
        if seen and (taken_at is None or seen > taken_at):
            taken_at = seen
        groups.setdefault(gallery_group_of(row['path']), []).append(_file_of(row))

    return {'folder': folder,
            'groups': [{'name': name, 'files': groups[name]}
                       for name in sorted(groups, key=_group_order)],
            'total': len(rows),
            'taken_at': taken_at.isoformat() if taken_at else None}


async def gallery_round_when() -> dict:
    """Когда обход Диска возьмёт в работу новые файлы.

    Вопрос человека на вкладке простой: «я положил фотографию на Диск — когда
    она тут появится?». Ответ складывается из факта (когда задание заходило) и
    известного шага (раз в час), а не из расписания: расписание живёт у
    Cronicle.

    Returns:
        `{'last_at': str, 'next_at': str, 'late': bool, 'every_sec': int}` —
        время UTC в ISO, пустая строка значит «не было». **Часовой пояс
        накладывает страница**, как и везде в панели.

    ⚠⚠ **`last_at` пустой — это не «скоро будет», а «задания нет в
    расписании».** Отличать их обязательно: иначе свежая установка, где
    Cronicle не настроен вовсе, обещала бы человеку заход через час — и так
    каждый час.

    ⚠ `late` поднимается, когда заход просрочен вдвое: один пропуск — обычное
    дело, два подряд значат, что планировщик до нас не доходит.
    """
    last = await cronjob_last(GALLERY_ROUND_JOB)
    if not last['at']:
        return {'last_at': '', 'next_at': '', 'late': False,
                'every_sec': GALLERY_ROUND_EVERY_SEC}

    was = datetime.fromisoformat(last['at'])
    next_at = was + timedelta(seconds=GALLERY_ROUND_EVERY_SEC)
    late = datetime_utc_now() - was > timedelta(seconds=GALLERY_ROUND_LATE_SEC)

    return {'last_at': was.isoformat(), 'next_at': next_at.isoformat(),
            'late': late, 'every_sec': GALLERY_ROUND_EVERY_SEC}


def gallery_group_of(path: str) -> str:
    """Имя группы из пути файла: последняя папка над ним.

    Берём именно последнюю, а не весь путь: `photo/public` и `photo/intim`
    человек читает как «public» и «intim», а приставка `photo/` у всех одна и
    ничего не различает.

    ⚠⚠ **Публичная, и это не стиль.** Отбор снимков под отправку спрашивает тот
    же вопрос («какой группы этот файл»), и своя копия правила разошлась бы с
    показом: человек видел бы снимок в «public», а отбор считал бы его чужим.
    Повторить это в SQL тем более нельзя — `SUBSTRING_INDEX(path, '/', 1)` даёт
    приставку `photo`, а не группу.
    """
    where = path.rpartition('/')[0]
    return where.rpartition('/')[2] if where else ''


def _group_order(name: str) -> tuple:
    """Порядок групп: `public` первой, остальные по алфавиту."""
    return (0 if name == GALLERY_GROUP_FIRST else 1, name)


def _file_of(row: dict) -> dict:
    """Запись файла для страницы: без внутренних отметок, но с их смыслом.

    Страница не должна знать ни про `is_gone`, ни про то, как хранится время.
    Ей нужно другое: показывать ли плитку-заглушку, можно ли открыть кадр, и
    чем помечено описание — моделью или рукой человека.
    """
    return {
        'id': row['file_id'],
        'name': row['name'],
        'kind': row['kind'],
        'mime': row['mime'],
        'size': int(row['size'] or 0),
        'width': int(row['width'] or 0),
        'height': int(row['height'] or 0),
        'description': row['description'] or '',
        # ⚠ Через json_column_list: JSON-столбец приезжает то списком,
        # то строкой — смотря какой драйвер и как настроен.
        'tags': json_column_list(row['tags']),
        'described': bool(row['described_at']),
        'described_error': row['described_error'] or '',
        'edited': bool(row['edited_at']),
        # Полный кадр видео отдаёт просмотрщик Диска, а не мы: гонять десятки
        # мегабайт через панель значило бы качать их внутрь.
        'view_url': (f'https://drive.google.com/file/d/{row["file_id"]}/view'
                     if row['kind'] == 'video' else ''),
    }
