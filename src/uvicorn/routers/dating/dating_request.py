"""Вкладка «Запросы»: каждый поход наружу отдельной строкой.

⚠ **Своя ручка и своя таблица, а не третий источник общей ленты.** В ленте
«Журнал» сходятся `dating_log` и `dating_scenario_run` — десятки строк в сутки,
которые человек читает глазами. Здесь десятки тысяч: одно присутствие даёт 37
тысяч в сутки на тринадцать анкет. Смешав их, мы утопили бы журнал анкеты в
потоке ударов — см. `migrations/20260915100000_dating_request.sql`.

Разные вопросы: журнал отвечает «что происходило с анкетой», а эта вкладка —
«куда и зачем мы ходили и что нам ответили».
"""

from fastapi import APIRouter, Depends, HTTPException, status

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.dating.dating_request import DATING_REQUEST_DOORS

router = APIRouter(prefix='/api/dating/request', tags=['dating'])

# Сколько строк на странице. Больше, чем у журнала: строка здесь короче и читается
# столбцами — время, дверь, путь, исход.
DATING_REQUEST_PAGE = 100
DATING_REQUEST_PAGE_MAX = 500

# Чем отбирают. `fail` первым: девять из десяти заходов сюда — «почему не
# получилось», и удачные строки в этот момент только мешают.
DATING_REQUEST_RESULTS = ('all', 'fail', 'ok')


@router.get('', include_in_schema=False)
async def dating_request_list(site_id: int = 0, profile_id: int = 0, door: str = '',
                              result: str = 'all', page: int = 1,
                              per_page: int = DATING_REQUEST_PAGE,
                              user=Depends(auth_required)):
    """Лента внешних запросов по сайту, свежие сверху.

    Args:
        site_id: сайт. Обязателен — как и у журнала: анкеты разных сайтов живут
            своей жизнью, и общий список без отбора перемешал бы одно с другим.
            ⚠ Походы **без сайта** (к соседу: справочник, агенты, проверка
            выходов) показываются при любом выбранном: они не про сайт, и своего
            места в отборе у них нет.
        profile_id: сузить до одной анкеты. Ноль — все, включая походы по сайту.
        door: занятие (`login`, `mail`, `heartbeat`…). Пусто — любое.
        result: `all`, `fail` (только неудачи) или `ok`.
        page: страница, с единицы.
        per_page: строк на странице, не больше `DATING_REQUEST_PAGE_MAX`.

    Returns:
        dict: `rows`, `total`, `page`, `pages`, `doors` — как называть двери
        человеку (страница берёт отсюда, своей копии не держит).

    Raises:
        HTTPException: 422 — не выбран сайт либо незнакомый отбор.
    """
    if not site_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не выбран сайт')

    if str(result or 'all') not in DATING_REQUEST_RESULTS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f'неизвестный отбор «{result}»')

    if door and str(door) not in DATING_REQUEST_DOORS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f'неизвестная дверь «{door}»')

    page = max(1, int(page or 1))
    per_page = min(max(1, int(per_page or DATING_REQUEST_PAGE)), DATING_REQUEST_PAGE_MAX)

    # ⚠⚠ **Походы без сайта показываем вместе с выбранным, а не прячем.**
    #
    # Здесь стояло голое `r.site_id = %s`, и строки с нулём не попадали в ленту
    # **никогда**: человек смотрел на вкладку «Запросы» и не находил там ни одного
    # обращения к соседу, хотя их две сотни в час. Записать и не показать — хуже,
    # чем не записать: в первом случае журнал молча врёт о полноте.
    #
    # Ноль значит «поход не про конкретный сайт»: к соседу ходят за справочником,
    # агентами и проверкой выходов — сайта там нет и быть не может. Прятать их от
    # обеих вкладок сразу означало бы, что видны они только в базе.
    where = ['(r.site_id = %s OR r.site_id = 0)']
    args = [int(site_id)]

    if int(profile_id or 0):
        where.append('r.profile_id = %s')
        args.append(int(profile_id))

    if door:
        where.append('r.door = %s')
        args.append(str(door))

    if result == 'fail':
        where.append('r.ok = 0')
    elif result == 'ok':
        where.append('r.ok = 1')

    clause = ' AND '.join(where)

    async with mysql_get_db_async() as db:
        await db.execute(f'SELECT COUNT(*) AS n FROM dating_request r WHERE {clause}',
                         tuple(args))
        total = int(((await db.fetchone()) or {}).get('n') or 0)

        # ⚠ Имя анкеты подтягиваем **слева**: у походов по сайту `profile_id`
        # ноль, и обычный `JOIN` выбросил бы из ленты ровно те строки, ради
        # которых её и завели, — ленту «Все онлайн» и сессию панели.
        await db.execute(
            'SELECT r.id, r.profile_id, r.door, r.method, r.path, r.status, r.ok,'
            '       r.error, r.ms, r.proxy_ip, r.created_at,'
            "       COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(p.profile, '$.name')), ''),"
            "                p.login, '') AS profile_name"
            '  FROM dating_request r'
            '  LEFT JOIN dating_profile p ON p.id = r.profile_id'
            f' WHERE {clause}'
            ' ORDER BY r.id DESC LIMIT %s OFFSET %s',
            tuple(args + [per_page, (page - 1) * per_page]))
        rows = await db.fetchall()

    return {'result': {
        'rows': [{**dict(row), 'ok': bool(row['ok'])} for row in rows or []],
        'total': total, 'page': page,
        'pages': max(1, -(-total // per_page)),
        'doors': DATING_REQUEST_DOORS}}
