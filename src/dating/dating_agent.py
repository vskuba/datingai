"""Агент под анкету: у каждой девушки свой, и заводится он сам.

Отвечать в чате будет агент, а не человек, поэтому у анкеты должен быть свой —
со своим именем, своей историей и своими фактами. Заводить его руками на каждую
новую анкету значит рано или поздно забыть, и анкета молча останется без ответов.

**Самого агента этот проект не хранит.** Агенты, workflow, факты и LLM живут в
RhapsodyAi; здесь только анкеты и чат. Поэтому «завести агента» означает позвать
`POST /agent/create` у соседа, а «есть ли он» — спросить его же список. Своей
таблицы агентов нет и быть не должно: вторая копия разошлась бы с первой в тот
день, когда агента переименуют или удалят в админке.

Имя агента собирается из имени анкеты и её номера: `natalia_1430119`. Номер в
имени не украшение — имена на сайте повторяются десятками (три Natalia в списке
собеседниц обычное дело), а имя агента обязано быть уникальным на всю установку
RhapsodyAi, где живут агенты всех проектов.

**Порядок обязателен: агент заводится последним.** Сначала учётка, вход, анкета —
и только потом агент, потому что делает его workflow `dating_girl_create`, и на
входе ему нужна биография. Из биографии он добывает факты: без неё получится
агент без единого факта, который на «где ты выросла» отвечает выдумкой вместо
своей же истории.
"""

from logging_.logging_ import logger_info

from src.dating.dating_log import dating_log_write
from src.dating.dating_role import DATING_ROLE_DEFAULT
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_agent_create,
                                       rhapsody_api_agent_find)

# Узел RhapsodyAi, под которым живут агенты анкет.
DATING_AGENT_NODE = 'Dating'

# Чем заводить агента — по роли анкеты. Словарь, а не одно имя: `dating_girl_create`
# лепит именно девушку (её промпт, её факты), и мужской анкете он не годится.
# Роли без своего workflow агента не получают — и в журнале сказано почему.
#
# Имя workflow и имя его подстановки (`girl_bio` ниже) — **чужие**: так они
# называются в базе RhapsodyAi. Переименование анкет здесь их не касается; менять
# их можно только вместе с соседом, иначе заведение агента молча перестанет
# находить workflow.
DATING_AGENT_WORKFLOW_CREATE = {'lady': 'dating_girl_create'}

# Латиница для кириллических имён: агент — системное имя, и `наталья_1430119`
# в нём читается хуже, чем `natalia_1430119`, а в путях и логах ещё и ломается.
DATING_AGENT_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e',
    'ю': 'yu', 'я': 'ya', 'і': 'i', 'ї': 'yi', 'є': 'ye', 'ґ': 'g',
}

# Имя агента — `char(50)` в базе RhapsodyAi. Режем сами, иначе заведение падает.
DATING_AGENT_NAME_MAX = 50


async def dating_agent_ensure(profile: dict, card: dict) -> dict:
    """Заводит агента анкеты в RhapsodyAi, если его там ещё нет.

    Порядок ровно такой: имя из анкеты → биография из анкеты → workflow. Анкета
    здесь не украшение, а сырьё: из биографии workflow добывает факты, и звать
    его до чтения анкеты бессмысленно.

    Args:
        profile: строка `dating_profile`.
        card: прочитанная анкета с сайта — из неё берётся имя и биография.

    Returns:
        dict: `name` — имя агента, `id` — его номер у соседа, `created` — завели ли
        сейчас, `error` — почему не вышло. Отсутствие агента не ломает проверку:
        это её последний шаг.
    """
    profile_id = int(profile['id'])
    name = dating_agent_name(card.get('name') or '', str(profile['login']))

    try:
        found = await rhapsody_api_agent_find(name)
    except RhapsodyError as e:
        await dating_log_write(profile_id, 'fail', f'Агента не спросить: {e}')
        return {'name': name, 'id': 0, 'created': False, 'error': str(e)}

    if found:
        await dating_log_write(profile_id, 'ok', f'Агент «{name}» уже есть (#{found.get("id")})')
        return {'name': name, 'id': int(found.get('id') or 0), 'created': False, 'error': ''}

    # Роль решает, чем заводить: `dating_girl_create` делает девушку — её промпт,
    # её факты. Мужской анкете он не подходит, и своего workflow у неё пока нет.
    role = str(profile.get('role') or DATING_ROLE_DEFAULT)
    workflow = DATING_AGENT_WORKFLOW_CREATE.get(role, '')
    if not workflow:
        error = f'для роли «{role}» нет workflow создания'
        await dating_log_write(profile_id, 'fail', f'Агент «{name}» не заведён: {error}')
        return {'name': name, 'id': 0, 'created': False, 'error': error}

    bio = dating_agent_bio(card)
    if not bio:
        error = 'в анкете нет биографии — сначала прочитайте анкету'
        await dating_log_write(profile_id, 'fail', f'Агент «{name}» не заведён: {error}')
        return {'name': name, 'id': 0, 'created': False, 'error': error}

    await dating_log_write(profile_id, 'info',
                           f'Завожу агента «{name}» по биографии ({len(bio)} знаков) '
                           f'через «{workflow}»')

    try:
        created = await rhapsody_api_agent_create(
            node=DATING_AGENT_NODE, workflow=workflow, name=name,
            title=str(card.get('name') or name)[:150], variables={'girl_bio': bio})
    except RhapsodyError as e:
        await dating_log_write(profile_id, 'fail', f'Агент «{name}» не завёлся: {e}')
        return {'name': name, 'id': 0, 'created': False, 'error': str(e)}

    agent_id = int(created.get('id') or 0)
    await dating_log_write(profile_id, 'ok', f'Агент «{name}» заведён (#{agent_id})')

    return {'name': name, 'id': agent_id, 'created': True, 'error': ''}


async def dating_agent_id(profile: dict, card: dict) -> int:
    """Номер агента анкеты у соседа. Нет такого — 0.

    Ищем по имени, а не по хранимой ссылке: агент живёт своей жизнью, его могли
    удалить или переименовать в админке RhapsodyAi, и единственный надёжный
    признак — существует ли он сейчас.
    """
    name = dating_agent_name((card or {}).get('name') or '', str(profile['login']))

    try:
        found = await rhapsody_api_agent_find(name)
    except RhapsodyError as e:
        logger_info(f'[dating] агент «{name}» не спросился: {e}')
        return 0

    return int((found or {}).get('id') or 0)


def dating_agent_bio(card: dict) -> str:
    """Биография анкеты — то, из чего workflow добудет факты.

    Сайт держит имя, возраст и город отдельными полями, а «о себе» пишет прозой.
    Отдать в workflow один текст «о себе» значило бы потерять самое проверяемое:
    там сплошь «я ценю честность», а где живёт и сколько лет — в соседних полях.
    Поэтому поля склеиваются в связный рассказ от первого лица — в таком виде
    биографию ждёт `dating_girl_create`.

    Язык английский: узел Dating объявляет `en`, и на нём же ведётся переписка.
    """
    name = str(card.get('name') or '').strip()
    city = str(card.get('city') or '').strip()
    about = str(card.get('about') or '').strip()

    try:
        age = int(card.get('age') or 0)
    except (TypeError, ValueError):
        age = 0

    parts = []
    if name:
        parts.append(f'My name is {name}.')
    if age:
        parts.append(f'I am {age} years old.')
    if city:
        parts.append(f'I live in {city}.')
    if about:
        parts.append(about)

    # Одно имя — ещё не биография: фактов из него не добыть, и агент вышел бы
    # пустым. Пусть лучше журнал скажет «сначала прочитайте анкету».
    if not about and not city and not age:
        return ''

    return ' '.join(parts)


def dating_agent_name(profile_name: str, login: str) -> str:
    """`Наталья` + `1430119` → `natalia_1430119`.

    Имя пустое — остаётся один номер: `1430119` хуже читается, но уникально и
    честно, а выдуманное имя пришлось бы потом отличать от настоящего.
    """
    latin = ''.join(DATING_AGENT_TRANSLIT.get(ch, ch) for ch in str(profile_name).lower())
    latin = ''.join(ch if ch.isalnum() else '_' for ch in latin)
    latin = '_'.join(part for part in latin.split('_') if part)

    digits = ''.join(ch for ch in str(login) if ch.isdigit()) or str(login)
    name = f'{latin}_{digits}' if latin else str(digits)

    return name[:DATING_AGENT_NAME_MAX]
