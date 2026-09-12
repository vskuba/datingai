"""Агент анкеты: один универсальный на всех, имя приходит переменной.

⚠ **Своего агента у анкеты больше нет, и это смена подхода у соседа.** Отвечает
`dating_girl_universal` («Девушка») — один агент на все анкеты; кто именно
говорит, задаётся переменными вызова: `girl_name` — имя, `girl_id` — под каким
ключом лежат её факты. Промпт у агента написан под подстановку (`You are a girl
%girl_name%`), и подменять в нём нечего.

## Зачем так

Агент на анкету означал агента на каждую заведённую девушку: тринадцать анкет —
тринадцать записей у соседа, и каждая со своей копией того же промпта. Правка
поведения («не соглашайся на встречи») требовала обойти их все, а забытая анкета
продолжала вести себя по-старому — молча.

## ⚠ Что важно передавать на каждом вызове

| Переменная | Зачем | Что будет без неё |
|------------|-------|-------------------|
| `girl_name` | подставляется в промпт | все девушки назовутся `Natalia` — значением по умолчанию у агента |
| `girl_id` | ключ фактов (`document_id` в Qdrant) | факты всех анкет лягут в один документ и подмешаются друг другу |

Наши переменные в сборке scope идут **последними** и перекрывают и переменные
агента, и его `workflow_variable` — проверено по `chat_ask_ai` у соседа.

## ⚠ Сессии у чата нет, и это не упущение этого перехода

Workflow `dating_talk` умеет помнить разговор (`session_messages`, проверка
повтора), но опирается на `session_uuid`, а мы его не шлём — сосед заводит новый
на каждый запрос. Так было и с персональными агентами: память разговора у чата не
велась никогда, вопрос агенту содержит одну реплику. Переход на общего агента
этого не ухудшает, но и не чинит: захотим память — надо будет слать свой
`session_uuid` на пару «анкета + собеседник», и вот **тогда** общий агент без него
слил бы все разговоры в один.

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

import re

from src.dating.dating_log import dating_log_write
from src.dating.dating_role import DATING_ROLE_DEFAULT
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_agent_create,
                                       rhapsody_api_agent_find, rhapsody_api_site_get)

# Узел RhapsodyAi, под которым живут агенты анкет.
DATING_AGENT_NODE = 'Dating'

# Универсальный агент девушки у соседа. Имя системное — его же видно в его админке;
# подпись там «Девушка».
DATING_AGENT_UNIVERSAL = 'dating_girl_universal'

# Настройка сайта: каким агентом отвечать в чате. Пусто — универсальным.
#
# ⚠ Настройка, а не константа, потому что подход новый: у сайта, заведённого до
# перехода, персональные агенты остались живыми, и вернуться к ним должно быть
# можно правкой строки, а не выкладкой.
DATING_AGENT_SETTING = 'chat_agent'

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

# Метка сайта в поле `agent.site` у соседа: строчная латиница и цифры, `^[a-z0-9]+$`,
# длина до 50 (`AgentCreateRequest`). Не подойдёт формат — заведение отвечает `422`,
# поэтому чистим имя сами, а не надеемся, что справочник хранит его пригодным.
DATING_AGENT_SITE_MAX = 50
DATING_AGENT_SITE_RE = re.compile(r'[^a-z0-9]+')


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

    # Сайт спрашиваем до похода за агентом: у соседа поле обязательное, и без него
    # заведение отвечает `422`. Лучше сказать это своей строкой в журнале анкеты, чем
    # получить чужую ошибку валидации, по которой не видно, чего именно не хватило.
    site = await dating_agent_site(int(profile.get('site_id') or 0))
    if not site:
        error = 'у сайта анкеты нет пригодного имени — агенту нечего записать в site'
        await dating_log_write(profile_id, 'fail', f'Агент «{name}» не заведён: {error}')
        return {'name': name, 'id': 0, 'created': False, 'error': error}

    await dating_log_write(profile_id, 'info',
                           f'Завожу агента «{name}» по биографии ({len(bio)} знаков) '
                           f'через «{workflow}», сайт «{site}»')

    try:
        created = await rhapsody_api_agent_create(
            node=DATING_AGENT_NODE, workflow=workflow, name=name,
            title=str(card.get('name') or name)[:150], variables={'girl_bio': bio},
            site=site)
    except RhapsodyError as e:
        await dating_log_write(profile_id, 'fail', f'Агент «{name}» не завёлся: {e}')
        return {'name': name, 'id': 0, 'created': False, 'error': str(e)}

    agent_id = int(created.get('id') or 0)
    await dating_log_write(profile_id, 'ok', f'Агент «{name}» заведён (#{agent_id})')

    return {'name': name, 'id': agent_id, 'created': True, 'error': ''}


async def dating_agent_id(profile: dict, card: dict) -> int:
    """Номер агента, которым отвечает эта анкета. Нет такого — 0.

    ⚠ **Обычно это один и тот же агент на всех** — `dating_girl_universal`. Кто
    именно говорит, задаётся переменными вызова (`dating_agent_vars`), а не
    отдельной записью у соседа.

    ⚠ **Персональный агент остаётся запасным путём.** Сайт, где настройка
    `chat_agent` указывает на другое имя, пойдёт к нему; а если универсального у
    соседа не нашлось — ищем агента этой анкеты по прежнему имени
    (`natalia_1430119`). Второе не про совместимость ради неё самой: агенты анкет,
    заведённые до перехода, живы и со своими фактами, и молча перестать их звать
    значило бы потерять их историю без единого следа.

    Ищем по имени, а не по хранимой ссылке: агент живёт своей жизнью, его могли
    удалить или переименовать в админке RhapsodyAi, и единственный надёжный
    признак — существует ли он сейчас.
    """
    universal = await dating_agent_universal_name(int(profile.get('site_id') or 0))

    for name in (universal, dating_agent_name((card or {}).get('name') or '',
                                              str(profile['login']))):
        if not name:
            continue

        try:
            found = await rhapsody_api_agent_find(name)
        except RhapsodyError as e:
            logger_info(f'[dating] агент «{name}» не спросился: {e}')

            return 0

        if int((found or {}).get('id') or 0):
            return int(found['id'])

    return 0


async def dating_agent_universal_name(site_id: int) -> str:
    """Каким агентом отвечает этот сайт. Пустая настройка — универсальным."""
    from src.uvicorn.routers.dating.dating_site import dating_site_setting

    try:
        own = str(await dating_site_setting(int(site_id or 0),
                                            DATING_AGENT_SETTING) or '').strip()
    except Exception as e:
        logger_info(f'[dating] настройка агента сайта #{site_id} не прочиталась: {e}')

        return DATING_AGENT_UNIVERSAL

    return own or DATING_AGENT_UNIVERSAL


def dating_agent_vars(profile: dict, card: dict) -> dict:
    """Чем universal-агент узнаёт, кто он сейчас.

    Returns:
        dict: `girl_name` — имя анкеты с сайта, `girl_id` — ключ её фактов.

    ⚠ **`girl_id` обязателен, и это не перестраховка.** Workflow ищет факты
    девушки фильтром `{"document_id": "%girl_id%"}`, а у самого агента эта
    переменная одна на всех (`dating_girl_universal`). Не передай мы свою — факты,
    придуманные про одну анкету, лягут в общий документ и подмешаются остальным:
    Diana начала бы отвечать историями Natalia.

    ⚠ **Ключ — логин, а не имя.** Имена на сайте повторяются десятками (три
    Natalia в списке обычное дело), а логин уникален и не меняется вместе с
    анкетой. Тот же довод, по которому номер стоит в имени персонального агента.
    """
    login = str(profile.get('login') or '')
    name = str((card or {}).get('name') or '').strip()

    return {'girl_name': name or login,
            'girl_id': dating_agent_name(name, login)}


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


async def dating_agent_site(site_id: int) -> str:
    """Метка сайта анкеты для агента у соседа: `Goldenbride` → `goldenbride`.

    Берём имя из справочника сайтов RhapsodyAi (`browser_site`), а не держим свой
    список: сайты заводит человек на той стороне, и вторая копия имён разошлась бы
    с первой на первом же новом сайте. Номер сайта у анкеты уже есть
    (`dating_profile.site_id`) и ссылается ровно на этот справочник.

    Приводим к формату поля (`^[a-z0-9]+$`): в справочнике имя человеческое —
    `Goldenbride`, `Ladadate`, — а заведение агента такое имя отвергает с `422`.

    Сайт не нашёлся или имя после чистки пусто — пустая строка, и решает вызывающий.
    Подставлять что-нибудь вместо него нельзя: агент приписался бы к чужому сайту,
    а разбирать потом, откуда он взялся, было бы уже не по чему.
    """
    site = await rhapsody_api_site_get(int(site_id or 0))
    slug = DATING_AGENT_SITE_RE.sub('', str(site.get('name') or '').lower())

    return slug[:DATING_AGENT_SITE_MAX]


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
