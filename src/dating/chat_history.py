"""Входящая реплика — в историю разговора у соседа.

Историю пары (`companion_session`) сосед собирает сам и отдаёт агенту лентой.
Писать её он умеет двумя дорогами: прогон workflow пишет обмен целиком, а
`rhapsody_api_chat_add` кладёт **одну половину** — его реплику (`question`),
на которую мы ещё не ответили, либо нашу (`answer`), которую не сочинял
прогон.

⚠ Дверь у соседа одна (`/chat/message/add`), и чья это половина, говорит
довод. Прежде дверей было две — отдельная на приём реплики, — и правки
приходилось вносить в обе по одному доводу.

## ⚠⚠ Зачем отдельным файлом: дорог две, а правило одно

Реплика приходит к нам двумя путями — вебсокетом (`chat.py`) и импортом из
панели агентства (`chat_panel.py`), — и докладывать её соседу надо одинаково.
Пока доклад жил приватной функцией внутри `chat.py`, панельная дорога не
докладывала **ничего**: у сайта без сокета — а это весь наш прод — агент видел
только ту реплику, на которую отвечал прямо сейчас, и не видел разговора.

Собрать ключи сторон по второму разу — ровно та ошибка, от которой стережёт
`companion_ref.py`: склейку `{номер}_{имя}` делает один шаг соседа на оба
конца, и второе правило разошлось бы с первым молча.

## ⚠⚠ Исходящие идут другой дверью и с выдержкой

Нашу реплику у соседа пишет та дорога, которая её сочинила: прогон `/chat/ask`
либо `mail_send_mark`. Привезённая из панели копия того же — **близнец**
(`message_twin.py`), и доклад завёл бы у соседа второй экземпляр сказанного.

Но есть текст, который не сочинял никто из наших: **оператор набрал его руками
в панели сайта**. Сайт его отправил, человек прочёл, а у соседа его нет — и
агент продолжает разговор, будто сказанного не было.

Такую строку докладывает `chat_history_said`, и отдельно она потому, что
узнаётся **не сразу**: в момент вставки своя она или наша, сказать нельзя.
Подробности — в её докстринге.
"""

import json

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.message_twin import message_twin_skip

# Сколько ждать, прежде чем счесть исходящую строку из панели чужой. ⚠ Это не
# вежливость, а **окно гонки**: между «сайт принял кадр» и нашей записью в
# `message` проходят секунды (у кадра по HTTP — до шести), и заход импорта
# помещается в этот промежуток. Десять минут покрывают любую отправку с запасом,
# а операторской реплике задержка ничего не стоит: её всё равно узнают заходом.
CHAT_HISTORY_SAID_WAIT_SEC = 600

# Сколько таких строк докладываем за заход. На проде их 11 за 90 дней, так что
# потолок здесь — предохранитель от неожиданности, а не рабочее ограничение.
CHAT_HISTORY_SAID_MAX = 20


async def chat_history_incoming(profile_id: int, message: dict) -> bool:
    """Отдаёт входящую реплику в историю разговора у соседа.

    Args:
        profile_id: наша анкета.
        message: запись реплики — нужны `sender_uid`, `text`, `message_uid`;
            `sender_name` берётся подсказкой, `sent_at` — временем реплики.

    Returns:
        bool: уехала ли. Неудача — не повод для исключения.

    ⚠⚠ **Своей бедой не роняет приём сообщения**: оно к этому моменту уже лежит
    в `message`, и исключение отсюда отменило бы запись. Не ушло — строка в
    журнале, история у соседа на реплику короче; это несравнимо дешевле потери
    самого сообщения.

    ⚠ Ключи собираются теми же функциями, что и для вызова агента
    (`agent_vars`, `companion_vars_known`): склейка `{номер}_{имя}` у соседа
    одна на все дороги, и второе правило разошлось бы с первым молча.

    ⚠ `said_at` — **время реплики**, а не время доклада. Без него сосед ставит
    своё «сейчас», и привезённая из панели переписка легла бы в ленту одним
    числом: агент увидел бы недельный разговор случившимся в одну минуту.
    """
    try:
        from src.dating.agent import agent_vars
        from src.dating.companion_card import companion_vars_known
        from src.rhapsody.rhapsody_api import rhapsody_api_chat_add

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s LIMIT 1',
                             (int(profile_id),))
            profile = await db.fetchone()

        if not profile:
            return False

        girl = agent_vars(profile, _card(profile.get('profile')))
        # ⚠⚠ Имя спрашиваем **у карточки**, а не берём из реплики: `sender_name`
        # приходит пустым почти всегда (99% входящих на проде), и ключ выходил
        # вырожденным — `663532_663532` рядом с живым `663532_youpikeke`. Довод
        # целиком — в докстринге `companion_vars_known`.
        companion = await companion_vars_known(profile.get('site_id'),
                                               message['sender_uid'],
                                               message.get('sender_name'))

        # ⚠ Проверяется только собеседник: у анкеты ключ есть всегда —
        # `agent_site_uid` падает до нашего внутреннего номера и пусто не
        # возвращает никогда (так же, как при вызове агента, и ключ у обеих
        # дорог обязан быть один). А вот номер собеседника бывает пуст, и
        # тогда класть реплику некуда.
        if not companion.get('companion_id'):
            return False

        # ⚠⚠ Стороны едут **частями**, а сшивает их сосед — тем же правилом,
        # которым сшивает их шаг прогона. Прежде ключ склеивали здесь, и
        # правило жило в двух домах: у него и у нас. Поменяй он разделитель —
        # дорога писем поехала бы за ним, а эта осталась, и пара расщепилась
        # бы надвое молча.
        #
        # ⚠ **Его половина — `question`.** Прежде у приёма была своя дверь с
        # доводом `text`, и чья это реплика, говорило имя ручки. Теперь дверь
        # одна на все дороги, и говорит это довод.
        await rhapsody_api_chat_add(
            str(girl['girl_id']), str(girl['girl_name']),
            str(companion['companion_id']), str(companion['companion_name']),
            question=str(message['text']), kind='chat',
            source_uid=str(message.get('message_uid') or ''),
            said_at=str(message.get('sent_at') or ''))

        return True
    except Exception as e:
        logger_info(f'[dating] входящее не уехало в историю: {e}')

        return False


async def chat_history_said(site_id: int, take: int = 0) -> int:
    """Докладывает соседу то, что сказали мы, а записать было некому.

    Args:
        site_id: сайт, чьи анкеты смотрим.
        take: сколько строк доложить за заход. Ноль — потолок по умолчанию.
            ⚠ Константа читается **внутри**, а не стоит умолчанием довода:
            умолчание вычисляется на импорте модуля, и правка константы потом
            не действует — молча, что здесь же и поймал сторож потолка.

    Returns:
        int: сколько доложили.

    ## ⚠⚠⚠ Зачем не хватило правила «докладываем то, что легло»

    Входящие докладываются при записи: легло — значит у нас этого не было, не
    было у нас — не было и у соседа. С **исходящими** так нельзя, и это
    замерено, а не предположено. Строка из панели ложится и тогда, когда она
    наша:

    * **инвайт.** В `message` он при отправке не пишется (пока человек молчит,
      разговора нет) и переносится туда первым ответом — то есть **позже**,
      чем панельная строка с тем же текстом успевает лечь;
    * **наша отправка в полёте.** Между «сайт принял кадр» и записью проходят
      секунды, и заход импорта в этот промежуток помещается.

    В обоих случаях пара появится, но **позже вставки**. Поэтому отбор идёт не
    по «легло только что», а по трём признакам сразу.

    ## Чем отличаем чужое

    1. **Выдержка** (`CHAT_HISTORY_SAID_WAIT_SEC`) — строка отлежалась, и
       отправка, если она была нашей, давно записала свою;
    2. **нет близнеца** — общим правилом `message_twin_skip`, тем же, которым
       показ прячет отражения. Второй копии правила здесь не заводится;
    3. **разговор есть** — человек хоть раз писал этой анкете. Инвайт, на
       который не ответили, разговором не считается (`chat.py`,
       `chat_invites_to_history`), и у соседа ему места нет: лента из одних
       наших слов — монолог, а не история.

    ⚠ Отметка `history_at` ставится **только доложенному**: не ушло — строка
    остаётся в очереди и уедет следующим заходом. Пометь мы её заранее,
    молчание соседа стоило бы реплики навсегда.

    ⚠ Ничего не бросает: заход импорта обходит сайты подряд, и исключение
    отсюда лишило бы доклада остальные.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT m.id, m.profile_id, m.sender_uid, m.sender_name,'
                '       m.text, m.message_uid, m.created_at'
                '  FROM message m'
                '  JOIN profile p ON p.id = m.profile_id'
                ' WHERE p.site_id = %s'
                "   AND m.source = 'panel' AND m.direction = 'out'"
                '   AND m.history_at IS NULL'
                '   AND m.created_at < NOW() - INTERVAL %s SECOND'
                # Разговор есть: он хоть раз писал этой анкете.
                '   AND EXISTS (SELECT 1 FROM message him'
                '                WHERE him.profile_id = m.profile_id'
                '                  AND him.sender_uid = m.sender_uid'
                "                  AND him.direction = 'in')"
                + message_twin_skip('m') +
                ' ORDER BY m.created_at LIMIT %s',
                (int(site_id), int(CHAT_HISTORY_SAID_WAIT_SEC),
                 int(take or CHAT_HISTORY_SAID_MAX)))
            rows = await db.fetchall() or []
    except Exception as e:
        logger_info(f'[dating] сказанное оператором не спросилось: {e}')

        return 0

    told = 0
    for row in rows:
        if await _said_one(row):
            told += 1

    if told:
        logger_info(f'[dating] сайт #{site_id}: доложено соседу реплик, '
                    f'написанных мимо нас: {told}')

    return told


# ── Приватное ────────────────────────────────────────────────────────────────


async def _said_one(row: dict) -> bool:
    """Докладывает одну нашу реплику и метит её. Неудача — `False` и журнал.

    ⚠ Стороны едут **частями**, а сшивает их сосед: склей мы ключ здесь,
    правило легло бы в два места и разошлось бы молча.
    """
    try:
        from src.dating.agent import agent_vars
        from src.dating.companion_card import companion_vars_known
        from src.rhapsody.rhapsody_api import rhapsody_api_chat_add

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s LIMIT 1',
                             (int(row['profile_id']),))
            profile = await db.fetchone()

        if not profile:
            return False

        girl = agent_vars(profile, _card(profile.get('profile')))
        companion = await companion_vars_known(profile.get('site_id'),
                                               row['sender_uid'],
                                               row.get('sender_name'))

        if not companion.get('companion_id'):
            return False

        made = await rhapsody_api_chat_add(
            str(girl['girl_id']), str(girl['girl_name']),
            str(companion['companion_id']), str(companion['companion_name']),
            # ⚠ Это **наша** половина: оператор писал от имени анкеты.
            answer=str(row['text'] or ''), kind='chat',
            source_uid=str(row['message_uid'] or ''),
            # ⚠ Дошло: строка взята из журнала самого сайта, то есть сайт её
            # принял и показал. Это не предположение о доставке, а её след.
            delivered=True,
            said_at=str(row['created_at'] or ''))

        # ⚠⚠ Ноль — это **неудача**, а не «уже было». Повтор сосед узнаёт по
        # `source_uid` и отвечает номером **уже записанной** строки, то есть
        # ненулём. Прими мы ноль за успех, молчание соседа стоило бы реплики
        # навсегда: отметка встала бы, а в истории её бы не было.
        if not made:
            return False

        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE message SET history_at = NOW()'
                ' WHERE id = %s AND history_at IS NULL', (int(row['id']),))
            await db.connection.commit()

        return True
    except Exception as e:
        logger_info(f"[dating] реплика #{row.get('id')} не уехала "
                    f'в историю: {e}')

        return False


def _card(raw) -> dict:
    """Карточка анкеты словарём: в базе она JSON-столбцом, в тестах — уже готовой."""
    if isinstance(raw, str):
        try:
            return json.loads(raw or '{}') or {}
        except ValueError:
            return {}

    return raw or {}
