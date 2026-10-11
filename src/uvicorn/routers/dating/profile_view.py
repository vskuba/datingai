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

Разделение — `profile_.py`, шапка.
"""

from fastapi import APIRouter, Depends

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.dating.chat_reply import chat_reply_pending
from src.dating.message_twin import message_twin_skip
from src.dating.json_column import json_column_load
from src.dating.online import online_check
from src.dating.profile_card import profile_card_edited
from src.dating.profile_link import profile_link
from src.dating.schedule import schedule_load, schedule_why_idle
from src.uvicorn.routers.dating.log import log_feed
from src.uvicorn.routers.dating.profile_ import profile_row

router = APIRouter(prefix='/api/dating/profile', tags=['dating'])

# Сколько диалогов на странице карточки. Больше пятидесяти за раз никто не
# просматривает, а страница на них заметно тяжелеет: у каждого ещё и последние
# десять реплик отдельным запросом. Остальные не теряются — за ними пейджер.
PROFILE_VIEW_CONTACTS_PAGE = 50


@router.get('/{profile_id}/message', include_in_schema=False)
async def profile_view_messages(profile_id: int, uid: str = '', limit: int = 30,
                               user=Depends(auth_required)):
    """Сообщения чата этой учётки, свежие сверху.

    Args:
        uid: собеседник. Пусто — вся переписка учётки; с ним — один диалог, которым
            и открывается окно переписки на странице.
        limit: сколько реплик отдать, не больше 200.

    `direction` в ответе есть намеренно: без него нельзя сказать, кто написал, а
    окно переписки — это в первую очередь ответ на вопрос «кто кому».

    Порядок — по времени, а не по `id`: импорт переписки из панели агентства кладёт
    историю за неделю разом, и порядок вставки перестал быть порядком событий
    (подробности — у карточки ниже).
    """
    await profile_row(profile_id)
    limit = max(1, min(int(limit or 30), 200))

    where, params = '', [profile_id]
    if str(uid or '').strip():
        where = ' AND sender_uid = %s'
        params.append(str(uid).strip())

    async with mysql_get_db_async() as db:
        await db.execute(
            # `answer_silent_at` — чтобы окно сказало «агент решил не отвечать»:
            # иначе тишина после чужой реплики читается как поломка.
            'SELECT id, sender_uid, sender_name, text, direction, is_read, '
            '       answer_silent_at, created_at '
            f'FROM message WHERE profile_id = %s{where}'
            # ⚠ Наше исходящее лежит в таблице дважды: своей строкой от
            # отправки и строкой из панели агентства. Ключи у них разные
            # (ULID против `panel:NNN`), и `INSERT IGNORE` импорта их не
            # сводит — прячем второго при чтении (`message_twin.py`).
            f'{message_twin_skip()} '
            f'ORDER BY created_at DESC, id DESC LIMIT {limit}', tuple(params))
        rows = await db.fetchall()

    rows = [dict(row) for row in (rows or [])]

    # ⚠⚠ **Ждущий черновик — в конце, и только при открытом диалоге.**
    #
    # Ответ уже написан, но собеседника нет в сети, и круг держит текст до его
    # возвращения. Не показать его значит показать пустоту там, где идёт работа —
    # ровно тот вопрос, с которого черновики и начались: «почему ответа не видно?».
    #
    # ⚠ **Время ему не ставим, и место — конец.** Поставь мы `created_at`, строка
    # встала бы **между** репликами и читалась бы как отправленная: будто ответ
    # ушёл, а собеседник продолжил, не заметив. У писем на этом уже обожглись
    # (`mail_reply_thread`), и правило здесь то же — разговор это сказанное, а
    # черновика никто не слышал.
    #
    # ⚠ Без `uid` — не показываем: там вся переписка учётки вперемешку, и место
    # «в конце» ничего не значит.
    if str(uid or '').strip():
        # Список уходит свежими сверху, поэтому ждущее кладём **в начало**:
        # страница переворачивает его перед показом.
        rows[:0] = [{'id': 0, 'sender_uid': str(uid).strip(), 'sender_name': '',
                     'text': str(one['body']), 'direction': 'out', 'is_read': 1,
                     'answer_silent_at': None, 'created_at': None,
                     'pending': True, 'error': str(one.get('error') or '')}
                    for one in await chat_reply_pending(int(profile_id), str(uid).strip())]

    return {'result': rows}


@router.get('/{profile_id}/can-send/{uid}', include_in_schema=False)
async def profile_view_can_send(profile_id: int, uid: str,
                                       user=Depends(auth_required)):
    """Можно ли сейчас написать этому собеседнику.

    Returns:
        dict: `can` — можно ли; `why` — почему нельзя, словами для страницы;
        `known` — знаем ли мы это наверняка; `age_sec` — возраст снимка «кто в
        сети».

    ⚠ **Спрашивает страница перед показом формы, но решает не она.** Тот же запрет
    стоит в самой отправке (`send.py`): страницу можно открыть в двух
    вкладках, оставить на час, дёрнуть ручку из консоли — и запрет, живущий только
    в разметке, ни от чего не защищает. Здесь он ради того, чтобы человек **видел**
    причину, а не жал кнопку в пустоту.
    """
    profile = await profile_row(profile_id)
    state = online_check(int(profile.get('site_id') or 0), uid)

    return {'result': {'can': bool(state['online']),
                       'known': bool(state['known']),
                       'age_sec': int(state['age_sec']),
                       'why': '' if state['online']
                              else 'собеседника сейчас нет в сети'}}


@router.post('/{profile_id}/read', include_in_schema=False)
async def profile_view_read(profile_id: int,
                           user=Depends(auth_required)):
    """Помечает сообщения учётки прочитанными — человек открыл её карточку.

    Право на просмотр, а не на правку: пометка «я это видел» — часть чтения, и
    требовать за неё право создавать значило бы запретить смотреть без него.
    """
    await profile_row(profile_id)

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE message SET is_read = 1 "
                         " WHERE profile_id = %s AND direction = 'in' AND is_read = 0", (profile_id,))
        read = db.rowcount
        await db.connection.commit()

    return {'result': {'read': read}}


@router.get('/{profile_id}/log', include_in_schema=False)
async def profile_view_log(profile_id: int, limit: int = 100,
                          user=Depends(auth_required)):
    """Журнал по учётке, свежие сверху.

    Собирается тем же сборщиком, что и общая лента «Журналов»
    (`log_feed`), только отбор здесь по анкете, а не по сайту. Отсюда
    главное: **прогоны сценариев видны и тут**.

    ⚠ Прогон — одна строка в базе, показанная в двух местах, а не копия.
    Дублировать её в `log` значило бы держать две записи об одном событии,
    которые разойдутся при первой же правке текста.
    """
    await profile_row(profile_id)

    feed = await log_feed(profile_id=profile_id,
                                 per_page=max(1, min(int(limit or 100), 500)))

    # Плоским списком, а не с пейджером: правая колонка показывает последние
    # строки без листания, и менять её контракт ради общего сборщика незачем.
    return {'result': feed['rows']}


@router.get('/{profile_id}/card', include_in_schema=False)
async def profile_view_card(profile_id: int, page: int = 1,
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
    profile = await profile_row(profile_id)

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
                         '  (SELECT 1 FROM message WHERE profile_id = %s '
                         '    GROUP BY sender_uid) d', (profile_id,))
        total = int(((await db.fetchone()) or {}).get('total') or 0)

        # Страница могла уехать за конец: собеседницы никуда не денутся, но номер
        # приходит со страницы, которая рисовалась ещё до пересчёта. Возвращаем
        # исправленный номер, чтобы браузеру было с чем сверить пейджер.
        pages = max(1, -(-total // PROFILE_VIEW_CONTACTS_PAGE))
        page = max(1, min(int(page or 1), pages))

        # Порядок целиком на стороне базы: сначала неотвеченные, внутри каждой
        # группы — по свежести последней реплики.
        #
        # ⚠ **Свежесть считается по времени, а не по `MAX(id)`.** Раньше по `id`:
        # автоинкремент совпадал с порядком событий, пока писал один сокет. Импорт
        # переписки из панели агентства приносит **историю за неделю** и кладёт её
        # сегодня, то есть после всего, что сокет успел поймать: в диалоге с обоими
        # источниками наибольший `id` оказывался у недельной давности реплики.
        # Диалоги сортировались наоборот, а красный значок «нужен ответ» загорался
        # по старому сообщению.
        #
        # Сортировать пачку при вставке недостаточно: внутри пачки порядок
        # поправить можно, но сама пачка ложится позже свежих сообщений сокета.
        #
        # Цена измерена (21 762 реплики, 400 диалогов): `MAX(id)` — 9 мс и неверно;
        # `ROW_NUMBER()` по времени — 121 мс; `MAX(created_at)` с индексом
        # `idx_dating_message_time` — **29 мс** и верно. Оконная функция не
        # выигрывает и от индекса: MySQL всё равно сортирует (118 мс).
        #
        # Вложенный `MAX(m.id)` в присоединении разнимает реплики, пришедшиеся на
        # одну секунду: у панели время с точностью до секунды, и без него
        # «последняя» из двух выбиралась бы случайно — строка мигала бы между ними
        # при каждой перерисовке.
        #
        # Направление и пометку последней реплики берём отдельным присоединением, а
        # не `MAX()`-ами внутри группировки: агрегат по индексу диалога им и
        # покрывается, а добавь мы туда хоть одно поле из строки — база пойдёт
        # читать все двадцать две тысячи строк вместо индекса (замер: 49 мс
        # против 12).
        await db.execute(
            'SELECT d.uid, d.messages, last.id AS last_id, '
            '       COALESCE(c.name, %s) AS name, '
            '       c.photo, c.age, c.city, c.is_online, '
            # ⚠ Молчание агента закрывает повод так же, как отправленный ответ.
            # Пустой ответ LLM — это решение «сейчас отвечать не надо», и значок,
            # мигающий после него, требует работы, которой не будет: круг про это
            # сообщение больше не спросит (`dating.md`, §5.9.3).
            "       (last.direction = 'in' AND last.is_answered = 0 "
            "        AND last.answer_silent_at IS NULL) AS needs_answer "
            # ⚠⚠ **Отсев близнецов нужен и счётчику, а не только показу.**
            #
            # Окно переписки прячет отражение реплики из панели (строка 76), а
            # `COUNT(*)` считал всё подряд — и число на карточке расходилось с
            # тем, что человек насчитает глазами. На проде у пары Diana ↔ Bruce
            # значок говорил 61 при шестидесяти видимых репликах, и разойтись они
            # могут на сколько угодно: близнецы копятся, пока идёт импорт.
            #
            # ⚠ Сторож `TestBothPagesUseIt` этого не ловил: он смотрит, есть ли
            # `message_twin_skip` **в модуле**, а здесь два запроса, и правило
            # стояло в одном из них.
            '  FROM (SELECT sender_uid AS uid, COUNT(*) AS messages, '
            '               MAX(created_at) AS last_at '
            '          FROM message WHERE profile_id = %s'
            # ⚠ Отсев внутри группировки задевает и `last_at`, и это к лучшему:
            # «последняя реплика» перестаёт попадать на спрятанного близнеца, то
            # есть строка списка и окно показывают одно и то же.
            + message_twin_skip() +
            '        GROUP BY sender_uid) d '
            '  JOIN message last '
            '    ON last.profile_id = %s AND last.sender_uid = d.uid '
            '   AND last.id = (SELECT MAX(m.id) FROM message m '
            '                   WHERE m.profile_id = %s AND m.sender_uid = d.uid '
            '                     AND m.created_at = d.last_at'
            # ⚠⚠ **И здесь тоже, иначе отсев в группировке ничего не даёт.**
            # Время у близнеца и у нашей строки совпадает секунда в секунду
            # сплошь и рядом, а `MAX(id)` выбирает из них больший — то есть
            # привезённый из панели позже. Строка списка указывала бы на
            # реплику, которой в окне нет.
            + message_twin_skip('m') + ') '
            '  LEFT JOIN contact c ON c.profile_id = %s AND c.uid = d.uid '
            ' ORDER BY needs_answer DESC, d.last_at DESC, last.id DESC '
            ' LIMIT %s OFFSET %s',
            ('', profile_id, profile_id, profile_id, profile_id,
             PROFILE_VIEW_CONTACTS_PAGE,
             (page - 1) * PROFILE_VIEW_CONTACTS_PAGE))
        contacts = [dict(row) for row in (await db.fetchall() or [])]

        for contact in contacts:
            # База отдаёт признак числом — странице и тестам нужен признак, а не
            # единица: в ответе `needs_answer` всегда был логическим.
            contact['needs_answer'] = bool(contact['needs_answer'])

            await db.execute(
                # По времени, а не по `id`: см. оговорку выше.
                'SELECT text, direction, is_answered, answer_silent_at, created_at '
                '  FROM message '
                ' WHERE profile_id = %s AND sender_uid = %s '
                ' ORDER BY created_at DESC, id DESC LIMIT 10',
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
                    'SELECT MAX(sender_name) AS name FROM message '
                    ' WHERE profile_id = %s AND sender_uid = %s AND sender_name <> %s',
                    (profile_id, contact['uid'], ''))
                contact['name'] = ((await db.fetchone()) or {}).get('name') or ''

    # Адрес анкеты на самом сайте: шаблон сайта плюс её номер.
    #
    # ⚠ **Собирает сервер, а не страница.** Шаблон — настройка сайта, и знай её
    # страница, тот же адрес собирался бы в двух местах: разойдись они в мелочи
    # (кодирование номера, проверка схемы), одна из ссылок вела бы не туда.
    # Пустая строка — обычный исход: у сайта нет шаблона или у анкеты нет номера.
    card = json_column_load(profile.get('profile'))
    # Какие поля человек правил руками — окну правки: у них стоит отметка
    # «правлено руками · вернуть сайту».
    #
    # ⚠ Поле отдаётся **всегда**, пустым списком в обычном случае: `undefined` на
    # странице читался бы как «отметок нет» случайно, а не по правилу. Та же
    # грабля, что уже была с `age` и `proxy_ip` — `response_model` выбрасывал их
    # молча, и страница получала `undefined`.
    edited = profile_card_edited(profile.get('profile_edited'))
    site_url = await profile_link(int(profile.get('site_id') or 0),
                                         str(card.get('id') or ''))

    # `enabled` и `auto_answer` едут в карточку, потому что оба рисуются на ней
    # чекбоксами — на «Анкетах» справа и в окне переписки. Без них страница знала бы
    # состояние только из списка, а карточку открывают и по прямой ссылке.
    return {'result': {'profile': card, 'edited': edited, 'site_url': site_url,
                       'login': profile['login'], 'contacts': contacts,
                       'enabled': bool(profile.get('enabled', 1)),
                       # Расписание — в настройках анкеты на «Анкетах», рядом с
                       # выключателем: оба про то, как её ведут.
                       'schedule': schedule_load(profile.get('schedule')),
                       'schedule_on': bool(profile.get('schedule_on')),
                       # Откуда пароль — блоку «Пароль» на странице. ⚠ Сам пароль
                       # не отдаём никогда: только признак, что он задан.
                       'password_source': str(profile.get('password_source') or 'site'),
                       'has_password': bool(str(profile.get('password') or '')),
                       # Почему расписание не действует — словами. Считает сервер:
                       # правило одно («только у включённой»), и повторять его на
                       # странице значило бы два места, которые разойдутся.
                       'schedule_idle': schedule_why_idle(profile),
                       'auto_answer': bool(profile.get('auto_answer')),
                       # Рассылка писем — третья галочка блока «Настройки анкеты».
                       # ⚠ Исполнителя у флага пока нет: карточка отдаёт его,
                       # чтобы галочка после перезагрузки страницы показывала то,
                       # что человек поставил, а не снятую.
                       'mailing_on': bool(profile.get('mailing_on')),
                       # Генерация ответов на входящие. ⚠ **Не `schedule_on`**: тот
                       # включает вход по сменам, этот — только подготовку ответов в
                       # пул. Оба видны в карточке, но живут на разных страницах.
                       'reply_on': bool(profile.get('reply_on')),
                       # Отправка фотографий в письмах — галочка там же, в
                       # «Настройках анкеты».
                       #
                       # ⚠ Исполнителя у флага пока нет, и карточка отдаёт его по
                       # той же причине, что и `mailing_on`: чтобы поставленная
                       # галочка пережила перезагрузку страницы. Иначе человек
                       # решит, что решение не сохранилось, и поставит её снова.
                       'mail_photo_on': bool(profile.get('mail_photo_on')),
                       # Папка галереи на Диске — вкладке «Галерея», чтобы селект
                       # после перезагрузки показывал выбранное.
                       #
                       # ⚠ **Пусто — обычный исход, а не «не настроено»:** папки
                       # названы логином анкеты, и пустое значение читается как
                       # «как у всех». Подставляет логин сервер, не страница.
                       #
                       # ⚠ Пишется поле **только ручкой галереи**: общий
                       # `PUT /api/dating/profile` его не принимает, иначе
                       # появился бы второй способ задать папку.
                       'gallery_folder': str(profile.get('gallery_folder') or ''),
                       'contacts_total': total, 'page': page, 'pages': pages}}


