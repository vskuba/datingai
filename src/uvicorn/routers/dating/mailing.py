"""Пул писем: ручки страницы «Письма рассылка».

Рассылка делается в два приёма, и это главное решение здесь. Сперва письма
**готовятся** — набирается пул карточек: кто, кому, с каким текстом. Потом их
читают, правят и только затем отправляют.

⚠ **Ради того и разделено, что письма уходят живым людям.** Кнопка «разослать»,
делающая всё разом, не оставляет места посмотреть, что получилось, — а получиться
может что угодно: агент выдумывает, переменные бывают не заполнены, шаблон правили
вчера.

⚠ **Сам набор живёт не здесь**, а в `src/dating/mailing.py`: его зовут двое
— человек кнопкой и круг рассылки по расписанию сайта. Тут только то, что делает
его ручкой: разбор запроса, отказы словами и права.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.json_column import json_column_load
from src.dating.mail import MAIL_BODY_MAX
from src.dating.mail_send import (DatingMailSendError, mail_send_mark,
                                         mail_send_one)
from src.dating.mail_reply import mail_reply_delivery
from src.dating.online import online_show
from src.dating.pool_pair import (pool_delivery_where, pool_pair_girl,
                                         pool_pair_joins, pool_pair_select)
from src.dating.send_round import send_why
from src.dating.mailing import (MAILING_BATCH_DEFAULT,
                                       MAILING_BATCH_MAX,
                                       mailing_gaps, mailing_girls,
                                       mailing_make, mailing_people,
                                       mailing_templates)
from src.uvicorn.routers.setting import setting_timezone_hours
from src.uvicorn.routers.dating.site_ import (site_seconds,
                                                    site_setting)
# ⚠ Сборка строки ответа берётся у соседа, а не повторяется здесь: журнал доставки
# показывает ответы теми же полями, что «Письма → Входящие», и вторая сборка
# разошлась бы с первой — у неё, например, лицо приходит из письма (`in_photo`).
#
# ⚠ Цикла нет и быть не может: `mail_in` не знает про `mailing` (проверено), а
# общее у них — только `site_`.

router = APIRouter(prefix='/api/dating/mailing', tags=['dating'])

# Сколько писем отдаём странице разом. Пул читают глазами, и тысячная карточка не
# помогает никому.
MAILING_LIMIT = 300

# Сколько строк на странице журнала доставки. ⚠ **То же число, что у доставки
# инвайтов и писем избранным**: три журнала стоят на соседних вкладках, выглядят
# одинаково и листаются одним пейджером — разные размеры страницы читались бы как
# разница по существу, которой нет.
MAILING_DELIVERY_PER = 25



class DatingMailingGenerate(BaseModel):
    """Чего просят у кнопки «Генерировать письма»."""

    site_id: int = Field(..., gt=0, description='Сайт: его анкеты, его адресаты')
    mail_ids: list[int] = Field(default_factory=list,
                                description='Шаблоны, по которым готовим письма')
    # ⚠ Ноль — «сколько задано настройкой сайта», а не «нисколько». Так ручка
    # остаётся отвечать и тем, кто про это поле не знает.
    limit: int = Field(0, ge=0, le=MAILING_BATCH_MAX,
                       description='Сколько пар пробуем за это нажатие')
    # ⚠ Ноль — «от всех, у кого включена рассылка». Так же, как пустой список
    # шаблонов означает «любым из включённых»: выбор на странице **сужает**.
    profile_id: int = Field(0, ge=0,
                            description='Писать только от этой анкеты')
    first_uid: str = Field('', max_length=32,
                           description='Кого поставить первым — карточка, '
                                       'выбранная в ленте «Все онлайн». '
                                       '⚠ Перестановка, а не отбор')


class DatingMailingEdit(BaseModel):
    """Правка письма в пуле — то, что человек поменял, прочитав."""

    body: str = Field(..., min_length=1, max_length=MAIL_BODY_MAX)


@router.get('', include_in_schema=False)
async def mailing_list(site_id: int = 0, status_: str = 'draft',
                              user: AuthUser = Depends(auth_required)):
    """Пул писем сайта — свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не письма всех сайтов.
        status_: `draft` (пул), `sent`, `failed`, `delivery` или `all`. ⚠ **Можно
            несколько через запятую.**

            `delivery` — не статус, а вопрос: «что происходило с доставкой». Он
            шире перечисления `sent,failed`, и вот почему. Неудачная отправка
            **оставляет письмо в пуле** (`draft` с непустым `error`, см.
            `mail_send.py`), чтобы её можно было повторить, — и по статусу
            такое письмо от нетронутого не отличить. Спрашивай вкладка
            «Доставка» `sent,failed`, она молчала бы ровно о тех письмах, ради
            которых на неё и заходят: о тех, что не дошли.

    В ответе, помимо полей строки, — имена анкеты и адресата: карточка показывает
    «от кого кому», и без них страница делала бы два лишних запроса на каждое
    письмо.
    """
    if not int(site_id or 0):
        return {'result': [], 'send_why': ''}

    sql = ('SELECT m.id, m.site_id, m.profile_id, m.mail_id, m.uid,'
           # ⚠ Имя и лицо собеседника — общим куском
           # (`pool_pair.py`): их собирают три пула писем, и свой
           # запрос в каждом однажды разошёлся — у рассылки лица не
           # было вовсе, хотя оно лежало в базе.
           + pool_pair_select('m', own_photo=False) +
           ' m.body, m.status, m.error, m.sent_at, m.created_at,'
           ' p.login AS profile_login, p.profile AS profile_card,'
           ' t.name AS mail_name'
           ' FROM mailing m'
           ' LEFT JOIN profile p ON p.id = m.profile_id'
           ' LEFT JOIN mail t ON t.id = m.mail_id'
           + pool_pair_joins() +
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
    args.append(MAILING_LIMIT)

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    return {'result': [_answer(row) for row in rows],
            # ⚠ Почему полный пул не уходит — вместе со списком: «50 из 50» без
            # объяснения читается как поломка (`send_round.py`).
            'send_why': await send_why(int(site_id), 'mailing',
                                              await setting_timezone_hours())}


@router.get('/delivery', include_in_schema=False)
async def mailing_delivery(site_id: int = 0, page: int = 1, per: int = 0,
                                  user: AuthUser = Depends(auth_required)):
    """Журнал доставки писем: **рассылка и ответы одним списком**, страницей.

    Args:
        site_id: сайт-закладка. Ноль — пустой список.
        page: страница, с единицы. Больше последней — отдаём последнюю.
        per: сколько строк на ней. Ноль — умолчание `MAILING_DELIVERY_PER`.

    Returns:
        dict: `result` — строки обоих родов, свежие сверху, у каждой `kind`
        (`mailing` — ушло по шаблону рассылки, `reply` — ответ на его письмо).
        Рядом `total`, `page`, `pages` — ими страница рисует пейджер.

    ## ⚠⚠ Почему ручка одна, а не две

    Страница показывала этот журнал **двумя запросами** и сливала их у себя. Со
    страницами так нельзя: «вторая страница рассылки» плюс «вторая страница
    ответов» — не вторая страница журнала, и число страниц по такому слиянию
    посчитать нечем. Отсюда одна ручка: она и сливает, и считает.

    ⚠ **Метку `kind` ставит сервер.** Страница не должна угадывать род письма по
    набору полей: карточки выглядят одинаково («Diana → Semen»), а вещи разные —
    первое ушло незнакомому по шаблону, второе написано на его письмо.

    ## ⚠⚠ Как берётся страница: слияние двух отсортированных списков

    Чтобы отдать первые `page * per` строк журнала, довольно взять по столько же
    свежих из каждой таблицы, слить и отрезать нужный кусок: строки за этим
    пределом в срез не попадут ни при каком порядке. Счёт — сумма двух `COUNT` по
    тем же условиям.

    ⚠ **Цена приёма растёт с номером страницы**: сороковая страница по 25 строк
    читает по тысяче строк из каждой таблицы. Журнал читают с первых страниц, и это
    терпимо; станет мало — надо идти дальше `UNION ALL` по скелету
    (`источник, id, время`) со страницей на нём и дозагрузкой полных строк по
    ключам. Приём выбран сознательно, и предел его назван, чтобы следующий читатель
    не считал это недосмотром.
    """
    if not int(site_id or 0):
        # ⚠ Поля пейджера есть и в пустом ответе: страница читает их без проверок,
        # и `pages: undefined` нарисовало бы пейджер из одной мёртвой страницы.
        return {'result': [], 'page': 1, 'pages': 1, 'total': 0}

    per = min(int(per or 0) or MAILING_DELIVERY_PER, MAILING_LIMIT)
    page = max(1, int(page or 1))
    # Сколько свежих строк хватит взять из каждой таблицы, чтобы отрезать нужную
    # страницу слияния.
    take = page * per

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT (SELECT COUNT(*) FROM mailing m WHERE m.site_id = %s'
            + pool_delivery_where('m') +
            ') AS letters,'
            ' (SELECT COUNT(*) FROM mail_reply r WHERE r.site_id = %s'
            + pool_delivery_where('r') +
            ') AS replies',
            (int(site_id), int(site_id)))
        counts = (await db.fetchone()) or {}
        total = int(counts.get('letters') or 0) + int(counts.get('replies') or 0)

        pages = max(1, -(-total // per))
        # ⚠ Страницу поправляем, а не отвергаем: журнал мог укоротиться, пока
        # человек листал, и отказ вместо последней страницы читался бы как поломка.
        page = min(page, pages)
        take = page * per

        # ⚠ Письма рассылки — **тем же запросом, что пул** (`mailing_list`): поля
        # собираются одним куском (`pool_pair_select`), и второй запрос за теми же
        # строками разошёлся бы с первым на первой правке.
        await db.execute(
            'SELECT m.id, m.site_id, m.profile_id, m.mail_id, m.uid,'
            + pool_pair_select('m', own_photo=False) +
            ' m.body, m.status, m.error, m.sent_at, m.created_at,'
            ' p.login AS profile_login, p.profile AS profile_card,'
            ' t.name AS mail_name'
            ' FROM mailing m'
            ' LEFT JOIN profile p ON p.id = m.profile_id'
            ' LEFT JOIN mail t ON t.id = m.mail_id'
            + pool_pair_joins() +
            ' WHERE m.site_id = %s'
            + pool_delivery_where('m')
            # ⚠ По времени события, а не по номеру: журнал читают как ленту «что
            # происходило», и неудача, случившаяся позже удачи, обязана стоять выше.
            # У неудачной `sent_at` нет вовсе — его снимает сама неудача.
            + ' ORDER BY COALESCE(m.sent_at, m.created_at) DESC, m.id DESC'
            ' LIMIT %s',
            (int(site_id), take))
        letters = [{**_answer(row), 'kind': 'mailing'}
                   for row in (await db.fetchall()) or []]

        # ⚠⚠ Ответы читает **общая функция** (`mail_reply_delivery`), а не свой
        # запрос: тот же журнал есть на «Фаворитах», и вторая копия чтения разошлась
        # бы с первой — полями, порядком или условием отбора.
        #
        # ⚠ Здесь без отбора по избранным: журнал писем отвечает на «что вообще
        # уходило с сайта», и ответ незнакомому — такой же ответ.
        replies = await mail_reply_delivery(db, int(site_id), take)

    # ⚠ Ключ сортировки — **время события**, и он же стоит в обоих запросах.
    # Разойдись они — срез брал бы строки не по тому порядку, в котором их считали.
    #
    # ⚠ `updated_at` в ключе — ради **ответов**: у неудачного это единственное его
    # время (`sent_at` снимает сама неудача). У писем рассылки столбца нет вовсе, и
    # для них звено просто пустое.
    rows = sorted(letters + replies,
                  key=lambda one: (str(one.get('sent_at')
                                       or one.get('updated_at')
                                       or one.get('created_at') or ''),
                                   int(one.get('id') or 0)),
                  reverse=True)

    return {'result': rows[(page - 1) * per:page * per],
            'page': page, 'pages': pages, 'total': total}


@router.get('/gaps', include_in_schema=False)
async def mailing_gaps_list(site_id: int = 0,
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

    gaps = await mailing_gaps(int(site_id))

    return {'result': {str(profile_id): names for profile_id, names in gaps.items()}}


@router.post('/generate', include_in_schema=False)
async def mailing_generate(payload: DatingMailingGenerate,
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
    templates = await mailing_templates(site_id, mail_ids)
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
    girls = await mailing_girls(site_id)
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
    # Выбранный в ленте — вперёд очереди. ⚠ Не отбор: остальные адресаты
    # остаются, а уже занятого пропустит `_taken`, как и прежде.
    first_uid = str(payload.first_uid or '').strip()
    if profile_id and not any(girl['id'] == profile_id for girl in girls):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=('выбранная анкета не готова к рассылке: либо у неё выключена '
                    'рассылка (включают на «Анкетах»), либо она сейчас не в сети '
                    '— письма готовятся только от тех, кто на сайте'))

    if not await mailing_people(site_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='в сети сейчас никого нет — некому писать')

    # ⚠ **Сколько готовить, говорит кнопка.** Настройки сайта здесь больше нет:
    # порция (`mailing_batch`) отвечала на тот же вопрос, что и потолок пула,
    # вторым голосом, а круг теперь добирает пул целиком (`mailing_round.py`).
    #
    # Числа с кнопки нет (старая вкладка, другой клиент) — берём умолчание:
    # столько, сколько прежде брала настройка по умолчанию.
    limit = min(int(payload.limit or 0) or MAILING_BATCH_DEFAULT,
                MAILING_BATCH_MAX)

    result = await mailing_make(site_id, templates, limit, profile_id,
                                       first_uid)

    return {'result': {**result, 'limit': limit}}


@router.put('/{mailing_id}', include_in_schema=False)
async def mailing_edit(mailing_id: int, payload: DatingMailingEdit,
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
        await db.execute('UPDATE mailing SET body = %s WHERE id = %s',
                         (payload.body, int(mailing_id)))
        await db.connection.commit()

    return {'result': await _row(mailing_id)}


@router.post('/{mailing_id}/send', include_in_schema=False)
async def mailing_send(mailing_id: int,
                              user: AuthUser = Depends(auth_required)) -> dict:
    """Отправляет одно письмо из пула — то, на кнопку которого нажали.

    Returns:
        dict: письмо после отправки — страница показывает новое состояние, не
        перечитывая пул целиком.

    Raises:
        HTTPException: 404 — письма нет; 409 — не ушло, с причиной словами.

    ⚠ **Отправку заводит человек, а не круг.** Письма уходят живым людям, и решение
    «это письмо отправить» принимается по прочтении — на карточке, где текст перед
    глазами. Кругового отправителя нет вовсе (`mail_send.py`).

    ⚠ **Не ушло — 409, а не 500.** Отказ сайта («слишком часто», «пароль не подошёл»)
    это не поломка панели: она отработала верно и принесла причину. Пятисотый увёл бы
    человека искать ошибку у нас.

    ⚠ **Не ушло — письмо остаётся в пуле.** Причина ложится в `error`, строка
    остаётся `draft`, и попробовать можно ещё раз. Почему не `failed` — в
    `mail_send.py`.

    Открыто всем вошедшим, как и правка письма: с пулом работает оператор, и это
    та самая работа, ради которой панель и открывают.
    """
    letter = await _row(mailing_id)

    profile = await _sender(int(letter['profile_id']))
    site_id = int(letter['site_id'])

    base = str(await site_setting(site_id, 'api_base')).strip()
    password = str(await site_setting(site_id, 'api_password')).strip()

    try:
        await mail_send_one(letter, profile, base, password)
    except DatingMailSendError as e:
        # ⚠ Причину храним **до** ответа: страница покажет её на карточке, а человек
        # закроет вкладку и вернётся завтра — в ответе HTTP её к тому времени нет.
        await mail_send_mark(letter, sent=False, error=str(e))
        logger_info(f'[dating] письмо #{mailing_id} не ушло: {e}')

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {'result': await _row(mailing_id)}


@router.delete('', include_in_schema=False)
async def mailing_clear(site_id: int = 0,
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
            "DELETE FROM mailing WHERE site_id = %s AND status = 'draft'",
            (int(site_id),))
        dropped = int(db.rowcount or 0)
        await db.connection.commit()

    if dropped:
        logger_info(f'[dating] пул сайта #{site_id} очищен: убрано {dropped}')

    return {'result': {'dropped': dropped}}


@router.delete('/{mailing_id}', include_in_schema=False)
async def mailing_drop(mailing_id: int,
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
        await db.execute('DELETE FROM mailing WHERE id = %s', (int(mailing_id),))
        await db.connection.commit()

    return {'result': {'id': int(mailing_id)}}


# ── Приватное ────────────────────────────────────────────────────────────────


#
# ⚠ Здесь только то, что превращает строку базы в ответ странице. Сам набор
# писем — в `src/dating/mailing.py`: его зовёт и круг рассылки, а круг
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
            ' FROM profile WHERE id = %s', (int(profile_id),))
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
            'SELECT m.id, m.site_id, m.profile_id, m.mail_id, m.uid,'
            + pool_pair_select('m', own_photo=False) +
            ' m.body, m.status, m.error, m.sent_at, m.created_at,'
            ' p.login AS profile_login, p.profile AS profile_card,'
            ' t.name AS mail_name'
            ' FROM mailing m'
            ' LEFT JOIN profile p ON p.id = m.profile_id'
            ' LEFT JOIN mail t ON t.id = m.mail_id'
            + pool_pair_joins() +
            ' WHERE m.id = %s', (int(mailing_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'письма #{mailing_id} нет')

    return _answer(row)


def _answer(row: dict) -> dict:
    """Строка базы в ответ странице."""
    card = json_column_load(row.get('profile_card'))

    # В сети ли адресат — **из снимка ленты, а не походом на сайт**. Тем же
    # `online_show`, что и карточки собеседников: правило одно, и вторая
    # копия разошлась бы. Снимок держит свежим круг ленты, страница его не трогает.
    #
    # ⚠ Два поля, а не одно: «не в сети» и «не знаем» подписываются по-разному, и
    # слить их в `online: False` значило бы соврать про каждого, кого не было в
    # последнем снимке.
    online = online_show(int(row['site_id']), str(row['uid']))

    got = {'online': bool(online.get('online')),
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
            'mail_name': str(row.get('mail_name') or 'шаблон удалён')}

    # ⚠ Имя, лицо и номер анкеты — тем же общим куском, что у остальных
    # пулов: окно письма рисует обе стороны одним видом (`talkSide`).
    return pool_pair_girl(got, card or {})
