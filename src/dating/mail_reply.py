"""Пул ответов: агент пишет ответы на входящие письма, человек их читает.

Устроено как набор пула рассылки (`mailing.py`) и по той же причине: между
«панель написала» и «письмо ушло» должно быть место, где текст читают. Разница в
поводе — там пишут первыми, здесь отвечают на присланное.

## ⚠ Что берём в работу

Письмо годится для ответа, когда сошлось всё:

| Условие | Почему |
|---------|--------|
| у сайта включена генерация (`reply_make_on`) | решение человека, и оно про весь сайт |
| у письма есть текст | отвечать на пустоту нечем: тело ещё не забрали |
| на него ещё не отвечали | один ответ на письмо, иначе их два подряд |
| оно последнее в переписке | см. ниже |

⚠ **Отвечаем на последнее письмо человека, а не на каждое.** Он мог написать три
подряд, не дождавшись ответа; три ответа в очередь на них — это не переписка, а
автоответчик. Берём последнее, а прежние идут в историю.

## ⚠ История — не больше N последних

Настройка сайта `reply_history` (обычно 10). Причина не в цене: переписку на сорок
писем модель читает по диагонали и отвечает на первое попавшееся вместо последнего.

Историю собираем из **двух источников**: входящие письма (`mail_in`) и наши
ответы (`mail_reply`) — иначе агент не знает, что уже сказал, и здоровается
второй раз.

## Чего здесь нет

**Отправки.** Ответ ложится в пул и ждёт. Уходит он отдельным решением — выключатель
«отвечать» у сайта (`reply_send_on`), выключенный по умолчанию. Та же граница, что у
рассылки: необратимый шаг остаётся за человеком.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_agent import DatingMailAgentError, mail_reply

# Настройка сайта: сколько писем переписки отдаём агенту.
MAIL_REPLY_HISTORY_SETTING = 'reply_history'

# Сколько берём, если настройки нет. Десять — то же умолчание, что в реестре сайта.
MAIL_REPLY_HISTORY_DEFAULT = 10

# Жёсткий потолок поверх настройки: значение правят руками, и «500» превратило бы
# промпт в простыню, на которую модель ответит первым, что увидит.
MAIL_REPLY_HISTORY_MAX = 50

# Сколько писем переписки показываем человеку в окне ответа.
#
# ⚠ Больше, чем отдаём агенту: тот читает, чтобы ответить, — ему хватает последних;
# человек читает, чтобы **проверить ответ**, и ему важно, о чём говорили раньше.
MAIL_REPLY_THREAD_SHOW = 30

# Сколько ответов пишем за один заход. Каждый — вызов LLM в несколько секунд; больше
# десятка означало бы заход на минуты, а круг вернётся и доберёт.
MAIL_REPLY_BATCH = 10


async def mail_reply_make(site_id: int, limit: int = 0) -> dict:
    """Пишет ответы на входящие письма сайта и кладёт их в пул.

    Args:
        site_id: сайт-закладка.
        limit: сколько ответов пробуем за заход. Ноль — предел по умолчанию.

    Returns:
        dict: `made` — сколько ответов готово, `skipped` — сколько писем пропущено,
        `reasons` — почему (первые несколько), `left` — сколько осталось на потом.

    ⚠ **Повторный заход продолжает с того же места**, а не делает то же самое:
    письма, на которые ответ уже лежит, пропускаются. Поэтому круг может будить
    набор хоть каждую минуту, не заваливая пул повторами.
    """
    letters = await _to_answer(site_id)
    if not letters:
        return {'made': 0, 'skipped': 0, 'reasons': [], 'left': 0}

    keep = await _history_size(site_id)
    cap = min(int(limit) or MAIL_REPLY_BATCH, MAIL_REPLY_BATCH)

    made, skipped, reasons, left = 0, 0, [], 0

    for letter in letters:
        if made + skipped >= cap:
            left += 1
            continue

        history = await _history(letter, keep)
        if not history:
            # Текста нет ни у одного письма переписки — отвечать не на что.
            skipped += 1
            _reason(reasons, 'у письма нет текста — тело ещё не забрали с сайта')
            continue

        profile = await _profile(int(letter['profile_id']))
        companion = await _companion(site_id, str(letter['uid']))

        try:
            body = await mail_reply(profile, site_id, history, companion)
        except DatingMailAgentError as e:
            # ⚠ Отказ агента обрывает **весь заход**: сосед занят, и следующие
            # письма упрутся в то же самое. Круг вернётся через свой срок.
            logger_info(f'[dating] ответы сайта #{site_id} прерваны: {e}')
            _reason(reasons, str(e))
            break

        body = str(body or '').strip()
        if not body:
            skipped += 1
            _reason(reasons, 'агент ответил пусто')
            continue

        await _put(site_id, letter, body)
        made += 1

    if made or skipped:
        logger_info(f'[dating] ответы сайта #{site_id}: готово {made}, '
                    f'пропущено {skipped}, осталось {left}')

    return {'made': made, 'skipped': skipped, 'reasons': reasons, 'left': left}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _to_answer(site_id: int) -> list[dict]:
    """Письма, на которые стоит ответить, — свежие сверху.

    ⚠ **Только последнее письмо от каждого собеседника.** Он мог написать три
    подряд, не дождавшись ответа; три ответа в очередь на них — не переписка, а
    автоответчик. Прежние при этом не теряются: они уйдут в историю.

    ⚠ **Только у сайта с включённой генерацией** (`site_.reply_make_on`).
    Решение человека, и оно про сайт целиком: пул ответов один на сайт, и «отвечаем
    ли вообще» отвечается один раз.

    Прежде флаг жил у анкеты (`profile_.reply_on`) — «у этой отвечаем, у той
    пишем руками». Отбор был честным, но включать его приходилось сорок раз, а
    забытая анкета молчала, и причину искали в поломке. Вдобавок на странице этот
    выключатель стоит рядом с «отправкой ответов», а та про сайт: два соседних
    флажка по разным правилам объяснить нельзя (миграция
    `20260910120000_dating_site_reply_make_on.sql`).

    ⚠ Понадобится отбор по анкетам — возвращать его надо **вторым** уровнем, как у
    рассылки (расписание сайта плюс «Письма рассылка» у анкеты), а не заменой.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.id, m.site_id, m.profile_id, m.uid, m.name, m.body,'
            ' m.date_post'
            ' FROM mail_in m'
            ' JOIN profile p ON p.id = m.profile_id'
            ' LEFT JOIN mail_reply r ON r.mail_in_id = m.id'
            ' JOIN site s ON s.site_id = m.site_id'
            ' WHERE m.site_id = %s AND s.reply_make_on = 1 AND p.enabled = 1'
            "   AND m.body <> '' AND r.id IS NULL"
            # ⚠ Последнее письмо пары «анкета — собеседник»: `NOT EXISTS` по более
            # свежему. Так же надёжно, как оконная функция, и работает на любой
            # версии MySQL.
            '   AND NOT EXISTS ('
            '       SELECT 1 FROM mail_in n'
            '        WHERE n.profile_id = m.profile_id AND n.uid = m.uid'
            '          AND (n.date_post > m.date_post'
            '               OR (n.date_post = m.date_post AND n.id > m.id)))'
            ' ORDER BY m.date_post DESC, m.id DESC', (int(site_id),))
        rows = await db.fetchall()

    return [dict(row) for row in rows or []]


async def mail_reply_thread(profile_id: int, uid: str,
                                   keep: int = 0) -> list[dict]:
    """Переписка с человеком — для показа человеку, а не агенту.

    Args:
        profile_id: наша анкета.
        uid: собеседник на сайте.
        keep: сколько последних писем. Ноль — предел показа.

    Returns:
        list: старые сверху,
        `{'id', 'from': 'him'|'her', 'kind', 'text', 'at', 'status'}`.

        ⚠ **Неотправленный ответ стоит последним**, а не по времени, когда его
        написали. Разговор — это то, что стороны сказали друг другу; черновик из
        пула никто не видел, и место ему в конце, где он и ждёт отправки.

        Поймано глазами: ответ, написанный в 15:28, встал между письмами 12:29 и
        20:32 — как будто девушка его отправила, а собеседник продолжил разговор,
        не заметив. У ушедших же реплик время **отправки**, а не сочинения:
        написать могли за час до того, как письмо ушло.

        `kind` — откуда реплика: `in` (его письмо), `reply` (наш ответ), `mailing`
        (письмо рассылки). Человеку это видно пометкой: письмо рассылки он писал
        не сам, и путать его с ответом не должен.

        ⚠ **Писем «Приставания» здесь нет.** Их сюда добавляли и убрали: занятие
        отдельное, со своим пулом и своей подвкладкой, а в ленте «Входящих» оно
        читалось как смешение двух пулов — рядом с ответом лежал чужой черновик,
        а форма под лентой правила ответ.

        ⚠ Номер нужен, чтобы вызывающий мог исключить **текущий** ответ: он и так
        стоит ниже, в поле правки, и вторым разом тем же текстом только сбивал бы.
        Номера двух таблиц пересекаются, но вместе со стороной (`from`) пара
        однозначна.

    ⚠ **Отдельно от `_history`, хотя источник один.** Тому нужен голый текст: даты и
    состояния в промпте только сбивают модель. Здесь наоборот — человек читает
    переписку и первым делом смотрит, когда что было и ушёл ли наш ответ.

    Слей мы их в одну функцию с флагом, у флага не было бы имени, и следующий
    читатель гадал бы, зачем он.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            "SELECT 'him' AS side, 'in' AS kind, body, date_post AS at, id,"
            " '' AS status, 0 AS pending"
            ' FROM mail_in WHERE profile_id = %s AND uid = %s'
            ' UNION ALL'
            # ⚠ Время ответа — **отправки**, а не сочинения: между ними бывает
            # час, и по времени написания ответ вставал бы в разговор раньше, чем
            # собеседник его получил. Неотправленному времени нет вовсе; его
            # выносит вниз `pending`.
            " SELECT 'her' AS side, 'reply' AS kind, body,"
            ' COALESCE(sent_at, created_at) AS at, id, status,'
            " (status = 'draft') AS pending"
            ' FROM mail_reply WHERE profile_id = %s AND uid = %s'
            ' UNION ALL'
            # ⚠ **Письма рассылки — тоже часть разговора**: чаще всего именно ими он
            # и начат, и он отвечал на них, а не написал сам. Только отправленные:
            # лежащее в пуле он не видел.
            " SELECT 'her' AS side, 'mailing' AS kind, body, sent_at AS at, id,"
            ' status, 0 AS pending'
            " FROM mailing WHERE profile_id = %s AND uid = %s AND status = 'sent'"
            # ⚠ **Писем «Приставания» здесь нет, и это решение.** Их добавляли
            # сюда — и убрали: занятие отдельное, со своим пулом, своей
            # подвкладкой и своим кругом. В ленте «Входящих» рядом с ответом
            # лежал его черновик, а форма под лентой правила ответ — два пула в
            # одном месте читались как смешение, чем и были.
            #
            # Приставания видны там, где ими и заняты: пул на «Фаворитах →
            # Приставание», ушедшие — на «Фаворитах → Доставка».
            # ⚠ `pending` первым и **по убыванию**: выборка идёт от новых к старым
            # и переворачивается ниже, так что ждущие отправки должны стоять в её
            # конце — только тогда после разворота они окажутся внизу разговора.
            #
            # Порядок сортировки тут легко перепутать, и ошибка выглядит
            # безобидно: черновик просто встаёт первой репликой, будто разговор им
            # и начали. Поймано глазами на живой переписке.
            ' ORDER BY pending DESC, at DESC, id DESC LIMIT %s',
            (int(profile_id), str(uid), int(profile_id), str(uid),
             int(profile_id), str(uid),
             int(keep) or MAIL_REPLY_THREAD_SHOW))
        rows = await db.fetchall()

    # Старые сверху — как читает человек. Обрезали же с конца: «последние N».
    # ⚠ **Лица — одни на всю нить, а не по реплике.** У письма своего фото нет:
    # оно у людей, а людей в разговоре двое. Класть его в каждую строку значило бы
    # тащить одну и ту же пару адресов по тридцать раз.
    #
    # Без этого лента рисовалась серыми кружками: разметка фото ждала, а ответ его
    # не отдавал вовсе — видно было только глазом, на живой переписке.
    faces = await _faces(int(profile_id), str(uid))

    return [{'id': int(row['id']), 'from': str(row['side']),
             'kind': str(row['kind']), 'text': str(row['body'] or ''),
             'at': row['at'], 'status': str(row['status'] or ''),
             'photo': faces['him' if str(row['side']) == 'him' else 'her']}
            for row in reversed(rows or [])]


async def _faces(profile_id: int, uid: str) -> dict:
    """Лица сторон разговора: `him` — собеседника, `her` — анкеты.

    ⚠ **Фото собеседника ищем в трёх местах**, и порядок тот же, что у диалогов
    чата (`api.md`, §3.8): контакт анкеты свежее всего, потом карточка сайта,
    потом письмо. `NULLIF` обязателен — пустая строка в `contact.photo`
    обычное дело, а `COALESCE` считает её значением.

    ⚠ **Сбой не роняет разговор**: вернём пустые адреса, и лента нарисуется
    серыми кружками. Потерять переписку из-за аватарки было бы худшим разменом.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COALESCE(NULLIF(c.photo, %s), NULLIF(comp.photo, %s),'
                '                NULLIF(m.photo, %s), %s) AS him,'
                '       p.profile AS card'
                '  FROM profile p'
                '  LEFT JOIN contact c'
                '    ON c.profile_id = p.id AND c.uid = %s'
                '  LEFT JOIN companion comp'
                '    ON comp.site_id = p.site_id AND comp.uid = %s'
                '  LEFT JOIN mail_in m'
                '    ON m.profile_id = p.id AND m.uid = %s'
                " AND m.photo <> ''"
                ' WHERE p.id = %s LIMIT 1',
                ('', '', '', '', str(uid), str(uid), str(uid), int(profile_id)))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] лица переписки #{profile_id} не спросились: {e}')

        return {'him': '', 'her': ''}

    from src.dating.json_column import json_column_load

    card = json_column_load((row or {}).get('card')) or {}

    return {'him': str((row or {}).get('him') or ''),
            'her': str(card.get('photo') or '')}


async def _history(letter: dict, keep: int) -> list[dict]:
    """Переписка с этим человеком: последние `keep` писем, старые сверху.

    ⚠ **Из трёх источников**, и третий — не мелочь.

    | Что | Откуда | Чья реплика |
    |-----|--------|-------------|
    | его письма | `mail_in` | его |
    | наши ответы | `mail_reply` | наша |
    | **письма рассылки** | `mailing`, только `sent` | наша |

    Третий добавлен потому, что разговор чаще всего **начинали мы**: ушло письмо
    рассылки, он на него ответил. Без него агент видит переписку с середины — «Moto
    is my life» без вопроса, на который это ответ, — и пишет, будто человек заговорил
    сам собой.

    ⚠ **Только отправленные письма рассылки.** Лежащее в пуле он не видел, и считать
    его сказанным значило бы отвечать на разговор, которого не было.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            "SELECT 'him' AS side, body, date_post AS at, id FROM mail_in"
            ' WHERE profile_id = %s AND uid = %s'
            ' UNION ALL'
            " SELECT 'her' AS side, body, created_at AS at, id FROM mail_reply"
            ' WHERE profile_id = %s AND uid = %s'
            ' UNION ALL'
            " SELECT 'her' AS side, body, sent_at AS at, id FROM mailing"
            " WHERE profile_id = %s AND uid = %s AND status = 'sent'"
            # Свежие сверху — и обрезаем; порядок для промпта развернём ниже.
            ' ORDER BY at DESC, id DESC LIMIT %s',
            (int(letter['profile_id']), str(letter['uid']),
             int(letter['profile_id']), str(letter['uid']),
             int(letter['profile_id']), str(letter['uid']), int(keep)))
        rows = await db.fetchall()

    # ⚠ Разворачиваем: агенту переписку отдаём **старыми сверху**, как её читает
    # человек. Обрезали же с конца — иначе «последние десять» оказались бы первыми
    # десятью, то есть самыми старыми.
    history = [{'from': str(row['side']), 'text': str(row['body'] or '').strip()}
               for row in reversed(rows or [])]

    return [one for one in history if one['text']]


async def _history_size(site_id: int) -> int:
    """Сколько писем переписки отдаём агенту."""
    from src.uvicorn.routers.dating.site_ import site_seconds

    return min(int(await site_seconds(site_id,
                                             MAIL_REPLY_HISTORY_SETTING)
                   or MAIL_REPLY_HISTORY_DEFAULT),
               MAIL_REPLY_HISTORY_MAX)


async def _profile(profile_id: int) -> dict:
    """Анкета, от имени которой отвечаем."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, login, profile FROM profile'
                         ' WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    return dict(row or {})


async def _companion(site_id: int, uid: str) -> dict:
    """Карточка собеседника, если она есть. Нет — пустой словарь, и агент об этом
    узнает словами: выдумывать про него нечего."""
    from src.uvicorn.routers.dating.companion import companion_get

    return await companion_get(int(site_id), str(uid)) or {}


async def _put(site_id: int, letter: dict, body: str) -> None:
    """Кладёт ответ в пул. `INSERT IGNORE` ловит гонку двух заходов разом.

    ⚠ Уникальный ключ по `mail_in_id`: один ответ на письмо. Два ответа подряд
    одному человеку выглядят поломкой — ею и являются.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO mail_reply'
            ' (site_id, profile_id, mail_in_id, uid, name, body, status)'
            " VALUES (%s, %s, %s, %s, %s, %s, 'draft')",
            (int(site_id), int(letter['profile_id']), int(letter['id']),
             str(letter['uid']), str(letter['name'] or ''), body))
        await db.connection.commit()


def _reason(reasons: list, text: str) -> None:
    """Копит первые несколько причин пропуска: страница показывает их человеку."""
    if len(reasons) < 5 and text not in reasons:
        reasons.append(text)
