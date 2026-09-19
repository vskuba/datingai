"""Импорт переписки из панели агентства: кнопка и её ручка.

Сообщения снимает сценарий у соседа (`чат: сообщения из панели`), разбирает и
кладёт `src/dating/chat_panel.py`. Здесь порядок вызовов: найти сценарий,
погнать его за диапазон дат, разобрать ответ, записать.

Показывать привезённое отдельной страницей не нужно: сообщения ложатся в
`dating_message`, и карточка чата на «Профилях» показывает их сама — тем же
списком диалогов и той же лентой реплик, что и пойманное сокетом.
"""

from fastapi import APIRouter, Depends, HTTPException

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.dating.chat_panel import (CHAT_PANEL_DAYS,
                                          CHAT_PANEL_SCENARIO,
                                          chat_panel_import)
from src.dating.json_column import json_column_load
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_scenario_find

router = APIRouter(prefix='/api/dating/chat', tags=['dating'])

# Сколько диалогов отдаём страницей. То же число, что у карточки анкеты
# (`PROFILE_VIEW_CONTACTS_PAGE`): страница у списка одна и та же, и два
# разных размера означали бы прыгающий пейджер при переключении фильтра.
CHAT_DIALOGS_PAGE = 50


@router.get('/ready', include_in_schema=False)
async def chat_ready(site_id: int = 0, user=Depends(auth_required)):
    """Настроен ли импорт переписки у сайта — есть ли у него сценарий.

    Признак берётся у соседа, а не из своей настройки: сценарий заводят там, и
    своя галочка разъехалась бы с ним в день, когда его переименуют.

    Отказ соседа — не «не настроено», а `ready: false` с причиной: кнопку гасим в
    обоих случаях, но чинить человеку разное.
    """
    if not int(site_id or 0):
        return {'result': {'ready': False, 'why': 'сайт не выбран'}}

    try:
        scenario = await rhapsody_api_scenario_find(int(site_id), CHAT_PANEL_SCENARIO)
    except RhapsodyError as e:
        return {'result': {'ready': False, 'why': str(e)}}

    if not scenario:
        return {'result': {'ready': False,
                           'why': f'у сайта нет сценария «{CHAT_PANEL_SCENARIO}» — '
                                  f'заведите его в RhapsodyAi на вкладке «Сценарии»'}}

    return {'result': {'ready': True, 'why': ''}}


@router.post('/import', include_in_schema=False)
async def chat_import(site_id: int = 0, days: int = CHAT_PANEL_DAYS,
                             user=Depends(auth_required)):
    """Забирает переписку сайта за последние `days` дней и кладёт в базу.

    Args:
        site_id: сайт-закладка, с которого забираем.
        days: за сколько дней спросить панель. Предел —
            `CHAT_PANEL_DAYS_MAX`, и проверяет его сама работа.

    Returns:
        dict: `total` — сколько строк дала панель, `ours` — сколько из них про наши
        анкеты, `added` — сколько новых, `same` — сколько уже было, `contacts` —
        скольким собеседникам завели карточку, `from`/`to` — запрошенный диапазон.

    Raises:
        HTTPException: 422 — не выбран сайт, кривой диапазон, нет сценария или у
            сайта нет ни одной анкеты с номером; 502 — сосед не ответил.

    ⚠ **Сценарий встал — это ответ, а не поломка панели** (200 со `status: error`).
    Правило то же, что у импорта анкет: сайт мог сменить вёрстку или не пустить в
    панель, и код ответа говорит про наш запрос, а не про то, что вышло у него.
    """
    # Работа — в `chat_panel_import`: её же зовёт круг воркера, который
    # забирает сообщения сам по настройке сайта. ⚠ Одной функцией на два вызова:
    # разойдись они — и автоматический импорт однажды стал бы разбирать ответ
    # иначе, чем ручной, а заметить это было бы нечем.
    #
    # Здесь остаётся то, что про HTTP: причины отказа превращаются в коды. Функция
    # их только называет — кругу воркера падать нельзя, он обходит все сайты.
    result = await chat_panel_import(site_id, days)

    if result['status'] == 'error':
        error = result['error']

        # ⚠ **Сорвавшийся прогон — это ответ, а не код ошибки.** Механика цела,
        # сосед на месте, просто панель сайта не отдала таблицу: страница показывает
        # причину в панели импорта, где человек её и читает. Отдай мы 502 — он увидел
        # бы «шлюз не отвечает» вместо «сценарий встал на шаге 7: нет таблицы».
        if 'сценарий встал' in error:
            return {'result': result}

        # 502 — «не мы»: соседа нет или он отказал. 422 — «спросили не то»: нет
        # сайта, нет анкет, нет сценария. Различать обязательно: по 502 звонят
        # соседу, по 422 правят настройку.
        raise HTTPException(status_code=502 if 'RhapsodyAi' in error else 422,
                            detail=error)

    return {'result': result}


async def _unanswered(site_id: int, profile_id: int = 0) -> int:
    """Сколько разговоров ждут нашего ответа.

    ⚠ **Считаем пары, а не реплики**: человек мог написать трижды подряд, и это
    один разговор, требующий одного ответа.

    ⚠ **Только входящие без ответа** (`is_answered = 0`): флаг ставит тот же код,
    что отвечает, и второй способ «понять, отвечали ли» разошёлся бы с ним.

    Сбой чтения отвечает нулём: значок — подсказка, а не работа, и ронять из-за
    него список диалогов незачем.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM ('
                '  SELECT m.profile_id, m.sender_uid FROM message m'
                '    JOIN profile p ON p.id = m.profile_id'
                '   WHERE p.site_id = %s AND (%s = 0 OR m.profile_id = %s)'
                "     AND m.direction = 'in' AND m.is_answered = 0"
                '   GROUP BY m.profile_id, m.sender_uid) AS d',
                (int(site_id), int(profile_id), int(profile_id)))
            row = await db.fetchone()

        return int((row or {}).get('n') or 0)
    except Exception as e:
        logger_info(f'[dating] неотвеченные диалоги сайта #{site_id} не спросились: {e}')

        return 0


@router.post('/dialogs/{profile_id}/{uid}/read', include_in_schema=False)
async def chat_dialog_read(profile_id: int, uid: str,
                           user=Depends(auth_required)):
    """Помечает разговор прочитанным: человек его открыл и увидел.

    ## ⚠⚠ Прочитано и «нужен ответ» — разное, и снимается разным

    `is_read` снимает **взгляд**: человек открыл разговор и увидел реплики.
    `is_answered` снимает **ответ**: пока последнее слово за собеседником, строка
    остаётся в работе, сколько на неё ни смотри.

    Пока пометки не было вовсе, список показывал разговор новым и после того, как
    его прочли: снять выделение было нечем, кроме ответа.

    ⚠ **По паре, а не по анкете.** Ручка `/profile/{id}/read` помечает всё разом —
    она для карточки анкеты, где человек видит весь список. Здесь он открыл один
    разговор и увидел один; чужие пометить за него значило бы соврать.

    Право — на просмотр: пометка «я это видел» не меняет ни текста, ни работы.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE message SET is_read = 1"
            " WHERE profile_id = %s AND sender_uid = %s"
            "   AND direction = 'in' AND is_read = 0",
            (int(profile_id), str(uid)))
        read = int(db.rowcount or 0)
        await db.connection.commit()

    return {'result': {'read': read}}


@router.get('/dialogs', include_in_schema=False)
async def chat_dialogs(site_id: int = 0, profile_id: int = 0, page: int = 1,
                              user=Depends(auth_required)):
    """Диалоги **всего сайта** страницей. Выбранная анкета — фильтр, не условие.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не диалоги всех сайтов.
        profile_id: сузить до одной анкеты. Ноль — все включённые и выключенные:
            переписка выключенной учётки не исчезает, и прятать её значило бы
            терять разговоры вместе с флагом.
        page: страница, с единицы.

    Returns:
        dict: `contacts` (как у карточки анкеты, плюс `profile_id`,
        `profile_name`, `profile_uid`), `contacts_total`, `page`, `pages`.

    ⚠ **Зачем ручка по сайту, если есть карточка анкеты.** Та отвечает на вопрос
    «что у этой девушки», а на «Профилях» первым встаёт другой: «что вообще
    происходит на сайте». Тринадцать анкет — тринадцать списков, и человек листал
    бы их по очереди, чтобы найти неотвеченное. Здесь всё в одном списке, а анкета
    работает фильтром — то есть отвечает и на первый вопрос, и на второй.

    ⚠ **Диалог — это пара «анкета — собеседник», а не человек.** Один и тот же
    мужчина, пишущий двум нашим девушкам, это два разговора: у них разные реплики
    и разные «нужен ответ». Группировка поэтому по паре, а `profile_id` едет в
    каждой строке — по нему страница знает, чьё окно открывать.

    ⚠ **Порядок — по свежести разговора.** Прежде первым ключом шло «неотвеченные
    сверху», и это было верно, пока разговор открывали окном: список читали, ища
    работу. Теперь разговор стоит рядом со списком, и первую строку страница
    открывает сама — значит первой должна быть та, где шевелилось последним, иначе
    открывался бы не свежий разговор, а самый старый неотвеченный.

    «Ждёт ответа» не потерялось: у строки остаётся значок ↩ и цветная пометка.

    ⚠ **Диалог начинается с ответа.** В список идут только пары, где есть хоть
    одна **входящая** реплика; разговор, где говорили только мы, — не разговор.

    Правило завелось по инвайтам. Ушедшая первая реплика кладётся в
    `dating_message` как обычная исходящая, и на странице она становилась
    карточкой диалога с одним сообщением — из трёх строк списка настоящей была
    одна. Инвайты, на которые ответили, при этом остаются: пришёл ответ — значит
    разговор начался, и он ничем не отличается от прочих.

    ⚠ Ушедшие инвайты **не теряются**: им своя подвкладка «Доставка» рядом с
    пулом, где видно, от кого, кому и чем кончилось (`api.md`, §3.9).
    """
    if not int(site_id or 0):
        return {'result': {'contacts': [], 'contacts_total': 0, 'page': 1, 'pages': 1}}

    page = max(1, int(page or 1))
    size = CHAT_DIALOGS_PAGE

    async with mysql_get_db_async() as db:
        # ⚠ Считаем **пары**, а не собеседников: один человек у двух анкет это два
        # диалога, и `COUNT(DISTINCT sender_uid)` соврал бы на единицу за каждого
        # такого — а именно они и есть самое интересное в списке.
        # ⚠ `HAVING` по входящим — то самое правило «диалог начинается с ответа».
        # Стоит в **обоих** запросах, и это не дублирование ради симметрии: счёт
        # рисует пейджер, и разойдись он со списком — последняя страница выходила
        # бы пустой.
        await db.execute(
            'SELECT COUNT(*) AS n FROM ('
            '  SELECT m.profile_id, m.sender_uid FROM message m'
            '    JOIN profile p ON p.id = m.profile_id'
            '   WHERE p.site_id = %s AND (%s = 0 OR m.profile_id = %s)'
            '   GROUP BY m.profile_id, m.sender_uid'
            "  HAVING SUM(m.direction = 'in') > 0) AS d",
            (int(site_id), int(profile_id), int(profile_id)))
        total = int(((await db.fetchone()) or {}).get('n') or 0)

        pages = max(1, (total + size - 1) // size)
        page = min(page, pages)

        # Устроено как в карточке анкеты (`profile_view.py`), и оговорки к
        # запросу те же: `MAX(created_at)` с индексом вместо оконной функции,
        # вложенный `MAX(id)` разнимает реплики одной секунды, а направление и
        # признак «нужен ответ» берутся присоединением, а не агрегатом — иначе база
        # уходит читать все строки вместо индекса.
        # ⚠ **Карточку собеседника подмешиваем из `companion`.**
        #
        # `dating_contact` — собеседницы **этой анкеты**, и строки там нет у
        # диалога, где говорили только мы: отправка кладёт реплику в
        # `dating_message`, а контакт не заводит. Такой диалог показывался как
        # «без имени» и без лица — при том, что и имя, и фотография лежали в базе
        # рядом, в карточке собеседника по сайту.
        #
        # Видно это было по странному признаку: стоило выбрать анкету, и лица
        # появлялись. Появлялись они не из базы — страница подставляла их из
        # снимка списка `dialog`, а тот грузится только под выбранной анкетой
        # (`peoplePhoto`). То есть данные брались из памяти браузера вместо той
        # самой таблицы, где и лежат.
        #
        # Порядок источников: контакт анкеты (свежее, приходит с чатом) →
        # карточка сайта → имя из самой переписки. `NULLIF` обязателен: пустая
        # строка в `contact.photo` — обычное дело, а `COALESCE` считает её
        # значением и до карточки не доходит.
        await db.execute(
            'SELECT d.profile_id, d.uid, d.messages, last.id AS last_id,'
            '       COALESCE(NULLIF(c.name, %s), NULLIF(comp.name, %s), %s) AS name,'
            '       COALESCE(NULLIF(c.photo, %s), NULLIF(comp.photo, %s), %s) AS photo,'
            '       COALESCE(NULLIF(c.age, 0), comp.age, 0) AS age,'
            '       COALESCE(NULLIF(c.city, %s), comp.city, %s) AS city,'
            '       c.is_online,'
            '       p.login AS profile_login, p.profile AS profile_card,'
            "       (last.direction = 'in' AND last.is_answered = 0"
            '        AND last.answer_silent_at IS NULL) AS needs_answer,'
            # ⚠ Непрочитанные — **отдельно от «нужен ответ»**: первое снимает
            # взгляд, второе ответ. Слей их в одно поле, и разговор оставался бы
            # новым после прочтения либо гас до ответа.
            '       (SELECT COUNT(*) FROM message u'
            '         WHERE u.profile_id = d.profile_id AND u.sender_uid = d.uid'
            "           AND u.direction = 'in' AND u.is_read = 0) AS unread"
            '  FROM (SELECT m.profile_id, m.sender_uid AS uid, COUNT(*) AS messages,'
            '               MAX(m.created_at) AS last_at'
            '          FROM message m'
            '          JOIN profile p2 ON p2.id = m.profile_id'
            '         WHERE p2.site_id = %s AND (%s = 0 OR m.profile_id = %s)'
            '         GROUP BY m.profile_id, m.sender_uid'
            # ⚠ Пары без единой входящей реплики — не диалоги (см. выше).
            "        HAVING SUM(m.direction = 'in') > 0) d"
            '  JOIN message last'
            '    ON last.profile_id = d.profile_id AND last.sender_uid = d.uid'
            '   AND last.id = (SELECT MAX(m2.id) FROM message m2'
            '                   WHERE m2.profile_id = d.profile_id'
            '                     AND m2.sender_uid = d.uid'
            '                     AND m2.created_at = d.last_at)'
            '  JOIN profile p ON p.id = d.profile_id'
            '  LEFT JOIN contact c'
            '    ON c.profile_id = d.profile_id AND c.uid = d.uid'
            '  LEFT JOIN companion comp'
            '    ON comp.site_id = p.site_id AND comp.uid = d.uid'
            # ⚠ **По свежести, и только по ней.** Прежде первым ключом шёл
            # `needs_answer` — «неотвеченные наверх», — и это разумно, пока
            # разговор открывают окном: список читают, ища работу. Но теперь
            # разговор стоит рядом, и первую строку страница открывает сама;
            # значит первой должна быть та, где шевелилось последним, иначе
            # открывается не самый свежий разговор, а самый старый неотвеченный.
            #
            # «Ждёт ответа» при этом не потерялось: у строки остаётся значок ↩.
            ' ORDER BY d.last_at DESC, last.id DESC'
            ' LIMIT %s OFFSET %s',
            ('', '', '', '', '', '', '', '',
             int(site_id), int(profile_id), int(profile_id),
             size, (page - 1) * size))
        contacts = [dict(row) for row in (await db.fetchall() or [])]

        for contact in contacts:
            contact['needs_answer'] = bool(contact['needs_answer'])
            contact['unread'] = int(contact.get('unread') or 0)

            card = json_column_load(contact.pop('profile_card', None)) or {}
            contact['profile_name'] = str(card.get('name')
                                          or contact.get('profile_login') or '')
            # Номер анкеты на сайте — заголовку окна переписки: по нему её находят
            # в панели сайта, а наш внутренний `id` там ни к чему.
            contact['profile_uid'] = str(card.get('id') or '')
            # ⚠ И лицо анкеты: карточка диалога показывает **обе** стороны
            # разговора, а не только собеседника. Берём из той же анкеты с сайта
            # (`profile_.profile`), что и список слева, — иначе одна и та же
            # девушка выглядела бы в двух местах по-разному.
            contact['profile_photo'] = str(card.get('photo') or '')

            await db.execute(
                'SELECT text, direction, is_answered, answer_silent_at, created_at'
                '  FROM message'
                ' WHERE profile_id = %s AND sender_uid = %s'
                ' ORDER BY created_at DESC, id DESC LIMIT 10',
                (int(contact['profile_id']), contact['uid']))
            last = [dict(row) for row in (await db.fetchall() or [])]
            # Разворачиваем: выбирали свежие, а читать надо сверху вниз.
            contact['messages_last'] = list(reversed(last))

            # Имя из переписки — только тем, кого нет в `dating_contact`: сайт
            # отдаёт список собеседниц не целиком, а подпись строке нужна всё равно.
            if not contact['name']:
                await db.execute(
                    'SELECT MAX(sender_name) AS name FROM message'
                    ' WHERE profile_id = %s AND sender_uid = %s AND sender_name <> %s',
                    (int(contact['profile_id']), contact['uid'], ''))
                contact['name'] = ((await db.fetchone()) or {}).get('name') or ''

    return {'result': {'contacts': contacts, 'contacts_total': total,
                       'page': page, 'pages': pages,
                       # ⚠⚠ **Сколько разговоров ждут ответа — числом от сервера.**
                       #
                       # Значок у вкладки «Диалоги» считал события ленты, а лента
                       # это кольцо на тридцать строк **на все виды событий**: при
                       # живом темпе инвайтов (по пять сотен за заход) в ней
                       # лежат одни инвайты, и `talk` вытесняется за секунды.
                       #
                       # Поймано глазами: два непрочитанных разговора в базе,
                       # полоса вкладок молчит. Лента годится, чтобы показать
                       # «что-то происходит», но не чтобы считать.
                       'unanswered': await _unanswered(int(site_id), int(profile_id))}}
