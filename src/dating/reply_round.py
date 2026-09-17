"""Круг ответов: пул набирается сам, пока включена генерация.

До сих пор ответы набирала **только кнопка** «Написать ответы». Выключатель
`reply_make_on` при этом решал, чьи письма брать, — но запускал набор человек, и
без нажатия пул стоял пустым, сколько бы писем ни пришло.

Этот круг и делает генерацию тем, чем её называет выключатель.

## ⚠ Чем отличается от круга рассылки

Рассылка **начинает** разговор: письма готовятся тем, кто сейчас в сети, и их
можно набрать полсотни впрок. Ответ отвечает на пришедшее письмо — его нельзя
«набрать заранее», он появляется ровно тогда, когда появилось письмо.

Отсюда два следствия:

**Потолка пула нет.** Сколько писем пришло без ответа, столько ответов и нужно;
ограничивать их числом значило бы молча оставить кого-то без ответа. Предел
ставит `reply_batch` — но это предел **одного захода**, а не запаса.

**Расписание не спрашивается.** Рассылка уходит в часы смен, потому что письмо в
четыре утра выдаёт робота. Здесь же готовится черновик, который никуда не уходит
без второго выключателя: набирать его можно когда угодно, а отправит его круг
отправки — в свои часы.

⚠ **Набор стоит вызовов LLM**, поэтому срок между заходами (`reply_every`) держим
в Redis: смена держателя перезапускается, и память процесса сбрасывалась бы вместе
с ней — после каждого деплоя круг набирал бы заново. Тот же приём, что у рассылки.
"""

from logging_.logging_ import logger_info

from src.dating.feed import feed_write
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get

from src.dating.mail_reply import mail_reply_make

# Как часто круг просыпается. ⚠ Не частота набора: её задаёт `reply_every`.
REPLY_ROUND_TICK_SEC = 60

# Как часто набирать, если у сайта не задано. Пять минут: письма приходят редко, а
# заход стоит вызовов LLM на каждый ответ.
REPLY_EVERY_DEFAULT = 300

# Сколько ответов пишем за один заход. ⚠ Предел **захода**, а не запаса: заход,
# пишущий три десятка ответов, длится полчаса, и круг всё это время занят.
REPLY_BATCH_DEFAULT = 5

# Настройки сайта.
REPLY_EVERY_SETTING = 'reply_every'
REPLY_BATCH_SETTING = 'reply_batch'

# Отметка «этот сайт только что набирали». В Redis — переживает смену держателя.
REPLY_ROUND_KEY = 'dating:reply:round:{site_id}'


async def reply_round_run() -> dict:
    """Один круг: добрать пул ответов на каждом сайте с включённой генерацией.

    Returns:
        dict: `sites` — сайтов взято в работу, `made` — сколько ответов написано.
    """
    sites = await _sites()
    if not sites:
        return {'sites': 0, 'made': 0}

    ran = made = 0
    for site_id in sites:
        if not _due(site_id):
            continue

        # ⚠ Отметку ставим **до** набора: он длится минутами, и следующий тик
        # завёл бы второй, пока идёт первый. Тот же приём, что у рассылки.
        _mark(site_id, await _every(site_id))

        try:
            result = await mail_reply_make(site_id, await _batch(site_id))
        except Exception as e:
            logger_info(f'[dating] круг ответов, сайт #{site_id}: {e}')

            continue

        ran += 1
        made += int(result.get('made') or 0)

        if result.get('made'):
            feed_write(site_id, 'reply',
                              f"Готовы ответы на письма: {result['made']}")

    if made:
        logger_info(f'[dating] круг ответов: сайтов {ran}, написано {made}')

    return {'sites': ran, 'made': made}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _sites() -> list[int]:
    """Сайты с включённой генерацией ответов."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id FROM site'
                             ' WHERE is_gone = 0 AND reply_make_on = 1'
                             ' ORDER BY site_id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] сайты для ответов не спросились: {e}')

        return []

    return [int(row['site_id']) for row in rows or []]


async def _every(site_id: int) -> int:
    """Как часто добирать пул этому сайту, в секундах."""
    from src.uvicorn.routers.dating.site_ import site_seconds

    return int(await site_seconds(site_id, REPLY_EVERY_SETTING)
               or REPLY_EVERY_DEFAULT)


async def _batch(site_id: int) -> int:
    """Сколько ответов пишем за заход.

    ⚠ Второго потолка в коде нет, и он не нужен: `site_seconds` принимает
    **только значения из набора** реестра — впиши кто-нибудь в базу «500», и
    вернётся умолчание. Свой `min()` поверх этого был бы защитой от того, чего
    уже не бывает, и создавал бы вид, что настройку можно задать любую.
    """
    from src.uvicorn.routers.dating.site_ import site_seconds

    return int(await site_seconds(site_id, REPLY_BATCH_SETTING)
               or REPLY_BATCH_DEFAULT)


def _due(site_id: int) -> bool:
    """Пора ли набирать. ⚠ Сбой Redis решается в сторону бездействия.

    Не прочиталась отметка — не идём: лишний заход стоит вызовов LLM на каждый
    ответ, а пропущенный — пяти минут ожидания. Цена ошибок несимметрична.
    """
    try:
        return not redis_conn_get().get(
            REPLY_ROUND_KEY.format(site_id=int(site_id)))
    except Exception as e:
        logger_info(f'[dating] отметка ответов не прочиталась: {e}')

        return False


def _mark(site_id: int, every: int) -> None:
    """Отмечает заход. Срок отметки **и есть** настройка: Redis погасит её сам."""
    try:
        redis_conn_get().setex(REPLY_ROUND_KEY.format(site_id=int(site_id)),
                               max(int(every), 1), '1')
    except Exception as e:
        logger_info(f'[dating] отметка ответов не поставилась: {e}')
