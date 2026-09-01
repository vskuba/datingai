"""Общая лента журнала: всё, что происходило по сайту, одним списком.

Журнал по одной анкете отдаёт `dating_profile_view.py` — он про «что было с этой
учёткой». Здесь другой вопрос: **что вообще происходило на сайте**, без выбора
анкеты заранее. С него начинают разбор, когда неизвестно, где искать.

## Два источника, одна лента

| Источник | Что в нём | Чего нет |
|----------|-----------|----------|
| `dating_log` | ход работы по анкетам: вход, проверка, сообщения | записей без анкеты |
| `dating_scenario_run` | прогоны сценариев: сценарий, исход, шаг, длительность | ничего, кроме прогонов |

Лента собирается **`UNION ALL` в базе**, а не склейкой двух ответов в Python. Иначе
пришлось бы тянуть с запасом из обеих таблиц и резать после сортировки: чтобы
показать пятьдесят свежих строк, надо знать, сколько их взять из каждой, а этого
заранее не знает никто.

⚠ **Прогоны импорта и выгрузки видны только здесь.** У них нет анкеты (`profile_id`
пуст) — они идут по сайту целиком, и в журнал ни одной учётки не попадают. Это и
есть главный повод завести общую ленту: до неё их след не был виден нигде.

## Почему сайт обязателен

Анкеты у разных сайтов живут своей жизнью, и общий список без отбора перемешал бы
работу по одному сайту с работой по другому. Селект сайта на странице всё равно
есть — он общий на весь раздел.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required

router = APIRouter(prefix='/api/dating/log', tags=['dating'])

# Сколько строк на странице. Пятьдесят — столько прочитывают глазами за один
# заход; остальные не теряются, за ними пейджер.
DATING_LOG_PAGE = 50

# Предел, дальше которого не пускаем, даже если попросили. Лента читается
# человеком, а не выгружается: тысяча строк за запрос — это не чтение.
DATING_LOG_PAGE_MAX = 200

# Что показываем. `all` — обе ленты вместе, `profile` — только работа по анкетам,
# `scenario` — только прогоны сценариев.
#
# Отбор по источнику, а не по уровню: уровни (`ok`, `fail`) есть у обоих, а вопрос
# «что запускалось у соседа» отвечается именно источником.
DATING_LOG_KINDS = ('all', 'profile', 'scenario')


@router.get('', include_in_schema=False)
async def dating_log_list(site_id: int = 0, kind: str = 'all', level: str = '',
                          q: str = '', page: int = 1, per_page: int = DATING_LOG_PAGE,
                          user=Depends(auth_required)):
    """Общая лента журнала по сайту, свежие сверху.

    Args:
        site_id: сайт. Обязателен — почему, в шапке модуля.
        kind: `all`, `profile` (журнал анкет) или `scenario` (прогоны сценариев).
        level: `ok`, `fail`, `info`, `in`, `out` — пусто значит «все».
        q: подстрока в тексте строки или в имени анкеты.
        page: страница, с единицы.
        per_page: строк на странице, не больше `DATING_LOG_PAGE_MAX`.

    Returns:
        dict: `rows` — строки ленты, `total`, `page`, `pages`.

        В строке: `source` (`profile` или `scenario`), `level`, `text`,
        `created_at`, `profile_id`, `profile_name`, и у прогонов — `scenario_name`,
        `step_no`, `ms`.

    Raises:
        HTTPException: 422 — не выбран сайт или неизвестный вид ленты.
    """
    if not site_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не выбран сайт')

    if kind not in DATING_LOG_KINDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f'неизвестная лента «{kind}»')

    per_page = max(1, min(int(per_page or DATING_LOG_PAGE), DATING_LOG_PAGE_MAX))
    page = max(1, int(page or 1))

    sql, params = _query(int(site_id), kind, str(level or ''), str(q or '').strip())

    async with mysql_get_db_async() as db:
        await db.execute(f'SELECT COUNT(*) AS n FROM ({sql}) AS лента', params)
        total = int((await db.fetchone() or {}).get('n') or 0)

        # Номер страницы поправляем здесь же и возвращаем исправленный: список
        # мог укоротиться, пока человек листал, и просить у него ещё одно нажатие,
        # чтобы увидеть хоть что-то, незачем. То же делает карточка собеседниц.
        pages = max(1, -(-total // per_page))
        page = min(page, pages)

        await db.execute(f'{sql} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s',
                         (*params, per_page, (page - 1) * per_page))
        rows = await db.fetchall()

    return {'result': {'rows': [dict(row) for row in rows],
                       'total': total, 'page': page, 'pages': pages}}


# ── Приватное ────────────────────────────────────────────────────────────────

# Как зовут анкету в ленте. Отдельной колонки с именем у неё нет: имя приходит с
# сайта и лежит в JSON-столбце `profile`. Нет и его — показываем логин: он есть
# всегда, а безымянная строка не говорит, о какой анкете речь.
#
# Выражением, а не подзапросом: оно попадает и в `SELECT`, и в поиск, и две
# разошедшиеся копии дали бы «ищу по одному, показываю другое».
_NAME = ("COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(p.profile, '$.name')), 'null'),"
         " p.login, '')")


def _query(site_id: int, kind: str, level: str, q: str) -> tuple[str, list]:
    """Собирает запрос ленты и его параметры.

    Обе половины дают **одинаковый набор колонок** — иначе `UNION` их не сложит.
    Недостающее подставляется пустым: у строки журнала анкеты нет ни сценария, ни
    длительности, и это не потеря, а разная природа записей.

    Отбор по сайту у половин разный, и это не оплошность: у прогона сайт записан
    прямо в строке, а строка журнала анкеты знает только анкету — сайт достаётся
    через неё.
    """
    parts, params = [], []

    if kind in ('all', 'profile'):
        where = ['p.site_id = %s']
        args = [site_id]
        if level:
            where.append('l.level = %s')
            args.append(level)
        if q:
            where.append(f'(l.text LIKE %s OR {_NAME} LIKE %s OR p.login LIKE %s)')
            args += [f'%{q}%'] * 3

        parts.append(
            "SELECT l.id, 'profile' AS source, l.level, l.text, l.created_at,"
            f'       p.id AS profile_id, {_NAME} AS profile_name,'
            "       '' AS scenario_name, 0 AS step_no, 0 AS ms"
            '  FROM dating_log l'
            '  JOIN dating_profile p ON p.id = l.profile_id'
            f' WHERE {" AND ".join(where)}')
        params += args

    if kind in ('all', 'scenario'):
        where = ['r.site_id = %s']
        args = [site_id]
        # У прогона нет колонки `level`: он либо дошёл, либо встал. Сводим к тем же
        # уровням, что у журнала анкеты, — иначе фильтр «только отказы» работал бы
        # на одной половине ленты и молчал на другой.
        if level in ('ok', 'fail'):
            where.append('r.status = %s')
            args.append('ok' if level == 'ok' else 'error')
        elif level:
            # Уровни `info`, `in`, `out` бывают только у журнала анкеты — прогонов
            # с ними не бывает, и половина ленты должна остаться пустой.
            where.append('1 = 0')
        if q:
            where.append('(r.scenario_name LIKE %s OR r.error LIKE %s'
                         f' OR {_NAME} LIKE %s OR p.login LIKE %s)')
            args += [f'%{q}%'] * 4

        parts.append(
            "SELECT r.id, 'scenario' AS source,"
            "       CASE r.status WHEN 'ok' THEN 'ok' ELSE 'fail' END AS level,"
            "       COALESCE(r.error, '') AS text, r.created_at,"
            f'       r.profile_id, {_NAME} AS profile_name,'
            '       r.scenario_name, r.step_no, r.ms'
            '  FROM dating_scenario_run r'
            '  LEFT JOIN dating_profile p ON p.id = r.profile_id'
            f' WHERE {" AND ".join(where)}')
        params += args

    return ' UNION ALL '.join(parts), params
