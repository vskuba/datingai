"""Запуск задания планировщика: имя из закрытого списка — отдельным процессом.

Cronicle умеет только одно — сходить по HTTP. Значит у панели должна быть
дверь, за которой он дёргает нашу работу: обход Диска, страховку держателя
чатов. Эта дверь здесь.

⚠⚠ **Задание называется именем, а не путём к файлу.** У соседа та же дверь
принимает `cronjob_path` и исполняет что назвали, проверяя лишь приставку
`src/cronjob/`. Нам так нельзя: наша панель открыта на публичном домене, и
«исполни файл, который я назову» — это приглашение. Имя же сверяется с
`CRONJOB_JOBS`, и всё, чего там нет, не запускается никак.

⚠ **Процессом, а не вызовом в панели.** Задание живёт минуты, держит свой пул
соединений и может упасть; внутри панели оно заняло бы её рабочий цикл, а
падение унесло бы не только себя. Отдельный процесс умирает один.

⚠ Срок обязателен: задание, зависшее на чужом молчании, иначе висит вечно и
копится — Cronicle запустит следующее, не дождавшись этого.
"""

import asyncio
import sys
from pathlib import Path

from datetime_.datetime_ import datetime_utc_now
from logging_.logging_ import logger_exception, logger_info
from setting_.setting_state import setting_state, setting_state_patch

# Что вообще можно запустить. ⚠⚠ Закрытый список, а не поиск по каталогу:
# ключ здесь — это разрешение, и появиться оно должно осознанно, вместе со
# строкой в расписании. Файл, случайно положенный в `src/cronjob/`, запускать
# снаружи нельзя.
CRONJOB_JOBS = {
    'gallery_round': 'src/cronjob/gallery_round.py',
    'dating_chat': 'src/cronjob/dating_chat.py',
    'e2e_run': 'src/cronjob/e2e_run.py',
}

# Сколько ждём задание по умолчанию. Пять минут: обход Диска по полусотне
# анкет и разбор пятёрки снимков укладываются, а зависшее — обрывается.
CRONJOB_TIMEOUT = 300

# Потолок срока. Cronicle передаёт своё число, и ошибка в нём (секунды против
# миллисекунд) не должна оставлять процесс на час.
CRONJOB_TIMEOUT_MAX = 1800

# Где лежит «когда задание заходило в последний раз»: одна строка `setting` на
# все задания, внутри — имя задания ключом.
#
# ⚠⚠ **Расписание у нас не хранится, и это важно.** Им распоряжается Cronicle
# (`worker.md`, §3.0.1), а здесь — **факт**: приходило или нет и когда. Панель,
# показывая «следующий заход», считает его от этой отметки, а не от своего
# представления о расписании: умри планировщик — отметка состарится, и человек
# увидит именно это, а не бодрое «следующий через 20 минут».
CRONJOB_LAST_KEY = 'cronjob_last'


async def cronjob_run(job: str, timeout: int = CRONJOB_TIMEOUT) -> dict:
    """Запустить задание по имени и дождаться его. Отказ — словами, не броском.

    Args:
        job: имя из `CRONJOB_JOBS`.
        timeout: сколько ждать, секунды; свыше `CRONJOB_TIMEOUT_MAX` обрезается.

    Returns:
        `{'ok': bool, 'job': str, 'out': str, 'why': str}` — `out` это то, что
        задание напечатало; `why` заполняется только при неудаче.

    Не бросает: ручка решает, каким кодом ответить, а Cronicle всё равно
    смотрит на код ответа — исключение здесь лишь спрятало бы причину в
    трейсбек.
    """
    path = CRONJOB_JOBS.get(str(job or '').strip())
    if not path:
        return {'ok': False, 'job': job, 'out': '',
                'why': f'задание «{job}» не из списка: {", ".join(sorted(CRONJOB_JOBS))}'}

    full = Path(path)
    if not full.is_file():
        return {'ok': False, 'job': job, 'out': '',
                'why': f'файл задания не найден: {path}'}

    wait = min(max(int(timeout or CRONJOB_TIMEOUT), 1), CRONJOB_TIMEOUT_MAX)
    logger_info(f'[cronjob] {job}: запуск, срок {wait} с')

    done = await _spawn(job, str(full), wait)
    await _mark(job, bool(done['ok']))

    return done


async def cronjob_last(job: str) -> dict:
    """Когда задание заходило в последний раз и чем кончилось.

    Отвечает на вопрос страницы «когда это возьмут в работу»: срок там считают
    **от прошлого захода**, а не от расписания, которого у нас нет.

    Args:
        job: имя задания из `CRONJOB_JOBS`.

    Returns:
        `{'at': str, 'ok': bool}`; `at` — время UTC в ISO. **Пустой `at` значит
        «не заходило ни разу»** — обычное состояние на свежей установке и
        единственный признак того, что задание в расписание не завели.
    """
    state = await setting_state(CRONJOB_LAST_KEY)
    one = state.get(str(job or '').strip()) or {}

    return {'at': str(one.get('at') or ''), 'ok': bool(one.get('ok'))}


async def _mark(job: str, ok: bool) -> None:
    """Отметить заход задания. Неудача — тоже заход: приходило, но не вышло.

    ⚠ Своя неудача записи отметки задание не роняет: она про показ, а работа
    уже сделана. Молча, впрочем, не проходит — строка в журнале остаётся.
    """
    try:
        await setting_state_patch(CRONJOB_LAST_KEY,
                                  {job: {'at': datetime_utc_now().isoformat(
                                      timespec='seconds'), 'ok': ok}})
    except (ValueError, OSError, RuntimeError) as err:
        logger_exception(f'[cronjob] {job}: отметка захода не легла: {err}')


async def _spawn(job: str, path: str, wait: int) -> dict:
    """Отдельный процесс тем же интерпретатором; срок обрывает зависшее.

    ⚠ Интерпретатор — `sys.executable`, а не `python` из `PATH`: в контейнере
    их бывает два, и второй не знает ни зависимостей панели, ни её путей.
    """
    started = None
    try:
        started = await asyncio.create_subprocess_exec(
            sys.executable, path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(started.communicate(), timeout=wait)
    except asyncio.TimeoutError:
        # ⚠ Процесс добиваем и **дожидаемся**: без `wait()` он остаётся
        # зомби до конца жизни панели, а заданий за сутки два десятка.
        if started:
            started.kill()
            await started.wait()
        logger_exception(f'[cronjob] {job}: не уложилось в {wait} с')

        return {'ok': False, 'job': job, 'out': '', 'why': f'срок {wait} с вышел'}
    except OSError as err:
        logger_exception(f'[cronjob] {job}: не запустилось: {err}')

        return {'ok': False, 'job': job, 'out': '', 'why': str(err)}

    said = (out or b'').decode(errors='replace').strip()
    cried = (err or b'').decode(errors='replace').strip()

    if started.returncode == 0:
        logger_info(f'[cronjob] {job}: готово')

        return {'ok': True, 'job': job, 'out': said, 'why': ''}

    # ⚠ В причину идёт **и** `stderr`, **и** `stdout`: трейсбек печатается в
    # первый, а внятная строка отказа — часто во второй, и без обоих в
    # журнале Cronicle остаётся пустота.
    logger_exception(f'[cronjob] {job}: код {started.returncode}: {cried[:300]}')

    return {'ok': False, 'job': job, 'out': said,
            'why': f'{cried}\n{said}'.strip() or f'код возврата {started.returncode}'}
