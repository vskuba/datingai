"""Инициатива: девушка пишет первой, когда разговор затих.

## Зачем

Разговор кончается не словом «пока», а молчанием: мы ответили, он не ответил.
Автоответ такую пару не видит вовсе — он отвечает на входящее, а входящего нет.
Инициатива её поднимает: через настроенный срок девушка сама спрашивает что-то
новое.

## ⚠⚠ Чем это **не** является

Не инвайт: там первая реплика незнакомому человеку, здесь продолжение начатого
разговора. Не автоответ: тот отвечает на сказанное, а здесь отвечать не на что.

Ближе всего механизм к тому, что уже делает роль `man` (`ANSWER_NUDGE_PROMPT`):
она возвращается после тишины сама. Инициатива — то же самое для женской анкеты,
только со сроком и числом заходов, которые задаёт сайт.

## Два предела, и оба нужны

| Настройка | Что задаёт |
|-----------|------------|
| `chat_initiative_after` | через сколько минут тишины пишем |
| `chat_initiative_repeat` | сколько раз подряд возвращаемся |

⚠ **Предел числа заходов важнее срока.** Молчание — тоже ответ, и возвращаться к
нему без счёта значит писать в пустоту от имени живой девушки: три подряд «ты
тут?» выдают автомат надёжнее любого текста.

⚠ **Его ответ обнуляет счёт.** Заговорил — разговор жив, и следующая тишина
начинается сначала. Считаем по ушедшим карточкам **после** его последней реплики.
"""

import random

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.agent import agent_id
from src.dating.answer import _agent_ask, _me_name
from src.dating.chat_initiative import (chat_initiative_put,
                                        chat_initiative_waiting)
from src.dating.companion_ref import companion_ref_foreign, companion_title
from src.dating.json_column import json_column_load
from src.dating.log import log_write

# Настройки сайта и их умолчания.
INITIATIVE_AFTER_SETTING = 'chat_initiative_after'
INITIATIVE_AFTER_DEFAULT = 60
INITIATIVE_REPEAT_SETTING = 'chat_initiative_repeat'
INITIATIVE_REPEAT_DEFAULT = 2

# Сколько пар берём у одной анкеты за заход. Каждая — вызов LLM; десяток разом
# растянул бы круг на минуты и задержал бы остальные анкеты.
INITIATIVE_PER_PROFILE = 2

# Что просим у агента. ⚠ Это **вопрос агенту**, а не текст сообщения: сочиняет он.
#
# ⚠⚠ Про молчание говорим прямо. Не скажи мы — агент отвечает на последнюю реплику
# разговора так, будто она только что пришла, и девушка повторяет сказанное час
# назад.
INITIATIVE_PROMPT = ('Собеседник не ответил, разговор затих. Напиши ему сама, '
                     'приёмом из переменной `initiative_kind`.')

# ⚠⚠ **Приём выбираем мы, а не модель.** Соблазн написать «будь разной» велик и
# уже проверен на инвайтах: три шаблона из трёх начинались с «you're trouble».
# Модель не умеет быть разной по требованию — она держит один тон и возвращается к
# нему всегда. Разнообразие даёт жребий здесь, а шаг у соседа получает **одну**
# задачу и выполняет одну задачу.
#
# ⚠ Вес — второе число. Он не «важность», а частота: лёгкое и безобидное должно
# выпадать чаще острого. Чёрный юмор хорош раз в десяток реплик и ужасен каждую
# вторую; рассказ о своём дне не надоедает никогда.
#
# ⚠⚠ Формулировка — **задача агенту**, а не тема. «Пошути» он исполнит как угодно;
# «расскажи короткую смешную нелепость, которая случилась с тобой сегодня» — одним
# способом. Чем уже задача, тем меньше разброс по качеству.
INITIATIVE_KINDS = (
    ('расскажи короткую историю из своего дня — что-то обычное и живое: '
     'работа, дорога, кофе, сосед, погода. Без морали и без вопроса в конце', 14),
    ('задай ему лёгкий вопрос о нём: чем занят, как прошёл день, что за '
     'музыка или сериал сейчас. Один вопрос, не три', 13),
    ('пофлиртуй: намекни, что думала о нём, или скажи что-то тёплое — но '
     'коротко и без приторности', 12),
    ('подколи его по-доброму: поддразни за что-то, что он говорил раньше, '
     'или за мужские привычки вообще', 10),
    ('скажи что-то спорное и спроси, согласен ли он: про еду, привычки, '
     'сериалы, утро или вечер. Спор ради разговора, а не ссоры', 9),
    ('предложи игру: «выбери одно из двух» или «угадай». Дай ему готовый '
     'выбор, чтобы отвечать было легко', 8),
    ('расскажи короткую смешную нелепость, которая случилась с тобой '
     'сегодня. Смеяться надо над собой, а не над ним', 8),
    ('признайся в чём-то маленьком и личном: чего боишься, что откладываешь, '
     'какая привычка дурацкая. Коротко, без исповеди', 7),
    ('пожалуйся на скуку и спроси, чем себя занять. Просьба о совете — самый '
     'простой повод ответить', 6),
    ('спроси про его планы на вечер или выходные и скажи про свои', 6),
    # ⚠ Острое — с малым весом. Оно оживляет разговор и оно же его заканчивает,
    # если попадётся не тому человеку или попадётся дважды подряд.
    ('пошути мрачно и с самоиронией — чёрный юмор без жестокости и без '
     'политики. Одна фраза', 4),
    ('скажи откровенную глупость или нелепицу просто так, без повода — '
     'ту, что скорее рассмешит, чем озадачит', 3),
)


async def initiative_make(site_id: int, limit: int = 0) -> dict:
    """Набирает пул инициативы по сайту.

    Args:
        site_id: сайт-закладка.
        limit: сколько карточек завести за заход. Ноль — без предела.

    Returns:
        dict: `made` — сколько карточек завели, `skipped` — сколько пар пропущено,
        `errors` — что не вышло.
    """
    after = await _after(site_id)
    repeat = await _repeat(site_id)

    if after <= 0 or repeat <= 0:
        return {'made': 0, 'skipped': 0, 'errors': [],
                'why': 'инициатива выключена настройками сайта'}

    made = skipped = 0
    errors = []

    for profile in await _profiles(site_id):
        # ⚠ Агента спрашиваем **на анкету**, а не на пару: он один и тот же для
        # всех её разговоров, а поход к соседу за номером стоит запроса.
        agent = await _agent(profile)
        if not agent:
            continue

        for pair in await _quiet(int(profile['id']), after, repeat):
            if limit and made >= int(limit):
                return {'made': made, 'skipped': skipped, 'errors': errors,
                        'why': ''}

            got = await _one(profile, agent, pair)
            if got == 'made':
                made += 1
            elif got.startswith('ошибка:'):
                errors.append(got[7:])
            else:
                skipped += 1

    return {'made': made, 'skipped': skipped, 'errors': errors, 'why': ''}


async def _one(profile: dict, agent: int, pair: dict) -> str:
    """Одна пара: спросить агента и положить карточку. Исход — словами."""
    profile_id = int(profile['id'])
    uid = str(pair['uid'])
    name = str(pair.get('name') or '')
    who = f'{name} {uid}'.strip()

    # ⚠ Карточка у пары уже лежит — второй не заводим. Ключ базы это и так
    # запретит, но платить за вызов LLM перед отказом вставки незачем.
    if await chat_initiative_waiting(profile_id, uid):
        return 'есть'

    language = str(pair.get('language') or 'en')
    history = await _history(profile_id, uid)
    kind = initiative_kind()

    # ⚠⚠ `anyway=True` всегда. Инициатива, на которую агент промолчал, — заход,
    # потраченный впустую: пара останется затихшей, а счётчик повторов не
    # сдвинется. Судья при этом не перестаёт проверять текст, он лишь не вправе
    # выбрать молчание (`wf_dating_talk_answer_anyway`).
    text, error = await _agent_ask(agent, INITIATIVE_PROMPT, language, profile,
                                   history=history, uid=uid, name=name,
                                   anyway=True, initiative=kind)

    if error:
        await log_write(profile_id, 'fail',
                        f'Инициатива не сочинилась для {who}: {error}')

        return f'ошибка:{who}: {error}'

    if not text:
        # Агент промолчал — значит разговор и вправду кончен. Это законный исход,
        # и настаивать незачем: молчание тут решение, а не сбой.
        return 'молчит'

    # ⚠ Сторож чужого имени — тот же, что у ответов. Инициатива уходит живому
    # человеку так же, как ответ, и цена ошибки та же.
    alien = companion_ref_foreign(text, companion_title(name, uid))
    if alien:
        await log_write(profile_id, 'fail',
                        f'Инициатива не взята для {who}: чужое имя «{alien}»')

        return f'ошибка:{who}: в тексте чужое имя «{alien}»'

    await chat_initiative_put(int(profile.get('site_id') or 0), profile_id, uid,
                              name, text, int(pair.get('try_no') or 1))
    await log_write(profile_id, 'info',
                    f'Инициатива готова: {_me_name(profile)} → {who} '
                    f'(заход {int(pair.get("try_no") or 1)})')

    return 'made'


def initiative_kind() -> str:
    """Каким приёмом заговорить. Жребий по весам из `INITIATIVE_KINDS`.

    ⚠ Взвешенный, а не равномерный: лёгкое и безобидное должно выпадать чаще
    острого. Чёрный юмор хорош раз в десяток реплик и ужасен каждую вторую.
    """
    kinds = [one for one, _ in INITIATIVE_KINDS]
    weights = [weight for _, weight in INITIATIVE_KINDS]

    return random.choices(kinds, weights=weights, k=1)[0]


async def _quiet(profile_id: int, after_sec: int, repeat_max: int) -> list[dict]:
    """Затихшие пары этой анкеты: кому пора написать самим.

    ## ⚠⚠ Что считается затихшим

    Два случая, и второй неочевиден.

    **Последняя реплика наша**, и ей больше `after_sec`. Он не ответил — тишина.

    **Последняя реплика его, но агент решил промолчать** (`answer_silent_at`). Со
    стороны это тоже тишина: мы намеренно не ответили, он больше не писал.

    ⚠⚠ **Второй случай пришлось дописывать по живой паре.** Без него разговор
    выпадал из обоих занятий сразу: автоответ такую реплику не берёт — решение
    молчать записано и держит его отбор (`answer_silent_at IS NULL`), — а
    инициатива не брала, потому что последним говорил он. Пара оставалась мёртвой
    навсегда. Поймано на проде: Nadezhda ↔ Semen, тридцать три часа тишины после
    намеренного молчания.

    ⚠ **Реплика, ждущая ответа, сюда не попадает.** Пока `answer_silent_at` пуст,
    автоответ ещё придёт, и лезть туда инициативе нельзя — вышли бы два наших
    сообщения подряд на одну реплику.

    ## ⚠ Сколько заходов уже было

    Считаем **ушедшие карточки инициативы после его последней реплики**. Ответил —
    счёт обнулился сам, потому что граница сдвинулась.

    ⚠ Пары без единого его слова не берём вовсе: это ушедший инвайт, на который не
    ответили, и возвращаться к нему — работа инвайтов (`invite_repeat_after`), а
    не эта.
    """
    async with mysql_get_db_async() as db:
        # «Последняя реплика» считается по времени, а не по `MAX(id)`: импорт из
        # панели кладёт недельную историю сегодняшними номерами. Та же мерка, что
        # у автоответа и у списка диалогов — три места, и во всех правило одно.
        await db.execute(
            'SELECT t.sender_uid AS uid, t.last_at,'
            '       (SELECT c.name FROM companion c'
            '         WHERE c.site_id = p.site_id AND c.uid = t.sender_uid) AS name,'
            # Его последнее слово — от него считаем заходы.
            '       (SELECT MAX(x.created_at) FROM message x'
            '         WHERE x.profile_id = %s AND x.sender_uid = t.sender_uid'
            "           AND x.direction = 'in') AS his_last,"
            '       (SELECT m2.direction FROM message m2'
            '         WHERE m2.profile_id = %s AND m2.sender_uid = t.sender_uid'
            '         ORDER BY m2.created_at DESC, m2.id DESC LIMIT 1) AS last_side,'
            # ⚠ Решение молчать по **последней** реплике: оно и превращает
            # «неотвеченное» в «затихшее». Берём тем же порядком, что и сторону, —
            # иначе можно поймать метку от позапрошлой.
            '       (SELECT m3.answer_silent_at FROM message m3'
            '         WHERE m3.profile_id = %s AND m3.sender_uid = t.sender_uid'
            '         ORDER BY m3.created_at DESC, m3.id DESC LIMIT 1) AS last_silent'
            '  FROM (SELECT m.sender_uid, MAX(m.created_at) AS last_at'
            '          FROM message m WHERE m.profile_id = %s'
            '         GROUP BY m.sender_uid) t'
            '  JOIN profile p ON p.id = %s'
            ' WHERE t.last_at < NOW() - INTERVAL %s SECOND'
            ' ORDER BY t.last_at DESC LIMIT %s',
            (int(profile_id), int(profile_id), int(profile_id), int(profile_id),
             int(profile_id), int(after_sec), INITIATIVE_PER_PROFILE * 5))
        rows = await db.fetchall() or []

        got = []
        for row in rows:
            # ⚠ Последним говорил он — берём, **только если решено молчать**.
            # Пока решения нет, реплику ждёт автоответ, и это его работа.
            if str(row.get('last_side') or '') != 'out' and not row.get('last_silent'):
                continue

            # ⚠ Он не сказал ни слова — это неотвеченный инвайт, не наша работа.
            if not row.get('his_last'):
                continue

            await db.execute(
                'SELECT COUNT(*) AS n FROM chat_initiative'
                " WHERE profile_id = %s AND uid = %s AND status = 'sent'"
                '   AND sent_at > %s',
                (int(profile_id), str(row['uid']), row['his_last']))
            before = int(((await db.fetchone()) or {}).get('n') or 0)

            if before >= int(repeat_max):
                continue

            got.append({'uid': str(row['uid']), 'name': row.get('name') or '',
                        'try_no': before + 1})

            if len(got) >= INITIATIVE_PER_PROFILE:
                break

    return got


async def _profiles(site_id: int) -> list[dict]:
    """Анкеты сайта, от чьего имени пишем.

    ⚠ **Только те, что в сети.** Инициатива уходит в чат, а чат — это вход под
    анкетой; спящей девушке написать нечем. Тот же отбор, что у круга инвайтов и
    у избранных.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, profile, role, auto_answer_only'
            '  FROM profile'
            " WHERE site_id = %s AND enabled = 1 AND status = 'online'",
            (int(site_id),))
        rows = await db.fetchall()

    return [dict(row) for row in (rows or [])]


async def _agent(profile: dict) -> int:
    """Агент анкеты у соседа. Ноль — анкету ещё не проверяли, и мы молчим."""
    card = profile.get('profile')
    if isinstance(card, str):
        card = json_column_load(card) or {}

    try:
        return int(await agent_id(profile, card or {}) or 0)
    except Exception as e:
        logger_info(f"[dating] агент для инициативы #{profile.get('id')}: {e}")

        return 0


async def _history(profile_id: int, uid: str) -> str:
    """Разговор пары для агента. ⚠ Без него он не знает, о чём уже говорили."""
    from src.dating.talk_history import talk_history_text

    return await talk_history_text(int(profile_id), str(uid))


async def _after(site_id: int) -> int:
    """Через сколько **секунд** тишины пишем. Настройка сайта — в минутах."""
    from src.uvicorn.routers.dating.site_ import site_seconds

    try:
        minutes = int(await site_seconds(int(site_id or 0),
                                         INITIATIVE_AFTER_SETTING))
    except Exception as e:
        logger_info(f'[dating] срок инициативы не прочитался: {e}')

        return INITIATIVE_AFTER_DEFAULT * 60

    return max(0, minutes) * 60


async def _repeat(site_id: int) -> int:
    """Сколько заходов подряд разрешено. Ноль — занятие выключено."""
    from src.uvicorn.routers.dating.site_ import site_seconds

    try:
        return max(0, int(await site_seconds(int(site_id or 0),
                                             INITIATIVE_REPEAT_SETTING)))
    except Exception as e:
        logger_info(f'[dating] число заходов инициативы не прочиталось: {e}')

        return INITIATIVE_REPEAT_DEFAULT
