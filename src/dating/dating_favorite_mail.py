"""Переписка с избранными: письмо, возобновляющее затихший разговор.

Цепочка почты до сих пор замыкалась на ответах: мы разослали, он ответил, мы
ответили — и ждём. Он не пишет, разговор затихает, а повода написать у нас нет:
все наши поводы были реакцией на его письмо.

Здесь повод появляется. Письмо **пишем первыми** тем, кто уже отвечал, — то есть
избранным (`dating_favorite`), и это не знакомство, а продолжение прерванного
разговора. Отсюда и агент другой: ему отдают историю переписки, как при ответе,
но отвечать не на что — последнее слово наше.

## Кого берём в работу

| Условие | Почему |
|---------|--------|
| человек в избранных этой анкеты | избранным он стал потому, что ответил на письмо |
| **анкета в сети** (`status = 'online'`) | письмо уходит от её имени, и уносит его отправка — а та тоже ждёт, пока девушка на сайте |
| **собеседник в сети** | письмо застаёт его на сайте; написанное в отсутствие уйдёт в груду непрочитанного, а предел в два письма будет потрачен |
| последнее слово **наше** | он молчит — это и есть повод; ответить ему должен круг ответов, а не этот |
| прошло не меньше `favorite_mail_gap` | два письма подряд за час выдают робота |
| за сутки ему ушло меньше `favorite_mail_per_day` | предел на человека, а не на сайт |
| в пуле нет его черновика | второй черновик тому же человеку — это два письма подряд, отложенных на потом |

⚠ **Порядок проверок — от дешёвого к дорогому.** Онлайн читается из снимка в
Redis, пределы — запросом к базе, и только потом зовётся агент. Спроси мы его
раньше — платили бы вызовом LLM за письмо, которое всё равно не ляжет в пул. Та же
развилка, что у рассылки (`dating_mailing._body`).

⚠ **Оба предела — про человека, а не про сайт.** Письма считает получатель, а не
наши таблицы: сделай мы «два письма в сутки» на сайт, тринадцать анкет делили бы
их на всех, и занятие не работало бы вовсе.

⚠ **Считается всё ушедшее ему**, а не только этот пул: рассылка, ответ и
возобновление приходят человеку в одну почту. Тот же довод, по которому
`send_max_day` общий на рассылку и ответы.

## Чего здесь нет

**Отправки.** Письмо ложится в пул и ждёт. Уходит оно отдельным решением —
выключателем `favorite_mail_send_on`, снятым по умолчанию, — и уносит его общий
круг отправки, в часы `mailing_schedule`. Та же граница, что у рассылки и
ответов: необратимый шаг остаётся за человеком.

**Своего расписания.** Набор ничего не отправляет, а отправка идёт по часам всей
почты сайта. Второе расписание отвечало бы на тот же вопрос вторым голосом.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_favorite import (DATING_FAVORITE_STATES,
                                        DATING_FAVORITE_STATES_SQL)
from src.dating.dating_mail_agent import (DatingMailAgentError,
                                          dating_favorite_mail_write)
from src.dating.dating_online import dating_online_show

# Сколько писем набирает один заход, если настройки нет.
DATING_FAVORITE_MAIL_BATCH_DEFAULT = 10

# Жёсткий предел поверх настройки. ⚠ Не дублирует её, а страхует: число приходит
# с кнопки, и «500» превратило бы нажатие в получасовое ожидание — каждое письмо
# это вызов LLM.
DATING_FAVORITE_MAIL_BATCH_MAX = 50

# Сколько писем переписки отдаём агенту. Тот же предел и та же причина, что у
# ответов: переписку на сорок писем модель читает по диагонали.
DATING_FAVORITE_MAIL_HISTORY = 10

# Настройки сайта, которыми занятие живёт.
DATING_FAVORITE_MAIL_PER_DAY_SETTING = 'favorite_mail_per_day'
DATING_FAVORITE_MAIL_GAP_SETTING = 'favorite_mail_gap'
DATING_FAVORITE_MAIL_POOL_SETTING = 'favorite_mail_pool_max'

# Умолчания на случай, если настройки сайта нет. Те же, что в реестре.
DATING_FAVORITE_MAIL_PER_DAY_DEFAULT = 2
DATING_FAVORITE_MAIL_GAP_DEFAULT = 14400


class DatingFavoriteMailError(RuntimeError):
    """Набор не пошёл вовсе. Причина — словами, для страницы."""


async def dating_favorite_mail_make(site_id: int, limit: int = 0,
                                    profile_id: int = 0) -> dict:
    """Набирает пул писем избранным.

    Args:
        site_id: сайт-закладка.
        limit: сколько пар пробуем за заход. Ноль — предел по умолчанию.
        profile_id: писать только от этой анкеты. Ноль — от всех включённых.
            ⚠ Сужение, а не отбор заново: анкета всё равно должна быть с этого
            сайта и включённой.

    Returns:
        dict: `made` — сколько писем готово, `skipped` — сколько пар пропущено,
        `reasons` — почему (первые несколько), `left` — сколько осталось на
        следующий заход, `stopped` — почему заход оборвался (пусто — дошёл до
        конца).

    ⚠ **Повторный заход продолжает с того же места.** Пары, которым письмо уже
    лежит в пуле или ушло недавно, отсеиваются самим отбором — поэтому кнопку
    можно жать, пока пул не наберётся, а круг может будить набор хоть каждые
    полчаса.
    """
    pairs = await _quiet(site_id, profile_id)
    if not pairs:
        return {'made': 0, 'skipped': 0, 'reasons': [], 'left': 0, 'stopped': ''}

    per_day = await _seconds(site_id, DATING_FAVORITE_MAIL_PER_DAY_SETTING,
                             DATING_FAVORITE_MAIL_PER_DAY_DEFAULT)
    gap = await _seconds(site_id, DATING_FAVORITE_MAIL_GAP_SETTING,
                         DATING_FAVORITE_MAIL_GAP_DEFAULT)

    cap = min(int(limit) or DATING_FAVORITE_MAIL_BATCH_DEFAULT,
              DATING_FAVORITE_MAIL_BATCH_MAX)

    made, skipped, reasons, left, stopped = 0, 0, [], 0, ''
    # Анкеты читаются по одной на первую же выпавшую пару: пар у одной анкеты
    # бывают десятки, а строка её не меняется за заход.
    girls: dict[int, dict] = {}

    for pair in pairs:
        if made + skipped >= cap:
            left += 1
            continue

        profile_id_ = int(pair['profile_id'])
        uid = str(pair['uid'])

        # ⚠ **Пишем только тем, кто сейчас в сети.** Письмо, возобновляющее
        # разговор, застаёт человека на сайте: он видит его в почте, пока сидит
        # там, и отвечает сразу. Написанное в его отсутствие уйдёт в общую груду
        # непрочитанного и разговора не вернёт — а предел в два письма в сутки
        # будет на него потрачен.
        #
        # ⚠ **«Не знаем» считаем «не сейчас»**, в отличие от чата
        # (`dating_online_check` пропускает по незнанию). Там речь о реплике,
        # которая ничего не стоит; здесь — о вызове LLM и об одном из двух писем
        # в сутки, и тратить их на догадку незачем: лента снимается каждые две
        # минуты, и круг вернётся через полчаса.
        online = dating_online_show(site_id, uid)
        if not online.get('online'):
            skipped += 1
            _reason(reasons, f'{_addressee(pair)}: не в сети'
                             + ('' if online.get('known') else ' (ленту ещё не снимали)'))
            continue

        # ⚠ **Пределы проверяем до агента**, а не после: каждое письмо это вызов
        # LLM, и платить за него ради того, чтобы потом выбросить, незачем. Тот же
        # порядок, что у рассылки, — дешёвое раньше платного.
        # ⚠ **Причину подписываем адресатом.** «За сутки ему уже ушло два
        # письма» — правда, но про кого? В заходе десятки пар, причины копятся
        # списком, и безымянная строка читается как отказ неизвестно по кому:
        # человек видит её и не знает, ждать ли и кого проверять.
        why = await _too_soon(profile_id_, uid, per_day, gap)
        if why:
            skipped += 1
            _reason(reasons, f'{_addressee(pair)}: {why}')
            continue

        if profile_id_ not in girls:
            girls[profile_id_] = await _girl(profile_id_)

        history = await _history(profile_id_, uid)
        if not history:
            # Переписки нет вовсе — возобновлять нечего. Такое бывает у избранных,
            # добавленных руками: звёздочка есть, разговора не было.
            skipped += 1
            _reason(reasons, f'{_addressee(pair)}: переписки нет — возобновлять нечего')
            continue

        try:
            body = await dating_favorite_mail_write(
                girls[profile_id_], site_id, history, await _companion(site_id, uid))
        except DatingMailAgentError as e:
            # ⚠ Отказ агента обрывает **весь заход**: сосед занят, и следующие
            # пары упрутся в то же самое. Круг вернётся через свой срок.
            logger_info(f'[dating] письма избранным сайта #{site_id} прерваны: {e}')
            stopped = str(e)
            break

        body = str(body or '').strip()
        if not body:
            skipped += 1
            _reason(reasons, f'{_addressee(pair)}: агент ответил пусто')
            continue

        await _put(site_id, profile_id_, pair, body)
        made += 1

    if made or skipped:
        logger_info(f'[dating] письма избранным сайта #{site_id}: готово {made}, '
                    f'пропущено {skipped}, осталось {left}')

    return {'made': made, 'skipped': skipped, 'reasons': reasons, 'left': left,
            'stopped': stopped}


async def dating_favorite_mail_pool_left(site_id: int) -> int:
    """Сколько писем лежит в пуле сайта. Круг добирает до потолка по этому числу."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM dating_favorite_mail'
                " WHERE site_id = %s AND status = 'draft'", (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] пул писем избранным не посчитался: {e}')

        # ⚠ Сомнение — в сторону бездействия: непрочитанный счёт означает
        # неизвестный потолок, а лишние письма стоят вызовов LLM и уходят людям.
        return 10 ** 6

    return int((row or {}).get('n') or 0)


# ── Приватное ────────────────────────────────────────────────────────────────

async def _quiet(site_id: int, profile_id: int = 0) -> list[dict]:
    """Пары «анкета — избранный», где разговор затих: последнее слово наше.

    ⚠ **Последнее слово наше** — это и есть определение затишья. Скажи последним
    он, письмо ему напишет круг ответов (`dating_reply_round.py`), и наше
    возобновление пришло бы вторым подряд, не отвечая на заданный вопрос.

    ⚠ **Кто уже ждёт в пуле — не берём.** Второй черновик тому же человеку это
    два письма подряд, просто отложенных: отправитель шлёт их по одному, но оба
    уйдут ему, и второе — не зная о первом.

    Порядок — по давности разговора: чем дольше молчит, тем нужнее письмо. Заход
    обрывается по потолку, и обрывать он должен свежих, а не забытых.
    """
    # ⚠ «Последняя реплика» считается **по всей почте**, а не по одному источнику:
    # входящие письма (он), наши ответы и оба пула ушедшего (мы). Смотри мы только
    # входящие, затихшим выглядел бы всякий, кому мы писали последним, — включая
    # того, кому письмо ушло пять минут назад.
    sql = (
        'SELECT f.profile_id, f.uid,'
        '       COALESCE(c.name, comp.name, %s) AS name,'
        '       COALESCE(NULLIF(comp.photo, %s), c.photo, %s) AS photo,'
        '       talk.last_at, talk.last_side'
        '  FROM dating_favorite f'
        # ⚠ **Анкета обязана быть в сети**, а не просто включена. Письмо уходит
        # от её имени, и сайт показывает его от лица девушки, которой в этот
        # момент на сайте нет; а круг отправки такое письмо всё равно не возьмёт
        # (`dating_send_round`), и пул набивался бы тем, что не уйдёт.
        "  JOIN dating_profile p ON p.id = f.profile_id AND p.enabled = 1"
        "   AND p.status = 'online'"
        '  JOIN ('
        '     SELECT profile_id, uid,'
        '            MAX(at) AS last_at,'
        '            SUBSTRING_INDEX(MAX(CONCAT(at, %s, side)), %s, -1) AS last_side'
        '       FROM ('
        "          SELECT profile_id, uid, date_post AS at, 'him' AS side"
        '            FROM dating_mail_in'
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM dating_mail_reply WHERE status = 'sent'"
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM dating_mailing WHERE status = 'sent'"
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM dating_favorite_mail WHERE status = 'sent'"
        '       ) AS all_mail'
        '      WHERE at IS NOT NULL'
        '      GROUP BY profile_id, uid) AS talk'
        '    ON talk.profile_id = f.profile_id AND talk.uid = f.uid'
        '  LEFT JOIN dating_contact c'
        '    ON c.profile_id = f.profile_id AND c.uid = f.uid'
        '  LEFT JOIN dating_companion comp'
        '    ON comp.site_id = f.site_id AND comp.uid = f.uid'
        ' WHERE f.site_id = %s AND (%s = 0 OR f.profile_id = %s)'
        # ⚠ Тот же набор, что делит разговоры между «Фаворитами» и «Входящими»
        # (`DATING_FAVORITE_STATES`): кого считаем избранным — один вопрос, и
        # ответ на него должен быть один.
        f'   AND f.state IN ({DATING_FAVORITE_STATES_SQL})'
        "   AND talk.last_side = 'her'"
        # Черновик этому человеку уже ждёт — второй не нужен.
        '   AND NOT EXISTS (SELECT 1 FROM dating_favorite_mail d'
        '                    WHERE d.profile_id = f.profile_id AND d.uid = f.uid'
        "                      AND d.status = 'draft')"
        ' ORDER BY talk.last_at')

    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, ('', '', '', ' ', ' ',
                                   int(site_id), int(profile_id), int(profile_id),
                                   *DATING_FAVORITE_STATES))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] затихшие разговоры сайта #{site_id} '
                    f'не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _too_soon(profile_id: int, uid: str, per_day: int, gap: int) -> str:
    """Рано ли писать этому человеку. Пустая строка — можно.

    ⚠ **Считается всё ушедшее ему от этой анкеты**: рассылка, ответ и
    возобновление. Письма считает получатель, а не наши таблицы — разделяй мы их,
    «два письма в сутки» означало бы шесть.

    ⚠ **Сбой базы — не пишем.** Непрочитанный счёт означает неизвестный предел, а
    лишнее письмо живому человеку отменить нельзя. Та же сторона ошибки, что у
    дневного потолка отправки.
    """
    sql = (
        'SELECT COUNT(*) AS сутки, MAX(sent_at) AS последнее FROM ('
        '   SELECT sent_at FROM dating_mailing'
        "    WHERE profile_id = %s AND uid = %s AND status = 'sent'"
        '   UNION ALL'
        '   SELECT sent_at FROM dating_mail_reply'
        "    WHERE profile_id = %s AND uid = %s AND status = 'sent'"
        '   UNION ALL'
        '   SELECT sent_at FROM dating_favorite_mail'
        "    WHERE profile_id = %s AND uid = %s AND status = 'sent'"
        ') AS ушло'
        ' WHERE sent_at IS NOT NULL AND sent_at > NOW() - INTERVAL 1 DAY')

    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, (int(profile_id), str(uid),
                                   int(profile_id), str(uid),
                                   int(profile_id), str(uid)))
            row = await db.fetchone()

            # Пауза считается от последнего ушедшего письма **за любой срок**, а
            # не только за сутки: письмо, ушедшее 25 часов назад, в первый счёт не
            # попадает, и без второго запроса пауза считалась бы от пустоты.
            await db.execute(
                'SELECT MAX(sent_at) AS последнее FROM ('
                '   SELECT sent_at FROM dating_mailing'
                "    WHERE profile_id = %s AND uid = %s AND status = 'sent'"
                '   UNION ALL'
                '   SELECT sent_at FROM dating_mail_reply'
                "    WHERE profile_id = %s AND uid = %s AND status = 'sent'"
                '   UNION ALL'
                '   SELECT sent_at FROM dating_favorite_mail'
                "    WHERE profile_id = %s AND uid = %s AND status = 'sent'"
                ') AS ушло',
                (int(profile_id), str(uid), int(profile_id), str(uid),
                 int(profile_id), str(uid)))
            last = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] счёт писем {uid} у анкеты #{profile_id} '
                    f'не спросился: {e}')

        return 'не удалось посчитать, сколько ему уже написали'

    day_sent = int((row or {}).get('сутки') or 0)
    when = (last or {}).get('последнее')
    since = await _since(when) if when is not None else None

    # ⚠ **Дневной предел называет, когда он отпустит.** «Ушло два — предел два»
    # отвечает на «почему», но не на «и что теперь»: человек у экрана решает,
    # ждать ему или поднимать настройку, а для этого нужен срок. Сутки считаются
    # скользящим окном, поэтому освободится оно тогда, когда из окна выпадет самое
    # давнее письмо, — но проще и честнее сказать про ближайшее.
    if day_sent >= int(per_day):
        count_word = f'{day_sent} {_letters_word(day_sent)}'

        return (f'за сутки уже ушло {count_word} — предел {int(per_day)} в сутки'
                + (f', последнее {_ago_word(since)}' if since is not None else ''))

    if since is not None and since < int(gap):
        # Сколько ещё ждать — вместо «прошло меньше четырёх часов». Первое можно
        # прочитать и решить, второе требует считать в уме.
        return (f'писали {_ago_word(since)}, пауза {_hours_word(gap)} — '
                f'можно через {_left_word(int(gap) - since)}')

    return ''


def _letters_word(n: int) -> str:
    """Склонение: «1 письмо», «2 письма», «5 писем».

    ⚠ Не украшение: строка идёт человеку на экран, и «ушло 2 писем» читается как
    сбой подстановки — то есть заставляет усомниться в самом числе.
    """
    tail = abs(int(n)) % 100
    if 11 <= tail <= 14:
        return 'писем'

    return {1: 'письмо', 2: 'письма', 3: 'письма', 4: 'письма'}.get(tail % 10, 'писем')


def _hours_word(seconds: int) -> str:
    """Срок словами: «4 часа». Настройка в секундах, человек думает часами."""
    hours = max(1, round(int(seconds) / 3600))

    tail = hours % 100
    if 11 <= tail <= 14:
        word = 'часов'
    else:
        word = {1: 'час', 2: 'часа', 3: 'часа', 4: 'часа'}.get(hours % 10, 'часов')

    return f'{hours} {word}'


def _left_word(seconds: int) -> str:
    """Сколько ещё ждать: «2 ч 40 мин», «35 мин».

    ⚠ Минуты нужны: пауза бывает в час, и «через 1 ч» вместо «через 5 мин»
    отправило бы человека ждать впустую.
    """
    minutes = max(1, round(int(seconds) / 60))
    if minutes < 60:
        return f'{minutes} мин'

    hours, rest = divmod(minutes, 60)

    return f'{hours} ч' + (f' {rest} мин' if rest else '')


def _ago_word(seconds: int) -> str:
    """Когда это было: «40 мин назад», «6 ч назад»."""
    return f'{_left_word(seconds)} назад'


async def _since(when) -> int:
    """Сколько секунд прошло с метки. ⚠ Считает **база**, а не процесс.

    Метки в базе стоят по часам сервера MySQL, а процесс живёт по своим: разойдись
    они на час — и пауза в четыре часа превратилась бы то в три, то в пять. Спорить
    об этом незачем, когда ту же разницу умеет посчитать тот, кто хранит метку.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT TIMESTAMPDIFF(SECOND, %s, NOW()) AS сек', (when,))
        row = await db.fetchone()

    return int((row or {}).get('сек') or 0)


async def _history(profile_id: int, uid: str) -> list[dict]:
    """Переписка с человеком, старые сверху: то, что агент прочитает.

    ⚠ **Из четырёх источников**, и четвёртый — этот же пул: возобновление могло
    уже уходить, и агент, не зная о нём, написал бы то же самое второй раз.

    Устроено как `_history` у ответов (`dating_mail_reply.py`) и намеренно
    одинаково: это одна и та же переписка, и два разных её вида означали бы, что
    агент ответа и агент возобновления видят разные разговоры.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                "SELECT 'him' AS side, body, date_post AS at, id FROM dating_mail_in"
                ' WHERE profile_id = %s AND uid = %s'
                ' UNION ALL'
                " SELECT 'her' AS side, body, sent_at AS at, id FROM dating_mail_reply"
                " WHERE profile_id = %s AND uid = %s AND status = 'sent'"
                ' UNION ALL'
                " SELECT 'her' AS side, body, sent_at AS at, id FROM dating_mailing"
                " WHERE profile_id = %s AND uid = %s AND status = 'sent'"
                ' UNION ALL'
                " SELECT 'her' AS side, body, sent_at AS at, id"
                ' FROM dating_favorite_mail'
                " WHERE profile_id = %s AND uid = %s AND status = 'sent'"
                ' ORDER BY at DESC, id DESC LIMIT %s',
                (int(profile_id), str(uid), int(profile_id), str(uid),
                 int(profile_id), str(uid), int(profile_id), str(uid),
                 DATING_FAVORITE_MAIL_HISTORY))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] переписка {uid} у анкеты #{profile_id} '
                    f'не спросилась: {e}')

        return []

    # Разворачиваем: агенту переписку отдаём старыми сверху, как её читает человек.
    # Обрезали же с конца — иначе «последние десять» оказались бы самыми старыми.
    history = [{'from': str(row['side']), 'text': str(row['body'] or '').strip()}
               for row in reversed(rows or [])]

    return [one for one in history if one['text']]


async def _girl(profile_id: int) -> dict:
    """Анкета, от имени которой пишем."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, login, profile FROM dating_profile'
                         ' WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    return dict(row or {})


async def _companion(site_id: int, uid: str) -> dict:
    """Карточка собеседника, если она есть."""
    from src.uvicorn.routers.dating.dating_companion import dating_companion_get

    return await dating_companion_get(int(site_id), str(uid)) or {}


async def _seconds(site_id: int, key: str, fallback: int) -> int:
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id, key) or fallback)


async def _put(site_id: int, profile_id: int, pair: dict, body: str) -> None:
    """Кладёт письмо в пул.

    ⚠ **Без `INSERT IGNORE`, в отличие от прочих пулов**: уникального ключа на
    пару здесь нет — одному человеку пишут много раз, в том и смысл занятия. От
    двух черновиков разом защищает отбор (`_quiet` не берёт тех, у кого черновик
    уже лежит), а гонка двух заходов стоила бы одного лишнего письма в пуле, где
    его прочтёт человек.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_favorite_mail'
            ' (site_id, profile_id, uid, name, photo, body, status)'
            " VALUES (%s, %s, %s, %s, %s, %s, 'draft')",
            (int(site_id), int(profile_id), str(pair['uid']),
             str(pair.get('name') or ''), str(pair.get('photo') or ''), body))
        await db.connection.commit()


def _addressee(pair: dict) -> str:
    """Как назвать адресата в причине пропуска: имя и номер на сайте.

    ⚠ **Номер обязателен, а имя бывает пустым.** Имя приходит из карточки и
    заполнено не у всех; по одному имени человека к тому же не найти — в пуле
    бывают двое «Michael». Номер же ведёт прямо на его страницу у нас.
    """
    uid = str((pair or {}).get('uid') or '')
    name = str((pair or {}).get('name') or '').strip()

    return f'{name} ({uid})' if name else uid


def _reason(reasons: list, text: str) -> None:
    """Копит первые несколько причин пропуска: страница показывает их человеку."""
    if len(reasons) < 5 and text not in reasons:
        reasons.append(text)


async def dating_favorite_mail_why(site_id: int) -> str:
    """Почему пул не наполняется прямо сейчас. Пусто — наполняется, повода нет.

    Returns:
        str: одна строка для шапки пула — та же, что круг пишет в `reasons`, но
        про **ближайшую** пару: ту, которой письмо появится раньше прочих.

    ⚠ **Отвечает не «когда придёт круг», а «почему сейчас пусто»**, и это разные
    вопросы. Круг может приходить каждые полчаса и каждый раз уходить ни с чем:
    ближайшей паре нельзя писать ещё три часа, и человек, видящий только срок
    круга, ждёт набора к этому сроку — а его не будет. Наблюдалось живьём: анкета
    в сети, её избранный в сети, генерация включена, пул пуст, и объяснения нет
    нигде, кроме журнала воркера.

    ⚠ **Считается по тем же правилам, что и набор** (`_quiet`, `_too_soon`) — не
    второй их копией: разойдись они, шапка обещала бы письмо, которого набор не
    сделает, или молчала бы там, где он работает.
    """
    site_id = int(site_id or 0)
    if not site_id:
        return ''

    try:
        pairs = await _quiet(site_id)
    except Exception as e:
        logger_info(f'[dating] повод пула избранных не спросился: {e}')

        return ''

    if not pairs:
        # ⚠ Не «ошибка», а обычное состояние: писать некому — все разговоры живы,
        # либо избранных нет вовсе, либо анкеты не в сети. Пул при этом пуст
        # законно, и человек должен читать это как ответ, а не как поломку.
        return 'писать некому: у избранных нет затихших разговоров'

    per_day = await _seconds(site_id, DATING_FAVORITE_MAIL_PER_DAY_SETTING,
                             DATING_FAVORITE_MAIL_PER_DAY_DEFAULT)
    gap = await _seconds(site_id, DATING_FAVORITE_MAIL_GAP_SETTING,
                         DATING_FAVORITE_MAIL_GAP_DEFAULT)

    reasons = []
    for pair in pairs:
        why = await _too_soon(int(pair['profile_id']), str(pair['uid']), per_day, gap)
        # Есть хоть одна пара, которой можно писать, — повода объясняться нет:
        # круг наберёт её на ближайшем заходе.
        if not why:
            return ''

        reasons.append(f"{_addressee(pair)}: {why}")

    # ⚠ Показываем **одну** причину, а не список: шапка пула — одна строка, и
    # десять причин в ней превратились бы в полосу, которую не читают. Первая —
    # про ближайшую пару: `_quiet` отдаёт их по свежести разговора.
    tail = f' (и ещё {len(reasons) - 1})' if len(reasons) > 1 else ''

    return reasons[0] + tail
