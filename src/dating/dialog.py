"""Переписка со страницы чата: кто написал и что именно.

Живой сокет отдаёт только то, что приходит **при нём**. Всё, что пришло раньше —
пока держателя не было, пока адрес протух, пока учётку не завели, — в сокет уже не
вернётся: сайт кладёт такое в «Missed chat», а сама переписка остаётся в чате.
Достать её можно только оттуда, где она нарисована, — со страницы.

Поэтому здесь браузер и сценарии, а не HTTP: один сценарий отдаёт список
собеседников из вкладки ACTIVE, второй открывает диалог и снимает обе половины
переписки. Что именно нажимать — в сценарии (`browser_scenario`), а не в Python:
вёрстка чата меняется, и чинить её должен тот, кто правит сценарии.

**Кто что сказал, выводится совмещением двух списков.** Сценарий снимает всю
ленту и отдельно свои реплики; свои — подпоследовательность общей, поэтому
совпадение ищется по порядку. Напрямую по классу контейнера не выходит: сайт
складывает подряд идущие реплики одной стороны в один блок, и пар
«текст ↔ класс» из этого не получить. Единственная неточность — повторяющиеся
слово в слово реплики обеих сторон: их можно отнести не туда.

Читается это **редко и по кнопке**: страница чата дорога (вкладка ~143 МБ) и, что
важнее, она отбирает у учётки живой сокет — сайт держит одно соединение на анкету.
Постоянный приём — работа держателя (`chat_hold`), а не этого модуля.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from src.dating.people import people_name_case

from src.dating.scenario import scenario_find

from src.dating.log import log_message_in, log_write
from src.dating.role import ROLE_DEFAULT, role_scenario

# Сколько диалогов читаем за раз. ACTIVE у живой анкеты — единицы, но открытие
# каждого стоит секунд, и полсотни превратили бы кнопку в получасовое ожидание.
DIALOG_LIMIT = 10

# Ключ сообщения, снятого со страницы. У страницы своего идентификатора нет —
# собираем стабильный из номера собеседника и места в переписке: при повторном
# чтении он тот же, и `INSERT IGNORE` не плодит копии.
DIALOG_UID = 'page:{uid}:{no}'


async def dialog_sync(profile: dict, presence: dict, session_id: str, runner) -> dict:
    """Читает все диалоги учётки: список собеседников, затем каждую переписку.

    Returns:
        dict: `contacts` — сколько диалогов прочитали, `saved` — сколько новых
        сообщений записали, `names` — с кем, `error` — если не вышло начать.
    """
    profile_id = int(profile['id'])
    role = str(profile.get('role') or ROLE_DEFAULT)

    names = await _names_read(profile, presence, session_id, runner, role)
    if names['error']:
        await log_write(profile_id, 'fail', f"Список диалогов не прочитался: {names['error']}")
        return {'contacts': 0, 'saved': 0, 'names': [], 'error': names['error']}

    if not names['names']:
        await log_write(profile_id, 'info', 'Диалогов в чате нет')
        return {'contacts': 0, 'saved': 0, 'names': [], 'error': ''}

    await log_write(profile_id, 'info',
                           f"Диалогов найдено: {len(names['names'])} — {', '.join(names['names'][:6])}")

    saved = 0
    read = 0
    for name in names['names'][:DIALOG_LIMIT]:
        result = await dialog_read(profile, presence, session_id, runner, name, role)
        if result['error']:
            await log_write(profile_id, 'fail', f'Диалог с «{name}» не прочитался: {result["error"]}')
            continue
        saved += result['saved']
        read += 1

    return {'contacts': read, 'saved': saved, 'names': names['names'], 'error': ''}


async def dialog_read(profile: dict, presence: dict, session_id: str, runner,
                             companion: str, role: str = '') -> dict:
    """Открывает один диалог и складывает переписку в базу."""
    profile_id = int(profile['id'])
    role = role or str(profile.get('role') or ROLE_DEFAULT)

    name = role_scenario(presence, 'dialog_read_scenario', role)
    scenario = await scenario_find(int(profile['site_id']), name) if name else None
    if not scenario:
        return {'saved': 0, 'error': f'нет сценария чтения диалога для роли «{role}»'}

    result = await runner(scenario, session_id, {'companion': str(companion)})
    if result.get('status') == 'error':
        return {'saved': 0,
                'error': f"шаг {result.get('step_no', 0)}: {str(result.get('error') or '')[:200]}"}

    extract = result.get('extract') or {}
    uid = _uid_of(str(extract.get('head') or ''))
    if not uid:
        return {'saved': 0, 'error': 'номер собеседника со страницы не снялся'}

    who = people_name_case(
        str(extract.get('name') or '').split('\n')[0]) or str(companion)
    await _contact_save(profile_id, uid, who)

    flow = _flow_build(extract)
    if not flow:
        return {'saved': 0, 'error': 'переписка со страницы не снялась'}

    saved = await _messages_save(profile_id, uid, who, flow)

    incoming = [text for side, text in flow if side == 'in']
    await log_write(
        profile_id, 'ok',
        f'Диалог с {who} (#{uid}): реплик {len(flow)}, из них их {len(incoming)}, новых {saved}')

    # Сами реплики — отдельными строками: ради них диалог и открывали.
    for text in incoming[-4:]:
        await log_message_in(profile_id, uid, who, text)

    return {'saved': saved, 'error': ''}


async def _names_read(profile: dict, presence: dict, session_id: str, runner, role: str) -> dict:
    """Имена собеседников из вкладки ACTIVE."""
    name = role_scenario(presence, 'dialog_list_scenario', role)
    scenario = await scenario_find(int(profile['site_id']), name) if name else None
    if not scenario:
        return {'names': [], 'error': f'нет сценария списка диалогов для роли «{role}»'}

    result = await runner(scenario, session_id, {})
    if result.get('status') == 'error':
        return {'names': [],
                'error': f"шаг {result.get('step_no', 0)}: {str(result.get('error') or '')[:200]}"}

    return {'names': _lines((result.get('extract') or {}).get('names')), 'error': ''}


def _uid_of(head: str) -> str:
    """Номер собеседника из шапки диалога: сайт печатает его как «ID: 1439074»."""
    for line in head.split('\n'):
        digits = ''.join(ch for ch in line if ch.isdigit())
        if 'ID' in line.upper() and digits:
            return digits[:32]

    return ''


def _lines(value) -> list:
    return [line.strip() for line in str(value or '').split('\n') if line.strip()]


async def _contact_save(profile_id: int, uid: str, name: str) -> None:
    """Собеседник в списке карточки. Уже есть — обновляем имя, а не плодим строку."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO contact (profile_id, uid, name) VALUES (%s, %s, %s) '
            'ON DUPLICATE KEY UPDATE name = VALUES(name)',
            (profile_id, str(uid)[:32], str(name)[:190]))
        await db.connection.commit()


def _flow_build(extract: dict) -> list:
    """Лента реплик в порядке страницы: [(сторона, текст), …].

    Порядок берём из самой страницы, а не собираем из двух списков: диалог читают
    как разговор, и «сначала все её реплики, потом все наши» — это не разговор.
    Сторона выводится совмещением двух списков: всей ленты и только своих реплик.
    Класс контейнера для этого не годится — сайт складывает подряд идущие реплики
    одной стороны в один контейнер, и пар «текст ↔ класс» не получается.
    """
    texts = _lines(extract.get('flow'))
    ours = _lines(extract.get('flow_out'))
    if not texts:
        return []

    # Наши реплики — подпоследовательность общей ленты: они идут в том же порядке,
    # просто через одну. Поэтому совмещаем два списка по порядку, а не по номеру
    # строки: страница группирует подряд идущие реплики одной стороны в один
    # контейнер, и счёт по индексу разъезжался — свои реплики оказывались чужими.
    flow = []
    at = 0
    for text in texts:
        if at < len(ours) and ours[at] == text:
            flow.append(('out', text))
            at += 1
        else:
            flow.append(('in', text))

    return flow


async def _messages_save(profile_id: int, uid: str, who: str, flow: list) -> int:
    """Кладёт ленту как есть. Повторное чтение ничего не дублирует.

    Номер реплики входит в ключ, поэтому порядок в базе повторяет порядок на
    странице: выборка «последние десять» отдаёт последние десять разговора, а не
    последние десять одной стороны.
    """
    saved = 0
    # Отвечена та реплика, после которой мы что-то сказали. «Ответили хоть раз —
    # значит всё отвечено» было бы неправдой: собеседник почти всегда пишет
    # последним, и такая пометка гасила бы именно тот случай, ради которого она
    # и нужна.
    last_out = max((no for no, (side, _) in enumerate(flow) if side == 'out'), default=-1)

    try:
        async with mysql_get_db_async() as db:
            for no, (side, text) in enumerate(flow):
                answered = 1 if (side == 'out' or no < last_out) else 0
                await db.execute(
                    'INSERT INTO message '
                    '(profile_id, sender_uid, sender_name, text, message_uid, direction, is_answered) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s) '
                    'ON DUPLICATE KEY UPDATE is_answered = VALUES(is_answered)',
                    (profile_id, str(uid)[:32], str(who)[:190], str(text)[:2000],
                     DIALOG_UID.format(uid=uid, no=no + 1)[:64],
                     side, answered))
                # `rowcount` у обновления равен 2, у вставки — 1: считаем только новое.
                saved += int(db.rowcount == 1)
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] переписка не записалась: {e}')

    return saved


