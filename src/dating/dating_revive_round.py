"""Круг автовосстановления: опросить анкеты под присмотром и починить выпавшие.

Здесь распоряжение, а не работа: правила приходят из `dating_revive.py`, вход — из
`dating_revive_login.py`, состояние — из `dating_revive_db.py`, настройка сайта —
из `dating_revive_site.py`. Разделение и его признак — в шапке `dating_revive.py`.

Круг заводит смена держателя сокетов (`dating_chat_hold._revive_loop`) раз в
`DATING_REVIVE_PERIOD_SEC`; своего процесса у автовосстановления нет.

**Соседи зовутся по именам, связанным в этом модуле** — так их подменяют тесты
(`tests/test_revive_round.py`): круг проверяется без базы, без соседа и без живого
сайта, а порядок работы, счётчики и решения при этом настоящие. Беды, ради которых
эти тесты написаны, живут именно в них.
"""

import asyncio

from src.dating.dating_revive import dating_revive_plan
from src.dating.dating_revive_db import (dating_revive_db_now, dating_revive_db_log,
                                         dating_revive_db_plan_save,
                                         dating_revive_db_probe_save,
                                         dating_revive_db_profiles_kept,
                                         dating_revive_db_site_missing)
from src.dating.dating_revive_login import dating_revive_login_try
from src.dating.dating_revive_site import (dating_revive_site_error_write,
                                           dating_revive_site_presence)

# Как часто смена сверяет вход анкет с сайтом.
DATING_REVIVE_PERIOD_SEC = 60

# Сколько опросов идёт разом. Столько же и по той же причине берёт ручка опроса
# (`DATING_PROFILE_ACTION_PROBE_PARALLEL`): опрос — это единицы килобайт по HTTP, и
# двести анкет подряд не уложились бы в минуту круга. Входы этим числом не
# ограничены — они идут строго по одному, вкладка Chromium стоит куда дороже.
DATING_REVIVE_PROBE_PARALLEL = 16


async def dating_revive_round_run(login_run=None) -> dict:
    """Один круг: опросить анкеты под присмотром и починить те, что выпали.

    Опросы идут разом, входы — строго по одному, и это разные вещи по разным причинам.

    Опрос — три-четыре HTTP-запроса с таймаутом в десять секунд; у двухсот анкет
    последовательный обход занял бы минут семь, и круг «раз в минуту» превратился бы
    в «раз в семь-десять», а задержка обнаружения росла бы вместе с числом анкет.
    Тот же приём и по той же причине — в ручке `dating_profile_probe`.

    Вход — наоборот: каждый открывает вкладку Chromium (~143 МБ) в пуле, общем с
    соседом. Сайт лёг, все анкеты решили входить — и параллельные входы уронили бы
    пул вместе с собственной работой соседа. Поэтому решения и входы идут в один
    ряд, после того как опросы кончились.

    Вход передаётся параметром, а не импортируется намертво: так круг проверяется
    тестами без браузера, а модуль не тянет за собой половину проекта.

    Returns:
        dict: `checked`, `online`, `tried` (сколько раз входили), `revived`,
        `given_up`.
    """
    if login_run is None:
        # Импорт только когда вход настоящий: с заглушкой круг обязан работать без
        # браузера и без зависимостей проекта — на этом стоят его тесты.
        from src.dating.dating_login import dating_login_run

        login_run = dating_login_run

    profiles = await dating_revive_db_profiles_kept()
    counts = {'checked': len(profiles), 'online': 0, 'tried': 0, 'revived': 0, 'given_up': 0}

    # Настройка опроса спрашивается на сайт, а не на анкету: двести анкет с трёх
    # сайтов — это три вопроса соседу вместо двухсот, и все опросы круга видят одно
    # и то же. Сосед не ответил — сайт этот круг пропускается целиком.
    site_of = {site_id: await dating_revive_site_presence(site_id)
               for site_id in sorted({int(profile['site_id']) for profile in profiles})}
    presence_of = {site_id: presence for site_id, (presence, _) in site_of.items()}

    kept = [profile for profile in profiles if presence_of[int(profile['site_id'])]]

    # Анкеты выпавшего сайта проверить нечем — но и молчать про них нельзя: раньше
    # такая анкета оставалась в панели с прежним «всё хорошо», хотя её не трогали
    # уже неделю. Объявление соседу про сам сайт уходит отдельно (`dating_revive_site_presence`),
    # здесь помечается каждая анкета: человек смотрит в карточку, а не в чужую
    # вкладку «Ошибки».
    for profile in profiles:
        if not presence_of[int(profile['site_id'])]:
            await dating_revive_db_site_missing(profile, site_of[int(profile['site_id'])][1])

    # Ограничитель тот же, что у ручки опроса: разом — не значит все двести сразу.
    limit = asyncio.Semaphore(DATING_REVIVE_PROBE_PARALLEL)

    async def probe(profile: dict) -> dict:
        async with limit:
            return await _probe_one(profile, presence_of[int(profile['site_id'])])

    # `return_exceptions=True` — тем же приёмом и по той же причине, что в
    # `_presence_loop` держателя: упавший опрос одной анкеты не имеет права уносить
    # с собой круг. Разбор — в `_probe_failed`.
    probes = await asyncio.gather(*(probe(profile) for profile in kept), return_exceptions=True)

    for profile, outcome in zip(kept, probes):
        result = _probe_failed(outcome) if isinstance(outcome, BaseException) else outcome
        online = result['status'] == 'online'
        counts['online'] += int(online)

        # Время — на каждую анкету, а не одно на круг: перед этой строкой мог
        # пройти вход соседней анкеты, а он стоит минуту-две. По устаревшему `now`
        # наступивший за это время срок читался бы как ненаступивший, и анкета
        # ждала бы ещё круг — и так каждый раз, пока в круге есть хоть один вход.
        plan = dating_revive_plan(profile, online, await dating_revive_db_now())
        saved = await dating_revive_db_plan_save(int(profile['id']), plan)

        if plan['action'] == 'wait':
            await dating_revive_db_log(int(profile['id']), 'fail',
                       f"Вход не признан сайтом: {result['error'] or result['status']}")
            await dating_revive_site_error_write(profile, result['error'] or result['status'])
        elif plan['action'] == 'give_up':
            # Считаем и объявляем переход, а не состояние: решение отдаёт `give_up`
            # только на том круге, который сдался, — иначе сюда бы попадала одна и
            # та же анкета каждую минуту, пока её не поднимут руками.
            counts['given_up'] += 1
            await dating_revive_db_log(int(profile['id']), 'fail',
                       'Сутки не выходит войти — попытки прекращены, нужен вход вручную')
            await dating_revive_site_error_write(
                profile, 'сутки не выходит войти, автовход остановлен')
        elif plan['action'] == 'login' and not saved:
            # Откат не записался — входить нельзя. Следующий круг увидит прежний,
            # уже наступивший срок и войдёт снова через минуту, и так до
            # бесконечности: это единственный путь, где лестницу можно превысить.
            # Пропущенная попытка дешевле вкладки Chromium раз в минуту, а срок
            # никуда не делся — войдём следующим кругом.
            await dating_revive_db_log(int(profile['id']), 'info',
                       'Вход отложен: череду попыток не удалось записать, '
                       'а без неё лестница пауз не работает')
        elif plan['action'] == 'login':
            counts['tried'] += 1
            if await dating_revive_login_try(profile, login_run, plan, online):
                counts['revived'] += 1

    return counts


def _probe_failed(error: BaseException) -> dict:
    """Упавший опрос — отказом одной анкеты, а не концом круга.

    `dating_presence_probe` ловит своё внутри, но не всё: cookie из
    `storage_state` читаются **до** его `try`, и кривое состояние (`cookies`
    списком строк) бросит наружу. Пока `gather` шёл без `return_exceptions`, такая
    строка обрывала круг целиком — и не разово: следующий круг спотыкался о неё же,
    и анкеты после сломанной переставали проверяться совсем.

    Отказ считаем «не в сети», а не «пропускаем»: тихо пропущенная анкета висела бы
    в панели здоровой — ровно та беда «никто не замечает», ради которой всё
    затевалось. А так зажжётся тревога, и человек увидит причину в карточке.

    Отмену пропускаем наружу: смена кончилась, и продолжать круг незачем.
    """
    if isinstance(error, asyncio.CancelledError):
        raise error

    return {'status': 'error', 'unread': -1, 'ms': 0,
            'error': f'опрос не отработал: {type(error).__name__}: {error}'}


async def _probe_one(profile: dict, presence: dict) -> dict:
    """Опрос одной анкеты вместе с записью его итога в карточку.

    Отдельной функцией, потому что распараллеливается именно эта пара шагов: выше
    их зовут разом, под общим ограничителем.
    """
    from src.dating.dating_presence import dating_presence_probe

    probe = await dating_presence_probe(profile, presence)
    await dating_revive_db_probe_save(int(profile['id']), probe)

    return probe


