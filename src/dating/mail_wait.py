"""Остуда получателя: кому сейчас нельзя писать, хотя завтра будет можно.

Сайт отвечает «нет» двумя разными способами, и до сих пор мы слышали только один.

    403 {"Error":"The user's hourly incoming message limit has been exceeded",
         "Reason":"ERROR_MESSAGE_USER_HOUR_IN_LIMIT"}

Это **не отказ письму**. Это «у него переполнен ящик за этот час» — состояние
получателя, которое само рассосётся. Для панели же оно было обычной неудачей:
причина ложилась в `error`, круг отправки такие строки не берёт никогда
(`send_round._oldest`), и письмо умирало навсегда.

## ⚠⚠ Цена ошибки, измеренная на проде

Журнал за трое суток: **1463 отказа против 184 разрешений**, и 1392 из них —
этот самый часовой предел. Пул рассылки в тот момент выглядел так:

    100 черновиков = потолок пула (`mailing_pool_max`)
     17 живых
     83 с «часовой предел» в `error`

Потолок считает все `draft` (`mailing_round._pool_size`), поэтому набор видел пул
полным и не готовил ничего, а отправка точила остаток в семнадцать писем.
Рассылка стояла — не от поломки, а оттого, что временный ответ сайта мы понимали
как вечный.

Хуже того: `uq_mailing_once(site_id, uid, mail_id)` считает и мёртвые строки. Пара
«адресат — шаблон», похороненная часовым пределом, **больше не могла быть
написана никогда**.

## Что здесь есть

Список «остывающих» людей — по сайту, с временем, когда остуда кончится. Его
спрашивают трое: набор пула (чтобы не сочинять письмо тому, кому его сейчас не
отдать), круг отправки (чтобы не тратить свой единственный заход в минуту) и сама
отправка (чтобы занести остуду, услышав отказ).

## ⚠ Остуда — на получателя, а не на пару

Предел считает **его** ящик: один и тот же мужчина получает отказ тринадцати
нашим анкетам подряд (uid 466182 — 40 попыток от 13 девушек за трое суток). Запиши
мы остуду на пару, остальные двенадцать продолжали бы стучать в ту же дверь и
узнавать то же самое.

Отличие от запрета (`block.py`) ровно в этом: там «он закрылся **от неё**», и
правило про пару; здесь — свойство человека и часа.

## ⚠ Почему приметы здесь константами, а не настройкой сайта

У запрета приметы живут настройкой (`block_deny_mark`), и довод верный: коды
придумывает сайт. Здесь примета несёт **ещё и срок, и слова для человека**, а эти
три вещи нельзя записать списком через запятую: «час» и «сутки» не
взаимозаменяемы, и обобщать их в одно значение значило бы выдумывать мини-язык
ради второго сайта, которого пока нет.

Появится сайт с другими словами — эта таблица и есть то место, куда он добавит
свою строку.

## ⚠ Redis молчит — считаем, что писать можно

Сбой Redis отвечает «остуды нет»: ошибка в эту сторону стоит одного отказа сайта,
а в обратную — молчания всей рассылки. Тот же выбор и по тому же доводу, что у
лестницы отказов входа (`site_api.py`).
"""

import time

from logging_.logging_ import logger_info
from redis_.redis_ import redis_conn_get, redis_text

# Остывающие люди сайта: упорядоченное множество «uid → когда остуда кончится».
#
# ⚠ **Одно множество на сайт, а не ключ на человека.** Список нужен целиком —
# его спрашивает и набор пула, и запрос очереди отправки, — а собирать его по
# `KEYS dating:mail_wait:*` значило бы обходить всю базу Redis ради сотни номеров.
MAIL_WAIT_KEY = 'dating:mail_wait:{site_id}'

# Приметы остуды: что в ответе сайта, на сколько молчим, как сказать человеку.
#
# ⚠ **Порядок — порядок поиска.** Приметы не пересекаются, но если однажды
# пересекутся, первая строка сильнее: срок у неё короче, и ошибка в сторону
# «подождём меньше» дешевле обратной.
MAIL_WAIT_MARKS = (
    ('ERROR_MESSAGE_USER_HOUR_IN_LIMIT', 3600,
     'у получателя переполнен ящик за этот час'),
    # ⚠ Сутки — слово сайта («daily limit»), а не наша осторожность. Примета
    # приходит на **нового** человека: у таких сайт держит отдельный, более
    # строгий счёт входящих.
    ('ERROR_MESSAGE_NEW_USER_DAILY_LIMIT', 24 * 3600,
     'у получателя выбран суточный предел писем новому человеку'),
    # ⚠⚠ Суточный предел входящих **у всех**, не только у нового человека.
    # Прежде он хоронил письмо причиной в `error`: замер 23.11.2026 на Ladadate —
    # 31 письмо рассылки в ошибках, и один человек (286031) за две минуты отказал
    # двум девушкам подряд. Владелец: пусть ждут суток в пуле.
    ('ERROR_MESSAGE_USER_DAILY_IN_LIMIT', 24 * 3600,
     'у получателя выбран суточный предел входящих писем'),
    # Пять минут — слово сайта: «too many messages in the last five minutes».
    ('ERROR_MESSAGE_USER_SHORT_IN_LIMIT', 5 * 60,
     'получатель только что получил слишком много писем'),
    # ⚠ «Try a few days» — слово сайта: он не читает уже присланное. Три дня —
    # решение владельца 23.11.2026; прежде письмо хоронилось причиной навсегда.
    ('ERROR_MESSAGE_USER_UNREAD_LIMIT', 3 * 24 * 3600,
     'получатель не читает присланное — сайт просит подождать несколько дней'),
)

# Сколько живёт само множество. ⚠ Больше самого долгого срока: ключ обязан
# пережить любую свою запись, иначе остуда исчезнет вместе с ним.
MAIL_WAIT_KEEP = 3 * 24 * 3600 + 3600


def mail_wait_mark(site_id: int, uid: str, said: str) -> int:
    """Заносит остуду, если сайт отказал именно по временной причине.

    Args:
        site_id: сайт-закладка.
        uid: номер получателя на сайте.
        said: ответ сайта словами — то, что вернул `site_api`.

    Returns:
        int: на сколько секунд человек остыл. **Ноль — примета не наша**, и
        отказ разбирает тот, кто спрашивал: это может быть и запрет навсегда
        (`block.py`), и настоящая неудача.

    ⚠ **Решение «наш ли это отказ» живёт здесь, а не у вызывающего.** Отправку
    зовут пять дорог (два круга и три кнопки), и перечень примет, разложенный по
    ним, разошёлся бы на первой же правке — первая забытая дорога продолжала бы
    хоронить письма.
    """
    site_id, uid = int(site_id or 0), str(uid or '').strip()
    if not site_id or not uid:
        return 0

    low = str(said or '').lower()
    hit = next((one for one in MAIL_WAIT_MARKS if one[0].lower() in low), None)
    if not hit:
        return 0

    _mark, wait, _words = hit

    try:
        conn = redis_conn_get()
        key = MAIL_WAIT_KEY.format(site_id=site_id)
        conn.zadd(key, {uid: time.time() + wait})
        conn.expire(key, MAIL_WAIT_KEEP)
    except Exception as e:
        logger_info(f'[dating] остуда получателя не записалась: {e}')

        # ⚠ Срок возвращаем и при сбое Redis: вызывающий по нему решает, портить
        # ли письмо причиной, и мёртвое письмо — беда дороже незаписанной остуды.
        return wait

    logger_info(f'[dating] сайт #{site_id}: {uid} остыл на {wait // 60} мин '
                f'— {_words}')

    return wait


def mail_wait_left(site_id: int, uid: str) -> int:
    """Сколько секунд этому человеку ещё не пишем. Ноль — можно писать."""
    site_id, uid = int(site_id or 0), str(uid or '').strip()
    if not site_id or not uid:
        return 0

    try:
        until = redis_conn_get().zscore(MAIL_WAIT_KEY.format(site_id=site_id), uid)
    except Exception as e:
        logger_info(f'[dating] остуда получателя не спросилась: {e}')

        return 0

    return max(0, int(float(until) - time.time())) if until else 0


def mail_wait_uids(site_id: int) -> list[str]:
    """Кто сейчас остывает у этого сайта. Пусто — писать можно всем.

    ⚠ **Истёкшие заодно и выметаются.** Множество живёт по сроку ключа, а
    записи внутри — по своим; не убирай их никто, и список рос бы, пока его
    не унесёт срок всего ключа.
    """
    site_id = int(site_id or 0)
    if not site_id:
        return []

    now = time.time()

    try:
        conn = redis_conn_get()
        key = MAIL_WAIT_KEY.format(site_id=site_id)
        conn.zremrangebyscore(key, '-inf', now)
        rows = conn.zrangebyscore(key, now, '+inf')
    except Exception as e:
        logger_info(f'[dating] список остывших не спросился: {e}')

        return []

    return [redis_text(one) for one in rows or []]


# С какого остатка остуды письмо рассылки не ждёт в пуле, а уходит из него.
# ⚠ Больше часового предела с запасом: часовая остуда сюда не попадает никогда,
# суточная и трёхдневная — пока им осталось больше двух часов.
MAIL_WAIT_LONG_SEC = 2 * 3600


def mail_wait_long_uids(site_id: int) -> list[str]:
    """Кому писать ещё долго нельзя — остатка остуды не меньше `MAIL_WAIT_LONG_SEC`.

    ⚠ Читает уборка пула рассылки (`pool.pool_sweep_mail_waiting`): письмо
    такому человеку держало бы место сутки и больше, а новое письмо другому
    адресату собирается подстановкой за доли секунды.
    """
    site_id = int(site_id or 0)
    if not site_id:
        return []

    try:
        rows = redis_conn_get().zrangebyscore(
            MAIL_WAIT_KEY.format(site_id=site_id),
            time.time() + MAIL_WAIT_LONG_SEC, '+inf')
    except Exception as e:
        logger_info(f'[dating] список долго остывающих не спросился: {e}')

        return []

    return [redis_text(one) for one in rows or []]


def mail_wait_words(said: str, left: int = 0) -> str:
    """Почему письмо не ушло — словами, по которым видно, что оно живо.

    ⚠ **Отдельно от `error` письма, и это весь смысл.** Строка уходит человеку в
    ответ кнопки и в журнал анкеты, но **не** в столбец `error`: там она
    означала бы «круг больше не вернётся», а он вернётся — через срок.
    """
    low = str(said or '').lower()
    hit = next((one for one in MAIL_WAIT_MARKS if one[0].lower() in low), None)
    girl = next((one for one in MAIL_WAIT_GIRL_MARKS if one[0].lower() in low), None)
    words = hit[2] if hit else girl[1] if girl else 'сайт просит подождать'

    return (f'письмо ждёт: {words}. Повторим через {_when(left)} — письмо цело и '
            f'остаётся в пуле' if left else f'письмо ждёт: {words}')


def mail_wait_skip(left: int) -> str:
    """Почему набор пропустил этого адресата. Строка видна человеку в причинах.

    ⚠ **Своя строка, а не `mail_wait_words`.** Там письмо уже написано и «цело»,
    здесь его ещё нет: сказать про целое письмо значило бы соврать о том, чего не
    было, — а причины набора человек читает именно затем, чтобы понять, почему
    письма **не появилось**.
    """
    return (f'сайт сейчас не даёт ему писать — выбран предел входящих, '
            f'ждём {_when(left)}')

# ── Только что доставлено ────────────────────────────────────────────────────
#
# ⚠⚠ Замер 23.11.2026 на Ladadate (3056 попыток за трое суток): из 1008 часов, в
# которые человеку дошло наше письмо, в 971 дошло ровно одно — удачная доставка
# сама выбирает его часовую норму. Следующая анкета в первые десять минут
# получала отказ «ящик полон за час» в 11 случаях из 29. Владелец: после удачи —
# час паузы этому человеку.
#
# ⚠ **Только для рассылки.** Ответ на его письмо и письма избранным этим списком
# не придерживаются: человек ждёт ответа, и половина повторов всё же проходит —
# держать живой разговор ради холодной статистики незачем.
#
# ⚠ Отдельное множество, а не запись в общую остуду: та говорит «сайт отказал»
# и показывается человеку причиной простоя всех пулов (`send_why`).
MAIL_WAIT_FRESH_KEY = 'dating:mail_fresh:{site_id}'
MAIL_WAIT_FRESH_SEC = 3600


def mail_wait_fresh_mark(site_id: int, uid: str) -> None:
    """Письмо этому человеку только что дошло: час рассылка ему не пишет."""
    site_id, uid = int(site_id or 0), str(uid or '').strip()
    if not site_id or not uid:
        return

    try:
        conn = redis_conn_get()
        key = MAIL_WAIT_FRESH_KEY.format(site_id=site_id)
        conn.zadd(key, {uid: time.time() + MAIL_WAIT_FRESH_SEC})
        conn.expire(key, MAIL_WAIT_FRESH_SEC + 600)
    except Exception as e:
        logger_info(f'[dating] отметка доставки {uid} не записалась: {e}')


def mail_wait_fresh_uids(site_id: int) -> list[str]:
    """Кому за последний час дошло наше письмо. Сбой Redis — пусто."""
    site_id = int(site_id or 0)
    if not site_id:
        return []

    now = time.time()

    try:
        conn = redis_conn_get()
        key = MAIL_WAIT_FRESH_KEY.format(site_id=site_id)
        conn.zremrangebyscore(key, '-inf', now)
        rows = conn.zrangebyscore(key, now, '+inf')
    except Exception as e:
        logger_info(f'[dating] недавние доставки не спросились: {e}')

        return []

    return [redis_text(one) for one in rows or []]

# ── Предел самой анкеты ──────────────────────────────────────────────────────
#
# ⚠⚠ `ERROR_MESSAGE_USER_NON_CONTACT_DAILY_SEND_LIMIT` — предел **отправительницы**,
# а не получателя: «Daily message sending limit for non-contacts exceeded». Замер
# 23.11.2026: 31 отказ за четыре дня, все у одной анкеты (#52) 28 разным людям,
# а тем же людям от других девушек письма проходили. Прежде отказ хоронил письмо
# причиной, и анкета жгла по письму в минуту до полуночи. Теперь анкета выпадает
# из очереди рассылки до конца суток UTC, а письма остаются в пуле.
#
# ⚠ Конец суток — догадка: когда сайт сбрасывает счёт, он не говорит. Ошибка в
# сторону «рано» стоит одного отказа в сутки, а не похороненного письма.
MAIL_WAIT_GIRL_KEY = 'dating:mail_girl_wait:{site_id}'
MAIL_WAIT_GIRL_MARKS = (
    ('ERROR_MESSAGE_USER_NON_CONTACT_DAILY_SEND_LIMIT',
     'у анкеты выбран суточный предел писем не-контактам'),
)


def mail_wait_girl_mark(site_id: int, profile_id: int, said: str) -> int:
    """Отказ — предел анкеты? Ставит её на паузу до конца суток. Секунды или ноль."""
    site_id, profile_id = int(site_id or 0), int(profile_id or 0)
    hit = next((one for one in MAIL_WAIT_GIRL_MARKS
                if one[0].lower() in str(said or '').lower()), None)
    if not site_id or not profile_id or not hit:
        return 0

    now = time.time()
    wait = max(60, 86400 - int(now) % 86400)

    try:
        conn = redis_conn_get()
        key = MAIL_WAIT_GIRL_KEY.format(site_id=site_id)
        conn.zadd(key, {str(profile_id): now + wait})
        conn.expire(key, 86400 + 600)
    except Exception as e:
        logger_info(f'[dating] пауза анкеты #{profile_id} не записалась: {e}')

    logger_info(f'[dating] анкета #{profile_id}: {hit[1]} — пауза {wait // 60} мин')

    return wait


def mail_wait_girls(site_id: int) -> list[int]:
    """Анкеты сайта на паузе по своему пределу. Сбой Redis — пусто."""
    site_id = int(site_id or 0)
    if not site_id:
        return []

    now = time.time()

    try:
        conn = redis_conn_get()
        key = MAIL_WAIT_GIRL_KEY.format(site_id=site_id)
        conn.zremrangebyscore(key, '-inf', now)
        rows = conn.zrangebyscore(key, now, '+inf')
    except Exception as e:
        logger_info(f'[dating] анкеты на паузе не спросились: {e}')

        return []

    return [int(redis_text(one)) for one in rows or [] if redis_text(one).isdigit()]



# ── Приватное ────────────────────────────────────────────────────────────────

def _when(left: int) -> str:
    """Срок словами: «40 мин», «3 ч». Секунды в журнале не читаются.

    ⚠ Правило то же, что у `site_api._fail_when`, и копией оно стало осознанно:
    там срок про вход и считается лестницей, здесь — про получателя и приходит
    из приметы. Свести их значило бы связать два несвязанных решения.
    """
    left = max(0, int(left))
    if left >= 3600:
        minutes = left % 3600 // 60

        return f'{left // 3600} ч' + (f' {minutes} мин' if minutes else '')

    return f'{left // 60} мин' if left >= 60 else f'{left} с'
