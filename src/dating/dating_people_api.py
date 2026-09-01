"""Списки собеседников через **API сайта**, без браузера и без соседа.

У сайта знакомств есть своё API (`https://ladadate.com/api.html`): вход отдаёт
JWT, а `GET /users/{state}` — списки людей. Всё, ради чего до сих пор гоняли
сценарий в живом Chromium, здесь получается одним HTTP-запросом.

## Зачем, если сценарий работал

Замеры на живом сайте, анкета 849:

| | сценарий у соседа | это API |
|---|---|---|
| избранные | 35–40 с, вкладка Chromium | **70 мс**, 17 человек |
| кто в сети | 35–40 с, **20 человек** | **166 мс**, **201 человек** |

Двадцать против двухсот — не мелочь: сценарий снимал первую страницу списка, и
никакой пометки о том, что список неполон, на странице не было. То есть дело не
только в скорости.

Дальше следствием: вкладка в пуле соседа не занимается вовсе (пул на 25 и общий
с его собственными задачами), очередь прогонов не растёт, и колонка перестаёт
зависеть от того, поднят ли RhapsodyAi.

## ⚠ Это не замена сценариям вообще

Сценарии остаются везде, где нужен настоящий DOM: вход с сохранением сессии,
открытие диалога, ответ в чат. Здесь только списки, и только там, где сайт
позволяет их спросить.

Отсюда и настройка: `people_source` у сайта (`api` или `scenario`, умолчание —
`scenario`). Сайт без API работает как работал, и новый сайт ничего не ломает.

## Что настраивается, а что живёт в коде

На странице «Сайты» — то, что **у каждой установки своё**: адрес API и пароль.
Пути (`/auth/login`, `/users/{state}`) и имена полей ответа лежат здесь
константами, а не настройкой.

Так — намеренно. Настройкой описывают то, что меняется без правки кода; протокол
второго сайта, когда он появится, всё равно потребует своего разбора — у него
будут другие имена полей и, скорее всего, другой способ входа. Городить под это
мини-язык в JSON, имея один сайт, значит выдумывать обобщение по одному примеру.
Появится второй — вот тогда протокол и переедет в настройку (как это уже сделано
с `presence` у соседа).

## Токен

JWT живёт **12 часов** (проверено: `iat` и `exp` в самом токене). Держим его в
Redis по анкете и переспрашиваем, только когда сайт ответил 401 — то есть один
вход в сутки вместо входа на каждый список.

⚠ **Пароль в токене не участвует.** В Redis лежит только сам токен: утечка кэша
не даёт войти заново, когда он протухнет.
"""

import base64
import json
import time

import httpx

from logging_.logging_ import logger_info
from redis_.redis_ import redis_conn_get

from src.dating.dating_people import (DATING_PEOPLE_NAME_MAX, DATING_PEOPLE_UID_MAX,
                                      dating_people_photo_uid)

# Что зовём. Пути из документации сайта; `{state}` — вид списка.
DATING_PEOPLE_API_LOGIN_PATH = '/auth/login'
DATING_PEOPLE_API_USERS_PATH = '/users/{state}'

# Наш вид списка → `state` сайта.
#
# ⚠ **Виды, которых здесь нет, API не отдаёт.** `dialog` снимается со страницы
# чата сценарием: у сайта есть `/messages`, но это письма, а не список диалогов, и
# сворачивать одно в другое — отдельная работа. Не нашлось вида — колонка честно
# уходит к сценарию.
DATING_PEOPLE_API_STATES = {'favorite': 'my-favorites', 'online': 'online'}

# Имена полей в ответе сайта.
DATING_PEOPLE_API_ROWS = 'Users'
DATING_PEOPLE_API_FIELDS = {'uid': 'AccountId', 'name': 'Name', 'age': 'Age',
                            'photo': 'ProfilePhoto'}

# Токен анкеты в Redis. С именем проекта в начале: Redis общий с очередью `dating`.
DATING_PEOPLE_API_TOKEN_KEY = 'dating:site_api:{profile_id}'

# Сколько держим токен, если срок в нём не прочитался. Час — заведомо меньше
# суточного: протухший токен стоит одного лишнего входа, а слишком долгий —
# колонки, которая молча не обновляется.
DATING_PEOPLE_API_TOKEN_KEEP = 3600

# Запас до конца срока. Токен, которому осталась минута, брать незачем: запрос с
# ним уйдёт уже просроченным, и мы заплатим лишним кругом «401 → вход → повтор».
DATING_PEOPLE_API_TOKEN_MARGIN = 300

# Сколько ждём ответа. Сайт отвечает за 70–170 мс; десяти секунд хватает с запасом
# на его плохой день, а больше ждать нечего — список спросят заново.
DATING_PEOPLE_API_TIMEOUT = 10.0

# Браузерный `User-Agent`: сайт отдаёт API тому, кто похож на его же страницу. То
# же правило и по той же причине, что в `dating_presence.py`.
DATING_PEOPLE_API_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36')

# Что означают коды отказа — словами. Строка уезжает в журнал анкеты и на
# страницу, и «403» там не говорит ничего никому. Тот же приём, что у двери к
# соседу (`rhapsody_api.py`).
DATING_PEOPLE_API_CODE_MEANING = {
    400: 'сайт не принял вход: неверный логин или пароль',
    401: 'сайт не признал вход',
    403: 'сайт просит подождать: слишком часто',
    404: 'сайт не знает такой ручки',
    500: 'сайт ответил ошибкой',
}


class DatingPeopleApiError(RuntimeError):
    """API сайта не ответило или ответило отказом. Текст годен для журнала анкеты."""


def dating_people_api_kinds() -> set:
    """Виды списков, которые API умеет отдавать."""
    return set(DATING_PEOPLE_API_STATES)


async def dating_people_api_rows(profile: dict, kind: str, base: str,
                                 password: str) -> list[dict]:
    """Список собеседников с сайта — тем же видом, что даёт разбор сценария.

    Args:
        profile: строка `dating_profile`: нужны `id` и `login`.
        kind: наш вид списка (`favorite`, `online`).
        base: корень API сайта из настройки.
        password: пароль для входа — свой у анкеты или общий у сайта.

    Returns:
        list: записи `{uid, name, age, photo, photo_uid, online, is_new, link}` —
        ровно те же поля, что у сценария. Страница и кэш об источнике не знают, и
        знать им незачем.

    Raises:
        DatingPeopleApiError: сайт не ответил, отказал или прислал не то.

    ⚠ **401 не поднимается наверх с первого раза.** Токен живёт 12 часов и
    протухает посреди рабочего дня — это обычное дело, а не отказ. Первый 401
    означает «войди заново», и только второй — «пароль не подошёл».
    """
    state = DATING_PEOPLE_API_STATES.get(kind)
    if not state:
        raise DatingPeopleApiError(f'API сайта не отдаёт список «{kind}»')

    profile_id = int(profile.get('id') or 0)
    path = DATING_PEOPLE_API_USERS_PATH.format(state=state)

    token = _token_get(profile_id)
    async with httpx.AsyncClient(timeout=DATING_PEOPLE_API_TIMEOUT,
                                 headers={'User-Agent': DATING_PEOPLE_API_UA}) as client:
        if not token:
            token = await _login(client, profile, base, password)

        answer = await _request(client, base, path, token)
        if answer.status_code == 401:
            _token_drop(profile_id)
            token = await _login(client, profile, base, password)
            answer = await _request(client, base, path, token)

        if answer.status_code >= 400:
            raise DatingPeopleApiError(_refusal(path, answer))

        body = _body(path, answer)

    return _rows(body.get(DATING_PEOPLE_API_ROWS), online=(kind == 'online'))


# ── Приватное ────────────────────────────────────────────────────────────────

async def _login(client: httpx.AsyncClient, profile: dict, base: str,
                 password: str) -> str:
    """Вход: логин и пароль в обмен на токен. Кладём токен в Redis.

    ⚠ Пароля здесь может не быть вовсе: у импортированных анкет своего нет, а
    общий задают настройкой сайта. Говорим об этом прямо — молчаливый 400 от сайта
    человек прочитал бы как «сайт сломался».
    """
    login = str(profile.get('login') or '')
    if not login or not password:
        raise DatingPeopleApiError('нечем войти в API сайта: нет логина или пароля '
                                   '(настройка сайта «Пароль анкет для API»)')

    answer = await _request(client, base, DATING_PEOPLE_API_LOGIN_PATH, '',
                            payload={'Login': login, 'Password': password})
    if answer.status_code >= 400:
        raise DatingPeopleApiError(_refusal(DATING_PEOPLE_API_LOGIN_PATH, answer))

    token = str(_body(DATING_PEOPLE_API_LOGIN_PATH, answer).get('Token') or '')
    if not token:
        raise DatingPeopleApiError('сайт не дал токена в ответ на вход')

    _token_put(int(profile.get('id') or 0), token)

    return token


async def _request(client: httpx.AsyncClient, base: str, path: str, token: str,
                   payload: dict = None) -> httpx.Response:
    """Один запрос к сайту. Обрыв связи — сразу словами, а не стеком."""
    url = str(base).rstrip('/') + path
    headers = {'Authorization': f'Bearer {token}'} if token else {}

    try:
        if payload is None:
            return await client.get(url, headers=headers)

        return await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise DatingPeopleApiError(f'сайт не отвечает: {e}')


def _body(path: str, answer: httpx.Response) -> dict:
    """Ответ разобранным. Не JSON — отказ с куском тела: по нему и разбираются."""
    try:
        body = answer.json()
    except ValueError:
        raise DatingPeopleApiError(f'{path} → ответ не JSON: {answer.text[:150]}')

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

    meaning = DATING_PEOPLE_API_CODE_MEANING.get(code, 'сайт ответил отказом')

    return f'{meaning}: {detail} ({path} → {code})' if detail \
        else f'{meaning} ({path} → {code})'


def _rows(users, online: bool) -> list[dict]:
    """Записи сайта — в наш вид.

    Args:
        users: список из ответа сайта.
        online: список снят по состоянию «в сети» — значит все в нём в сети.
            Признака у самой записи нет, и выдумывать его из воздуха нельзя:
            точка «в сети» у избранных осталась бы серой, и это правда — мы про
            них этого не знаем.
    """
    if not isinstance(users, list):
        return []

    found, seen = [], set()
    for user in users:
        if not isinstance(user, dict):
            continue

        uid = str(user.get(DATING_PEOPLE_API_FIELDS['uid']) or '').strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)

        photo = str(user.get(DATING_PEOPLE_API_FIELDS['photo']) or '').strip()
        age = user.get(DATING_PEOPLE_API_FIELDS['age'])

        found.append({'uid': uid[:DATING_PEOPLE_UID_MAX],
                      'name': str(user.get(DATING_PEOPLE_API_FIELDS['name'])
                                  or '')[:DATING_PEOPLE_NAME_MAX],
                      'age': int(age) if isinstance(age, int) else 0,
                      'photo': photo,
                      # Ключ по фотографии — тот же, что у сценария: имя файла у
                      # сайта одно и то же и в API, и в разметке. На нём держится
                      # значок «есть переписка», и разойдись эти ключи, он пропал бы.
                      'photo_uid': dating_people_photo_uid(photo),
                      'online': online,
                      # Признака «новый» у API нет. Ставить его наугад значило бы
                      # помечать новыми всех подряд.
                      'is_new': False,
                      # Ссылки на анкету API не даёт, а собирать её из номера —
                      # догадка: адрес на сайте содержит ещё и хвост из имени.
                      'link': ''})

    return found


def _token_get(profile_id: int) -> str:
    """Токен анкеты из Redis. Сбой Redis — не беда: войдём заново."""
    try:
        raw = redis_conn_get().get(DATING_PEOPLE_API_TOKEN_KEY.format(profile_id=profile_id))
    except Exception as e:
        logger_info(f'[dating] токен API не прочитался: {e}')

        return ''

    return str(raw or '')


def _token_put(profile_id: int, token: str) -> None:
    """Кладёт токен на его же срок, минус запас.

    Срок берём **из самого токена**: сайт вправе выдавать их на сколько угодно, и
    своя константа разошлась бы с ним молча — колонка перестала бы обновляться, а
    причина «токен протух» нигде бы не всплыла.
    """
    try:
        redis_conn_get().set(DATING_PEOPLE_API_TOKEN_KEY.format(profile_id=profile_id),
                             token, ex=_token_keep(token))
    except Exception as e:
        logger_info(f'[dating] токен API не записался: {e}')


def _token_drop(profile_id: int) -> None:
    """Забывает токен — после 401, то есть когда сайт его уже не принимает."""
    try:
        redis_conn_get().delete(DATING_PEOPLE_API_TOKEN_KEY.format(profile_id=profile_id))
    except Exception as e:
        logger_info(f'[dating] токен API не убрался: {e}')


def _token_keep(token: str) -> int:
    """Сколько токену осталось жить по его же `exp`, за вычетом запаса.

    Не разобрался — умолчание: подпись мы всё равно не проверяем (это дело сайта),
    и чужой формат токена не повод отказываться от работы.
    """
    try:
        payload = str(token).split('.')[1]
        payload += '=' * (-len(payload) % 4)
        exp = int(json.loads(base64.urlsafe_b64decode(payload)).get('exp') or 0)
    except Exception:
        return DATING_PEOPLE_API_TOKEN_KEEP

    left = int(exp - time.time() - DATING_PEOPLE_API_TOKEN_MARGIN)

    return left if left > 60 else DATING_PEOPLE_API_TOKEN_KEEP
