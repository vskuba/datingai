"""Чтение для страниц панели: переписка, журнал, карточка анкеты.

Третий файл ручек анкеты, и признак у него тот же, что развёл первые два: **эти
ручки только читают свою базу**. Ни браузера, ни соседа, ни записи — кроме одной
пометки «прочитано», которая и есть часть чтения.

Отсюда их свойства: отвечают за миллисекунды, не умеют падать с 502 и вызываются
чаще всех остальных вместе взятых — страницы панели ходят сюда своим кругом
(«Анкеты» раз в 15 секунд, «Профили» раз в 10). Поэтому цена запроса здесь важнее,
чем где-либо ещё: карточка ходит в базу за каждой показанной собеседницей, и
именно ради неё держится составной индекс `idx_dating_message_dialog`
(`database.md`, §5).

Разделение — `dating_profile.py`, шапка.
"""

from fastapi import APIRouter, Depends

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.dating.dating_json import dating_json_load
from src.uvicorn.routers.dating.dating_log import dating_log_feed
from src.uvicorn.routers.dating.dating_profile import dating_profile_row

router = APIRouter(prefix='/api/dating/profile', tags=['dating'])

# Сколько диалогов на странице карточки. Больше пятидесяти за раз никто не
# просматривает, а страница на них заметно тяжелеет: у каждого ещё и последние
# десять реплик отдельным запросом. Остальные не теряются — за ними пейджер.
DATING_PROFILE_VIEW_CONTACTS_PAGE = 50


@router.get('/{profile_id}/message', include_in_schema=False)
async def dating_profile_view_messages(profile_id: int, limit: int = 30,
                               user=Depends(auth_required)):
    """Сообщения чата этой учётки, свежие сверху."""
    await dating_profile_row(profile_id)
    limit = max(1, min(int(limit or 30), 200))

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, sender_uid, sender_name, text, is_read, created_at '
            f'FROM dating_message WHERE profile_id = %s ORDER BY id DESC LIMIT {limit}', (profile_id,))
        rows = await db.fetchall()

    return {'result': rows}


@router.post('/{profile_id}/read', include_in_schema=False)
async def dating_profile_view_read(profile_id: int,
                           user=Depends(auth_required)):
    """Помечает сообщения учётки прочитанными — человек открыл её карточку.

    Право на просмотр, а не на правку: пометка «я это видел» — часть чтения, и
    требовать за неё право создавать значило бы запретить смотреть без него.
    """
    await dating_profile_row(profile_id)

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE dating_message SET is_read = 1 "
                         " WHERE profile_id = %s AND direction = 'in' AND is_read = 0", (profile_id,))
        read = db.rowcount
        await db.connection.commit()

    return {'result': {'read': read}}


@router.get('/{profile_id}/log', include_in_schema=False)
async def dating_profile_view_log(profile_id: int, limit: int = 100,
                          user=Depends(auth_required)):
    """Журнал по учётке, свежие сверху.

    Собирается тем же сборщиком, что и общая лента «Журналов»
    (`dating_log_feed`), только отбор здесь по анкете, а не по сайту. Отсюда
    главное: **прогоны сценариев видны и тут**.

    ⚠ Прогон — одна строка в базе, показанная в двух местах, а не копия.
    Дублировать её в `dating_log` значило бы держать две записи об одном событии,
    которые разойдутся при первой же правке текста.
    """
    await dating_profile_row(profile_id)

    feed = await dating_log_feed(profile_id=profile_id,
                                 per_page=max(1, min(int(limit or 100), 500)))

    # Плоским списком, а не с пейджером: правая колонка показывает последние
    # строки без листания, и менять её контракт ради общего сборщика незачем.
    return {'result': feed['rows']}


@router.get('/{profile_id}/card', include_in_schema=False)
async def dating_profile_view_card(profile_id: int, page: int = 1,
                             user=Depends(auth_required)):
    """Профиль учётки и страница её собеседниц: сначала те, кому не ответили.

    Сообщений на собеседницу берём десять и обеими сторонами: диалог читают как
    разговор, а не как список чужих реплик. Отдаём в порядке разговора — сверху
    старое, снизу свежее, как в самом чате.

    `needs_answer` — последнее слово осталось за собеседником. Это и есть работа,
    которую видно в карточке: не «сколько всего сообщений», а «кому не ответили».

    Признак считается **в запросе**, а не после выборки, и это не украшательство.
    Считай его в Python по уже отобранным пятидесяти строкам — собеседница с
    давним неотвеченным сообщением наверх не поднимется, потому что в выборку не
    попадёт вовсе: потолок отрежет её по свежести. А это ровно тот случай, ради
    которого сортировку и просили.

    Страница **серверная**, в отличие от списка анкет (тот приходит целиком и
    режется на странице). Причина в запросе за репликами: он идёт на каждую
    показанную собеседницу отдельно, и «отдать все четыре сотни, а резать в
    браузере» означало бы четыреста запросов на одно открытие карточки.
    """
    profile = await dating_profile_row(profile_id)

    # Список ведём **от переписки**, а не от таблицы собеседников. Раньше он шёл
    # от `dating_contact`, куда попадают только те, кого отдал сайт своим списком
    # «кто онлайн» (двести штук), — и диалог с человеком, которого в том списке
    # нет, в панель не попадал вовсе. Счётчик же считался по переписке, поэтому
    # цифры расходились втрое: 549 в счётчике против 59 показанных.
    async with mysql_get_db_async() as db:
        # Считаем группировкой, а не `COUNT(DISTINCT sender_uid)`: число то же, но
        # группировка идёт по индексу подряд, а `COUNT(DISTINCT)` держит своё дерево
        # уникальных значений — на 22 тысячах реплик это 9 мс против 39.
        await db.execute('SELECT COUNT(*) AS total FROM '
                         '  (SELECT 1 FROM dating_message WHERE profile_id = %s '
                         '    GROUP BY sender_uid) d', (profile_id,))
        total = int(((await db.fetchone()) or {}).get('total') or 0)

        # Страница могла уехать за конец: собеседницы никуда не денутся, но номер
        # приходит со страницы, которая рисовалась ещё до пересчёта. Возвращаем
        # исправленный номер, чтобы браузеру было с чем сверить пейджер.
        pages = max(1, -(-total // DATING_PROFILE_VIEW_CONTACTS_PAGE))
        page = max(1, min(int(page or 1), pages))

        # Порядок целиком на стороне базы: сначала неотвеченные, внутри каждой
        # группы — по свежести последней реплики. Свежесть считаем по `MAX(id)`, а
        # не по времени: `id` автоинкрементный, и порядок вставки не спутается
        # часовыми поясами сайта.
        #
        # Направление и пометку последней реплики берём отдельным присоединением по
        # первичному ключу, а не `MAX()`-ами внутри группировки: агрегат по
        # `profile_id, sender_uid, id` целиком покрывается индексом, и добавь мы туда
        # хоть одно поле из строки — база пойдёт читать все двадцать две тысячи
        # строк вместо индекса (замер: 49 мс против 12).
        await db.execute(
            'SELECT d.uid, d.messages, d.last_id, '
            '       COALESCE(c.name, %s) AS name, '
            '       c.photo, c.age, c.city, c.is_online, '
            "       (last.direction = 'in' AND last.is_answered = 0) AS needs_answer "
            '  FROM (SELECT sender_uid AS uid, COUNT(*) AS messages, MAX(id) AS last_id '
            '          FROM dating_message WHERE profile_id = %s GROUP BY sender_uid) d '
            '  JOIN dating_message last ON last.id = d.last_id '
            '  LEFT JOIN dating_contact c ON c.profile_id = %s AND c.uid = d.uid '
            ' ORDER BY needs_answer DESC, d.last_id DESC '
            ' LIMIT %s OFFSET %s',
            ('', profile_id, profile_id, DATING_PROFILE_VIEW_CONTACTS_PAGE,
             (page - 1) * DATING_PROFILE_VIEW_CONTACTS_PAGE))
        contacts = [dict(row) for row in (await db.fetchall() or [])]

        for contact in contacts:
            # База отдаёт признак числом — странице и тестам нужен признак, а не
            # единица: в ответе `needs_answer` всегда был логическим.
            contact['needs_answer'] = bool(contact['needs_answer'])

            await db.execute(
                'SELECT text, direction, is_answered, created_at FROM dating_message '
                ' WHERE profile_id = %s AND sender_uid = %s ORDER BY id DESC LIMIT 10',
                (profile_id, contact['uid']))
            last = [dict(row) for row in (await db.fetchall() or [])]
            # Разворачиваем: выбирали свежие, а читать надо сверху вниз.
            contact['messages_last'] = list(reversed(last))

            # Имя из переписки — только тем, кого нет в `dating_contact`: сайт
            # отдаёт список собеседниц не целиком, а подпись строке нужна всё
            # равно. Спрашиваем на показанных, а не на всех: незаполненных единицы,
            # и лишний запрос дешевле, чем чтение строк в общей группировке.
            if not contact['name']:
                await db.execute(
                    'SELECT MAX(sender_name) AS name FROM dating_message '
                    ' WHERE profile_id = %s AND sender_uid = %s AND sender_name <> %s',
                    (profile_id, contact['uid'], ''))
                contact['name'] = ((await db.fetchone()) or {}).get('name') or ''

    return {'result': {'profile': dating_json_load(profile.get('profile')),
                       'login': profile['login'], 'contacts': contacts,
                       'contacts_total': total, 'page': page, 'pages': pages}}


