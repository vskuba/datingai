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

import httpx

from config.config import config_get
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


class RhapsodyError(RuntimeError):
    """RhapsodyAi не ответил или ответил отказом. Текст пригоден для журнала анкеты."""


async def rhapsody_api_site_get(site_id: int) -> dict:
    """Сайт-закладка целиком: адрес и настройка присутствия (`presence`).

    Отдельной ручкой, а не из списка: в списке `presence` нет — она простыня, и
    возить её в каждой строке селекта незачем.
    """
    data = await _request('GET', f'/api/browser/site/{int(site_id)}')

    return data.get('result') or {}


async def rhapsody_api_scenario_find(site_id: int, name: str) -> dict:
    """Сценарий сайта по имени. Нет такого — пустой словарь, решает вызывающий."""
    scenarios = await _request('GET', '/api/browser/scenario')
    for row in scenarios.get('result') or []:
        if int(row.get('site_id') or 0) == int(site_id) and str(row.get('name') or '') == str(name):
            return row

    return {}


async def rhapsody_api_site_list() -> list:
    """Сайты-закладки соседа — для селекта на странице."""
    data = await _request('GET', '/api/browser/site')

    return list(data.get('result') or [])


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
                                    secrets: dict, keep_session: bool = True) -> dict:
    """Гонит сценарий в браузере RhapsodyAi.

    `secrets` уходят отдельным полем, а не в `variables`: RhapsodyAi прячет их в
    своём журнале прогонов, и пароль анкеты туда не попадает.
    """
    return (await _request('POST', f'/api/browser/scenario/{int(scenario_id)}/run',
                           {'session_id': str(session_id or ''),
                            'variables': variables or {},
                            'secrets': secrets or {},
                            'keep_session': bool(keep_session)},
                           timeout=RHAPSODY_API_TIMEOUT_SCENARIO)).get('result') or {}


async def rhapsody_api_session_open(url: str = '', name: str = '', storage_state: dict = None,
                                    site_id: int = 0) -> str:
    """Заводит сессию браузера и отдаёт её номер."""
    data = await _request('POST', '/api/browser/session',
                          {'url': str(url or ''), 'name': str(name or ''),
                           'storage_state': storage_state or None,
                           'site_id': int(site_id or 0)},
                          timeout=RHAPSODY_API_TIMEOUT_SCENARIO)
    session = data.get('session') or data

    return str(session.get('session_id') or '')


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


async def rhapsody_api_session_goto(session_id: str, url: str) -> dict:
    """Переводит страницу сессии на адрес."""
    return await _request('POST', f'/api/browser/session/{session_id}/goto',
                          {'url': str(url)}, timeout=RHAPSODY_API_TIMEOUT_SCENARIO)


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
                                    variables: dict) -> dict:
    """Заводит агента анкеты в RhapsodyAi — прогоном его же workflow.

    Здесь только вызов: промпт, факты из биографии и связка с `dating_talk` — дело
    той стороны. Повторять её работу своей вставкой значило бы завести вторую
    дорогу, которая разойдётся с первой на первой же правке.
    """
    data = await _request('POST', '/agent/create',
                          {'node': str(node), 'workflow': str(workflow), 'name': str(name),
                           'title': str(title), 'variables': variables or {}},
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


async def rhapsody_api_chat_ask(agent_id: int, question: str, companion_id: int = 0,
                                variables: dict = None) -> str:
    """Спрашивает агента и отдаёт его ответ строкой.

    Пустая строка — законный ответ: агент может намеренно промолчать, и это
    решение, а не сбой.
    """
    payload = {'question': str(question), 'agent_id': int(agent_id),
               'variables': {'chat_timeout': RHAPSODY_API_CHAT_TIMEOUT, **(variables or {})}}
    if companion_id:
        payload['companion_id'] = int(companion_id)

    data = await _request('POST', '/chat/ask', payload, timeout=RHAPSODY_API_TIMEOUT_AGENT)

    return str(data.get('result') or '')


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

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, RHAPSODY_API_URL + path,
                                            json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise RhapsodyError(f'RhapsodyAi недоступен: {e}')

    if response.status_code >= 400:
        raise RhapsodyError(f'{path} → {response.status_code}: {response.text[:200]}')

    try:
        return response.json() if response.content else {}
    except ValueError:
        raise RhapsodyError(f'{path} → ответ не JSON: {response.text[:150]}')
