"""Полная проверка учётки: вход → чат → чтение → отправка → закрытие.

Кнопка «Проверка» на странице отвечает на один вопрос: работает ли анкета целиком,
а не «отвечает ли сайт». Поэтому шаги идут ровно в том порядке, в каком их прошёл
бы человек, и каждый пишет в журнал (`dating_log`) свой итог — по нему видно, где
именно встало, без чтения кода.

Шаг падает — проверка останавливается. Продолжать после неудачного входа значит
получить лавину ошибок вместо одной настоящей; это то же правило, что у шагов
сценария и у звеньев цепочки.

Тестовая отправка идёт последней и **не обязана** проходить: на этом сайте письмо
стоит денег, и отказ «нет средств» — это исправная работа, а не поломка. Такой
итог пишется в журнал как `info`, а не как провал.
"""

import time

from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_chat import dating_chat_listen
from src.dating.dating_log import dating_log_write
from src.dating.dating_presence import dating_presence_probe

# Сколько слушать чат в проверке: за это время на живой учётке приходило по
# 10-15 сообщений, и дольше держать человека у экрана незачем.
DATING_CHECK_LISTEN_SEC = 25

# Текст пробного сообщения. Нейтральный: он уйдёт живому человеку, если отправка
# вдруг пройдёт, и «test 123» в чужой переписке выглядит дико.
DATING_CHECK_TEST_TEXT = 'Hello! How are you today?'


async def dating_check_run(profile: dict, presence: dict, ws_capture) -> dict:
    """Гонит полную проверку учётки, записывая каждый шаг в журнал.

    Args:
        profile: строка `dating_profile`.
        presence: настройка опроса сайта (`browser_site.presence`).
        ws_capture: как снять адрес чат-сокета — зовётся, если его нет.

    Returns:
        dict: `ok` — дошли ли до конца, `steps` — что сделали, `error` — на чём встали.
    """
    profile_id = int(profile['id'])
    steps = []
    started = time.monotonic()

    await dating_log_write(profile_id, 'info', 'Проверка запущена')

    # 1. Вход: без него остальное бессмысленно.
    probe = await dating_presence_probe(profile, presence)
    if probe['status'] != 'online':
        await dating_log_write(profile_id, 'fail', f"Вход не подтверждён: {probe['error']}")
        return _done(steps, False, probe['error'], started)
    steps.append('вход')
    await dating_log_write(profile_id, 'ok', f"Вход подтверждён сайтом ({probe['ms']} мс)")

    if probe['unread'] >= 0:
        await dating_log_write(profile_id, 'info', f"Непрочитанных писем в почте: {probe['unread']}")

    # 2. Чат: адрес сокета живёт не вечно, поэтому снимаем, если его нет.
    ws_url = str(profile.get('ws_url') or '')
    if not ws_url:
        await dating_log_write(profile_id, 'info', 'Снимаю адрес чат-сокета браузером')
        ws_url = await ws_capture(profile)
        profile = {**profile, 'ws_url': ws_url}

    if not ws_url:
        await dating_log_write(profile_id, 'fail', 'Чат не открылся: адрес сокета не снялся')
        return _done(steps, False, 'адрес чат-сокета не снят', started)
    steps.append('чат открыт')
    await dating_log_write(profile_id, 'ok', 'Чат открыт, подключение установлено')

    # 3. Чтение: слушаем сокет и складываем входящие.
    listen = await dating_chat_listen(profile, seconds=DATING_CHECK_LISTEN_SEC)
    if listen['error'] and not listen['frames']:
        await dating_log_write(profile_id, 'fail', f"Чтение чата не пошло: {listen['error']}")
        return _done(steps, False, listen['error'], started)

    steps.append('чтение')
    await dating_log_write(
        profile_id, 'ok',
        f"Чтение чата: кадров {listen['frames']}, новых сообщений {listen['saved']}")

    # Сами сообщения в журнал пишет приём (`dating_chat`): их ловит и держатель
    # сокетов, и человек должен видеть их в ленте в обоих случаях.
    if not listen['saved']:
        await dating_log_write(profile_id, 'info', 'Новых сообщений за время проверки не пришло')

    # 4. Пробная отправка. Не прошла — это не провал проверки: сайт может брать
    # за сообщение деньги, и отказ по балансу означает, что механика цела.
    sent = await _test_send(profile_id, presence, profile)
    steps.append('отправка' if sent['ok'] else 'отправка (отказ сайта)')

    # 5. Закрытие: сокет закрывается сам выходом из `dating_chat_listen`, а
    # временную сессию браузера гасит сам перехват адреса. Отмечаем явно, чтобы
    # в журнале был виден конец работы, а не обрыв.
    await dating_log_write(profile_id, 'ok', 'Сессия закрыта, проверка завершена')

    return _done(steps, True, '', started)


async def _test_send(profile_id: int, presence: dict, profile: dict) -> dict:
    """Пробная отправка сообщения последней написавшей.

    Кому писать, берём из журнала сообщений: слать в пустоту нельзя, а живой
    собеседник у активной анкеты всегда найдётся.
    """
    async with mysql_get_db_async() as db:
        # Только входящие: в этой же таблице лежит отправленное нами, и ответ
        # самому себе — не проверка отправки.
        await db.execute("SELECT sender_uid FROM dating_message WHERE profile_id = %s "
                         "  AND direction = 'in' ORDER BY id DESC LIMIT 1", (profile_id,))
        row = await db.fetchone()

    if not row:
        await dating_log_write(profile_id, 'info', 'Пробная отправка пропущена: некому писать')
        return {'ok': False, 'skipped': True}

    to_uid = str(row['sender_uid'])
    send = presence.get('send')
    if not send:
        await dating_log_write(
            profile_id, 'info',
            'Пробная отправка пропущена: у сайта не настроена ручка отправки (presence.send)')
        return {'ok': False, 'skipped': True}

    from src.dating.dating_send import dating_send_message

    result = await dating_send_message(profile, presence, to_uid, DATING_CHECK_TEST_TEXT)
    if result['ok']:
        await dating_log_write(profile_id, 'out', f'#{to_uid}: {DATING_CHECK_TEST_TEXT}')
        await dating_log_write(profile_id, 'ok', 'Пробная отправка принята сайтом')
    else:
        # Отказ сайта — не поломка нашей стороны, и в журнале он не красный.
        await dating_log_write(profile_id, 'info', f"Пробная отправка не прошла: {result['error']}")

    return result


def _done(steps: list, ok: bool, error: str, started: float) -> dict:
    return {'ok': ok, 'steps': steps, 'error': error,
            'ms': int((time.monotonic() - started) * 1000)}
