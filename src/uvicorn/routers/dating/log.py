"""Общая лента журнала: всё, что происходило по сайту, одним списком.

Здесь же собирается и **журнал одной анкеты** — тот, что показывает правая колонка
на «Профилях» (`profile_view.py` зовёт `log_feed` с номером анкеты).
Отличается он только отбором: по анкете вместо сайта.

⚠ **Прогон сценария — одна строка в базе, показанная в двух местах.** Не копия:
дублировать её в `log` значило бы держать две записи об одном событии,
которые разойдутся при первой же правке текста. Поэтому обе ленты складывают одни
и те же две таблицы, и разница между ними — в условии отбора.

## Два источника, одна лента

| Источник | Что в нём | Чего нет |
|----------|-----------|----------|
| `log` | ход работы по анкетам: вход, проверка, сообщения | записей без анкеты |
| `scenario_run` | прогоны сценариев: сценарий, исход, шаг, длительность | ничего, кроме прогонов |

Лента собирается **`UNION ALL` в базе**, а не склейкой двух ответов в Python. Иначе
пришлось бы тянуть с запасом из обеих таблиц и резать после сортировки: чтобы
показать пятьдесят свежих строк, надо знать, сколько их взять из каждой, а этого
заранее не знает никто.

⚠ **Прогоны импорта и выгрузки видны только здесь.** У них нет анкеты (`profile_id`
пуст) — они идут по сайту целиком, и в журнал ни одной учётки не попадают. Это и
есть главный повод завести общую ленту: до неё их след не был виден нигде.

## Почему отбор обязателен

Анкеты у разных сайтов живут своей жизнью, и общий список без отбора перемешал бы
работу по одному сайту с работой по другому. Селект сайта на странице всё равно
есть — он общий на весь раздел.

## Текст строки собирается здесь, а не на странице

У прогона в базе лежат части — имя сценария, исход, шаг, длительность, — а не
готовая фраза. Собрать её мог бы и браузер, но лент две, и две копии правил
разошлись бы: журнал анкеты говорил бы одно, общая лента другое об одном и том же
событии.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required

router = APIRouter(prefix='/api/dating/log', tags=['dating'])

# Сколько строк на странице. Пятьдесят — столько прочитывают глазами за один
# заход; остальные не теряются, за ними пейджер.
LOG_PAGE = 50

# Предел, дальше которого не пускаем, даже если попросили. Лента читается
# человеком, а не выгружается: тысяча строк за запрос — это не чтение.
LOG_PAGE_MAX = 200

# Что показываем. `all` — обе ленты вместе, `profile` — только работа по анкетам,
# `scenario` — только прогоны сценариев.
#
# Отбор по источнику, а не по уровню: уровни (`ok`, `fail`) есть у обоих, а вопрос
# «что запускалось у соседа» отвечается именно источником.
LOG_KINDS = ('all', 'profile', 'scenario')


@router.get('', include_in_schema=False)
async def log_list(site_id: int = 0, kind: str = 'all', level: str = '',
                          q: str = '', page: int = 1, per_page: int = LOG_PAGE,
                          user=Depends(auth_required)):
    """Общая лента журнала по сайту, свежие сверху.

    Args:
        site_id: сайт. Обязателен — почему, в шапке модуля.
        kind: `all`, `profile` (журнал анкет) или `scenario` (прогоны сценариев).
        level: `ok`, `fail`, `info`, `in`, `out` — пусто значит «все».
        q: подстрока в тексте строки или в имени анкеты.
        page: страница, с единицы.
        per_page: строк на странице, не больше `LOG_PAGE_MAX`.

    Returns:
        dict: `rows` — строки ленты, `total`, `page`, `pages`.

        В строке: `source` (`profile` или `scenario`), `level`, `text`,
        `created_at`, `profile_id`, `profile_name`, `profile_photo`, и у
        прогонов — `scenario_name`,
        `step_no`, `ms`.

    Raises:
        HTTPException: 422 — не выбран сайт или неизвестный вид ленты.
    """
    if not site_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не выбран сайт')

    if kind not in LOG_KINDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f'неизвестная лента «{kind}»')

    return {'result': await log_feed(site_id=int(site_id), kind=kind,
                                            level=str(level or ''),
                                            q=str(q or '').strip(),
                                            page=page, per_page=per_page)}


async def log_feed(site_id: int = 0, profile_id: int = 0, kind: str = 'all',
                          level: str = '', q: str = '', page: int = 1,
                          per_page: int = LOG_PAGE) -> dict:
    """Лента журнала: работа по анкетам и прогоны сценариев одним списком.

    Args:
        site_id: отбор по сайту — общая лента страницы «Журналы».
        profile_id: отбор по анкете — журнал в правой колонке «Профилей». Задают
            одно из двух; оба сразу сузят ленту до пересечения.
        kind, level, q: вид ленты, уровень, поиск.
        page, per_page: страница и её размер.

    Returns:
        dict: `rows` с готовым текстом, `total`, `page` (исправленный), `pages`.

    Общая **для обеих лент**: прогон лежит в базе одной строкой, и показывать его
    двумя разными способами значило бы разойтись в словах об одном событии.
    """
    per_page = max(1, min(int(per_page or LOG_PAGE), LOG_PAGE_MAX))
    page = max(1, int(page or 1))

    sql, params = _query(int(site_id), int(profile_id), kind, level, q)

    async with mysql_get_db_async() as db:
        await db.execute(f'SELECT COUNT(*) AS n FROM ({sql}) AS лента', params)
        total = int((await db.fetchone() or {}).get('n') or 0)

        # Номер страницы поправляем здесь же и возвращаем исправленный: список
        # мог укоротиться, пока человек листал, и просить у него ещё одно нажатие,
        # чтобы увидеть хоть что-то, незачем. То же делает карточка собеседниц.
        pages = max(1, -(-total // per_page))
        page = min(page, pages)

        # ⚠ Порядок по времени, а не по `id`: номера идут из **двух** таблиц, и
        # соседние по времени записи могут иметь несравнимые номера. Сортировка по
        # `id` перемешала бы ленту так, что этого никто бы не заметил.
        await db.execute(f'{sql} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s',
                         (*params, per_page, (page - 1) * per_page))
        rows = await db.fetchall()

    return {'rows': [_row(dict(row)) for row in rows],
            'total': total, 'page': page, 'pages': pages}


# ── Приватное ────────────────────────────────────────────────────────────────

# Как зовут анкету в ленте. Отдельной колонки с именем у неё нет: имя приходит с
# сайта и лежит в JSON-столбце `profile`. Нет и его — показываем логин: он есть
# всегда, а безымянная строка не говорит, о какой анкете речь.
#
# Выражением, а не подзапросом: оно попадает и в `SELECT`, и в поиск, и две
# разошедшиеся копии дали бы «ищу по одному, показываю другое».
_NAME = ("COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(p.profile, '$.name')), 'null'),"
         " p.login, '')")

# Фотография анкеты — рядом с именем. ⚠⚠ **Имена у девушек повторяются**, и в
# ленте это не мелочь: две Дианы на одном сайте различались только глазами
# читающего, который держал в голове, какая из них какая. Лицо в строке снимает
# вопрос без чтения.
#
# Тем же выражением, что и имя: приходит из карточки сайта, своей колонки нет.
# Пусто — обычный случай (анкету ещё не читали), и страница рисует кружок с
# буквой; прочерка тут быть не должно.
_PHOTO = ("COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(p.profile, '$.photo')), 'null'), '')")

# Как уровень ленты ложится на исход прогона. Таблицей, а не цепочкой `if`: список
# уровней растёт со стороны журнала анкеты, и забытая ветка молча отдаёт всё
# подряд вместо пустоты.
_LEVEL_TO_STATUS = {'ok': 'ok', 'fail': 'error', 'info': 'sent'}


def _row(row: dict) -> dict:
    """Дополняет строку готовым текстом. У журнала анкеты он уже есть."""
    if row.get('source') != 'scenario':
        return row

    row['text'] = _run_text(row)

    return row


def _run_text(row: dict) -> str:
    """Что написано в строке прогона.

    Прогон даёт **две** записи, и читаются они как пара: «отправлен запрос» и
    следом «получен ответ» с длительностью.

    ⚠ Строка «отправлен» без пары — не потерянная запись, а **зависший прогон**:
    ответ не пришёл, и мерить пока нечего. Ради этого случая отметка об отправке и
    заводилась.

    ⚠ Шаг 0 у отказа не значит «встал на первом»: он значит, что сценарий **не
    начинался** — сосед не ответил, вкладка не открывалась. Написать «встал на
    шаге 0» было бы враньём про место отказа.
    """
    name = f"«{row.get('scenario_name') or ''}»"
    level = row.get('level')

    if level == 'info':
        return f'Отправлен запрос на сценарий {name}'

    if level == 'fail':
        where = f"встал на шаге {row['step_no']}" if row.get('step_no') else 'не запустился'

        return f"Сценарий {name} {where}: {row.get('text') or 'причина неизвестна'}"

    return f"Получен ответ на сценарий {name} — {int(row.get('ms') or 0) / 1000:.1f} с"


def _query(site_id: int, profile_id: int, kind: str, level: str, q: str) -> tuple[str, list]:
    """Собирает запрос ленты и его параметры.

    Обе половины дают **одинаковый набор колонок** — иначе `UNION` их не сложит.
    Недостающее подставляется пустым: у строки журнала анкеты нет ни сценария, ни
    длительности, и это не потеря, а разная природа записей.

    Отбор по сайту у половин разный, и это не оплошность: у прогона сайт записан
    прямо в строке, а строка журнала анкеты знает только анкету — сайт достаётся
    через неё. Отбор по анкете, наоборот, одинаков: номер анкеты есть у обеих.
    """
    parts, params = [], []

    # `1 = 1` как основа: без отбора половина отдала бы всё подряд, а пустой
    # `WHERE` — синтаксическая ошибка. Оба вызывающих отбор задают (ручка требует
    # сайт, журнал анкеты передаёт её номер), но полагаться на это в сборке
    # запроса нельзя: третий вызывающий об этом правиле не узнает.
    if kind in ('all', 'profile'):
        where, args = ['1 = 1'], []
        if site_id:
            where.append('p.site_id = %s')
            args.append(site_id)
        if profile_id:
            where.append('l.profile_id = %s')
            args.append(profile_id)
        if level:
            where.append('l.level = %s')
            args.append(level)
        if q:
            where.append(f'(l.text LIKE %s OR {_NAME} LIKE %s OR p.login LIKE %s)')
            args += [f'%{q}%'] * 3

        parts.append(
            "SELECT l.id, 'profile' AS source, l.level, l.text, l.created_at,"
            f'       p.id AS profile_id, {_NAME} AS profile_name,'
            f'       {_PHOTO} AS profile_photo,'
            "       '' AS scenario_name, 0 AS step_no, 0 AS ms"
            '  FROM log l'
            '  JOIN profile p ON p.id = l.profile_id'
            f' WHERE {" AND ".join(where)}')
        params += args

    if kind in ('all', 'scenario'):
        where, args = ['1 = 1'], []
        if site_id:
            where.append('r.site_id = %s')
            args.append(site_id)
        if profile_id:
            where.append('r.profile_id = %s')
            args.append(profile_id)
        # У прогона нет колонки `level`: есть исход. Сводим к тем же уровням, что
        # у журнала анкеты, — иначе фильтр «только отказы» работал бы на одной
        # половине ленты и молчал на другой.
        #
        # `sent` («запрос ушёл, ответа ещё нет») ложится в `info`: это ход работы,
        # а не её итог.
        if level in _LEVEL_TO_STATUS:
            where.append('r.status = %s')
            args.append(_LEVEL_TO_STATUS[level])
        elif level:
            # Уровни `in` и `out` — сообщения чата, они бывают только у журнала
            # анкеты. Половина ленты должна остаться пустой.
            where.append('1 = 0')
        if q:
            where.append('(r.scenario_name LIKE %s OR r.error LIKE %s'
                         f' OR {_NAME} LIKE %s OR p.login LIKE %s)')
            args += [f'%{q}%'] * 4

        parts.append(
            "SELECT r.id, 'scenario' AS source,"
            "       CASE r.status WHEN 'ok' THEN 'ok'"
            "                     WHEN 'sent' THEN 'info' ELSE 'fail' END AS level,"
            "       COALESCE(r.error, '') AS text, r.created_at,"
            f'       r.profile_id, {_NAME} AS profile_name,'
            f'       {_PHOTO} AS profile_photo,'
            '       r.scenario_name, r.step_no, r.ms'
            '  FROM scenario_run r'
            '  LEFT JOIN profile p ON p.id = r.profile_id'
            f' WHERE {" AND ".join(where)}')
        params += args

    return ' UNION ALL '.join(parts), params
