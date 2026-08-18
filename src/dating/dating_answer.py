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

**Кому отвечать — задаётся у анкеты** (`dating_girl.auto_answer_only`): номера
собеседников через запятую, пусто — всем. Это не украшение: каждый ответ стоит
денег дважды — прогон LLM и платное сообщение на сайте, — а у анкеты с сотнями
диалогов «отвечать всем» превращается в рассылку. Проверено на живой учётке:
включённый без ограничения автоответ за четыре минуты написал шести собеседницам.

**Права берутся у того, кто нажал.** Внутренний вызов идёт с заголовком авторизации
исходного запроса: ответ делается от имени человека, а не от безымянного служебного
токена, который пришлось бы где-то хранить.
"""


from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_chat_ask
from src.dating.dating_log import dating_log_write
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

# Что сказать агенту, когда собеседник молчит. Это не текст сообщения — это
# вопрос агенту, а сочиняет он сам.
DATING_ANSWER_NUDGE_PROMPT = ('Собеседница не ответила на последнее сообщение. '
                              'Продолжи разговор: задай ей новый вопрос.')

# Язык ответа. Не вкусовщина: фильтр сайта отклоняет кириллицу — «Message not
# delivered. Unacceptable words: …», — поэтому русский ответ агента до собеседника
# просто не дойдёт. Язык задаётся сайтом (`presence.answer_language`), а здесь
# лишь разумное умолчание.
DATING_ANSWER_LANGUAGE = 'en'


async def dating_answer_run(girl: dict, presence: dict, agent_id: int,
                            limit: int = DATING_ANSWER_LIMIT) -> dict:
    """Отвечает тем, чьё сообщение осталось без ответа.

    Args:
        girl: строка `dating_girl`.
        presence: настройка сайта — из неё берётся отправка.
        agent_id: агент анкеты, который сочиняет ответ.
        limit: сколько диалогов обработать за раз.

    Returns:
        dict: `asked` — сколько раз спросили агента, `sent` — сколько ответов
        ушло, `silent` — сколько раз агент промолчал, `offline` — сколько
        собеседников вышли из чата, `errors` — что не вышло.
    """
    girl_id = int(girl['id'])
    language = str(presence.get('answer_language') or DATING_ANSWER_LANGUAGE)
    only = _only_parse(girl.get('auto_answer_only'))
    lead = str(girl.get('role') or '') == DATING_ANSWER_ROLE_LEAD
    waiting = await _waiting_find(girl_id, limit, only, lead)

    if not waiting:
        await dating_log_write(girl_id, 'info',
                               'Продолжать разговор пока не нужно' if lead
                               else 'Неотвеченных сообщений нет')
        return {'asked': 0, 'sent': 0, 'silent': 0, 'offline': 0, 'errors': []}

    await dating_log_write(girl_id, 'info', f'Поводов написать: {len(waiting)}')

    asked = sent = silent = offline = 0
    errors = []

    for row in waiting:
        uid = str(row['sender_uid'])
        who = str(row['sender_name'] or '') or f'#{uid}'

        # Тишина — это не реплика: агенту про неё говорим прямо, иначе он ответит
        # на давнее сообщение, как будто оно только что пришло.
        question = (str(row['text']) if row.get('direction') == 'in'
                    else DATING_ANSWER_NUDGE_PROMPT)

        # Попытку отмечаем до вопроса, а не после: прогон длится минуты, и круг
        # успел бы начаться заново, спросив агента о том же во второй раз.
        await _tried_mark(girl_id, uid)

        answer, error = await _agent_ask(agent_id, question, language)
        asked += 1
        if error:
            errors.append(f'{who}: {error}')
            await dating_log_write(girl_id, 'fail', f'Агент не ответил для {who}: {error}')
            continue

        if not answer:
            # Молчание — решение агента. Отмечаем попытку, но не «ответ»: иначе
            # диалог выглядел бы закрытым без единого нашего слова.
            silent += 1
            await dating_log_write(girl_id, 'info', f'{who}: агент промолчал, вернусь позже')
            continue

        result = await dating_send_message(girl, presence, uid, answer)
        if result['ok']:
            sent += 1
            await dating_log_write(girl_id, 'out', f'{who}: {answer[:300]}')
        elif result.get('offline'):
            # Собеседник вышел из чата. Ошибкой это не считаем и пометку не
            # ставим: он вернётся, и тогда ответ уйдёт — сообщение остаётся
            # неотвеченным, а не теряется.
            offline += 1
            await dating_log_write(girl_id, 'info', f'{who}: нет в чате, отвечу когда вернётся')
        else:
            errors.append(f"{who}: {result['error']}")
            await dating_log_write(girl_id, 'fail', f"Ответ {who} не ушёл: {result['error']}")

    await dating_log_write(
        girl_id, 'ok',
        f'Ответы: спрошено {asked}, отправлено {sent}, промолчал {silent}, '
        f'нет в чате {offline}, ошибок {len(errors)}')

    return {'asked': asked, 'sent': sent, 'silent': silent, 'offline': offline, 'errors': errors}


async def _agent_ask(agent_id: int, question: str, language: str) -> tuple[str, str]:
    """Спрашивает агента в RhapsodyAi. Возвращает (ответ, ошибка).

    Пустой ответ ошибкой не считается — он значим сам по себе и разбирается выше:
    агент может намеренно промолчать.

    Ходим служебным токеном, а не заголовками того, кто нажал кнопку: в RhapsodyAi
    пользователей этого проекта нет, и представляться там надо своей учёткой.
    """
    try:
        answer = await rhapsody_api_chat_ask(agent_id, question,
                                             companion_id=DATING_ANSWER_COMPANION_ID,
                                             variables={'user_language': language})
    except RhapsodyError as e:
        return '', str(e)

    return answer, ''



def _only_parse(value) -> list:
    """Список тех, кому разрешено отвечать. Пусто — ограничения нет."""
    return [part.strip() for part in str(value or '').replace(';', ',').split(',') if part.strip()]


async def _waiting_find(girl_id: int, limit: int, only: list, lead: bool) -> list:
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
    fresh = ("m.direction = 'in' AND m.is_answered = 0 "
             " AND (m.answer_tried_at IS NULL "
             "      OR m.answer_tried_at < NOW() - INTERVAL %s SECOND)")

    reason = f'({fresh})' if not lead else \
        f'({fresh} OR m.created_at < NOW() - INTERVAL %s SECOND)'

    params = [int(girl_id), int(DATING_ANSWER_RETRY_SEC)]
    if lead:
        params.append(int(DATING_ANSWER_NUDGE_SEC))

    where = ''
    if only:
        where = ' AND m.sender_uid IN (' + ', '.join(['%s'] * len(only)) + ')'
        params.extend(only)
    params.append(int(limit))

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.sender_uid, m.sender_name, m.text, m.direction FROM dating_message m '
            '  JOIN (SELECT sender_uid, MAX(id) AS last_id FROM dating_message '
            '         WHERE girl_id = %s GROUP BY sender_uid) t '
            '    ON t.last_id = m.id '
            f' WHERE {reason}' + where +
            ' ORDER BY m.id DESC LIMIT %s', tuple(params))
        rows = await db.fetchall()

    return [dict(row) for row in (rows or [])]


async def _tried_mark(girl_id: int, uid: str) -> None:
    """Отмечает, что про эти сообщения агента уже спрашивали.

    Не бросает: неудачная отметка приведёт к лишнему вопросу агенту через минуту,
    а сорванный из-за неё круг — к тому, что не ответят вообще никому.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE dating_message SET answer_tried_at = NOW() '
                " WHERE girl_id = %s AND sender_uid = %s AND direction = 'in' AND is_answered = 0",
                (int(girl_id), str(uid)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] отметка попытки не проставилась: {e}')
