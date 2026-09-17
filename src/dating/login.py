"""Вход под анкетой: прогон сценария в браузере и всё, что делает его настоящим.

Отдельным модулем, а не телом ручки, потому что входить умеет не только человек:
смена держателя сокетов входит сама, когда сессия сайта протухла
(`revive`). Две реализации входа разошлись бы на первой же правке — одна
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

from src.mysql_.repository.profile_repository import DatingProfileRepository
from src.rhapsody.rhapsody_api import (rhapsody_api_error_write,
                                       rhapsody_api_scenario_find_by_id, rhapsody_api_scenario_login_find,
                                       rhapsody_api_session_close,
                                       rhapsody_api_session_list, rhapsody_api_session_open,
                                       rhapsody_api_session_state, rhapsody_api_site_get)
from src.dating.json_column import json_column_load
from src.dating.log import log_write
from src.dating.presence import presence_probe
from src.dating.ready import password_pick
from src.uvicorn.routers.dating.site_ import site_setting
from src.dating.scenario import scenario_run
from src.dating.profile_page import profile_page_read
from src.dating.proxy import proxy_ready, proxy_url
from src.dating.ws import ws_capture


class DatingLoginError(RuntimeError):
    """Входить нечем. Текст пригоден и для журнала анкеты, и для ответа ручки."""


# Имена подстановок, по которым узнаётся сценарий входа. Сайты называют поля
# по-разному, поэтому список, а не одно имя.
LOGIN_LOGIN_KEYS = ('login', 'username', 'email', 'user')
LOGIN_PASSWORD_KEYS = ('password', 'pass', 'pwd')

# Настройка сайта с общим агентским паролем — та же, что у отправки и у списков.
LOGIN_PASSWORD_SETTING = 'api_password'


async def login_run(profile_id: int, scenario_id: int = 0, session_id: str = '',
                           force: bool = False) -> dict:
    """Входит под учёткой: сценарий, состояние, проверка опросом, адрес сокета.

    Args:
        profile_id: номер учётки.
        scenario_id: сценарий входа; 0 — найти по подстановкам сайта.
        session_id: готовая вкладка; пусто — завести свою и закрыть после.
        force: вход по кнопке человека. Проходит и через паузу, и через
            исчерпанный счётчик неудач: человек у экрана знает больше автомата —
            он мог сменить пароль, разблокировать учётку, дождаться конца чужой
            блокировки. Отказывать ему значило бы чинить сайт через базу.

    Returns:
        dict: `status` — `success` или `error`, `session_id`, `scenario`, `error`,
        `profile`.

    Raises:
        DatingLoginError: входить нечем — нет пароля, сайта или сценария входа.
        DatingLoginHeld: входить **рано** — идёт пауза после неудачи или попытки
            кончились. Отдельным исключением, потому что это не поломка: круг
            обязан промолчать и уйти, а не писать строку, похожую на отказ.

    ⚠⚠ **Проверка повторов стоит здесь, а не в кругах, и это главное в ней.**
    Лестница пауз автовосстановления существовала и раньше — но её спрашивал круг
    восстановления, а круг расписания входил прямым вызовом, мимо всех правил. На
    проде это дало девять входов за четырнадцать минут у анкеты, чей вход сайт не
    признавал (`login_retry.py`).

    Правило у двери обойти нельзя, не переписав саму дверь. Потому оно и здесь, а
    не в трёх местах, откуда входят.
    """
    from src.dating.login_retry import (DatingLoginHeld,
                                               login_retry_allowed,
                                               login_retry_fail)

    profile = await _profile_row(profile_id)

    if not force:
        allowed, why = await login_retry_allowed(profile)
        if not allowed:
            raise DatingLoginHeld(why)

    site = await _site_check(int(profile['site_id']))

    # ⚠ **Вход брал только свой пароль анкеты** — в отличие от отправки и списков,
    # которые падали на общий агентский. Расхождение было тихим: анкета работала
    # общим паролем везде, кроме входа, где отвечала «пароль не задан».
    #
    # Теперь правило одно на всех: откуда пароль, решает сама анкета
    # (`password_source`), а выбор делает `password_pick`.
    password = password_pick(
        profile, str(await site_setting(int(profile['site_id']),
                                               LOGIN_PASSWORD_SETTING)))
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
    # ⚠ **Номер попытки в строке, а не только в базе.** Долбёжку из девяти входов
    # подряд (живой случай анкеты #41) видно было, только если сесть и посчитать
    # строки глазами: каждая выглядела первой. «Попытка 7 из 3» бросается в
    # глаза сразу — и сразу говорит, что предел пройден кнопкой человека.
    await log_write(profile_id, 'info', await _try_text(profile))

    # ⚠ **Выход берём до входа, а не после.** Именно вход сайт и считает по
    # внешнему адресу: с одного IP он пускает ограниченное число девушек, дальше
    # отвергает. Войди мы общим адресом сервера — место на нём заняла бы эта
    # анкета, а следующая получила бы отказ, причём без внятной причины.
    #
    # Пока `proxy_required` снят, отсутствие выхода вход не останавливает: на
    # свежей установке прокси ещё не заведены, и глушить работу до их появления
    # значит менять одну беду на другую (`proxy.py`).
    can_go, why = await proxy_ready(profile)
    if not can_go:
        raise DatingLoginError(why)

    closed = await login_session_close_old(profile['login'], session_id)
    if closed:
        await log_write(profile_id, 'info', f'Прежних вкладок закрыто: {closed}')

    session_id = session_id or await login_session_open(site, profile['login'], profile)
    secrets = {name: (profile['login'] if name in LOGIN_LOGIN_KEYS else password)
               for name in scenario['keys']}

    await log_write(profile_id, 'info', f"Прохожу форму входа сценарием «{scenario['name']}»")

    result = await scenario_run(scenario, int(site['id']), profile_id,
                                       session_id, {}, secrets, keep_session=True)

    card = {}
    if result.get('status') == 'error':
        await log_write(profile_id, 'fail',
                               f"Вход не прошёл на шаге {result.get('step_no', 0)}: "
                               f"{str(result.get('error') or '')[:300]}")
    else:
        await log_write(profile_id, 'ok', 'Форма входа пройдена, жду кабинета')
        # Анкета читается первой не ради анкеты: её сценарий ждёт появления
        # кабинета, то есть настоящего конца входа. Нажатие кнопки «войти» ещё
        # не сессия — снятое сразу после него состояние приходило без cookie
        # сайта, и опрос честно отвечал «сайт не признал вход».
        # ⚠⚠ **Заполненную карточку заново не читаем.** Строка «Анкета не
        # прочиталась: у сайта нет сценария анкеты для роли lady» висела в
        # журнале **на каждый вход** — 19 раз в сутки, — и была враньём про
        # беду: карточки у анкет заполнены, просто приехали другим путём
        # (импортом с сайта). Сценария же чтения у Ladadate нет и, судя по
        # журналу, не было никогда.
        #
        # Читаем только пустую: анкета, у которой нет ни имени, ни фото, в
        # панели выглядит безымянной строкой — вот её и стоит дочитать, если
        # сайт умеет. Заполненную трогать незачем: имя с фотографией не
        # протухают, а каждое чтение — прогон сценария у соседа.
        if _card_empty(profile):
            card = await login_profile_page(await _profile_row(profile_id),
                                                   json_column_load(site.get('presence')),
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
            await login_state_clear(profile_id)
            await log_write(profile_id, 'fail',
                                   f"Сайт вход не признал: {verify['error']}")
            await login_session_close(session_id)
            await rhapsody_api_error_write(
                'dating', f"вход под «{profile['login']}» не признан сайтом: {verify['error']}",
                context={'profile_id': profile_id, 'site_id': profile['site_id'],
                         'scenario_id': scenario['id'], 'where': 'проверка входа опросом'})

            return {'status': 'error', 'session_id': '',
                    'scenario': scenario['name'], 'error': verify['error'],
                    'profile': {}}

        await log_write(profile_id, 'ok',
                               'Вход признан сайтом, состояние снято — опрос пойдёт без браузера')

        # Анкета под присмотром автомата: раз человек её ввёл, она должна быть в
        # сети и дальше. Здесь же гасим череду неудач — вход только что удался.
        await _keep_online_set(profile_id)

        # И сразу новый адрес чат-сокета: токены в прежнем привязаны к прежней
        # сессии, и после входа он мёртв. Без этого держатель вечно висел на
        # мёртвом адресе — соединение выглядело живым, а сообщения уходили в
        # «пропущенные». Снимаем в этой же вкладке: вторая выбила бы первую.
        # ⚠⚠ **Только у сайта с сокетом**, и это не оптимизация. У Ladadate
        # сокета нет вовсе — чат там обычный HTTP, — но перехват запускался всё
        # равно: `ws_capture` берёт `presence.chat_url`, а когда его нет,
        # подставляет **корень сайта** и честно ждёт сокета на главной странице.
        #
        # Стоило это вкладки живого Chromium у соседа на каждый вход и строки
        # «Адрес чат-сокета снять не вышло — живой чат работать не будет» в
        # журнале. Строка при этом врала дважды: снимать было нечего, а чат у
        # Ladadate работает и без сокета.
        #
        # Признак берём тот же, по которому решается отправка
        # (`ready_transport`): сокет нужен ровно там, где им пишут.
        if await _ws_needed(int(profile['site_id']), site):
            await log_write(profile_id, 'info', 'Снимаю адрес чат-сокета')
            ws_url = await login_ws_capture(await _profile_row(profile_id),
                                                   session_id)
            await log_write(
                profile_id, 'ok' if ws_url else 'fail',
                'Адрес чат-сокета снят, чат подхватит держатель в течение минуты'
                if ws_url
                else 'Адрес чат-сокета снять не вышло — живой чат работать не будет')

        # И закрываем вкладку. Сайт отдаёт сообщение одному соединению, а
        # оставленная страница чата — тоже соединение: она молча забирала
        # входящие себе, и держатель не получал ничего. Вход нужен ради cookie и
        # адреса сокета, а не ради открытого окна.
        await login_session_close(session_id)
        session_id = ''

    if result.get('status') == 'error':
        # ⚠ **Считаем неудачу здесь, у самой двери.** Считай её вызывающие — первый
        # же забывший вход не попал бы в счётчик, а именно так и вышло с кругом
        # расписания: он входил мимо всех правил и долбил сайт каждые 86 секунд.
        #
        # Записывается и вход по кнопке (`force`): человек обошёл паузу, но его
        # неудача такая же настоящая, и следующему автомату о ней знать полезно.
        await login_retry_fail(profile_id)
        await rhapsody_api_error_write(
            'dating', f"вход под «{profile['login']}» не прошёл: {result.get('error', '')}",
            session_id=session_id,
            context={'profile_id': profile_id, 'site_id': profile['site_id'],
                     'scenario_id': scenario['id'], 'step_no': result.get('step_no', 0)})

    return {'status': result.get('status'), 'session_id': session_id,
            'scenario': scenario['name'], 'error': result.get('error', ''),
            'profile': card}


async def login_profile_page(profile: dict, presence: dict, session_id: str) -> dict:
    """Читает анкету со страницы кабинета и пишет итог в журнал учётки.

    Неудача входа не роняет: анкета — украшение карточки, а вход уже состоялся, и
    отменять его из-за нечитаемой анкеты значило бы отобрать у человека сессию.
    """
    # Прогон идёт общей дверью: она пишет его в журнал прогонов и в журнал анкеты.
    # Замыканием, потому что `profile_page_read` про сайт и анкету знает
    # только то, что лежит в переданной строке, а журналу нужны их номера.
    async def _run(scenario, session, variables):
        return await scenario_run(scenario, int(profile['site_id']),
                                         int(profile['id']), session, variables, {},
                                         keep_session=True)

    result = await profile_page_read(profile, presence, session_id, _run)
    await log_write(int(profile['id']), 'ok' if result['ok'] else 'info',
                           f"Анкета прочитана: {result['profile'].get('name', '')}"
                           if result['ok'] else f"Анкета не прочиталась: {result['error']}")

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"анкета не прочиталась: {result['error']}",
                                  session_id=session_id,
                                  context={'profile_id': profile['id'], 'where': 'чтение анкеты'})

    return result['profile'] if result['ok'] else {}


async def _try_text(profile: dict) -> str:
    """Строка «Вход запущен» с номером попытки, если он не первый.

    ⚠ **У первой попытки номера нет.** Приписывать «попытка 1 из 3» к обычному
    входу значило бы красить тревожным то, что происходит каждую смену, — и
    номер перестал бы что-либо значить ровно тогда, когда он важен.
    """
    from src.dating.login_retry import login_retry_max

    tries = int(profile.get('login_fail_tries') or 0)
    if not tries:
        return 'Вход запущен'

    limit = await login_retry_max(int(profile.get('site_id') or 0))

    return f'Вход запущен (попытка {tries + 1} из {limit})'


def _card_empty(profile: dict) -> bool:
    """Пуста ли карточка анкеты — есть ли смысл её читать.

    ⚠ **Признак — имя, а не наличие столбца.** В `profile` сайт кладёт полтора
    десятка полей, и «что-то есть» ничего не значит: карточка без имени в панели
    выглядит безымянной строкой, ради чего чтение и заводилось. Фото при этом
    достаточным не считаем — по одной картинке анкету не отличить от соседней,
    а имена у наших девушек повторяются (две Elena, две Natalia, две Diana).
    """
    card = json_column_load(profile.get('profile')) or {}

    return not str(card.get('name') or '').strip()


async def _ws_needed(site_id: int, site: dict) -> bool:
    """Нужен ли этому сайту адрес чат-сокета.

    ⚠ **Спрашиваем транспорт отправки, а не наличие `chat_url`.** Транспорт и
    есть ответ на вопрос «чем тут разговаривают»: `ws` значит сокет, `http` и
    `scenario` — что сокета нет и искать его незачем. Проверяй мы `chat_url`,
    правило разошлось бы с отправкой в первый же день, когда у сайта заведут
    адрес чата без сокета.

    Наша настройка при этом старше соседской — то же правило, что везде
    (`ready.py`).
    """
    from src.dating.ready import ready_transport
    from src.uvicorn.routers.dating.site_ import site_settings

    transport = ready_transport(json_column_load(site.get('presence')),
                                       await site_settings(int(site_id)))

    return transport == 'ws'


async def login_ws_capture(profile: dict, session_id: str = '') -> str:
    """Снимает адрес чат-сокета учётки — общей механикой, а не своей копией.

    Тот же перехват нужен держателю сокетов (`chat_hold`), и две копии кода
    разошлись бы первой же правкой: одна половина системы обновляла бы адрес,
    другая держалась за старый.
    """
    site = await _site_check(int(profile['site_id']))
    url = await ws_capture({**profile, 'storage_state': json_column_load(profile.get('storage_state'))},
                                  json_column_load(site.get('presence')), site['url'], session_id)
    if not url:
        await rhapsody_api_error_write('dating', 'адрес чат-сокета не снялся',
                                  context={'profile_id': profile['id']})

    return url


async def login_state_clear(profile_id: int) -> None:
    """Стирает состояние входа: сайт по нему не пускает, и держать его вредно.

    Заодно гасит адрес сокета: он снят в той же мёртвой сессии, и держатель чата
    иначе будет ломиться по нему, показывая живое соединение вместо неудачи.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET storage_state = NULL, ws_url = %s, '
                             '       status = %s, last_error = %s WHERE id = %s',
                             ('', 'error', 'сайт не признал вход', profile_id))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] состояние входа не стёрлось: {e}')


async def login_session_close_old(login: str, keep_session_id: str) -> int:
    """Гасит живые сессии этой анкеты, кроме той, куда попросили войти.

    Не роняет вход, если контейнер недоступен: сессии — его хозяйство, и вход по
    сценарию всё равно попробуется.
    """
    closed = 0
    for session in await login_session_all():
        if str(session.get('name') or '') != str(login) or session.get('session_id') == keep_session_id:
            continue
        try:
            await login_session_close(str(session.get('session_id') or ''))
            closed += 1
        except Exception as e:
            await rhapsody_api_error_write('dating', f'прежняя сессия не закрылась: {e}',
                                      session_id=str(session.get('session_id') or ''))

    return closed


async def login_session_all() -> list:
    """Живые сессии контейнера. Недоступен — пустой список, а не исключение:
    вызывающие умеют работать без браузера, а падать из-за него не должны."""
    try:
        return await rhapsody_api_session_list()
    except Exception:
        return []


async def login_session_close(session_id: str) -> None:
    """Гасит вкладку. Уже погасла — не беда: важен итог, а не кто её закрыл."""
    if not session_id:
        return

    try:
        await rhapsody_api_session_close(session_id)
    except Exception as e:
        logger_info(f'[dating] вкладка {session_id} не закрылась: {e}')


async def login_session_open(site: dict, name: str, profile: dict = None) -> str:
    """Заводит сессию браузера на адрес сайта — под именем учётки, чтобы её было
    видно в полосе вкладок `/admin/browser`.

    ⚠ **Вкладка идёт через выход анкеты**, тот же, которым ходят наши HTTP и
    сокет. Сайт считает учётки по внешнему адресу: вход браузером с одного IP и
    присутствие с другого для него — две попытки на одну учётку.

    `profile` не обязателен только ради старых вызовов без анкеты на руках; с ним
    вкладка получает выход, без него — общий адрес сервера.
    """
    proxy_id = int((profile or {}).get('proxy_id') or 0)
    if profile and not proxy_id:
        # Выход ещё не закреплён — занимаем его сейчас, до входа: именно вход и
        # считается сайтом, и делать это с общего адреса нельзя.
        await proxy_url(profile)
        proxy_id = int(profile.get('proxy_id') or 0)

    return await rhapsody_api_session_open(url=site['url'], name=name[:100],
                                          site_id=int(site['id']), proxy_id=proxy_id)


async def _verify(profile: dict, site: dict) -> dict:
    """Признал ли сайт вход — по настройке присутствия этого сайта.

    ⚠ **Раньше проверка бывала пустышкой.** Она шла через `presence` соседа, а у
    сайта без него (Ladadate) описание пусто: шагов нет, проверять нечего, и опрос
    возвращал `online`, ничего не спросив. Вход объявлялся признанным всегда — то
    есть ровно там, где проверка и нужна, её не было.

    Теперь спрашиваем настройку присутствия (`heartbeat.py`): своя у сайта
    старше, соседская читается запасной. Настройки нет вовсе — честно говорим об
    этом, а не выдаём молчание за успех.
    """
    from http_.http_heartbeat import (http_heartbeat_check, http_heartbeat_client,
                                      http_heartbeat_cookies, http_heartbeat_ready)

    from src.dating.heartbeat import heartbeat_config

    config = await heartbeat_config(int(site.get('id') or profile['site_id']))
    if not http_heartbeat_ready(config):
        # ⚠ Не отказ. Вход сценарием прошёл, cookie сняты, и работа с сайтом по
        # ним пойдёт; неизвестно лишь, признал ли сайт вход. Объявить это провалом
        # значило бы стереть рабочую сессию из-за незаполненной настройки.
        return {'status': 'online', 'unread': -1, 'ms': 0,
                'error': 'присутствие сайта не настроено — вход не проверен'}

    cookies = http_heartbeat_cookies(json_column_load(profile.get('storage_state')),
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
            await db.execute('UPDATE profile SET storage_state = %s, status = %s, '
                             '       last_seen_at = NOW(), last_error = %s WHERE id = %s',
                             (json.dumps(state, ensure_ascii=False), 'online', '', profile_id))
            await db.connection.commit()
    except Exception as e:
        await rhapsody_api_error_write('dating', f'состояние входа не снялось: {e}',
                                  session_id=session_id, context={'profile_id': profile_id})


async def _keep_online_set(profile_id: int) -> None:
    """Успешный вход: анкета под присмотром, череда неудач обнулена.

    ⚠ **Счётчик повторов гаснет здесь же, одним запросом с тревогой.** Удача по
    одной двери не должна означать разного в двух местах: разойдись они — анкета
    вошла бы, а автомат считал бы её всё ещё исчерпавшей попытки.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET keep_online = 1, logged_in_at = NOW(), '
                             '       relogin_since = NULL, relogin_tries = 0, '
                             '       login_fail_tries = 0, login_fail_at = NULL, '
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
            'keys': list(LOGIN_LOGIN_KEYS[:1] + LOGIN_PASSWORD_KEYS[:1])}


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
    # ⚠ **Описание — из нашей настройки**, адрес и имя — из снимка списка сайтов.
    # Прежде обе половины брались карточкой у соседа; вход идёт сценарием, то
    # есть сосед всё равно нужен, но за **сценарием**, а не за настройкой.
    from src.dating.site_presence import site_presence
    from src.uvicorn.routers.dating.site_ import site_rows

    presence = await site_presence(int(site_id))
    row = next((one for one in await site_rows()
                if int(one['id']) == int(site_id)), None)

    if not row and not presence:
        raise DatingLoginError(f'нет сайта с номером {site_id}')

    return {'id': int(site_id),
            'name': str((row or {}).get('name') or ''),
            # ⚠ Адрес из снимка, а пусто — из описания: `base` говорит то же
            # самое, и сайт, который ещё не сверяли, иначе не вошёл бы вовсе.
            'url': str((row or {}).get('url') or presence.get('base') or ''),
            'presence': presence}


async def _profile_row(profile_id: int) -> dict:
    """Учётка из базы. Нет такой — `DatingLoginError`: входить не под кем."""
    row = await DatingProfileRepository().find(profile_id)
    if not row:
        raise DatingLoginError('учётка не найдена')

    return dict(row)
