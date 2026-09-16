"""Круг забора писем: входящие приходят в панель сами.

До сих пор письма забирала **только кнопка** «Забрать письма», и забирала у одной
анкеты — той, что выбрана слева. Автоматики не было никакой.

## ⚠ Почему это было хуже, чем выглядело

Забор стоит **первым** в цепочке, и вся остальная автоматика опиралась на него:
круг ответов пишет ответы на то, что лежит в `dating_mail_in`, круг отправки шлёт
написанное. Оба научились работать сами — и оба молча простаивали, пока человек не
нажмёт кнопку. Панель при этом выглядела работающей: выключатели подняты, ошибок
нет. Просто писем не было, потому что за ними никто не сходил.

## ⚠ Обходим анкеты, а не сайты

У сайта нет ручки «письма всех наших учёток», и быть не может: он про них не
знает. Письма приходят конкретной девушке, и ходить за ними надо под ней. Отсюда
устройство круга: сайт решает **пойдём ли** (`mail_check_on`) и **как часто**
(`mail_check_every`), а ходим по каждой включённой анкете этого сайта.

## ⚠⚠ Ходим только под анкетой, которая в сети

Забор идёт **под самой девушкой**: вход её логином, потом чтение писем. У спящей
анкеты живой сессии нет, значит каждый заход — это новый `POST /auth/login`.

Прежде условие было одно (`enabled`), и круг обходил все анкеты сайта каждые две
минуты независимо от их состояния. Пока вход получался, это были лишние запросы;
когда общий агентский пароль перестал подходить — тринадцать неудачных входов
каждые две минуты, девять тысяч в сутки. Счётчик у сайта суточный, и опустеть он
не мог: мы сами не давали.

Работа при этом не теряется. Письма никуда не денутся, и вышедшая в смену девушка
прочтёт их все разом.

## ⚠ Расписание не спрашивается

Письмо приходит, когда его написали, а не когда у девушки смена. Ответ на него всё
равно уйдёт по часам отправки — но лежать он к тому времени будет уже готовым.
Привяжи мы забор к сменам, ночная почта ждала бы утра, чтобы только начать путь.

⚠ **Заход стоит запросов к сайту**, поэтому срок между заходами держим в Redis:
смена держателя перезапускается, и память процесса сбрасывалась бы вместе с ней —
после каждого деплоя круг ходил бы заново. Тот же приём, что у рассылки и ответов.
"""

import asyncio

from logging_.logging_ import logger_info

from src.dating.dating_feed import dating_feed_done, dating_feed_write
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.dating.dating_mail_in import DatingMailInError, dating_mail_in_fetch
from src.dating.dating_ready import dating_ready_online

# Как часто круг просыпается. ⚠ Не частота забора: её задаёт `mail_check_every`.
DATING_MAIL_IN_ROUND_TICK_SEC = 60

# Как часто ходить, если у сайта не задано. Пять минут: столько же, сколько у
# импорта переписки, и по той же причине — письмо, замеченное через час, уже
# опоздало.
DATING_MAIL_IN_EVERY_DEFAULT = 300

# Настройка сайта.
DATING_MAIL_IN_EVERY_SETTING = 'mail_check_every'

# Отметка «на этот сайт только что ходили». В Redis — переживает смену держателя.
DATING_MAIL_IN_ROUND_KEY = 'dating:mail_in:round:{site_id}'

# Пауза между анкетами одного сайта. ⚠ Не вежливость, а осторожность: тринадцать
# входов в одну секунду сайт считает подозрением, а блокировка стоит анкеты. Та же
# причина, по которой заходы под одной анкетой разведены замком в
# `dating_site_api.py`.
DATING_MAIL_IN_PAUSE_SEC = 1.0


async def dating_mail_in_round_run() -> dict:
    """Один круг: обойти анкеты сайтов, где забор включён и подошёл срок.

    Returns:
        dict: `sites` — сайтов обойдено, `profiles` — анкет опрошено,
            `added` — новых писем принесено, `failed` — анкет с отказом.
    """
    sites = await _sites()
    if not sites:
        return {'sites': 0, 'profiles': 0, 'added': 0, 'failed': 0}

    ran = seen = added = failed = 0
    for site in sites:
        site_id = int(site['site_id'])
        if not _due(site_id):
            continue

        # ⚠ Отметку ставим **до** обхода: он идёт секундами на анкету, и
        # следующий тик завёл бы второй обход, пока идёт первый.
        _mark(site_id, await _every(site_id))

        profiles = await _profiles(site_id)
        if not profiles:
            continue

        ran += 1
        base = str(await _setting(site_id, 'api_base')).strip()
        password = str(await _setting(site_id, 'api_password')).strip()
        if not base:
            continue

        # ⚠ В ленту пишем **до** обхода: он идёт секундами на анкету (тринадцать
        # анкет — десятки секунд), и человек должен видеть «идём за почтой» пока
        # оно идёт, а не после (`dating_feed.py`).
        # ⚠ Без многоточия в конце. Оно ставилось как «работа идёт», а читается
        # как обрезанный текст: человек решил, что строку режут, и пошёл искать
        # предел длины. Что работа идёт, видно по самой строке — итог её заменит.
        started = dating_feed_write(site_id, 'mail',
                                    f'Забираю письма: анкет {len(profiles)}')

        arrived = site_failed = fresh = 0
        for number, profile in enumerate(profiles):
            if number:
                await asyncio.sleep(DATING_MAIL_IN_PAUSE_SEC)

            # ⚠⚠ **Статус перечитываем перед самим походом.** Отбор выше взял
            # анкеты `status = 'online'`, но обход идёт по одной с паузой — сайт
            # считает частые заходы подозрением, — и список успевает устареть.
            #
            # Поймано живьём: смена кончилась на второй секунде обхода, и два
            # `GET /messages` ушли под анкетами, которые уже были офлайн. Вреда
            # в тот раз не вышло (токен жив, входа не было), но правило «под
            # спящей наружу не ходим» держится не на везении.
            if not await dating_ready_online(int(profile['id'])):
                continue

            seen += 1
            got = await _fetch(profile, base, password)
            if got is None:
                failed += 1
                site_failed += 1
            else:
                arrived += int(got.get('added') or 0)
                fresh += int(got.get('fresh') or 0)
                added += int(got.get('added') or 0)

        # ⚠ Итог **заменяет** строку «забираю», а не встаёт второй: без него на
        # экране оставалось бы вечное «забираю письма», которое читается как
        # незаконченная работа. Вторая же строка забивала бы полосу одной почтой.
        #
        # ⚠ Пишем и пустой итог: «новых нет» — тоже ответ, и молчание вместо него
        # оставляло бы человека гадать, дошёл ли круг до конца.
        # ⚠ **«Привезено» и «свежих» — разные числа, и врозь их пишем не зря.**
        # Забор берёт письма от последнего известного минус запас, а первый заход —
        # за неделю: «новых 5» почти всегда значит пять писем, каких у нас не было,
        # а не пять только что пришедших. Человек это проверяет сразу — открывает
        # входящие и находит там позавчерашнее.
        if not arrived:
            summary = 'Писем не пришло'
        elif fresh:
            summary = f'Писем: {arrived}, свежих {fresh}'
        else:
            summary = f'Писем привезено: {arrived} — все старые'

        dating_feed_done(site_id, started, 'warn' if site_failed and not arrived else 'mail',
                         summary + (f' · с отказом анкет: {site_failed}' if site_failed else ''))

    if added or failed:
        logger_info(f'[dating] круг писем: анкет {seen}, новых {added}'
                    + (f', с отказом {failed}' if failed else ''))

    return {'sites': ran, 'profiles': seen, 'added': added, 'failed': failed}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _sites() -> list[dict]:
    """Сайты с включённым забором писем."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id FROM dating_site'
                             ' WHERE is_gone = 0 AND mail_check_on = 1'
                             ' ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для забора писем не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _profiles(site_id: int) -> list[dict]:
    """Анкеты сайта, под которыми можно сходить за письмами.

    ⚠ **Расписание не спрашиваем.** Письмо приходит, когда его написали; смена
    решает, когда на него ответят, а не когда его увидят.

    ⚠⚠ **Зато обязателен онлайн** (`status = 'online'`), и это исправление живой
    беды — третье место с одним и тем же промахом.

    Забор писем идёт **под самой девушкой**: `POST /auth/login` её логином, потом
    `GET /messages`. Прежде условие было одно (`enabled`), и круг обходил все
    тринадцать анкет каждые две минуты, не глядя на то, что все они офлайн. Пока
    вход получался, это стоило лишних запросов; когда общий пароль перестал
    подходить — стало тринадцатью неудачными входами каждые две минуты, то есть
    девятью тысячами в сутки.

    Этим мы сами и держали блокировку: счётчик у сайта суточный, и опустеть он не
    мог, пока мы стучались. Спящая анкета писем не прочтёт всё равно — их прочтёт
    та, что выйдет в смену, и прочтёт все разом.

    Тот же довод и то же правило, что у списков собеседников
    (`dating_people._hold_why`), избранных (`dating_favorite_round`) и инвайтов.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, site_id, login, password, password_source'
                ' FROM dating_profile'
                ' WHERE site_id = %s AND enabled = 1 AND login <> %s'
                "   AND status = 'online'"
                ' ORDER BY id', (int(site_id), ''))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] анкеты сайта #{site_id} для писем не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _fetch(profile: dict, base: str, password: str) -> dict | None:
    """Забирает письма одной анкеты. `None` — отказ; круг идёт к следующей.

    ⚠ Отказ одной анкеты не должен уносить остальных: у неё мог протухнуть пароль,
    а у соседних всё в порядке. В журнал он попадает через `dating_mail_in_fetch`,
    отдельной строки здесь не заводим — иначе сломанная анкета писала бы её каждые
    пять минут круглые сутки.
    """
    try:
        return await dating_mail_in_fetch(profile, base, password)
    except DatingMailInError:
        return None
    except Exception as e:
        logger_info(f"[dating] письма анкеты #{profile.get('id')} сорвались: {e}")

        return None


async def _setting(site_id: int, key: str) -> str:
    from src.uvicorn.routers.dating.dating_site import dating_site_setting

    return await dating_site_setting(site_id, key)


async def _every(site_id: int) -> int:
    """Как часто ходить за письмами этого сайта, в секундах."""
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id, DATING_MAIL_IN_EVERY_SETTING)
               or DATING_MAIL_IN_EVERY_DEFAULT)


def _due(site_id: int) -> bool:
    """Пора ли идти. ⚠ Сбой Redis решается в сторону бездействия.

    Не прочиталась отметка — не идём. Лишний обход это тринадцать входов на сайт
    подряд, а сайт считает частые заходы подозрением; пропущенный стоит пяти минут
    задержки письма. Цена ошибок несимметрична.
    """
    try:
        return not redis_conn_get().get(
            DATING_MAIL_IN_ROUND_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] отметка писем не прочиталась: {e}')

        return False


def _mark(site_id: int, every: int) -> None:
    """Отмечает обход. Срок отметки **и есть** настройка: Redis погасит её сам."""
    try:
        redis_conn_get().setex(
            DATING_MAIL_IN_ROUND_KEY.format(site_id=int(site_id)),
            max(int(every), 1), '1')
    except Exception as e:
        logger_info(f'[dating] отметка писем не поставилась: {e}')
