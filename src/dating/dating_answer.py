"""Разговор с собеседником: спросить агента и отправить сказанное в чат.

**Роли не равны.** На этом сайте разговор начинает мужчина — он платит за
сообщения и ведёт беседу; женская анкета отвечает. Поэтому и поводы написать
разные:

| Роль | Когда пишет |
|------|-------------|
| `lady` | пришло сообщение без ответа |
| `man`  | то же **плюс** тишина в диалоге дольше `DATING_ANSWER_NUDGE_SEC` |

Второе правило нужно, потому что молчание — законный ответ агента: женская анкета
может промолчать, и для собеседника это выглядит как «не ответила». Инициатор
должен спросить что-то ещё, иначе разговор умрёт на первой же паузе. Знать, что
там было намеренное молчание, ему незачем — он видит только тишину.

Отвечает агент анкеты (`dating_agent`), а не этот модуль: здесь только доставка —
взять неотвеченное, сходить в `/chat/ask`, отправить полученное в чат и проставить
пометку. Что именно сказать, решает workflow агента, и менять это надо там.

**Пустой ответ — это решение, а не сбой.** Агент может намеренно промолчать:
собеседник написал «ок», или разговор окончен, или workflow решил не отвечать.

Молчание отмечается **попыткой** (`dating_message.answer_tried_at`), а не пометкой
«отвечено». Разница важная. «Отвечено» значит «мы отправили ответ», и ставить его
на молчание — обманывать оператора: в панели диалог выглядит закрытым, хотя в нём
нет ни слова от нас. А спрашивать агента про то же сообщение каждую минуту —
сжигать деньги на LLM. Отметка попытки решает и то, и другое: сообщение остаётся
без ответа, но повторно агента спросят не раньше `DATING_ANSWER_RETRY_SEC`.

**Кому отвечать — задаётся у анкеты** (`dating_profile.auto_answer_only`): номера
собеседников через запятую, пусто — всем. Это не украшение: каждый ответ стоит
денег дважды — прогон LLM и платное сообщение на сайте, — а у анкеты с сотнями
диалогов «отвечать всем» превращается в рассылку. Проверено на живой учётке:
включённый без ограничения автоответ за четыре минуты написал шести собеседницам.

**Права берутся у того, кто нажал.** Внутренний вызов идёт с заголовком авторизации
исходного запроса: ответ делается от имени человека, а не от безымянного служебного
токена, который пришлось бы где-то хранить.
"""


import json

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_chat_ask
from src.dating.dating_log import dating_log_write
from src.dating.dating_online import dating_online_check
from src.dating.dating_send import dating_send_message

# Тип собеседника для движка. У анкет он один — человек в чате.
DATING_ANSWER_COMPANION_ID = 1

# Сколько диалогов отвечаем за раз. Каждый — прогон workflow, и полсотни разом
# превратили бы кнопку в получасовое ожидание.
DATING_ANSWER_LIMIT = 5

# Через сколько тишины инициатор пишет снова. Три минуты: раньше — похоже на
# нетерпеливого робота, позже — разговор рвётся. Считается от последней реплики
# в диалоге, чья бы она ни была.
DATING_ANSWER_NUDGE_SEC = 180

# Роль, которая ведёт разговор: пишет первой и возвращается после тишины.
DATING_ANSWER_ROLE_LEAD = 'man'

# Через сколько повторно спрашивать агента про то же сообщение, если он промолчал.
# Меньше — трата LLM на одно и то же, больше — собеседник ждёт слишком долго.
DATING_ANSWER_RETRY_SEC = 300

# Насколько свежим должно быть привезённое из панели сообщение, чтобы на него
# отвечали. Сутки.
#
# ⚠ Сутки, а не «с прошлого импорта»: панель отдаёт переписку **по датам**, без
# времени с точностью до минуты, и узкое окно теряло бы сообщения на границе суток.
# А ответ через сутки на дневное сообщение — всё ещё разговор; через неделю — уже
# нет, и такое лучше не отправлять вовсе.
#
# Считается по времени **сайта** (`created_at`), а не по времени импорта: иначе
# повторный импорт делал бы недельное сообщение «свежим».
DATING_ANSWER_PANEL_FRESH_SEC = 24 * 3600

# Что сказать агенту, когда собеседник молчит. Это не текст сообщения — это
# вопрос агенту, а сочиняет он сам.
DATING_ANSWER_NUDGE_PROMPT = ('Собеседница не ответила на последнее сообщение. '
                              'Продолжи разговор: задай ей новый вопрос.')

# Язык ответа. Не вкусовщина: фильтр сайта отклоняет кириллицу — «Message not
# delivered. Unacceptable words: …», — поэтому русский ответ агента до собеседника
# просто не дойдёт. Язык задаётся сайтом (`presence.answer_language`), а здесь
# лишь разумное умолчание.
DATING_ANSWER_LANGUAGE = 'en'


async def dating_answer_run(profile: dict, presence: dict, agent_id: int,
                            limit: int = DATING_ANSWER_LIMIT) -> dict:
    """Отвечает тем, чьё сообщение осталось без ответа.

    Args:
        profile: строка `dating_profile`.
        presence: настройка сайта — из неё берётся отправка.
        agent_id: агент анкеты, который сочиняет ответ.
        limit: сколько диалогов обработать за раз.

    Returns:
        dict: `asked` — сколько раз спросили агента, `sent` — сколько ответов
        ушло, `silent` — сколько раз агент промолчал, `offline` — сколько
        собеседников вышли из чата, `errors` — что не вышло.
    """
    profile_id = int(profile['id'])
    # Имя анкеты для журнала: строки всех анкет там вперемешку, и «→ Semen» без
    # отправителя не говорит, кто ответил.
    me = _me_name(profile)
    language = str(presence.get('answer_language') or DATING_ANSWER_LANGUAGE)
    only = _only_parse(profile.get('auto_answer_only'))
    lead = str(profile.get('role') or '') == DATING_ANSWER_ROLE_LEAD
    waiting = await _waiting_find(profile_id, limit, only, lead)

    if not waiting:
        await dating_log_write(profile_id, 'info',
                               'Продолжать разговор пока не нужно' if lead
                               else 'Неотвеченных сообщений нет')
        return {'asked': 0, 'sent': 0, 'silent': 0, 'offline': 0, 'errors': []}

    await dating_log_write(profile_id, 'info', f'Поводов написать: {len(waiting)}')

    asked = sent = silent = offline = 0
    errors = []

    for row in waiting:
        uid = str(row['sender_uid'])
        # Имя **с номером**: имена собеседников повторяются десятками («три Natalia
        # в списке — обычное дело»), и по одному имени в журнале не понять, с кем
        # разговор.
        name = str(row['sender_name'] or '').strip()
        who = f'{name} {uid}' if name else f'#{uid}'

        # ⚠ **Собеседника нет в сети — не спрашиваем агента вовсе.**
        #
        # Проверка стоит **до** `_agent_ask`, и это главное: ответ LLM стоит денег,
        # а отправить его будет некому — сайт либо отклонит сообщение, либо примет
        # в пустоту. Прежде порядок был обратный: спрашивали, получали ответ и
        # только потом узнавали от сайта, что собеседник вышел.
        #
        # Пометки попытки тоже нет: он вернётся, и тогда ответ уйдёт. Сообщение
        # остаётся неотвеченным, а не теряется.
        away = dating_online_check(int(profile.get('site_id') or 0), uid)
        if not away['online']:
            offline += 1
            await dating_log_write(profile_id, 'info',
                                   f'{who}: нет в сети, отвечу когда вернётся')
            continue

        # Тишина — это не реплика: агенту про неё говорим прямо, иначе он ответит
        # на давнее сообщение, как будто оно только что пришло.
        question = (str(row['text']) if row.get('direction') == 'in'
                    else DATING_ANSWER_NUDGE_PROMPT)

        # Попытку отмечаем до вопроса, а не после: прогон длится минуты, и круг
        # успел бы начаться заново, спросив агента о том же во второй раз.
        await _tried_mark(profile_id, uid)

        answer, error = await _agent_ask(agent_id, question, language, profile)
        asked += 1
        if error:
            errors.append(f'{who}: {error}')
            await dating_log_write(profile_id, 'fail', f'Агент не ответил для {who}: {error}')
            continue

        if not answer:
            # ⚠ **Молчание — решение, и его надо запомнить.**
            #
            # Прежде ставилась только отметка попытки, а `is_answered` оставался
            # нулём: значок «нужен ответ» мигал вечно, а круг спрашивал агента
            # снова каждые пять минут — тот снова молчал, и молчание стоило денег
            # на LLM в цикле.
            #
            # `is_answered` для этого не годится: он означает «мы отправили ответ»,
            # и диалог выглядел бы закрытым без единого нашего слова. Поэтому своя
            # метка — она же и время, для показа в окне переписки.
            silent += 1
            await _silent_mark(profile_id, uid)
            await dating_log_write(profile_id, 'info',
                                   f'{me} → {who}: агент решил не отвечать')
            continue

        result = await dating_send_message(profile, presence, uid, answer)
        if result['ok']:
            sent += 1
            await dating_log_write(profile_id, 'out', f'{me} → {who}: «{answer[:300]}»')
        elif result.get('offline'):
            # Собеседник вышел из чата. Ошибкой это не считаем и пометку не
            # ставим: он вернётся, и тогда ответ уйдёт — сообщение остаётся
            # неотвеченным, а не теряется.
            offline += 1
            await dating_log_write(profile_id, 'info', f'{who}: нет в чате, отвечу когда вернётся')
        else:
            errors.append(f"{who}: {result['error']}")
            # ⚠ Текст и при отказе: без него не видно, что именно не ушло, и
            # нельзя решить — повторять или сообщение всё-таки дошло.
            await dating_log_write(
                profile_id, 'fail',
                f"Не ушло: {me} → {who} «{answer[:120]}» — {result['error']}")

    await dating_log_write(
        profile_id, 'ok',
        f'Ответы: спрошено {asked}, отправлено {sent}, промолчал {silent}, '
        f'нет в чате {offline}, ошибок {len(errors)}')

    return {'asked': asked, 'sent': sent, 'silent': silent, 'offline': offline, 'errors': errors}


async def _agent_ask(agent_id: int, question: str, language: str,
                     profile: dict) -> tuple[str, str]:
    """Спрашивает агента в RhapsodyAi. Возвращает (ответ, ошибка).

    Пустой ответ ошибкой не считается — он значим сам по себе и разбирается выше:
    агент может намеренно промолчать.

    ⚠ **Кто говорит — задаётся переменными вызова, а не агентом.** Отвечает один
    универсальный агент на все анкеты (`dating_agent.py`), и без `girl_name` он
    назовётся значением по умолчанию, а без `girl_id` сложит факты всех девушек в
    один документ. Наши переменные в сборке у соседа идут последними и перекрывают
    его собственные.

    Ходим служебным токеном, а не заголовками того, кто нажал кнопку: в RhapsodyAi
    пользователей этого проекта нет, и представляться там надо своей учёткой.
    """
    from src.dating.dating_agent import dating_agent_vars

    card = profile.get('profile')
    if isinstance(card, str):
        try:
            card = json.loads(card or '{}')
        except ValueError:
            card = {}

    try:
        answer = await rhapsody_api_chat_ask(
            agent_id, question, companion_id=DATING_ANSWER_COMPANION_ID,
            variables={'user_language': language,
                       **dating_agent_vars(profile, card or {})})
    except RhapsodyError as e:
        return '', str(e)

    return answer, ''



def _me_name(profile: dict) -> str:
    """Имя анкеты с сайта — то, под которым её видит собеседник.

    Из JSON-столбца `profile`: анкета приходит от сайта целиком, и вытаскивать
    поля в свои колонки значило бы держать вторую копию. Нет имени — остаётся
    логин, он есть всегда.
    """
    card = profile.get('profile')
    if isinstance(card, str):
        try:
            card = json.loads(card or '{}')
        except ValueError:
            card = {}

    card = card or {}
    name = str(card.get('name') or '').strip()
    uid = str(card.get('site_uid') or card.get('id') or '').strip()

    if name:
        return f'{name} {uid}'.strip()

    return uid or str(profile.get('login') or '')


def _only_parse(value) -> list:
    """Список тех, кому разрешено отвечать. Пусто — ограничения нет."""
    return [part.strip() for part in str(value or '').replace(';', ',').split(',') if part.strip()]


async def _waiting_find(profile_id: int, limit: int, only: list, lead: bool) -> list:
    """Поводы написать: по одному на диалог.

    Смотрим последнюю реплику диалога, а не каждое сообщение: ответ на неё
    закрывает переписку целиком, а не одну строку.

    Отвечающему повод один — входящее без ответа. Ведущему разговор их два: то же
    входящее и **тишина**, когда последнее слово осталось за нами дольше
    `DATING_ANSWER_NUDGE_SEC`. Иначе после намеренного молчания собеседницы
    разговор кончается сам.

    `only` — кому разрешено писать. Отбор в запросе, а не после: иначе выборка
    «последних N» съедалась бы теми, кому писать нельзя, и разрешённый собеседник
    ждал бы своей очереди неизвестно сколько.
    """
    # Ждёт ответа то входящее, на которое мы не отвечали и про которое давно не
    # спрашивали агента. Второе условие — защита от повторов при молчании.
    #
    # ⚠ **Привезённое из панели теперь отвечается — но только свежее.**
    #
    # Прежде `source = 'panel'` исключался целиком, и это было верно, пока импорт
    # шёл кнопкой: он приносит переписку за неделю разом, и для отбора она
    # неотличима от свежей почты — агент ответил бы на все недельные сообщения
    # пачкой, живым людям.
    #
    # Теперь у сайта без сокета (Ladadate) панель — **единственный** источник
    # входящих (`dating.md`, §5.9.1), и исключать её значит не отвечать вовсе.
    # Поэтому вместо запрета — **порог по возрасту**: отвечаем на то, что пришло не
    # позже `DATING_ANSWER_PANEL_FRESH_SEC`.
    #
    # ⚠ Порог обязателен, а не «желателен»: импорт за 60 дней остаётся доступным
    # кнопкой, и без порога первый же такой прогон дал бы агенту десятки поводов
    # написать по старым письмам. Пометки «уже пробовали» тут мало — она держит
    # пять минут, и потом попытка повторяется.
    #
    # Порог считается по `created_at` — времени **сайта**, а не времени импорта:
    # привезённое вчера сообщение вчерашним и остаётся, сколько бы раз его ни
    # импортировали заново.
    # ⚠ `answer_silent_at IS NULL` — «про это сообщение решения молчать не было».
    # Без него круг переспрашивал агента про то же сообщение до конца времён.
    #
    # Метка стоит на конкретных строках, а не на собеседнике: напишет он снова — у
    # новой строки она пуста, и агента спросят опять. Молчание относится к
    # сказанному, а не к человеку навсегда.
    fresh = ("m.direction = 'in' AND m.is_answered = 0 AND m.answer_silent_at IS NULL "
             " AND (m.source <> 'panel' "
             "      OR m.created_at > NOW() - INTERVAL %s SECOND) "
             " AND (m.answer_tried_at IS NULL "
             "      OR m.answer_tried_at < NOW() - INTERVAL %s SECOND)")

    reason = f'({fresh})' if not lead else \
        f'({fresh} OR m.created_at < NOW() - INTERVAL %s SECOND)'

    # Номер анкеты дважды: во внешний подзапрос («последняя реплика диалога») и во
    # внутренний («какая именно, если время совпало»).
    params = [int(profile_id), int(profile_id), int(DATING_ANSWER_PANEL_FRESH_SEC),
              int(DATING_ANSWER_RETRY_SEC)]
    if lead:
        params.append(int(DATING_ANSWER_NUDGE_SEC))

    where = ''
    if only:
        where = ' AND m.sender_uid IN (' + ', '.join(['%s'] * len(only)) + ')'
        params.extend(only)
    params.append(int(limit))

    # ⚠ **«Последняя реплика» считается по времени, а не по `MAX(id)`.**
    #
    # Автоинкремент совпадал с порядком событий, пока писал один сокет. Импорт
    # переписки из панели приносит **историю за неделю** и кладёт её сегодня, то
    # есть с наибольшими `id`: «последней» в диалоге оказывалась реплика недельной
    # давности.
    #
    # Последствие было не косметическое: свежее неотвеченное сообщение оставалось
    # без ответа, потому что последней считалась привезённая старая реплика. Тот же
    # разбор, что у списка диалогов (`dating_profile_view.py`) и у запрета повтора
    # (`dating_send.py`) — три места, и во всех порядок один.
    #
    # Двойной подзапрос нужен для случая одинакового времени: панель отдаёт время с
    # точностью до минуты, и две реплики в одну минуту — обычное дело. Тогда
    # различаем по `id` — внутри одного времени он снова означает порядок.
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.sender_uid, m.sender_name, m.text, m.direction FROM dating_message m '
            '  JOIN (SELECT sender_uid, MAX(created_at) AS last_at FROM dating_message '
            '         WHERE profile_id = %s GROUP BY sender_uid) t '
            '    ON t.sender_uid = m.sender_uid AND t.last_at = m.created_at '
            '   AND m.id = (SELECT MAX(x.id) FROM dating_message x '
            '                WHERE x.profile_id = %s AND x.sender_uid = m.sender_uid '
            '                  AND x.created_at = t.last_at) '
            f' WHERE {reason}' + where +
            ' ORDER BY m.created_at DESC, m.id DESC LIMIT %s', tuple(params))
        rows = await db.fetchall()

    return [dict(row) for row in (rows or [])]


async def _silent_mark(profile_id: int, uid: str) -> None:
    """Отмечает, что про эти сообщения агент решил не отвечать.

    Ставится на все неотвеченные входящие этого собеседника — на то, что агент и
    видел, когда решал. Он читает разговор целиком, а не одну реплику, и решение
    относится ко всему, что накопилось.

    Не бросает: неудачная отметка вернёт круг к тому же сообщению через пять минут
    — неприятно, но терпимо, а сорванный круг лишил бы ответов всех остальных.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE dating_message SET answer_silent_at = NOW() '
                " WHERE profile_id = %s AND sender_uid = %s AND direction = 'in' "
                '   AND is_answered = 0 AND answer_silent_at IS NULL',
                (int(profile_id), str(uid)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] молчание #{profile_id} не отметилось: {e}')


async def _tried_mark(profile_id: int, uid: str) -> None:
    """Отмечает, что про эти сообщения агента уже спрашивали.

    Не бросает: неудачная отметка приведёт к лишнему вопросу агенту через минуту,
    а сорванный из-за неё круг — к тому, что не ответят вообще никому.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE dating_message SET answer_tried_at = NOW() '
                " WHERE profile_id = %s AND sender_uid = %s AND direction = 'in' AND is_answered = 0",
                (int(profile_id), str(uid)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] отметка попытки не проставилась: {e}')
