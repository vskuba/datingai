"""Пул писем: ручки страницы «Письма рассылка».

Рассылка делается в два приёма, и это главное решение здесь. Сперва письма
**готовятся** — набирается пул карточек: кто, кому, с каким текстом. Потом их
читают, правят и только затем отправляют.

⚠ **Ради того и разделено, что письма уходят живым людям.** Кнопка «разослать»,
делающая всё разом, не оставляет места посмотреть, что получилось, — а получиться
может что угодно: агент выдумывает, переменные бывают не заполнены, шаблон правили
вчера.

⚠ **Сам набор живёт не здесь**, а в `src/dating/dating_mailing.py`: его зовут двое
— человек кнопкой и круг рассылки по расписанию сайта. Тут только то, что делает
его ручкой: разбор запроса, отказы словами и права.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.dating_json import dating_json_load
from src.dating.dating_mail import DATING_MAIL_BODY_MAX
from src.dating.dating_mail_send import (DatingMailSendError, dating_mail_send_mark,
                                         dating_mail_send_one)
from src.dating.dating_online import dating_online_show
from src.dating.dating_mailing import (DATING_MAILING_BATCH_DEFAULT,
                                       DATING_MAILING_BATCH_MAX,
                                       dating_mailing_gaps, dating_mailing_girls,
                                       dating_mailing_make, dating_mailing_people,
                                       dating_mailing_templates)
from src.uvicorn.routers.dating.dating_site import (dating_site_seconds,
                                                    dating_site_setting)

router = APIRouter(prefix='/api/dating/mailing', tags=['dating'])

# Сколько писем отдаём странице разом. Пул читают глазами, и тысячная карточка не
# помогает никому.
DATING_MAILING_LIMIT = 300


class DatingMailingGenerate(BaseModel):
    """Чего просят у кнопки «Генерировать письма»."""

    site_id: int = Field(..., gt=0, description='Сайт: его анкеты, его адресаты')
    mail_ids: list[int] = Field(default_factory=list,
                                description='Шаблоны, по которым готовим письма')
    # ⚠ Ноль — «сколько задано настройкой сайта», а не «нисколько». Так ручка
    # остаётся отвечать и тем, кто про это поле не знает.
    limit: int = Field(0, ge=0, le=DATING_MAILING_BATCH_MAX,
                       description='Сколько пар пробуем за это нажатие')
    # ⚠ Ноль — «от всех, у кого включена рассылка». Так же, как пустой список
    # шаблонов означает «любым из включённых»: выбор на странице **сужает**.
    profile_id: int = Field(0, ge=0,
                            description='Писать только от этой анкеты')


class DatingMailingEdit(BaseModel):
    """Правка письма в пуле — то, что человек поменял, прочитав."""

    body: str = Field(..., min_length=1, max_length=DATING_MAIL_BODY_MAX)


@router.get('', include_in_schema=False)
async def dating_mailing_list(site_id: int = 0, status_: str = 'draft',
                              user: AuthUser = Depends(auth_required)):
    """Пул писем сайта — свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не письма всех сайтов.
        status_: `draft` (пул), `sent`, `failed`, `delivery` или `all`. ⚠ **Можно
            несколько через запятую.**

            `delivery` — не статус, а вопрос: «что происходило с доставкой». Он
            шире перечисления `sent,failed`, и вот почему. Неудачная отправка
            **оставляет письмо в пуле** (`draft` с непустым `error`, см.
            `dating_mail_send.py`), чтобы её можно было повторить, — и по статусу
            такое письмо от нетронутого не отличить. Спрашивай вкладка
            «Доставка» `sent,failed`, она молчала бы ровно о тех письмах, ради
            которых на неё и заходят: о тех, что не дошли.

    В ответе, помимо полей строки, — имена анкеты и адресата: карточка показывает
    «от кого кому», и без них страница делала бы два лишних запроса на каждое
    письмо.
    """
    if not int(site_id or 0):
        return {'result': []}

    sql = ('SELECT m.id, m.site_id, m.profile_id, m.mail_id, m.uid, m.name,'
           ' m.body, m.status, m.error, m.sent_at, m.created_at,'
           ' p.login AS profile_login, p.profile AS profile_card,'
           ' t.name AS mail_name'
           ' FROM dating_mailing m'
           ' LEFT JOIN dating_profile p ON p.id = m.profile_id'
           ' LEFT JOIN dating_mail t ON t.id = m.mail_id'
           ' WHERE m.site_id = %s')
    args = [int(site_id)]

    # ⚠ Незнакомое имя молча отбрасываем, а не отвечаем отказом: `status_` приходит
    # из адреса, и опечатка в нём не повод показывать человеку ошибку вместо писем.
    # Не осталось ни одного знакомого — значит спрашивали про всё.
    asked = [one.strip() for one in str(status_ or '').split(',')]
    wanted = [one for one in asked if one in ('draft', 'sent', 'failed')]

    # ⚠ «Всё про доставку» — это статусы плюс **черновики с причиной отказа**:
    # неудавшаяся отправка оставляет письмо в пуле, и по статусу его от нетронутого
    # не отличить.
    delivery = 'delivery' in asked

    if delivery:
        sql += (" AND (m.status IN ('sent', 'failed')"
                "      OR (m.status = 'draft' AND m.error <> ''))")
    elif wanted:
        sql += ' AND m.status IN (%s)' % ', '.join(['%s'] * len(wanted))
        args.extend(wanted)

    sql += ' ORDER BY m.id DESC LIMIT %s'
    args.append(DATING_MAILING_LIMIT)

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    return {'result': [_answer(row) for row in rows]}


@router.get('/gaps', include_in_schema=False)
async def dating_mailing_gaps_list(site_id: int = 0,
                                   user: AuthUser = Depends(auth_required)):
    """Каким анкетам сайта нечем заполнить письмо.

    ⚠ **Отдельной ручкой, а не полем в списке анкет.** Список анкет читают все
    четыре вкладки и вторая страница, а ответ здесь нужен одной: считать его
    всем — это лишний обход шаблонов и значений на каждое обновление списка.

    Args:
        site_id: сайт-закладка. Ноль — пустой ответ: анкеты выбираются по сайту.

    Returns:
        dict: `result` — анкета (строкой, ключом объекта) → чего не хватает. В
        ответе **только проблемные**: пустой объект значит «все готовы».
    """
    if not int(site_id or 0):
        return {'result': {}}

    gaps = await dating_mailing_gaps(int(site_id))

    return {'result': {str(profile_id): names for profile_id, names in gaps.items()}}


@router.post('/generate', include_in_schema=False)
async def dating_mailing_generate(payload: DatingMailingGenerate,
                                  user: AuthUser = Depends(auth_required)):
    """Набирает пул: письма по выбранным шаблонам.

    Returns:
        dict: `made` — сколько письмо готово, `skipped` — сколько пар пропущено из-за
        нехватки переменных, `reasons` — чего именно не хватило (первые несколько),
        `left` — сколько пар осталось на следующее нажатие.

    Raises:
        HTTPException: 422 — шаблонов нет вовсе, нет анкет с рассылкой или в сети
            никого нет.

    ⚠ **Пустой `mail_ids` — «любой из всех включённых»**, а не отказ: выбор шаблонов
    сужает набор, а не открывает его. Так же и `profile_id`: ноль — от всех анкет с
    рассылкой, номер — только от этой.

    ⚠ **Повторное нажатие продолжает с того же места**, а не делает то же самое:
    пары, для которых письмо уже лежит в пуле или ушло, пропускаются. Поэтому
    кнопку можно жать, пока пул не наберётся, и не бояться дублей.
    """
    site_id = int(payload.site_id)
    mail_ids = [int(one) for one in (payload.mail_ids or []) if int(one) > 0]

    # ⚠ **Пустой выбор — «любой из всех», а не отказ.** Прежде здесь стоял 422
    # «выберите хотя бы один шаблон»: рассудили, что молчаливую рассылку всеми
    # человек не заказывал. На деле выходило наоборот — выбирать приходилось перед
    # каждым нажатием, хотя обычно всё равно, каким шаблоном писать.
    #
    # Теперь выбор означает **сужение**: отметил три — пишем ими, не отметил ничего
    # — берём из всех включённых. Отказаться от сужения можно кнопкой сброса рядом
    # со списком. Тот же порядок, что у круга по расписанию, где выбирать некому.
    templates = await dating_mailing_templates(site_id, mail_ids)
    if not templates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=('на этом сайте нет включённых шаблонов — заведите их на «Письмах»'
                    if not mail_ids else 'выбранных шаблонов на этом сайте нет'))

    # ⚠ Пустоту называем **до** набора, а не после. Набор на пустом месте вернул бы
    # «сделано 0», и человек прочитал бы это как «сходили и ничего не нашлось» —
    # тогда как на деле он не включил рассылку ни одной анкете.
    # ⚠ Отбор требует и **сети**: письмо уходит от её имени, и сайт покажет
    # получателю отправителя, которого на сайте нет. Причину называем вслух —
    # «нет анкет с рассылкой» человек прочитал бы как «сними галочку где-то ещё»,
    # хотя галочка на месте, а девушка просто спит.
    girls = await dating_mailing_girls(site_id)
    if not girls:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='нет анкет, готовых к рассылке: нужна включённая рассылка '
                   '(«Анкеты») и статус «в сети» — письма готовятся только от '
                   'тех, кто сейчас на сайте')

    # ⚠ Выбранная анкета без рассылки — **отказ, а не молчаливый ноль**. Человек
    # выбрал её в списке и нажал кнопку: «сделано 0» он прочитал бы как «адресатов
    # не нашлось», а дело в галочке, которую он видит на другой странице.
    profile_id = int(payload.profile_id or 0)
    if profile_id and not any(girl['id'] == profile_id for girl in girls):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=('выбранная анкета не готова к рассылке: либо у неё выключена '
                    'рассылка (включают на «Анкетах»), либо она сейчас не в сети '
                    '— письма готовятся только от тех, кто на сайте'))

    if not await dating_mailing_people(site_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='в сети сейчас никого нет — некому писать')

    # ⚠ **Число с кнопки старше настройки сайта.** Настройка отвечает на вопрос
    # «сколько брать, когда решает круг»; здесь у экрана человек, и он только что
    # сказал, сколько хочет сейчас — спорить с ним значило бы сделать выбор
    # украшением.
    #
    # Настройка остаётся умолчанием: страница может и не назвать числа (старая
    # вкладка, другой клиент), и тогда порядок работы прежний.
    limit = int(payload.limit or 0) or min(
        await dating_site_seconds(site_id, 'mailing_batch')
        or DATING_MAILING_BATCH_DEFAULT, DATING_MAILING_BATCH_MAX)

    result = await dating_mailing_make(site_id, templates, limit, profile_id)

    return {'result': {**result, 'limit': limit}}


@router.put('/{mailing_id}', include_in_schema=False)
async def dating_mailing_edit(mailing_id: int, payload: DatingMailingEdit,
                              user: AuthUser = Depends(auth_required)):
    """Правит текст письма в пуле.

    Raises:
        HTTPException: 404 — письма нет; 409 — оно уже ушло.

    ⚠ **Отправленное не правится.** Журнал отвечает на вопрос «что человек
    получил», и задним числом этот ответ меняться не должен.
    """
    row = await _row(mailing_id)
    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='письмо уже ушло — его текст не меняется')

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_mailing SET body = %s WHERE id = %s',
                         (payload.body, int(mailing_id)))
        await db.connection.commit()

    return {'result': await _row(mailing_id)}


@router.post('/{mailing_id}/send', include_in_schema=False)
async def dating_mailing_send(mailing_id: int,
                              user: AuthUser = Depends(auth_required)) -> dict:
    """Отправляет одно письмо из пула — то, на кнопку которого нажали.

    Returns:
        dict: письмо после отправки — страница показывает новое состояние, не
        перечитывая пул целиком.

    Raises:
        HTTPException: 404 — письма нет; 409 — не ушло, с причиной словами.

    ⚠ **Отправку заводит человек, а не круг.** Письма уходят живым людям, и решение
    «это письмо отправить» принимается по прочтении — на карточке, где текст перед
    глазами. Кругового отправителя нет вовсе (`dating_mail_send.py`).

    ⚠ **Не ушло — 409, а не 500.** Отказ сайта («слишком часто», «пароль не подошёл»)
    это не поломка панели: она отработала верно и принесла причину. Пятисотый увёл бы
    человека искать ошибку у нас.

    ⚠ **Не ушло — письмо остаётся в пуле.** Причина ложится в `error`, строка
    остаётся `draft`, и попробовать можно ещё раз. Почему не `failed` — в
    `dating_mail_send.py`.

    Открыто всем вошедшим, как и правка письма: с пулом работает оператор, и это
    та самая работа, ради которой панель и открывают.
    """
    letter = await _row(mailing_id)

    profile = await _sender(int(letter['profile_id']))
    site_id = int(letter['site_id'])

    base = str(await dating_site_setting(site_id, 'api_base')).strip()
    password = str(await dating_site_setting(site_id, 'api_password')).strip()

    try:
        await dating_mail_send_one(letter, profile, base, password)
    except DatingMailSendError as e:
        # ⚠ Причину храним **до** ответа: страница покажет её на карточке, а человек
        # закроет вкладку и вернётся завтра — в ответе HTTP её к тому времени нет.
        await dating_mail_send_mark(letter, sent=False, error=str(e))
        logger_info(f'[dating] письмо #{mailing_id} не ушло: {e}')

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {'result': await _row(mailing_id)}


@router.delete('', include_in_schema=False)
async def dating_mailing_clear(site_id: int = 0,
                               user: AuthUser = Depends(auth_required)):
    """Очищает пул сайта — разом всё, что ещё не ушло.

    Args:
        site_id: сайт-закладка. Ноль — отказ, а не «очистить у всех»: очистка
            необратима, и промах в одну цифру стоил бы чужого пула.

    Returns:
        dict: `dropped` — сколько писем убрано.

    Raises:
        HTTPException: 422 — сайт не назван.

    ⚠ **Убираются только черновики.** Ушедшие письма остаются: они уже случились,
    и стереть запись значило бы соврать журналу — то же правило, что у удаления по
    одному.

    ⚠ **Путь без номера, и объявлен он до `/{mailing_id}`.** Иначе пустой хвост
    однажды прочитался бы как номер письма — тот же порядок, что у `/draft` и
    `/var` на «Письмах» (`api.md`, §3.7).
    """
    if not int(site_id or 0):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не назван сайт — очищать нечего')

    async with mysql_get_db_async() as db:
        await db.execute(
            "DELETE FROM dating_mailing WHERE site_id = %s AND status = 'draft'",
            (int(site_id),))
        dropped = int(db.rowcount or 0)
        await db.connection.commit()

    if dropped:
        logger_info(f'[dating] пул сайта #{site_id} очищен: убрано {dropped}')

    return {'result': {'dropped': dropped}}


@router.delete('/{mailing_id}', include_in_schema=False)
async def dating_mailing_drop(mailing_id: int,
                              user: AuthUser = Depends(auth_required)):
    """Убирает письмо из пула.

    Raises:
        HTTPException: 404 — письма нет; 409 — оно уже ушло.

    Убрать из пула — значит «это письмо не пойдёт». Ушедшее удалить нельзя: оно уже
    случилось, и стереть запись значило бы соврать журналу.
    """
    row = await _row(mailing_id)
    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='письмо уже ушло — из журнала оно не убирается')

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_mailing WHERE id = %s', (int(mailing_id),))
        await db.connection.commit()

    return {'result': {'id': int(mailing_id)}}


# ── Приватное ────────────────────────────────────────────────────────────────
#
# ⚠ Здесь только то, что превращает строку базы в ответ странице. Сам набор
# писем — в `src/dating/dating_mailing.py`: его зовёт и круг рассылки, а круг
# не должен ходить в роутеры.

async def _sender(profile_id: int) -> dict:
    """Анкета, от которой уходит письмо, — целиком.

    ⚠ Нужны **все поля входа**, а не одно имя: под этой анкетой заходят в API сайта,
    и для входа берутся `login`, `password` и `password_source`. Ровно на этом
    однажды падал набор пула (`KeyError: 'site_id'`), когда полей взяли меньше, чем
    нужно вызываемому.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, password, password_source, enabled'
            ' FROM dating_profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='анкета письма удалена — отправлять не от кого')

    if not int(row.get('enabled') or 0):
        # ⚠ Выключенную анкету не ведут вовсе (`dating.md`, §5.10), и отправка от
        # неё — действие наружу, самое заметное из всех.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='анкета выключена — от неё не отправляем')

    return dict(row)


async def _row(mailing_id: int) -> dict:
    """Письмо по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.id, m.site_id, m.profile_id, m.mail_id, m.uid, m.name,'
            ' m.body, m.status, m.error, m.sent_at, m.created_at,'
            ' p.login AS profile_login, p.profile AS profile_card,'
            ' t.name AS mail_name'
            ' FROM dating_mailing m'
            ' LEFT JOIN dating_profile p ON p.id = m.profile_id'
            ' LEFT JOIN dating_mail t ON t.id = m.mail_id'
            ' WHERE m.id = %s', (int(mailing_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'письма #{mailing_id} нет')

    return _answer(row)


def _answer(row: dict) -> dict:
    """Строка базы в ответ странице."""
    card = dating_json_load(row.get('profile_card'))

    # В сети ли адресат — **из снимка ленты, а не походом на сайт**. Тем же
    # `dating_online_show`, что и карточки собеседников: правило одно, и вторая
    # копия разошлась бы. Снимок держит свежим круг ленты, страница его не трогает.
    #
    # ⚠ Два поля, а не одно: «не в сети» и «не знаем» подписываются по-разному, и
    # слить их в `online: False` значило бы соврать про каждого, кого не было в
    # последнем снимке.
    online = dating_online_show(int(row['site_id']), str(row['uid']))

    return {'online': bool(online.get('online')),
            'online_known': bool(online.get('known')),
            'id': int(row['id']),
            'site_id': int(row['site_id']),
            'profile_id': int(row['profile_id']),
            'mail_id': int(row['mail_id'] or 0),
            'uid': str(row['uid']),
            'name': str(row['name']),
            'body': str(row['body']),
            'status': str(row['status']),
            'error': str(row['error'] or ''),
            'sent_at': row['sent_at'],
            'created_at': row['created_at'],
            # Имя анкеты, а не логин: логин это почта, и в карточке «от кого» она
            # читается как подпись робота. Логин остаётся запасным.
            'girl': str((card or {}).get('name') or row.get('profile_login') or ''),
            'mail_name': str(row.get('mail_name') or 'шаблон удалён')}
