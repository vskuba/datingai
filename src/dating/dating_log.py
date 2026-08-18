"""Журнал работы по учётке — одна строка на событие.

Отдельным модулем, а не внутри проверки: писать в журнал нужно и проверке, и
держателю сокетов, и отправке. Держать функцию в `dating_check` значило бы, что
приём сообщений импортирует проверку, а проверка — приём: круг из импортов.

**Журнал не имеет права ронять работу.** Упавшая запись строки — это потерянная
строка, а не потерянное сообщение; поэтому здесь всё молча гасится.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Длина строки журнала. Тексты из чата бывают на страницу, а в ленте читаются
# первые слова — остальное видно в самой переписке.
DATING_LOG_TEXT_MAX = 2000

# Сообщение в ленте режем короче: лента отвечает на вопрос «кто написал и о чём»,
# а не заменяет переписку.
DATING_LOG_MESSAGE_MAX = 300


async def dating_log_write(girl_id: int, level: str, text: str) -> None:
    """Строка журнала. Не бросает: сбой журнала не должен ронять работу."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('INSERT INTO dating_log (girl_id, level, text) VALUES (%s, %s, %s)',
                             (int(girl_id), str(level or 'info'),
                              str(text or '')[:DATING_LOG_TEXT_MAX]))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] журнал: {e}')


async def dating_log_message_in(girl_id: int, sender: str, name: str, text: str) -> None:
    """Входящее сообщение в журнал: от кого и о чём.

    Имя, если сайт его прислал, иначе номер — по голому номеру человек собеседницу
    не узнаёт, а имя приходит не всегда.
    """
    who = str(name or '').strip() or f'#{sender}'
    body = str(text or '').strip().replace('\n', ' ')
    if len(body) > DATING_LOG_MESSAGE_MAX:
        body = body[:DATING_LOG_MESSAGE_MAX].rstrip() + '…'

    await dating_log_write(girl_id, 'in', f'{who}: {body}')
