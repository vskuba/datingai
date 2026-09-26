"""Журнал работы по учётке — одна строка на событие.

Отдельным модулем, а не внутри проверки: писать в журнал нужно и проверке, и
держателю сокетов, и отправке. Держать функцию в `check` значило бы, что
приём сообщений импортирует проверку, а проверка — приём: круг из импортов.

**Журнал не имеет права ронять работу.** Упавшая запись строки — это потерянная
строка, а не потерянное сообщение; поэтому здесь всё молча гасится.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Длина строки журнала. Тексты из чата бывают на страницу, а в ленте читаются
# первые слова — остальное видно в самой переписке.
LOG_TEXT_MAX = 2000

# Сообщение в ленте режем короче: лента отвечает на вопрос «кто написал и о чём»,
# а не заменяет переписку.
LOG_MESSAGE_MAX = 300

# Сколько часов держим журнал. Настройка **сайта**: таблица общая, а срок
# решает тот, кто ведёт сайт. Трое суток — «что было в пятницу» ещё живой
# вопрос в понедельник.
#
# ⚠ Здесь только **имя** настройки. Умолчание живёт в реестре сайта
# (`SITE_KNOWN['log_keep_hours']`), и своей копии тут нет: была (`LOG_KEEP_DEFAULT =
# 72`), но чистка её не читала — срок она спрашивает у настройки через `site_seconds`,
# а тот берёт умолчание из реестра. Второе число просто расходилось бы с первым молча.
LOG_KEEP_SETTING = 'log_keep_hours'

# Пачка удаления и сколько их за заход. ⚠ Десять тысяч проходят за доли
# секунды; сотня пачек — миллион строк, с запасом на любой затор.
LOG_SWEEP_BATCH = 10000
LOG_SWEEP_PASSES = 100


async def log_write(profile_id: int, level: str, text: str) -> None:
    """Строка журнала. Не бросает: сбой журнала не должен ронять работу.

    Повтор предыдущей строки не копится: если текст совпал с самой свежей строкой
    по этой учётке, прежняя удаляется, а новая встаёт с текущим временем. Держатель
    сокетов ходит по кругу раз в минуту, и без этого лента на странице превращается
    в столбец из одной и той же фразы, за которым не видно событий. Сравнивается
    только текст: время — это «когда в последний раз», а не отдельное событие.
    """
    profile_id = int(profile_id)
    level = str(level or 'info')
    text = str(text or '')[:LOG_TEXT_MAX]

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT id, text FROM log WHERE profile_id = %s '
                             ' ORDER BY id DESC LIMIT 1', (profile_id,))
            last = await db.fetchone()

            if last and str(last['text']) == text:
                await db.execute('DELETE FROM log WHERE id = %s', (int(last['id']),))

            await db.execute('INSERT INTO log (profile_id, level, text) VALUES (%s, %s, %s)',
                             (profile_id, level, text))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] журнал: {e}')


async def log_message_in(profile_id: int, sender: str, name: str, text: str) -> None:
    """Входящее сообщение в журнал: от кого и о чём.

    Имя, если сайт его прислал, иначе номер — по голому номеру человек собеседницу
    не узнаёт, а имя приходит не всегда.
    """
    who = str(name or '').strip() or f'#{sender}'
    body = str(text or '').strip().replace('\n', ' ')
    if len(body) > LOG_MESSAGE_MAX:
        body = body[:LOG_MESSAGE_MAX].rstrip() + '…'

    await log_write(profile_id, 'in', f'{who}: {body}')


async def log_sweep() -> int:
    """Убирает записи журнала старше срока сайта. Возвращает, сколько убрано.

    ## ⚠⚠ Зачем

    Журнал растёт **вслед за инвайтами**: каждая отправка пишет строку, и при
    девяноста тысячах инвайтов в сутки выходит около ста двенадцати тысяч строк —
    три миллиона в месяц, под три гигабайта в год. Ротации у него не было вовсе:
    единственное удаление здесь схлопывает подряд идущие повторы.

    ## ⚠ Срок — у сайта, а таблица общая

    В `log` сайта нет, есть анкета; сайт узнаётся через неё. Поэтому чистим
    посайтно: у каждого свой срок, и строки его анкет живут по нему.

    Осиротевших строк не бывает — внешний ключ с каскадом уносит журнал вместе с
    анкетой.

    ## ⚠ Режем пачками, а не одним `DELETE`

    Миллион строк одним запросом держит блокировку минутами, а в журнал пишут все
    круги разом: запись встала бы в очередь за уборкой. Пачка в десять тысяч
    проходит за доли секунды, и между пачками писатели успевают вклиниться. То же
    решение и та же причина, что у журнала запросов (`request_sweep`).
    """
    from src.uvicorn.routers.dating.site_ import site_seconds

    gone = 0
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT DISTINCT site_id FROM profile')
            sites = [int(row['site_id']) for row in (await db.fetchall() or [])
                     if int(row['site_id'] or 0)]
    except Exception as e:
        logger_info(f'[dating] сайты для чистки журнала не спросились: {e}')

        return 0

    for site_id in sites:
        try:
            # ⚠⚠ **Без `or`**, и это не мелочь. `site_seconds` сама подставляет
            # умолчание реестра, когда значения нет или оно мусор; `or` поверх неё
            # превращал бы в умолчание **законный ноль** — то есть «не чистить»
            # молча становилось бы «чистить через трое суток».
            #
            # Поймано тестом на нуле в тот же час, когда уборку писали.
            hours = int(await site_seconds(site_id, LOG_KEEP_SETTING))
        except Exception as e:
            logger_info(f'[dating] срок журнала сайта #{site_id}: {e}')

            continue

        # ⚠ Ноль — законное «не чистить». Подставь мы его в `INTERVAL`, условие
        # стало бы «создано в будущем», то есть уборка стёрла бы **всё**.
        if hours <= 0:
            continue

        try:
            async with mysql_get_db_async() as db:
                for _ in range(LOG_SWEEP_PASSES):
                    # ⚠⚠ **Сначала номера, потом удаление по ним.** MySQL не
                    # принимает `LIMIT` в многотабличном `DELETE` (ошибка 1064), а
                    # без `LIMIT` пачки не выйдет: удаление миллиона строк одним
                    # запросом держит блокировку минутами, пока в журнал пишут все
                    # круги разом.
                    #
                    # Отбор идёт по своему индексу (`idx_log_when`), а удаление —
                    # по первичному ключу: оба шага дёшевы.
                    await db.execute(
                        'SELECT l.id FROM log l JOIN profile p ON p.id = l.profile_id'
                        ' WHERE p.site_id = %s'
                        '   AND l.created_at < NOW() - INTERVAL %s HOUR'
                        f' LIMIT {LOG_SWEEP_BATCH}', (site_id, hours))
                    ids = [int(row['id']) for row in (await db.fetchall() or [])]

                    if not ids:
                        break

                    marks = ', '.join(['%s'] * len(ids))
                    await db.execute(f'DELETE FROM log WHERE id IN ({marks})', ids)
                    cut = int(db.rowcount or 0)
                    await db.connection.commit()
                    gone += cut

                    if len(ids) < LOG_SWEEP_BATCH:
                        break
        except Exception as e:
            logger_info(f'[dating] чистка журнала сайта #{site_id}: {e}')

    if gone:
        logger_info(f'[dating] журнал анкет: убрано {gone} строк')

    return gone
