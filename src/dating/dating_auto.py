"""Автоответ: анкета отвечает сама, пока она подключена.

Кнопка «Ответить» — запасной путь, для случая «сделай сейчас и при мне». Основная
работа идёт здесь: смена держателя чатов (`dating_chat_hold`) раз в минуту
обходит подключённые анкеты и отвечает тем, чьё сообщение осталось без ответа.

**Включено у анкеты по умолчанию** (`dating_girl.auto_answer`): её заводят, чтобы
она разговаривала, а не чтобы за ней ходили с кнопкой. Выключатель — у каждой
анкеты свой, потому что и решение своё: одну ведут вручную, другая отвечает сама.

К агенту ходим в RhapsodyAi служебным токеном (`RHAPSODY_TOKEN`): у воркера нет
ни сессии, ни заголовков исходного запроса — он живёт сам по себе, и представиться
ему нечем, кроме собственной учётки.

**Раз в минуту, а не чаще.** Ограничение самого сайта: «You can send a second
message after 1 minute». Частить — значит копить отказы вместо ответов.
"""

import json
import time

from logging_.logging_ import logger_info

from src.dating.dating_agent import dating_agent_id
from src.dating.dating_answer import dating_answer_run

# Как часто обходить анкеты. Сайт не принимает больше одного сообщения в минуту
# одному собеседнику, поэтому чаще бессмысленно.
DATING_AUTO_PERIOD_SEC = 60

# Сколько диалогов отвечаем за круг у одной анкеты. Каждый — прогон workflow с
# LLM; десяток растянул бы круг на полчаса и задержал бы всех остальных.
DATING_AUTO_PER_GIRL = 2


async def dating_auto_round(girls: list) -> dict:
    """Один круг автоответа по подключённым анкетам.

    Args:
        girls: то же, что держит смена — по элементу на анкету с её настройкой сайта.

    Returns:
        dict: `girls` — сколько анкет обошли, `sent` — сколько ответов ушло,
        `silent` — сколько раз агент промолчал.
    """
    touched = sent = silent = 0

    for item in girls:
        girl = item['girl']
        if not int(girl.get('auto_answer') or 0):
            continue
        agent_id = await _agent_id(girl)
        if not agent_id:
            # Агента нет — значит анкету ещё не проверяли. Молчим: заводить его
            # здесь означало бы делать это без ведома человека.
            continue

        try:
            result = await dating_answer_run(girl, item['presence'], agent_id,
                                             limit=DATING_AUTO_PER_GIRL)
        except Exception as e:
            logger_info(f"[dating] автоответ #{girl['id']}: {e}")
            continue

        if result['asked']:
            touched += 1
            sent += result['sent']
            silent += result['silent']

    if touched:
        logger_info(f'[dating] автоответ: анкет {touched}, отправлено {sent}, промолчал {silent}')

    return {'girls': touched, 'sent': sent, 'silent': silent, 'error': ''}


async def _agent_id(girl: dict) -> int:
    """Агент анкеты у соседа. Ищем по имени: его могли удалить или переименовать."""
    profile = girl.get('profile')
    if isinstance(profile, str):
        try:
            profile = json.loads(profile or '{}')
        except Exception:
            profile = {}

    return await dating_agent_id(girl, profile or {})
