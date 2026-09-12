"""Входящие письма: ручки вкладки «Письма входящие».

Забор живёт не здесь, а в `src/dating/dating_mail_in.py`: он ходит к API сайта, и
звать его будет не только эта ручка. Тут — разбор запроса, отказы словами и права.

⚠ **Читать может любой вошедший, забирать — тоже.** Это работа с перепиской анкеты,
ровно та, ради которой панель и открывают; правка шаблонов — другое дело, там письмо
уходит от всех анкет многим людям.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.dating_favorite import (DATING_FAVORITE_STATES,
                                        DATING_FAVORITE_STATES_SQL)
from src.dating.dating_mail_in import (DatingMailInError, dating_mail_in_fetch,
                                       dating_mail_in_unread)
from src.dating.dating_mail_reply import (dating_mail_reply_make,
                                          dating_mail_reply_thread)
from src.dating.dating_mail_send import (DatingMailSendError, dating_mail_send_mark,
                                         dating_mail_send_one)
from src.dating.dating_online import dating_online_show
from src.uvicorn.routers.dating.dating_site import dating_site_setting

router = APIRouter(prefix='/api/dating/mail-in', tags=['dating'])

# Сколько писем отдаём странице разом. Переписку читают глазами, и трёхсотое письмо
# не помогает никому.
DATING_MAIL_IN_LIMIT = 200


class DatingMailInSeen(BaseModel):
    """Отметка «просмотрено»."""

    seen: bool = Field(True, description='Просмотрено человеком')


@router.get('', include_in_schema=False)
async def dating_mail_in_list(profile_id: int = 0, site_id: int = 0,
                              user: AuthUser = Depends(auth_required)):
    """Входящие письма — свежие сверху.

    Args:
        profile_id: чьи письма. Ноль — все письма сайта.
        site_id: сайт. Нужен, когда анкета не названа.

    ⚠ **Ни того ни другого — пустой список, а не все письма всех сайтов.** Страница
    без выбранного сайта не должна показывать чужую переписку.
    """
    if not int(profile_id or 0) and not int(site_id or 0):
        return {'result': []}

    # ⚠ Имя анкеты берём **сразу**, а не отдельным запросом на письмо. Список
    # стал общим по сайту, и в нём рядом лежат письма разных девушек: без «кому»
    # человек не знает, чьё письмо читает. Из карточки анкеты (`profile`) — то же
    # имя, что показывает список слева; нет его — логин, он есть всегда.
    sql = ('SELECT m.id, m.site_id, m.profile_id, m.message_id, m.uid, m.name,'
           ' m.photo, m.age, m.city, m.country, m.body, m.date_post,'
           ' m.is_replied, m.seen, m.created_at,'
           ' p.login AS profile_login, p.profile AS profile_card'
           ' FROM dating_mail_in m'
           ' LEFT JOIN dating_profile p ON p.id = m.profile_id'
           ' WHERE ')
    args = []

    if int(profile_id or 0):
        sql += 'm.profile_id = %s'
        args.append(int(profile_id))
    else:
        sql += 'm.site_id = %s'
        args.append(int(site_id))

    # ⚠ **Избранные сюда не попадают: их переписка живёт на «Фаворитах →
    # Диалоги».** Занятия там и тут одинаковы — читать разговор и править ответ, —
    # и различаются они ровно собеседником: с избранным разговор ведут на его
    # вкладке, со всеми прочими здесь.
    #
    # Без этого условия одна и та же переписка стояла бы в двух списках, а
    # дважды показанный разговор — это дважды написанный ответ.
    #
    # ⚠ **По паре «анкета — человек», а не по человеку вообще.** Диалог
    # принадлежит паре: один и тот же мужчина, пишущий двум нашим девушкам, у
    # одной может быть в избранных, у другой нет — и у каждой свой разговор.
    sql += (' AND NOT EXISTS (SELECT 1 FROM dating_favorite f'
            '                  WHERE f.profile_id = m.profile_id AND f.uid = m.uid'
            f'                   AND f.state IN ({DATING_FAVORITE_STATES_SQL}))')
    args.extend(DATING_FAVORITE_STATES)

    # ⚠ По `date_post`, а не по `id`: письма приезжают пачкой, и порядок вставки —
    # порядок ответа сайта, а не времени. `id` вторым — у писем одной минуты порядок
    # должен быть устойчивым, иначе список перетасовывается между отрисовками.
    # ⚠ **Строка на переписку, а не на письмо.** Он мог написать три подряд, не
    # дождавшись ответа; три карточки на них — не список писем, а один и тот же
    # разговор, размноженный по экрану. Показываем последнее письмо пары «анкета —
    # собеседник», а прежние видны в окне, где им и место.
    #
    # Тем же `NOT EXISTS` по более свежему, что и отбор писем для ответа
    # (`dating_mail_reply._to_answer`): правило «последнее в переписке» одно на
    # проект, и второе его толкование разошлось бы с первым.
    sql += ('   AND NOT EXISTS ('
            '       SELECT 1 FROM dating_mail_in n'
            '        WHERE n.profile_id = m.profile_id AND n.uid = m.uid'
            '          AND (n.date_post > m.date_post'
            '               OR (n.date_post = m.date_post AND n.id > m.id)))')

    # ⚠ Сколько сообщений в переписке — **подзапросом рядом со списком**, а не
    # отдельной ручкой на письмо. Число нужно на каждой карточке, и сотня карточек
    # означала бы сотню запросов; сама переписка при этом по-прежнему едет только
    # по открытию (`/{id}/thread`).
    #
    # Считаем три источника, те же, что показывает окно: его письма, наши ответы и
    # **ушедшие** письма рассылки. Разойдись счёт с окном — человек открыл бы
    # карточку «5» и увидел три реплики.
    sql = sql.replace(
        ' FROM dating_mail_in m',
        ', (SELECT COUNT(*) FROM dating_mail_in t'
        '   WHERE t.profile_id = m.profile_id AND t.uid = m.uid)'
        ' + (SELECT COUNT(*) FROM dating_mail_reply t'
        '     WHERE t.profile_id = m.profile_id AND t.uid = m.uid)'
        " + (SELECT COUNT(*) FROM dating_mailing t"
        "     WHERE t.profile_id = m.profile_id AND t.uid = m.uid"
        "       AND t.status = 'sent') AS thread_count,"
        # Непрочитанные — по всей переписке, а не по одному письму: строка теперь
        # одна на разговор, и метка «новое» должна гореть, если новое есть в нём.
        ' (SELECT COUNT(*) FROM dating_mail_in t'
        '   WHERE t.profile_id = m.profile_id AND t.uid = m.uid'
        '     AND t.seen = 0) AS unseen_count,'
        # ⚠ **Свежесть разговора, а не письма.** Строка одна на переписку, и
        # порядок в списке должен отвечать на вопрос «где шевелилось последним».
        # Считать по `date_post` мало: ответ, написанный кругом **позже** его
        # письма, разговор не двигал — и переписка с готовым черновиком уезжала
        # вниз, под те, где отвечать нечем.
        #
        # Берём наибольшее из трёх: его письмо, наш ответ (**включая черновик** —
        # он тоже работа) и ушедшее письмо рассылки.
        ' GREATEST(m.date_post,'
        '   COALESCE((SELECT MAX(COALESCE(r.sent_at, r.created_at))'
        '               FROM dating_mail_reply r'
        '              WHERE r.profile_id = m.profile_id AND r.uid = m.uid),'
        '            m.date_post),'
        '   COALESCE((SELECT MAX(t.sent_at) FROM dating_mailing t'
        "              WHERE t.profile_id = m.profile_id AND t.uid = m.uid"
        "                AND t.status = 'sent'), m.date_post)) AS last_at"
        ' FROM dating_mail_in m')

    sql += ' ORDER BY last_at DESC, m.id DESC LIMIT %s'
    args.append(DATING_MAIL_IN_LIMIT)

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    return {'result': [_answer(row) for row in rows or []]}


@router.get('/unread', include_in_schema=False)
async def dating_mail_in_unread_map(site_id: int = 0,
                                    user: AuthUser = Depends(auth_required)):
    """Сколько непросмотренных писем у каждой анкеты сайта и где они лежат.

    Returns:
        dict: `{номер анкеты: {'all': сколько, 'fav': сколько от избранных}}` —
        только у тех, у кого письма есть.

    ⚠ **`fav` — не украшение счёта, а адрес перехода.** Письмо от избранного видно
    только на «Фавориты → Диалоги», от прочих — только на «Письма → Входящие»
    (§3.7.4). Значок вёл всегда во «Входящие», и для письма от избранного это был
    переход в пустоту.

    Своей ручкой, а не полем в списке анкет: список анкет перерисовывается своим
    кругом и по своим правилам, а значок писем меняется от действия человека —
    открыл письмо, и он должен погаснуть, не дожидаясь общего круга.
    """
    if not int(site_id or 0):
        return {'result': {}}

    return {'result': {str(k): v for k, v in
                       (await dating_mail_in_unread(int(site_id))).items()}}


@router.get('/{mail_in_id}/thread', include_in_schema=False)
async def dating_mail_in_thread_get(mail_in_id: int,
                                    user: AuthUser = Depends(auth_required)):
    """Переписка, к которой относится это письмо, — старые сверху.

    ⚠ **Та же нить, что у ответа** (`/reply/{id}/thread`), и тем же
    `dating_mail_reply_thread`: разговор один, и второй способ его собрать
    разошёлся бы с первым в день, когда добавится третий источник реплик.

    ⚠ **Своей ручкой, а не полем в списке.** Переписка нужна, только когда письмо
    открыли: у сотни писем это сотня разговоров, и класть их в список значило бы
    возить мегабайты ради того, что человек прочтёт у одного.
    """
    row = await _in_row(mail_in_id)

    thread = await dating_mail_reply_thread(int(row['profile_id']), str(row['uid']))

    # ⚠ Лица к репликам добавляет сервер, а не собирает страница: фото собеседника
    # лежит в самом письме, фото девушки — в её карточке. Собирай их страница, ей
    # пришлось бы держать две карты «кто есть кто» и обновлять их со списком.
    girl_photo = await _girl_photo(int(row['profile_id']))

    return {'result': [{**one, 'photo': row['photo'] if one['from'] == 'him'
                                        else girl_photo}
                       for one in thread]}


@router.post('/fetch', include_in_schema=False)
async def dating_mail_in_get(profile_id: int = 0,
                             user: AuthUser = Depends(auth_required)):
    """Забирает новые письма анкеты с сайта.

    Raises:
        HTTPException: 404 — анкеты нет; 409 — сайт не ответил или писать нечем.

    ⚠ **По анкете, а не по сайту.** Письма приходят конкретной девушке, и ходить за
    ними надо под ней: у сайта нет ручки «письма всех наших учёток», да и быть не
    может — он про них не знает.

    ⚠ **Отказ сайта — 409, а не 500.** Панель отработала верно и принесла причину;
    пятисотый увёл бы человека искать ошибку у нас.
    """
    profile = await _profile(int(profile_id))
    site_id = int(profile['site_id'])

    base = str(await dating_site_setting(site_id, 'api_base')).strip()
    password = str(await dating_site_setting(site_id, 'api_password')).strip()

    try:
        got = await dating_mail_in_fetch(profile, base, password)
    except DatingMailInError as e:
        logger_info(f'[dating] входящие анкеты #{profile_id}: {e}')

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {'result': got}


@router.get('/reply', include_in_schema=False)
async def dating_mail_reply_list(site_id: int = 0, status_: str = 'draft',
                                 user: AuthUser = Depends(auth_required)):
    """Пул ответов сайта — свежие сверху.

    ⚠ **Пул общий на сайт, а не на анкету.** Письма приходят разным девушкам, но
    читает ответы один человек и подряд: раздели мы пул по анкетам, ему пришлось бы
    обойти сорок списков, чтобы понять, есть ли работа.

    Отсюда и то, что колонка живёт без выбранной анкеты — в отличие от списка
    входящих слева, где выбор нужен: за письмами ходят под конкретной девушкой.
    """
    if not int(site_id or 0):
        return {'result': []}

    sql = ('SELECT r.id, r.site_id, r.profile_id, r.mail_in_id, r.uid, r.name,'
           ' r.body, r.status, r.error, r.sent_at, r.created_at,'
           ' p.login AS profile_login, p.profile AS profile_card,'
           # ⚠ Письмо, на которое отвечаем, — здесь же: ответ читают, сверяя с
           # ним, и второй запрос на каждую карточку был бы лишним.
           ' i.body AS in_body, i.date_post AS in_at,'
           # ⚠ Номер письма **на сайте**: он уедет в `ReplyForMessageId` при
           # отправке. Без него сайт покажет ответ отдельным письмом, и собеседник
           # не поймёт, на что ему отвечают.
           ' i.message_id AS in_message_id, i.photo AS in_photo'
           ' FROM dating_mail_reply r'
           ' LEFT JOIN dating_profile p ON p.id = r.profile_id'
           ' LEFT JOIN dating_mail_in i ON i.id = r.mail_in_id'
           ' WHERE r.site_id = %s')
    args = [int(site_id)]

    asked = [one.strip() for one in str(status_ or '').split(',')]

    # ⚠ `delivery` — не статус, а вопрос «что происходило с доставкой», и он шире
    # перечисления `sent,failed`. Неудачная отправка **оставляет ответ в пуле**
    # (`draft` с непустым `error`), чтобы её можно было повторить: по статусу
    # такой ответ от нетронутого не отличить. Правило то же, что у писем рассылки
    # (`dating_mailing.py`), и разойтись им нельзя — вкладка «Доставка» одна.
    if 'delivery' in asked:
        sql += (" AND (r.status IN ('sent', 'failed')"
                "      OR (r.status = 'draft' AND r.error <> ''))")
    else:
        wanted = [one for one in asked if one in ('draft', 'sent', 'failed')]
        if wanted:
            sql += ' AND r.status IN (%s)' % ', '.join(['%s'] * len(wanted))
            args.extend(wanted)

    sql += ' ORDER BY r.id DESC LIMIT %s'
    args.append(DATING_MAIL_IN_LIMIT)

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    return {'result': [_reply_answer(row) for row in rows or []]}


@router.post('/reply/make', include_in_schema=False)
async def dating_mail_reply_generate(site_id: int = 0, limit: int = 0,
                                     user: AuthUser = Depends(auth_required)):
    """Пишет ответы на входящие письма сайта.

    ⚠ **Кнопки за этой ручкой больше нет.** Пул набирает круг, пока поднят
    выключатель «генерация ответов» (`dating_reply_round.py`); кнопка «Написать
    ответы» убрана, потому что обещала то, что теперь происходит само. Ручка
    оставлена как повод набрать вне очереди — тем же способом, каким это устроено
    у рассылки: круг спит до своего срока, а разбирать пул иногда нужно сейчас.

    ⚠ **По сайту, а не по анкете.** Пул общий, и набор идёт по всем анкетам с
    включённой генерацией разом: человек нажимает один раз, а не сорок.

    ⚠ **Наружу ничего не уходит.** Ответы ложатся в пул и ждут; отправка — отдельный
    выключатель «отвечать» у сайта, выключенный по умолчанию.
    """
    if not int(site_id or 0):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не назван сайт — для кого писать ответы')

    return {'result': await dating_mail_reply_make(int(site_id), int(limit or 0))}


class DatingMailReplyEdit(BaseModel):
    """Правка ответа: текст, который человек поправил, прочитав."""

    body: str = Field(..., min_length=1, max_length=20000)


@router.put('/reply/{reply_id}', include_in_schema=False)
async def dating_mail_reply_edit(reply_id: int, payload: DatingMailReplyEdit,
                                 user: AuthUser = Depends(auth_required)):
    """Правит текст ответа — то, что уйдёт.

    ⚠ Только у неотправленного: ушедшее письмо уже случилось, и править его нечестно
    перед тем, кто его получил. Тот же порядок, что у пула рассылки.
    """
    row = await _reply_row(reply_id)
    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='ответ уже ушёл — править нечего')

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_mail_reply SET body = %s WHERE id = %s',
                         (payload.body.strip(), int(reply_id)))
        await db.connection.commit()

    return {'result': await _reply_row(reply_id)}


@router.get('/reply/{reply_id}/thread', include_in_schema=False)
async def dating_mail_reply_thread_get(reply_id: int,
                                       user: AuthUser = Depends(auth_required)):
    """Переписка, к которой относится этот ответ, — старые сверху.

    ⚠ **Своей ручкой, а не полем в списке пула.** Переписка нужна только когда ответ
    открыли: у полусотни карточек это полсотни разговоров, и класть их в список
    значило бы возить мегабайты ради того, что человек прочтёт у одного.
    """
    row = await _reply_row(reply_id)

    thread = await dating_mail_reply_thread(int(row['profile_id']), str(row['uid']))

    # ⚠ Лица к репликам **добавляет сервер**, а не собирает страница. Он и так их
    # знает: фото девушки лежит в её карточке, фото собеседника — во входящем письме.
    # Собирай их страница — ей пришлось бы держать две карты «кто есть кто» и
    # обновлять их вместе со списком.
    return {'result': [{**one, 'photo': row['photo'] if one['from'] == 'him'
                                       else row['girl_photo']}
                       for one in thread]}


@router.delete('/reply/{reply_id}', include_in_schema=False)
async def dating_mail_reply_drop(reply_id: int,
                                 user: AuthUser = Depends(auth_required)):
    """Убирает ответ из пула.

    ⚠ Только неотправленный: ушедшее письмо стереть нельзя — это соврать журналу.

    ⚠ **Письмо, на которое отвечали, остаётся**, и это важно: убрав ответ, человек
    говорит «не такой», а не «не отвечать вовсе». Письмо снова становится годным для
    ответа, и следующий заход напишет новый.
    """
    row = await _reply_row(reply_id)
    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='ответ уже ушёл — из пула его не убрать')

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_mail_reply WHERE id = %s', (int(reply_id),))
        await db.connection.commit()

    return {'result': {'dropped': 1}}


@router.post('/reply/{reply_id}/send', include_in_schema=False)
async def dating_mail_reply_send(reply_id: int,
                                 user: AuthUser = Depends(auth_required)):
    """Отправляет один ответ — тот, на кнопку которого нажали.

    ⚠ **Уходит именно ответом**: номер входящего письма едет в `ReplyForMessageId`.
    Без него сайт покажет его отдельным письмом, и собеседник не поймёт, на что ему
    отвечают.

    ⚠ **Не ушло — 409 и причина словами**, а письмо остаётся в пуле: причина обычно
    временная. Та же механика и тот же отправитель, что у пула рассылки.
    """
    row = await _reply_row(reply_id)

    profile = await _sender(int(row['profile_id']))
    site_id = int(row['site_id'])

    base = str(await dating_site_setting(site_id, 'api_base')).strip()
    password = str(await dating_site_setting(site_id, 'api_password')).strip()

    letter = {'id': int(row['id']), 'uid': row['uid'], 'name': row['name'],
              'body': row['body'], 'status': row['status'],
              'profile_id': int(row['profile_id']),
              # ⚠ Вот ради чего номер письма ехал в ответе списка.
              'reply_to': int(row['in_message_id'] or 0)}

    try:
        await dating_mail_send_one(letter, profile, base, password,
                                   table='dating_mail_reply')
    except DatingMailSendError as e:
        await dating_mail_send_mark(letter, sent=False, error=str(e),
                                    table='dating_mail_reply')
        logger_info(f'[dating] ответ #{reply_id} не ушёл: {e}')

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {'result': await _reply_row(reply_id)}


@router.put('/{mail_in_id}/seen', include_in_schema=False)
async def dating_mail_in_seen(mail_in_id: int, payload: DatingMailInSeen,
                              user: AuthUser = Depends(auth_required)):
    """Помечает письмо просмотренным (или снимает отметку).

    ⚠ **Это наш признак, а не сайта.** У него его нет вовсе: есть `IsReplied` —
    «ответили ли», а прочесть и не ответить это обычное дело. Поэтому отметку ставим
    сами, и сайт о ней не узнаёт.

    Снять её тоже можно: письмо открыли мимоходом, а вернуться к нему надо — и
    «непрочитанное» здесь единственный способ себе об этом напомнить.
    """
    await _row(mail_in_id)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_mail_in SET seen = %s WHERE id = %s',
                         (1 if payload.seen else 0, int(mail_in_id)))
        await db.connection.commit()

    return {'result': await _row(mail_in_id)}


class DatingMailInSeenThread(BaseModel):
    """Чей разговор прочитан: пара «анкета — собеседник»."""

    profile_id: int = Field(..., description='Анкета, у которой открыли разговор')
    uid: str = Field(..., description='Номер собеседника на сайте')


@router.put('/seen-thread', include_in_schema=False)
async def dating_mail_in_seen_thread(payload: DatingMailInSeenThread,
                                     user: AuthUser = Depends(auth_required)):
    """Помечает прочитанными **все** письма этого собеседника у этой анкеты.

    Returns:
        dict: `seen` — скольким письмам поставили отметку.

    ⚠ **Разговор читают целиком, а не по письму.** Обе вкладки — «Письма →
    Входящие» и «Фавориты → Диалоги» — показывают всю переписку сразу, и человек,
    открывший её, увидел каждое письмо. Пока отметка ставилась одному — тому, по
    которому щёлкнули, — значок в списке анкет показывал «2 непрочитано» у
    разговора, прочитанного целиком, и погасить его было нечем.

    ⚠ **Снятие отметки осталось поштучным** (`PUT /{id}/seen`): «вернуться к этому
    письму» — про одно письмо, а не про разговор.
    """
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_mail_in SET seen = 1'
                         ' WHERE profile_id = %s AND uid = %s AND seen = 0',
                         (int(payload.profile_id), str(payload.uid)))
        seen = int(db.rowcount or 0)
        await db.connection.commit()

    return {'result': {'seen': seen}}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _in_row(mail_in_id: int) -> dict:
    """Входящее письмо по номеру или 404. Нужны пара «анкета — собеседник» и фото."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, uid, name, photo'
                         ' FROM dating_mail_in WHERE id = %s', (int(mail_in_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'письма #{mail_in_id} нет')

    return dict(row)


async def _girl_photo(profile_id: int) -> str:
    """Фото анкеты для ленты разговора. Нет карточки — пусто, круг будет серым."""
    from src.dating.dating_json import dating_json_load

    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile FROM dating_profile WHERE id = %s',
                         (int(profile_id),))
        row = await db.fetchone()

    return str((dating_json_load((row or {}).get('profile')) or {}).get('photo') or '')


async def _reply_row(reply_id: int) -> dict:
    """Ответ по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT r.id, r.site_id, r.profile_id, r.mail_in_id, r.uid, r.name,'
            ' r.body, r.status, r.error, r.sent_at, r.created_at,'
            ' p.login AS profile_login, p.profile AS profile_card,'
            ' i.body AS in_body, i.date_post AS in_at,'
            ' i.message_id AS in_message_id, i.photo AS in_photo'
            ' FROM dating_mail_reply r'
            ' LEFT JOIN dating_profile p ON p.id = r.profile_id'
            ' LEFT JOIN dating_mail_in i ON i.id = r.mail_in_id'
            ' WHERE r.id = %s', (int(reply_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'ответа #{reply_id} нет')

    return _reply_answer(row)


async def _sender(profile_id: int) -> dict:
    """Анкета, от имени которой отвечаем, — целиком.

    ⚠ Нужны **все поля входа**: под ней заходят в API сайта. Ровно на этом однажды
    падал набор пула, когда полей взяли меньше, чем нужно вызываемому.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, password, password_source, enabled'
            ' FROM dating_profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='анкета ответа удалена — отправлять не от кого')

    if not int(row.get('enabled') or 0):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='анкета выключена — от неё не отправляем')

    return dict(row)


async def _profile(profile_id: int) -> dict:
    """Анкета целиком — под ней и пойдём на сайт.

    ⚠ Нужны **все поля входа**, а не одно имя: `login`, `password` и
    `password_source`. Ровно на этом однажды падал набор пула, когда полей взяли
    меньше, чем нужно вызываемому.
    """
    if not profile_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='не названа анкета — за чьими письмами идти')

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, password, password_source, enabled'
            ' FROM dating_profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'анкеты #{profile_id} нет')

    return dict(row)


async def _row(mail_in_id: int) -> dict:
    """Письмо по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, profile_id, message_id, uid, name, photo, age,'
            ' city, country, body, date_post, is_replied, seen, created_at'
            ' FROM dating_mail_in WHERE id = %s', (int(mail_in_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'письма #{mail_in_id} нет')

    return _answer(row)


def _reply_answer(row: dict) -> dict:
    """Строка пула ответов в ответ странице."""
    from src.dating.dating_json import dating_json_load

    card = dating_json_load(row.get('profile_card'))

    # В сети ли адресат — **из снимка ленты, а не походом на сайт**. Тем же
    # `dating_online_show`, что у пула рассылки и карточек собеседников: правило
    # одно, и вторая копия разошлась бы.
    online = dating_online_show(int(row['site_id']), str(row['uid']))

    return {'id': int(row['id']),
            'site_id': int(row['site_id']),
            'profile_id': int(row['profile_id']),
            'mail_in_id': int(row['mail_in_id']),
            'online': bool(online.get('online')),
            'online_known': bool(online.get('known')),
            'photo': str(row.get('in_photo') or ''),
            'uid': str(row['uid']),
            'name': str(row['name']),
            'body': str(row['body']),
            'status': str(row['status']),
            'error': str(row['error'] or ''),
            'sent_at': row['sent_at'],
            'created_at': row['created_at'],
            # Имя анкеты, а не логин: логин это почта, и в карточке «от кого» она
            # читается как подпись робота. То же правило, что у пула рассылки.
            'girl': str((card or {}).get('name') or row.get('profile_login') or ''),
            # Лицо девушки — из её карточки: в переписке реплики подписаны лицами, а
            # не только именами.
            'girl_photo': str((card or {}).get('photo') or ''),
            # На что отвечаем — рядом: ответ читают, сверяя с письмом.
            'in_body': str(row.get('in_body') or ''),
            'in_at': row.get('in_at'),
            'in_message_id': int(row.get('in_message_id') or 0)}


def _girl(row: dict) -> str:
    """Имя анкеты, которой пришло письмо. Нет карточки — логин.

    ⚠ Логин запасным, а не пустая строка: он есть всегда, а письмо без «кому» в
    общем списке читается как ничьё.
    """
    from src.dating.dating_json import dating_json_load

    card = dating_json_load(row.get('profile_card'))

    return str((card or {}).get('name') or row.get('profile_login') or '')


def _girl_face(row: dict) -> str:
    """Лицо анкеты из её карточки с сайта. Нет карточки — пусто, круг серый.

    ⚠ Своей функцией, а не выражением по месту: `dating_json_load` импортируется
    лениво (иначе роутер тянул бы предметный модуль на сборке), и повторять этот
    импорт в каждом месте разбора значило бы четыре одинаковых строки.
    """
    from src.dating.dating_json import dating_json_load

    card = dating_json_load((row or {}).get('profile_card')) or {}

    return str(card.get('photo') or '')


def _answer(row: dict) -> dict:
    """Строка базы в ответ странице."""
    place = ' · '.join(one for one in (str(row['city'] or '').strip(),
                                       str(row['country'] or '').strip()) if one)

    return {'id': int(row['id']),
            'site_id': int(row['site_id']),
            'profile_id': int(row['profile_id']),
            'message_id': int(row['message_id']),
            'uid': str(row['uid']),
            'name': str(row['name']),
            'photo': str(row['photo']),
            'age': int(row['age'] or 0),
            'place': place,
            'body': str(row['body']),
            'date_post': row['date_post'],
            'is_replied': bool(row['is_replied']),
            'seen': bool(row['seen']),
            # Кому пришло письмо. ⚠ Нужно с тех пор, как список стал общим по
            # сайту: рядом лежат письма разных анкет, и одно имя отправителя не
            # говорит, чьё это письмо.
            'girl': _girl(row),
            # ⚠ И лицо анкеты: карточка списка показывает **обе** стороны —
            # «анкета ↔ собеседник», как в «Диалогах» чата и фаворитов. Берём из
            # той же анкеты с сайта (`dating_profile.profile`), что и список
            # слева: одна и та же девушка не должна выглядеть в двух местах
            # по-разному.
            'profile_photo': _girl_face(row),
            # Сколько всего сообщений в этой переписке — для подписи на карточке.
            # ⚠ Ноль у ручки одного письма: подзапроса там нет, и число не
            # выдумываем.
            'thread_count': int(row.get('thread_count') or 0),
            # Сколько в переписке непрочитанных. ⚠ По разговору, а не по письму:
            # строка одна на переписку, и метка «новое» гаснет, только когда
            # прочитаны все.
            'unseen_count': int(row.get('unseen_count') or 0),
            # ⚠ Пустое тело — не то же, что пустое письмо: список приехал, а за
            # текстом ещё не сходили. Страница подписывает такие отдельно, чтобы
            # человек не решил, что письмо пустое.
            'has_body': bool(str(row['body'] or '').strip()),
            'created_at': row['created_at']}
