"""Круг избранных: написавшего добавляем девушке в звёздочки сам.

Ответил человек — он должен оказаться у девушки в избранных. Круг берёт работу
**из входящих писем** (`mail_in`) **и из ответов в чате** (`message`), и жмёт
звёздочку тем, кого в избранных ещё нет.

⚠⚠ **Чат добавлен вторым источником по живой беде.** Сперва была одна почта, и
на сайте без писем занятие работало только на бумаге: трое ответивших, в
избранных один — тот, кто прислал письмо. У LadaDate разговор идёт чатом.

## ⚠ Почему кругом, а не при заборе письма

Забор писем — дешёвый: тринадцать анкет, по HTTP, секунды. Звёздочка — дорогая:
вход браузером у соседа, ~40 секунд на человека. Повесь мы её на приход письма, круг
забора растянулся бы с секунд до минут, и опоздали бы **письма** — то есть первое
звено цепочки, от которого зависят и ответы, и отправка.

Поэтому занятия разведены: письма приходят своим кругом, звёздочки жмутся своим, а
связь между ними — таблица. Ту же развилку проходили описания собеседников
(`mailing._card`): дорогое не встраивают в дешёвое.

## Порядок работы одного сайта

1. Отправители писем этой анкеты — свежие первыми.
2. Кого мы уже решили (`favorite`) — вычитаем. **Ничего не осталось —
   уходим, не спросив сайт**: у большинства анкет большую часть времени новых
   писем нет, и запрос был бы платой за пустоту.
3. Список избранных с сайта (`GET /users/my-favorites`, ~70 мс) — истина о том,
   кто там уже есть.
4. Кто в списке, но не у нас в таблице, — записываем `already` и больше не
   спрашиваем: строка стоит записи, а не сорока секунд браузера.
5. Оставшихся жмём сценарием, не больше `favorite_batch` за заход, с паузой.

## ⚠ Без API сайта круг не работает — и это не ограничение, а следствие

Список избранных читается ручкой сайта. Её нет — нечем узнать, кто там уже есть.
Но и работы тогда нет тоже: письма попадают в `mail_in` **той же самой
ручкой** (`mail_in.py`), и сайт без API не принесёт ни одного письма. Так что
условие «нужен `api_base`» ничего не отсекает — оно лишь называет вещи своими
именами.

## ⚠ Расписание не спрашивается

Тот же довод, что у забора писем: человек написал, когда написал. Звёздочка — не
сообщение, она никому не показывается в ленте и не требует, чтобы девушка была «на
смене». Привяжи мы её к сменам, ночная почта ждала бы утра ради нажатия кнопки.

## ⚠⚠ Зато анкета обязана быть в сети

И это не то же самое, что расписание. Расписание — про часы работы; онлайн — про
то, есть ли у анкеты **живая сессия**. Нет её — и список избранных, и нажатие
звёздочки означают вход под спящей учёткой: в хороший день лишний запрос, в
плохой — строка в счётчике неудачных входов, за который сайт закрывает адрес всей
установке разом.

Прежде условие было одно (`enabled`), и круг исправно ходил за избранными к
анкетам, спящим вторые сутки. Работа при этом не теряется: письма никуда не
денутся, и вышедшая в смену девушка получит звёздочки все разом.

⚠ **Когда ходили — помним в Redis**, а не в памяти процесса: смена держателя
перезапускается (свой срок, деплой), и после каждого перезапуска круг ходил бы
заново. Тот же приём, что у забора писем и рассылки.
"""

import asyncio

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.dating.ready import ready_online
from src.dating.favorite import (FAVORITE_ALREADY,
                                        FAVORITE_DONE,
                                        FAVORITE_REASON_MAIL,
                                        DatingFavoriteError, favorite_add,
                                        favorite_mark, favorite_pick,
                                        favorite_settled, favorite_uids)

# Как часто круг просыпается. ⚠ Не частота захода: её задаёт `favorite_every`.
FAVORITE_ROUND_TICK_SEC = 60

# Настройки сайта, по которым круг живёт.
FAVORITE_ON_SETTING = 'favorite_on'
FAVORITE_EVERY_SETTING = 'favorite_every'
FAVORITE_BATCH_SETTING = 'favorite_batch'

# Отметка «на этот сайт только что ходили». В Redis — переживает смену держателя.
FAVORITE_ROUND_KEY = 'dating:favorite:round:{site_id}'

# Пауза между звёздочками. ⚠ Не вежливость, а осторожность: два входа под разными
# анкетами в одну секунду сайт считает подозрением, а блокировка стоит анкеты. Та
# же причина, по которой разведён обход у забора писем.
FAVORITE_PAUSE_SEC = 2.0

# Сколько отправителей смотрим у одной анкеты. Письма старше этого числа уже
# разобраны прошлыми заходами; читать всю переписку ради отбора незачем.
FAVORITE_SENDERS_MAX = 100


async def favorite_round_run() -> dict:
    """Один круг: обойти сайты, где добавление включено и подошёл срок.

    Returns:
        dict: `sites` — сайтов обойдено, `profiles` — анкет посмотрено,
            `added` — звёздочек нажато, `already` — оказалось на месте,
            `failed` — не вышло.
    """
    ran = seen = added = already = failed = 0

    for site in await _sites():
        site_id = int(site['site_id'])
        if not await _on(site_id) or not _due(site_id):
            continue

        # ⚠ Отметку ставим **до** обхода: он идёт минутами, и следующий тик завёл
        # бы второй обход, пока идёт первый.
        _mark(site_id, await _every(site_id))

        base = str(await _setting(site_id, 'api_base')).strip()
        if not base:
            continue

        profiles = await _profiles(site_id)
        if not profiles:
            continue

        ran += 1
        password = str(await _setting(site_id, 'api_password')).strip()
        left = await _batch(site_id)

        for profile in profiles:
            if left <= 0:
                break

            # ⚠ **Статус перед походом, а не только при отборе.** Звёздочка — это
            # вход под девушкой на сайте, и заходы разведены паузой: за время
            # обхода список успевает устареть. Та же беда, что поймана у писем
            # (`ready_online`), и тот же ответ.
            if not await ready_online(int(profile['id'])):
                continue

            seen += 1
            got = await _profile_run(profile, base, password, left)
            added += got['added']
            already += got['already']
            failed += got['failed']
            # ⚠ Потолок съедают **прогоны**, а не неудачи вообще. Не пришедший
            # список избранных — это отказ по HTTP, вкладку у соседа он не занимал,
            # и вычитать его из потолка значило бы наказывать сайт за молчание.
            left -= got['spent']

    if added or failed:
        logger_info(f'[dating] круг избранных: анкет {seen}, добавлено {added}'
                    + (f', уже там {already}' if already else '')
                    + (f', с отказом {failed}' if failed else ''))

    return {'sites': ran, 'profiles': seen, 'added': added, 'already': already,
            'failed': failed}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _profile_run(profile: dict, base: str, password: str, left: int) -> dict:
    """Одна анкета: кого надо — добавить, остальных не трогать.

    ⚠ **Отказ одной анкеты не уносит остальных**: у неё мог протухнуть пароль, а у
    соседних всё в порядке. В журнал анкеты он попадает через `favorite_add`
    (или, если не дошли до захода, остаётся в логе процесса) — отдельной строки
    здесь не заводим, иначе сломанная анкета писала бы её каждый заход круглые
    сутки.
    """
    out = {'added': 0, 'already': 0, 'failed': 0, 'spent': 0}
    profile_id = int(profile['id'])
    site_id = int(profile['site_id'])

    letters = await _senders(profile_id)
    if not letters:
        return out

    settled = await favorite_settled(profile_id)

    # ⚠ **Сайт спрашиваем только если есть о ком.** Все отправители уже решены —
    # запрос ничего не изменит, а у большинства анкет большую часть времени именно
    # так: письма приходят реже, чем ходит круг.
    if not favorite_pick(letters, set(), settled, len(letters)):
        return out

    try:
        favorites = await favorite_uids(profile, base, password)
    except DatingFavoriteError as e:
        logger_info(f'[dating] избранные анкеты #{profile_id}: {e}')
        out['failed'] += 1

        return out

    # Кто уже там, но у нас не записан — записываем и больше не спрашиваем. Строка
    # стоит записи в базу, а неведение — запроса к сайту на каждом заходе.
    #
    # ⚠ Записываем **только писавших**: у девушки в избранных бывают десятки людей,
    # заведённых руками, и таскать их всех в свою таблицу незачем — вопрос к ней
    # задают лишь про тех, кто написал.
    wrote = set(letters)
    for uid in favorites:
        if uid in wrote and uid not in settled:
            await favorite_mark(site_id, profile_id, uid,
                                       FAVORITE_ALREADY, '',
                                       FAVORITE_REASON_MAIL)
            out['already'] += 1

    pick = favorite_pick(letters, favorites, settled, left)
    for number, uid in enumerate(pick):
        if number:
            await asyncio.sleep(FAVORITE_PAUSE_SEC)

        out['spent'] += 1
        try:
            got = await favorite_add(profile, uid, password)
        except Exception as e:
            logger_info(f'[dating] звёздочка {uid} у анкеты #{profile_id} '
                        f'сорвалась: {e}')
            out['failed'] += 1
            continue

        if str(got.get('state')) == FAVORITE_DONE:
            out['added'] += 1
        elif str(got.get('state')) == FAVORITE_ALREADY:
            out['already'] += 1
        else:
            out['failed'] += 1

    return out


async def _sites() -> list[dict]:
    """Живые сайты. Включённость — настройкой, а не столбцом: спрашиваем отдельно."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id FROM site'
                             ' WHERE is_gone = 0 ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для избранных не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _profiles(site_id: int) -> list[dict]:
    """Анкеты сайта, под которыми можно жать звёздочку.

    ⚠ **Расписание не спрашиваем**: звёздочка не сообщение и смены не требует.

    ⚠⚠ **Зато обязателен онлайн** (`status = 'online'`), и это исправление живой
    беды. Прежде условие было одно (`enabled`), и круг ходил на сайт за списком
    избранных у анкет, спящих вторые сутки. Каждый такой заход — вход под спящей
    учёткой: в хороший день лишний запрос, в плохой — строка в счётчике неудачных
    входов, за который сайт закрывает адрес **всей установке**
    (`ERROR_SING_IN_BY_IP_DAILY_FAILED_LIMIT`, сутки работы в сентябре).

    Работа при этом не теряется: письма, ради которых звёздочку и ставят, никуда
    не денутся, а девушка выйдет в смену и получит их все разом. Тот же довод и то
    же правило, что у инвайтов (`invite._girls`) и рассылки.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, site_id, login, password, password_source'
                ' FROM profile'
                ' WHERE site_id = %s AND enabled = 1 AND login <> %s'
                "   AND status = 'online'"
                ' ORDER BY id', (int(site_id), ''))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] анкеты сайта #{site_id} для избранных '
                    f'не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _senders(profile_id: int) -> list[str]:
    """Номера тех, кто **ответил** этой анкете, — свежие первыми.

    ## ⚠⚠ Два источника, и второй пришлось добавлять по живой беде

    Здесь была одна таблица — `mail_in`, входящие письма. Для сайта с почтой это
    верно, но у LadaDate разговор идёт **чатом**: реплики ложатся в `message`, и
    круг не видел их вовсе.

    Цена была видна на проде: трое ответивших, в избранных — **один**, и ровно
    тот, кто прислал письмо. Двое, ответивших в чате, прошли мимо звёздочки; на
    сайте, где писем почти нет, занятие работало только на бумаге.

    Условие при этом не менялось — «ответил, значит в избранные». Изменилась
    дорога, которой ответ приходит.

    ⚠ **Порядок не украшение**: потолок захода отрезает хвост, и отрезать надо
    самое старое. Человек, написавший минуту назад, ждёт звёздочки больше, чем
    тот, чьё письмо лежит с прошлой недели. Поэтому источники сливаются в один
    список и сортируются **вместе**, а не «сперва письма, потом чат».

    ⚠ Только **входящие** реплики (`direction = 'in'`): наш инвайт, уехавший в
    переписку при первом ответе, лежит там же и ответом не является.
    """
    try:
        async with mysql_get_db_async() as db:
            # ⚠ `UNION ALL` и группировка поверх, а не два запроса: человек мог и
            # письмо прислать, и ответить в чате — тогда он один, и берём
            # свежайшее из двух.
            await db.execute(
                'SELECT uid, MAX(at) AS last FROM ('
                '  SELECT uid, date_post AS at FROM mail_in'
                '   WHERE profile_id = %s AND uid <> %s'
                '  UNION ALL'
                '  SELECT sender_uid AS uid, created_at AS at FROM message'
                "   WHERE profile_id = %s AND direction = 'in' AND sender_uid <> %s"
                ') AS both_'
                ' GROUP BY uid ORDER BY last DESC LIMIT %s',
                (int(profile_id), '', int(profile_id), '', FAVORITE_SENDERS_MAX))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] отправители анкеты #{profile_id} не спросились: {e}')

        return []

    return [str(row['uid']) for row in rows or []]


async def _setting(site_id: int, key: str) -> str:
    from src.uvicorn.routers.dating.site_ import site_setting

    return await site_setting(site_id, key)


async def _seconds(site_id: int, key: str) -> int:
    from src.uvicorn.routers.dating.site_ import site_seconds

    return int(await site_seconds(site_id, key))


async def _on(site_id: int) -> bool:
    """Включено ли добавление у этого сайта."""
    return await _seconds(site_id, FAVORITE_ON_SETTING) == 1


async def _every(site_id: int) -> int:
    """Как часто ходить по этому сайту, в секундах."""
    return await _seconds(site_id, FAVORITE_EVERY_SETTING)


async def _batch(site_id: int) -> int:
    """Сколько звёздочек жать за заход — на весь сайт, а не на анкету.

    ⚠ Потолок общий: он про занятую вкладку у соседа, а вкладка одна на всех.
    Раздай мы по столько каждой анкете, тринадцать учёток дали бы тридцать девять
    прогонов — полчаса живого браузера за один заход.
    """
    return await _seconds(site_id, FAVORITE_BATCH_SETTING)


def _due(site_id: int) -> bool:
    """Пора ли идти. ⚠ Сбой Redis решается в сторону бездействия.

    Не прочиталась отметка — не идём. Лишний заход это входы браузером под каждой
    анкетой подряд, а сайт считает частые заходы подозрением; пропущенный стоит
    десяти минут задержки звёздочки. Цена ошибок несимметрична.
    """
    try:
        return not redis_conn_get().get(
            FAVORITE_ROUND_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] отметка избранных не прочиталась: {e}')

        return False


def _mark(site_id: int, every: int) -> None:
    """Отмечает заход. Срок отметки **и есть** настройка: Redis погасит её сам."""
    try:
        redis_conn_get().setex(
            FAVORITE_ROUND_KEY.format(site_id=int(site_id)),
            max(int(every), 1), '1')
    except Exception as e:
        logger_info(f'[dating] отметка избранных не поставилась: {e}')
