"""Дверь к **API самого сайта знакомств**: вход, токен, один запрос.

У сайта есть своё API (`https://ladadate.com/api.html`): `POST /auth/login` отдаёт
JWT, дальше всё под `Authorization: Bearer`. Здесь лежит то, что одинаково у любой
его ручки, — вход, хранение токена, повтор после `401` и превращение кода отказа во
фразу для журнала.

## Почему отдельным модулем, а не в каждом по копии

Ручек у сайта уже две породы: списки собеседников (`dating_people_api.py`) и
отправка писем (`dating_mail_send.py`). Обе ходят **под одной и той же анкетой**, и
токен у них обязан быть общим.

Разведи мы их — каждая заводила бы свой вход, и на одну анкету приходилось бы два
захода вместо одного. Сайт считает частые входы подозрением и отвечает `403
ERROR_TOO_MANY_REQUESTS`, а расплата за это — не отказ в запросе, а блокировка
учётки. Общий токен здесь не про красоту кода.

⚠ **Это не «дверь наружу вообще».** К соседу (RhapsodyAi) ведёт своя и
единственная — `src/rhapsody/rhapsody_api.py`. Здесь дверь к сайту знакомств, и
границы у них разные: сосед даёт браузер и агентов, сайт — свои данные о своих
людях.

## Что здесь есть, а чего нет

Здесь — транспорт: путь, заголовок, токен, коды отказа. **Разбор ответа — не
здесь**: имена полей у каждой ручки свои (`Users` у списков, `CheckId` у проверки
отправки), и знать их должен тот, кто ручку зовёт.

## Токен

JWT живёт 12 часов (проверено: `iat` и `exp` в самом токене). Держим его в Redis по
анкете и переспрашиваем, только когда сайт ответил `401`, — то есть один вход в
сутки вместо входа на каждый запрос.

⚠ **Пароль в кэше не участвует.** Лежит только сам токен: утечка кэша не даёт войти
заново, когда он протухнет.
"""

import asyncio
import base64
import json
import time

import httpx

from logging_.logging_ import logger_info
from redis_.redis_ import redis_conn_get

# Вход. Путь из документации сайта.
DATING_SITE_API_LOGIN_PATH = '/auth/login'

# Токен анкеты в Redis. С именем проекта в начале: Redis общий с очередью `dating`.
#
# ⚠ Ключ **тот же, что был у списков собеседников**, и это намеренно: токен один на
# анкету, и смени мы имя — все живые токены разом стали бы не найдены, а сайт
# получил бы всплеск входов ровно от того, что мы переставили модули.
DATING_SITE_API_TOKEN_KEY = 'dating:site_api:{profile_id}'

# Замок на **сам вход**, а не на его итог.
#
# ⚠ **Общего токена мало, и это стоило тринадцати отказов за пять дней.** Токен
# делится через Redis, но два вызова, не нашедшие его одновременно, оба шли на
# `/auth/login` — а два входа подряд под одной учёткой сайт считает подозрением и
# отвечает `400 Login or password invalid`. Читалось это как «неверный пароль»,
# хотя пароль верный: тот же самый работал в соседнем вызове секундой позже.
#
# Ловилось только на самой занятой анкете и только на фоновом списке избранных:
# он обновляется в отдельной задаче (`ensure_future`), то есть ровно параллельно
# всему прочему. Остальные анкеты работы делают меньше и до гонки не доживали.
DATING_SITE_API_LOGIN_LOCK_KEY = 'dating:site_api:lock:{profile_id}'

# Срок замка. Больше времени ответа сайта (10 с) с запасом: замок обязан пережить
# самый медленный вход, иначе смысла в нём нет. И заведомо конечный — умерший
# процесс не должен запирать анкету навсегда.
DATING_SITE_API_LOGIN_LOCK_TTL = 30

# Сколько ждём чужого входа, прежде чем идти своим. Полминуты — это срок замка:
# дольше ждать нечего, к тому времени держатель либо положил токен, либо умер.
DATING_SITE_API_LOGIN_WAIT_STEP = 0.25
DATING_SITE_API_LOGIN_WAIT_TRIES = 120

# Сколько держим токен, если срок в нём не прочитался. Час — заведомо меньше
# суточного: протухший токен стоит одного лишнего входа, а слишком долгий — работы,
# которая молча перестала идти.
DATING_SITE_API_TOKEN_KEEP = 3600

# Запас до конца срока. Токен, которому осталась минута, брать незачем: запрос с ним
# уйдёт уже просроченным, и мы заплатим лишним кругом «401 → вход → повтор».
DATING_SITE_API_TOKEN_MARGIN = 300

# Сколько ждём ответа. Сайт отвечает за 70–170 мс; десяти секунд хватает с запасом на
# его плохой день, а больше ждать нечего.
DATING_SITE_API_TIMEOUT = 10.0

# Браузерный `User-Agent`: сайт отдаёт API тому, кто похож на его же страницу. То же
# правило и по той же причине, что в `dating_presence.py`.
DATING_SITE_API_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')

# Что означают коды отказа — словами. Строка уезжает в журнал анкеты и на страницу, и
# «403» там не говорит ничего никому. Тот же приём, что у двери к соседу.
DATING_SITE_API_CODE_MEANING = {
    400: 'сайт не принял вход: неверный логин или пароль',
    401: 'сайт не признал вход',
    403: 'сайт просит подождать: слишком часто',
    404: 'сайт не знает такой ручки',
    500: 'сайт ответил ошибкой',
}


class DatingSiteApiError(RuntimeError):
    """API сайта не ответило или ответило отказом. Текст годен для журнала анкеты."""


async def dating_site_api_call(profile: dict, base: str, password: str, path: str,
                               payload: dict = None, method: str = '') -> dict:
    """Один запрос к API сайта под этой анкетой. Ответ — разобранным.

    Args:
        profile: строка `dating_profile`: нужны `id` и `login`.
        base: корень API сайта из настройки сайта.
        password: пароль для входа — свой у анкеты или общий у сайта.
        path: путь ручки, от корня (`/users/online`).
        payload: тело запроса. `None` — запрос без тела.
        method: `GET` или `POST`. Пусто — по наличию тела, как и было у списков.

    Returns:
        dict: тело ответа. Не словарь (сайт вправе отдать список) — пустой словарь:
        разбирать всё равно вызывающему, и пусть он решает сам.

    Raises:
        DatingSiteApiError: сайт не ответил, отказал или прислал не JSON.

    ⚠ **`401` не поднимается наверх с первого раза.** Токен живёт 12 часов и
    протухает посреди рабочего дня — это обычное дело, а не отказ. Первый `401`
    означает «войди заново», и только второй — «пароль не подошёл».
    """
    profile_id = int(profile.get('id') or 0)
    verb = str(method or ('POST' if payload is not None else 'GET')).upper()

    token = _token_get(profile_id)
    async with httpx.AsyncClient(timeout=DATING_SITE_API_TIMEOUT,
                                 headers={'User-Agent': DATING_SITE_API_UA}) as client:
        if not token:
            token = await _login_once(client, profile, base, password)

        answer = await _request(client, base, path, token, payload, verb)
        if answer.status_code == 401:
            _token_drop(profile_id)
            token = await _login_once(client, profile, base, password)
            answer = await _request(client, base, path, token, payload, verb)

        if answer.status_code >= 400:
            raise DatingSiteApiError(_refusal(path, answer))

        return _body(path, answer)


# ── Приватное ────────────────────────────────────────────────────────────────

async def _login_once(client: httpx.AsyncClient, profile: dict, base: str,
                      password: str) -> str:
    """Вход, которого в один момент времени идёт ровно один на анкету.

    ⚠ **Сайт считает второй одновременный вход подозрением** и отвечает на него
    `400 Login or password invalid` — тем же кодом, что и на настоящую опечатку в
    пароле. Отличить их по ответу нельзя, и в журнале это пять дней читалось как
    «пароль неверный» у анкеты, которая тем же паролем прекрасно работала.

    Кто пришёл первым — входит; остальные ждут его токен и берут готовый. Ждать
    выгоднее, чем входить самому: чужой вход занимает те же доли секунды, а второй
    вход не просто лишний, он **ломает первый**.

    ⚠ **Не дождались — идём сами.** Замок живёт полминуты и переживает любой
    здоровый вход; висит он дольше — значит державший умер, и вечно ждать мертвеца
    хуже, чем рискнуть гонкой, которой уже не с кем случиться.

    ⚠ **Сбой Redis — входим без замка.** Без Redis нет и общего токена, то есть
    каждый вызов и так логинится сам; запирать анкету наглухо из-за недоступного
    Redis значило бы менять редкую гонку на полный простой.
    """
    profile_id = int(profile.get('id') or 0)

    for _ in range(DATING_SITE_API_LOGIN_WAIT_TRIES):
        if _lock_take(profile_id):
            try:
                return await _login(client, profile, base, password)
            finally:
                _lock_free(profile_id)

        await asyncio.sleep(DATING_SITE_API_LOGIN_WAIT_STEP)

        # ⚠ Токен читаем **после** сна, а не до: до сна его заведомо нет — мы
        # только что его не нашли, оттого и вошли в эту функцию.
        token = _token_get(profile_id)
        if token:
            return token

    return await _login(client, profile, base, password)


def _lock_take(profile_id: int) -> bool:
    """Занять право на вход. `True` — входить нам.

    ⚠ Сбой Redis отвечает `True`, а не `False`: см. `_login_once`.
    """
    try:
        return bool(redis_conn_get().set(
            DATING_SITE_API_LOGIN_LOCK_KEY.format(profile_id=profile_id), '1',
            nx=True, ex=DATING_SITE_API_LOGIN_LOCK_TTL))
    except Exception as e:
        logger_info(f'[dating] замок входа в API не взялся: {e}')

        return True


def _lock_free(profile_id: int) -> None:
    """Отпустить право на вход. Молча: замок и сам погаснет по сроку."""
    try:
        redis_conn_get().delete(
            DATING_SITE_API_LOGIN_LOCK_KEY.format(profile_id=profile_id))
    except Exception as e:
        logger_info(f'[dating] замок входа в API не снялся: {e}')


async def _login(client: httpx.AsyncClient, profile: dict, base: str,
                 password: str) -> str:
    """Вход: логин и пароль в обмен на токен. Кладём токен в Redis.

    ⚠ Пароля здесь может не быть вовсе: у импортированных анкет своего нет, а общий
    задают настройкой сайта. Говорим об этом прямо — молчаливый 400 от сайта человек
    прочитал бы как «сайт сломался».
    """
    login = str(profile.get('login') or '')
    if not login or not password:
        raise DatingSiteApiError('нечем войти в API сайта: нет логина или пароля '
                                 '(настройка сайта «Пароль анкет для API»)')

    answer = await _request(client, base, DATING_SITE_API_LOGIN_PATH, '',
                            {'Login': login, 'Password': password}, 'POST')
    if answer.status_code >= 400:
        raise DatingSiteApiError(_refusal(DATING_SITE_API_LOGIN_PATH, answer))

    token = str(_body(DATING_SITE_API_LOGIN_PATH, answer).get('Token') or '')
    if not token:
        raise DatingSiteApiError('сайт не дал токена в ответ на вход')

    _token_put(int(profile.get('id') or 0), token)

    return token


async def _request(client: httpx.AsyncClient, base: str, path: str, token: str,
                   payload: dict, method: str) -> httpx.Response:
    """Один запрос к сайту. Обрыв связи — сразу словами, а не стеком."""
    url = str(base).rstrip('/') + path
    headers = {'Authorization': f'Bearer {token}'} if token else {}

    try:
        if method == 'POST':
            return await client.post(url, json=payload or {}, headers=headers)

        return await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        raise DatingSiteApiError(f'сайт не отвечает: {e}')


def _body(path: str, answer: httpx.Response) -> dict:
    """Ответ разобранным. Не JSON — отказ с куском тела: по нему и разбираются."""
    try:
        body = answer.json()
    except ValueError:
        raise DatingSiteApiError(f'{path} → ответ не JSON: {answer.text[:150]}')

    return body if isinstance(body, dict) else {}


def _refusal(path: str, answer: httpx.Response) -> str:
    """Код отказа — фразой для журнала анкеты, с объяснением сайта и кодом в хвосте."""
    code = int(answer.status_code)

    # Сайт объясняет отказ двумя полями сразу: `Error` — фразой для человека,
    # `Reason` — кодом для поиска в его поддержке. Берём оба: фраза без кода
    # непроверяема, код без фразы непонятен.
    detail = ''
    try:
        body = answer.json()
        if isinstance(body, dict):
            said, code_word = str(body.get('Error') or ''), str(body.get('Reason') or '')
            detail = f'{said} ({code_word})' if said and code_word else said or code_word
    except ValueError:
        detail = answer.text[:150]

    meaning = DATING_SITE_API_CODE_MEANING.get(code, 'сайт ответил отказом')

    return f'{meaning}: {detail} ({path} → {code})' if detail \
        else f'{meaning} ({path} → {code})'


def _token_get(profile_id: int) -> str:
    """Токен анкеты из Redis. Сбой Redis — не беда: войдём заново."""
    try:
        raw = redis_conn_get().get(DATING_SITE_API_TOKEN_KEY.format(profile_id=profile_id))
    except Exception as e:
        logger_info(f'[dating] токен API не прочитался: {e}')

        return ''

    return str(raw or '')


def _token_put(profile_id: int, token: str) -> None:
    """Кладёт токен на его же срок, минус запас.

    Срок берём **из самого токена**: сайт вправе выдавать их на сколько угодно, и
    своя константа разошлась бы с ним молча — работа перестала бы идти, а причина
    «токен протух» нигде бы не всплыла.
    """
    try:
        redis_conn_get().set(DATING_SITE_API_TOKEN_KEY.format(profile_id=profile_id),
                             token, ex=_token_keep(token))
    except Exception as e:
        logger_info(f'[dating] токен API не записался: {e}')


def _token_drop(profile_id: int) -> None:
    """Забывает токен — после 401, то есть когда сайт его уже не принимает."""
    try:
        redis_conn_get().delete(DATING_SITE_API_TOKEN_KEY.format(profile_id=profile_id))
    except Exception as e:
        logger_info(f'[dating] токен API не убрался: {e}')


def _token_keep(token: str) -> int:
    """Сколько токену осталось жить по его же `exp`, за вычетом запаса.

    Не разобрался — умолчание: подпись мы всё равно не проверяем (это дело сайта), и
    чужой формат токена не повод отказываться от работы.
    """
    try:
        payload = str(token).split('.')[1]
        payload += '=' * (-len(payload) % 4)
        exp = int(json.loads(base64.urlsafe_b64decode(payload)).get('exp') or 0)
    except Exception:
        return DATING_SITE_API_TOKEN_KEEP

    left = int(exp - time.time() - DATING_SITE_API_TOKEN_MARGIN)

    return left if left > 60 else DATING_SITE_API_TOKEN_KEEP
