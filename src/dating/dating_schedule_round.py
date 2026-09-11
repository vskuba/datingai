"""Круг расписания: воркер водит анкету по сменам.

Раз в минуту смотрит, кому пора быть в сети, и приводит состояние к расписанию:
смена началась — вход сценарием (после него держатель поднимет сокет сам), смена
кончилась — выход, и сокет отпускается.

## ⚠ Сокет не поднимается и не гасится напрямую

Вход сценарием заканчивается тем, что браузер подсматривает адрес чат-сокета и
кладёт его в анкету; держатель подхватывает адрес в течение минуты. Выход гасит
адрес — держатель следующим кругом отпускает соединение.

Дёргать сокет отдельно значило бы получить соединение по мёртвой сессии или живую
сессию без соединения. Поэтому круг управляет **входом**, а сокет — следствие.

Исключение одно, и оно про немедленность: **расписание выключили, пока сокет
жив** — тогда рвём сразу, не дожидаясь конца смены. Человек снял галочку, чтобы
анкета перестала работать сейчас, а не через сорок минут.

## Почему вход не мгновенный

Сценарий входа — минуты живого браузера. Смена, начавшаяся в 07:00, застанет анкету
в сети к 07:01–07:02. Это приемлемо: расписание про часы, а не про минуты.

⚠ **Входы идут по одному.** Каждый открывает вкладку Chromium у соседа, и десяток
разом уронил бы его пул. Тот же порядок, что у круга восстановления входов.
"""

import time

from datetime_.datetime_ import datetime_offset_hours
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_log import dating_log_write
from src.dating.dating_pool import dating_pool_drop
from src.dating.dating_schedule import dating_schedule_now

# Как часто круг просыпается. Минута: смены заданы с точностью до четверти часа, и
# чаще смотреть нечего — а реже значило бы входить с опозданием до пяти минут.
DATING_SCHEDULE_ROUND_TICK_SEC = 60

# Сколько ждать между входами. ⚠ Не троттлинг, а пауза после действия: вход только
# что занял вкладку у соседа, и следующий стоит начинать, когда та освободилась.
DATING_SCHEDULE_ROUND_GAP_SEC = 5


async def dating_schedule_round_run(offset_hours: int = 0) -> dict:
    """Один круг: привести состояние анкет к расписанию.

    Args:
        offset_hours: часовое смещение панели (настройка `timezone`). Расписание
            задано местным временем, а сервер живёт по UTC.

    Returns:
        dict: `checked`, `entered`, `left`, `dropped` — сколько проверено, вошло,
        вышло и сколько сокетов оборвано из-за снятой галочки.
    """
    rows = await _rows()
    minutes = _minutes_now(offset_hours)

    entered = left = dropped = 0

    for row in rows:
        profile_id = int(row['id'])
        в_сети = str(row['status'] or '') == 'online'
        есть_сокет = bool(str(row['ws_url'] or '').strip())
        # ⚠ **Живая сессия — тоже «есть что отпускать».** Присутствие держится на
        # `storage_state`, а не на статусе и не на сокете: пока он лежит, анкета
        # остаётся вошедшей на сайте. Прежде конец смены смотрел только на статус
        # и сокет — и у анкеты, чей статус сбился в `error`, выход не срабатывал
        # вовсе: сессия оставалась жить, а точка в панели не гасла.
        есть_сессия = bool(row.get('storage_state'))

        # ⚠ **Выключенную анкету круг отпускает**, даже если расписание включено:
        # «не ведём» значит не ведём совсем (`dating.md`, §5.11).
        if not bool(row['enabled']):
            if есть_сокет or в_сети or есть_сессия:
                if await _leave(profile_id, 'анкета выключена'):
                    dropped += 1

            continue

        пора = dating_schedule_now(row['schedule'], minutes)

        if пора and not в_сети:
            if await _enter(profile_id):
                entered += 1

            continue

        if not пора and (в_сети or есть_сокет or есть_сессия):
            if await _leave(profile_id, 'смена кончилась'):
                left += 1

    if entered or left or dropped:
        logger_info(f'[dating] круг расписания: вошло {entered}, вышло {left}, '
                    f'оборвано {dropped}')

    return {'checked': len(rows), 'entered': entered, 'left': left, 'dropped': dropped}


async def dating_schedule_round_drop(profile_id: int) -> bool:
    """Отпускает анкету сейчас же: гасит адрес сокета и вход.

    Зовётся ручкой, когда человек снял галочку расписания. ⚠ **Именно ручкой, а не
    кругом**: только она знает, что переход случился. Круг видит лишь текущее
    состояние, и отпуская всё без галочки, он сбрасывал бы ручные входы — так и
    вышло на живой базе, семь анкет.

    Returns:
        bool: было ли что отпускать. `False` — анкета и так не в сети, и говорить
        человеку «сокет снят» было бы неправдой.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute("SELECT status, ws_url FROM dating_profile WHERE id = %s",
                             (int(profile_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] состояние #{profile_id} не прочиталось: {e}')

        return False

    if not row:
        return False

    держится = str(row['status'] or '') == 'online' or bool(str(row['ws_url'] or '').strip())
    if not держится:
        return False

    return await _state_clear(int(profile_id))


# ── Приватное ────────────────────────────────────────────────────────────────

async def _rows() -> list[dict]:
    """Анкеты, которые круг ведёт или обязан отпустить.

    ⚠ **Только с включённым расписанием**, и это исправление настоящей ошибки.
    Сперва круг брал всех, у кого жив сокет, — чтобы «выключенное расписание рвало
    соединение сразу». Получилось, что **ручной вход становится невозможен**:
    нажали «Войти», через минуту круг вышел, потому что галочки нет. Проверено на
    живой базе — круг сбросил вход у семи анкет, залогиненных руками.

    Обрыв при снятии галочки делает теперь **ручка** `PUT /{id}/schedule-on`: она
    знает, что переход случился, и рвёт мгновенно, а не через минуту. Круг же
    отвечает ровно за то, что ему поручили, — за анкеты с включённым расписанием.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, login, schedule, schedule_on, enabled, status, ws_url,'
                '       storage_state IS NOT NULL AS storage_state'
                '  FROM dating_profile WHERE schedule_on = 1 ORDER BY id')
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] анкеты для круга расписания не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


def _minutes_now(offset_hours: int) -> int:
    """Минуты от начала **местных** суток.

    Расписание сверяют с часами на стене, а сервер живёт по UTC: «войти в 7 утра»
    человек понимает буквально.
    """
    local = time.gmtime(time.time() + int(offset_hours) * 3600)

    return local.tm_hour * 60 + local.tm_min


async def _enter(profile_id: int) -> bool:
    """Вход по расписанию. Не бросает: один сорванный вход не должен лишать
    остальных."""
    from src.dating.dating_login import DatingLoginError, dating_login_run

    try:
        await dating_log_write(profile_id, 'info', 'Расписание: смена началась, вхожу')
        result = await dating_login_run(profile_id)
    except DatingLoginError as e:
        await dating_log_write(profile_id, 'fail', f'Расписание: войти не вышло — {e}')

        return False
    except Exception as e:
        logger_info(f'[dating] расписание, вход #{profile_id}: {e}')

        return False

    if str(result.get('status')) == 'error':
        await dating_log_write(profile_id, 'fail',
                               f"Расписание: войти не вышло — {result.get('error')}")

        return False

    return True


async def _leave(profile_id: int, why: str) -> bool:
    """Выход по расписанию: гасим адрес сокета и снимаем вход.

    ⚠ **Полного отключения (`dating_disconnect_run`) здесь нет намеренно.** Оно
    требует собранного контекста — сессий у соседа, сценария выхода, — и собирает
    его роутер: в воркере FastAPI нет вовсе. Тащить сборку сюда значило бы завести
    вторую копию того, что уже расходилось однажды.

    Здесь достаточно нашей половины: гасим `ws_url` и `storage_state`. Держатель
    отпускает соединение по пустому адресу следующим кругом — это и делает выход
    немедленным.

    ⚠ **Сессия у соседа остаётся жить** до своего срока. Это приемлемо: она сама
    закроется, а следующий вход её и так выбросит (сайт держит одну на анкету).
    Разница в цене: вкладка у соседа стоит памяти, но не действий на сайте, — а
    висящий сокет означал бы, что анкета «в сети» вне смены, то есть ровно то, от
    чего расписание и уводит.
    """
    await dating_log_write(profile_id, 'info', f'Расписание: {why}, выхожу')

    return await _state_clear(profile_id)


async def _state_clear(profile_id: int) -> bool:
    """Гасит адрес сокета и вход. Держатель отпустит соединение следующим кругом.

    `status` ставим `offline` сразу: иначе круг увидел бы анкету «в сети» и вышел
    из неё второй раз через минуту.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE dating_profile SET ws_url = '', storage_state = NULL, "
                             "       status = 'offline' WHERE id = %s",
                             (int(profile_id),))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] выход по расписанию #{profile_id} не записался: {e}')

        return False

    # ⚠ Черновики уходят вместе с анкетой. Потолок пула — **на сайт**, и письма,
    # которые не могут уйти (отправка ждёт анкету в сети), занимали бы его до её
    # следующей смены: сайт молчал бы целиком, хотя в сети есть кому писать
    # (`dating_pool.py`).
    await dating_pool_drop(profile_id, 'смена кончилась')

    return True
