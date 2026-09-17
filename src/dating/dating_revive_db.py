"""Состояние автовосстановления в своей базе: что прочитать и что записать.

Отдельно от круга по признаку из шапки `dating_revive.py`: **всё здесь ходит в
нашу базу и никуда больше.** Отсюда два общих правила этих функций.

**Часы — базы, а не контейнера.** `logged_in_at` и `last_seen_at` пишет сам MySQL
своим `NOW()`, возраст входа считается там же `TIMESTAMPDIFF`, а `now` для правил
берётся отсюда (`dating_revive_db_now`). Возьми питон свои `datetime.now()`, и
лестница поехала бы на расхождение поясов: `TZ` не задан ни в одном compose, а
прод-MySQL стоит на чужом сервере.

**Запись не роняет работу.** Исключение гасится и уходит в лог — кроме
`dating_revive_db_plan_save`, которая возвращает исход: незаписанный откат это
разрешение войти снова через минуту, и круг обязан о нём знать.

Тяжёлые импорты (`mysql_`, `logging_`) живут внутри функций: так модуль
импортируется даром, а сюита правил остаётся без зависимостей проекта.
"""

from datetime import datetime


async def dating_revive_db_log(profile_id: int, kind: str, text: str) -> None:
    """Строка в журнал анкеты. Обёртка ради одного импорта: журнал тянет за собой
    базу, а модуль держит тяжёлые импорты внутри функций (см. шапку)."""
    from src.dating.dating_log import dating_log_write

    await dating_log_write(int(profile_id), kind, text)


async def dating_revive_db_now() -> datetime:
    """Который час — по часам базы, а не контейнера.

    В этих часах живут все времена анкеты: `logged_in_at` и `last_seen_at` пишет
    сам MySQL своим `NOW()`, а `relogin_since` и `relogin_next_at` пишет питон —
    но значениями отсюда, так что порода у них одна. Возьми питон свои
    `datetime.now()`, и сравнение «пора ли входить» поехало бы ровно на
    расхождение поясов: `TZ` не задан ни в `docker-compose.yml`, ни в локальной
    накладке, а прод-MySQL стоит на чужом сервере со своими настройками. Заказчик
    просил лестницу в минутах, а не в минутах ± часовой пояс.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT NOW() AS db_now')
        row = await db.fetchone()

    return (dict(row or {}) or {}).get('db_now') or datetime.now()


async def dating_revive_db_profiles_kept() -> list[dict]:
    """Анкеты под присмотром автомата — те, кого хотя бы раз ввели успешно.

    ⚠ **Расписание старше присмотра**, и без этого правила два круга дерутся.
    Поймано живым прогоном: анкету ввели руками (`keep_online` ставится после
    удачного входа), потом ей завели расписание. Смена кончилась — круг
    расписания вышел и стёр сессию; через минуту восстановление вошло обратно,
    ещё через минуту расписание снова вышло. Каждый заход — вкладка Chromium у
    соседа, и так по кругу всю ночь.

    Поэтому анкету с включённым расписанием восстанавливаем **только внутри её
    смены**. Вне смены она обязана быть не в сети — это и есть смысл расписания,
    а «присмотр» здесь означал бы «расписание не действует».

    ⚠ Отбор по сменам идёт в питоне, а не запросом: смены лежат JSON-полем, и
    вопрос «попадает ли минута в интервал» SQL не задать. Правило одно на всех —
    `dating_schedule_now`, тот же, по которому решает круг расписания и по
    которому зеленеет полоса на странице.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        # Возраст входа считает база, а не питон: `logged_in_at` пишется её же
        # `NOW()`, и вычитание из `datetime.now()` контейнера сравнивало бы двое
        # разных часов — `TZ` не задан ни в одном compose, а прод-MySQL стоит на
        # чужом сервере. Наружу уходит число секунд, момент времени тут не нужен.
        await db.execute(
            'SELECT id, login, site_id, storage_state, status, last_error, '
            '       relogin_since, relogin_tries, relogin_next_at, '
            '       schedule, schedule_on, '
            '       TIMESTAMPDIFF(SECOND, logged_in_at, NOW()) AS login_age_sec '
            '  FROM profile WHERE keep_online = 1 ORDER BY id')
        rows = await db.fetchall()

    return [dict(row) for row in (rows or []) if await _on_duty(row)]


async def dating_revive_db_probe_save(profile_id: int, probe: dict) -> None:
    """Итог опроса в карточку. Раньше это делала только открытая страница."""
    from logging_.logging_ import logger_info
    from mysql_.mysql_ import mysql_get_db_async

    seen = 'NOW()' if probe['status'] == 'online' else 'last_seen_at'
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'UPDATE profile SET status = %s, unread = %s, last_error = %s, '
                f'       last_seen_at = {seen} WHERE id = %s',
                (probe['status'], probe['unread'] if probe['unread'] >= 0 else 0,
                 probe['error'], int(profile_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] итог опроса не записался: {e}')


async def dating_revive_db_plan_save(profile_id: int, plan: dict) -> bool:
    """Новые тревога и откат. Успешный вход перепишет их сам — и это правильно.

    Исключение гасится (журналы работу не роняют), но итог возвращается: круг
    обязан знать, легла запись или нет. Незаписанный откат — это разрешение войти
    снова через минуту, и вход после потери записи круг отменяет.
    """
    from logging_.logging_ import logger_info
    from mysql_.mysql_ import mysql_get_db_async

    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET relogin_since = %s, relogin_tries = %s, '
                             '       relogin_next_at = %s WHERE id = %s',
                             (plan['relogin_since'], int(plan['relogin_tries']),
                              plan['relogin_next_at'], int(profile_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] череда попыток не записалась: {e}')
        return False

    return True


async def dating_revive_db_site_missing(profile: dict, why: str) -> None:
    """Анкета выпавшего сайта: показать это в её карточке, а не только соседу.

    Тревогу (`relogin_since`) **не зажигаем** и лестницу не двигаем: анкета не
    объявлена вне сети — её просто нечем проверить, и входить под ней бессмысленно
    (вход всё равно кончается тем же опросом, которого нет). Поэтому только
    `status` и причина: человек, открывший панель, должен увидеть, что анкету не
    трогали, а не прежнее «всё хорошо» недельной давности.

    `last_seen_at` не двигаем: у него смысл «когда сайт последний раз признал
    учётку», и опроса как раз не было.

    Строку в журнал пишем каждый круг намеренно: журнал схлопывает одинаковые
    подряд (`dating_log_write`), поэтому в ленте останется одна строка со временем
    последней проверки, а не столбец из полутора тысяч за сутки.
    """
    from logging_.logging_ import logger_info
    from mysql_.mysql_ import mysql_get_db_async

    profile_id = int(profile['id'])
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET status = %s, last_error = %s '
                             ' WHERE id = %s', ('error', str(why)[:500], profile_id))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] пометка о выпавшем сайте не записалась: {e}')

    await dating_revive_db_log(profile_id, 'fail', f'Проверить анкету нечем: {why}')


# ── Приватное ────────────────────────────────────────────────────────────────

async def _on_duty(row) -> bool:
    """Положено ли анкете быть в сети прямо сейчас. Без расписания — всегда да."""
    if not int(row.get('schedule_on') or 0):
        return True

    import time

    from datetime_.datetime_ import datetime_offset_hours
    from setting_.setting_ import setting_get

    from src.dating.dating_json import dating_json_load
    from src.dating.dating_schedule import dating_schedule_now

    offset = datetime_offset_hours(await setting_get('timezone', '+0'))
    local = time.gmtime(time.time() + offset * 3600)

    return dating_schedule_now(dating_json_load(row.get('schedule')),
                               local.tm_hour * 60 + local.tm_min)
