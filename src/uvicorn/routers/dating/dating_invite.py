"""Инвайты: пул первых реплик в чат и отправка одной из них.

Ручки повторяют пул писем (`dating_mailing.py`) — чтение, правка, удаление, повод
набрать, отправка одной карточки, — и это намеренно: занятие устроено так же, а
второй набор соглашений на ту же работу человеку пришлось бы разгадывать.

Расходятся они в двух местах, и оба про природу инвайта:

⚠ **Шаблона у инвайта нет.** Текст сочиняет агент под конкретного человека, и
`generate` не принимает списка шаблонов — принимать было бы нечего.

⚠ **Отправка идёт в чат, а не в почту сайта.** Дорога у неё своя —
`dating_invite_send.py` поверх `dating_send_message` (тот же путь, что у кнопки в
окне переписки и у автоответа), а не `dating_mail_send_one`. Разница видна и в
ответе: «принял ли сайт кадр» — это не «дошло до адресата», и путать их дорого
(`dating_send.py`).

⚠ **Выключатель сайта** `invite_send_on` (снят по умолчанию) включает **круг**
отправки (`dating_invite_send_round.py`), а не кнопку. Кнопка на карточке живая
всегда: ею проверяют текст руками и досылают то, что круг пропустил с ошибкой.

Запертый круг честнее круга, который пишет живым людям без спроса и молча. А вот
запирать кнопку тем же флагом оказалось вредно: «остановить автоматику» стало
неотличимо от «запретить себе проверить руками», и человек, сняв флаг, терял
способ дослать зависшую реплику. Защищаемся от автоматики, не от человека.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.dating_invite import (DATING_INVITE_BATCH_DEFAULT,
                                      DATING_INVITE_BATCH_MAX,
                                      DatingInviteError, dating_invite_make,
                                      dating_invite_pool_left)
from src.dating.dating_invite_round import dating_invite_round_next
from src.dating.dating_invite_send import dating_invite_send_one
from src.dating.dating_json import dating_json_load
from src.dating.dating_mail_agent import DATING_INVITE_MAX

router = APIRouter(prefix='/api/dating/invite', tags=['dating'])

# Сколько карточек отдаём странице. Тот же предел, что у пула писем: список читают
# глазами, и тысяча карточек в нём не нужна никому.
DATING_INVITE_LIMIT = 300


class DatingInviteGenerate(BaseModel):
    """Повод набрать пул. ⚠ Шаблонов здесь нет — их у инвайта не бывает."""

    site_id: int = Field(..., description='Сайт-закладка')
    limit: int = Field(DATING_INVITE_BATCH_DEFAULT, ge=1,
                       le=DATING_INVITE_BATCH_MAX,
                       description='Сколько пар пробуем за это нажатие')
    profile_id: int = Field(0, description='Звать только от этой анкеты. '
                                          'Ноль — от всех включённых')
    first_uid: str = Field('', max_length=32,
                           description='Кого поставить первым — карточка, '
                                       'выбранная в ленте «Все онлайн». '
                                       '⚠ Перестановка, а не отбор')


class DatingInviteEdit(BaseModel):
    """Правка текста реплики перед отправкой."""

    body: str = Field(..., min_length=1, max_length=DATING_INVITE_MAX)


@router.get('', include_in_schema=False)
async def dating_invite_list(site_id: int = 0, status_: str = 'draft',
                             user: AuthUser = Depends(auth_required)):
    """Пул инвайтов сайта — свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не инвайты всех сайтов.
        status_: `draft` (пул), `sent`, `failed`, `delivery` или `all`; можно
            несколько через запятую.

            `delivery` — не статус, а вопрос: «что происходило с отправкой».
            Он шире перечисления `sent,failed`, и по той же причине, что у писем:
            неудачная отправка **оставляет инвайт в пуле** (`draft` с непустой
            причиной, см. `dating_invite_send_mark`), чтобы её можно было
            повторить — рукой, потому что круг такую строку не берёт. Спрашивай
            подвкладка `sent,failed`, она молчала бы ровно о тех репликах, ради
            которых на неё и заходят, — о не дошедших.

    В ответе, помимо полей строки, — имя анкеты: карточка показывает «от кого
    кому», и без него страница делала бы запрос на каждую реплику.
    """
    if not int(site_id or 0):
        return {'result': [], 'pool': 0, 'send_on': False}

    sql = ('SELECT i.id, i.site_id, i.profile_id, i.uid, i.name, i.photo,'
           ' i.body, i.status, i.error, i.sent_at, i.created_at,'
           ' p.login AS profile_login, p.profile AS profile_card'
           ' FROM dating_invite i'
           ' LEFT JOIN dating_profile p ON p.id = i.profile_id'
           ' WHERE i.site_id = %s')
    args = [int(site_id)]

    # ⚠ Незнакомое имя молча отбрасываем, а не отвечаем отказом: `status_` приходит
    # из адреса, и опечатка в нём не повод показывать человеку ошибку вместо пула.
    asked = [one.strip() for one in str(status_ or '').split(',')]
    wanted = [one for one in asked if one in ('draft', 'sent', 'failed')]

    # ⚠ «Всё про доставку» — статусы **плюс черновики с причиной отказа**:
    # неудавшаяся отправка оставляет реплику в пуле, и по статусу её от нетронутой
    # не отличить.
    if 'delivery' in asked:
        sql += (" AND (i.status IN ('sent', 'failed')"
                "      OR (i.status = 'draft' AND i.error <> ''))")
    elif wanted:
        sql += ' AND i.status IN (%s)' % ', '.join(['%s'] * len(wanted))
        args.extend(wanted)

    sql += ' ORDER BY i.id DESC LIMIT %s'
    args.append(DATING_INVITE_LIMIT)

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    # ⚠ Флаг отправки и счёт пула едут **вместе со списком**, а не отдельной
    # ручкой. По первому страница решает, живая ли кнопка на карточке, по второму —
    # показывать ли «пул полон»; спрашивай она их порознь, полоса подвкладок
    # успевала бы показать кнопку живой там, где отправка снята.
    # ⚠ **Срок следующего захода едет вместе со списком.** В шапке пула висело
    # «по расписанию» — обещание без срока: человек, включивший генерацию, не знал,
    # ждать минуту или полчаса, и читал это как «включил, а оно не работает».
    #
    # Отдельной ручкой было бы хуже: два запроса на один взгляд, и ответы из
    # разных моментов времени — «пул пуст» из одного, «уже набирали» из другого.
    return {'result': [_answer(row) for row in rows or []],
            'pool': await dating_invite_pool_left(int(site_id)),
            'next_sec': dating_invite_round_next(int(site_id)),
            'send_on': await _send_on(int(site_id))}


@router.post('/generate', include_in_schema=False)
async def dating_invite_generate(payload: DatingInviteGenerate,
                                 user: AuthUser = Depends(auth_required)):
    """Набирает пул инвайтов — то же, что делает круг, но по нажатию.

    Raises:
        HTTPException: 502 — не спросить, с кем уже говорили (без этого писать
            «здравствуйте» знакомому опаснее, чем не писать вовсе).

    ⚠ **Заход не отправляет ничего.** Он готовит реплики; уносит их круг отправки
    (при поднятом `invite_send_on`) или человек кнопкой на карточке.
    """
    try:
        got = await dating_invite_make(int(payload.site_id), int(payload.limit),
                                       int(payload.profile_id), payload.first_uid)
    except DatingInviteError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    return {'result': {**got,
                       'pool': await dating_invite_pool_left(int(payload.site_id))}}


@router.put('/{invite_id}', include_in_schema=False)
async def dating_invite_edit(invite_id: int, payload: DatingInviteEdit,
                             user: AuthUser = Depends(auth_required)):
    """Правит текст реплики в пуле.

    Raises:
        HTTPException: 404 — инвайта нет; 409 — он уже ушёл.

    ⚠ **Отправленное не правится.** Строка отвечает на вопрос «что человек
    прочитал», и задним числом этот ответ меняться не должен.
    """
    row = await _row(invite_id)
    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='инвайт уже ушёл — его текст не меняется')

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_invite SET body = %s WHERE id = %s',
                         (payload.body, int(invite_id)))
        await db.connection.commit()

    return {'result': await _row(invite_id)}


@router.post('/{invite_id}/send', include_in_schema=False)
async def dating_invite_send(invite_id: int,
                             user: AuthUser = Depends(auth_required)) -> dict:
    """Отправляет один инвайт из пула — тот, на кнопку которого нажали.

    Raises:
        HTTPException: 404 — инвайта нет; 409 — уже ушёл либо не ушло, с причиной.

    ⚠ **Кнопка — не единственный отправитель.** Реплики уходят сами
    (`dating_invite_send_round.py`); кнопка остаётся тем, чем была при отладке:
    ею проверяют текст глазами и досылают то, что круг пропустил с ошибкой —
    такую строку он не берёт до вмешательства человека.

    Отправляют оба **одной дорогой** (`dating_invite_send_one`): отметка исхода,
    запись в журнал и то, что считать удачей, разойтись у них не должны.

    ⚠ **`invite_send_on` эту ручку больше не запирает, и это исправление.**
    Пока круга не было, флаг отпирал одну только кнопку, и 403 здесь был
    осмыслен. С кругом флаг стал значить «слать наружу самим», и запрет на кнопку
    сделал «остановить автоматику» неотличимым от «запретить себе проверить
    руками»: сняв флаг, человек терял и способ дослать зависшую реплику.

    Защищаться надо от автоматики, а не от человека: он жмёт кнопку на карточке,
    где текст перед глазами, — решение уже принято и осмотрено. Поймано живьём в
    первый же день работы круга.

    ⚠ **Не ушло — 409, а не 500.** Отказ сайта («собеседника нет в чате», «вход не
    прошёл») это не поломка панели: она отработала верно и принесла причину.

    ⚠ **`ok` от сайта — это «принял кадр», а не «дошло до адресата».** У LadaDate
    сообщение от женской анкеты сайт принимает и показывает в её же переписке, а
    мужчине оно не приходит, пока тот не пригласит первым (`needInvite`,
    подробности в `dating_send.py`). Поэтому «ушло» здесь означает ровно то, что
    сказал сайт, и ни слова больше.
    """
    invite = await _row(invite_id)
    site_id = int(invite['site_id'])

    if invite['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='инвайт уже ушёл')

    got = await dating_invite_send_one(invite)
    if not got['ok']:
        # Причина уже лежит в строке пула и в журнале — её положил отправитель:
        # человек закроет вкладку и вернётся завтра, а в ответе HTTP её к тому
        # времени нет.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=got['error'])

    return {'result': await _row(invite_id)}


@router.delete('', include_in_schema=False)
async def dating_invite_clear(site_id: int = 0,
                              user: AuthUser = Depends(auth_required)):
    """Очищает пул сайта — разом всё, что ещё не ушло.

    ⚠ **Ушедшее остаётся.** Оно уже случилось: человек его прочитал, и стирать
    строку значило бы врать себе о том, кого мы уже позвали, — а по этой же строке
    держится правило «одного человека зовём один раз».

    ⚠ **Путь без номера, и объявлен он до `/{invite_id}`.** Иначе пустой хвост
    однажды прочитался бы как номер инвайта — тот же порядок, что у пула писем.
    """
    if not int(site_id or 0):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не назван сайт — очищать нечего')

    async with mysql_get_db_async() as db:
        await db.execute("DELETE FROM dating_invite"
                         " WHERE site_id = %s AND status = 'draft'",
                         (int(site_id),))
        # ⚠ `db.rowcount`, а не `db.cursor.rowcount`: у обёртки курсора своего
        # `cursor` нет, и второе роняло ручку `AttributeError`. Поймано живым
        # нажатием — очистка отвечала 500 там, где всё удалилось.
        dropped = int(db.rowcount or 0)
        await db.connection.commit()

    if dropped:
        logger_info(f'[dating] пул инвайтов сайта #{site_id} очищен: убрано {dropped}')

    return {'result': {'dropped': dropped}}


@router.delete('/{invite_id}', include_in_schema=False)
async def dating_invite_drop(invite_id: int,
                             user: AuthUser = Depends(auth_required)):
    """Убирает один инвайт из пула — тот, на крестик которого нажали.

    Raises:
        HTTPException: 404 — инвайта нет; 409 — он уже ушёл.
    """
    row = await _row(invite_id)
    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='инвайт уже ушёл — его не убрать')

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_invite WHERE id = %s', (int(invite_id),))
        await db.connection.commit()

    return {'result': {'dropped': 1}}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _send_on(site_id: int) -> bool:
    """Разрешена ли отправка инвайтов у сайта.

    ⚠ Колонкой, а не настройкой в JSON: выключатель стоит в полосе подвкладок и
    дёргается щелчком — там же, где рассылочные, и по тем же соображениям
    (`dating_site.py`).
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT invite_send_on FROM dating_site'
                             ' WHERE site_id = %s', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] выключатель инвайтов сайта #{site_id} '
                    f'не прочитался: {e}')

        # ⚠ Не прочитался — считаем снятым. Верная сторона ошибки: молчание
        # дешевле реплики, ушедшей живому человеку вопреки решению.
        return False

    return bool((row or {}).get('invite_send_on'))


async def _row(invite_id: int) -> dict:
    """Строка пула или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT i.id, i.site_id, i.profile_id, i.uid, i.name, i.photo,'
            ' i.body, i.status, i.error, i.sent_at, i.created_at,'
            ' p.login AS profile_login, p.profile AS profile_card'
            ' FROM dating_invite i'
            ' LEFT JOIN dating_profile p ON p.id = i.profile_id'
            ' WHERE i.id = %s', (int(invite_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='инвайта нет')

    return _answer(row)


def _answer(row) -> dict:
    """Строка пула в вид для страницы: имя анкеты вместо её карточки JSON.

    ⚠ **Лицо и номер девушки — тоже отсюда.** «Доставка» рисует обе стороны тем
    же видом, что «Диалоги» (`talkSide`): лицо, имя, номер на сайте. Карточка
    анкеты у нас уже в руках — джойн всё равно сделан ради имени, — и отдать из
    неё два поля дешевле, чем заставить страницу спрашивать анкету на каждую
    строку списка.
    """
    got = dict(row)
    card = dating_json_load(got.pop('profile_card', None)) or {}
    got['profile_name'] = str(card.get('name') or got.get('profile_login') or '')
    got['profile_photo'] = str(card.get('photo') or '')
    got['profile_uid'] = str(card.get('site_uid') or '')
    got['sent_at'] = str(got['sent_at']) if got.get('sent_at') else ''
    got['created_at'] = str(got.get('created_at') or '')

    return got
