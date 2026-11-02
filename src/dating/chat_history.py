"""Входящая реплика — в историю разговора у соседа.

Историю пары (`companion_session`) сосед собирает сам и отдаёт агенту лентой.
Писать её он умеет двумя дорогами: прогон workflow пишет обмен целиком, а
`rhapsody_api_chat_incoming` кладёт **одну реплику собеседника** — ту, на
которую мы ещё не ответили или не ответим вовсе.

## ⚠⚠ Зачем отдельным файлом: дорог две, а правило одно

Реплика приходит к нам двумя путями — вебсокетом (`chat.py`) и импортом из
панели агентства (`chat_panel.py`), — и докладывать её соседу надо одинаково.
Пока доклад жил приватной функцией внутри `chat.py`, панельная дорога не
докладывала **ничего**: у сайта без сокета — а это весь наш прод — агент видел
только ту реплику, на которую отвечал прямо сейчас, и не видел разговора.

Собрать ключи сторон по второму разу — ровно та ошибка, от которой стережёт
`companion_ref.py`: склейку `{номер}_{имя}` делает один шаг соседа на оба
конца, и второе правило разошлось бы с первым молча.

## ⚠ Исходящие сюда не идут, и это решение

Нашу реплику у соседа пишет та дорога, которая её сочинила: прогон `/chat/ask`
либо `mail_send_mark`. Привезённая из панели копия того же — **близнец**
(`message_twin.py`), и доклад завёл бы у соседа второй экземпляр сказанного.

Остаётся узкий случай — текст, написанный оператором прямо в панели сайта: его
у соседа не будет. Чинить это тем же способом нельзя: `chat/message/put`
времени не принимает и ставит «сейчас», то есть недельная переписка легла бы
сегодняшним числом. Здесь нужен либо срок у той ручки, либо ничего.
"""

import json

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async


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
        from src.rhapsody.rhapsody_api import rhapsody_api_chat_incoming

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

        await rhapsody_api_chat_incoming(
            f"{girl['girl_id']}_{girl['girl_name']}",
            f"{companion['companion_id']}_{companion['companion_name']}",
            message['text'], kind='chat',
            source_uid=str(message.get('message_uid') or ''),
            said_at=str(message.get('sent_at') or ''))

        return True
    except Exception as e:
        logger_info(f'[dating] входящее не уехало в историю: {e}')

        return False


def _card(raw) -> dict:
    """Карточка анкеты словарём: в базе она JSON-столбцом, в тестах — уже готовой."""
    if isinstance(raw, str):
        try:
            return json.loads(raw or '{}') or {}
        except ValueError:
            return {}

    return raw or {}
