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

from http_.http_pool import http_pool_transport
from logging_.logging_ import logger_info
from redis_.redis_ import redis_conn_get, redis_text

from src.dating.dating_proxy import dating_proxy_url

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

# ⚠⚠ **Пауза после неудачного входа. Написано по живой блокировке.**
#
# Сайт считает **неудачные** попытки входа с одного IP за сутки и, набрав своё,
# закрывает адрес целиком: `ERROR_SING_IN_BY_IP_DAILY_FAILED_LIMIT`. Дальше не
# входит уже никто — ни та анкета, что ошиблась, ни двенадцать соседних.
#
# Без паузы мы сами кормили этот счётчик: круг ленты ходит каждые две минуты, при
# отказе токена не появляется, и следующий заход честно пробует снова. Тридцать
# неудач в час, семьсот в сутки — блокировка держалась ровно столько, сколько
# работал воркер, и снять её было нечем.
#
# Отсюда лестница: первая неудача — пять минут молчания, дальше вдвое, потолок —
# час. Успешный вход стирает её. Числа не выверены до секунды и не могут быть: они
# лишь обязаны быть **много больше** тика круга (2 минуты) и **много меньше**
# суток, за которые сайт считает свой лимит.
DATING_SITE_API_FAIL_KEY = 'dating:site_api:fail:{profile_id}'
DATING_SITE_API_FAIL_FIRST_SEC = 300
DATING_SITE_API_FAIL_MAX_SEC = 3600

# ⚠⚠ **Сайт сказал «суточный лимит» — молчим сутки, а не час.**
#
# Здесь стоял час, и это была ошибка, стоившая нам недели работы. Счётчик у сайта
# **суточный**; пробуя раз в час, мы даём ему 24 неудачи в сутки — ровно столько,
# чтобы предел никогда не опустел. Блокировка при этом держится не сутки, а
# бесконечно, и виноваты в этом мы сами.
#
# Сутки с запасом: сайт точного срока не называет, а лишний час молчания не стоит
# ничего — пока предел не сброшен, войти всё равно нельзя.
DATING_SITE_API_FAIL_LIMIT_SEC = 25 * 3600

# Приметы «предел исчерпан». ⚠ Ищем **словами, а не кодом**: коды сайт придумывает
# сам, и в одну блокировку он прислал две разные формулировки.
DATING_SITE_API_FAIL_LIMIT_MARKS = ('DAILY_FAILED_LIMIT', 'daily limit',
                                    'invalid login attempts per day')

# ⚠⚠ **Предел бывает двух родов, и путать их дорого.**
#
#   `ERROR_SING_IN_BY_IP_DAILY_FAILED_LIMIT`      — закрыт **наш адрес**;
#   `ERROR_SING_IN_BY_ACCOUNT_DAILY_FAILED_LIMIT` — закрыта **эта учётка**.
#
# Первый означает, что пробовать другими анкетами того же сайта бессмысленно: их
# всех видно с того же адреса. Прежде мы этого не различали и, упёршись одной
# анкетой, шли пробовать остальными двенадцатью — то есть добавляли двенадцать
# неудач к счётчику, который и так переполнен.
DATING_SITE_API_FAIL_IP_MARKS = ('BY_IP', 'daily limit registrations')

# Метка на **весь сайт**: адрес закрыт, и трогать его не стоит ни одной анкетой.
DATING_SITE_API_FAIL_SITE_KEY = 'dating:site_api:fail:site:{site_id}'
# Каким был отказ — запоминаем вместе со сроком, чтобы сказать человеку правду.
# «Сайт закрыл наш адрес» и «пароль не подошёл» чинятся по-разному, и одинаковая
# строка в журнале заставляла бы гадать, что именно случилось.
DATING_SITE_API_FAIL_KIND_LIMIT = 'limit'
DATING_SITE_API_FAIL_KIND_OTHER = 'fail'

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
                               payload: dict = None, method: str = '',
                               door: str = '') -> dict:
    """Один запрос к API сайта под этой анкетой. Ответ — разобранным.

    Args:
        profile: строка `profile`: нужны `id` и `login`.
        base: корень API сайта из настройки сайта.
        password: пароль для входа — свой у анкеты или общий у сайта.
        path: путь ручки, от корня (`/users/online`).
        payload: тело запроса. `None` — запрос без тела.
        method: `GET` или `POST`. Пусто — по наличию тела, как и было у списков.
        door: занятие для журнала запросов — `mail`, `people`, `send`… Пусто —
            запишем как `people`: списки были первым и остаются самым частым
            занятием этой двери. ⚠ Занятие называет **вызывающий**: по пути его
            не угадать, `/messages` это и забор писем, и отправка.

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

    from src.dating.dating_request import DATING_REQUEST_LOGIN, DATING_REQUEST_PEOPLE

    door = str(door or DATING_REQUEST_PEOPLE)
    token = _token_get(profile_id)
    # Клиент разовый, соединения — из общего транспорта (`http_pool.py`):
    # токен ездит в заголовке, клиент здесь — только настройки вызова, и всякий
    # раз заводить с сайтом TLS незачем. Не закрываем: ушёл бы и общий пул.
    # ⚠ **Выход анкеты, а не общий.** Здесь живёт `POST /auth/login`, а сайт
    # считает учётки по внешнему адресу: войди мы с общего IP сервера, лимит
    # сложился бы на нём — и следующая девушка получила бы отказ входа.
    # Транспорт свой у каждого выхода (`http_pool.py`), соединения внутри выхода
    # переиспользуются.
    client = httpx.AsyncClient(timeout=DATING_SITE_API_TIMEOUT,
                               headers={'User-Agent': DATING_SITE_API_UA},
                               transport=http_pool_transport(await dating_proxy_url(profile)))
    if not token:
        token = await _login_once(client, profile, base, password)

    answer = await _request(client, base, path, token, payload, verb, profile, door)
    if answer.status_code == 401:
        _token_drop(profile_id)
        token = await _login_once(client, profile, base, password)
        answer = await _request(client, base, path, token, payload, verb, profile, door)

    if answer.status_code >= 400:
        raise DatingSiteApiError(_refusal(path, answer))

    return _body(path, answer)


def dating_site_api_has_token(profile_id: int) -> bool:
    """Лежит ли у анкеты живой токен. Нужен тому, кто **выбирает**, кем идти.

    ⚠ **Токен живёт у каждой анкеты свой** (`dating:site_api:{profile_id}`), и это
    не мелочь для того, кто берёт «любую подходящую». Взяв каждый раз новую
    анкету, он каждый раз получает вход заново — а сайт считает входы с адреса и
    закрывает его, набрав своё. Спросив сперва здесь, вызывающий возьмёт ту, у
    которой вход уже есть, и не потратит ни одной попытки.

    Сбой Redis отвечает `False`: «не знаем» здесь безопаснее «есть», потому что
    ошибка в эту сторону стоит лишнего входа, а в обратную — работы по мёртвому
    токену и лишнего же входа следом.
    """
    return bool(_token_get(int(profile_id)))


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

    # ⚠ **Отказавший вход не повторяем сразу.** Иначе круг, идущий каждые две
    # минуты, за час набирает сайту тридцать неудач — и тот закрывает наш IP
    # целиком, для всех анкет разом. Так и случилось; см. `DATING_SITE_API_FAIL_KEY`.
    # ⚠ Сперва адрес, потом анкета. Закрыт адрес — бессмысленно пробовать любой
    # учёткой этого сайта: сайт видит их все с одного IP.
    site_left = _fail_site_left(int(profile.get('site_id') or 0))
    if site_left:
        raise DatingSiteApiError(_fail_words(site_left, DATING_SITE_API_FAIL_KIND_LIMIT))

    left = _fail_left(profile_id)
    if left:
        raise DatingSiteApiError(_fail_words(left, _fail_kind(profile_id)))

    for _ in range(DATING_SITE_API_LOGIN_WAIT_TRIES):
        # ⚠ **Токен перечитываем перед каждой попыткой взять замок, а не только
        # после сна.** Между чтением токена у вызывающего и этой строкой есть
        # `await` — с появлением своего выхода у анкеты (`dating_proxy`) их стало
        # больше, — и за это время соседний вызов успевает войти целиком: замок
        # он уже отпустил, а мы о его токене ещё не знаем. Тогда замок свободен,
        # и мы честно делаем **второй вход** — тот самый, который сайт бракует
        # как подозрение.
        #
        # Ловится это только гонкой: тест «два списка разом — один вход» покраснел
        # ровно в тот день, когда перед входом добавился один лишний await.
        token = _token_get(profile_id)
        if token:
            return token

        if _lock_take(profile_id):
            try:
                token = await _login(client, profile, base, password)
            except DatingSiteApiError as e:
                # Отказ входа запоминаем: следующая попытка — не раньше срока.
                _fail_mark(profile_id, str(e))
                # ⚠ И, если закрыт адрес, — весь сайт: остальные анкеты живут за
                # тем же IP, и каждая их попытка продлевает общий запрет.
                _fail_site_mark(int(profile.get('site_id') or 0), str(e))
                raise
            else:
                _fail_clear(profile_id)

                return token
            finally:
                _lock_free(profile_id)

        # Замок занят — ждём того, кто вошёл первым: его токен станет нашим на
        # следующем витке (проверка стоит в начале цикла).
        await asyncio.sleep(DATING_SITE_API_LOGIN_WAIT_STEP)

    return await _login(client, profile, base, password)


def _fail_left(profile_id: int) -> int:
    """Сколько секунд ещё молчим после неудачного входа. Ноль — можно пробовать.

    ⚠ Сбой Redis отвечает нулём: без него нет ни счётчика, ни общего токена, и
    запирать анкету наглухо из-за недоступного Redis значило бы менять редкую
    беду на постоянный простой.
    """
    try:
        return max(0, int(redis_conn_get().ttl(
            DATING_SITE_API_FAIL_KEY.format(profile_id=profile_id)) or 0))
    except Exception:
        return 0


def _fail_site_left(site_id: int) -> int:
    """Сколько секунд ещё не трогаем сайт **ни одной анкетой**. Ноль — можно.

    ⚠ Сбой Redis отвечает нулём — по тому же доводу, что и у метки анкеты: без
    Redis нет ни счётчика, ни общего токена, и запирать работу наглухо из-за
    недоступного Redis значило бы менять редкую беду на постоянный простой.
    """
    if not int(site_id or 0):
        return 0

    try:
        return max(0, int(redis_conn_get().ttl(
            DATING_SITE_API_FAIL_SITE_KEY.format(site_id=int(site_id))) or 0))
    except Exception:
        return 0


def _fail_site_mark(site_id: int, error: str) -> None:
    """Закрывает сайт целиком, если сайт сказал, что закрыт **адрес**.

    ⚠⚠ **Это не перестраховка, а исправление живой беды.** Упёршись одной анкетой
    в «предел по адресу», мы шли пробовать остальными двенадцатью — и добавляли
    двенадцать неудач к счётчику, который и так переполнен. Так запрет и держался
    неделю: его продлевали мы сами.
    """
    if not int(site_id or 0):
        return

    low = str(error).lower()
    if not any(mark.lower() in low for mark in DATING_SITE_API_FAIL_IP_MARKS):
        return

    try:
        redis_conn_get().set(
            DATING_SITE_API_FAIL_SITE_KEY.format(site_id=int(site_id)),
            DATING_SITE_API_FAIL_LIMIT_SEC, ex=DATING_SITE_API_FAIL_LIMIT_SEC)
    except Exception as e:
        logger_info(f'[dating] запрет по адресу не записался: {e}')

        return

    logger_info(f'[dating] сайт #{site_id}: закрыт наш IP — не трогаем сайт '
                f'{DATING_SITE_API_FAIL_LIMIT_SEC // 3600} ч ни одной анкетой')


def dating_site_api_fail_clear(site_id: int = 0, profile_id: int = 0) -> None:
    """Снимает запрет руками — когда человек знает то, чего не знает код.

    Например: сменили выход наружу, и адрес теперь другой; или на стороне сайта
    подтвердили, что предел сброшен. Ждать в этих случаях нечего.
    """
    try:
        conn = redis_conn_get()
        if int(site_id or 0):
            conn.delete(DATING_SITE_API_FAIL_SITE_KEY.format(site_id=int(site_id)))
        if int(profile_id or 0):
            conn.delete(DATING_SITE_API_FAIL_KEY.format(profile_id=int(profile_id)))
    except Exception as e:
        logger_info(f'[dating] запрет не снялся: {e}')


def _fail_kind(profile_id: int) -> str:
    """Каким был прошлый отказ: `limit` — сайт закрыл адрес, `fail` — прочее."""
    try:
        raw = redis_text(redis_conn_get().get(
            DATING_SITE_API_FAIL_KEY.format(profile_id=profile_id)))
    except Exception:
        return DATING_SITE_API_FAIL_KIND_OTHER

    return (raw.split(':')[1] if ':' in raw else DATING_SITE_API_FAIL_KIND_OTHER)


def _fail_wait(raw) -> int:
    """Срок из значения метки. Понимает и старую запись — голое число.

    ⚠ Старую понимать обязательно: метки лежат в Redis до часа, и выкладка
    посреди этого часа встретит записи прежнего вида. Уронить на них разбор
    значило бы потерять паузу ровно тогда, когда она нужнее всего.
    """
    try:
        return int(redis_text(raw).split(':')[0] or 0)
    except ValueError:
        return 0


def _fail_words(left: int, kind: str) -> str:
    """Почему сейчас не входим — словами, которые понятны без чтения кода.

    ⚠⚠ **Строка уезжает в журнал анкеты, и человек читает её вместо объяснения.**
    Прежняя («вход отложен на 2388 с: прошлая попытка не прошла…») отвечала на
    вопрос «что случилось» так, что спрашивать приходилось заново: непонятно, кто
    отложил, почему счёт в секундах и при чём тут адрес. Здесь сказано прямо: мы
    не пробуем войти, вот сколько ещё не будем, и вот зачем.

    Два разных случая, и путать их дорого — чинятся они по-разному:

    * `limit` — **сайт закрыл наш адрес**. Пароль ни при чём, и менять его
      бессмысленно: сайт не доходит до проверки. Лечится сменой выхода наружу
      (прокси) либо ожиданием, пока у него сбросится суточный счёт;
    * `fail` — вход не прошёл по другой причине (пароль, сессия, молчание сайта).
      Повторяем не сразу, чтобы не превратить одну беду во вторую.
    """
    if kind == DATING_SITE_API_FAIL_KIND_LIMIT:
        return (f'сайт закрыл вход — у него превышен суточный предел неудачных '
                f'попыток. Пароль тут ни при чём: до его проверки сайт не доходит. '
                f'⚠ Ждём {_fail_when(left)} и раньше не пробуем: счётчик у сайта '
                f'**суточный**, и каждая новая попытка продлевает запрет ещё на '
                f'сутки — именно так он и держался неделю. Снять раньше можно, '
                f'только сменив выход наружу: предел считается по адресу')

    return (f'прошлый вход не прошёл, повторим через {_fail_when(left)}. Сразу не '
            f'повторяем нарочно: сайт считает неудачные входы по нашему IP-адресу '
            f'и, набрав своё, закрывает его сразу всем анкетам')


def _fail_when(left: int) -> str:
    """Срок словами: «40 мин», «3 мин», «45 с». Секунды в журнале не читаются."""
    left = max(0, int(left))
    if left >= 3600:
        minutes = left % 3600 // 60

        # «1 ч 0 мин» человек читает как опечатку, а не как ровный час.
        return f'{left // 3600} ч' + (f' {minutes} мин' if minutes else '')

    return f'{left // 60} мин' if left >= 60 else f'{left} с'


def _fail_mark(profile_id: int, error: str) -> None:
    """Запоминает неудачный вход: следующая попытка — через растущий срок.

    ⚠ **Сайт сказал «суточный лимит» — ждём час, а не пять минут.** Лестница
    здесь бессмысленна: счётчик у него суточный, и частые попытки его же и
    продлевают. Отличаем по словам из ответа — коды сайт придумывает сам, и
    привязываться к одному было бы хрупко.
    """
    key = DATING_SITE_API_FAIL_KEY.format(profile_id=profile_id)
    hard = any(mark.lower() in str(error).lower() for mark in DATING_SITE_API_FAIL_LIMIT_MARKS)

    try:
        conn = redis_conn_get()
        if hard:
            wait = DATING_SITE_API_FAIL_LIMIT_SEC
        else:
            # Лестница: удваиваем прежний срок, начиная с первого.
            was = _fail_wait(conn.get(key))
            wait = min(DATING_SITE_API_FAIL_MAX_SEC,
                       was * 2 if was else DATING_SITE_API_FAIL_FIRST_SEC)

        # ⚠ Срок и вид отказа — **в одном значении**, через двоеточие. Вторым
        # ключом они разъехались бы: у каждого свой срок жизни, и переживший
        # соседа отвечал бы про отказ, которого уже нет.
        kind = DATING_SITE_API_FAIL_KIND_LIMIT if hard else DATING_SITE_API_FAIL_KIND_OTHER
        conn.set(key, f'{wait}:{kind}', ex=wait)
    except Exception as e:
        logger_info(f'[dating] пауза после отказа входа не записалась: {e}')

        return

    logger_info(f'[dating] анкета #{profile_id}: вход не прошёл, молчим {wait} с'
                + (' (сайт назвал суточный лимит)' if hard else ''))


def _fail_clear(profile_id: int) -> None:
    """Удачный вход стирает паузу: лестница считается заново."""
    try:
        redis_conn_get().delete(DATING_SITE_API_FAIL_KEY.format(profile_id=profile_id))
    except Exception:
        pass


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

    from src.dating.dating_request import DATING_REQUEST_LOGIN

    answer = await _request(client, base, DATING_SITE_API_LOGIN_PATH, '',
                            {'Login': login, 'Password': password}, 'POST',
                            profile, DATING_REQUEST_LOGIN)
    if answer.status_code >= 400:
        raise DatingSiteApiError(_refusal(DATING_SITE_API_LOGIN_PATH, answer))

    token = str(_body(DATING_SITE_API_LOGIN_PATH, answer).get('Token') or '')
    if not token:
        raise DatingSiteApiError('сайт не дал токена в ответ на вход')

    _token_put(int(profile.get('id') or 0), token)

    return token


async def _request(client: httpx.AsyncClient, base: str, path: str, token: str,
                   payload: dict, method: str, profile: dict = None,
                   door: str = '') -> httpx.Response:
    """Один запрос к сайту. Обрыв связи — сразу словами, а не стеком.

    ⚠⚠ **Здесь же пишется строка журнала запросов, и это единственное место.**
    Через `dating_site_api` идут все двери к сайту — вход, письма, списки,
    избранные, отправка, — и записывай мы каждую у себя, полдюжины копий разошлись
    бы в мелочах: одна считала бы миллисекунды, другая забыла бы про выход, третья
    не отличила бы `200` с `IsSuccess: false` от удачи.

    Цена слепоты известна: 484 неудачных входа за сутки не были видны в панели
    нигде, и сайт закрыл наш IP раньше, чем мы поняли, что происходит.
    """
    url = str(base).rstrip('/') + path
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    started = time.monotonic()

    try:
        if method == 'POST':
            answer = await client.post(url, json=payload or {}, headers=headers)
        else:
            answer = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        # Ответа не было вовсе: код нулевой, причина словами.
        await _note(profile, door, method, path, 0, False, f'сайт не отвечает: {e}',
                    started)

        raise DatingSiteApiError(f'сайт не отвечает: {e}')

    # ⚠ Удача — это `2xx`, а **не** «ответ пришёл». Разбор тела живёт выше
    # (`IsSuccess`, `_refusal`), и уточнить исход умеет только он; здесь мы
    # честно пишем то, что видит транспорт, а `_refusal` допишет причину сам.
    # ⚠ Тело отдаём журналу **целиком**: что с ним делать, решает он — длину
    # пишет всегда, само тело только у отказа. Реши мы здесь, правило пришлось бы
    # повторить в шести местах, откуда пишут запросы.
    await _note(profile, door, method, path, answer.status_code,
                answer.status_code < 400,
                '' if answer.status_code < 400 else _refusal(path, answer), started,
                body=answer.text)

    return answer


async def _note(profile: dict, door: str, method: str, path: str, status: int,
                ok: bool, error: str, started: float, body: str = '') -> None:
    """Строка в журнал запросов. Ничего не бросает и ничего не ждёт от вызова.

    ⚠ Дверь приходит от вызывающего: транспорт по пути занятия не угадает —
    `/messages` это и забор писем, и отправка.
    """
    from src.dating.dating_request import (DATING_REQUEST_LOGIN, dating_request_ms,
                                           dating_request_write)

    await dating_request_write(
        door=str(door or DATING_REQUEST_LOGIN),
        profile_id=int((profile or {}).get('id') or 0),
        site_id=int((profile or {}).get('site_id') or 0),
        method=method, path=path, status=status, ok=ok, error=error,
        ms=dating_request_ms(started),
        # ⚠ Адрес, а не строка подключения: в ней пароль канала.
        proxy_ip=str((profile or {}).get('proxy_ip') or ''),
        # Длина пишется всегда, тело — только у отказа (`_body_keep`).
        body=body)


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
    """Токен анкеты из Redis. Сбой Redis — не беда: войдём заново.

    ⚠⚠ **Через `redis_text`, а не `str()`, и это не придирка.** Redis отдаёт
    **байты**; `str(b"eyJhbGci…")` даёт питоновское представление — `b'eyJhbGci…'`,
    вместе с буквой `b` и кавычками. Сайт на такой токен отвечает
    `401 IDX12709: CanReadToken() returned false`, кэш перестаёт работать целиком,
    и вход идёт **на каждый запрос** вместо одного в 12 часов.

    Так и случилось: сайт счёл это перебором паролей и закрыл наш адрес на неделю.
    Причину искали в паролях и в кругах, а она была в одной этой строке.

    ⚠ Поймать сюитой было нельзя: поддельный Redis отдаёт `str`, как и положено
    подделке. Видно только на живом — по паре строк «login 200 → 401 → login 200»
    в журнале запросов.
    """
    try:
        raw = redis_conn_get().get(DATING_SITE_API_TOKEN_KEY.format(profile_id=profile_id))
    except Exception as e:
        logger_info(f'[dating] токен API не прочитался: {e}')

        return ''

    return redis_text(raw)


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
