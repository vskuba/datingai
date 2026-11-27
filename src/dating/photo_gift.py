"""Фото-подарок: девушке просто захотелось прислать снимок — без просьбы.

Обещанное фото (`she_promised_send_photo`) появляется, когда тема зашла о
снимках. Этого мало: живая девушка шлёт фото и просто так, подарком. Решение
владельца 21.11.2026 — намерение `she_just_send_photo`: после двух-трёх его
сообщений, с вероятностью 30%.

## ⚠⚠ Решают двое, и каждый своё

| Кто | Что решает |
|-----|------------|
| мы (`photo_gift_due`) | **можно ли**: факты — медиа открыто, пределы, его сообщения, есть невиданный снимок |
| сосед (шаг «Жребий» `dating_talk`) | **хочется ли**: 30% — доля живёт у него одного |

Доля в двух местах уже стоила двойного счёта (зачин «писать первой неловко»
складывался из 15,9% у нас и 3% у соседа). Поэтому здесь нет ни одного
`random`: только факты, переменной `photo_gift_due` = `'1'`/`'0'`.

## ⚠⚠ Снимок уходит **вместе** с ответом

Выпал подарок — писатель соседа добавляет строку о снимке, а прогон метит
`she_just_send_photo` с приметами. Отправка ответа (`answer.py`) видит
ждущее намерение своего обмена и шлёт текст со снимком одним сообщением
(`photo_gift_attach`). Медиа закрылось или снимка нет — уходит один текст, а
намерение закрывается с причиной: подарок не обещание, ждать он не должен.

## ⚠ Порог «2–3 его сообщения» — жребий, но устойчивый

Свежий бросок на каждом его сообщении превратил бы «2–3» в «2»: первое же
низкое выпадение уносит пару. Бросок привязан к паре и времени последнего
снимка — `dice_steady`, как срок инициативы.

## Диагностика: почему «нет»

Ответов «нет» девять, а наружу у них один `False` — на проде не разобрать,
какая причина сработала. Поэтому каждый заход `photo_gift_due` оставляет
строку в журнале и JSON в список Redis
`dating:photo_gift:why:{сайт}:{анкета}:{он}` — последние 50 ответов, живут
14 суток: `{at, due, why, his, need, day, per_day}`, где `why` — короткий
код: `no_chat_photo`, `intention_off`, `per_day_zero`, `day_limit`, `gap`,
`few_messages`, `no_fresh_photo`, `media_closed`, `media_closed_cached`
(ответ из памяти `_OPEN`, сокета не было), `ok`, `error`. Читается обратно
`photo_gift_why_rows` — свежие вперёд.

⚠⚠ Диагностика ничего не решает: `photo_gift_due` отвечает ровно тот же
`bool`, что и до неё, а сбой Redis не ломает ответ — список пишется и
читается под `try/except`, по тому же доводу, что остуда получателя
(`mail_wait.py`).
"""

import json
import time

from google_drive_.google_drive_bytes import google_drive_bytes
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from redis_.redis_ import redis_conn_get, redis_text

from src.dating.chat_intention import chat_intention_do_allowed
from src.dating.companion_gallery import companion_gallery_fresh, companion_gallery_pick
from src.dating.dice import dice_steady
from src.dating.site.site_registry import site_registry_chat
from src.dating.site_setting import site_seconds

# Метка намерения — слово в слово как у соседа (ветка подпотока
# `intention_context` и шаг «Подарок: намерение» в `dating_talk`).
PHOTO_GIFT_INTENTION = 'she_just_send_photo'

# Настройки сайта (вкладка «Намерения и действия»).
PHOTO_GIFT_PER_DAY_SETTING = 'photo_gift_per_day'
PHOTO_GIFT_PER_DAY_DEFAULT = 2
PHOTO_GIFT_GAP_SETTING = 'photo_gift_gap_hours'
PHOTO_GIFT_GAP_DEFAULT = 3

# Сколько его сообщений с прошлого снимка: от и до, жребием на промежуток.
PHOTO_GIFT_AFTER_MIN = 2
PHOTO_GIFT_AFTER_MAX = 3

# Подарок — только из открытой папки: интимное уходит лишь по просьбе словами.
PHOTO_GIFT_GROUP = 'public'

# Ширина снимка, который уходит в чат.
PHOTO_GIFT_WIDTH = 1600

# Память ответа «открыто ли медиа в диалоге»: `{(сайт, она, он): (когда, да/нет)}`.
# ⚠ Открытое не закрывают — помним долго; закрытое откроется разговором —
# спрашиваем снова через полчаса. Без памяти каждый ответ открывал бы сокет.
_OPEN: dict = {}
PHOTO_GIFT_OPEN_YES_SEC = 6 * 3600
PHOTO_GIFT_OPEN_NO_SEC = 1800

# Диагностика решений пары: список JSON-строк, свежие — первыми (`LPUSH`).
# ⚠ Список — на пару: вопрос всегда про неё («почему этой ей не ушло»), а
# ключ без `uid` заставил бы вычитать одного человека из чужих ответов.
PHOTO_GIFT_WHY_KEY = 'dating:photo_gift:why:{site_id}:{profile_id}:{uid}'
PHOTO_GIFT_WHY_MAX = 50
PHOTO_GIFT_WHY_KEEP = 14 * 24 * 3600


async def photo_gift_due(profile: dict, presence: dict, uid: str) -> bool:
    """Можно ли сейчас подарить этому собеседнику снимок. Ничего не бросает.

    ⚠ Порядок — от дешёвого к дорогому: сокет спрашивается последним.

    Кроме ответа, каждый заход записывает причину: строку в журнал и JSON в
    список Redis (читается `photo_gift_why_rows`). Решение диагностика не
    меняет — `bool` ровно тот же, что был.
    """
    uid = str(uid)
    site_id, profile_id, login, note = 0, 0, '', ''

    try:
        site_id = int(profile.get('site_id') or 0)
        profile_id = int(profile.get('id') or 0)
        login = _login(profile)
        res = await _due(profile, presence, uid)
    except Exception as e:
        # ⚠ Тот же `False`, что и до диагностики: сбой — не подарок.
        res = {'due': False, 'why': 'error'}
        note = f' не посчитался ({e})'

    logger_info(f'[dating] фото-подарок {login}->{uid}: {res["why"]}{_why_nums(res)}{note}')
    _why_write(site_id, profile_id, uid, res)

    return bool(res['due'])


def photo_gift_why_rows(site_id: int, profile_id: int, uid: str, limit: int = 50) -> list[dict]:
    """Последние решения по паре, свежие вперёд. Пусто — записей нет или Redis молчит.

    Строка, которая не читается как JSON, пропускается: битая запись не должна
    утаскивать за собой весь список.
    """
    key = PHOTO_GIFT_WHY_KEY.format(site_id=int(site_id or 0),
                                    profile_id=int(profile_id or 0), uid=str(uid or ''))

    try:
        rows = redis_conn_get().lrange(key, 0, max(int(limit or 1) - 1, 0))
    except Exception as e:
        logger_info(f'[dating] фото-подарок: причины не прочитались ({e})')

        return []

    out = []
    for one in rows or []:
        try:
            out.append(json.loads(redis_text(one)))
        except Exception:
            continue

    return out


async def photo_gift_attach(profile: dict, message_id: int, uid: str) -> dict:
    """Снимок к ответу, если прогон пометил подарок. Пусто — подарка нет.

    Returns:
        dict: `intention_id`, `file` (строка `gallery_file`), `raw`, `kind` —
        готово к `send_message(photo=…)`; `{'intention_id': N, 'why': …}` —
        подарок помечен, но снимка нет; `{}` — подарка в этом обмене нет.
    """
    if not int(message_id or 0):
        return {}

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, context FROM chat_intention'
            ' WHERE message_id = %s AND intention = %s AND profile_id = %s'
            ' ORDER BY id DESC LIMIT 1',
            (int(message_id), PHOTO_GIFT_INTENTION, int(profile['id'])))
        row = await db.fetchone()
    if not row:
        return {}

    from src.dating.json_column import json_column_load

    context = json_column_load(row.get('context'))
    pick = await companion_gallery_pick(int(profile['id']), int(profile['site_id']),
                                        str(uid), context.get('tags'), PHOTO_GIFT_GROUP)
    file = pick.get('file') or {}
    if not file.get('file_id'):
        # ⚠ Приметы ни к чему не подошли — берём любой невиданный из открытой
        # папки: подарок не обещал определённого кадра.
        fresh = await companion_gallery_fresh(int(profile['id']), int(profile['site_id']),
                                              str(uid), 1, PHOTO_GIFT_GROUP)
        file = fresh[0] if fresh else {}
    if not file.get('file_id'):
        return {'intention_id': int(row['id']), 'why': pick.get('why') or 'снимка нет'}

    raw, kind = await google_drive_bytes(file['file_id'], PHOTO_GIFT_WIDTH)

    return {'intention_id': int(row['id']), 'file': file, 'raw': raw,
            'kind': kind or 'image/jpeg'}


async def _due(profile: dict, presence: dict, uid: str) -> dict:
    """Факты «можно ли» с причиной: `{due, why, his, need, day, per_day, ago, gap}`.

    ⚠ Порядок проверок, выходов и запросов — ровно тот же, что до диагностики:
    диагностика не имеет права проглотить или ускорить решение.
    """
    site_id, profile_id = int(profile.get('site_id') or 0), int(profile['id'])
    driver = site_registry_chat(presence)
    if not driver.chat_photo():
        return _why(False, 'no_chat_photo')
    if not await chat_intention_do_allowed(site_id):
        return _why(False, 'intention_off')

    per_day = int(await site_seconds(site_id, PHOTO_GIFT_PER_DAY_SETTING))
    gap = int(await site_seconds(site_id, PHOTO_GIFT_GAP_SETTING)) * 3600
    if per_day <= 0:
        return _why(False, 'per_day_zero', per_day=per_day, gap=gap)

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT MAX(sent_at) AS last_at,'
            '       SUM(sent_at > NOW() - INTERVAL 1 DAY) AS day,'
            '       TIMESTAMPDIFF(SECOND, MAX(sent_at), NOW()) AS ago'
            '  FROM companion_gallery_file'
            ' WHERE profile_id = %s AND site_id = %s AND uid = %s',
            (profile_id, site_id, uid))
        shown = (await db.fetchone()) or {}
        day = int(shown.get('day') or 0)
        if day >= per_day:
            return _why(False, 'day_limit', day=day, per_day=per_day)
        ago = int(shown['ago']) if shown.get('ago') is not None else None
        if shown.get('last_at') and int(shown.get('ago') or 0) < gap:
            return _why(False, 'gap', day=day, per_day=per_day, ago=ago, gap=gap)

        await db.execute(
            "SELECT COUNT(*) AS n FROM message WHERE profile_id = %s AND sender_uid = %s"
            "   AND direction = 'in' AND (%s IS NULL OR created_at > %s)",
            (profile_id, uid, shown.get('last_at'), shown.get('last_at')))
        his = int(((await db.fetchone()) or {}).get('n') or 0)

    need = dice_steady(f'photo_gift:{profile_id}:{uid}:{shown.get("last_at") or ""}',
                       PHOTO_GIFT_AFTER_MIN, PHOTO_GIFT_AFTER_MAX)
    nums = {'his': his, 'need': need, 'day': day, 'per_day': per_day, 'ago': ago, 'gap': gap}
    if his < need:
        return _why(False, 'few_messages', **nums)

    if not await companion_gallery_fresh(profile_id, site_id, uid, 1, PHOTO_GIFT_GROUP):
        return _why(False, 'no_fresh_photo', **nums)

    opened, cached = await _open(driver, site_id, presence, profile, uid)
    if opened:
        return _why(True, 'ok', **nums)

    return _why(False, 'media_closed_cached' if cached else 'media_closed', **nums)


async def _open(driver, site_id: int, presence: dict, profile: dict, uid: str) -> tuple:
    """Открыто ли медиа в диалоге — и откуда ответ: `(открыто, из_памяти)`.

    ⚠ Открытое не закрывают — помним долго; закрытое откроется разговором —
    спрашиваем снова через полчаса. Без памяти каждый ответ открывал бы сокет.
    """
    girl = _login(profile)
    key = (site_id, girl, uid)
    seen = _OPEN.get(key)
    if seen:
        ttl = PHOTO_GIFT_OPEN_YES_SEC if seen[1] else PHOTO_GIFT_OPEN_NO_SEC
        if time.monotonic() - seen[0] < ttl:
            return seen[1], True

    answer = await driver.chat_photo_open(site_id, presence, girl, uid)
    _OPEN[key] = (time.monotonic(), bool(answer))

    return bool(answer), False


def _login(profile: dict) -> str:
    """Логин анкеты на сайте — то же слово, по которому медиа помнит `_open`."""
    return str(profile.get('profile_id') or profile.get('login') or '').strip()


def _why(due: bool, code: str, **nums) -> dict:
    """Результат `_due`: вердикт, короткий код причины и посчитанные числа."""
    return {'due': bool(due), 'why': code, **nums}


def _why_nums(res: dict) -> str:
    """Числа к строке журнала: только те, что успели посчитать до выхода."""
    nums = ' '.join(f'{k}={res[k]}' for k in ('his', 'need', 'day', 'per_day', 'ago', 'gap')
                    if res.get(k) is not None)

    return f' {nums}' if nums else ''


def _why_write(site_id: int, profile_id: int, uid: str, res: dict) -> None:
    """Записать причину в список Redis пары. Молчит Redis — молчим и мы.

    ⚠ Сбой записи не имеет права ломать ответ: диагноз дешевле вердикта,
    тот же выбор и по тому же доводу, что у остуды получателя (`mail_wait.py`).
    """
    row = {'at': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()),
           'due': bool(res.get('due')), 'why': str(res.get('why') or ''),
           'his': res.get('his'), 'need': res.get('need'),
           'day': res.get('day'), 'per_day': res.get('per_day')}

    try:
        conn = redis_conn_get()
        key = PHOTO_GIFT_WHY_KEY.format(site_id=site_id, profile_id=profile_id, uid=uid)
        conn.lpush(key, json.dumps(row, ensure_ascii=False))
        conn.ltrim(key, 0, PHOTO_GIFT_WHY_MAX - 1)
        conn.expire(key, PHOTO_GIFT_WHY_KEEP)
    except Exception as e:
        logger_info(f'[dating] фото-подарок: причина не записалась ({e})')
