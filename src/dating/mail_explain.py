"""Почему этой паре не пишут письма: что мешает сейчас и когда снимется.

На «Чат → Диалоги» такое окно уже есть — про инициативу
(`initiative.initiative_explain`). Здесь то же самое и по той же причине, но про
почту: занятий два — **ответ на его письмо** и **приставание**, — правил у них
шестнадцать, и почти все невидимы.

## ⚠⚠ Почему это нужно именно окном

Снято с прода 11.10.2026. Собеседник написал анкете #44 в 08:32:30, ответ ушёл в
08:38:36. Шесть минут, из которых на модель ушло шестнадцать секунд: остальное —
два срока опроса (`mail_check_every`, `reply_every`), и **ни один из них на
странице не был виден**. Молчащая переписка неотличима от сломанной, и человек у
экрана идёт искать поломку там, где всё работает ровно так, как настроено.

## ⚠ Считаем теми же выражениями, что и круги

Готовое берём как есть: `favorite_mail_girl_soon`, `favorite_mail_person_soon`,
`favorite_mail_shift_site`, `mail_wait_left`, `online_show`, сроки кругов.

Цена, которую платим осознанно: отбор кругов (`favorite_mail._quiet`,
`mail_reply._to_answer`) идёт по всем парам сайта одним запросом и выражен иначе
— для одной пары условия переписаны здесь. Ровно та же цена, что уже заплачена в
`initiative_explain`, и платится она тем же способом: сторожем согласия в
`tests/test_mail_explain.py`, а не внимательностью.

## ⚠ Чего здесь нет намеренно

**Минуты отправки.** Письма уносит общий круг отправки со своей паузой на весь
сайт, и очередь делится со всеми анкетами: назвать минуту значило бы соврать
точностью. Говорим «когда пара станет годной» — это считается честно.

**Возрастного окна.** `mail_age` спрашивается только у рассылки, у холодного
обращения. Избранному анкета писала первой, он отвечал, и критерий к этой паре не
применяется вовсе.

**`send_why` целиком.** Она отвечает про **сайт** («пул пуст», «все письма с
ошибкой»), а окно спрашивает про пару: подмешай мы её строку, человек получил бы
ответ про чужое письмо — ровно то, ради чего окно и заводится.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_send import mail_sent_union
from src.dating.mail_wait import mail_wait_left


async def mail_explain(profile_id: int, uid: str,
                       offset_hours: int = 0) -> dict:
    """Всё о почте одной пары: что уйдёт следующим, что мешает и когда снимется.

    Args:
        profile_id: наша анкета.
        uid: собеседник на сайте.
        offset_hours: часовое смещение панели — смены заданы местным временем.

    Returns:
        dict: пустой, если пары не существует вовсе; иначе `known: True` и
        разбор по занятиям. Поля — в спеке
        `docs/superpowers/specs/2026-10-11-mail-explain-design.md`.

    ⚠ Пустой словарь, а не исключение: человек открывает окно у разговора,
    которого в наших таблицах может не быть. Ручка превращает это в
    `known: false` со словами, а не в 404 — 404 страница нарисовала бы красным.
    """
    profile_id, uid = int(profile_id or 0), str(uid or '')
    if not profile_id or not uid:
        return {}

    pair = await _pair(profile_id, uid)
    if not pair:
        return {}

    site_id = int(pair['site_id'])
    last = await _last(profile_id, uid)
    # ⚠ Остуда спрашивается **один раз**: её читают обе части, и два чтения ради
    # одного числа дали бы два разных мгновения.
    cooling = mail_wait_left(site_id, uid)

    return {
        'site_id': site_id,
        'known': True,
        'last': last,
        'block': {'is': bool(pair.get('block_mark') is not None),
                  'mark': str(pair.get('block_mark') or ''),
                  'channel': str(pair.get('block_channel') or '')},
        'cooling_sec': cooling,
    }


# ── Приватное ────────────────────────────────────────────────────────────────

async def _pair(profile_id: int, uid: str) -> dict:
    """Анкета, звёздочка и запрет — одним запросом.

    ⚠ **Один запрос, а не три.** Окно открывают щелчком, и три похода в базу
    ради трёх полей — это три разных мгновения: запрет мог появиться между
    первым и третьим.

    ⚠ `block` приклеивается `LEFT JOIN`-ом **без отбора по каналу**: охват
    решает вызывающий, глядя на `channel`. Отсеки мы здесь — окно ответа
    молчало бы про запрет, который к ответам и правда не относится, но знать о
    котором человеку надо.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT p.id AS profile_id, p.site_id, p.enabled, p.status,'
                '       f.state AS star_state,'
                '       b.reason AS block_mark, b.channel AS block_channel'
                '  FROM profile p'
                '  LEFT JOIN favorite f'
                '    ON f.profile_id = p.id AND f.uid = %s'
                '  LEFT JOIN block b'
                '    ON b.profile_id = p.id AND b.uid = %s'
                ' WHERE p.id = %s LIMIT 1',
                (str(uid), str(uid), int(profile_id)))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] пара #{profile_id} → {uid} не спросилась: {e}')

        return {}

    return dict(row or {})


async def _last(profile_id: int, uid: str) -> dict:
    """Последняя реплика **по всей почте** пары: когда и чья.

    ⚠ **По всей почте, а не по одному источнику** — тем же перечислением, каким
    считает отбор круга (`favorite_mail._quiet`): его входящие плюс три наших
    пула ушедшего. Смотри мы только входящие, затихшим выглядел бы всякий, кому
    мы писали последними, включая того, кому письмо ушло пять минут назад.

    ⚠ `side` пустой — «реплик нет вовсе». Это законный случай: звёздочку ставят
    и тому, с кем разговор шёл на сайте задолго до панели.
    """
    sql = ('SELECT MAX(at) AS last_at,'
           '       SUBSTRING_INDEX(MAX(CONCAT(at, %s, side)), %s, -1) AS last_side'
           '  FROM ('
           "    SELECT date_post AS at, 'him' AS side FROM mail_in"
           '     WHERE profile_id = %s AND uid = %s'
           '    UNION ALL '
           + mail_sent_union("profile_id = %s AND uid = %s AND status = 'sent'",
                             select="sent_at AS at, 'her' AS side") +
           '  ) AS all_mail WHERE at IS NOT NULL')

    # ⚠ Условие подставляется **в каждую** ветку союза, поэтому доводов втрое
    # больше: так же считают их дневной предел и пауза отправительницы.
    args = [' ', ' ', int(profile_id), str(uid)]
    args += [int(profile_id), str(uid)] * 3

    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, tuple(args))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] последняя реплика пары #{profile_id} '
                    f'не спросилась: {e}')

        return {'at': None, 'side': ''}

    return {'at': (row or {}).get('last_at'),
            'side': str((row or {}).get('last_side') or '')}
