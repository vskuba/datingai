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
from src.dating.round import (round_sites, round_sites_run, round_slot_due,
                              round_slot_mark)

from src.dating.mail_reply import mail_reply_make
from src.dating.site_setting import site_seconds

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
REPLY_ROUND_KEY = 'dating:reply:round:{id}'


async def reply_round_run() -> dict:
    """Один круг: добрать пул ответов на каждом сайте с включённой генерацией.

    Returns:
        dict: `sites` — сайтов взято в работу, `made` — сколько ответов написано.
    """
    sites = await _sites()
    ran = made = 0
    for _, result in await round_sites_run(sites, gate=_gate, every=_every,
                                         work=_work, key=REPLY_ROUND_KEY,
                                         what='ответам'):
        if result is not None:
            ran += 1
            made += int(result.get('made') or 0)

    if made:
        logger_info(f'[dating] круг ответов: сайтов {ran}, написано {made}')

    return {'sites': ran, 'made': made}


async def _gate(site: dict, minutes: int) -> bool:
    """Пора ли отвечать этому сайту: вышел ли срок круга.

    ⚠ Сбой — молчим (правило общее в `round_slot_due`): лишний заход —
    вызовы LLM на каждый ответ.
    """
    return round_slot_due(REPLY_ROUND_KEY, int(site['site_id']), 'ответам')


async def _work(site: dict, minutes: int) -> dict | None:
    """Отвечает на входящие этого сайта; отказ одной анкеты не уносит остальных.

    Черновик ответа уходит по своему сроку, и лента видит работу до её итога
    (`feed.py`): строка-итог заменяет «отвечаем», а не встаёт второй.
    """
    site_id = int(site['site_id'])

    try:
        result = await mail_reply_make(site_id, await _batch(site_id))
    except Exception as e:
        logger_info(f'[dating] круг ответов, сайт #{site_id}: {e}')

        return None

    if result.get('made'):
        feed_write(site_id, 'reply',
                          f"Готовы ответы на письма: {result['made']}")

    return result
# ── Приватное ────────────────────────────────────────────────────────────────

async def _sites() -> list[dict]:
    """Сайты с включённой генерацией ответов."""
    return await round_sites(flags=('reply_make_on',), what='ответам')


async def _every(site_id: int) -> int:
    """Как часто добирать пул этому сайту, в секундах."""
    return int(await site_seconds(site_id, REPLY_EVERY_SETTING)
               or REPLY_EVERY_DEFAULT)


async def _batch(site_id: int) -> int:
    """Сколько ответов пишем за заход.

    ⚠ Второго потолка в коде нет, и он не нужен: `site_seconds` принимает
    **только значения из набора** реестра — впиши кто-нибудь в базу «500», и
    вернётся умолчание. Свой `min()` поверх этого был бы защитой от того, чего
    уже не бывает, и создавал бы вид, что настройку можно задать любую.
    """
    return int(await site_seconds(site_id, REPLY_BATCH_SETTING)
               or REPLY_BATCH_DEFAULT)


