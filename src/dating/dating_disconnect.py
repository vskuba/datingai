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

from src.dating.dating_log import dating_log_write
from src.dating.dating_role import DATING_ROLE_DEFAULT, dating_role_scenario

# Сколько ждать сценарий выхода. Это несколько кликов по уже открытому кабинету;
# не уложился — значит кабинет не поднялся, и ждать дальше нечего.
DATING_DISCONNECT_TIMEOUT = 120.0


async def dating_disconnect_run(girl: dict, site: dict, presence: dict,
                                runner, session_open, session_list, session_close) -> dict:
    """Отключает учётку: выход на сайте, закрытие вкладок, снятие с мониторинга.

    Args:
        girl: строка `dating_girl`.
        site: строка `browser_site` этой учётки.
        presence: настройка сайта; `logout_scenario` — имя сценария выхода.
        runner: чем гонять сценарий (`rhapsody_api_scenario_run`).
        session_open: как завести сессию браузера под этот вход.
        session_list: как получить живые сессии контейнера.
        session_close: как погасить сессию.

    Returns:
        dict: `ok`, `logout` — вышли ли на сайте, `closed` — сколько вкладок
        погасили, `error` — почему выход не удался (локальную часть не отменяет).
    """
    girl_id = int(girl['id'])
    await dating_log_write(girl_id, 'info', 'Отключение запущено')

    logout, error = await _logout(girl, site, presence, runner, session_open, session_list)
    closed = await _sessions_close(str(girl['login']), session_list, session_close)

    await _state_clear(girl_id)

    await dating_log_write(
        girl_id, 'ok',
        f"Отключено: {'выход на сайте выполнен' if logout else 'выход на сайте пропущен'}, "
        f'вкладок закрыто {closed}, опрос и чат остановлены')

    return {'ok': True, 'logout': logout, 'closed': closed, 'error': error}


async def _logout(girl: dict, site: dict, presence: dict, runner, session_open,
                  session_list) -> tuple[bool, str]:
    """Выход на самом сайте — в живой вкладке этой учётки либо во временной."""
    role = str(girl.get('role') or DATING_ROLE_DEFAULT)
    name = dating_role_scenario(presence, 'logout_scenario', role)
    if not name:
        return False, f'у сайта нет сценария выхода для роли «{role}»'

    scenario = await _scenario_find(int(girl['site_id']), name)
    if not scenario:
        return False, f'сценарий выхода «{name}» у сайта не заведён'

    if not girl.get('storage_state'):
        return False, 'вход не снят: выходить нечем'

    # Строка в журнал до самого выхода: сценарий идёт до минуты, и без неё
    # страница молчит всё это время, а человек думает, что кнопка не сработала.
    await dating_log_write(int(girl['id']), 'info', f'Выхожу на сайте сценарием «{name}»')

    # Своя вкладка этой учётки, если она жива: сайт держит одну сессию на анкету, и
    # выход из второй вкладки выбил бы первую, оставив её висеть.
    session_id = await _session_own(str(girl['login']), session_list)
    own = not session_id
    if own:
        try:
            session_id = await session_open(site, girl)
        except Exception as e:
            return False, f'сессия для выхода не завелась: {e}'

    try:
        result = await runner(int(scenario['id']), session_id, {'login': str(girl['login'])}, {}, True)
    except Exception as e:
        return False, f'сценарий выхода не прошёл: {e}'

    if result.get('status') == 'error':
        return False, (f"сценарий выхода встал на шаге {result.get('step_no', 0)}: "
                       f"{str(result.get('error') or '')[:200]}")

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


async def _state_clear(girl_id: int) -> None:
    """Стирает вход и адрес сокета — этим учётка и снимается с мониторинга.

    Переписка, журнал и анкета остаются: отключение это «не веди её», а не
    «забудь, что она была».
    """
    async with mysql_get_db_async() as db:
        # Снимаем и присмотр автомата: «Отключить» — это «не веди её», а не
        # «выйди и войди снова». Иначе смена вернула бы анкету в сеть через минуту.
        await db.execute("UPDATE dating_girl SET storage_state = NULL, ws_url = '', "
                         "       status = 'offline', unread = 0, last_error = '', "
                         '       keep_online = 0, relogin_since = NULL, relogin_tries = 0, '
                         '       relogin_next_at = NULL WHERE id = %s', (int(girl_id),))
        await db.connection.commit()
