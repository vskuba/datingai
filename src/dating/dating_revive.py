"""Восстановление входа анкеты: когда пробовать и когда сдаться.

Вход на сайт живёт около суток. Когда он протухает, анкета уходит из сети молча:
сообщения перестают приходить, а сайт не сообщает об этом ничем, кроме отказа в
опросе. Здесь — правила, по которым смена держателя решает, пора ли входить заново.

Лестница пауз, а не постоянный интервал: половина причин временные (сайт лёг,
сосед недоступен), и они рассасываются за минуты; вторая половина требует человека,
и долбить сайт настоящим входом через браузер раз в минуту ради них нельзя.

Сутки — рубеж, за которым автомат перестаёт пробовать. Дальше это уже не сбой, а
поломка: пароль сменили, анкету закрыли, сайт просит подтверждение.

Тревога («анкеты нет в сети», поле `relogin_since`) и откат («не входить чаще
лестницы», поля `relogin_tries` и `relogin_next_at`) — разные вещи, см. подробности
в `dating_revive_plan`. Свести их в одно поле тянет каждого, кто сюда заходит, но
именно из этой экономии выросли обе поломки: и сдача при живом опросе, и вход через
браузер раз в минуту.

База, сосед и сам вход подтягиваются импортами внутри функций, а не в шапке модуля.
Правила отсюда читают чистые тесты (`tests/test_revive_schedule.py`,
`tests/test_revive_round.py`), а сюита нарочно запускается без зависимостей проекта:
шапка с `mysql_` и `logging_` сделала бы арифметику лестницы непроверяемой.
"""

import asyncio
import time
from datetime import datetime, timedelta

from src.dating.dating_json import dating_json_load

# Как часто смена сверяет вход анкет с сайтом.
DATING_REVIVE_PERIOD_SEC = 60

# Сколько опросов идёт разом. Столько же и по той же причине берёт ручка опроса
# (`DATING_GIRL_PROBE_PARALLEL`): опрос — это единицы килобайт по HTTP, и двести
# анкет подряд не уложились бы в минуту круга. Входы этим числом не ограничены —
# они идут строго по одному, вкладка Chromium стоит куда дороже.
DATING_REVIVE_PROBE_PARALLEL = 16

# Возраст входа, после которого он обновляется профилактически. Сессия сайта живёт
# около суток, и ловить её протухание в середине разговора незачем.
DATING_REVIVE_AGE_SEC = 20 * 3600

# Сколько терпим череду неудач, прежде чем перестать пробовать.
DATING_REVIVE_GIVE_UP_SEC = 24 * 3600

# Лестница пауз в минутах и хвост после неё.
DATING_REVIVE_STEPS_MIN = (1, 5, 15, 45, 90)
DATING_REVIVE_TAIL_MIN = 120


def dating_revive_delay(tries: int) -> int:
    """Сколько ждать до попытки номер `tries` (0 — первая), в секундах."""
    step = max(0, int(tries))
    minutes = (DATING_REVIVE_STEPS_MIN[step] if step < len(DATING_REVIVE_STEPS_MIN)
               else DATING_REVIVE_TAIL_MIN)

    return minutes * 60


def dating_revive_state(relogin_since: datetime | None, now: datetime) -> str:
    """Состояние анкеты: `ok`, `failing` (череда идёт) или `given_up` (сдались).

    Считается по времени, а не по наличию назначенной попытки: так ответ один и
    тот же у страницы, у журнала и у самой смены, чем бы ни кончился её прошлый круг.
    """
    if not relogin_since:
        return 'ok'

    lasted = (now - relogin_since).total_seconds()

    return 'given_up' if lasted >= DATING_REVIVE_GIVE_UP_SEC else 'failing'


def dating_revive_due(relogin_next_at: datetime | None, now: datetime) -> bool:
    """Настал ли срок очередной попытки."""
    return bool(relogin_next_at) and relogin_next_at <= now


def dating_revive_plan(girl: dict, online: bool, now: datetime) -> dict:
    """Что делать с анкетой по итогам опроса — одним решением, без побочных действий.

    Отдельно от самого круга, потому что здесь всё интересное: лестница, сдача,
    профилактика. Круг вокруг — это база и вход, их в тестах не прогонишь.

    Часы тут одни на всё — часы базы, и это условие, а не деталь:

    * возраст входа приходит **числом секунд** (`login_age_sec`), посчитанным самой
      базой (`TIMESTAMPDIFF` в `_girls_kept`): `logged_in_at` пишет её `NOW()`, и
      момент времени наружу вообще не выходит;
    * `now` круг берёт у базы же (`_db_now`), поэтому записанные из него
      `relogin_since` и `relogin_next_at` меряются той же линейкой — и здесь, и в
      ручке списка анкет, и на карточке рядом с `last_seen_at`.

    Возьми питон свои `datetime.now()`, и всё поехало бы на расхождение поясов: `TZ`
    не задан ни в одном compose, а прод-MySQL стоит на чужом сервере со своими
    настройками. Заказчик просил профилактику при двадцати часах, а не при двадцати
    ± часовой пояс, и молчаливость этой ошибки — худшее в ней.

    Три поля тут делят между собой две разные заботы, и путать их нельзя:

    * `relogin_since` — **тревога**: анкеты нет в сети. Это она мигает на карточке и
      она же через сутки даёт «сдались, нужен вход вручную» — то есть зовёт человека.
    * `relogin_tries` + `relogin_next_at` — **откат**: не ходить в браузер соседа
      чаще лестницы. Повод неважен, плановый или аварийный.

    Разведены они потому, что признак у них разный. Тревогу видно по опросу прямо
    сейчас; неудачу входа — только по тому, что `logged_in_at` не подвинулся. Пока
    они были одним полем, анкета, у которой череда началась от лежащего сайта, после
    выздоровления оставалась «в тревоге» из-за возраста входа и через сутки получала
    сдачу — при том что опрос всё это время отвечал `online`.

    Обратный перекос ровно так же плох: погасить вместе с тревогой и откат — значит
    после каждой минутной отлёжки сайта разрешить вход через браузер раз в минуту.
    Откат снимает только удавшийся вход, `_keep_online_set` в `dating_login.py`.

    Returns:
        dict: `action` — `none` (ничего), `clear` (тревога кончилась), `wait`
        (тревога началась, входить ещё рано), `login` (входить сейчас), `give_up`
        (сутки вышли, и это первый круг, который это заметил); плюс новые значения
        `relogin_since`, `relogin_tries`, `relogin_next_at` для полей учётки.
    """
    since = girl.get('relogin_since')
    tries = int(girl.get('relogin_tries') or 0)
    next_at = girl.get('relogin_next_at')
    # Возраст входа считает база (`TIMESTAMPDIFF` в `_girls_kept`), а не питон:
    # `logged_in_at` пишется её `NOW()`, и разница часов уходила бы прямо в число
    # часов профилактики. Пусто — входа не было вовсе, и обновить его точно пора.
    age = girl.get('login_age_sec')
    old = age is None or int(age) >= DATING_REVIVE_AGE_SEC

    if online:
        # Анкета отвечает — тревоге взяться неоткуда, гасим её всегда. Возраст входа
        # тут ни при чём: он говорит не «анкеты нет», а «пора обновить cookie».
        if old and (next_at is None or dating_revive_due(next_at, now)):
            # Профилактика. Входим, но откат двигаем: плановый вход через браузер
            # может не проходить (капча, сменённый пароль), а опрос по старым cookie
            # всё это время отвечает `online`. Без отката такой вход открывал бы
            # вкладку Chromium у соседа раз в минуту и бессрочно.
            return _plan_login(None, tries, now)

        # Либо вход свеж, либо пауза лестницы ещё идёт. Откат несём дальше нетронутым.
        return {'action': 'clear' if since else 'none', 'relogin_since': None,
                'relogin_tries': tries, 'relogin_next_at': next_at}

    if not since:
        # Тревога зажигается: анкеты нет в сети. Входить сразу нельзя — сайт мог
        # моргнуть, и минута ожидания дешевле лишнего прогона браузера.
        #
        # А вот лестницу начинать заново тут можно не всегда, и признак для этого —
        # исчерпанность отката (срока попытки нет или он прошёл), а не пустая тревога.
        # Соблазн взять тревогу велик — «повод новый, значит и лестница новая», — но
        # тревога гаснет от одного удачного опроса, а откат его переживает: между
        # входами лежит пауза, и в её середине опрос запросто ответит `online` разок.
        # Считая по тревоге, следующая же строка «не online» выбрасывала бы живую
        # паузу и звала браузер соседа через минуту — та самая беда, ради которой
        # лестницу и завели. Плановые входы по возрасту делают это ещё нагляднее:
        # тревоги у них нет вовсе, так что по ней любая их пауза обнулялась бы.
        held = bool(next_at) and not dating_revive_due(next_at, now)
        step = tries if held else 0
        when = next_at if held else now + timedelta(seconds=dating_revive_delay(0))

        return {'action': 'wait', 'relogin_since': now, 'relogin_tries': step,
                'relogin_next_at': when}

    if dating_revive_state(since, now) == 'given_up':
        # Сдача — переход, а не состояние: тревога остаётся в базе до входа вручную,
        # и круг наткнётся на неё каждую минуту до конца времён. Момент перехода
        # виден по ещё не стёртому сроку попытки: первый же круг после сдачи
        # запишет `relogin_next_at = NULL`, и дальше это уже давняя сдача, о которой
        # и журналу, и вкладке «Ошибки» соседа сказали.
        announce = bool(next_at)

        return {'action': 'give_up' if announce else 'none', 'relogin_since': since,
                'relogin_tries': tries, 'relogin_next_at': None}

    if not dating_revive_due(next_at, now):
        return {'action': 'none', 'relogin_since': since, 'relogin_tries': tries,
                'relogin_next_at': next_at}

    return _plan_login(since, tries, now)


def _plan_login(since: datetime | None, tries: int, now: datetime) -> dict:
    """Решение «входим сейчас»: попытка считается сделанной, следующая — по лестнице.

    Одно место для двух поводов (протухание и профилактика по сроку): откат у них
    общий, и разойтись эти две лестницы не должны.

    Тревога приходит сюда готовой и здесь не выдумывается — в этом вся суть развода:
    плановому входу тревога не нужна (анкета в сети, звать человека не о чем), а
    аварийному её уже зажёг отказ опроса.
    """
    step = tries + 1

    return {'action': 'login', 'relogin_since': since, 'relogin_tries': step,
            'relogin_next_at': now + timedelta(seconds=dating_revive_delay(step))}


async def dating_revive_round(login_run=None) -> dict:
    """Один круг: опросить анкеты под присмотром и починить те, что выпали.

    Опросы идут разом, входы — строго по одному, и это разные вещи по разным причинам.

    Опрос — три-четыре HTTP-запроса с таймаутом в десять секунд; у двухсот анкет
    последовательный обход занял бы минут семь, и круг «раз в минуту» превратился бы
    в «раз в семь-десять», а задержка обнаружения росла бы вместе с числом анкет.
    Тот же приём и по той же причине — в ручке `dating_girl_probe`.

    Вход — наоборот: каждый открывает вкладку Chromium (~143 МБ) в пуле, общем с
    соседом. Сайт лёг, все анкеты решили входить — и параллельные входы уронили бы
    пул вместе с собственной работой соседа. Поэтому решения и входы идут в один
    ряд, после того как опросы кончились.

    Вход передаётся параметром, а не импортируется намертво: так круг проверяется
    тестами без браузера, а модуль не тянет за собой половину проекта.

    Returns:
        dict: `checked`, `online`, `tried` (сколько раз входили), `revived`,
        `given_up`.
    """
    if login_run is None:
        # Импорт только когда вход настоящий: с заглушкой круг обязан работать без
        # браузера и без зависимостей проекта — на этом стоят его тесты.
        from src.dating.dating_login import dating_login_run

        login_run = dating_login_run

    girls = await _girls_kept()
    counts = {'checked': len(girls), 'online': 0, 'tried': 0, 'revived': 0, 'given_up': 0}

    # Настройка опроса спрашивается на сайт, а не на анкету: двести анкет с трёх
    # сайтов — это три вопроса соседу вместо двухсот, и все опросы круга видят одно
    # и то же. Сосед не ответил — сайт этот круг пропускается целиком.
    presence_of = {site_id: await _presence_of(site_id)
                   for site_id in sorted({int(girl['site_id']) for girl in girls})}

    kept = [girl for girl in girls if presence_of[int(girl['site_id'])]]

    # Ограничитель тот же, что у ручки опроса: разом — не значит все двести сразу.
    limit = asyncio.Semaphore(DATING_REVIVE_PROBE_PARALLEL)

    async def probe(girl: dict) -> dict:
        async with limit:
            return await _probe_one(girl, presence_of[int(girl['site_id'])])

    # `return_exceptions=True` — тем же приёмом и по той же причине, что в
    # `_presence_loop` держателя: упавший опрос одной анкеты не имеет права уносить
    # с собой круг. Разбор — в `_probe_failed`.
    probes = await asyncio.gather(*(probe(girl) for girl in kept), return_exceptions=True)

    for girl, outcome in zip(kept, probes):
        result = _probe_failed(outcome) if isinstance(outcome, BaseException) else outcome
        online = result['status'] == 'online'
        counts['online'] += int(online)

        # Время — на каждую анкету, а не одно на круг: перед этой строкой мог
        # пройти вход соседней анкеты, а он стоит минуту-две. По устаревшему `now`
        # наступивший за это время срок читался бы как ненаступивший, и анкета
        # ждала бы ещё круг — и так каждый раз, пока в круге есть хоть один вход.
        plan = dating_revive_plan(girl, online, await _db_now())
        saved = await _plan_save(int(girl['id']), plan)

        if plan['action'] == 'wait':
            await _log(int(girl['id']), 'fail',
                       f"Вход не признан сайтом: {result['error'] or result['status']}")
            await _error_write(girl, result['error'] or result['status'])
        elif plan['action'] == 'give_up':
            # Считаем и объявляем переход, а не состояние: решение отдаёт `give_up`
            # только на том круге, который сдался, — иначе сюда бы попадала одна и
            # та же анкета каждую минуту, пока её не поднимут руками.
            counts['given_up'] += 1
            await _log(int(girl['id']), 'fail',
                       'Сутки не выходит войти — попытки прекращены, нужен вход вручную')
            await _error_write(girl, 'сутки не выходит войти, автовход остановлен')
        elif plan['action'] == 'login' and not saved:
            # Откат не записался — входить нельзя. Следующий круг увидит прежний,
            # уже наступивший срок и войдёт снова через минуту, и так до
            # бесконечности: это единственный путь, где лестницу можно превысить.
            # Пропущенная попытка дешевле вкладки Chromium раз в минуту, а срок
            # никуда не делся — войдём следующим кругом.
            await _log(int(girl['id']), 'info',
                       'Вход отложен: череду попыток не удалось записать, '
                       'а без неё лестница пауз не работает')
        elif plan['action'] == 'login':
            counts['tried'] += 1
            if await _login_try(girl, login_run, plan, online):
                counts['revived'] += 1

    return counts


def _probe_failed(error: BaseException) -> dict:
    """Упавший опрос — отказом одной анкеты, а не концом круга.

    `dating_presence_probe` ловит своё внутри, но не всё: cookie из
    `storage_state` читаются **до** его `try`, и кривое состояние (`cookies`
    списком строк) бросит наружу. Пока `gather` шёл без `return_exceptions`, такая
    строка обрывала круг целиком — и не разово: следующий круг спотыкался о неё же,
    и анкеты после сломанной переставали проверяться совсем.

    Отказ считаем «не в сети», а не «пропускаем»: тихо пропущенная анкета висела бы
    в панели здоровой — ровно та беда «никто не замечает», ради которой всё
    затевалось. А так зажжётся тревога, и человек увидит причину в карточке.

    Отмену пропускаем наружу: смена кончилась, и продолжать круг незачем.
    """
    if isinstance(error, asyncio.CancelledError):
        raise error

    return {'status': 'error', 'unread': -1, 'ms': 0,
            'error': f'опрос не отработал: {type(error).__name__}: {error}'}


async def _login_try(girl: dict, login_run, plan: dict, planned: bool = False) -> bool:
    """Одна попытка входа. Успех сам снимает откат — это делает `dating_login_run`.

    Повод берётся из опроса, а не из полей учётки: откат есть у обоих поводов, а
    человеку в журнале важно, чинили протухший вход или обновляли живой по сроку.

    **Вкладку за собой убираем здесь, на каждом исходе.** Сам вход её не закрывает:
    для ручки это осознанно — человек нажал «Войти» ради вкладки, о чём и говорит
    её docstring. У автомата смотреть на вкладку некому, а стоит она ~143 МБ в пуле,
    общем с соседом. Худший случай не выдуман: сайт лёг, все анкеты под присмотром
    пошли входить в один круг — и десятки брошенных вкладок кладут пул вместе с
    собственной работой соседа.

    Убираем при этом **только за собой**: список живых вкладок анкеты снимается до
    входа, и гасится потом лишь то, что появилось сверх него, — см. `_sessions_drop`.
    """
    girl_id = int(girl['id'])
    await _log(girl_id, 'info',
               f"{'Обновляю вход заранее' if planned else 'Вхожу заново'} "
               f"(попытка {plan['relogin_tries']})")
    # Снимок «что было до нас». Только по нему и можно узнать вкладку этого входа,
    # когда исключение оборвало его до ответа и номер сгорел вместе с вызовом.
    before = await _sessions_seen(girl)
    try:
        result = await login_run(girl_id)
    except asyncio.CancelledError:
        # Смена кончилась, и задачу отменили. `CancelledError` не наследник
        # `Exception`, и без отдельной ветки вкладка пережила бы саму смену.
        # Убрав за собой, отмену обязательно пробрасываем дальше: проглоченная
        # отмена — это смена, которая не заканчивается.
        await _sessions_drop(girl, before)
        raise
    except Exception as e:
        # Сосед не ответил (`RhapsodyError`), сценария нет, база отвалилась —
        # вкладка при этом уже открыта, а её номер сгорел вместе с вызовом.
        # Причину в журнал пишем до уборки: она — то, ради чего человек туда
        # смотрит, и заслонять её хлопотами с вкладкой нельзя.
        await _log(girl_id, 'fail', f'Войти не вышло: {str(e)[:300]}')
        await _sessions_drop(girl, before)
        return False

    # Вход отдаёт номер живой вкладки, когда сценарий вернул ошибку: свои пути он
    # закрывает сам, а этот оставляет ручке. Автомату вкладка не нужна ни на одном
    # исходе — закрываем по результату, пустой номер закрытие просто пропустит.
    await _session_close(str(result.get('session_id') or ''))

    ok = str(result.get('status')) == 'success' and not result.get('error')
    await _log(girl_id, 'ok' if ok else 'fail',
               'Вход восстановлен' if ok
               else f"Войти не вышло: {str(result.get('error') or '')[:300]}")

    return ok


async def _sessions_seen(girl: dict) -> set | None:
    """Номера живых вкладок анкеты **до** входа — чтобы потом закрыть только новые.

    `None` — посмотреть не вышло. Тогда уборка не тронет ничего: не зная, что было
    до нас, гасить чужое хуже, чем оставить своё.
    """
    try:
        return set(_sessions_mine(girl, await _sessions_list()))
    except Exception as e:
        await _log(int(girl['id']), 'info',
                   f'Список вкладок не спросился ({str(e)[:200]}) — за собой '
                   f'после входа убрать будет нечем')

        return None


async def _sessions_drop(girl: dict, before: set | None) -> None:
    """Гасит вкладки, которые открыл этот вход, — и только их.

    Номер нужной вкладки до нас не дошёл: исключение оборвало `login_run` до ответа.
    Найти её можно единственным способом — сравнением с тем, что жило до входа.

    Раньше здесь гасилось «всё под этим логином», и в этом было два зазора. Первый:
    исключение бывает и **до** того, как вход открыл свою вкладку (у сайта нет
    сценария входа, у анкеты нет пароля, сосед не ответил на запрос сайта, база
    отвалилась при чтении анкеты) — и автомат убивал вкладку, которую не открывал:
    ту, что человек оставил себе после ручного входа, или ту, где прямо сейчас
    работают «Диалоги». Случай «нет сценария входа» повторяется каждую попытку до
    самой сдачи, то есть держать вкладку открытой было нельзя вообще. Второй: имя
    вкладки — это логин, а логины совпадают у анкет с разных сайтов, и две такие
    анкеты гасили вкладки друг другу.

    Уборка обёрнута целиком: правило проекта — уборка не роняет работу. Без этого
    её исключение подменяло бы собой настоящую причину неудачи (строки «Войти не
    вышло» человек бы не увидел) и обрывало круг для остальных анкет.
    """
    if before is None:
        return

    try:
        for session_id in _sessions_mine(girl, await _sessions_list()):
            if session_id not in before:
                await _session_close(session_id)
    except Exception as e:
        # В журнал анкеты, а не в лог контейнера: брошенная вкладка — это ~143 МБ
        # чужого пула, и человек должен видеть, что она осталась.
        await _log(int(girl['id']), 'info',
                   f'Вкладку за собой закрыть не вышло: {str(e)[:200]}')


def _sessions_mine(girl: dict, sessions: list) -> list[str]:
    """Номера вкладок, принадлежащих этой анкете, из общего списка соседа.

    Сайт учитывается наравне с именем: имя вкладки — это логин анкеты, а один и тот
    же логин живёт на разных сайтах. Когда сосед сайт не назвал (старый ответ,
    вкладка заведена не отсюда), сверяем только имя — это по-прежнему точнее, чем
    ничего.
    """
    login = str(girl.get('login') or '')
    site = str(girl.get('site_id') or '')
    mine = []
    for session in sessions or []:
        if str(session.get('name') or '') != login:
            continue

        their = str(session.get('site_id') or '')
        if their and site and their != site:
            continue

        session_id = str(session.get('session_id') or '')
        if session_id:
            mine.append(session_id)

    return mine


async def _sessions_list() -> list:
    """Живые вкладки соседа. Обёртка ради одного импорта, как у `_log`: недоступного
    соседа `dating_login_session_all` переживает сама и отвечает пустым списком."""
    from src.dating.dating_login import dating_login_session_all

    return await dating_login_session_all()


async def _session_close(session_id: str) -> None:
    """Гасит одну вкладку по номеру. Обёртка ради одного импорта, как у `_log`:
    уже закрытую вкладку `dating_login_session_close` переживает сама, а
    исключений наружу не пускает.

    Пустой номер отсекаем ещё до импорта, а не отдаём проверку самой функции: тот
    же исход (нечего закрывать), но без тяжёлых зависимостей `dating_login.py` —
    большинство успешных входов приходят сюда именно с пустым номером (вкладку уже
    закрыл `dating_login_run`), и тащить их ради нуля незачем.
    """
    if not session_id:
        return

    from src.dating.dating_login import dating_login_session_close

    await dating_login_session_close(session_id)


async def _probe_one(girl: dict, presence: dict) -> dict:
    """Опрос одной анкеты вместе с записью его итога в карточку.

    Отдельной функцией, потому что распараллеливается именно эта пара шагов: выше
    их зовут разом, под общим ограничителем.
    """
    from src.dating.dating_presence import dating_presence_probe

    probe = await dating_presence_probe(girl, presence)
    await _probe_save(int(girl['id']), probe)

    return probe


async def _log(girl_id: int, kind: str, text: str) -> None:
    """Строка в журнал анкеты. Обёртка ради одного импорта: журнал тянет за собой
    базу, а модуль держит тяжёлые импорты внутри функций (см. шапку)."""
    from src.dating.dating_log import dating_log_write

    await dating_log_write(int(girl_id), kind, text)


async def _db_now() -> datetime:
    """Который час — по часам базы, а не контейнера.

    В этих часах живут все времена анкеты: `logged_in_at` и `last_seen_at` пишет
    сам MySQL своим `NOW()`, а `relogin_since` и `relogin_next_at` пишет питон —
    но значениями отсюда, так что порода у них одна. Возьми питон свои
    `datetime.now()`, и сравнение «пора ли входить» поехало бы ровно на
    расхождение поясов: `TZ` не задан ни в `docker-compose.yml`, ни в
    `docker-compose.prod.yml`, а прод-MySQL стоит на чужом сервере со своими
    настройками. Заказчик просил лестницу в минутах, а не в минутах ± часовой пояс.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT NOW() AS db_now')
        row = await db.fetchone()

    return (dict(row or {}) or {}).get('db_now') or datetime.now()


async def _girls_kept() -> list[dict]:
    """Анкеты под присмотром автомата — те, кого хотя бы раз ввели успешно."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        # Возраст входа считает база, а не питон: `logged_in_at` пишется её же
        # `NOW()`, и вычитание из `datetime.now()` контейнера сравнивало бы двое
        # разных часов — `TZ` не задан ни в одном compose, а прод-MySQL стоит на
        # чужом сервере. Наружу уходит число секунд, момент времени тут не нужен.
        await db.execute(
            'SELECT id, login, site_id, storage_state, status, last_error, '
            '       relogin_since, relogin_tries, relogin_next_at, '
            '       TIMESTAMPDIFF(SECOND, logged_in_at, NOW()) AS login_age_sec '
            '  FROM dating_girl WHERE keep_online = 1 ORDER BY id')
        rows = await db.fetchall()

    return [dict(row) for row in (rows or [])]


async def _presence_of(site_id: int) -> dict:
    """Настройка опроса сайта. Сосед не ответил или опрос не настроен — пустой
    словарь, и все анкеты сайта выпадают из круга (см. `dating_revive_round`).

    Раньше на этом всё и кончалось: пусто — значит `continue`, а след оставался
    только в логе контейнера, который никто не читает. Анкета при этом выглядела
    в панели совершенно здоровой («всё хорошо», раз опрос её просто не коснулся) —
    ровно та беда «никто не замечает», ради которой всю ветку и делали. Поэтому
    оба пути ниже зовут `_presence_missing_announce`."""
    from logging_.logging_ import logger_info
    from src.dating.dating_presence import dating_presence_ready
    from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_get

    try:
        site = await rhapsody_api_site_get(int(site_id))
    except RhapsodyError as e:
        logger_info(f'[dating] сайт #{site_id} не спросился: {e}')
        # Обычно рассасывается за минуту — сосед перезапускается или моргнул сам.
        # Отдельная причина в объявлении на случай, если это затянется: если
        # соседа чинят несколько часов, человек должен понимать, что дело не в
        # настройке сайта.
        await _presence_missing_announce(site_id, 'unavailable', f'сосед не ответил: {e}')
        return {}

    presence = dating_json_load(site.get('presence'))
    if dating_presence_ready(presence):
        return presence

    # А это надолго и само не пройдёт: `presence` заводится руками на вкладке
    # «Сайты» соседа, и без человека тут ничего не изменится хоть сутки, хоть
    # неделю.
    await _presence_missing_announce(
        site_id, 'not_configured',
        'у сайта не настроен опрос присутствия (`presence`) — анкеты сайта '
        'проверяться не будут, пока его не заведут на вкладке «Сайты»')

    return {}


# Когда каждому сайту в последний раз писали объявление о пропавшем опросе — ключ
# `(site_id, reason)`, значение — момент записи. `reason` — часть ключа, а не
# только текста: если причина сменилась (сосед ожил, но опрос так и не настроен,
# или наоборот), это уже новая беда и напоминать о ней заново можно сразу, не
# дожидаясь остатка паузы от прежней.
_presence_missing_at: dict[tuple[int, str], float] = {}

# Не чаще этого напоминаем про один и тот же сайт по одной и той же причине. Круг
# ходит раз в минуту, а без паузы вкладка «Ошибки» соседа получала бы до полутора
# тысяч одинаковых строк в сутки — именно так уже обожглись со сдачей анкеты
# (`give_up` ниже, отсюда и приём «объявляем переход, а не состояние»). Здесь,
# правда, нет поля учётки, которое можно погасить успешным кругом, — беда не про
# анкету, а про сайт целиком, — так что вместо перехода в базе берём паузу по
# часам в памяти процесса. Она не переживает рестарт воркера, но смена держателя
# и так single-flight (см. `dating_chat_hold.py`): в худшем случае рестарт даёт
# одно лишнее объявление, а не шторм.
DATING_REVIVE_PRESENCE_ANNOUNCE_COOLDOWN_SEC = 3600


async def _presence_missing_announce(site_id: int, reason: str, why: str) -> None:
    """Строка в «Ошибках» соседа про сайт, который выпал из круга целиком —
    не каждый круг, а раз в час на каждую причину, пока беда не пройдёт.

    Часы здесь **монотонные**, а не календарные: меряется не «когда это было», а
    «сколько прошло внутри этого же процесса». Календарные часы для такого не
    годятся — рывок NTP назад или перевод времени заглушил бы объявления на лишний
    час, и о выпавшем сайте никто бы не узнал. Тем же `time.monotonic()` меряет
    свои сроки держатель сокетов.
    """
    from src.rhapsody.rhapsody_api import rhapsody_api_error_write

    key = (int(site_id), reason)
    now = time.monotonic()
    last = _presence_missing_at.get(key)
    if last is not None and now - last < DATING_REVIVE_PRESENCE_ANNOUNCE_COOLDOWN_SEC:
        return

    _presence_missing_at[key] = now
    await rhapsody_api_error_write(
        'dating', f'сайт #{site_id} выпал из круга проверки: {why}',
        context={'site_id': int(site_id), 'where': 'автовосстановление входа'})


async def _probe_save(girl_id: int, probe: dict) -> None:
    """Итог опроса в карточку. Раньше это делала только открытая страница."""
    from logging_.logging_ import logger_info
    from mysql_.mysql_ import mysql_get_db_async

    seen = 'NOW()' if probe['status'] == 'online' else 'last_seen_at'
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'UPDATE dating_girl SET status = %s, unread = %s, last_error = %s, '
                f'       last_seen_at = {seen} WHERE id = %s',
                (probe['status'], probe['unread'] if probe['unread'] >= 0 else 0,
                 probe['error'], int(girl_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] итог опроса не записался: {e}')


async def _plan_save(girl_id: int, plan: dict) -> bool:
    """Новые тревога и откат. Успешный вход перепишет их сам — и это правильно.

    Исключение гасится (журналы работу не роняют), но итог возвращается: круг
    обязан знать, легла запись или нет. Незаписанный откат — это разрешение войти
    снова через минуту, и вход после потери записи круг отменяет.
    """
    from logging_.logging_ import logger_info
    from mysql_.mysql_ import mysql_get_db_async

    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_girl SET relogin_since = %s, relogin_tries = %s, '
                             '       relogin_next_at = %s WHERE id = %s',
                             (plan['relogin_since'], int(plan['relogin_tries']),
                              plan['relogin_next_at'], int(girl_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] череда попыток не записалась: {e}')
        return False

    return True


async def _error_write(girl: dict, why: str) -> None:
    """Переход состояния — во вкладку «Ошибки» соседа. Каждая неудача туда не идёт:
    за сутки набежало бы два десятка одинаковых строк."""
    from src.rhapsody.rhapsody_api import rhapsody_api_error_write

    await rhapsody_api_error_write('dating', f"анкета «{girl['login']}» не входит: {why}",
                                   context={'girl_id': girl['id'], 'site_id': girl['site_id'],
                                            'where': 'автовосстановление входа'})
