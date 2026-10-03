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

## Три настройки, и каждая своё

| Настройка | Что задаёт |
|-----------|------------|
| `chat_initiative_after` | через сколько минут тишины пишем — **от** |
| `chat_initiative_after_max` | тот же срок — **до**; между ними жребий |
| `chat_initiative_repeat` | сколько раз за **сутки** возвращаемся к одной паре |

⚠ **Предел числа заходов важнее срока.** Молчание — тоже ответ, и возвращаться к
нему без счёта значит писать в пустоту от имени живой девушки: три подряд «ты
тут?» выдают автомат надёжнее любого текста.

⚠⚠ **Сутки скользящие, и его ответ счёт не обнуляет.** «Два в сутки» значит два
за последние 24 часа этой паре, сколько бы раз он ни отвечал между ними. Прежде
счёт шёл от его последней реплики и ответом обнулялся — то есть предел держал
один промежуток тишины, а не день: отвечающий через раз получал инициативу хоть
двадцать раз в сутки. Правило и мерка здесь те же, что у инвайтов
(`invite_repeat_max_day`): полночь ничего не значит для того, кто читает
сообщения.

⚠⚠ **Срок бросается жребием, и жребий устойчив на один промежуток тишины.**
Одно число на сайт означало бы, что все затихшие пары оживают через одинаковое
время после последней реплики — ряд сообщений по линейке. Разброс даёт `_due_sec`,
и он выводит срок **из самой пары**, а не из случая: круг просыпается каждую
минуту, и свежий жребий на каждом тике рано или поздно выпал бы низким — работала
бы одна «от», а «до» не значила бы ничего.
"""


import hashlib

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
# Верхняя граница срока. ⚠ Меньше нижней или ноль — значит «ровно нижняя»:
# человек, не желающий разброса, ставит ноль, а не повторяет число дважды.
INITIATIVE_AFTER_MAX_SETTING = 'chat_initiative_after_max'
INITIATIVE_AFTER_MAX_DEFAULT = 120
INITIATIVE_REPEAT_SETTING = 'chat_initiative_repeat'
INITIATIVE_REPEAT_DEFAULT = 2

# Сколько пар берём у одной анкеты за заход. Каждая — вызов LLM; десяток разом
# растянул бы круг на минуты и задержал бы остальные анкеты.
INITIATIVE_PER_PROFILE = 2

# ⚠⚠ **Приёма здесь больше нет, и это переезд, а не потеря.** Двенадцать приёмов
# с весами жили константой `INITIATIVE_KINDS` и жребием `initiative_kind()`: панель
# бросала и присылала готовое переменной `initiative_kind`.
#
# Теперь их бросает шаг «Жребий» у соседа (`…_wf_talk_dice_common.sql`), веса
# перенесены слово в слово. Причина не в красоте: список приёмов — часть **задания
# модели**, и лежать он должен рядом с промптом, который его исполняет. Здесь он
# был второй копией договора и разошёлся бы с промптом на первой правке.
#
# Отсюда уходит и входящая переменная: панель шлёт только повод (`make_initiative`).


async def initiative_make(site_id: int, limit: int = 0) -> dict:
    """Набирает пул инициативы по сайту.

    Args:
        site_id: сайт-закладка.
        limit: сколько карточек завести за заход. Ноль — без предела.

    Returns:
        dict: `made` — сколько карточек завели, `skipped` — сколько пар пропущено,
        `errors` — что не вышло.
    """
    after, after_max = await _after_range(site_id)
    repeat = await _repeat(site_id)

    # ⚠ Выключает занятие **нижняя** граница: ноль в ней значит «не возвращаться
    # к затихшим вовсе». Ноль в верхней — всего лишь «без разброса».
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

        for pair in await _quiet(int(profile['id']), after, after_max, repeat):
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

    # ⚠⚠ `anyway=True` всегда. Инициатива, на которую агент промолчал, — заход,
    # потраченный впустую: пара останется затихшей, а счётчик повторов не
    # сдвинется. Судья при этом не перестаёт проверять текст, он лишь не вправе
    # выбрать молчание (`wf_dating_talk_answer_anyway`).
    # ⚠ Историю не спрашиваем: обе — чат и письма — берёт сам `_agent_ask`.
    # ⚠⚠ **Вопрос пуст, и это правильно.** `prompt_user` у соседа означает «что
    # сказал собеседник», а он ничего не говорил — потому мы и пишем. Задание
    # «заговори первой, приёмом из жребия» целиком лежит в промпте ветки
    # «Инициатива»; дублировать его вопросом значило бы спорить с промптом за
    # внимание модели, а шаг записи положил бы нашу инструкцию в переписку от
    # имени человека.
    #
    # ⚠ Ветку выбирает флаг `initiative`, а не текст вопроса.
    text, error, message_id = await _agent_ask(agent, '', language, profile,
                                               uid=uid, name=name,
                                               anyway=True, initiative=True)

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

    # ⚠⚠ Номер обмена кладём в строку пула: карточку сочиняет этот круг, а
    # уносит отправкой другой (`initiative_round._send`), позже — когда
    # собеседник окажется в сети. Без своего места в строке номер до отправки
    # не дожил бы, и ушедшая инициатива осталась бы у соседа неотмеченной.
    await chat_initiative_put(int(profile.get('site_id') or 0), profile_id, uid,
                              name, text, int(pair.get('try_no') or 1),
                              message_id)
    await log_write(profile_id, 'info',
                    f'Инициатива готова: {_me_name(profile)} → {who} '
                    f'(заход {int(pair.get("try_no") or 1)})')

    return 'made'


async def _quiet(profile_id: int, after_sec: int, after_max_sec: int,
                 repeat_max: int) -> list[dict]:
    """Затихшие пары этой анкеты: кому пора написать самим.

    ## ⚠⚠ Что считается затихшим

    Два случая, и второй неочевиден.

    **Последняя реплика наша**, и ей больше срока пары. Он не ответил — тишина.

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

    ## ⚠ Сколько заходов уже было за сутки

    Считаем **ушедшие карточки инициативы этой паре за последние 24 часа** и
    сравниваем с `repeat_max`. Его ответ счёт не обнуляет: предел дневной, а не
    «на один промежуток тишины».

    ⚠ Пары без единого его слова не берём вовсе: это ушедший инвайт, на который не
    ответили, и возвращаться к нему — работа инвайтов (`invite_repeat_after`), а
    не эта.

    ## ⚠⚠ Срок у каждой пары свой

    В SQL уходит только **нижняя** граница — она отсекает дёшево и индексом. Выше
    неё срок у каждой пары свой, жребием (`_due_sec`), и проверяется он здесь же,
    строкой: `quiet_sec` — сколько пара молчит.

    ⚠ Жребий считается **раньше** дневного счёта намеренно: он бесплатный, а счёт
    — отдельный запрос на пару. Поэтому и смотрим вдвое больше строк, чем раньше:
    порядок `last_at DESC` даёт сперва самые свежие затихшие, то есть те, кому
    срок скорее ещё не вышел.
    """
    async with mysql_get_db_async() as db:
        # «Последняя реплика» считается по времени, а не по `MAX(id)`: импорт из
        # панели кладёт недельную историю сегодняшними номерами. Та же мерка, что
        # у автоответа и у списка диалогов — три места, и во всех правило одно.
        await db.execute(
            'SELECT t.sender_uid AS uid, t.last_at,'
            '       (SELECT c.name FROM companion c'
            '         WHERE c.site_id = p.site_id AND c.uid = t.sender_uid) AS name,'
            # Его последнее слово. ⚠ Заходы от него больше не считаются (счёт
            # дневной), но сказал ли он хоть раз — решается им: без единого его
            # слова это неотвеченный инвайт, а не затихший разговор.
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
            '         ORDER BY m3.created_at DESC, m3.id DESC LIMIT 1) AS last_silent,'
            # Сколько пара молчит — им и проверяется жребий срока. Считает база,
            # а не мы: её `NOW()` и отобрал строку выше, и мерить одно и то же
            # двумя часами значило бы ловить расхождение в секундах.
            '       TIMESTAMPDIFF(SECOND, t.last_at, NOW()) AS quiet_sec'
            '  FROM (SELECT m.sender_uid, MAX(m.created_at) AS last_at'
            '          FROM message m WHERE m.profile_id = %s'
            '         GROUP BY m.sender_uid) t'
            '  JOIN profile p ON p.id = %s'
            ' WHERE t.last_at < NOW() - INTERVAL %s SECOND'
            ' ORDER BY t.last_at DESC LIMIT %s',
            (int(profile_id), int(profile_id), int(profile_id), int(profile_id),
             int(profile_id), int(after_sec), INITIATIVE_PER_PROFILE * 10))
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

            # Срок этой пары — жребием между границами. Нижнюю уже проверил SQL;
            # всё, что между границами, решается здесь.
            due = _due_sec(profile_id, str(row['uid']), row.get('last_at'),
                           int(after_sec), int(after_max_sec))
            if int(row.get('quiet_sec') or 0) < due:
                continue

            # ⚠⚠ Сутки **скользящие**, и его ответ счёт не обнуляет: предел
            # дневной. Мерка та же, что у инвайтов (`invite_repeat_max_day`), и
            # держаться их одной — не косметика: оба предела отвечают человеку на
            # один вопрос «сколько раз в день мне напишут», и разъехавшись, они
            # сделали бы ответ непредсказуемым.
            await db.execute(
                'SELECT COUNT(*) AS n FROM chat_initiative'
                " WHERE profile_id = %s AND uid = %s AND status = 'sent'"
                '   AND sent_at > NOW() - INTERVAL 1 DAY',
                (int(profile_id), str(row['uid'])))
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
            'SELECT id, site_id, login, profile_id, profile, role, auto_answer_only'
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


async def _after_range(site_id: int) -> tuple[int, int]:
    """Границы срока тишины в **секундах**: `(от, до)`. Настройки — в минутах.

    ⚠ Верхняя приводится к нижней, если меньше её или ноль: «до» ниже «от» —
    не настройка, а описка, и работать по ней значило бы писать раньше, чем
    человек велел. Ноль в «до» — законное «без разброса».
    """
    from src.uvicorn.routers.dating.site_ import site_seconds

    try:
        low = int(await site_seconds(int(site_id or 0),
                                     INITIATIVE_AFTER_SETTING))
    except Exception as e:
        logger_info(f'[dating] срок инициативы не прочитался: {e}')

        low = INITIATIVE_AFTER_DEFAULT

    try:
        high = int(await site_seconds(int(site_id or 0),
                                      INITIATIVE_AFTER_MAX_SETTING))
    except Exception as e:
        logger_info(f'[dating] верхний срок инициативы не прочитался: {e}')

        high = INITIATIVE_AFTER_MAX_DEFAULT

    low = max(0, low) * 60

    return low, max(low, max(0, high) * 60)


def _due_sec(profile_id: int, uid: str, last_at, low: int, high: int) -> int:
    """Срок тишины для этой пары: жребий между `low` и `high`, в секундах.

    ## ⚠⚠ Жребий устойчив, и в этом весь смысл

    Круг просыпается раз в минуту. Бросай мы случай на каждом тике, пара уходила
    бы в работу на первом низком выпадении — то есть почти сразу после «от», и
    «до» не значила бы ничего. Поэтому срок **выводится из самой пары**: пока
    тишина та же, он тот же.

    Ключ жребия — анкета, собеседник и время последней реплики. Третье делает
    бросок новым на каждый новый промежуток тишины: ответил он или ушла наша
    инициатива — время сдвинулось, и следующая пауза будет другой длины.

    ⚠⚠ **Не `hash()` и не `random`.** Встроенный `hash()` солится на каждый
    процесс (`PYTHONHASHSEED`), а воркеров несколько и перезапускаются они часто:
    пара получала бы новый срок от каждого процесса и после каждой выкладки — то
    есть ровно тот же развал, от которого жребий и уводится в устойчивый.
    """
    if high <= low:
        return low

    key = f'{int(profile_id)}:{uid}:{last_at}'
    digest = hashlib.blake2b(key.encode('utf-8'), digest_size=8).digest()
    share = int.from_bytes(digest, 'big') / float(1 << 64)

    return low + int(share * (high - low + 1))


async def _repeat(site_id: int) -> int:
    """Сколько заходов за сутки разрешено одной паре. Ноль — занятие выключено."""
    from src.uvicorn.routers.dating.site_ import site_seconds

    try:
        return max(0, int(await site_seconds(int(site_id or 0),
                                             INITIATIVE_REPEAT_SETTING)))
    except Exception as e:
        logger_info(f'[dating] число заходов инициативы не прочиталось: {e}')

        return INITIATIVE_REPEAT_DEFAULT
