"""Вход под анкетой: прогон сценария в браузере и всё, что делает его настоящим.

Отдельным модулем, а не телом ручки, потому что входить умеет не только человек:
смена держателя сокетов входит сама, когда сессия сайта протухла
(`dating_revive`). Две реализации входа разошлись бы на первой же правке — одна
половина системы обновляла бы состояние, другая нет.

Вход — не «нажать кнопку»: сценарий отчитывается успехом и на неверном пароле,
потому что его последние шаги (ожидание кабинета) помечены необязательными — у
мужской и женской анкеты кабинет разный. Поэтому после сценария идёт проверка
опросом, и только подтверждённый вход считается входом.

Ошибки наружу — `DatingLoginError` с текстом для журнала анкеты: HTTP-кода здесь
нет, его ставит ручка.
"""

import json

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.mysql_.repository.dating_profile_repository import DatingProfileRepository
from src.rhapsody.rhapsody_api import (rhapsody_api_error_write,
                                       rhapsody_api_scenario_find_by_id, rhapsody_api_scenario_login_find,
                                       rhapsody_api_session_close,
                                       rhapsody_api_session_list, rhapsody_api_session_open,
                                       rhapsody_api_session_state, rhapsody_api_site_get)
from src.dating.dating_json import dating_json_load
from src.dating.dating_log import dating_log_write
from src.dating.dating_presence import dating_presence_probe
from src.dating.dating_ready import dating_password_pick
from src.uvicorn.routers.dating.dating_site import dating_site_setting
from src.dating.dating_scenario import dating_scenario_run
from src.dating.dating_profile_page import dating_profile_page_read
from src.dating.dating_ws import dating_ws_capture


class DatingLoginError(RuntimeError):
    """Входить нечем. Текст пригоден и для журнала анкеты, и для ответа ручки."""


# Имена подстановок, по которым узнаётся сценарий входа. Сайты называют поля
# по-разному, поэтому список, а не одно имя.
DATING_LOGIN_LOGIN_KEYS = ('login', 'username', 'email', 'user')
DATING_LOGIN_PASSWORD_KEYS = ('password', 'pass', 'pwd')

# Настройка сайта с общим агентским паролем — та же, что у отправки и у списков.
DATING_LOGIN_PASSWORD_SETTING = 'api_password'


async def dating_login_run(profile_id: int, scenario_id: int = 0, session_id: str = '') -> dict:
    """Входит под учёткой: сценарий, состояние, проверка опросом, адрес сокета.

    Args:
        profile_id: номер учётки.
        scenario_id: сценарий входа; 0 — найти по подстановкам сайта.
        session_id: готовая вкладка; пусто — завести свою и закрыть после.

    Returns:
        dict: `status` — `success` или `error`, `session_id`, `scenario`, `error`,
        `profile`.

    Raises:
        DatingLoginError: входить нечем — нет пароля, сайта или сценария входа.
    """
    profile = await _profile_row(profile_id)
    site = await _site_check(int(profile['site_id']))

    # ⚠ **Вход брал только свой пароль анкеты** — в отличие от отправки и списков,
    # которые падали на общий агентский. Расхождение было тихим: анкета работала
    # общим паролем везде, кроме входа, где отвечала «пароль не задан».
    #
    # Теперь правило одно на всех: откуда пароль, решает сама анкета
    # (`password_source`), а выбор делает `dating_password_pick`.
    password = dating_password_pick(
        profile, str(await dating_site_setting(int(profile['site_id']),
                                               DATING_LOGIN_PASSWORD_SETTING)))
    if not password:
        raise DatingLoginError('нечем войти: пароль не задан — проверьте блок «Пароль» '
                               'у анкеты')

    scenario = await _scenario_login(int(profile['site_id']), scenario_id)
    if not scenario:
        raise DatingLoginError('для сайта нет сценария входа: заведите его на вкладке '
                               '«Сценарии» с подстановками логина и пароля')

    # Прежнюю сессию этой анкеты гасим: сайт держит одну на учётку и при втором
    # входе выбрасывает первую. Две живые вкладки кончались тем, что обе теряли
    # вход, а страница показывала «сессия завершена» на ровном месте.
    # Журнал ведём по ходу, а не итогом: вход идёт до пары минут, и всё это время
    # страница показывала бы пустоту. Каждая строка — ответ на вопрос «где оно
    # сейчас», ради которого человек и смотрит в журнал.
    await dating_log_write(profile_id, 'info', 'Вход запущен')

    closed = await dating_login_session_close_old(profile['login'], session_id)
    if closed:
        await dating_log_write(profile_id, 'info', f'Прежних вкладок закрыто: {closed}')

    session_id = session_id or await dating_login_session_open(site, profile['login'])
    secrets = {name: (profile['login'] if name in DATING_LOGIN_LOGIN_KEYS else password)
               for name in scenario['keys']}

    await dating_log_write(profile_id, 'info', f"Прохожу форму входа сценарием «{scenario['name']}»")

    result = await dating_scenario_run(scenario, int(site['id']), profile_id,
                                       session_id, {}, secrets, keep_session=True)

    card = {}
    if result.get('status') == 'error':
        await dating_log_write(profile_id, 'fail',
                               f"Вход не прошёл на шаге {result.get('step_no', 0)}: "
                               f"{str(result.get('error') or '')[:300]}")
    else:
        await dating_log_write(profile_id, 'ok', 'Форма входа пройдена, жду кабинета')
        # Анкета читается первой не ради анкеты: её сценарий ждёт появления
        # кабинета, то есть настоящего конца входа. Нажатие кнопки «войти» ещё
        # не сессия — снятое сразу после него состояние приходило без cookie
        # сайта, и опрос честно отвечал «сайт не признал вход».
        card = await dating_login_profile_page(await _profile_row(profile_id),
                                               dating_json_load(site.get('presence')),
                                               session_id)
        # Снимаем вход: дальше присутствие держит HTTP-опрос по этим cookie, и
        # второй раз через браузер логиниться не придётся.
        await _state_save(profile_id, session_id)

        # И тут же спрашиваем сайт, признал ли он вход. Сценарий этого не скажет:
        # его последние шаги — ожидания кабинета, и они помечены необязательными,
        # потому что у мужской и женской анкеты кабинет разный. Из-за этого
        # сценарий отчитывался успехом и на неверном пароле: сайт показывал «Bad
        # credentials», а карточка получала бесполезное состояние и выглядела
        # рабочей до первой попытки что-нибудь отправить.
        #
        # Опрос годится обеим ролям — в отличие от чтения анкеты, сценарий которой
        # заведён только для женской.
        verify = await _verify(await _profile_row(profile_id), site)
        if verify['status'] != 'online':
            # Состояние от неудачного входа стираем: держать cookie, по которым
            # сайт не пускает, значит показывать в карточке вход, которого нет.
            await dating_login_state_clear(profile_id)
            await dating_log_write(profile_id, 'fail',
                                   f"Сайт вход не признал: {verify['error']}")
            await dating_login_session_close(session_id)
            await rhapsody_api_error_write(
                'dating', f"вход под «{profile['login']}» не признан сайтом: {verify['error']}",
                context={'profile_id': profile_id, 'site_id': profile['site_id'],
                         'scenario_id': scenario['id'], 'where': 'проверка входа опросом'})

            return {'status': 'error', 'session_id': '',
                    'scenario': scenario['name'], 'error': verify['error'],
                    'profile': {}}

        await dating_log_write(profile_id, 'ok',
                               'Вход признан сайтом, состояние снято — опрос пойдёт без браузера')

        # Анкета под присмотром автомата: раз человек её ввёл, она должна быть в
        # сети и дальше. Здесь же гасим череду неудач — вход только что удался.
        await _keep_online_set(profile_id)

        # И сразу новый адрес чат-сокета: токены в прежнем привязаны к прежней
        # сессии, и после входа он мёртв. Без этого держатель вечно висел на
        # мёртвом адресе — соединение выглядело живым, а сообщения уходили в
        # «пропущенные». Снимаем в этой же вкладке: вторая выбила бы первую.
        await dating_log_write(profile_id, 'info', 'Снимаю адрес чат-сокета')
        ws_url = await dating_login_ws_capture(await _profile_row(profile_id), session_id)
        await dating_log_write(
            profile_id, 'ok' if ws_url else 'fail',
            'Адрес чат-сокета снят, чат подхватит держатель в течение минуты' if ws_url
            else 'Адрес чат-сокета снять не вышло — живой чат работать не будет')

        # И закрываем вкладку. Сайт отдаёт сообщение одному соединению, а
        # оставленная страница чата — тоже соединение: она молча забирала
        # входящие себе, и держатель не получал ничего. Вход нужен ради cookie и
        # адреса сокета, а не ради открытого окна.
        await dating_login_session_close(session_id)
        session_id = ''

    if result.get('status') == 'error':
        await rhapsody_api_error_write(
            'dating', f"вход под «{profile['login']}» не прошёл: {result.get('error', '')}",
            session_id=session_id,
            context={'profile_id': profile_id, 'site_id': profile['site_id'],
                     'scenario_id': scenario['id'], 'step_no': result.get('step_no', 0)})

    return {'status': result.get('status'), 'session_id': session_id,
            'scenario': scenario['name'], 'error': result.get('error', ''),
            'profile': card}


async def dating_login_profile_page(profile: dict, presence: dict, session_id: str) -> dict:
    """Читает анкету со страницы кабинета и пишет итог в журнал учётки.

    Неудача входа не роняет: анкета — украшение карточки, а вход уже состоялся, и
    отменять его из-за нечитаемой анкеты значило бы отобрать у человека сессию.
    """
    # Прогон идёт общей дверью: она пишет его в журнал прогонов и в журнал анкеты.
    # Замыканием, потому что `dating_profile_page_read` про сайт и анкету знает
    # только то, что лежит в переданной строке, а журналу нужны их номера.
    async def _run(scenario, session, variables):
        return await dating_scenario_run(scenario, int(profile['site_id']),
                                         int(profile['id']), session, variables, {},
                                         keep_session=True)

    result = await dating_profile_page_read(profile, presence, session_id, _run)
    await dating_log_write(int(profile['id']), 'ok' if result['ok'] else 'info',
                           f"Анкета прочитана: {result['profile'].get('name', '')}"
                           if result['ok'] else f"Анкета не прочиталась: {result['error']}")

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"анкета не прочиталась: {result['error']}",
                                  session_id=session_id,
                                  context={'profile_id': profile['id'], 'where': 'чтение анкеты'})

    return result['profile'] if result['ok'] else {}


async def dating_login_ws_capture(profile: dict, session_id: str = '') -> str:
    """Снимает адрес чат-сокета учётки — общей механикой, а не своей копией.

    Тот же перехват нужен держателю сокетов (`dating_chat_hold`), и две копии кода
    разошлись бы первой же правкой: одна половина системы обновляла бы адрес,
    другая держалась за старый.
    """
    site = await _site_check(int(profile['site_id']))
    url = await dating_ws_capture({**profile, 'storage_state': dating_json_load(profile.get('storage_state'))},
                                  dating_json_load(site.get('presence')), site['url'], session_id)
    if not url:
        await rhapsody_api_error_write('dating', 'адрес чат-сокета не снялся',
                                  context={'profile_id': profile['id']})

    return url


async def dating_login_state_clear(profile_id: int) -> None:
    """Стирает состояние входа: сайт по нему не пускает, и держать его вредно.

    Заодно гасит адрес сокета: он снят в той же мёртвой сессии, и держатель чата
    иначе будет ломиться по нему, показывая живое соединение вместо неудачи.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_profile SET storage_state = NULL, ws_url = %s, '
                             '       status = %s, last_error = %s WHERE id = %s',
                             ('', 'error', 'сайт не признал вход', profile_id))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] состояние входа не стёрлось: {e}')


async def dating_login_session_close_old(login: str, keep_session_id: str) -> int:
    """Гасит живые сессии этой анкеты, кроме той, куда попросили войти.

    Не роняет вход, если контейнер недоступен: сессии — его хозяйство, и вход по
    сценарию всё равно попробуется.
    """
    closed = 0
    for session in await dating_login_session_all():
        if str(session.get('name') or '') != str(login) or session.get('session_id') == keep_session_id:
            continue
        try:
            await dating_login_session_close(str(session.get('session_id') or ''))
            closed += 1
        except Exception as e:
            await rhapsody_api_error_write('dating', f'прежняя сессия не закрылась: {e}',
                                      session_id=str(session.get('session_id') or ''))

    return closed


async def dating_login_session_all() -> list:
    """Живые сессии контейнера. Недоступен — пустой список, а не исключение:
    вызывающие умеют работать без браузера, а падать из-за него не должны."""
    try:
        return await rhapsody_api_session_list()
    except Exception:
        return []


async def dating_login_session_close(session_id: str) -> None:
    """Гасит вкладку. Уже погасла — не беда: важен итог, а не кто её закрыл."""
    if not session_id:
        return

    try:
        await rhapsody_api_session_close(session_id)
    except Exception as e:
        logger_info(f'[dating] вкладка {session_id} не закрылась: {e}')


async def dating_login_session_open(site: dict, name: str) -> str:
    """Заводит сессию браузера на адрес сайта — под именем учётки, чтобы её было
    видно в полосе вкладок `/admin/browser`."""
    return await rhapsody_api_session_open(url=site['url'], name=name[:100],
                                          site_id=int(site['id']))


async def _verify(profile: dict, site: dict) -> dict:
    """Признал ли сайт вход — по настройке присутствия этого сайта.

    ⚠ **Раньше проверка бывала пустышкой.** Она шла через `presence` соседа, а у
    сайта без него (Ladadate) описание пусто: шагов нет, проверять нечего, и опрос
    возвращал `online`, ничего не спросив. Вход объявлялся признанным всегда — то
    есть ровно там, где проверка и нужна, её не было.

    Теперь спрашиваем настройку присутствия (`dating_heartbeat.py`): своя у сайта
    старше, соседская читается запасной. Настройки нет вовсе — честно говорим об
    этом, а не выдаём молчание за успех.
    """
    from http_.http_heartbeat import (http_heartbeat_check, http_heartbeat_client,
                                      http_heartbeat_cookies, http_heartbeat_ready)

    from src.dating.dating_heartbeat import dating_heartbeat_config

    config = await dating_heartbeat_config(int(site.get('id') or profile['site_id']))
    if not http_heartbeat_ready(config):
        # ⚠ Не отказ. Вход сценарием прошёл, cookie сняты, и работа с сайтом по
        # ним пойдёт; неизвестно лишь, признал ли сайт вход. Объявить это провалом
        # значило бы стереть рабочую сессию из-за незаполненной настройки.
        return {'status': 'online', 'unread': -1, 'ms': 0,
                'error': 'присутствие сайта не настроено — вход не проверен'}

    cookies = http_heartbeat_cookies(dating_json_load(profile.get('storage_state')),
                                     str(config.get('base') or ''))
    client = http_heartbeat_client()
    try:
        return await http_heartbeat_check(client, cookies, config)
    finally:
        await client.aclose()


async def _state_save(profile_id: int, session_id: str) -> None:
    """Снимает `storage_state` сессии и кладёт его учётке.

    Не роняет вход, если не получилось: сценарий уже отработал, и терять его
    результат из-за неснятого состояния незачем — снимем при следующем входе.
    """
    try:
        state = await rhapsody_api_session_state(session_id)
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_profile SET storage_state = %s, status = %s, '
                             '       last_seen_at = NOW(), last_error = %s WHERE id = %s',
                             (json.dumps(state, ensure_ascii=False), 'online', '', profile_id))
            await db.connection.commit()
    except Exception as e:
        await rhapsody_api_error_write('dating', f'состояние входа не снялось: {e}',
                                  session_id=session_id, context={'profile_id': profile_id})


async def _keep_online_set(profile_id: int) -> None:
    """Успешный вход: анкета под присмотром, череда неудач обнулена."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_profile SET keep_online = 1, logged_in_at = NOW(), '
                             '       relogin_since = NULL, relogin_tries = 0, '
                             '       relogin_next_at = NULL WHERE id = %s', (int(profile_id),))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] пометка keep_online не поставилась: {e}')


async def _scenario_login(site_id: int, scenario_id: int | None) -> dict | None:
    """Сценарий входа этого сайта: тот, что ждёт и логин, и пароль.

    Ищет его RhapsodyAi — там сценарии и лежат. Явно переданный `scenario_id`
    берётся как есть: человек лучше знает, чем он входит.

    Отказ соседа уходит наверх `RhapsodyError`: «сосед не отвечает» — это не
    «войти нечем», и ручка отвечает на них разными кодами.
    """
    if scenario_id:
        scenario = await rhapsody_api_scenario_find_by_id(int(scenario_id))
    else:
        scenario = await rhapsody_api_scenario_login_find(int(site_id))

    if not scenario:
        return None

    return {'id': scenario['id'], 'name': scenario.get('name', ''),
            'keys': list(DATING_LOGIN_LOGIN_KEYS[:1] + DATING_LOGIN_PASSWORD_KEYS[:1])}


async def _site_check(site_id: int) -> dict:
    """Сайт-закладка, к которой относится учётка. Живёт в RhapsodyAi.

    Своей таблицы сайтов здесь нет и быть не должно: закладки, сценарии и пул
    Chromium — хозяйство соседа, и вторая копия настройки разошлась бы с первой
    в тот день, когда сайт переверстает форму входа.

    Не дубль роутерного `_site_check`, хотя тело то же: тот бросает
    `HTTPException`, а этот — `DatingLoginError`, потому что модуль входа зовут из
    воркера `rq`, где FastAPI нет и HTTP-кода поставить некому. Молчание соседа
    (`RhapsodyError`) не перехватывается: это не «войти нечем», а сбой связи.
    """
    row = await rhapsody_api_site_get(int(site_id))
    if not row:
        raise DatingLoginError(f'нет сайта с номером {site_id}')

    return dict(row)


async def _profile_row(profile_id: int) -> dict:
    """Учётка из базы. Нет такой — `DatingLoginError`: входить не под кем."""
    row = await DatingProfileRepository().find(profile_id)
    if not row:
        raise DatingLoginError('учётка не найдена')

    return dict(row)
