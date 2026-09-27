"""Инвайты: пул первых реплик в чат и отправка одной из них.

Ручки повторяют пул писем (`mailing.py`) — чтение, правка, удаление, повод
набрать, отправка одной карточки, — и это намеренно: занятие устроено так же, а
второй набор соглашений на ту же работу человеку пришлось бы разгадывать.

Расходятся они в двух местах, и оба про природу инвайта:

⚠ **Шаблона у инвайта нет.** Текст сочиняет агент под конкретного человека, и
`generate` не принимает списка шаблонов — принимать было бы нечего.

⚠ **Отправка идёт в чат, а не в почту сайта.** Дорога у неё своя —
`invite_send.py` поверх `send_message` (тот же путь, что у кнопки в
окне переписки и у автоответа), а не `mail_send_one`. Разница видна и в
ответе: «принял ли сайт кадр» — это не «дошло до адресата», и путать их дорого
(`send.py`).

⚠ **Выключатель сайта** `invite_send_on` (снят по умолчанию) включает **круг**
отправки (`invite_send_round.py`), а не кнопку. Кнопка на карточке живая
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
from src.dating.invite import (INVITE_BATCH_DEFAULT,
                                      INVITE_BATCH_MAX,
                                      DatingInviteError, invite_make,
                                      invite_pool_left)
from src.dating.invite_round import invite_round_next
from src.dating.invite_send import invite_send_one
from src.dating.invite_send_round import invite_send_why
from src.dating.json_column import json_column_load
from src.dating.mail_agent import INVITE_MAX
from src.dating.online import online_show
from src.dating.pool_pair import pool_pick_where
from src.uvicorn.routers.setting import setting_timezone_hours

router = APIRouter(prefix='/api/dating/invite', tags=['dating'])

# Сколько карточек отдаём странице. Тот же предел, что у пула писем: список читают
# глазами, и тысяча карточек в нём не нужна никому.
INVITE_LIMIT = 300


class DatingInviteGenerate(BaseModel):
    """Повод набрать пул: один шаблон от агента, карточки — подстановкой."""

    site_id: int = Field(..., description='Сайт-закладка')
    limit: int = Field(INVITE_BATCH_DEFAULT, ge=1,
                       le=INVITE_BATCH_MAX,
                       description='Сколько карточек сделать за это нажатие. '
                                   '⚠ Не число вызовов LLM: вызов один')
    profile_id: int = Field(0, description='Звать только от этой анкеты. '
                                          'Ноль — от всех включённых')
    first_uid: str = Field('', max_length=32,
                           description='Кого поставить первым — карточка, '
                                       'выбранная в ленте «Все онлайн». '
                                       '⚠ Перестановка, а не отбор')


class DatingInviteEdit(BaseModel):
    """Правка текста реплики перед отправкой."""

    body: str = Field(..., min_length=1, max_length=INVITE_MAX)


@router.get('', include_in_schema=False)
async def invite_list(site_id: int = 0, status_: str = 'draft',
                             page: int = 1, per: int = 0,
                             profile_id: int = 0, uid: str = '',
                             user: AuthUser = Depends(auth_required)):
    """Пул инвайтов сайта — свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не инвайты всех сайтов.
        status_: `draft` (пул), `sent`, `failed`, `delivery` или `all`; можно
            несколько через запятую.

            `delivery` — не статус, а вопрос: «что происходило с отправкой».
            Он шире перечисления `sent,failed`, и по той же причине, что у писем:
            неудачная отправка **оставляет инвайт в пуле** (`draft` с непустой
            причиной, см. `invite_send_mark`), чтобы её можно было
            повторить — рукой, потому что круг такую строку не берёт. Спрашивай
            подвкладка `sent,failed`, она молчала бы ровно о тех репликах, ради
            которых на неё и заходят, — о не дошедших.

        page: страница, с единицы. `per` — сколько строк на ней; ноль означает
            «все», и это **умолчание**: пул читают целиком, страницами ходит
            только журнал доставки.

            ⚠ **Перелистывание на сервере, а не в браузере.** Журнал растёт на
            реплику в минуту, и отдать его целиком значит однажды отдать всё
            подряд. Обрежь мы его потолком — отбор именами (ниже) искал бы внутри
            обрезка и молчал о том, что за ним.
        profile_id: показать только реплики этой анкеты. Ноль — всех.
        uid: показать только реплики этому собеседнику. Пусто — всем.

            ⚠ **Отбор тоже на сервере, и по той же причине.** Фильтруй страница
            то, что ей прислали, «все письма Семёну» означало бы «те письма
            Семёну, что попали на текущую страницу».

    В ответе, помимо полей строки, — имя анкеты: карточка показывает «от кого
    кому», и без него страница делала бы запрос на каждую реплику.

    ⚠ **Списка имён для отбора ручка не отдаёт, и это решение.** Он был: `who` со
    всеми анкетами и собеседниками журнала. За сутки это полторы сотни имён —
    стена чипсов поверх самого журнала, которую человек и увидел. Отбор начинается
    в карточке: имя, по которому хотят отобрать, и так стоит в строке перед
    глазами.
    """
    if not int(site_id or 0):
        return {'result': [], 'pool': 0, 'send_on': False, 'send_why': ''}

    sql = ('SELECT i.id, i.site_id, i.profile_id, i.uid, i.name, i.photo,'
           ' i.body, i.status, i.error, i.answer, i.sent_at, i.created_at,'
           ' i.updated_at,'
           ' p.login AS profile_login, p.profile AS profile_card'
           ' FROM invite i'
           ' LEFT JOIN profile p ON p.id = i.profile_id'
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

    # ⚠ Отбор именами прикладывается **после** выбора состояний, к тому же запросу:
    # он сужает уже отобранное, а не спрашивает другое.
    #
    # ⚠ Общей `pool_pick_where` — той же, что у двух журналов доставки писем: отбор
    # там завёлся позже, по образцу этого, и три своих написания одного условия
    # расходятся с первой правкой.
    pick, pick_args = pool_pick_where('i', profile_id, uid)
    sql += pick
    args.extend(pick_args)

    # ⚠ Сколько всего — **отдельным счётом по тем же условиям**, а не длиной
    # выборки: страница показывает 25 строк, а «страниц 7» считается по всем.
    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM (' + sql + ') AS all_rows',
                         tuple(args))
        total = int(((await db.fetchone()) or {}).get('n') or 0)

    per = min(int(per or 0) or INVITE_LIMIT, INVITE_LIMIT)
    pages = max(1, -(-total // per))
    # ⚠ Страницу поправляем, а не отвергаем: список мог укоротиться, пока человек
    # листал, и отказ вместо последней страницы читался бы как поломка.
    page = min(max(int(page or 1), 1), pages)

    # ⚠ **По времени события, а не по номеру строки.** `id` — это порядок, в
    # котором реплики **сочинили**, а журнал отвечает на «что происходило с
    # отправкой»: круг берёт из пула старейшую, и порядок отправки с порядком
    # заведения не совпадает вовсе.
    #
    # ⚠ У неудачной `sent_at` нет — его снимает сама неудача, — и временем события
    # остаётся `updated_at`: минута, когда исход записали. Без `COALESCE` такие
    # строки уезжали бы в конец журнала, то есть прятались бы ровно те, ради
    # которых на вкладку заходят.
    sql += ' ORDER BY COALESCE(i.sent_at, i.updated_at) DESC, i.id DESC LIMIT %s OFFSET %s'
    args.extend([per, (page - 1) * per])

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
            'page': page, 'pages': pages, 'total': total,
            'pool': await invite_pool_left(int(site_id)),
            'next_sec': invite_round_next(int(site_id)),
            # ⚠ **Почему ничего не уходит — вместе со списком**, а не отдельной
            # ручкой: «пул 50 из 50» и объяснение простоя читают одним взглядом, и
            # два запроса дали бы два разных момента времени.
            'send_why': await invite_send_why(
                int(site_id), await setting_timezone_hours()),
            'send_on': await _send_on(int(site_id)),
            'speed': await _speed(int(site_id))}


@router.post('/generate', include_in_schema=False)
async def invite_generate(payload: DatingInviteGenerate,
                                 user: AuthUser = Depends(auth_required)):
    """Набирает пул инвайтов — то же, что делает круг, но по нажатию.

    Raises:
        HTTPException: 502 — не спросить, с кем уже говорили (без этого писать
            «здравствуйте» знакомому опаснее, чем не писать вовсе).

    ⚠ **Заход не отправляет ничего.** Он готовит реплики; уносит их круг отправки
    (при поднятом `invite_send_on`) или человек кнопкой на карточке.
    """
    try:
        got = await invite_make(int(payload.site_id), int(payload.limit),
                                       int(payload.profile_id), payload.first_uid)
    except DatingInviteError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    return {'result': {**got,
                       'pool': await invite_pool_left(int(payload.site_id))}}


@router.put('/{invite_id}', include_in_schema=False)
async def invite_edit(invite_id: int, payload: DatingInviteEdit,
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
        await db.execute('UPDATE invite SET body = %s WHERE id = %s',
                         (payload.body, int(invite_id)))
        await db.connection.commit()

    return {'result': await _row(invite_id)}


@router.post('/{invite_id}/send', include_in_schema=False)
async def invite_send(invite_id: int,
                             user: AuthUser = Depends(auth_required)) -> dict:
    """Отправляет один инвайт из пула — тот, на кнопку которого нажали.

    Raises:
        HTTPException: 404 — инвайта нет; 409 — уже ушёл либо не ушло, с причиной.

    ⚠ **Кнопка — не единственный отправитель.** Реплики уходят сами
    (`invite_send_round.py`); кнопка остаётся тем, чем была при отладке:
    ею проверяют текст глазами и досылают то, что круг пропустил с ошибкой —
    такую строку он не берёт до вмешательства человека.

    Отправляют оба **одной дорогой** (`invite_send_one`): отметка исхода,
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
    подробности в `send.py`). Поэтому «ушло» здесь означает ровно то, что
    сказал сайт, и ни слова больше.
    """
    invite = await _row(invite_id)
    site_id = int(invite['site_id'])

    if invite['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='инвайт уже ушёл')

    got = await invite_send_one(invite)
    if not got['ok']:
        # Причина уже лежит в строке пула и в журнале — её положил отправитель:
        # человек закроет вкладку и вернётся завтра, а в ответе HTTP её к тому
        # времени нет.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=got['error'])

    return {'result': await _row(invite_id)}


@router.delete('', include_in_schema=False)
async def invite_clear(site_id: int = 0,
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
        await db.execute("DELETE FROM invite"
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
async def invite_drop(invite_id: int,
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
        await db.execute('DELETE FROM invite WHERE id = %s', (int(invite_id),))
        await db.connection.commit()

    return {'result': {'dropped': 1}}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _speed(site_id: int) -> dict:
    """Скорость отправки: сколько ушло за пять минут и что будет за двенадцать
    часов при таком темпе.

    Returns:
        dict: `last_5min` — реплик за последние пять минут, `per_hour` — темп в
        час, `forecast_12h` — прогноз, `cap_left` — сколько осталось до дневной
        нормы, `capped` — упрётся ли прогноз в неё.

    ## ⚠⚠ Считаем по своей таблице, а не по ленте событий

    Прежде число «ушло за 5 минут» бралось из ленты — и занижало в разы. Лента
    это **кольцо на тридцать строк** (`feed.FEED_KEEP`), общее для всех видов
    событий; на проде за минуту уходит до 98 реплик, то есть в пятиминутное окно
    не помещается и десятая часть.

    Лента для мигания конвертов годится (важно «идёт ли отправка сейчас»), а для
    счёта — нет.

    ## ⚠ Прогноз считается от текущего темпа и **упирается в дневную норму**

    Линейное «×144» обещало бы тысячи там, где норма сотня: круг остановится,
    дойдя до неё, и человек, поверивший прогнозу, ждал бы отправки, которой не
    будет. Поэтому отдаём и остаток нормы, и признак «прогноз упёрся».

    ⚠ **Сутки скользящие**, как и у самого потолка (`invite_send_round._today`):
    полночь для человека, который читает реплики, ничего не значит.
    """
    from src.dating.invite_send_round import INVITE_SEND_MAX_DAY_SETTING
    from src.uvicorn.routers.dating.site_ import site_seconds

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                "SELECT SUM(sent_at > NOW() - INTERVAL 5 MINUTE) AS per_five,"
                '       SUM(sent_at > NOW() - INTERVAL 1 DAY) AS per_day'
                "  FROM invite WHERE site_id = %s AND status = 'sent'"
                '   AND sent_at > NOW() - INTERVAL 1 DAY', (int(site_id),))
            row = await db.fetchone() or {}
    except Exception as e:
        logger_info(f'[dating] скорость отправки сайта #{site_id} не спросилась: {e}')

        return {}

    per_five = int(row.get('per_five') or 0)
    per_day = int(row.get('per_day') or 0)

    per_hour = per_five * 12
    cap = await site_seconds(int(site_id), INVITE_SEND_MAX_DAY_SETTING)
    cap_left = max(0, int(cap or 0) - per_day) if cap else 0

    forecast = per_hour * 12
    capped = bool(cap) and forecast > cap_left

    return {'last_5min': per_five,
            'per_hour': per_hour,
            'forecast_12h': min(forecast, cap_left) if capped else forecast,
            'cap_left': cap_left,
            'capped': capped}


async def _send_on(site_id: int) -> bool:
    """Разрешена ли отправка инвайтов у сайта.

    ⚠ Колонкой, а не настройкой в JSON: выключатель стоит в полосе подвкладок и
    дёргается щелчком — там же, где рассылочные, и по тем же соображениям
    (`site_.py`).
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT invite_send_on FROM site'
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
            ' i.body, i.status, i.error, i.answer, i.sent_at, i.created_at,'
           ' i.updated_at,'
            ' p.login AS profile_login, p.profile AS profile_card'
            ' FROM invite i'
            ' LEFT JOIN profile p ON p.id = i.profile_id'
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
    card = json_column_load(got.pop('profile_card', None)) or {}
    got['profile_name'] = str(card.get('name') or got.get('profile_login') or '')
    got['profile_photo'] = str(card.get('photo') or '')
    got['profile_uid'] = str(card.get('id') or '')
    got['sent_at'] = str(got['sent_at']) if got.get('sent_at') else ''
    # ⚠ Время события у **неудачной** строки: `sent_at` у неё снят, и без этого
    # поля карточка показывала бы пустое место там, где стоит время отправки.
    got['updated_at'] = str(got['updated_at']) if got.get('updated_at') else ''
    got['created_at'] = str(got.get('created_at') or '')

    # В сети ли собеседник — **из снимка ленты**, тем же `online_show`, что у пула
    # рассылки и у карточек собеседников. Правило одно на все три места, и
    # собственная копия разошлась бы с ним на первой же правке срока годности.
    #
    # ⚠ Полей два, а не одно: «не в сети» и «не знаем» подписываются по-разному.
    # Слить их в `online: False` значило бы соврать про каждого, кого не было в
    # последнем снимке, — а инвайт **уходит только человеку в сети**, и по серой
    # точке человек решает, ждать круга или досылать руками.
    online = online_show(int(got.get('site_id') or 0), str(got.get('uid') or ''))
    got['online'] = bool(online.get('online'))
    got['online_known'] = bool(online.get('known'))

    return got
