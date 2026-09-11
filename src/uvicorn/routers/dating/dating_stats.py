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
from src.rhapsody.rhapsody_api import RHAPSODY_API_LLM_DAYS, rhapsody_api_llm_usage

router = APIRouter(prefix='/api/dating/stats', tags=['dating'])


@router.get('', include_in_schema=False)
async def dating_stats_get(site_id: int = 0, profile_id: int = 0,
                           user: AuthUser = Depends(auth_required)):
    """Числа по сайту, а с `profile_id` — по одной анкете.

    Returns:
        dict: `chat`, `mail`, `favorite`, `invite`, `llm` — по группе на занятие,
        плюс `profile_id` и `site_id` эхом: страница рисует подпись «по сайту» или
        «по анкете», и решать это должен один ответ, а не два источника.

    ⚠ **Ноль сайта — пустые числа, а не сумма по всем сайтам.** Селект сайта стоит
    в шапке страницы и пустым не бывает; сумма по разным сайтам не значит ничего —
    у них разные анкеты и разные собеседники.
    """
    site_id, profile_id = int(site_id or 0), int(profile_id or 0)
    if not site_id:
        return {'result': _empty(site_id, profile_id)}

    return {'result': {'site_id': site_id, 'profile_id': profile_id,
                       'chat': await _chat(site_id, profile_id),
                       'mail': await _mail(site_id, profile_id),
                       'favorite': await _favorite(site_id, profile_id),
                       'invite': await _invite(site_id, profile_id),
                       'llm': await _llm()}}


# ── Приватное ────────────────────────────────────────────────────────────────

def _empty(site_id: int, profile_id: int) -> dict:
    """Пустой ответ той же формы. ⚠ Формы одной: страница не должна помнить, какая
    ветка что кладёт, — отсутствующий ключ читался бы как ноль только случайно."""
    return {'site_id': site_id, 'profile_id': profile_id,
            'chat': {'in': 0, 'out': 0, 'dialogs': 0, 'waiting': 0},
            'mail': {'in': 0, 'out': 0, 'draft': 0, 'unseen': 0},
            'favorite': {'added': 0, 'already': 0, 'failed': 0},
            'invite': {'out': 0, 'draft': 0},
            'llm': {'days': 0, 'requests': 0, 'errors': 0, 'tokens_input': 0,
                    'tokens_output': 0, 'rows': [], 'error': ''}}


async def _chat(site_id: int, profile_id: int) -> dict:
    """Реплики чата: пришло, ушло, сколько разговоров и сколько ждут ответа.

    ⚠ **Отбор по сайту идёт через анкету**, а не по своей колонке: у
    `dating_message` её нет, и это правильно — сообщение принадлежит переписке
    анкеты, а сайт у анкеты уже записан.

    ⚠ **«Ждут ответа» считается тем же правилом, что значок ↩ в списке диалогов**
    (последняя реплика входящая, не отвечена и агент не промолчал). Своя копия
    правила разошлась бы с тем, что человек видит на «Чате», и верить нельзя было
    бы обеим.
    """
    sql = ("SELECT SUM(m.direction = 'in') AS `in`,"
           "       SUM(m.direction = 'out') AS `out`,"
           '       COUNT(DISTINCT m.sender_uid) AS dialogs'
           '  FROM dating_message m'
           '  JOIN dating_profile p ON p.id = m.profile_id'
           ' WHERE p.site_id = %s AND (%s = 0 OR m.profile_id = %s)')

    waiting_sql = (
        '  SELECT COUNT(*) AS n FROM ('
        '    SELECT m.profile_id, m.sender_uid, MAX(m.created_at) AS last_at'
        '      FROM dating_message m'
        '      JOIN dating_profile p ON p.id = m.profile_id'
        '     WHERE p.site_id = %s AND (%s = 0 OR m.profile_id = %s)'
        '     GROUP BY m.profile_id, m.sender_uid) d'
        '  JOIN dating_message last'
        '    ON last.profile_id = d.profile_id AND last.sender_uid = d.sender_uid'
        '   AND last.created_at = d.last_at'
        "  WHERE last.direction = 'in' AND last.is_answered = 0"
        '    AND last.answer_silent_at IS NULL')

    args = (site_id, profile_id, profile_id)
    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, args)
            row = dict((await db.fetchone()) or {})

            await db.execute(waiting_sql, args)
            waiting = int(((await db.fetchone()) or {}).get('n') or 0)
    except Exception as e:
        logger_info(f'[dating] статистика чата не сосчиталась: {e}')

        return {'in': 0, 'out': 0, 'dialogs': 0, 'waiting': 0}

    return {'in': int(row.get('in') or 0), 'out': int(row.get('out') or 0),
            'dialogs': int(row.get('dialogs') or 0), 'waiting': waiting}


async def _mail(site_id: int, profile_id: int) -> dict:
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
                'SELECT COUNT(*) AS n, SUM(seen = 0) AS unseen FROM dating_mail_in'
                ' WHERE site_id = %s AND (%s = 0 OR profile_id = %s)', args)
            row = dict((await db.fetchone()) or {})
            got['in'] = int(row.get('n') or 0)
            got['unseen'] = int(row.get('unseen') or 0)

            # Два пула одним запросом: строки у них одинаковой формы, а вопрос
            # общий — «сколько ушло и сколько ждёт».
            for table in ('dating_mailing', 'dating_mail_reply'):
                await db.execute(
                    f"SELECT SUM(status = 'sent') AS sent,"
                    f"       SUM(status = 'draft') AS draft FROM {table}"
                    ' WHERE site_id = %s AND (%s = 0 OR profile_id = %s)', args)
                row = dict((await db.fetchone()) or {})
                got['out'] += int(row.get('sent') or 0)
                got['draft'] += int(row.get('draft') or 0)
    except Exception as e:
        logger_info(f'[dating] статистика писем не сосчиталась: {e}')

    return got


async def _favorite(site_id: int, profile_id: int) -> dict:
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
                "       SUM(state = 'fail') AS failed FROM dating_favorite"
                ' WHERE site_id = %s AND (%s = 0 OR profile_id = %s)',
                (site_id, profile_id, profile_id))
            row = dict((await db.fetchone()) or {})
    except Exception as e:
        logger_info(f'[dating] статистика избранных не сосчиталась: {e}')

        return {'added': 0, 'already': 0, 'failed': 0}

    return {'added': int(row.get('added') or 0),
            'already': int(row.get('already') or 0),
            'failed': int(row.get('failed') or 0)}


async def _invite(site_id: int, profile_id: int) -> dict:
    """Инвайты: ушло и лежит в пуле.

    ⚠ **Входящего у инвайта не бывает по определению**: это первая реплика тому, с
    кем разговора не было. Ответ на неё — уже чат, и считается там.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                "SELECT SUM(status = 'sent') AS sent,"
                "       SUM(status = 'draft') AS draft FROM dating_invite"
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
