"""Статистика: сколько чего прошло через панель.

Вкладка «Статистика» на «Профилях» отвечает на один вопрос — **сколько работы
сделано**, — и разбит он по занятиям: чат, письма, избранные, инвайты. Плюс цена
этой работы в вызовах LLM.

## ⚠ Анкета — фильтр, а не условие

Как у диалогов (`api.md`, §3.8): без выбранной анкеты числа по всему сайту, с
выбранной — по ней. Иначе вкладка отвечала бы «выберите анкету» на вопрос «что
вообще происходит на сайте», который здесь и главный.

## Что считается «входящим» и «исходящим»

| Занятие | Входящее | Исходящее |
|---------|----------|-----------|
| чат | реплики `direction = 'in'` | реплики `direction = 'out'` |
| письма | `dating_mail_in` — пришло с сайта | ушедшее из пула рассылки плюс ушедшие ответы |
| инвайты | — | ушедшие первые реплики |

⚠ **У избранных «входящего» нет, и придумывать его нельзя.** Звёздочку жмём мы;
знает ли сайт, кто добавил **нас**, — вопрос к сайту, и такой ручки у него нет.
Поэтому у избранных свои три числа: добавили сами, были там до нас, не вышло. Числа
честные, хотя и не парные — пара «вход/выход» тут была бы выдумкой.

## ⚠ Черновик — не работа

Пул писем, ответов и инвайтов считается **по ушедшим**, а лежащее в пуле идёт
отдельной строкой «готово, не ушло». Слей мы их, «исходящих 40» означало бы сорок
подготовленных, из которых уйти могло ни одного, — и статистика врала бы ровно там,
где на неё смотрят.

## Токены считает сосед

Своей копии у нас нет: токены знает тот, кто зовёт модель (`rhapsody_api.md`).
⚠ Журнал живёт **семь дней**, и это единственное число здесь не «за всё время» —
подпись на странице об этом говорит.
"""

from fastapi import APIRouter, Depends

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.dating_agent import DATING_AGENT_NODE
from src.dating.dating_json import dating_json_load
from src.rhapsody.rhapsody_api import RHAPSODY_API_LLM_DAYS, rhapsody_api_llm_usage

router = APIRouter(prefix='/api/dating/stats', tags=['dating'])


@router.get('', include_in_schema=False)
async def dating_stats_get(site_id: int = 0, profile_id: int = 0, hours: int = 0,
                           user: AuthUser = Depends(auth_required)):
    """Числа по сайту, а с `profile_id` — по одной анкете.

    Returns:
        dict: `chat`, `mail`, `favorite`, `invite`, `llm` — по группе на занятие,
        плюс `profile_id` и `site_id` эхом: страница рисует подпись «по сайту» или
        «по анкете», и решать это должен один ответ, а не два источника.

    ⚠ **Ноль сайта — пустые числа, а не сумма по всем сайтам.** Селект сайта стоит
    в шапке страницы и пустым не бывает; сумма по разным сайтам не значит ничего —
    у них разные анкеты и разные собеседники.

    Args:
        hours: окно в часах — 12, 24 или ноль («за всё время»).

            ⚠ **Скользящее окно, а не календарные сутки.** Вопрос к нему «сколько
            сделано за последние сутки», и полночь на него не отвечает: в 00:10
            календарные сутки показали бы десять минут работы.

            ⚠ **Считается по времени события**, и у каждого занятия оно своё:
            реплика — когда написана, письмо — когда ушло, черновик — когда
            приготовлен. Общей колонки у них нет, и подставлять одну значило бы
            считать разное одинаково.

            ⚠ **LLM окну не подчиняется**: журнал живёт у соседа, он отдаёт его за
            свои семь дней и разрезать их не умеет. Подпись под сеткой об этом
            говорит — иначе числа рядом читались бы как одно окно.
    """
    site_id, profile_id = int(site_id or 0), int(profile_id or 0)
    hours = int(hours or 0)
    if not site_id:
        return {'result': _empty(site_id, profile_id)}

    return {'result': {'site_id': site_id, 'profile_id': profile_id,
                       'hours': hours,
                       'chat': await _chat(site_id, profile_id, hours),
                       'mail': await _mail(site_id, profile_id, hours),
                       'favorite': await _favorite(site_id, profile_id, hours),
                       'invite': await _invite(site_id, profile_id, hours),
                       'llm': await _llm()}}


# ── Приватное ────────────────────────────────────────────────────────────────


def _window(column: str, hours: int) -> str:
    """Кусок условия «за последние N часов». Ноль часов — пустая строка.

    Args:
        column: колонка со временем события. ⚠ Своя у каждого занятия: реплика —
            когда написана, письмо — когда ушло, черновик — когда приготовлен.
        hours: окно. Ноль означает «за всё время», и тогда условия нет вовсе.

    ⚠ **Число подставляем в текст, а не параметром**, и это не оплошность: `hours`
    приводится к `int` вызывающим, а `INTERVAL ? HOUR` с плейсхолдером MySQL не
    принимает. Колонка — наша константа из этого файла.
    """
    hours = int(hours or 0)

    return f' AND {column} > NOW() - INTERVAL {hours} HOUR' if hours > 0 else ''


def _empty(site_id: int, profile_id: int) -> dict:
    """Пустой ответ той же формы. ⚠ Формы одной: страница не должна помнить, какая
    ветка что кладёт, — отсутствующий ключ читался бы как ноль только случайно."""
    return {'site_id': site_id, 'profile_id': profile_id, 'hours': 0,
            'chat': {'in': 0, 'out': 0, 'dialogs': 0, 'waiting': 0},
            'mail': {'in': 0, 'out': 0, 'draft': 0, 'unseen': 0},
            'favorite': {'added': 0, 'already': 0, 'failed': 0},
            'invite': {'out': 0, 'draft': 0},
            'llm': {'days': 0, 'requests': 0, 'errors': 0, 'tokens_input': 0,
                    'tokens_output': 0, 'rows': [], 'error': ''}}


@router.get('/profiles', include_in_schema=False)
async def dating_stats_profiles(site_id: int = 0, hours: int = 0,
                                user: AuthUser = Depends(auth_required)):
    """Работа каждой анкеты сайта: ушло и не ушло по трём занятиям.

    Args:
        site_id: сайт-закладка. Ноль — пустой список.
        hours: окно в часах — 12, 24 или ноль («за всё время»). То же, что у
            общей сводки: числа стоят на соседних вкладках, и разные сроки у них
            читались бы как разница по существу.

    Returns:
        dict: `result` — строка на анкету: `profile_id`, `name`, `photo`, `uid`,
        `status`, и по паре чисел на занятие — `invite`, `favorite`, `mailing`,
        каждое `{'sent': …, 'failed': …}`.

    ⚠ **Три занятия, а не одно.** Вопрос вкладки — «кто из девушек работает», а
    работа у анкеты троякая: зовёт в чат, пристаёт к избранным, шлёт рассылку.
    Одни инвайты отвечали бы на него только про чат.

    ⚠ **Своя ручка, а не поле в общей.** Общая отвечает «сколько сделано на
    сайте» — одно число на занятие; здесь строка на анкету. Слей их, каждое
    открытие вкладки считало бы десяток строк ради четырёх чисел в шапке.

    ⚠ **«Не ушло» — это `failed` плюс черновики с причиной.** Неудачная отправка
    оставляет строку в пуле, и по статусу её от нетронутой не отличить: спроси мы
    `status = 'failed'`, число молчало бы ровно о тех попытках, ради которых
    вкладку и открывают.

    ⚠ **Анкеты без единой строки тоже в списке.** Ноль — это ответ: «она включена,
    в сети, а работы от неё не ушло» — то, что и надо увидеть.
    """
    if not int(site_id or 0):
        return {'result': []}

    # ⚠ **Время неудачи мы знаем приблизительно, и честнее так и считать.**
    # У ушедшего есть `sent_at`; у неудачи его нет — инвайт его снимает, а письмо
    # и не ставило. Точное время лежало бы в `updated_at`, но он есть только у
    # `dating_invite`: у двух почтовых пулов его нет вовсе. Одно правило на три
    # таблицы лучше трёх разных, поэтому берём `COALESCE(sent_at, created_at)` —
    # «когда ушло, а если не ушло, то когда приготовили».
    when = 'COALESCE(sent_at, created_at)'

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT p.id AS profile_id, p.status, p.enabled,'
                '       p.login AS profile_login, p.profile AS profile_card'
                '  FROM profile p WHERE p.site_id = %s'
                ' ORDER BY p.id', (int(site_id),))
            girls = [dict(row) for row in await db.fetchall() or []]

            # ⚠ Запрос на занятие, а не один сшитый: у трёх таблиц одинаковые
            # столбцы и разные объёмы, и `LEFT JOIN` всех трёх разом умножал бы
            # строки друг на друга — классическая ошибка, которую видно только на
            # живых данных, когда числа втрое больше правды.
            work = {}
            for table, kind in (('invite', 'invite'),
                                ('favorite_mail', 'favorite'),
                                ('mailing', 'mailing')):
                await db.execute(
                    f"SELECT profile_id,"
                    f"       SUM(status = 'sent'{_window('sent_at', hours)}) AS sent,"
                    f"       SUM((status = 'failed'"
                    f"            OR (status = 'draft' AND error <> ''))"
                    f"{_window(when, hours)}) AS failed"
                    f'  FROM {table} WHERE site_id = %s GROUP BY profile_id',
                    (int(site_id),))
                for row in await db.fetchall() or []:
                    work.setdefault(int(row['profile_id']), {})[kind] = {
                        'sent': int(row['sent'] or 0),
                        'failed': int(row['failed'] or 0)}
    except Exception as e:
        logger_info(f'[dating] статистика по анкетам не сосчиталась: {e}')

        return {'result': []}

    empty = {'sent': 0, 'failed': 0}
    out = []
    for girl in girls:
        card = dating_json_load(girl['profile_card']) or {}
        mine = work.get(int(girl['profile_id']), {})
        out.append({'profile_id': int(girl['profile_id']),
                    'name': str(card.get('name') or girl['profile_login'] or ''),
                    'photo': str(card.get('photo') or ''),
                    'uid': str(card.get('site_uid') or ''),
                    'status': str(girl['status'] or ''),
                    'enabled': bool(girl['enabled']),
                    'invite': mine.get('invite', dict(empty)),
                    'favorite': mine.get('favorite', dict(empty)),
                    'mailing': mine.get('mailing', dict(empty))})

    # ⚠ Порядок — по работе, а не по номеру: вкладку открывают, чтобы увидеть, кто
    # работает, и молчащие анкеты должны оказаться внизу сами.
    out.sort(key=lambda one: -(one['invite']['sent'] + one['favorite']['sent']
                               + one['mailing']['sent']))

    return {'result': out}


async def _chat(site_id: int, profile_id: int, hours: int = 0) -> dict:
    """Реплики чата: пришло, ушло, сколько разговоров и сколько ждут ответа.

    ⚠ **Отбор по сайту идёт через анкету**, а не по своей колонке: у
    `dating_message` её нет, и это правильно — сообщение принадлежит переписке
    анкеты, а сайт у анкеты уже записан.

    ⚠⚠ **«Разговоров» — столько же, сколько строк на вкладке «Диалоги».** Правило
    одно: пара «анкета — собеседник», где есть хоть одна **входящая** реплика.
    Разойдись они, два числа об одном и том же спорили бы на соседних вкладках, и
    верить нельзя было бы ни одному.

    ⚠ **«Исходящих» при этом много больше**, и это не противоречие: инвайт тоже
    ложится сюда исходящей репликой. Сто шестьдесят пять отправленных приглашений
    при одном ответе — это один разговор, а не сто шестьдесят пять.

    ⚠ **«Ждут ответа» считается тем же правилом, что значок ↩ в списке диалогов**
    (последняя реплика входящая, не отвечена и агент не промолчал). Своя копия
    правила разошлась бы с тем, что человек видит на «Чате», и верить нельзя было
    бы обеим.
    """
    # Время события у реплики — когда она написана.
    since = _window('m.created_at', hours)

    sql = ("SELECT SUM(m.direction = 'in') AS `in`,"
           "       SUM(m.direction = 'out') AS `out`"
           '  FROM message m'
           '  JOIN profile p ON p.id = m.profile_id'
           ' WHERE p.site_id = %s AND (%s = 0 OR m.profile_id = %s)' + since)

    # ⚠⚠ **Разговор — это пара, которой ответили**, слово в слово как на вкладке
    # «Диалоги» (`dating_chat.py`). Здесь стоял `COUNT(DISTINCT m.sender_uid)`, и
    # он врал дважды.
    #
    # Во-первых, `sender_uid` у исходящей реплики — это **адресат**, а не
    # отправитель: столбец один на обе стороны. Значит считались все, кому мы
    # когда-либо писали, — а пишем мы первыми, инвайтами, сотнями.
    #
    # Во-вторых, считались люди, а не пары: один мужчина у двух наших анкет — два
    # разговора, и на вкладке их именно два.
    #
    # Поймано заказчиком на живом проде: «входящих 6, исходящих 165, разговоров
    # 93» — при одном настоящем диалоге. Девяносто три это те, кого позвали и кто
    # промолчал.
    dialogs_sql = (
        'SELECT COUNT(*) AS n FROM ('
        '  SELECT m.profile_id, m.sender_uid FROM message m'
        '    JOIN profile p ON p.id = m.profile_id'
        '   WHERE p.site_id = %s AND (%s = 0 OR m.profile_id = %s)' + since +
        '   GROUP BY m.profile_id, m.sender_uid'
        "  HAVING SUM(m.direction = 'in') > 0) AS d")

    waiting_sql = (
        '  SELECT COUNT(*) AS n FROM ('
        '    SELECT m.profile_id, m.sender_uid, MAX(m.created_at) AS last_at'
        '      FROM message m'
        '      JOIN profile p ON p.id = m.profile_id'
        '     WHERE p.site_id = %s AND (%s = 0 OR m.profile_id = %s)' + since +
        '     GROUP BY m.profile_id, m.sender_uid) d'
        '  JOIN message last'
        '    ON last.profile_id = d.profile_id AND last.sender_uid = d.sender_uid'
        '   AND last.created_at = d.last_at'
        "  WHERE last.direction = 'in' AND last.is_answered = 0"
        '    AND last.answer_silent_at IS NULL')

    args = (site_id, profile_id, profile_id)
    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, args)
            row = dict((await db.fetchone()) or {})

            await db.execute(dialogs_sql, args)
            dialogs = int(((await db.fetchone()) or {}).get('n') or 0)

            await db.execute(waiting_sql, args)
            waiting = int(((await db.fetchone()) or {}).get('n') or 0)
    except Exception as e:
        logger_info(f'[dating] статистика чата не сосчиталась: {e}')

        return {'in': 0, 'out': 0, 'dialogs': 0, 'waiting': 0}

    return {'in': int(row.get('in') or 0), 'out': int(row.get('out') or 0),
            'dialogs': dialogs, 'waiting': waiting}


async def _mail(site_id: int, profile_id: int, hours: int = 0) -> dict:
    """Письма: пришло, ушло, лежит в пулах, непрочитано.

    ⚠ **Исходящее — это `sent`, а не «сколько наготовили».** Пул рассылки и пул
    ответов складываются в `draft` отдельной строкой: «исходящих 40» из сорока
    подготовленных, из которых не ушло ни одного, было бы неправдой ровно там, где
    на статистику и смотрят.

    ⚠ **Ответы считаются вместе с рассылкой**, а не отдельной группой: для
    получателя это одинаково «письмо от неё», и делить их значило бы отвечать на
    вопрос, которого никто не задаёт. Различие живёт на вкладке «Письма», где
    видно, что чему отвечает.
    """
    args = (site_id, profile_id, profile_id)
    got = {'in': 0, 'out': 0, 'draft': 0, 'unseen': 0}
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n, SUM(seen = 0) AS unseen FROM mail_in'
                ' WHERE site_id = %s AND (%s = 0 OR profile_id = %s)'
                # Время события у входящего письма — когда его написали на сайте.
                + _window('date_post', hours), args)
            row = dict((await db.fetchone()) or {})
            got['in'] = int(row.get('n') or 0)
            got['unseen'] = int(row.get('unseen') or 0)

            # Два пула одним запросом: строки у них одинаковой формы, а вопрос
            # общий — «сколько ушло и сколько ждёт».
            for table in ('mailing', 'mail_reply'):
                await db.execute(
                    # ⚠ Два числа — два времени: ушедшее меряется отправкой,
                    # лежащее в пуле — заведением. Одна колонка на оба считала бы
                    # «готово вчера, ушло сегодня» дважды или ни разу.
                    f"SELECT SUM(status = 'sent'{_window('sent_at', hours)})"
                    '         AS sent,'
                    f"       SUM(status = 'draft'{_window('created_at', hours)})"
                    f'         AS draft FROM {table}'
                    ' WHERE site_id = %s AND (%s = 0 OR profile_id = %s)', args)
                row = dict((await db.fetchone()) or {})
                got['out'] += int(row.get('sent') or 0)
                got['draft'] += int(row.get('draft') or 0)
    except Exception as e:
        logger_info(f'[dating] статистика писем не сосчиталась: {e}')

    return got


async def _favorite(site_id: int, profile_id: int, hours: int = 0) -> dict:
    """Избранные: добавили сами, были там до нас, не вышло.

    ⚠ **Пары «вход/выход» здесь нет, и выдумывать её нельзя.** Звёздочку жмём мы;
    знает ли сайт, кто добавил **нас**, — вопрос к сайту, а такой ручки у него нет
    (`api.md`, §3.7.6). Три честных числа полезнее двух выдуманных.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                "SELECT SUM(state = 'done') AS added,"
                "       SUM(state = 'already') AS already,"
                "       SUM(state = 'fail') AS failed FROM favorite"
                ' WHERE site_id = %s AND (%s = 0 OR profile_id = %s)'
                # Время события у звезды — когда её поставили (или переписали с
                # сайта): `updated_at` двигался бы на каждой сверке.
                + _window('created_at', hours),
                (site_id, profile_id, profile_id))
            row = dict((await db.fetchone()) or {})
    except Exception as e:
        logger_info(f'[dating] статистика избранных не сосчиталась: {e}')

        return {'added': 0, 'already': 0, 'failed': 0}

    return {'added': int(row.get('added') or 0),
            'already': int(row.get('already') or 0),
            'failed': int(row.get('failed') or 0)}


async def _invite(site_id: int, profile_id: int, hours: int = 0) -> dict:
    """Инвайты: ушло и лежит в пуле.

    ⚠ **Входящего у инвайта не бывает по определению**: это первая реплика тому, с
    кем разговора не было. Ответ на неё — уже чат, и считается там.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                # Те же два времени, что у писем: ушедшее меряется отправкой,
                # лежащее в пуле — заведением.
                f"SELECT SUM(status = 'sent'{_window('sent_at', hours)}) AS sent,"
                f"       SUM(status = 'draft'{_window('created_at', hours)})"
                '         AS draft FROM invite'
                ' WHERE site_id = %s AND (%s = 0 OR profile_id = %s)',
                (site_id, profile_id, profile_id))
            row = dict((await db.fetchone()) or {})
    except Exception as e:
        logger_info(f'[dating] статистика инвайтов не сосчиталась: {e}')

        return {'out': 0, 'draft': 0}

    return {'out': int(row.get('sent') or 0), 'draft': int(row.get('draft') or 0)}


async def _llm() -> dict:
    """Запросы к LLM и токены — у соседа.

    ⚠ **По узлу, а не по анкете.** Журнал соседа знает узел и учётку, но не знает,
    какой анкете принадлежал вызов: спрашивают агента сайта, а не анкеты. Поэтому
    число одно на всю панель и от фильтра по анкете не зависит — подпись на
    странице так и говорит.

    ⚠ **За семь дней, а не за всё время**: дольше журнал не живёт.
    """
    return await rhapsody_api_llm_usage(DATING_AGENT_NODE, RHAPSODY_API_LLM_DAYS)
