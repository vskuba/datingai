"""Письма, написанные живым человеком на сайте, а не нами.

## ⚠⚠ Откуда модуль: письмо, помеченное отвеченным, без нашего ответа

Иногда на письмо мужчины отвечает **живой оператор**: зашёл на сайт от имени
девушки, напечатал, отправил — и письмо на сайте стало «отвеченным»
(`mail_in.is_replied = 1`), а в наших пулах от него **пусто**. Круг ответов
(`mail_reply.py`) видел такое письмо свободным и сочинял второй ответ: мужчина
получал две реплики подряд, одну живым стилем, другую агентом. Решение владельца
26.11.2026: такие письма **не отвечает никто из наших**, а то, что написал
человек, обязано дойти до агента — иначе он продолжит разговор, не зная, что
его половину уже сказали.

## ⚠ Откуда берётся текст: страница истории панели, а не API сайта

API сайта отправленных писем не показывает вовсе. Показывает **панель
агентства**: `usermessagehistory/{её номер}?id2={его номер}` — письмо за
письмом, свежие сверху, пять на страницу. Путь и разбор знает драйвер панели
сайта (`site_panel.py`, `HISTORY_PATH`); у сайта, который такой страницы не
знает, проверка пары проходит **молча**: письма человека у него нет, и
вызывать несуществующую ручку незачем.

## Что делает модуль, по порядку

1. `mail_human_sweep` — дверь из круга ответов: сначала **проверка пар**
   (`_pairs` → `mail_human_pull`), потом **доклад** того, что уже лежит
   и ещё не доехало (`mail_human_report`).
2. `mail_human_pull` — по правилу владельца от 27.11.2026 «отвечено на сайте»
   означает: ответ человека — **верхнее (самое свежее) её письмо** на первой
   странице истории пары, новее его письма. Одна страница, одно письмо; глубже
   листать нечем — там история, которую прошлые проверки уже разобрали.
   Близнец **нашей** отправки (текстом, как `message_twin`, и временем с
   допуском) — сетка безопасности: тогда брать нечего. Отметку
   `mail_in.human_checked_at` ставит только после **успешной** страницы:
   сорванная проверка повторится следующим кругом.
3. `mail_human_answered` — **последний взгляд на сайт перед отправкой**
   нашего ответа: агент сочиняет минуты, задержка держит часы, и человек
   мог успеть ответить на сайте **после** проверки круга. Увидев «отвечено»,
   отправка отменяется: черновик уходит из пула, письмо получает
   `is_replied = 1`, пара тут же проходит `mail_human_pull`. Сайт не
   ответил — отправляем как раньше: свой сбой не должен запирать письмо.
4. `mail_human_report` — каждую строку `mail_human` **один раз** докладывает
   соседу (`rhapsody_api_chat_add`, kind `'mail'`, доставленной, временем
   письма). Отметку `history_at` — только по успешному ответу соседа: ноль у
   него **отказ**, а не «уже было» (тот же смысл, что у `chat_history_said`).

## ⚠⚠ Время страницы — часы сайта, по замеру совпадающие с UTC

Страница истории подписана в панели как журнал по «Date UTC», и замер
26.11.2026 на живой паре (письмо 853634242: страница 16:19, в базе 16:19:01)
совпал. Храним как есть, без сдвигов. Но **сверку временем ведём с допуском**
в несколько часов: тот же владелец видел на другой панельной странице 18:19
для того же письма — если какая-то панельная страница пишет по поясному
времени сайта, допуск устоит, а точное равенство нет.

## ⚠ Сторона письма — разметкой, имя только сверяется

Красная шапка письма — её, серая — его; имя вызывающий передаёт для сверки на
случай перевёрстки. Тот же порядок и та же причина, что у импорта чата
(`chat_panel.py`). Имя из письма **никогда не становится ключом** разговора —
имена для ключа берутся из карточек (`agent_vars`, `companion_vars_known`),
ровно поэтому доклад пары не расщепляется, когда панель показывает саму себя
по-другому.
"""

from datetime import datetime, timedelta

from datetime_.datetime_ import datetime_utc_now

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.log import log_write
from src.dating.message_twin import message_twin_same
from src.dating.panel_fetch import DatingPanelFetchError, panel_fetch_html
from src.dating.request import REQUEST_MAIL
from src.dating.site_api import DatingSiteApiError, site_api_call
from src.dating.site_presence import site_presence
from src.dating.site.site_registry import site_registry_panel

# На сколько минут раньше письма отступает `startDate` вопроса сайту. Секунда
# в секунду сайт отвечает списком без него — берём минуту, как делает импорт
# писем (`mail_in._since` по той же причине).
MAIL_HUMAN_LEAD_MIN = 1

# Сколько пар проверяем за один заход круга. Свежих строк на проде единицы
# (письмо человека — событие, а не будни); потолок — предохранитель от
# накопившегося, а не рабочее ограничение.
MAIL_HUMAN_PAIRS = 10

# Сколько строк докладываем соседу за заход. Тот же смысл, что у
# `CHAT_HISTORY_SAID_MAX`: заход будится кругом часто, догонять нечем.
MAIL_HUMAN_REPORT_MAX = 20

# Допуск по времени, часам. Граница между «наше письмо, вернувшееся из
# истории своим близнецом» и «оператор два раза набрал одно и то же». Замер
# близнецов чата (`message_twin.py`) — секунды, но там часы одни; страница
# истории — панельные часы, и про них известно ровно то, что написано в
# шапке модуля. Четыре часа покрывают любой часовой пояс сайта и не дают
# слиться двум разным письмам: один и тот же текст дважды за четыре часа
# одному человеку — это и есть повтор, а не новая реплика.
MAIL_HUMAN_TOLERANCE_HOURS = 4


async def mail_human_sweep(site_id: int) -> dict:
    """Проверяет пары с человеческим ответом и докладывает недоложенное.

    Дверь из круга ответов (`mail_reply_make`): до отбора писем — какие из
    «отвеченных на сайте» ответов написал человек.

    Args:
        site_id: сайт-закладка.

    Returns:
        dict: `checked` — сколько пар заглянули в историю, `letters` — сколько
        писем человека добавили, `reported` — сколько доложили соседу.

    ⚠ Исход круга ответов **не зависит** от этого словаря: не получилось
    проверить — круг всё равно отвечает на то, что умеет, а пара с сорванной
    проверкой остаётся неотмеченной и попадёт в следующий заход.
    """
    pairs = await _pairs(site_id)

    letters, failed = 0, 0
    for profile_id, uid in pairs:
        try:
            letters += await mail_human_pull(site_id, profile_id, uid)
        except (DatingPanelFetchError, ValueError) as e:
            # ⚠ Срыв — не беда круга, а неотмеченная пара: отметка встала бы
            # только после успешного забора, и попытка повторится.
            failed += 1
            logger_info(f'[dating] история пары ({profile_id}, {uid}) '
                        f'сайта #{site_id} не забрана: {e}')

    reported = await mail_human_report(site_id)

    if pairs or reported:
        logger_info(f'[dating] письма человека: сайт #{site_id}, пар {len(pairs)}'
                    f' (срывов {failed}), писем {letters}, докладов {reported}')

    return {'checked': len(pairs), 'failed': failed, 'letters': letters,
            'reported': reported}


async def mail_human_pull(site_id: int, profile_id: int, uid: str) -> int:
    """Забирает страницу истории пары: на каждое его отвеченное письмо — её первое после него.

    Правило владельца от 27.11.2026: когда сайт говорит «отвечено», а нашего
    ответа нет, живой человек ответил **тем самым письмом девушки, что лежит
    на странице первым**, — свежее его письма, на которое отвечает. Дальше по
    странице — переписка, которую разбирали прошлые проверки, и второй
    страницы для этого правила не нужно вовсе.

    Args:
        site_id: сайт-закладка.
        profile_id: наша анкета.
        uid: его номер на сайте.

    Returns:
        int: сколько писем добавилось. Пары, у панели которой страницы истории
        нет, — законный ноль, **молча**: угадывать путь по чужой разметке
        незачем, `HISTORY_PATH` драйвера и есть ответ.

    Raises:
        DatingPanelFetchError: панель отказала (сессия, 404) — пара остаётся
            непроверенной и повторится.
        ValueError: страница ответила, но разобрать её нельзя — разметка
            изменилась; молча пройти тоже было бы враньём.
    """
    driver = site_registry_panel(await site_presence(int(site_id)))

    if not driver.HISTORY_PATH:
        # ⚠ Не молча **пропустить проверку**, а всё же отметить письма? Нет:
        # отметка значит «мы смотрели историю», а здесь её не смотрели, и на
        # сайте, который заговорит с панелью позже, пара уже не всплывёт.
        # Такие пары круг будет трогать каждый раз — ценой одного `SELECT`,
        # похода к панели здесь нет вовсе.
        return 0

    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s LIMIT 1',
                         (int(profile_id),))
        girl = await db.fetchone()

        # Отсечка: самое старое его письмо без нашего ответа. Ответ
        # человека — её письмо **новее** него; старее — разговор, который
        # прошлые проверки уже разобрали.
        await db.execute(
            'SELECT m.date_post, m.name'
            ' FROM mail_in m'
            ' WHERE m.profile_id = %s AND m.uid = %s AND m.is_replied = 1'
            '   AND m.human_checked_at IS NULL'
            '   AND NOT EXISTS ('
            '       SELECT 1 FROM mail_reply r WHERE r.mail_in_id = m.id)'
            ' ORDER BY m.date_post',
            (int(profile_id), str(uid)))
        his = [dict(one) for one in (await db.fetchall() or [])]

    if not girl:
        raise ValueError(f'анкеты #{profile_id} нет в базе')

    card = _card(girl.get('profile'))
    her_uid = str(girl.get('profile_id') or card.get('id') or '')

    if not her_uid:
        raise ValueError(f'анкета #{profile_id} без номера на сайте')

    girl_name = str(card.get('name') or '')
    man_name = next((str(one.get('name') or '') for one in reversed(his)
                     if one.get('name')), '')

    # Его самое старое письмо без нашего ответа — **отсечка**: ответ человека
    # новее его. Время страницы по замеру — те же UTC (шапка модуля), поэтому
    # сверка строгая; время не разобрался — письмо **не берём**: без даты
    # сказать «новее» нельзя, а брать вслепую — выдать агенту уже виденное
    # прошлыми проверками как свежий ответ.

    raw = await panel_fetch_html(
        int(site_id), driver.HISTORY_PATH.format(uid=her_uid),
        {'id2': str(uid), 'page': '1'})
    letters = driver.history_page(raw, her=girl_name, him=man_name)

    # ⚠ Пустая страница — **конец истории**, а не отказ: за последнюю панель
    # отвечает пустым списком, а не ошибкой. Писем нет — брать нечего.
    # ⚠⚠ **На каждое его отвеченное письмо — её первое письмо после него.**
    # Владелец сформулировал правило как «верхнее письмо на странице», и для
    # одного неотвеченного письма это одно и то же. Но Mark → Ekaterina
    # 26.11.2026 написал дважды (16:19 и 20:48 UTC), человек ответил на оба, и
    # «верхнее» взяло только второй ответ — первый (19:06 по панели) пропал.
    # ⚠ Сетка безопасности `_ours` остаётся: наш собственный текст, ушедший
    # через сайт, возвращается истории тем же письмом — его человек не писал.
    hers = sorted(
        ((when, one) for one in letters
         if one.get('side') == 'her' and str(one.get('body') or '')
         for when in [_moment(one.get('at'))] if when),
        key=lambda pair: pair[0])

    picked = {}
    for row in his:
        sent = row.get('date_post')
        if not isinstance(sent, datetime):
            continue

        answer = next((pair for pair in hers if pair[0] > sent), None)
        if answer:
            picked[int(answer[1].get('message_id') or 0)] = answer

    got = 0

    for when, letter in picked.values():
        if not await _ours(profile_id, uid, letter, when):
            got += await _keep(site_id, profile_id, uid, letter, when)

    # Только **успешный** обход: отметка ставится здесь, в конце, — сорванная
    # страница выше бросила выше и пару не тронула.
    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE mail_in SET human_checked_at = NOW()"
            ' WHERE site_id = %s AND profile_id = %s AND uid = %s'
            '   AND is_replied = 1 AND human_checked_at IS NULL',
            (int(site_id), int(profile_id), str(uid)))
        await db.connection.commit()

    return got


async def mail_human_answered(letter: dict, profile: dict, base: str,
                              password: str) -> bool:
    """Последний взгляд на сайт перед отправкой **нашего** ответа.

    Args:
        letter: строка пула `mail_reply`: нужны `id`, `uid`.
        profile: анкета-отправитель: `id`, `site_id`, `login`.
        base: корень API сайта (настройка `api_base`).
        password: пароль для входа — решает `password_pick`.

    Returns:
        bool: True — человек на сайте **уже ответил**, письмо не должно уйти;
        черновик снят с полки, письмо отмечено, пара проверена забором.
        False — не отвечено, проверить нечем (у сайта нет API-ручки) или
        сайт не ответил: **отправляем как раньше**.

    ⚠⚠ **Зачем, если круг уже проверял пару.** Между сочинением ответа и
    отправкой проходят минуты, а то и часы — `reply_delay` держит черновик
    дольше. Ответив в середине этого пути, человек превращает наш ответ во
    вторую реплику подряд — ровно то, ради чего затеяна вся эта история.
    Спрашиваем сайт прямо перед дверью, а не верим отметке утра.

    ⚠ Свой сбой — **не повод запирать письмо**: не ответил сайт — письмо
    уходит как уходило, след остаётся в общем логе.
    """
    from src.dating.mail_in import (MAIL_IN_ID, MAIL_IN_LIST_PATH,
                                    MAIL_IN_REPLIED, MAIL_IN_ROWS)
    from src.dating.ready import password_pick

    reply_id = int(letter.get('id') or 0)

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT i.id AS in_id, i.site_id, i.message_id, i.date_post'
            ' FROM mail_reply r JOIN mail_in i ON i.id = r.mail_in_id'
            ' WHERE r.id = %s', (reply_id,))
        row = await db.fetchone() or {}

    in_id = int(row.get('in_id') or 0)

    if not in_id:
        # Черновик без письма (письмо за время черновика удалено) — сверять
        # не с чем, дорога прежняя.
        return False

    try:
        body = await site_api_call(
            profile, base, password_pick(profile, password),
            f'{MAIL_IN_LIST_PATH}?fromAccountId={letter.get("uid") or ""}'
            f'&startDate={_since_site(row.get("date_post"))}',
            method='GET', door=REQUEST_MAIL)

    except DatingSiteApiError as e:
        # ⚠ Сомнение — в сторону отправки, как у `mail_reply_panel_due`:
        # свой сбой не должен запирать письмо.
        logger_info(f'[dating] перед отправкой #{reply_id} сайт не ответил: {e}')
        return False

    answered = False

    for one in body.get(MAIL_IN_ROWS) or []:
        if isinstance(one, dict) and _int(one.get(MAIL_IN_ID)) == \
                _int(row.get('message_id')):
            answered = bool(one.get(MAIL_IN_REPLIED))
            break

    if not answered:
        return False

    async with mysql_get_db_async() as db:
        # ⚠ «Отвечено» без **нашей** отправки — иначе IsReplied на странице
        # мог поставить и наш же ответ, ушедший с другой кнопки.
        await db.execute(
            " SELECT 1 FROM mail_reply WHERE mail_in_id = %s"
            "   AND status = 'sent'", (in_id,))

        if await db.fetchone():
            return False

        # ⚠ Снимаем **только черновик**: гонка с другой кнопкой не должна
        # стереть уже ушедшее письмо — ушёл, значит ушёл, прежняя дорога.
        await db.execute(
            " DELETE FROM mail_reply WHERE id = %s AND status = 'draft'",
            (reply_id,))
        done = int(db.rowcount or 0)
        await db.execute(
            ' UPDATE mail_in SET is_replied = 1 WHERE id = %s', (in_id,))
        await db.connection.commit()

    who = str(letter.get('name') or letter.get('uid') or '')

    await log_write(int(profile.get('id') or 0), 'fail',
                    f'ответ не ушёл: на письмо уже ответил человек на сайте'
                    + (f' — {who}' if who else ''))
    logger_info(f'[dating] ответ #{reply_id} не ушёл: на письмо ответил '
                f'человек на сайте (анкета #{profile.get("id")} → '
                f'{letter.get("uid")}, снято: {done})')

    try:
        await mail_human_pull(int(row.get('site_id') or
                                  profile.get('site_id') or 0),
                              int(profile.get('id') or 0),
                              str(letter.get('uid') or ''))
    except (DatingPanelFetchError, ValueError) as e:
        # Забор не сорвал отмену: пара без отметки дойдёт кругом.
        logger_info(f'[dating] после отмены #{reply_id} забор не прошёл: {e}')

    return True


async def mail_human_report(site_id: int, limit: int = 0) -> int:
    """Докладывает соседу недоложенные письма человека. Возвращает число.

    Тот же порядок, что у `chat_history_said`: ключи сторон — **из карточек**
    (`agent_vars`, `companion_vars_known`), а не из письма; `delivered` —
    правда (письмо на сайте, его показали); `source_uid` — `mail_human:{id}`,
    им сосед узнает повтор, если мы доложим дважды.

    ⚠ Неотмеченные строки — **наше молчание**, а не соседа: доложил — отметил,
    не доложил — повторится следующим заходом. Ноль в ответ соседа — отказ,
    тот же смысл, что у `chat_history_said`.
    """
    cap = int(limit) or MAIL_HUMAN_REPORT_MAX

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT h.id, h.profile_id, h.uid, h.body, h.date_post,'
            '       p.site_id, p.login, p.profile_id AS girl_site_uid,'
            '       p.profile'
            ' FROM mail_human h'
            ' JOIN profile p ON p.id = h.profile_id'
            ' WHERE h.site_id = %s AND h.history_at IS NULL'
            ' ORDER BY h.id LIMIT %s', (int(site_id), cap))
        rows = await db.fetchall()

    said = 0
    for row in rows or []:
        if await _said_one(dict(row)):
            said += 1

    return said


# ── Приватное ────────────────────────────────────────────────────────────────


async def _ours(profile_id: int, uid: str, letter: dict, when) -> bool:
    """Это письмо — наш текст, вернувшийся из истории: близнец нашей отправки.

    Сверка **тем же определением**, что прячет отражения в чате
    (`message_twin_same`: скелет, срез панели), и временем — с допуском из
    шапки модуля. Пул один на показ и на вставку: разойдутся, и одна дорога
    покажет близнеца, а другая спрячет.

    ⚠ Временем сверяем по **отправке** (`sent_at`), а не сочинения: страница
    истории знает, когда письмо ушло.
    """
    cond = message_twin_same('ours.body', '%s')
    border = (when - timedelta(hours=MAIL_HUMAN_TOLERANCE_HOURS),
              when + timedelta(hours=MAIL_HUMAN_TOLERANCE_HOURS)) if when \
        else (None, None)

    sql = (
        'SELECT EXISTS ('
        '  SELECT 1 FROM ('
        "    SELECT profile_id, uid, body, sent_at FROM mail_reply"
        "     WHERE status = 'sent'"
        '    UNION ALL'
        "    SELECT profile_id, uid, body, sent_at FROM mailing"
        "     WHERE status = 'sent'"
        '    UNION ALL'
        "    SELECT profile_id, uid, body, sent_at FROM favorite_mail"
        "     WHERE status = 'sent'"
        '    UNION ALL'
        "    SELECT profile_id, uid, body, sent_at FROM intention_mail"
        "     WHERE status = 'sent'"
        '  ) AS ours'
        '  WHERE ours.profile_id = %s AND ours.uid = %s'
        + ('    AND ours.sent_at BETWEEN %s AND %s' if when else '')
        + '    AND ' + cond + ') AS twin')

    args = [int(profile_id), str(uid)]
    if when:
        args += list(border)
    args += [str(letter.get('body') or '')] * cond.count('%s')

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        row = await db.fetchone()

    return bool((row or {}).get('twin'))


async def _keep(site_id: int, profile_id: int, uid: str, letter: dict,
                when) -> int:
    """Кладёт письмо человека. Уже лежавшее — ноль, молча (`INSERT IGNORE`).

    Номер письма на сайте — ключ повтора (`uq_mail_human`): повторный заход
    той же страницы не заведёт вторую строку и второй доклад.
    """

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO mail_human'
            ' (site_id, profile_id, uid, message_id, body, date_post,'
            '  attachment)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (int(site_id), int(profile_id), str(uid),
             int(letter.get('message_id') or 0),
             str(letter.get('body') or ''), when,
             str(letter.get('attachment') or '')))
        made = int(db.rowcount or 0)
        await db.connection.commit()

    return made


async def _said_one(row: dict) -> bool:
    """Докладывает одно письмо и метит его. Неудача — `False` и журнал."""
    from src.dating.agent import agent_vars
    from src.dating.companion_card import companion_vars_known
    from src.rhapsody.rhapsody_api import rhapsody_api_chat_add

    try:
        # ⚠ Словарь «анкеты» для `agent_vars` — **строка `profile`**, а не
        # `mail_human`: `agent_site_uid` идёт по колонке `profile_id` строки, и
        # подсовывать ей наш номер вместо сайтового нельзя (ступени — в её
        # докстринге).
        girl = agent_vars({'id': row.get('profile_id'),
                           'site_id': row.get('site_id'),
                           'login': row.get('login'),
                           'profile_id': row.get('girl_site_uid'),
                           'profile': row.get('profile')},
                          _card(row.get('profile')))
        companion = await companion_vars_known(row.get('site_id'), row['uid'], '')

        if not companion.get('companion_id'):
            logger_info(f'[dating] письмо #{row["id"]}: собеседника '
                        f'{row["uid"]} не нашли в карточках — доклад отложен')

            return False

        made = await rhapsody_api_chat_add(
            str(girl['girl_id']), str(girl['girl_name']),
            str(companion['companion_id']), str(companion['companion_name']),
            # ⚠ Половина его — **не наше**: у письма-ответа вопрос есть всегда,
            # здесь его половина лежит в `mail_in`, и агент читает её из своей
            # истории. Мы докладываем **её** половину и только её.
            question='', answer=str(row.get('body') or ''), kind='mail',
            source_uid=f'mail_human:{row["id"]}',
            # ⚠ Письмо на сайте и показано адресату: это её след, а не
            # предположение, — тот же смысл `delivered`, что у чата.
            delivered=True,
            said_at=str(row.get('date_post') or ''))

        if not made:
            return False

        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE mail_human SET history_at = NOW()'
                ' WHERE id = %s AND history_at IS NULL', (int(row['id']),))
            await db.connection.commit()

        return True
    except Exception as e:
        logger_info(f"[dating] письмо #{row.get('id')} не уехало "
                    f'в историю: {e}')

        return False


def _card(raw) -> dict:
    """Карточка анкеты словарём: в базе JSON-столбцом, в тестах — готовой."""
    if isinstance(raw, str):
        import json

        try:
            return json.loads(raw or '{}') or {}
        except ValueError:
            return {}

    return raw or {}


def _int(raw) -> int:
    """Число из строки сайта: сайт отдаёт номера и строкой, и числом."""
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _since_site(raw) -> str:
    """`startDate` для вопроса сайту: время письма минус запас, как в импорте."""
    since = (raw - timedelta(minutes=MAIL_HUMAN_LEAD_MIN)) if \
        isinstance(raw, datetime) else datetime_utc_now()

    return since.strftime('%Y-%m-%dT%H:%M:%S')


def _moment(raw) -> datetime | None:
    """Время письма из разбора страницы — `datetime` или `None` (не понял)."""
    text = str(raw or '').strip().replace('T', ' ')

    for mask in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(text, mask)
        except ValueError:
            continue

    return None


async def _pairs(site_id: int) -> list[tuple[int, str]]:
    """Пары, где стоит заглянуть в историю: отвечено на сайте, не нами.

    ⚠ **Отмеченных не берём** (`human_checked_at IS NULL`): страница истории
    пары не меняется от того, что мы на неё посмотрели дважды, а каждый
    просмотр — поход к панели. Новая его строка после отметки не бывает:
    «отвечено» без нашего ответа — состояние, из которого пара уже вышла
    (ответили мы — отпадает условие, ответили они — отпало раньше).
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.profile_id, m.uid'
            ' FROM mail_in m'
            ' JOIN profile p ON p.id = m.profile_id AND p.enabled = 1'
            ' WHERE m.site_id = %s AND m.is_replied = 1'
            '   AND m.human_checked_at IS NULL'
            '   AND NOT EXISTS ('
            '       SELECT 1 FROM mail_reply r WHERE r.mail_in_id = m.id)'
            # ⚠ Свежие вперёд: заход берёт десяток пар, и по номеру
            # собеседника сегодняшний ответ ждал бы очереди за сентябрьскими.
            ' GROUP BY m.profile_id, m.uid'
            ' ORDER BY MAX(m.date_post) DESC LIMIT %s',
            (int(site_id), MAIL_HUMAN_PAIRS))
        rows = await db.fetchall()

    return [(int(row['profile_id']), str(row['uid'])) for row in rows or []]
