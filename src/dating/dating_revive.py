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

from datetime import datetime, timedelta

from src.dating.dating_json import dating_json_load

# Как часто смена сверяет вход анкет с сайтом.
DATING_REVIVE_PERIOD_SEC = 60

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
    logged_in_at = girl.get('logged_in_at')
    old = not logged_in_at or (now - logged_in_at).total_seconds() >= DATING_REVIVE_AGE_SEC

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

    Вход передаётся параметром, а не импортируется намертво: так круг проверяется
    тестами без браузера, а модуль не тянет за собой половину проекта.

    Returns:
        dict: `checked`, `online`, `tried` (сколько раз входили), `revived`,
        `given_up`.
    """
    from src.dating.dating_log import dating_log_write
    from src.dating.dating_login import dating_login_run
    from src.dating.dating_presence import dating_presence_probe

    login_run = login_run or dating_login_run
    counts = {'checked': 0, 'online': 0, 'tried': 0, 'revived': 0, 'given_up': 0}
    now = datetime.now()

    for girl in await _girls_kept():
        counts['checked'] += 1
        presence = await _presence_of(int(girl['site_id']))
        if not presence:
            continue

        probe = await dating_presence_probe(girl, presence)
        online = probe['status'] == 'online'
        counts['online'] += int(online)
        await _probe_save(int(girl['id']), probe)

        plan = dating_revive_plan(girl, online, now)
        await _plan_save(int(girl['id']), plan)

        if plan['action'] == 'wait':
            await dating_log_write(int(girl['id']), 'fail',
                                   f"Вход не признан сайтом: {probe['error'] or probe['status']}")
            await _error_write(girl, probe['error'] or probe['status'])
        elif plan['action'] == 'give_up':
            # Считаем и объявляем переход, а не состояние: решение отдаёт `give_up`
            # только на том круге, который сдался, — иначе сюда бы попадала одна и
            # та же анкета каждую минуту, пока её не поднимут руками.
            counts['given_up'] += 1
            await dating_log_write(int(girl['id']), 'fail',
                                   'Сутки не выходит войти — попытки прекращены, нужен вход вручную')
            await _error_write(girl, 'сутки не выходит войти, автовход остановлен')
        elif plan['action'] == 'login':
            counts['tried'] += 1
            if await _login_try(int(girl['id']), login_run, plan, online):
                counts['revived'] += 1

    return counts


async def _login_try(girl_id: int, login_run, plan: dict, planned: bool = False) -> bool:
    """Одна попытка входа. Успех сам снимает откат — это делает `dating_login_run`.

    Повод берётся из опроса, а не из полей учётки: откат есть у обоих поводов, а
    человеку в журнале важно, чинили протухший вход или обновляли живой по сроку.
    """
    from src.dating.dating_log import dating_log_write

    await dating_log_write(girl_id, 'info',
                           f"{'Обновляю вход заранее' if planned else 'Вхожу заново'} "
                           f"(попытка {plan['relogin_tries']})")
    try:
        result = await login_run(girl_id)
    except Exception as e:
        await dating_log_write(girl_id, 'fail', f'Войти не вышло: {str(e)[:300]}')
        return False

    ok = str(result.get('status')) == 'success' and not result.get('error')
    await dating_log_write(girl_id, 'ok' if ok else 'fail',
                           'Вход восстановлен' if ok
                           else f"Войти не вышло: {str(result.get('error') or '')[:300]}")

    return ok


async def _girls_kept() -> list[dict]:
    """Анкеты под присмотром автомата — те, кого хотя бы раз ввели успешно."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, login, site_id, storage_state, keep_online, logged_in_at, '
            '       relogin_since, relogin_tries, relogin_next_at '
            '  FROM dating_girl WHERE keep_online = 1 ORDER BY id')
        rows = await db.fetchall()

    return [dict(row) for row in (rows or [])]


async def _presence_of(site_id: int) -> dict:
    """Настройка опроса сайта. Сосед не ответил — пустой словарь: круг повторится."""
    from logging_.logging_ import logger_info
    from src.dating.dating_presence import dating_presence_ready
    from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_get

    try:
        site = await rhapsody_api_site_get(int(site_id))
    except RhapsodyError as e:
        logger_info(f'[dating] сайт #{site_id} не спросился: {e}')
        return {}

    presence = dating_json_load(site.get('presence'))

    return presence if dating_presence_ready(presence) else {}


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


async def _plan_save(girl_id: int, plan: dict) -> None:
    """Новые тревога и откат. Успешный вход перепишет их сам — и это правильно."""
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


async def _error_write(girl: dict, why: str) -> None:
    """Переход состояния — во вкладку «Ошибки» соседа. Каждая неудача туда не идёт:
    за сутки набежало бы два десятка одинаковых строк."""
    from src.rhapsody.rhapsody_api import rhapsody_api_error_write

    await rhapsody_api_error_write('dating', f"анкета «{girl['login']}» не входит: {why}",
                                   context={'girl_id': girl['id'], 'site_id': girl['site_id'],
                                            'where': 'автовосстановление входа'})
