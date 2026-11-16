"""Присутствие девушки кнопкой панели: агентство само включает её «в сети».

У Luxee кабинета под девушкой нет: одна панель агентства на всех, и «в сети»
девушку держит **сама панель** — пунктами «Set online» / «Set offline» в меню
её плитки. Входить под анкетой некуда и незачем; значит, вход по смене у такого
сайта — это нажатие «Set online», а выход — «Set offline».

## Кто нажимает

| Когда | Кто | Что |
|-------|-----|-----|
| смена началась, кнопка «Войти» | `login_run` → `panel_presence_enter` | «Set online» |
| смена кончилась, сняли галочку, выключили анкету | `schedule_round._state_clear` → `panel_presence_leave` | «Set offline» |
| раз в минуту | круг панели → `panel_presence_keep` | сверка с расписанием |

## ⚠⚠ Зачем сверка, если вход и выход нажимают сами

Панель меняет статус и без нас: у неё есть окно «Confirm your activity… I am
online» — бездействие оператора она замечает. А наш выход мог не дойти (сеть,
протухшая сессия): статус у нас погашен, круг расписания к анкете больше не
вернётся, а девушка горит в панели до утра. Сверка — то, что **держит**
расписание правдой с обеих сторон: читает статусы всех анкет сайта одним POST и
переключает расходящихся.

⚠ Сверка трогает **только анкеты с включённым расписанием**: без галочки
анкету ведут руками, и гасить её по часам значило бы отменить ручное решение —
та же граница, что у круга расписания (`schedule_round._rows`).

## ⚠ Своего входа в панель здесь нет

Сессию держит круг панели (`panel_round.py`). Не признала — отказ, а не вход:
два входа наперегонки — тот же довод, что у `panel_fetch`.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load
from src.dating.log import log_write
from src.dating.panel import PANEL_URL_SETTING
from src.dating.panel_fetch import (DatingPanelFetchError, panel_fetch_post)
from src.dating.schedule import schedule_now
from src.dating.site.site_registry import site_registry_panel
from src.dating.site_presence import site_presence
from src.dating.site_setting import site_setting


class DatingPanelPresenceError(RuntimeError):
    """Панель не переключила или не ответила. Текст годен для журнала анкеты."""


async def panel_presence_on(site_id: int) -> bool:
    """Держит ли этот сайт присутствие кнопкой панели.

    ⚠ Нужны оба условия: драйвер знает ручку **и** у сайта задан адрес панели
    — без адреса нет сессии, и обещать эту дорогу значило бы спрятать прежнюю.
    """
    if not site_id:
        return False

    if not str(await site_setting(int(site_id), PANEL_URL_SETTING) or '').strip():
        return False

    return bool((await _driver(int(site_id))).PRESENCE_SET_PATH)


async def panel_presence_enter(profile: dict) -> None:
    """«Set online» одной анкете; удача — анкета у нас `online`.

    Raises:
        DatingPanelPresenceError: номера нет, панель отказала или статус после
            нажатия не тот.
    """
    await _press(profile, True)
    await _status_set([int(profile['id'])], 'online')


async def panel_presence_leave(profile_id: int) -> None:
    """«Set offline» одной анкете. Не бросает: выход не должен срываться.

    ⚠ Неудачу пишем в журнал анкеты и идём дальше — свою половину выхода
    (`status`, сокет) вызывающий делает всё равно, а недошедшее нажатие
    выправит сверка (`panel_presence_keep`) на следующей минуте.
    """
    profile = await _profile(int(profile_id))
    if not profile or not await panel_presence_on(int(profile['site_id'])):
        return

    try:
        await _press(profile, False)
    except DatingPanelPresenceError as e:
        await log_write(int(profile_id), 'fail',
                        f'Панель: «Set offline» не прошло — {e}; выправит сверка')


async def panel_presence_keep(site_id: int, minutes: int) -> dict:
    """Сверка: статус каждой анкеты в панели — как велит её расписание.

    Args:
        site_id: сайт с присутствием кнопкой панели.
        minutes: минута местных суток — по ней решается «в смене ли».

    Returns:
        dict: `checked` — сколько анкет сверили, `on` / `off` — скольких
        включили и погасили, `error` — чем кончилось, если не вышло.
    """
    rows = [row for row in await _site_profiles(int(site_id)) if row['uid']]
    if not rows:
        return {'checked': 0, 'on': 0, 'off': 0, 'error': ''}

    driver = await _driver(int(site_id))
    try:
        now = await _read(int(site_id), driver, rows)
    except DatingPanelPresenceError as e:
        return {'checked': 0, 'on': 0, 'off': 0, 'error': str(e)}

    # Анкеты, которых панель не знает, выпадают из сверки целиком: переключать
    # их нечем, а в пачке они уронили бы и остальных.
    rows = [row for row in rows if row['uid'] in now]

    want = {row['uid']: bool(row['enabled']) and schedule_now(row['schedule'], minutes)
            for row in rows}

    done = {True: 0, False: 0}
    final = {row['uid']: bool(now.get(row['uid'])) for row in rows}
    for state in (True, False):
        uids = [uid for uid, wish in want.items()
                if wish is state and now.get(uid) is not state]
        if not uids:
            continue

        try:
            await _ask(int(site_id), driver, driver.PRESENCE_SET_PATH,
                       driver.presence_form(uids, state))
        except DatingPanelPresenceError as e:
            logger_info(f'[dating] сверка присутствия сайта #{site_id}: {e}')
            continue

        done[state] = len(uids)
        for row in rows:
            if row['uid'] in uids:
                final[row['uid']] = state
                await log_write(int(row['id']), 'info',
                                'Панель: расписание велит быть в сети — «Set online»'
                                if state else
                                'Панель: вне смены — «Set offline»')

    # Наш статус — по тому, что **теперь** в панели, а не по желаемому: круг
    # расписания смотрит на него и не должен входить в анкету, которую сверка
    # уже включила; а несработавшее нажатие не должно рисовать «в сети».
    await _status_set([int(row['id']) for row in rows if final[row['uid']]], 'online')
    await _status_set([int(row['id']) for row in rows if not final[row['uid']]],
                      'offline')

    return {'checked': len(rows), 'on': done[True], 'off': done[False], 'error': ''}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _driver(site_id: int):
    return site_registry_panel(await site_presence(int(site_id)))


async def _read(site_id: int, driver, rows: list[dict]) -> dict[str, bool]:
    """Статусы анкет: пачкой, а при отказе — по одной.

    ⚠⚠ Номер, которого панель не знает, роняет **весь** запрос `403` (замер
    16.11.2026: анкета 622339 есть у нас и нет в панели). Пачкой — дёшево, пока
    все свои; отказала — читаем по одной, и чужие получают строку в журнале, а
    не молчаливый вечный отказ сверки всему сайту.

    Raises:
        DatingPanelPresenceError: не прочиталась ни одна — значит дело не в
            номерах, а в сессии или панели.
    """
    try:
        return await _ask(site_id, driver, driver.PRESENCE_READ_PATH,
                          driver.presence_form([row['uid'] for row in rows]))
    except DatingPanelPresenceError as e:
        first = e

    got, strangers = {}, []
    for row in rows:
        try:
            got.update(await _ask(site_id, driver, driver.PRESENCE_READ_PATH,
                                  driver.presence_form([row['uid']])))
        except DatingPanelPresenceError:
            strangers.append(row)

    # ⚠ Не прочиталась ни одна — дело в сессии, а не в номерах: строка «панель
    # не знает анкету» всем двенадцати была бы ложью.
    if not got:
        raise first

    for row in strangers:
        await log_write(int(row['id']), 'fail',
                        f"Панель не знает анкету {row['uid']}: в присутствии "
                        'по расписанию она не участвует')

    return got


async def _press(profile: dict, online: bool) -> None:
    """Нажимает кнопку одной анкете и сверяет ответ панели."""
    uid = _uid(profile)
    if not uid:
        raise DatingPanelPresenceError('у анкеты нет номера на сайте')

    driver = await _driver(int(profile['site_id']))
    got = await _ask(int(profile['site_id']), driver, driver.PRESENCE_SET_PATH,
                     driver.presence_form([uid], online))

    if got.get(uid) is not online:
        raise DatingPanelPresenceError(
            f"панель ответила «{'в сети' if got.get(uid) else 'не в сети'}» "
            f"на «{'Set online' if online else 'Set offline'}»")


async def _ask(site_id: int, driver, path: str, form: dict) -> dict:
    """POST ручки присутствия и разбор ответа драйвером.

    ⚠ `files={}` — форма уходит строкой полей, как шлёт её `$.post` страницы:
    пустой словарь файлов многочастную кодировку не включает
    (`panel_fetch_post`).
    """
    try:
        raw = await panel_fetch_post(site_id, path, form, files={})
        return driver.presence_answer(raw)
    except (DatingPanelFetchError, ValueError) as e:
        raise DatingPanelPresenceError(str(e))


def _uid(profile: dict) -> str:
    """Номер анкеты на сайте: ключ карточки, иначе колонка.

    ⚠ Номер лежит в двух местах сразу (`profile_card.py`), и читают их разные
    места — берём оба, как `profile_panel`.
    """
    card = json_column_load(profile.get('profile')) or {}

    return str(card.get('id') or profile.get('profile_id') or '').strip()


async def _profile(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, profile FROM profile'
                         ' WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    return dict(row) if row else {}


async def _site_profiles(site_id: int) -> list[dict]:
    """Анкеты сайта под расписанием — их и сверяем (см. заголовок)."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, profile, enabled, schedule'
                         '  FROM profile WHERE site_id = %s AND schedule_on = 1'
                         ' ORDER BY id', (int(site_id),))
        rows = await db.fetchall() or []

    return [{**dict(row), 'uid': _uid(row)} for row in rows]


async def _status_set(profile_ids: list[int], status: str) -> None:
    """Ставит статус пачке анкет. ⚠ `last_seen_at` — только включённым."""
    if not profile_ids:
        return

    marks = ', '.join(['%s'] * len(profile_ids))
    seen = ', last_seen_at = NOW(), last_error = %s' if status == 'online' else ''
    args = [status] + ([''] if seen else []) + [int(one) for one in profile_ids]

    async with mysql_get_db_async() as db:
        await db.execute(f'UPDATE profile SET status = %s{seen}'
                         f' WHERE id IN ({marks})', tuple(args))
        await db.connection.commit()
