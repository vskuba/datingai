"""Отключение учётки: выйти с сайта и перестать её вести.

Отключение — не удаление. Анкета остаётся в списке со своим журналом и перепиской,
но перестаёт существовать для системы: ни опроса присутствия, ни держателя сокетов,
ни живых вкладок браузера. Вернуть её в работу — это «Залогиниться», то есть тот же
путь, каким она в работу попала впервые.

**Снятый вход и есть выключатель.** Держатель сокетов берёт учётки с непустым
`storage_state`, опрос присутствия — тоже; стёртое состояние убирает её из обоих
разом. Отдельный столбец «включена» означал бы два источника истины об одном и том
же, и рано или поздно они разошлись бы — учётка без входа, но «включённая».

Порядок шагов обязателен: сначала выход на сайте, потом стирание состояния. Наоборот
не выйдет — выходить будет нечем, cookie уже стёрты, и сессия останется висеть на
стороне сайта до своего срока.

Выход на сайте необязателен и не считается провалом: сценарий может быть не заведён,
сайт — не ответить, вход — уже протухнуть. Локальную часть это не отменяет, иначе
учётку нельзя было бы отключить именно тогда, когда это нужнее всего.
"""

from mysql_.mysql_ import mysql_get_db_async

from logging_.logging_ import logger_info
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_scenario_find

from src.dating.dating_json import dating_json_load
from src.dating.dating_log import dating_log_write
from src.dating.dating_pool import dating_pool_drop
from src.dating.dating_role import DATING_ROLE_DEFAULT, dating_role_scenario

# Сколько ждать сценарий выхода. Это несколько кликов по уже открытому кабинету;
# не уложился — значит кабинет не поднялся, и ждать дальше нечего.
DATING_DISCONNECT_TIMEOUT = 120.0


async def dating_disconnect_run(profile: dict, site: dict, presence: dict,
                                runner, session_open, session_list, session_close) -> dict:
    """Отключает учётку: выход на сайте, закрытие вкладок, снятие с мониторинга.

    Args:
        profile: строка `dating_profile`.
        site: строка `browser_site` этой учётки.
        presence: настройка сайта; `logout_scenario` — имя сценария выхода.
        runner: чем гонять сценарий. Принимает **запись сценария**, а не номер:
            журнал прогонов пишет имя рядом с номером (`dating_scenario.py`).
        session_open: как завести сессию браузера под этот вход.
        session_list: как получить живые сессии контейнера.
        session_close: как погасить сессию.

    Returns:
        dict: `ok`, `logout` — вышли ли на сайте, `closed` — сколько вкладок
        погасили, `error` — почему выход не удался (локальную часть не отменяет).
    """
    profile_id = int(profile['id'])
    await dating_log_write(profile_id, 'info', 'Отключение запущено')

    logout, error = await _logout(profile, site, presence, runner, session_open, session_list)
    closed = await _sessions_close(str(profile['login']), session_list, session_close)

    await _state_clear(profile_id)

    await dating_log_write(
        profile_id, 'ok',
        f"Отключено: {'выход на сайте выполнен' if logout else 'выход на сайте пропущен'}, "
        f'вкладок закрыто {closed}, опрос и чат остановлены')

    return {'ok': True, 'logout': logout, 'closed': closed, 'error': error}


async def _logout(profile: dict, site: dict, presence: dict, runner, session_open,
                  session_list) -> tuple[bool, str]:
    """Выход на самом сайте: сперва ручкой, а сценарием — только если её нет.

    ⚠ **Ручка старше сценария, и это не экономия секунд.** Выход у сайта обычно
    один запрос; сценарий ради него поднимает вкладку Chromium (~143 МБ) и стоит
    десятки секунд. Тот же размен, что у присутствия и у чтения панели: «найти
    HTTP-ручку вместо сценария».

    ⚠ **Что выход вообще делает — вопрос к сайту, а не к нам.** У Ladadate
    `GET /signout` отвечает `302` и `Set-Cookie: AUTH=; expires=вчера`, то есть
    гасит cookie **в браузере**, а сессию на своей стороне оставляет живой:
    сохранённый `AUTH` после него работает (проверено). Для нас это значит, что
    честный итог такого выхода — «cookie сброшены», а не «сессия закрыта»;
    состояние мы всё равно стираем сами, и оно и есть настоящий выход с нашей
    стороны.
    """
    # ⚠ **Шаг ищем в нашей настройке сайта, а соседская — запасная.** Присутствие
    # у нас своё (`heartbeat` на странице «Сайты»), и выход — его часть: он про то
    # же соединение и по тем же cookie. Спроси мы только соседа, настройка выхода
    # оказалась бы единственной, живущей не там, где остальные.
    from src.dating.dating_heartbeat import dating_heartbeat_config

    config = await dating_heartbeat_config(int(profile['site_id'])) or {}
    step = config.get('logout')
    if not (isinstance(step, dict) and step.get('path')):
        config, step = presence or {}, (presence or {}).get('logout')

    if isinstance(step, dict) and step.get('path'):
        return await _logout_http(profile, config, step)

    role = str(profile.get('role') or DATING_ROLE_DEFAULT)
    name = dating_role_scenario(presence, 'logout_scenario', role)
    if not name:
        return False, (f'у сайта нет ни ручки выхода (`logout` в настройке '
                       f'присутствия), ни сценария для роли «{role}»')

    scenario = await _scenario_find(int(profile['site_id']), name)
    if not scenario:
        return False, f'сценарий выхода «{name}» у сайта не заведён'

    if not profile.get('storage_state'):
        return False, 'вход не снят: выходить нечем'

    # Строка в журнал до самого выхода: сценарий идёт до минуты, и без неё
    # страница молчит всё это время, а человек думает, что кнопка не сработала.
    await dating_log_write(int(profile['id']), 'info', f'Выхожу на сайте сценарием «{name}»')

    # Своя вкладка этой учётки, если она жива: сайт держит одну сессию на анкету, и
    # выход из второй вкладки выбил бы первую, оставив её висеть.
    session_id = await _session_own(str(profile['login']), session_list)
    own = not session_id
    if own:
        try:
            session_id = await session_open(site, profile)
        except Exception as e:
            return False, f'сессия для выхода не завелась: {e}'

    try:
        result = await runner(scenario, session_id, {'login': str(profile['login'])})
    except Exception as e:
        return False, f'сценарий выхода не прошёл: {e}'

    if result.get('status') == 'error':
        return False, (f"сценарий выхода встал на шаге {result.get('step_no', 0)}: "
                       f"{str(result.get('error') or '')[:200]}")

    return True, ''


async def _logout_http(profile: dict, presence: dict, step: dict) -> tuple[bool, str]:
    """Выход одним запросом по сохранённым cookie. Без браузера и без соседа.

    Шаг описывается как у присутствия — `{path, method, body, headers}`
    (`docs-common/http_heartbeat.md`, §3), и берётся из той же настройки сайта.

    ⚠ **Ответ не проверяем на «вышли ли».** Сайт на выход отвечает переадресацией
    на главную, и `200` там значит ровно «запрос дошёл». Настоящая проверка —
    следующий опрос присутствия, и делать её здесь значило бы обещать больше, чем
    знаем.
    """
    from http_.http_heartbeat import http_heartbeat_client, http_heartbeat_cookies

    if not profile.get('storage_state'):
        return False, 'вход не снят: выходить нечем'

    base = str(presence.get('base') or '').rstrip('/')
    if not base:
        return False, 'у сайта не задан корень (`base`) — некуда слать выход'

    await dating_log_write(int(profile['id']), 'info',
                           f"Выхожу на сайте ручкой {step.get('path')}")

    client = http_heartbeat_client()
    try:
        answer = await client.request(
            str(step.get('method') or ('POST' if step.get('body') else 'GET')),
            base + str(step['path']),
            cookies=http_heartbeat_cookies(dating_json_load(profile['storage_state']), base),
            content=step.get('body') or None,
            headers={**(presence.get('headers') or {}), **(step.get('headers') or {})},
            follow_redirects=False)
    except Exception as e:
        return False, f'выход не прошёл: {type(e).__name__}: {e}'
    finally:
        await client.aclose()

    if answer.status_code >= 400:
        return False, f'сайт отказал на выходе: {answer.status_code}'

    return True, ''


async def _sessions_close(login: str, session_list, session_close) -> int:
    """Гасит все живые вкладки этой учётки — включая ту, в которой выходили."""
    try:
        sessions = await session_list()
    except Exception:
        return 0

    closed = 0
    for session in sessions:
        if str(session.get('name') or '') != str(login):
            continue
        try:
            await session_close(str(session.get('session_id') or ''))
            closed += 1
        except Exception:
            continue

    return closed


async def _session_own(login: str, session_list) -> str:
    """Живая вкладка этой учётки, если она есть."""
    try:
        sessions = await session_list()
    except Exception:
        return ''

    for session in sessions:
        if str(session.get('name') or '') == str(login):
            return str(session.get('session_id') or '')

    return ''


async def _scenario_find(site_id: int, name: str) -> dict | None:
    """Сценарий сайта по имени. Живёт он в RhapsodyAi — спрашиваем его.

    Нет такого — None: вызывающий скажет об этом человеку своими словами.
    """
    try:
        scenario = await rhapsody_api_scenario_find(int(site_id), str(name))
    except RhapsodyError as e:
        logger_info(f'[dating] сценарий «{name}» не спросился: {e}')
        return None

    return dict(scenario) if scenario else None


async def _state_clear(profile_id: int) -> None:
    """Стирает вход и адрес сокета — этим учётка и снимается с мониторинга.

    Переписка, журнал и анкета остаются: отключение это «не веди её», а не
    «забудь, что она была».
    """
    async with mysql_get_db_async() as db:
        # Снимаем и присмотр автомата: «Отключить» — это «не веди её», а не
        # «выйди и войди снова». Иначе смена вернула бы анкету в сеть через минуту.
        await db.execute("UPDATE profile SET storage_state = NULL, ws_url = '', "
                         "       status = 'offline', unread = 0, last_error = '', "
                         '       keep_online = 0, relogin_since = NULL, relogin_tries = 0, '
                         '       relogin_next_at = NULL WHERE id = %s', (int(profile_id),))
        await db.connection.commit()

    # ⚠ Черновики уходят вместе с анкетой: потолок пула — **на сайт**, и письма,
    # которые уже некому отправить, занимали бы его, пока анкету не включат
    # обратно (`dating_pool.py`). Переписка и журнал при этом остаются:
    # отключение — «не веди её», а не «забудь, что она была».
    await dating_pool_drop(profile_id, 'анкету отключили')
