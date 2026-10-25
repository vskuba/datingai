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

from datetime_.datetime_ import datetime_day_minutes
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.favorite import FAVORITE_STATES
from src.dating.favorite_mail import (FAVORITE_MAIL_PER_DAY_DEFAULT,
                                      FAVORITE_MAIL_PER_DAY_SETTING,
                                      favorite_mail_gap_range,
                                      favorite_mail_girl_wait,
                                      favorite_mail_person_sent,
                                      favorite_mail_person_soon,
                                      favorite_mail_shift_site)
from src.dating.favorite_mail_round import (FAVORITE_MAIL_ROUND_EVERY_DEFAULT,
                                            FAVORITE_MAIL_ROUND_EVERY_SETTING,
                                            favorite_mail_round_next)
from src.dating.json_column import json_column_list
from src.dating.mail_send import mail_sent_union
from src.dating.mail_wait import mail_wait_left, mail_wait_skip
from src.dating.online import online_show
from src.dating.schedule import schedule_now
from src.dating.reply_round import (REPLY_EVERY_DEFAULT, REPLY_EVERY_SETTING,
                                    reply_round_next)
from src.dating.site_setting import site_seconds

# Таблицы пулов, из которых окно читает черновик пары. ⚠ Имя таблицы уезжает в
# SQL текстом — параметром его не передать, — поэтому только отсюда.
MAIL_EXPLAIN_POOLS = ('favorite_mail', 'mail_reply')


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
    flags = await _flags(site_id)
    # ⚠ Остуда спрашивается **один раз**: её читают обе части, и два чтения ради
    # одного числа дали бы два разных мгновения.
    cooling = mail_wait_left(site_id, uid)

    # ⚠ Обе части — **локальными переменными до словаря**: `_next` сравнивает их
    # между собой, и ему нужны обе готовыми.
    reply = await _reply(site_id, profile_id, uid, pair, flags, cooling,
                         offset_hours)
    favorite = await _favorite(site_id, profile_id, uid, pair, last, flags,
                               cooling, offset_hours)

    return {
        'site_id': site_id,
        'known': True,
        'last': last,
        'block': {'is': bool(pair.get('block_mark') is not None),
                  'mark': str(pair.get('block_mark') or ''),
                  'channel': str(pair.get('block_channel') or '')},
        'cooling_sec': cooling,
        'reply': reply,
        'favorite': favorite,
        'next': _next(reply, favorite),
        # ⚠ Меньший из двух ненулевых: ждать паре до **ближайшего** из занятий.
        # Ноль — ждать нечего, и что именно мешает, говорят `blockers`.
        'wait_sec': min([one for one in (reply['wait_sec'],
                                         favorite['wait_sec']) if one] or [0]),
    }


# ── Приватное ────────────────────────────────────────────────────────────────
#
# ⚠ Публичная функция в файле одна (`mail_explain`), всё дальше — её части.
# Подразделы ниже — для глаза, по занятиям: порядок чтения тот же, в каком
# окно и собирается.

# ── Отправка: общая часть обоих занятий ──────────────────────────────────────

def _send_blockers(*, flag: bool, said: str, shift: bool, draft,
                   cooling: int) -> list:
    """Почему готовое письмо сейчас не уходит. Четыре вопроса, порядок круга.

    Args:
        flag: поднят ли выключатель отправки этого занятия.
        said: как занятие зовётся в строке («ответов», «писем избранным»).
        shift: идёт ли смена отправки (`mailing_schedule` — одна на всю почту).
        draft: строка пула этой пары либо `None`.
        cooling: сколько секунд получателю ещё не пишем.

    ⚠ **`send_why` целиком не зовём**: она отвечает про сайт («пул пуст», «все
    письма с ошибкой»), а окно — про пару. Подмешай мы её строку, человек
    получил бы ответ про чужое письмо. Берём те же два предиката, из которых
    она складывает вопрос о смене.

    ⚠ Минуту отправки не называем ни при каких условиях: уносит письма общий
    круг со своей паузой на весь сайт, и очередь делится со всеми анкетами.
    """
    blockers = []
    if not flag:
        blockers.append(f'отправка {said} выключена')
    if not shift:
        blockers.append('сейчас не смена: письма уходят по часам, '
                        'заданным у сайта')
    if draft and str(draft.get('status')) == 'draft' and draft.get('error'):
        blockers.append('у письма прежняя ошибка — круг такие не берёт, '
                        'досылают кнопкой')
    if cooling:
        blockers.append(mail_wait_skip(cooling))

    return blockers


def _send_shift(flags: dict, offset_hours: int) -> bool:
    """Идёт ли смена отправки. ⚠ Она **одна на всю почту сайта**.

    У занятий свои часы сочинения (`favorite_mail_schedule`), а уносит письма
    общий круг — по `mailing_schedule`. Второе расписание отвечало бы на тот же
    вопрос вторым голосом.
    """
    return schedule_now(json_column_list(flags.get('mailing_schedule')),
                        datetime_day_minutes(int(offset_hours)))


def _wait(blockers: list, next_sec: int) -> int:
    """Сколько ждать годности. Ноль — ждать нечего.

    ⚠ Ждать показываем, только когда ждут **времени**: у пары, упёршейся в
    предел, ожидание бессмысленно — предел снимают настройкой или его ответом,
    а не терпением. Ноль здесь значит «ждать нечего», а не «уйдёт сейчас».
    """
    return int(next_sec) if not blockers and int(next_sec) > 0 else 0


def _next(reply: dict, favorite: dict) -> str:
    """Что уйдёт этой паре следующим письмом. Пусто — нечего.

    ⚠⚠ **Считает сервер, а не страница.** Это сравнение двух занятий по их
    помехам, и своя арифметика в браузере разошлась бы с кругами на первой
    правке — ровно та грабля, что уже была дважды (`initiative_why`, значок
    потолка).

    ⚠ **Лежащий черновик старше годной пары.** Он уже сочинён, за него заплачено
    вызовом модели, и уносит его ближайший круг отправки; годная пара ещё только
    дождётся захода набора.

    ⚠ Ответ старше приставания при прочих равных: он отвечает на заданный
    вопрос, а приставание напоминает о себе молчащему. Круги и так не
    пересекутся — «последним говорил он» отнимает приставание, — но при
    рассинхроне данных правило должно быть названо, а не выведено из случая.
    """
    for name, part in (('reply', reply), ('favorite', favorite)):
        draft = part.get('draft') or {}
        if str(draft.get('status')) == 'draft' and not part['send_blockers']:
            return name

    for name, part in (('reply', reply), ('favorite', favorite)):
        if not part['blockers']:
            return name

    return ''


# ── Ответ на его письмо ──────────────────────────────────────────────────────

async def _reply(site_id: int, profile_id: int, uid: str, pair: dict,
                 flags: dict, cooling: int, offset_hours: int) -> dict:
    """Что с ответом на его письмо: помехи в порядке `mail_reply._to_answer`.

    ⚠ **Срок круга помехой не считается.** Он мешает не паре, а всем сразу, и
    назови мы его помехой — человек стал бы искать, что эта пара нарушила. Срок
    идёт своим полем `next_sec`.

    ⚠ Доводы `cooling` и `offset_hours` этой части пока не нужны — их берёт
    отправка, следующей задачей. Сигнатура сразу конечная: менять её задним
    числом значило бы править уже проверенный вызов.
    """
    his = await _his_last(profile_id, uid)
    draft = await _draft(site_id, 'mail_reply', profile_id, uid)
    answered = await _answered(his)

    blockers = []
    if not flags.get('reply_make_on'):
        blockers.append('генерация ответов выключена у сайта')
    if not int(pair.get('enabled') or 0):
        blockers.append('анкета выключена')
    if not his:
        blockers.append('он не писал ни разу — отвечать не на что')
    elif answered:
        blockers.append('на его последнее письмо ответ уже написан')
    elif his.get('empty'):
        blockers.append('у письма нет текста — тело ещё не забрали с сайта')

    next_sec = reply_round_next(site_id)

    return {
        'make_on': bool(flags.get('reply_make_on')),
        'send_on': bool(flags.get('reply_send_on')),
        'every_sec': int(await site_seconds(site_id, REPLY_EVERY_SETTING)
                         or REPLY_EVERY_DEFAULT),
        'next_sec': next_sec,
        'his_last': his, 'draft': draft,
        'blockers': blockers,
        'send_blockers': _send_blockers(
            flag=bool(flags.get('reply_send_on')), said='ответов',
            shift=_send_shift(flags, offset_hours), draft=draft,
            cooling=cooling),
        'wait_sec': _wait(blockers, next_sec),
    }


async def _his_last(profile_id: int, uid: str):
    """Его последнее письмо: номер, время и есть ли у него текст.

    ⚠ **Последнее, а не каждое.** Он мог написать три подряд, не дождавшись
    ответа; отвечает круг на последнее, прежние идут в историю. Тот же порядок,
    что у `_to_answer`: по `date_post`, при равенстве — по номеру.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, body, date_post FROM mail_in'
                ' WHERE profile_id = %s AND uid = %s'
                ' ORDER BY date_post DESC, id DESC LIMIT 1',
                (int(profile_id), str(uid)))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] письма пары #{profile_id} не спросились: {e}')

        return None

    if not row:
        return None

    return {'id': int(row['id']), 'at': row['date_post'],
            'empty': not str(row['body'] or '').strip()}


async def _answered(his) -> bool:
    """Есть ли уже ответ на **это** письмо.

    ⚠ Ключ — `mail_in_id`, а не пара: ответ один на письмо, и уникальный ключ
    `mail_reply` стоит именно на нём. Спроси мы «есть ли ответ этой паре»,
    старый ответ на позапрошлое письмо запретил бы отвечать на новое.
    """
    if not his:
        return False

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT 1 FROM mail_reply WHERE mail_in_id = %s'
                             ' LIMIT 1', (int(his['id']),))

            return bool(await db.fetchone())
    except Exception as e:
        logger_info(f'[dating] ответ на письмо #{his["id"]} '
                    f'не спросился: {e}')

        return False


# ── Приставание ──────────────────────────────────────────────────────────────

async def _favorite(site_id: int, profile_id: int, uid: str, pair: dict,
                    last: dict, flags: dict, cooling: int,
                    offset_hours: int) -> dict:
    """Что с приставанием у этой пары: помехи в порядке проверки круга.

    ⚠⚠ **Порядок — тот же, что у круга**, от решённого человеком к преходящему:
    выключатель → смена → звёздочка → анкета в сети → чьё последнее слово →
    черновик → запрет → он в сети → остуда → дневной предел получателя → пауза
    отправительницы. Переставь их — и окно назовёт главной ту помеху, что пройдёт
    сама через минуту, а человек уйдёт ждать вместо того, чтобы поправить
    настройку.

    ⚠ **Двух внутрисходовых правил здесь нет намеренно**: «ему уже пишет другая
    анкета этим заходом» и «эта анкета уже написала этим заходом» — про заход, а
    не про пару. К следующему заходу их нет, и назови их окно помехой, человек
    ждал бы снятия того, что уже снято.

    ⚠ Срок круга помехой тоже не считается: он мешает не паре, а всем сразу.
    Идёт своим полем `next_sec`.
    """
    gap_low, gap_high = await favorite_mail_gap_range(site_id)
    per_day = int(await site_seconds(site_id, FAVORITE_MAIL_PER_DAY_SETTING)
                  or FAVORITE_MAIL_PER_DAY_DEFAULT)
    online = online_show(site_id, uid)
    star_state = str(pair.get('star_state') or '')
    star = star_state in FAVORITE_STATES
    shift = await favorite_mail_shift_site(site_id, offset_hours)
    draft = await _draft(site_id, 'favorite_mail', profile_id, uid)
    # ⚠ Черновик пары и черновик **анкеты** — разные вопросы: ближайшую очередь
    # девушки занимает любой её черновик, кому бы он ни был написан.
    girl_draft = await _girl_draft(profile_id)
    # ⚠ Число и вердикт по нему — из одного места (`favorite_mail`): свой
    # запрос здесь показывал бы человеку одно число, а предел считал по
    # другому, и разошлись бы они молча.
    sent = await favorite_mail_person_sent(site_id, uid)
    person = await favorite_mail_person_soon(site_id, uid, per_day)
    # ⚠ Срок и помеха — **одним чтением**: окно показывает оба, и спроси оно их
    # порознь, второй вопрос бросил бы жребий заново по новому письму.
    gap, girl_soon = await favorite_mail_girl_wait(profile_id, gap_low, gap_high)

    blockers = []
    if not flags.get('favorite_mail_make_on'):
        blockers.append('набор писем избранным выключен у сайта')
    if not shift:
        blockers.append('сейчас не смена: письма сочиняются по часам, '
                        'заданным у сайта')
    if not star:
        blockers.append('его нет в избранных этой анкеты')
    if str(pair.get('status') or '') != 'online':
        blockers.append('анкета не в сети — письмо уходит от её имени')
    if str(last.get('side') or '') == 'him':
        blockers.append('последним говорил он — ответить должен круг ответов')
    if girl_draft:
        blockers.append('у этой анкеты уже лежит черновик приставания')
    if pair.get('block_mark') is not None:
        blockers.append(f'паре запрещено писать: {pair["block_mark"]}')
    if not online.get('online'):
        blockers.append(
            'его нет в сети'
            + ('' if online.get('known') else ' (ленту ещё не снимали)'))
    if cooling:
        blockers.append(mail_wait_skip(cooling))
    if person:
        blockers.append(person)
    if girl_soon:
        blockers.append(girl_soon)

    next_sec = favorite_mail_round_next(site_id)

    return {
        'make_on': bool(flags.get('favorite_mail_make_on')),
        'send_on': bool(flags.get('favorite_mail_send_on')),
        'star': star, 'star_state': star_state,
        'shift_now': bool(shift),
        'every_sec': int(await site_seconds(site_id,
                                            FAVORITE_MAIL_ROUND_EVERY_SETTING)
                         or FAVORITE_MAIL_ROUND_EVERY_DEFAULT),
        'next_sec': next_sec,
        # ⚠ Пауза отдаётся **выпавшей этой анкете**, а границы — рядом: окно
        # называет и то, сколько ждать ей, и то, из чего это число взялось.
        # Одно без другого читается как поломка — «пауза 5 часов» при
        # выставленных четырёх выглядит ошибкой, пока не видно «до 6».
        'gap_sec': gap, 'gap_low_sec': gap_low, 'gap_high_sec': gap_high,
        'per_day': per_day,
        'day_sent': int((sent or {}).get('count') or 0),
        # ⚠ Граница счёта — его письмо, если оно позже суточного края. Окно
        # обязано её называть: «1 из 1» без неё читается как «за сутки», а
        # считалось «с его ответа», и человек пошёл бы искать пропажу в журнале
        # доставки.
        'day_since_his': (sent or {}).get('his_last'),
        'him_online': bool(online.get('online')),
        'him_known': bool(online.get('known')),
        'draft': draft,
        'blockers': blockers,
        'send_blockers': _send_blockers(
            flag=bool(flags.get('favorite_mail_send_on')),
            said='писем избранным',
            shift=_send_shift(flags, offset_hours), draft=draft,
            cooling=cooling),
        'wait_sec': _wait(blockers, next_sec),
    }


# ── Общее: база и настройки ──────────────────────────────────────────────────

async def _flags(site_id: int) -> dict:
    """Выключатели занятий и расписание отправки — одним запросом.

    ⚠ Расписание отправки **одно на всю почту сайта** (`mailing_schedule`): у
    занятий свои часы сочинения, а уносит письма общий круг.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT favorite_mail_make_on, favorite_mail_send_on,'
                ' reply_make_on, reply_send_on, mailing_schedule'
                ' FROM site WHERE site_id = %s', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] выключатели сайта #{site_id} не спросились: {e}')

        return {}

    return dict(row or {})


async def _draft(site_id: int, table: str, profile_id: int, uid: str):
    """Последняя строка пары в этом пуле. `None` — строки нет вовсе.

    ⚠ Имя таблицы уезжает в SQL текстом — параметром его не передать. Приходит
    оно из `MAIL_EXPLAIN_POOLS`, снаружи взяться неоткуда, и свериться со
    списком всё равно дешевле, чем однажды об этом пожалеть.
    """
    if str(table) not in MAIL_EXPLAIN_POOLS:
        return None

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'SELECT id, status, error, created_at, sent_at FROM {table}'
                ' WHERE site_id = %s AND profile_id = %s AND uid = %s'
                ' ORDER BY id DESC LIMIT 1',
                (int(site_id), int(profile_id), str(uid)))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] черновик пары #{profile_id} не спросился: {e}')

        return None

    if not row:
        return None

    return {'id': int(row['id']), 'status': str(row['status'] or ''),
            'error': str(row['error'] or ''),
            'at': row['sent_at'] or row['created_at']}


async def _girl_draft(profile_id: int) -> bool:
    """Лежит ли у анкеты **любой** черновик приставания.

    ⚠ По анкете, а не по паре: пауза считает отправительницу, и лежащий черновик
    занимает её ближайшую очередь целиком — всё набранное сверх него пролежит до
    вечера и протухнет. То же условие, что в `favorite_mail._quiet`.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT 1 FROM favorite_mail'
                " WHERE profile_id = %s AND status = 'draft' LIMIT 1",
                (int(profile_id),))

            return bool(await db.fetchone())
    except Exception as e:
        logger_info(f'[dating] черновики анкеты #{profile_id} не спросились: {e}')

        return False


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
