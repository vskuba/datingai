"""Автоответ: анкета отвечает сама, пока она подключена.

Кнопка «Ответить» — запасной путь, для случая «сделай сейчас и при мне». Основная
работа идёт здесь: смена держателя чатов (`chat_hold`) раз в минуту
обходит подключённые анкеты и отвечает тем, чьё сообщение осталось без ответа.

**Выключатель один — у сайта** (`site_.chat_answer_on`, «отправка ответов» на
вкладке «Диалоги»). Флаг анкеты `auto_answer` больше не читается: два
выключателя на один вопрос рождают вопрос «а какой из них сейчас?», и на проде
это стоило трёх часов молчания при поднятом выключателе сайта.

К агенту ходим в RhapsodyAi служебным токеном (`RHAPSODY_TOKEN`): у воркера нет
ни сессии, ни заголовков исходного запроса — он живёт сам по себе, и представиться
ему нечем, кроме собственной учётки.

**Раз в минуту, а не чаще.** Ограничение самого сайта: «You can send a second
message after 1 minute». Частить — значит копить отказы вместо ответов.
"""

import json

from logging_.logging_ import logger_info

from src.dating.agent import agent_id
from src.dating.answer import answer_run

# Как часто обходить анкеты. Сайт не принимает больше одного сообщения в минуту
# одному собеседнику, поэтому чаще бессмысленно.
AUTO_PERIOD_SEC = 60

# Сколько диалогов отвечаем за круг у одной анкеты. Каждый — прогон workflow с
# LLM; десяток растянул бы круг на полчаса и задержал бы всех остальных.
AUTO_PER_PROFILE = 2


async def auto_round(profiles: list) -> dict:
    """Один круг автоответа по подключённым анкетам.

    Args:
        profiles: то же, что держит смена — по элементу на анкету с её настройкой сайта.

    Returns:
        dict: `profiles` — сколько анкет обошли, `sent` — сколько ответов ушло,
        `silent` — сколько раз агент промолчал.
    """
    touched = sent = silent = 0

    for item in profiles:
        profile = item['profile']

        # ⚠⚠ **Выключатель автоответа теперь один, и он у сайта.**
        #
        # Здесь стояла вторая проверка — `profile.auto_answer`, флаг анкеты. Из
        # интерфейса его убрали ещё при переезде выключателя на сайт
        # (`site_.chat_answer_on`, «отправка ответов» на вкладке «Диалоги»), и в
        # комментариях страниц записано «круг его больше не читает». Читал.
        #
        # Стоило это живого молчания: на проде у двенадцати анкет из тринадцати
        # столбец остался в нуле — таким его завели до переезда, — и агент не
        # отвечал никому, хотя выключатель сайта был поднят, а галочки, которой
        # это можно поправить, в панели нет. Диалог Oksana ↔ Gerard простоял так
        # три часа.
        #
        # ⚠ `enabled` остаётся, и он про другое: «ведём ли анкету вовсе». Стоит
        # до `_agent_id`, потому что поход к соседу за агентом стоит запроса, а
        # ответ LLM — денег.
        if not int(profile.get('enabled', 1) or 0):
            continue

        if not agent_id:
            # Агента нет — значит анкету ещё не проверяли. Молчим: заводить его
            # здесь означало бы делать это без ведома человека.
            continue

        try:
            result = await answer_run(profile, item['presence'], agent_id,
                                             limit=AUTO_PER_PROFILE)
        except Exception as e:
            logger_info(f"[dating] автоответ #{profile['id']}: {e}")
            continue

        if result['asked']:
            touched += 1
            sent += result['sent']
            silent += result['silent']

    if touched:
        logger_info(f'[dating] автоответ: анкет {touched}, отправлено {sent}, промолчал {silent}')

    return {'profiles': touched, 'sent': sent, 'silent': silent, 'error': ''}


async def _answer_on(site_id: int) -> bool:
    """Отвечает ли агент в чате этого сайта.

    ⚠ **Не прочиталось — не отвечаем.** Молчание стоит задержки до следующего
    круга (минута), а лишний ответ уходит живому человеку от имени девушки, и
    отменить его нельзя. Та же сторона ошибки, что у прочих выключателей наружу.

    Спрашиваем на каждую анкету, а не раз на круг: сайтов у смены немного, запрос
    дешёвый, а кэш пришлось бы гасить при щелчке по галочке — иначе поднятый
    выключатель ждал бы конца смены.
    """
    from mysql_.mysql_ import mysql_get_db_async

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT chat_answer_on FROM site'
                             ' WHERE site_id = %s', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] выключатель автоответа сайта #{site_id} '
                    f'не прочитался: {e}')

        return False

    return bool((row or {}).get('chat_answer_on'))


async def _agent_id(profile: dict) -> int:
    """Агент анкеты у соседа. Ищем по имени: его могли удалить или переименовать."""
    card = profile.get('profile')
    if isinstance(card, str):
        try:
            card = json.loads(card or '{}')
        except Exception:
            card = {}

    return await agent_id(profile, card or {})
