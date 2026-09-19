"""Круг инициативы: набирает пул и уносит готовое в чат.

## ⚠⚠ Один круг, но два выключателя

Круг один: набор и отправка идут в одном тике, потому что сроки у них общие и
делить их по разным тикам незачем.

А флага **два**, и это не избыточность:

| Флаг | Что разрешает |
|------|---------------|
| `chat_initiative_on` | набирать пул — карточки сочиняются |
| `chat_initiative_send_on` | отправлять из него — карточки уходят людям |

Оба сняты по умолчанию. Инициатива требует такого разделения сильнее прочих
занятий: текст сочиняется под тишину, приёмом из жребия — флирт, подколка, чёрный
юмор, — и увидеть, что именно агент понял под «подколи по-доброму», надо **до**
того, как оно ушло живому человеку.

Порядок включения поэтому такой: поднять набор, прочитать пул глазами, и только
потом поднять отправку.

## Что круг делает за тик

1. Снимает протухшее: собеседник ответил — карточка стала вредна.
2. Набирает пул затихшим парам (`initiative.py`).
3. Уносит готовое тем, кто **сейчас** в чате.

⚠ **Порядок именно такой.** Чисти мы протухшее после набора, круг успел бы
отправить карточку тому, кто ответил секунду назад, — а её текст сочинялся под
тишину и прозвучал бы мимо разговора.

## ⚠ Кому отправляем

Только тем, кто в сети: реплика зовёт того, кто **сейчас** в чате. Нет его — она
ждёт в пуле, ровно как черновик ответа (`chat_reply`).
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.chat_initiative import (chat_initiative_failed,
                                        chat_initiative_ready,
                                        chat_initiative_sent,
                                        chat_initiative_stale)
from src.dating.initiative import initiative_make
from src.dating.log import log_write
from src.dating.online import online_check
from src.dating.send import send_message

# Как часто круг просыпается. Минута — как у автоответа: чаще незачем, сроки
# инициативы считаются десятками минут.
INITIATIVE_ROUND_TICK_SEC = 60

# Сколько карточек заводим за тик на весь сайт. Каждая — вызов LLM; без предела
# первый же заход на затихшем сайте стоил бы сотен вызовов разом.
INITIATIVE_ROUND_MAKE = 5

# Сколько отправляем за тик. ⚠ Меньше, чем набираем: отправка идёт живым людям, и
# всплеск из десятка реплик за минуту виден со стороны.
INITIATIVE_ROUND_SEND = 3


async def initiative_round_run() -> dict:
    """Один круг по всем сайтам, где занятие включено хоть наполовину."""
    sites = await _sites()

    if not sites:
        return {'sites': 0, 'made': 0, 'sent': 0, 'stale': 0}

    made = sent = stale = 0

    for site_id, набор, отправка in sites:
        try:
            # ⚠ Чистка идёт **всегда**, независимо от флагов: карточка, ставшая
            # вредной, вредна и при снятой отправке — человек прочитает её в пуле
            # и не поймёт, почему панель собралась писать тому, кто только что
            # ответил сам.
            stale += await _stale_clean(site_id)

            if набор:
                got = await initiative_make(site_id, limit=INITIATIVE_ROUND_MAKE)
                made += int(got.get('made') or 0)

            if отправка:
                sent += await _send(site_id)
        except Exception as e:
            logger_info(f'[dating] круг инициативы, сайт #{site_id}: {e}')

    if made or sent or stale:
        logger_info(f'[dating] инициатива: набрано {made}, отправлено {sent}, '
                    f'снято протухших {stale}')

    return {'sites': len(sites), 'made': made, 'sent': sent, 'stale': stale}


async def _send(site_id: int) -> int:
    """Уносит готовые карточки тем, кто сейчас в чате."""
    sent = 0

    for card in await chat_initiative_ready(site_id):
        if sent >= INITIATIVE_ROUND_SEND:
            break

        uid = str(card['uid'])

        # ⚠ Нет в сети — не отправляем и **пометки не ставим**: он вернётся, и
        # карточка уйдёт. Ровно как черновик ответа.
        if not online_check(int(site_id), uid)['online']:
            continue

        profile = await _profile(int(card['profile_id']))
        if not profile:
            continue

        who = f"{card.get('name') or ''} {uid}".strip()
        result = await send_message(profile, await _presence(site_id), uid,
                                    str(card['body']))

        if result['ok']:
            sent += 1
            await chat_initiative_sent(int(card['id']))
            await log_write(int(card['profile_id']), 'out',
                            f"Инициатива → {who}: «{str(card['body'])[:300]}»")
        elif result.get('offline'):
            # Вышел между проверкой и отправкой — не ошибка, карточка ждёт дальше.
            continue
        else:
            await chat_initiative_failed(int(card['id']), result['error'])
            await log_write(
                int(card['profile_id']), 'fail',
                f"Инициатива не ушла → {who}: {result['error']}")

    return sent


async def _stale_clean(site_id: int) -> int:
    """Снимает карточки тех, кто успел ответить сам.

    ⚠⚠ **Текст сочинялся под тишину.** Уйдя после его реплики, «расскажи, как
    прошли выходные» прозвучит мимо разговора: он спросил о другом, а девушка
    будто не прочитала. Отвечать теперь будет автоответ — на сказанное.
    """
    async with mysql_get_db_async() as db:
        # Пара, у которой последняя реплика — входящая, а карточка всё ещё ждёт.
        await db.execute(
            'SELECT i.profile_id, i.uid FROM chat_initiative i'
            " WHERE i.site_id = %s AND i.status = 'draft'"
            '   AND EXISTS (SELECT 1 FROM message m'
            '                WHERE m.profile_id = i.profile_id'
            '                  AND m.sender_uid = i.uid'
            "                  AND m.direction = 'in'"
            # ⚠ `>=`, а не `>`: `created_at` здесь с точностью до секунды, и
            # реплика, пришедшая в ту же секунду, что легла карточка, при
            # строгом сравнении осталась бы незамеченной. Сторона ошибки
            # выбрана нарочно: снять лишнюю карточку дёшево — круг наберёт
            # новую, — а отправить мимо разговора нельзя.
            '                  AND m.created_at >= i.created_at)',
            (int(site_id),))
        rows = await db.fetchall() or []

    gone = 0
    for row in rows:
        gone += await chat_initiative_stale(int(row['profile_id']),
                                            str(row['uid']))

    return gone


async def _sites() -> list[tuple]:
    """Сайты и их флаги: `(номер, набирать ли, отправлять ли)`.

    ⚠ Берём сайт, где поднят **хоть один** флаг: при снятом наборе и поднятой
    отправке круг доносит то, что уже лежит в пуле, — а это законный порядок
    работы («набрали, выключили набор, отправляем прочитанное»).

    ⚠ Оба сняты по умолчанию: круг пишет живым людям первым, и накат миграции,
    сам разрешивший это, был бы худшим из сюрпризов.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT site_id, chat_initiative_on, chat_initiative_send_on'
                '  FROM site'
                ' WHERE chat_initiative_on = 1 OR chat_initiative_send_on = 1')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] выключатели инициативы не прочитались: {e}')

        return []

    return [(int(row['site_id']), bool(row['chat_initiative_on']),
             bool(row['chat_initiative_send_on'])) for row in (rows or [])]


async def _profile(profile_id: int) -> dict:
    """Анкета, от чьего имени уйдёт реплика."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    return dict(row) if row else {}


async def _presence(site_id: int) -> dict:
    """Настройка присутствия сайта — из неё берётся транспорт отправки."""
    from src.dating.site_presence import site_presence

    try:
        return await site_presence(int(site_id)) or {}
    except Exception as e:
        logger_info(f'[dating] настройка присутствия #{site_id}: {e}')

        return {}
