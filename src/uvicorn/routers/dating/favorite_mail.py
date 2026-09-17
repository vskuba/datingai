"""Переписка с избранными: ручки пула на вкладке «Фавориты».

Повторяют пул рассылки (`mailing.py`) — чтение, правка, удаление, повод
набрать, отправка одной карточки, — и это намеренно: занятие устроено так же, а
второй набор соглашений на ту же работу человеку пришлось бы разгадывать.

Расходятся они в двух местах, и оба про природу занятия:

⚠ **Шаблона нет.** Текст пишет агент под конкретный разговор, глядя на его
историю, и `generate` не принимает списка шаблонов — принимать было бы нечего.

⚠ **Пара «анкета — адресат» не одноразовая.** Рассылка пишет человеку один раз
каждым шаблоном; здесь ему пишут снова и снова — в том и смысл возобновления.
Частоту держат два предела на человека (`favorite_mail_per_day`,
`favorite_mail_gap`), а не уникальный ключ в базе.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.send_round import send_why
from src.dating.favorite import (FAVORITE_STATES,
                                        FAVORITE_STATES_SQL)
from src.dating.favorite_mail import (FAVORITE_MAIL_BATCH_DEFAULT,
                                             FAVORITE_MAIL_BATCH_MAX,
                                             favorite_mail_make,
                                             favorite_mail_pool_left,
                                             favorite_mail_why)
from src.dating.favorite_mail_round import favorite_mail_round_next
from src.dating.json_column import json_column_load
from src.dating.mail import MAIL_BODY_MAX
from src.dating.mail_reply import mail_reply_thread
from src.dating.mail_send import (DatingMailSendError, mail_send_mark,
                                         mail_send_one)
from src.dating.online import online_show
from src.uvicorn.routers.dating.site_ import site_setting
from src.uvicorn.routers.setting import setting_timezone_hours

router = APIRouter(prefix='/api/dating/favorite-mail', tags=['dating'])

# Куда пишется исход отправки. ⚠ Имя таблицы уезжает в `mail_send_one`, а
# там оно сверяется с белым списком: в SQL оно не параметризуется.
FAVORITE_MAIL_TABLE = 'favorite_mail'

# Сколько карточек отдаём странице. Тот же предел, что у пула писем.
FAVORITE_MAIL_LIMIT = 300


class DatingFavoriteMailGenerate(BaseModel):
    """Повод набрать пул. ⚠ Шаблонов здесь нет — их у этого занятия не бывает."""

    site_id: int = Field(..., gt=0, description='Сайт-закладка')
    limit: int = Field(FAVORITE_MAIL_BATCH_DEFAULT, ge=1,
                       le=FAVORITE_MAIL_BATCH_MAX,
                       description='Сколько пар пробуем за это нажатие')
    profile_id: int = Field(0, ge=0, description='Писать только от этой анкеты. '
                                                 'Ноль — от всех включённых')


class DatingFavoriteMailEdit(BaseModel):
    """Правка текста письма перед отправкой."""

    body: str = Field(..., min_length=1, max_length=MAIL_BODY_MAX)


@router.get('', include_in_schema=False)
async def favorite_mail_list(site_id: int = 0, status_: str = 'draft',
                                    profile_id: int = 0,
                                    user: AuthUser = Depends(auth_required)):
    """Пул писем избранным — свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не письма всех сайтов.
        status_: `draft` (пул), `sent`, `failed`, `delivery` или `all`; можно
            несколько через запятую. `delivery` — «что происходило с отправкой»:
            статусы плюс черновики с причиной отказа, потому что неудача
            **оставляет письмо в пуле** (`mail_send.py`).
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.

            ⚠ **Это фильтр, а не условие показа.** Список отвечает на вопрос «что
            происходит на сайте», и без выбранной анкеты он полон; выбор анкеты
            меняет **сколько** строк в нём, а не показывать ли его вообще. Тот же
            порядок, что у диалогов (§3.8) и у пула рассылки.

    В ответе, помимо полей строки, — имя анкеты и признак «в сети»: карточка
    показывает «от кого кому», и без них страница делала бы запрос на письмо.
    """
    if not int(site_id or 0):
        return {'result': [], 'pool': 0, 'send_on': False, 'send_why': ''}

    # ⚠ **Имя и лицо собеседника подмешиваем из карточек**, а не берём только из
    # строки пула. В строку они попали при наборе, а у пары, приехавшей со списка
    # избранных сайта, их не было вовсе: в «Доставке» человек оставался голым
    # номером. Порядок источников тот же, что у диалогов: контакт анкеты (свежее)
    # → карточка сайта → то, что записали при наборе.
    sql = ('SELECT m.id, m.site_id, m.profile_id, m.uid,'
           '       COALESCE(NULLIF(c.name, %s), NULLIF(comp.name, %s),'
           '                NULLIF(m.name, %s), %s) AS name,'
           '       COALESCE(NULLIF(comp.photo, %s), NULLIF(c.photo, %s),'
           '                NULLIF(m.photo, %s), %s) AS photo,'
           ' m.body, m.status, m.error, m.answer, m.sent_at, m.created_at,'
           ' p.login AS profile_login, p.profile AS profile_card'
           ' FROM favorite_mail m'
           ' LEFT JOIN profile p ON p.id = m.profile_id'
           ' LEFT JOIN contact c'
           '   ON c.profile_id = m.profile_id AND c.uid = m.uid'
           ' LEFT JOIN companion comp'
           '   ON comp.site_id = m.site_id AND comp.uid = m.uid'
           ' WHERE m.site_id = %s')
    args = ['', '', '', '', '', '', '', '', int(site_id)]

    asked = [one.strip() for one in str(status_ or '').split(',')]
    wanted = [one for one in asked if one in ('draft', 'sent', 'failed')]

    if 'delivery' in asked:
        sql += (" AND (m.status IN ('sent', 'failed')"
                "      OR (m.status = 'draft' AND m.error <> ''))")
    elif wanted:
        sql += ' AND m.status IN (%s)' % ', '.join(['%s'] * len(wanted))
        args.extend(wanted)

    if int(profile_id or 0):
        sql += ' AND m.profile_id = %s'
        args.append(int(profile_id))

    sql += ' ORDER BY m.id DESC LIMIT %s'
    args.append(FAVORITE_MAIL_LIMIT)

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    # ⚠ Флаг отправки и счёт пула едут **вместе со списком**, как у инвайтов: по
    # первому страница решает, живая ли кнопка на карточке, по второму — показывать
    # ли «пул полон». Спрашивай она их порознь, полоса подвкладок успевала бы
    # показать кнопку живой там, где отправка снята.
    # ⚠ **Срок круга и повод пустоты — разные ответы, и нужны оба.** Срок говорит,
    # когда круг зайдёт; повод — почему он уйдёт ни с чем. Наблюдалось живьём:
    # анкета в сети, её избранный в сети, генерация включена, пул пуст — а причина
    # («писали 21 минуту назад, пауза 4 часа») была видна только в журнале воркера.
    # Один срок без повода читается как обещание набора к этому сроку.
    return {'result': [_answer(row) for row in rows or []],
            'pool': await favorite_mail_pool_left(int(site_id)),
            'next_sec': favorite_mail_round_next(int(site_id)),
            'why': await favorite_mail_why(int(site_id)),
            # ⚠ `why` — про **набор** («почему пул пуст»), `send_why` — про
            # **отправку** («почему полный пул не уходит»). Вопроса два, и ответ на
            # один ничего не говорит о другом: пул бывает пуст при живой отправке и
            # полон при выбранной норме.
            'send_why': await send_why(
                int(site_id), FAVORITE_MAIL_TABLE,
                await setting_timezone_hours()),
            'send_on': await _send_on(int(site_id))}


@router.get('/threads', include_in_schema=False)
async def favorite_mail_threads(site_id: int = 0, profile_id: int = 0,
                                       user: AuthUser = Depends(auth_required)):
    """Переписки с избранными: пары «анкета — человек», свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не переписки всех сайтов.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.

            ⚠ **Фильтр, а не условие показа**, как у диалогов чата (§3.8) и у
            журнала доставки: без выбора список полон и отвечает на «что вообще
            происходит на сайте».

    Returns:
        dict: `result` — по записи на пару: `profile_id`, `profile_name`, `uid`,
        `name`, `photo`, `letters` (сколько писем в переписке), `last_at` (когда
        последнее), `last_side` (`him` — ждёт нашего ответа, `her` — ждём его),
        `in_pool` (лежит ли ему черновик).

    ⚠ **Переписка — это письма, а не звёздочка.** В списке только те избранные, с
    кем письма были; добавленный в избранные и молчащий сюда не попадает —
    показывать пустой разговор незачем.

    ⚠ **Считаем по всем трём источникам**: входящие, наши ответы и оба пула
    ушедшего (рассылка и возобновление). Смотри мы только входящие, переписка, где
    последними писали мы, выглядела бы короче, чем есть, — а именно она здесь
    главная: это и есть затихший разговор.
    """
    if not int(site_id or 0):
        return {'result': []}

    sql = (
        'SELECT f.profile_id, f.uid,'
        '       COALESCE(NULLIF(c.name, %s), NULLIF(comp.name, %s), %s) AS name,'
        '       COALESCE(NULLIF(comp.photo, %s), NULLIF(c.photo, %s), %s) AS photo,'
        '       p.login AS profile_login, p.profile AS profile_card,'
        '       talk.letters, talk.last_at, talk.last_side,'
        '       EXISTS (SELECT 1 FROM favorite_mail d'
        '                WHERE d.profile_id = f.profile_id AND d.uid = f.uid'
        "                  AND d.status = 'draft') AS in_pool"
        '  FROM favorite f'
        '  JOIN profile p ON p.id = f.profile_id'
        '  JOIN ('
        '     SELECT profile_id, uid, COUNT(*) AS letters, MAX(at) AS last_at,'
        '            SUBSTRING_INDEX(MAX(CONCAT(at, %s, side)), %s, -1) AS last_side'
        '       FROM ('
        "          SELECT profile_id, uid, date_post AS at, 'him' AS side"
        '            FROM mail_in'
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM mail_reply WHERE status = 'sent'"
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM mailing WHERE status = 'sent'"
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM favorite_mail WHERE status = 'sent'"
        '       ) AS all_mail'
        '      WHERE at IS NOT NULL'
        '      GROUP BY profile_id, uid) AS talk'
        '    ON talk.profile_id = f.profile_id AND talk.uid = f.uid'
        '  LEFT JOIN contact c'
        '    ON c.profile_id = f.profile_id AND c.uid = f.uid'
        '  LEFT JOIN companion comp'
        '    ON comp.site_id = f.site_id AND comp.uid = f.uid'
        ' WHERE f.site_id = %s AND (%s = 0 OR f.profile_id = %s)'
        # ⚠ Тем же набором состояний, что отсекает их у «Входящих»: разойдись
        # перечисления — пара либо задвоилась бы в двух списках, либо исчезла из
        # обоих. `fail` не считается избранным: звёздочку нажать не вышло.
        f'   AND f.state IN ({FAVORITE_STATES_SQL})')

    args = ['', '', '', '', '', '', ' ', ' ',
            int(site_id), int(profile_id), int(profile_id),
            *FAVORITE_STATES]

    # Свежие сверху: список читают, чтобы найти работу, а работа — в том, что
    # шевелилось последним.
    sql += ' ORDER BY talk.last_at DESC LIMIT %s'
    args.append(FAVORITE_MAIL_LIMIT)

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    threads = []
    for row in rows or []:
        got = dict(row)
        card = json_column_load(got.pop('profile_card', None)) or {}

        got['profile_name'] = str(card.get('name') or got.pop('profile_login', '') or '')
        got.pop('profile_login', None)
        # ⚠ И лицо анкеты: карточка разговора показывает **обе** стороны, как в
        # диалогах чата. Берём из той же анкеты с сайта (`profile_.profile`),
        # что и список слева, — иначе одна и та же девушка выглядела бы в двух
        # местах по-разному.
        got['profile_photo'] = str(card.get('photo') or '')
        got['letters'] = int(got.get('letters') or 0)
        got['last_at'] = str(got['last_at']) if got.get('last_at') else ''
        got['in_pool'] = bool(got.get('in_pool'))
        threads.append(got)

    return {'result': threads}


@router.get('/threads/{profile_id}/{uid}', include_in_schema=False)
async def favorite_mail_thread(profile_id: int, uid: str,
                                      user: AuthUser = Depends(auth_required)):
    """Сама переписка с человеком: письма обеих сторон, старые сверху.

    ⚠ **Собирает её `mail_reply_thread`** — тот же код, что показывает
    разговор на «Входящих». Разговор один, и второй способ его собрать разошёлся
    бы с первым: там письмо рассылки уже входит в нить, а неотправленный черновик
    стоит последним, а не по времени сочинения.
    """
    return {'result': await mail_reply_thread(int(profile_id), str(uid))}


@router.post('/generate', include_in_schema=False)
async def favorite_mail_generate(payload: DatingFavoriteMailGenerate,
                                        user: AuthUser = Depends(auth_required)):
    """Набирает пул: письма тем избранным, чей разговор затих.

    Returns:
        dict: `made`, `skipped`, `reasons`, `left`, `stopped` и `pool` — сколько
        в пуле стало.

    ⚠ **Заход ничего не отправляет.** Он готовит письма; уносит их круг отправки,
    и только при поднятом `favorite_mail_send_on`.

    ⚠ **Повторное нажатие продолжает с того же места**: пары, которым письмо уже
    лежит в пуле или ушло недавно, отсеиваются самим отбором. Поэтому кнопку можно
    жать, пока пул не наберётся, и не бояться дублей.
    """
    site_id = int(payload.site_id)
    got = await favorite_mail_make(site_id, int(payload.limit),
                                          int(payload.profile_id))

    return {'result': {**got, 'pool': await favorite_mail_pool_left(site_id)}}


@router.put('/{mail_id}', include_in_schema=False)
async def favorite_mail_edit(mail_id: int, payload: DatingFavoriteMailEdit,
                                    user: AuthUser = Depends(auth_required)):
    """Правит текст письма в пуле.

    Raises:
        HTTPException: 404 — письма нет; 409 — оно уже ушло.

    ⚠ **Отправленное не правится.** Журнал отвечает на вопрос «что человек
    получил», и задним числом этот ответ меняться не должен.
    """
    row = await _row(mail_id)
    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='письмо уже ушло — его текст не меняется')

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE favorite_mail SET body = %s WHERE id = %s',
                         (payload.body, int(mail_id)))
        await db.connection.commit()

    return {'result': await _row(mail_id)}


@router.post('/{mail_id}/send', include_in_schema=False)
async def favorite_mail_send(mail_id: int,
                                    user: AuthUser = Depends(auth_required)) -> dict:
    """Отправляет одно письмо из пула — то, на кнопку которого нажали.

    Raises:
        HTTPException: 404 — письма нет; 409 — не ушло, с причиной словами.

    ⚠ **Кнопка живёт рядом с кругом отправки, и это не противоречие.** Круг шлёт
    сам, по часам сайта и с паузой; кнопка нужна тому, кто прочитал письмо и хочет
    отправить его сейчас, не дожидаясь очереди. Так же устроена «Рассылка».

    ⚠ **Не ушло — 409, а не 500.** Отказ сайта это не поломка панели: она
    отработала верно и принесла причину. Письмо при этом **остаётся в пуле** —
    почему именно так, сказано в `mail_send.py`.
    """
    letter = await _row(mail_id)

    if letter['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='письмо уже ушло — второй раз не отправляем')

    profile = await _sender(int(letter['profile_id']))
    site_id = int(letter['site_id'])

    base = str(await site_setting(site_id, 'api_base')).strip()
    password = str(await site_setting(site_id, 'api_password')).strip()

    try:
        await mail_send_one(letter, profile, base, password,
                                   FAVORITE_MAIL_TABLE)
    except DatingMailSendError as e:
        # ⚠ Причину храним **до** ответа: страница покажет её на карточке, а человек
        # закроет вкладку и вернётся завтра — в ответе HTTP её к тому времени нет.
        await mail_send_mark(letter, sent=False, error=str(e),
                                    table=FAVORITE_MAIL_TABLE)
        logger_info(f'[dating] письмо избранному #{mail_id} не ушло: {e}')

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {'result': await _row(mail_id)}


@router.delete('', include_in_schema=False)
async def favorite_mail_clear(site_id: int = 0,
                                     user: AuthUser = Depends(auth_required)):
    """Очищает пул сайта — разом всё, что ещё не ушло.

    ⚠ **Ушедшее остаётся.** Оно уже случилось, и стереть запись значило бы соврать
    журналу — а по этим же строкам считается, сколько человеку уже написали.

    ⚠ **Путь без номера объявлен до `/{mail_id}`**: иначе пустой хвост однажды
    прочитался бы как номер письма.
    """
    if not int(site_id or 0):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не назван сайт — очищать нечего')

    async with mysql_get_db_async() as db:
        await db.execute("DELETE FROM favorite_mail"
                         " WHERE site_id = %s AND status = 'draft'", (int(site_id),))
        dropped = int(db.rowcount or 0)
        await db.connection.commit()

    if dropped:
        logger_info(f'[dating] пул писем избранным сайта #{site_id} очищен: '
                    f'убрано {dropped}')

    return {'result': {'dropped': dropped}}


@router.delete('/{mail_id}', include_in_schema=False)
async def favorite_mail_drop(mail_id: int,
                                    user: AuthUser = Depends(auth_required)):
    """Убирает письмо из пула.

    Raises:
        HTTPException: 404 — письма нет; 409 — оно уже ушло.
    """
    row = await _row(mail_id)
    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='письмо уже ушло — из журнала оно не убирается')

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM favorite_mail WHERE id = %s',
                         (int(mail_id),))
        await db.connection.commit()

    return {'result': {'dropped': 1}}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _send_on(site_id: int) -> bool:
    """Разрешена ли отправка писем избранным.

    ⚠ Не прочиталось — считаем снятым. Верная сторона ошибки: молчание дешевле
    письма, ушедшего живому человеку вопреки решению.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT favorite_mail_send_on FROM site'
                             ' WHERE site_id = %s', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] выключатель писем избранным сайта #{site_id} '
                    f'не прочитался: {e}')

        return False

    return bool((row or {}).get('favorite_mail_send_on'))


async def _sender(profile_id: int) -> dict:
    """Анкета, от которой уходит письмо, — со всеми полями входа.

    ⚠ Нужны `login`, `password` и `password_source`: под этой анкетой заходят в
    API сайта. Тот же набор, что у отправки из пула рассылки.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, password, password_source, enabled'
            ' FROM profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='анкета письма удалена — отправлять не от кого')

    if not int(row.get('enabled') or 0):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='анкета выключена — от неё не отправляем')

    return dict(row)


async def _row(mail_id: int) -> dict:
    """Письмо по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.id, m.site_id, m.profile_id, m.uid, m.name, m.photo,'
            ' m.body, m.status, m.error, m.sent_at, m.created_at,'
            ' p.login AS profile_login, p.profile AS profile_card'
            ' FROM favorite_mail m'
            ' LEFT JOIN profile p ON p.id = m.profile_id'
            ' WHERE m.id = %s', (int(mail_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'письма #{mail_id} нет')

    return _answer(row)


def _answer(row) -> dict:
    """Строка пула в вид для страницы."""
    got = dict(row)
    card = json_column_load(got.pop('profile_card', None)) or {}

    # В сети ли адресат — из снимка ленты, тем же `online_show`, что у
    # карточек рассылки: правило одно, и вторая копия разошлась бы.
    online = online_show(int(got['site_id']), str(got['uid']))

    got['online'] = bool(online.get('online'))
    got['online_known'] = bool(online.get('known'))
    # Имя анкеты, а не логин: логин это почта, и в карточке «от кого» она
    # читается как подпись робота. Логин остаётся запасным.
    got['girl'] = str(card.get('name') or got.get('profile_login') or '')
    got['profile_name'] = got['girl']
    # ⚠ Лицо и номер анкеты — тоже: «Доставка» рисует обе стороны тем же видом,
    # что «Диалоги» (`talkSide`), а тому нужны три поля на сторону. Без них девушка
    # оставалась буквой в кружке, хотя карточка уже была в руках запроса.
    got['profile_photo'] = str(card.get('photo') or '')
    got['profile_uid'] = str(card.get('site_uid') or '')
    got['sent_at'] = str(got['sent_at']) if got.get('sent_at') else ''
    got['created_at'] = str(got.get('created_at') or '')

    return got
