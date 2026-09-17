"""Адрес чат-сокета учётки: снять браузером и запомнить.

Один модуль на всех, а не копия в роутере и в держателе сокетов: адрес живёт в
`dating_profile.ws_url`, и две разошедшиеся копии кода дали бы самый неприятный сорт
ошибки — одна половина системы обновляет адрес, другая держится за старый.

Адрес собирает JS страницы чата, в разметке его нет (проверено: `/chat` отдаёт
8 КБ без единого упоминания сокета). Поэтому браузер нужен **один раз**: открыли,
подсмотрели, закрыли. Дальше соединение держит обычный клиент из Python — вкладка
Chromium стоит ~143 МБ, вебсокет-клиент десятки килобайт.

Токены в адресе привязаны к сессии сайта: новый вход обесценивает прежний адрес,
и снимать его приходится заново. Это нормальный ход событий, а не сбой.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Сколько ждать, пока страница поднимет сокет. Замеры: 10-20 секунд.
DATING_WS_WAIT_SEC = 30

# Перехват открывает страницу и ждёт — минуты хватает с запасом, дольше ждать
# нечего: не поднялся, значит не поднимется.
DATING_WS_TIMEOUT = 120.0


async def dating_ws_capture(profile: dict, presence: dict, site_url: str = '',
                            session_id: str = '') -> str:
    """Снимает адрес чат-сокета браузером и запоминает его у учётки.

    Args:
        profile: строка `profile` со `storage_state`.
        presence: настройка сайта; `chat_url` — что открывать, `chat_ws_mark` —
            по какой подстроке отличить чатовый сокет от служебных.
        site_url: адрес сайта — на случай, если `chat_url` не задан.
        session_id: снять в уже открытой сессии. Так делает вход: сайт держит одну
            сессию на анкету, и вторая вкладка ради перехвата выбила бы первую.

    Returns:
        str: адрес сокета либо пустая строка, если снять не вышло.
    """
    from src.rhapsody.rhapsody_api import rhapsody_api_ws_capture

    chat_url = str(presence.get('chat_url') or '').strip() or str(site_url or '')
    if not chat_url:
        return ''

    try:
        found = await rhapsody_api_ws_capture(
            url=chat_url,
            session_id=str(session_id or ''),
            # Прогрев главной: мужской кабинет не поднимает чат с прямого перехода.
            warmup_url=str(site_url or ''),
            # А чат там открывается только кнопкой — селектор задаёт сайт, потому
            # что у мужского и женского кабинета она разная.
            click_selector=str(presence.get('chat_button') or ''),
            storage_state=profile.get('storage_state') or {},
            wait_sec=DATING_WS_WAIT_SEC)
    except Exception as e:
        logger_info(f'[dating] адрес сокета не снялся: {e}')
        return ''

    # Из всех поднятых сокетов берём чатовый: страница поднимает и служебные.
    mark = str(presence.get('chat_ws_mark') or '').strip()
    sockets = [u for u in found if not mark or mark in u]
    url = sockets[0] if sockets else ''

    if url:
        await dating_ws_save(int(profile['id']), url)

    return url


async def dating_ws_save(profile_id: int, url: str) -> None:
    """Запоминает адрес сокета. Не бросает: адрес — кэш, а не источник истины."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET ws_url = %s WHERE id = %s',
                             (str(url), int(profile_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] адрес сокета не записался: {e}')
