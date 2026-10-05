"""Ручки отказов: страница «Ошибки» и лента отказов для «Журналов».

Страница отвечает на вопрос «что сломано прямо сейчас» — отказы, которые не
привязаны к анкете: не обновился список сайтов, не ответил сосед, встала фоновая
сверка. Журнал анкеты на такое не годится: работа не её. Хранение и правила —
`src/dating/error.py`.

Вкладка «Ошибки» на «Журналах» отвечает на другой вопрос: **«что ломалось на проекте
вообще»**. Отказы приходят из четырёх мест — журнал анкеты, прогоны сценариев,
походы наружу, таблица общих отказов, — и до вкладки каждый читался на своей
странице. Лента складывает их `UNION ALL` в базе, как это уже делает общая лента
(`log.py`): чтобы показать пятьдесят свежих отказов, надо знать, сколько взять из
каждой таблицы, а этого заранее не знает никто.

Открыто всем вошедшим: оператор первым замечает, что панель показывает не то, и
отправлять его к администратору за объяснением значило бы терять время дважды.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.dating.error import ERROR_LIMIT, error_rows, error_title
from src.uvicorn.routers.dating.log import _NAME, _PHOTO, _run_text, feed_page

router = APIRouter(prefix='/api/dating/error', tags=['dating'])


@router.get('', include_in_schema=False)
async def error_list(site_id: int = 0, limit: int = ERROR_LIMIT,
                            user=Depends(auth_required)):
    """Что сломано сейчас — свежее сверху.

    Args:
        site_id: показать отказы этого сайта. Ноль — все.
        limit: сколько строк отдать.

    Returns:
        dict: `errors` — записи `source`, `title`, `site_id`, `message`, `hits`,
        `first_at`, `last_at`.

    ⚠ **Отбор по сайту не прячет общие отказы** (`site_id = 0`): «список сайтов не
    обновляется» не относится ни к одному сайту в отдельности, но касается любого —
    и объясняет ровно то, что человек видит на странице.

    Время — в UTC, как всё в проекте: сдвигает страница по настройке `timezone`.
    """
    return {'result': {'errors': await error_rows(int(site_id or 0),
                                                         min(int(limit or 0), ERROR_LIMIT))}}


# Сколько строк ленты на странице. Как у общей ленты: лента читается человеком,
# а не выгружается.
ERROR_FEED_PAGE = 50
ERROR_FEED_PAGE_MAX = 200

# Откуда приходят отказы. Ключи — те же слова, что ложатся в строку `source`;
# подписи селекта страница берёт отсюда, своей копии не держит (как `doors`
# у «Запросов»).
ERROR_FEED_SOURCES = {
    'profile': 'журнал анкет',
    'scenario': 'прогон сценария',
    'request': 'запрос наружу',
    'system': 'не про анкеты',
}


@router.get('/feed', include_in_schema=False)
async def error_feed(site_id: int = 0, source: str = '', q: str = '', page: int = 1,
                            per_page: int = ERROR_FEED_PAGE, user=Depends(auth_required)):
    """Все отказы проекта одним списком — вкладка «Ошибки» на «Журналах».

    Args:
        site_id: сайт. Обязателен — как у общей ленты.
        source: откуда брать отказы (`profile`, `scenario`, `request`, `system`).
            Пусто — откуда угодно.
        q: подстрока в тексте отказа или в имени анкеты.
        page, per_page: страница и её размер.

    Returns:
        dict: `rows` — строки ленты с готовым текстом, `total`, `page`, `pages`,
        `sources` — как звать каждый источник для человека.

        В строке: `source`, `text`, `created_at` (последняя неудача), `first_at`
        (когда началось), `hits` (сколько раз подряд; у истории — 0), `profile_id`,
        `profile_name`, `profile_photo`.

    ⚠ **Отказ здесь — событие, а не состояние.** Строки `error`-таблицы живут до
    починки и показывают «сломано сейчас» (страница «Ошибки»); здесь они стоят
    рядом с историей отказов анкет и прогонов, и у них тот же формат строки.

    Raises:
        HTTPException: 422 — не выбран сайт или неизвестный источник.
    """
    if not site_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не выбран сайт')

    if source and source not in ERROR_FEED_SOURCES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f'неизвестный источник «{source}»')

    per_page = max(1, min(int(per_page or ERROR_FEED_PAGE), ERROR_FEED_PAGE_MAX))

    async with mysql_get_db_async() as db:
        feed = await feed_page(db, _feed_query(int(site_id), str(source or ''),
                                               str(q or '').strip()),
                               page, per_page)

    return {'result': {'rows': [_feed_row(row) for row in feed['rows']],
                      'total': feed['total'], 'page': feed['page'],
                      'pages': feed['pages'], 'sources': ERROR_FEED_SOURCES}}


# ── Приватное (лента) ────────────────────────────────────────────────────────

# Имя и фото анкеты читаются теми же выражениями, что у общей ленты: одна копия
# правил на обе ленты (`log.py`, `_NAME`/`_PHOTO`).


def _feed_row(row: dict) -> dict:
    """Дополняет строку готовым текстом. Собирает сервер, а не страница.

    Прогон говорит частями — имя сценария, шаг, причина, — и собирает их `_run_text`
    из `log.py`: третья копия правил о прогоне разошлась бы с двумя первыми на
    первой же правке. Общий отказ зовёт ту же `error_title`, что и страница
    «Ошибки»: человек читает «Список сайтов: сосед не ответил», а не `sites_sync`.

    ⚠⚠ Именно `error_title`, а **не** `ERROR_TITLES.get`. Словарь отвечает лишь
    на предметные источники, и сбой процесса в него не попадает: его `source`
    несёт отпечаток (`crash:uvicorn:e39650de`), то есть всякий раз новый ключ.
    Пока здесь стоял словарь, страница показывала «Сбой сервера», а эта же
    строка в ленте — сырой ключ с отпечатком.
    """
    if row['source'] == 'scenario':
        row['text'] = _run_text({**row, 'level': 'fail'})

    if row['source'] == 'system':
        title = error_title(row['scenario_name'])
        row['text'] = f'{title}: {row["text"] or "причина неизвестна"}'

    return row


def _feed_query(site_id: int, source: str, q: str) -> list[tuple[str, list]]:
    """Собирает половины ленты отказов: пар `(запрос, параметры)`, как в общей
    ленте (`feed_page`).

    Половины дают **одинаковый набор колонок** — иначе `UNION` их не сложит.
    Чего-то половине неоткуда взять (у общих отказов нет анкеты, у журнала анкеты
    нет счётчика повторов) — подставляется пустым или нулём: это разная природа
    записей, а не потеря.

    Отбор по сайту у половин разный, и это не оплошность — как в общей ленте:
    строка журнала анкеты знает только анкету, прогоны и походы носят сайт в
    строке, а общие отказы не относятся ни к одному сайту и остаются видны
    при любом выборе.
    """
    parts: list[tuple[str, list]] = []

    if source in ('', 'profile'):
        where = ['l.level = \'fail\'', 'p.site_id = %s']
        args = [site_id]
        if q:
            where.append(f'(l.text LIKE %s OR {_NAME} LIKE %s OR p.login LIKE %s)')
            args += [f'%{q}%'] * 3
        parts.append((
            'SELECT l.id, \'profile\' AS source, l.text, l.created_at,'
            '       l.created_at AS first_at, 0 AS hits,'
            '       p.id AS profile_id,'
            f'       {_NAME} AS profile_name, {_PHOTO} AS profile_photo,'
            "       '' AS scenario_name, 0 AS step_no"
            '  FROM log l'
            '  JOIN profile p ON p.id = l.profile_id'
            f' WHERE {" AND ".join(where)}', args))

    if source in ('', 'scenario'):
        where = ["r.status = 'error'", 'r.site_id = %s']
        args = [site_id]
        if q:
            where.append(f"(r.scenario_name LIKE %s OR r.error LIKE %s"
                         f" OR {_NAME} LIKE %s OR p.login LIKE %s)")
            args += [f'%{q}%'] * 4
        parts.append((
            "SELECT r.id, 'scenario' AS source, COALESCE(r.error, '') AS text,"
            '       r.created_at, r.created_at AS first_at, 0 AS hits,'
            '       r.profile_id,'
            f'       {_NAME} AS profile_name, {_PHOTO} AS profile_photo,'
            '       r.scenario_name, r.step_no'
            '  FROM scenario_run r'
            '  LEFT JOIN profile p ON p.id = r.profile_id'
            f' WHERE {" AND ".join(where)}', args))

    if source in ('', 'request'):
        # ⚠ Походы без сайта (к соседу) показываем вместе с выбранным: они не про
        # сайт, и прятались бы от вкладки вовсе (`request.py`).
        where = ['r.ok = 0', '(r.site_id = %s OR r.site_id = 0)']
        args = [site_id]
        if q:
            where.append('(r.error LIKE %s OR r.path LIKE %s)')
            args += [f'%{q}%'] * 2
        parts.append((
            # ⚠ Число в CONCAT идёт без `CAST(... AS CHAR)`: каст вешает на кусок
            # сличение соединения с тем же весом, что у столбца, — и `UNION` падает
            # с 1271 на базах, где сличение соединения и таблиц различаются.
            "SELECT r.id, 'request' AS source,"
            "       CONCAT_WS(': ', CONCAT(r.method, ' ', r.path,"
            "                           ' — ', r.status), r.error) AS text,"
            '       r.created_at, r.created_at AS first_at, 0 AS hits,'
            '       r.profile_id,'
            f'       {_NAME} AS profile_name, {_PHOTO} AS profile_photo,'
            "       r.door AS scenario_name, 0 AS step_no"
            '  FROM request r'
            '  LEFT JOIN profile p ON p.id = r.profile_id'
            f' WHERE {" AND ".join(where)}', args))

    if source in ('', 'system'):
        # Человек читает «Список сайтов: сосед не ответил», а не `sites_sync` —
        # заголовок подставляется тем же словарём, что и на странице «Ошибки».
        # ⚠ Общие отказы (`site_id = 0`) видны при любом выбранном сайте: они
        # объясняют ровно то, что человек видит на странице (шапка модуля).
        where = ['site_id IN (%s, 0)']
        args: list = [site_id]
        if q:
            where.append('(message LIKE %s OR source LIKE %s)')
            args += [f'%{q}%'] * 2
        parts.append((
            "SELECT id, 'system' AS source, message AS text, last_at AS created_at,"
            '       first_at, hits,'
            "       0 AS profile_id, '' AS profile_name,"
            "       '' AS profile_photo,"
            "       source AS scenario_name, 0 AS step_no"
            '  FROM error'
            f' WHERE {" AND ".join(where)}', args))

    return parts
