"""Круг исполнения намерений: собирает черновики по обещаниям агента.

Ходит по сайтам с поднятой галочкой «выполнять действия», берёт открытые
намерения и складывает письма со снимками в `intention_mail`
(`intention_do.py`). Уносит их потом общий круг отправки.

## ⚠⚠ Расписания здесь нет, и это не забывчивость

У соседних занятий смены отвечают на вопрос «когда **сочиняем**»: сочинение
стоит вызовов LLM, и ночью платить за него незачем. Здесь сочинять нечего —
текст письма приехал вместе с намерением, из того же прогона, что его нашёл.
Заход стоит запроса к базе и похода на Диск за снимком, причём только когда
обещание есть.

Зато есть причина ходить **круглые сутки**: обещание даётся в живом разговоре,
и письмо через восемь часов читается как забывчивость. Смены отложили бы
исполнение до утра ровно для тех пар, которые говорили ночью.

## ⚠ Срок — не про частоту писем

Сколько бы круг ни ходил, письмо не уйдёт чаще, чем позволит круг отправки со
своим `intention_send_every` и общей паузой между письмами анкеты. Свой срок
здесь про нагрузку: заход идёт на Диск за каждым снимком, и будить его каждую
минуту незачем — обещания появляются куда реже.

⚠ **Когда ходили — помним в Redis**, а не в памяти процесса: смена держателя
перезапускается (свой срок, выкладка), и после каждого перезапуска круг заходил
бы заново. Тот же приём, что у писем избранным и забора почты.
"""

from logging_.logging_ import logger_info

from src.dating.intention_do import intention_mail_make
from src.dating.round import round_sites, round_sites_run, round_slot_due
from src.dating.site_setting import site_seconds

# Как часто круг просыпается. ⚠ Это **не** частота захода: её держит слот, а тик
# лишь смотрит, не пора ли.
INTENTION_ROUND_TICK_SEC = 60

# Настройка срока захода по сайту.
INTENTION_ROUND_EVERY_SETTING = 'intention_make_every'

# Сколько ждать между заходами, если настройки нет. Пять минут: обещания
# появляются по одному и редко, а заход идёт на Диск.
INTENTION_ROUND_EVERY_DEFAULT = 300

# Отметка «на этот сайт только что ходили». ⚠ Метка `{id}`, а не `{site_id}`:
# чужая осталась бы в ключе текстом, то есть одним общим слотом на все сайты.
INTENTION_ROUND_KEY = 'dating:intention:round:{id}'


async def intention_round_run(offset: int = 0) -> dict:
    """Один круг: собрать черновики тем сайтам, кому пора.

    Args:
        offset: сдвиг часового пояса панели. ⚠ Принимается ради единообразия с
            прочими кругами и **не используется**: расписания у занятия нет.

    Returns:
        dict: `sites` — сколько сайтов обошли, `made` — сколько писем собрано,
        `skipped` — сколько обещаний исполнить не вышло.
    """
    sites = await _sites()
    ran = made = skipped = 0

    for _, got in await round_sites_run(sites, offset_hours=offset, gate=_gate,
                                        every=_every, work=_work,
                                        key=INTENTION_ROUND_KEY,
                                        what='исполнению обещаний'):
        if got is not None:
            ran += 1
            made += got['made']
            skipped += got['skipped']

    if made or skipped:
        logger_info(f'[dating] круг обещаний: сайтов {ran}, собрано {made}, '
                    f'пропущено {skipped}')

    return {'sites': ran, 'made': made, 'skipped': skipped}



async def _sites() -> list[dict]:
    """Сайты, где разрешено исполнять намерения.

    ⚠ Галочка та же, что спрашивает код исполнения
    (`chat_intention_do_allowed`), и других выключателей у занятия нет: вторая
    отвечала бы на тот же вопрос.
    """
    return await round_sites(flags=('chat_intention_do_on',),
                             extra=('chat_intention_do_on',),
                             what='исполнению обещаний')


async def _gate(site: dict, minutes: int) -> bool:
    """Пора ли заходить. Расписание не спрашивается — его у занятия нет."""
    return round_slot_due(INTENTION_ROUND_KEY, int(site['site_id']),
                          'исполнению обещаний',
                          every=await _every(int(site['site_id'])))


async def _every(site_id: int) -> int:
    """Срок захода по сайту, секунды."""
    return await site_seconds(site_id, INTENTION_ROUND_EVERY_SETTING,
                              INTENTION_ROUND_EVERY_DEFAULT)


async def _work(site: dict, minutes: int) -> dict:
    """Собрать черновики этого сайта."""
    return await intention_mail_make(int(site['site_id']))
