"""Единственная дверь наружу: HTTP-клиент к RhapsodyAi.

Этот проект держит анкеты и живой чат, но ничего не знает про то, как водить
браузер и откуда берутся ответы агента. И то и другое живёт в RhapsodyAi:
там сценарии сайтов, там пул Chromium, там workflow и факты. Сюда это приходит
по HTTP — одним модулем, а не россыпью вызовов по коду.

**Один модуль на все обращения** не ради красоты: адрес, токен, таймауты и разбор
ошибок должны быть в одном месте. Иначе половина кода ходит с одним таймаутом,
половина с другим, а при смене адреса чинить приходится в десяти файлах.

Ошибка соседа — не наша ошибка. Функции возвращают то, что ответил RhapsodyAi, а
на обрыв связи бросают `RhapsodyError` с внятным текстом: в журнале анкеты должно
быть написано «RhapsodyAi недоступен», а не трасса httpx.
"""

import asyncio
import json
import sys
import time
from urllib.parse import quote

import httpx

from config.config import config_get
from http_.http_pool import http_pool_transport
from logging_.logging_ import logger_info

# Адрес соседа и токен служебной учётки. Токен — `user_access_token` пользователя
# `dating_service`: у него ровно те права, что нужны (браузер и создание агента).
RHAPSODY_API_URL = str(config_get('RHAPSODY_URL', 'http://uvicorn:8000')).rstrip('/')
RHAPSODY_API_TOKEN = str(config_get('RHAPSODY_TOKEN', ''))

# Таймауты разные по природе вызова, а не по вкусу: справочники отвечают мгновенно,
# сценарий входа водит живой браузер минуты, а ответ агента ждёт LLM.
RHAPSODY_API_TIMEOUT = 30.0
RHAPSODY_API_TIMEOUT_SCENARIO = 300.0
RHAPSODY_API_TIMEOUT_AGENT = 600.0

# Сколько ждать ответа агента внутри самого RhapsodyAi. Передаётся ему же: его
# ручка по умолчанию ждёт минуту, а разговор с фактами укладывается не всегда.
RHAPSODY_API_CHAT_TIMEOUT = 240

# Метка сайтов знакомств в закладках соседа. У него в списке лежит и его собственная
# админка, и рабочие стенды — анкету на них не заведёшь, а в селекте они путались.
# Новый сайт знакомств получает эту метку у соседа и появляется здесь сам, без правки
# кода. Константой, а не настройкой: значение общее для двух проектов, и разъехаться
# им нельзя — метка в чужой базе и метка в запросе обязаны совпадать буквально.
RHAPSODY_API_SITE_TAG = 'dating'

# Сколько помним, какой сценарий нашёлся по имени.
#
# Поиск сценария — это **выгрузка всего списка сценариев соседа** с последующим
# отбором по имени: у него их сотни, а нам нужен один номер. Список меняется
# редко (сценарий заводят руками), а спрашивается перед каждым прогоном — то есть
# перед каждым обновлением колонки собеседников.
#
# Десять минут — про то, как часто у соседа заводят сценарии, а не про нагрузку.
# Заведённый только что сценарий появится здесь через это время; ждать дольше
# значило бы держать кнопку мёртвой после того, как её настроили.
RHAPSODY_API_SCENARIO_TTL_SEC = 600

# Что означают коды отказа соседа — словами, а не числом. Текст уезжает в журнал
# анкеты, и «409» там не говорит ничего никому: за ним стоит занятый пул вкладок,
# и лечится он не перезапуском панели, а свободной вкладкой у соседа.
RHAPSODY_API_CODE_MEANING = {
    401: 'токен не принят',
    404: 'у соседа этого нет',
    409: 'браузер соседа занят',
    422: 'сосед не принял запрос',
    500: 'сосед сломался внутри',
    502: 'сосед не отвечает',
    503: 'сосед не отвечает',
}

# Найденные сценарии: `(site_id, имя)` → `(момент, запись)`. Момент — монотонные
# часы: меряется «сколько прошло внутри процесса», и рывок NTP назад не должен
# объявлять запись свежей на лишние часы.
_scenario_cache: dict[tuple[int, str], tuple[float, dict]] = {}

# Очередь прогонов: сосед водит браузер вкладками из общего пула, и мы просим по
# одному прогону за раз. Почему — в `rhapsody_api_scenario_run`.
_run_lock = asyncio.Lock()


# Сколько знаков тела запроса и ответа кладём в лог. 300 хватает, чтобы увидеть
# суть — какой сценарий, какие подстановки, чем кончился прогон, — и не хватает,
# чтобы килобайтный список сайтов забил лог на каждом показе страницы.
RHAPSODY_API_LOG_MAX = 300

# Ключи тела запроса, чьё содержимое в лог не попадает.
#
# ⚠ `secrets` — пароли анкет: их кладёт `scenario`, и в логе им места нет.
# Сам сосед их тоже не пишет (в его журнале прогонов остаётся `{{girl_password}}`),
# и терять эту защиту на нашей стороне нельзя.
RHAPSODY_API_LOG_HIDE = {'secrets'}


class RhapsodyError(RuntimeError):
    """RhapsodyAi не ответил или ответил отказом. Текст пригоден для журнала анкеты.

    `code` — код ответа соседа, 0 при обрыве связи. Нужен там, где отказ бывает
    обычным исходом: «за ключом выход не закреплён» — это 404, и разбирать его
    поиском подстроки в тексте значило бы сломаться от правки формулировки.
    """

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = int(code or 0)


async def rhapsody_api_site_get(site_id: int) -> dict:
    """Сайт-закладка целиком: адрес и настройка присутствия (`presence`).

    Отдельной ручкой, а не из списка: в списке `presence` нет — она простыня, и
    возить её в каждой строке селекта незачем.
    """
    # ⚠ **Пишем, кто позвал.** Описание сайта переехало к нам
    # (`site_presence.py`), и каждый оставшийся поход за карточкой — повод
    # спросить «а зачем». Без имени вызывающего такой поиск превращается в
    # перебор шестнадцати мест по журналу, где все строки одинаковы: это уже
    # стоило получаса на живом проде.
    #
    # Кадр берётся напрямую (`sys._getframe`), а не разбором стека: `inspect.stack`
    # читает исходники всех кадров и стоит миллисекунды — на вызове, который сам
    # укладывается в девять.
    who = sys._getframe(1)
    logger_info(f'[rhapsody] карточку сайта #{int(site_id)} просит '
                f'{who.f_globals.get("__name__", "?")}.{who.f_code.co_name}')

    try:
        data = await _request('GET', f'/api/browser/site/{int(site_id)}')
    except RhapsodyError as e:
        # 404 — «такого сайта нет», законный ответ на неверный номер, а не сбой
        # связи. Разница видна вызывающему: он превращает пустоту в 422 (ошибка
        # запроса), а обрыв — в 502 (сосед недоступен). Смешать их значило бы
        # отвечать «шлюз не отвечает» на опечатку в номере сайта.
        if ' → 404' in str(e):
            return {}
        raise

    return data.get('result') or {}


async def rhapsody_api_scenario_find(site_id: int, name: str) -> dict:
    """Сценарий сайта по имени. Нет такого — пустой словарь, решает вызывающий.

    Найденное запоминается на `RHAPSODY_API_SCENARIO_TTL_SEC`: под капотом это
    выгрузка **всего** списка сценариев соседа ради одного номера, а спрашивают её
    перед каждым прогоном. Заодно снимается лишний повод для отказа: пока номер
    помним, занятый или упавший сосед не мешает хотя бы узнать, что сценарий есть.

    ⚠ **Промахи не кэшируются.** «Такого сценария нет» — обычно значит, что его
    сейчас заводят у соседа; запомни мы пустоту на десять минут, кнопка осталась
    бы мёртвой после того, как её настроили.
    """
    key = (int(site_id), str(name))
    hit = _scenario_cache.get(key)
    if hit and time.monotonic() - hit[0] < RHAPSODY_API_SCENARIO_TTL_SEC:
        return hit[1]

    scenarios = await _request('GET', '/api/browser/scenario')
    for row in scenarios.get('result') or []:
        if int(row.get('site_id') or 0) == int(site_id) and str(row.get('name') or '') == str(name):
            _scenario_cache[key] = (time.monotonic(), row)
            return row

    return {}


def rhapsody_api_scenario_forget(site_id: int = 0, name: str = '') -> None:
    """Забыть найденный сценарий — после отказа, похожего на «его больше нет».

    Без этого удалённый или переименованный у соседа сценарий держал бы нас на
    старом номере до конца выдержки, а каждый прогон отвечал бы 404. Пустые
    аргументы очищают память целиком.
    """
    if not name:
        _scenario_cache.clear()
        return

    _scenario_cache.pop((int(site_id), str(name)), None)


async def rhapsody_api_site_list(tag: str = RHAPSODY_API_SITE_TAG) -> list:
    """Сайты-закладки соседа — для селекта на странице.

    Спрашиваются **с меткой**: у соседа в закладках лежит и его собственная админка,
    и рабочие стенды, а анкету на них не заведёшь. Отбор делает он же — сюда приходит
    готовый список, а не весь с просеиванием на месте: правило «что считать сайтом
    знакомств» должно жить там, где заводят закладки.

    Пустая метка вернёт всё — на случай, когда список нужен целиком.
    """
    path = '/api/browser/site'
    if tag:
        path += f'?tag={quote(str(tag))}'

    data = await _request('GET', path)

    return list(data.get('result') or [])


async def rhapsody_api_site_variable(site_id: int, name: str) -> str:
    """Переменная сайта у соседа: `panel_url`, `chat_companion_url_pattern` и такие же.

    Нужна там, где значение описывает **сам сайт**, а не нашу работу с ним: как у
    него выглядит адрес чата, где лежит панель агентства. Держать это у себя значило
    бы завести вторую копию того, что и так живёт в закладке сайта.

    ⚠ **Секреты отсюда не приходят.** В списке они замаскированы, а `reveal` у
    соседа закрыт правом человека (`BROWSER_SECRET_VIEW`) — служебному токену он не
    отдаст ничего. Это правильно: пароли анкет мы храним у себя, а чужие секреты
    читать нам незачем.

    Returns:
        str: значение или пустая строка — если переменной нет или сосед не ответил.
        Пустота, а не исключение: вызывающий и так обязан проверить, есть ли чем
        работать, а лишняя ветка отказа на каждый вызов ничего не добавляет.
    """
    try:
        data = await _request('GET', f'/api/browser/variable?site_id={int(site_id)}')
    except RhapsodyError as e:
        logger_info(f'[rhapsody] переменные сайта #{site_id} не пришли: {e}')

        return ''

    for row in data.get('items') or []:
        if str(row.get('name') or '') == str(name):
            # Секрет приходит маской — отдавать её как значение нельзя: подставится
            # в адрес или в поле формы и молча сломает работу.
            if row.get('is_secret'):
                logger_info(f'[rhapsody] переменная «{name}» секретна — значение не отдаётся')

                return ''

            return str(row.get('value') or '')

    return ''


async def rhapsody_api_scenario_find_by_id(scenario_id: int) -> dict:
    """Сценарий по номеру — когда человек выбрал его сам."""
    data = await _request('GET', f'/api/browser/scenario/{int(scenario_id)}')

    return data.get('result') or {}


async def rhapsody_api_scenario_login_find(site_id: int) -> dict:
    """Сценарий входа: тот, что ждёт и логин, и пароль.

    Ищется по подстановкам, а не по имени: имя у каждого сайта своё («вход
    (общий)», «login»), а вход всегда узнаётся по паре «логин + пароль» в
    подстановках. Список подстановок в ответе не приходит — приходится читать
    каждый сценарий сайта целиком; их единицы, и делается это раз за вход.
    """
    scenarios = await _request('GET', '/api/browser/scenario')
    for row in scenarios.get('result') or []:
        if int(row.get('site_id') or 0) != int(site_id):
            continue

        full = await _request('GET', f"/api/browser/scenario/{int(row['id'])}")
        keys = rhapsody_api_scenario_keys((full.get('result') or {}).get('steps') or [])
        if keys & set(RHAPSODY_API_LOGIN_KEYS) and keys & set(RHAPSODY_API_PASSWORD_KEYS):
            return row

    return {}


def rhapsody_api_scenario_keys(steps: list) -> set:
    """Имена подстановок `{{...}}`, которые ждёт сценарий.

    Считаются по шагам, а не берутся из ответа: список подстановок RhapsodyAi
    наружу не отдаёт, а по нему узнаётся сценарий входа.
    """
    keys = set()
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        for field in ('value', 'url', 'selector'):
            text = str(step.get(field) or '')
            start = text.find('{{')
            while start >= 0:
                end = text.find('}}', start)
                if end < 0:
                    break
                keys.add(text[start + 2:end].strip().lower())
                start = text.find('{{', end)

    return keys


async def rhapsody_api_scenario_run(scenario_id: int, session_id: str, variables: dict,
                                    secrets: dict, keep_session: bool = True,
                                    proxy_id: int = 0) -> dict:
    """Гонит сценарий в браузере RhapsodyAi. **По одному за раз.**

    `secrets` уходят отдельным полем, а не в `variables`: RhapsodyAi прячет их в
    своём журнале прогонов, и пароль анкеты туда не попадает.

    ⚠ **Прогоны выстраиваются в очередь, а не идут параллельно.** Каждый — это
    вкладка Chromium в пуле соседа: она входит на сайт под анкетой, ждёт отрисовки
    и гаснет. Запусти мы четыре разом — сайт получит четыре одновременных входа
    под одной учёткой (для него это подозрение), а пул отдаст четыре вкладки под
    работу, из которой пригодится одна.

    Наблюдалось живьём: один щелчок по анкете открывал **четыре** страницы сайта,
    и только одна доходила до конца — остальные гасились следом.

    Цена — ожидание в очереди: два списка подряд это не сорок секунд, а восемьдесят.
    Это честная цена, а не потеря: параллельно они всё равно мешали друг другу.

    Замок **внутрипроцессный**. Воркер `rq` живёт отдельным процессом, и его входы
    сюда не выстраиваются: чтобы свести и их, нужен замок в Redis. Пока не сведены —
    беды нет, воркер ходит редко и по своим анкетам.

    `proxy_id` — выход анкеты для **временной** вкладки прогона (`session_id`
    пуст). ⚠ Без него сценарий, который логинится сам — отправка, звёздочка,
    карточка собеседника, — ходил бы с общего адреса сервера, и лимит сайта
    складывался бы на нём. У готовой сессии выход задан при её открытии, и
    подменять его нечем.
    """
    async with _run_lock:
        return (await _request('POST', f'/api/browser/scenario/{int(scenario_id)}/run',
                               {'session_id': str(session_id or ''),
                                'variables': variables or {},
                                'secrets': secrets or {},
                                'keep_session': bool(keep_session),
                                'proxy_id': int(proxy_id or 0)},
                               timeout=RHAPSODY_API_TIMEOUT_SCENARIO)).get('result') or {}


async def rhapsody_api_session_open(url: str = '', name: str = '', storage_state: dict = None,
                                    site_id: int = 0, proxy_id: int = 0) -> str:
    """Заводит сессию браузера и отдаёт её номер.

    `proxy_id` — выход из реестра соседа (`/api/browser/proxy`). ⚠ **Номер, а не
    адрес**: разворачивает его сосед, у него же реестр и лежит, — пароль канала не
    ездит через сеть ради того, что он и так знает.

    ⚠ **Выход обязан быть тем же, каким ходят наши двери.** Сайт считает учётки по
    внешнему адресу: вход браузером с одного IP и присутствие с другого выглядят
    для него как две попытки на одну учётку, а не как одна анкета.
    """
    data = await _request('POST', '/api/browser/session',
                          {'url': str(url or ''), 'name': str(name or ''),
                           'storage_state': storage_state or None,
                           'site_id': int(site_id or 0),
                           'proxy_id': int(proxy_id or 0)},
                          timeout=RHAPSODY_API_TIMEOUT_SCENARIO)
    session = data.get('session') or data

    return str(session.get('session_id') or '')


# Список выходов и когда его брали. ⚠ **Кэш на секунды, а не на минуты.**
# Список читают с двух сторон: страница настроек при каждом показе и раздача при
# каждом входе анкеты — а меняется он редко, руками человека. Пятнадцать секунд
# убирают залп одинаковых запросов (открыл страницу, нажал сохранить, вернулся) и
# при этом не прячут только что заведённый выход дольше, чем длится вздох.
_proxy_cache: dict = {'rows': None, 'at': 0.0}
RHAPSODY_API_PROXY_FRESH_SEC = 15


def rhapsody_api_proxy_forget() -> None:
    """Сбрасывает кэш списка выходов. Звать после проверки: она меняет адреса."""
    _proxy_cache['rows'] = None


async def rhapsody_api_proxy_list(ready: bool = True) -> list:
    """Выходы из реестра соседа — **с адресами и паролями**.

    Args:
        ready: только проверенные и включённые. Так и спрашиваем: непроверенный
            выход нам не годится — неизвестно, каким адресом его видят, а считаем
            мы своих именно по адресу.

    Returns:
        list: строки `{id, name, url, check_ip, …}`; `url` с паролем канала.

    ⚠ **Раздачу ведём мы, а не он.** Реестр отвечает «какие выходы есть и какие
    живые»; кому какой достанется и сколько анкет допустимо на один внешний
    адрес, знаем только мы — предел выдумывает сайт знакомств, про который у
    соседа не слыхали. Поэтому здесь список, а не «дай мне выход»
    (`src/dating/proxy.py`).
    """
    # ⚠ Кэш общий на оба вида запроса, а фильтр `ready` накладывается **после**:
    # два кэша (годные и все) расходились бы в свежести, и страница показывала бы
    # одно, а раздача брала другое.
    if (_proxy_cache['rows'] is None
            or time.monotonic() - _proxy_cache['at'] > RHAPSODY_API_PROXY_FRESH_SEC):
        data = await _request('GET', '/api/browser/proxy/address?ready=false')
        _proxy_cache.update({'rows': list(data.get('result') or []),
                             'at': time.monotonic()})

    rows = _proxy_cache['rows']

    if ready:
        rows = [row for row in rows
                if row.get('check_ok') and row.get('is_enabled') and row.get('check_ip')]

    return list(rows)


async def rhapsody_api_proxy_check_all() -> list:
    """Просит реестр проверить **все** выходы и отдаёт итоги.

    ⚠ **Проверяет он, а не мы.** Своя проверка пошла бы с адреса нашего
    контейнера, и увиденный IP оказался бы не тем: реестр считает адрес по походу
    через сам прокси. Один источник — одна правда.

    Идёт секунды на выход и до минуты на два десятка: у соседа они проверяются
    пачками по пять. Срок ожидания поэтому сценарный, а не обычный.
    """
    data = await _request('POST', '/api/browser/proxy/check', {},
                          timeout=RHAPSODY_API_TIMEOUT_SCENARIO)

    # Проверка меняет увиденные адреса и годность — кэш списка после неё врёт.
    rhapsody_api_proxy_forget()

    return list(data.get('result') or [])


async def rhapsody_api_session_list() -> list:
    """Живые сессии браузера — чтобы погасить чужие вкладки этой же анкеты."""
    data = await _request('GET', '/api/browser/session')

    return list(data.get('sessions') or [])


async def rhapsody_api_session_close(session_id: str) -> bool:
    """Гасит сессию. Повтор не ошибка: результат тот же — сессии нет."""
    if not session_id:
        return False

    data = await _request('DELETE', f'/api/browser/session/{session_id}')

    return bool(data.get('closed'))


async def rhapsody_api_session_state(session_id: str) -> dict:
    """Снимает вход сессии (`storage_state`) — по нему потом идёт HTTP-опрос."""
    data = await _request('GET', f'/api/browser/session/{session_id}/storage_state')

    return data.get('storage_state') or data


async def rhapsody_api_ws_capture(url: str, storage_state: dict = None, session_id: str = '',
                                  warmup_url: str = '', click_selector: str = '',
                                  wait_sec: int = 30) -> list:
    """Снимает адреса вебсокетов, которые подняла страница.

    Адрес чат-сокета собирает JS страницы, в разметке его нет — поэтому браузер
    нужен один раз: открыли, подсмотрели, закрыли. Дальше сокет держит этот проект
    обычным клиентом из Python.
    """
    data = await _request('POST', '/api/browser/ws/capture',
                          {'url': str(url), 'session_id': str(session_id or ''),
                           'warmup_url': str(warmup_url or ''),
                           'click_selector': str(click_selector or ''),
                           'storage_state': storage_state or {},
                           'wait_sec': int(wait_sec)},
                          timeout=RHAPSODY_API_TIMEOUT_SCENARIO)

    return list(data.get('sockets') or [])


async def rhapsody_api_error_write(source: str, message: str, session_id: str = '',
                                   context: dict = None) -> None:
    """Кладёт строку во вкладку «Ошибки» страницы браузера RhapsodyAi.

    Не бросает: сбой журнала не должен ронять работу с анкетой. Это то же правило,
    что и у остальных журналов — упавшая запись хуже всего тем, что уносит с собой
    то, ради чего её писали.
    """
    try:
        await _request('POST', '/api/browser/error',
                       {'source': str(source), 'message': str(message)[:1000],
                        'session_id': str(session_id or ''), 'context': context or {}})
    except Exception as e:
        logger_info(f'[rhapsody] ошибка не записалась: {e}')


async def rhapsody_api_agent_create(node: str, workflow: str, name: str, title: str,
                                    variables: dict, site: str) -> dict:
    """Заводит агента анкеты в RhapsodyAi — прогоном его же workflow.

    Здесь только вызов: промпт, факты из биографии и связка с `dating_talk` — дело
    той стороны. Повторять её работу своей вставкой значило бы завести вторую
    дорогу, которая разойдётся с первой на первой же правке.

    `site` — метка сайта-владельца (`goldenbride`, `ladadate`): у соседа она
    обязательна и проверяется форматом `^[a-z0-9]+$`, иначе `422`. Собирает её
    `agent_site` из имени сайта-закладки; довод обязательный и без умолчания
    намеренно — агент без сайта у соседа не заводится, и подставить сюда что-нибудь
    «по умолчанию» значило бы приписать анкету чужому сайту.
    """
    data = await _request('POST', '/agent/create',
                          {'node': str(node), 'workflow': str(workflow), 'name': str(name),
                           'title': str(title), 'variables': variables or {},
                           'site': str(site)},
                          timeout=RHAPSODY_API_TIMEOUT_AGENT)

    return data.get('result') or {}


async def rhapsody_api_agent_find(name: str) -> dict:
    """Агент по системному имени. Нет такого — пустой словарь.

    Точечной ручкой, а не списком: `/agent/list` для неполноправной роли отдаёт
    только агентов её узлов, и служебной учётке приходит пустой массив. Здесь же
    спрашивается ровно один агент, про которого имя известно.
    """
    try:
        data = await _request('GET', f'/agent/name/{str(name).strip()}')
    except RhapsodyError as e:
        # 404 — это «агента нет», законный ответ, а не сбой связи.
        if ' → 404' in str(e):
            return {}
        raise

    return data.get('result') or {}


async def rhapsody_api_chat_talk(agent_id: int, question: str,
                                 variables: dict = None) -> dict:
    """Спрашивает агента и отдаёт ответ вместе с номером обмена разговора.

    Returns:
        dict: `text` — ответ агента (пустой законен: он может промолчать),
        `message_id` — номер строки `companion_session` у соседа. Ноль значит
        «обмена нет»: агент смолчал, прогон ушёл в «Тишину».

    ## ⚠⚠ Номер нужен, чтобы отметить доставку

    Сосед пишет обмен **внутри прогона** — до того, как мы попробуем отправить
    реплику. Отметку ставит `rhapsody_api_chat_delivered`, когда реплика реально
    ушла; без неё лента для агента эту строку не покажет.

    ⚠ Зовут это только дороги `dating_talk` — чат и два пула писем. Галерея и
    почтовые агенты номера не получают: их workflow в `companion_session` не
    пишут, и спрашивать его там было бы враньём.
    """
    payload = {'question': str(question), 'agent_id': int(agent_id),
               'variables': {'chat_timeout': RHAPSODY_API_CHAT_TIMEOUT, **(variables or {})}}

    data = await _request('POST', '/chat/ask', payload, timeout=RHAPSODY_API_TIMEOUT_AGENT)

    try:
        number = int(data.get('message_id') or 0)
    except (TypeError, ValueError):
        number = 0

    return {'text': str(data.get('result') or ''), 'message_id': number}


async def rhapsody_api_chat_ask(agent_id: int, question: str,
                                variables: dict = None) -> str:
    """Спрашивает агента и отдаёт его ответ строкой.

    Пустая строка — законный ответ: агент может намеренно промолчать, и это
    решение, а не сбой.

    ⚠ Тонкая обёртка над `rhapsody_api_chat_talk` для тех, кому номер обмена не
    нужен: галерея и почтовые агенты (`mail_agent.py`) идут другими workflow, и
    `companion_session` там не пишется вовсе.
    """
    return (await rhapsody_api_chat_talk(agent_id, question, variables))['text']


async def rhapsody_api_chat_delivered(message_id: int) -> bool:
    """Говорит соседу, что наша реплика дошла до собеседника.

    Args:
        message_id: номер обмена из `rhapsody_api_chat_talk`. Ноль — звать не
            надо, и функция молча отвечает `False`.

    Returns:
        bool: легла ли отметка. `False` у уже отмеченной строки — обычный исход,
        а не беда: ручка у соседа идемпотентна.

    ## ⚠⚠ Отказ не роняет отправку

    Реплика к этому моменту **уже ушла живому человеку**, и бросить исключение
    значило бы отменить то, что отменить нельзя. Неудача метки — строка в
    журнале: агент один раз увидит в истории лишнюю свою реплику, и это
    несравнимо дешевле.
    """
    number = int(message_id or 0)

    if number <= 0:
        return False

    try:
        data = await _request('POST', '/chat/message/delivered',
                              {'message_id': number})
    except RhapsodyError as e:
        logger_info(f'[rhapsody] отметка доставки {number} не легла: {e}')

        return False

    # ⚠ Разбор — тоже под защитой. Реплика к этому моменту уже ушла живому
    # человеку: ответь сосед кодом 200 с `result`, который не словарь (список,
    # строка, число), `.get('marked')` бросит `AttributeError`, и исключение
    # отменило бы то, что отменить нельзя.
    try:
        return bool((data.get('result') or {}).get('marked'))
    except AttributeError:
        logger_info(f'[rhapsody] отметка доставки {number}: '
                    f'сосед ответил не тем, что мы умеем прочитать: {data!r}')

        return False


# Сколько дней просить у журнала LLM. ⚠ Больше семи бессмысленно: сосед держит
# `llm_request` неделю и сам ограничивает запрос до семи дней. Число здесь затем,
# чтобы страница не обещала «за месяц» того, чего нет.
RHAPSODY_API_LLM_DAYS = 7


async def rhapsody_api_llm_usage(node: str, days: int = RHAPSODY_API_LLM_DAYS) -> dict:
    """Сколько запросов к LLM ушло и сколько токенов потрачено.

    Args:
        node: узел у соседа **по имени** (`AGENT_NODE`, «Dating»).
        days: за сколько дней. ⚠ Сосед клампит до семи — дольше журнал не живёт.

    Returns:
        dict: `days`, `requests`, `errors`, `tokens_input`, `tokens_output` и
        `rows` — разбивка по учётке и модели, как её отдал сосед.

    ⚠ **Считает это только сосед**, и своей копии у нас быть не должно: токены
    знает тот, кто зовёт модель, а вторая копия расходится с первой в первый же
    день. Отсюда и право `llm:view` у нашей служебной учётки — **только чтение**.

    ⚠ **Узел называем именем, а не номером.** Так мы адресуем его везде, где
    обращаемся к соседу (`POST /agent/create` берёт `node`), и номер взять негде:
    читать список узлов служебной учётке не положено, а зашить `2` в код значило бы
    сломать его при первом переносе базы. Имя разрешает сосед, у которого список
    узлов и так под рукой.

    ⚠ **Путь без `/api`**, в отличие от браузерных ручек: у соседа роутер LLM
    подключён в корень. Не описка — так у него и есть.

    ⚠ **Отказ не роняет страницу.** Статистика — не то, ради чего открывают
    панель: лежит сосед, и числа приходят нулями с причиной в `error`. Иначе
    вкладка «Статистика» гасла бы целиком из-за одной недоступной строки.
    """
    try:
        data = await _request(
            'GET', f'/llm/usage/stats?node={quote(str(node))}'
                   f'&days={max(1, min(int(days), RHAPSODY_API_LLM_DAYS))}')
    except RhapsodyError as e:
        return {'days': 0, 'requests': 0, 'errors': 0, 'tokens_input': 0,
                'tokens_output': 0, 'rows': [], 'error': str(e)}

    got = data.get('result') or {}
    rows = [dict(row) for row in (got.get('rows') or [])]

    return {'days': int(got.get('days') or 0),
            'requests': sum(int(row.get('requests') or 0) for row in rows),
            'errors': sum(int(row.get('errors') or 0) for row in rows),
            'tokens_input': sum(int(row.get('tokens_input') or 0) for row in rows),
            'tokens_output': sum(int(row.get('tokens_output') or 0) for row in rows),
            'rows': rows, 'error': ''}


# Имена подстановок, по которым узнаётся сценарий входа. Сайты называют поля
# по-разному, поэтому списки, а не одно имя.
RHAPSODY_API_LOGIN_KEYS = ('login', 'username', 'email', 'user')
RHAPSODY_API_PASSWORD_KEYS = ('password', 'pass', 'pwd')


async def _request(method: str, path: str, payload: dict = None,
                   timeout: float = RHAPSODY_API_TIMEOUT) -> dict:
    """Запрос к RhapsodyAi. Отказ соседа превращается в `RhapsodyError` с текстом.

    Код ответа сохраняется в тексте: 401 значит «протух токен», 404 — «нет такой
    сессии», и это разные поводы для звонка.
    """
    if not RHAPSODY_API_TOKEN:
        raise RhapsodyError('не задан RHAPSODY_TOKEN — обращаться нечем')

    headers = {'Authorization': f'Bearer {RHAPSODY_API_TOKEN}'}

    # ⚠ **Каждое обращение к соседу видно в логе — и запрос, и ответ.**
    #
    # Без этого разбор упирается в пустоту: отправка не удалась, человек смотрит
    # журнал анкеты и видит только «не ушло», а куда ходили, что послали и что
    # сосед ответил — не видно нигде. Так и вышло: сообщение не отправилось, и
    # сказать почему было нечем.
    #
    # Пишем **здесь**, в единственной двери, а не у вызывающих: их десяток, и
    # первый забытый снова оставил бы слепое место.
    logger_info(f'[rhapsody] → {method} {path}{_payload_short(payload)}')
    started = time.monotonic()

    try:
        # Клиент разовый — таймаут-то у каждого вызова свой, — а соединения идут
        # из общего транспорта: круги ходят к соседу сотнями, и всякий раз
        # заводить с ним TCP и TLS незачем. Клиента не закрываем: с ним ушёл бы
        # и общий транспорт, остальные вызовы остались бы без пула.
        client = httpx.AsyncClient(timeout=timeout, transport=http_pool_transport())
        response = await client.request(method, RHAPSODY_API_URL + path,
                                        json=payload, headers=headers)
    except httpx.HTTPError as e:
        # Обрыв связи — тоже ответ, и в логе он нужен: по нему видно, что дело не в
        # соседе, а в сети или в том, что его вовсе нет.
        logger_info(f'[rhapsody] ← {method} {path}: связь оборвалась за '
                    f'{_ms(started)} мс: {e}')
        await _note(method, path, 0, False, f'RhapsodyAi недоступен: {e}', started)

        raise RhapsodyError(f'RhapsodyAi недоступен: {e}')

    logger_info(f'[rhapsody] ← {response.status_code} {path} за {_ms(started)} мс'
                f'{_body_short(response)}')

    if response.status_code >= 400:
        await _note(method, path, response.status_code, False,
                    _error_text(path, response), started)

        raise RhapsodyError(_error_text(path, response), code=response.status_code)

    await _note(method, path, response.status_code, True, '', started)

    try:
        return response.json() if response.content else {}
    except ValueError:
        raise RhapsodyError(f'{path} → ответ не JSON: {response.text[:150]}')


async def _note(method: str, path: str, status: int, ok: bool, error: str,
                started: float) -> None:
    """Строка в журнал внешних запросов: сосед — тоже наружу.

    ⚠⚠ **Пишем здесь, в единственной двери.** Вызывающих у соседа десяток, и
    первый забытый снова оставил бы слепое место — тот же довод, по которому
    здесь же пишется и лог процесса.

    ⚠ **Анкеты у строки нет.** К соседу ходят и по анкете (прогон сценария), и по
    сайту (справочник, агенты), и вовсе без них (проверка выходов). Дверь про
    **соседа**, а не про девушку; приписать ей анкету значило бы соврать в двух
    случаях из трёх. Чья работа — видно в журнале прогонов.

    ⚠ **Путь без тела.** В теле прогона едут пароли анкет (`secrets`), а журнал
    читают в консоли и копируют в переписку.

    Ничего не бросает: сбой журнала не имеет права уронить работу.
    """
    from src.dating.request import (REQUEST_RHAPSODY,
                                           request_about_get,
                                           request_write)

    # Чья это работа — спрашиваем у неё самой, а не выводим из пути.
    profile_id, site_id = request_about_get()

    # ⚠ Строку запроса режем: в ней бывают номера и метки, но длина её не
    # ограничена, а колонка — 255 знаков.
    await request_write(door=REQUEST_RHAPSODY, method=str(method or ''),
                               path=str(path or '')[:255], status=int(status or 0),
                               ok=bool(ok), error=str(error or ''),
                               ms=_ms(started),
                               profile_id=profile_id, site_id=site_id)


def _ms(started: float) -> int:
    """Сколько шёл запрос. Прогон сценария длится десятки секунд, и по времени
    видно, ждали мы браузер или получили отказ сразу."""
    return int((time.monotonic() - started) * 1000)


def _payload_short(payload) -> str:
    """Тело запроса для лога — коротко и **без секретов**.

    ⚠ Секреты вырезаются по имени ключа, а не по значению: сравнивать значения
    неоткуда — пароль приходит сюда уже подставленным. Ключ `secrets` кладёт
    `scenario`, и его содержимое в лог попадать не должно ни при каких
    условиях.
    """
    if not isinstance(payload, dict) or not payload:
        return ''

    safe = {}
    for key, value in payload.items():
        if key in RHAPSODY_API_LOG_HIDE:
            safe[key] = f'<скрыто: {len(value)}>' if hasattr(value, '__len__') else '<скрыто>'
        else:
            safe[key] = value

    text = json.dumps(safe, ensure_ascii=False)

    return f' {text[:RHAPSODY_API_LOG_MAX]}' + ('…' if len(text) > RHAPSODY_API_LOG_MAX else '')


def _body_short(response) -> str:
    """Ответ соседа для лога, обрезанный по длине.

    Целиком нельзя: список сайтов со всеми настройками — это килобайты на каждый
    показ страницы, а в логе нужен признак «что пришло», а не сами данные.
    """
    body = (response.text or '').strip().replace('\n', ' ')
    if not body:
        return ''

    return f': {body[:RHAPSODY_API_LOG_MAX]}' + ('…' if len(body) > RHAPSODY_API_LOG_MAX else '')


def _error_text(path: str, response) -> str:
    """Отказ соседа — фразой для журнала анкеты, а не кодом и телом ответа.

    Строка попадает человеку на страницу, и «`/api/browser/scenario/259/run` →
    409: {"detail":"предел сессий исчерпан: 25"}» не говорит ему ничего: причина
    утоплена в служебной обвязке, а числу 409 взяться в голове неоткуда.

    Собираем из трёх частей — что это значит, что сказал сосед, куда стучались.
    Код оставляем в хвосте: по нему ищут в его логах.
    """
    code = int(response.status_code)

    detail = ''
    try:
        body = response.json()
        detail = str(body.get('detail') or '') if isinstance(body, dict) else ''
    except ValueError:
        detail = ''
    detail = detail or response.text[:200]

    meaning = RHAPSODY_API_CODE_MEANING.get(code, 'сосед ответил отказом')

    return f'{meaning}: {detail} ({path} → {code})' if detail \
        else f'{meaning} ({path} → {code})'
