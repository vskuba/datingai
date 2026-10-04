"""Переключатели и расписания сайта: когда сайту работать и можно ли наружу.

Ручки одной страницы «Сайты», но не настроек-пар (те в `site_.py`), а
отдельных колонок: смены рассылки, инвайтов и шаблонов, выключатели автоответа,
забора писем, ответов и писем избранным.

⚠ **Всё это про сайт, а не про анкету**, и лежит своими колонками, а не ключом
в `settings`: настройки переписываются целиком ручкой `PUT /{site_id}`, и
ключ-расписание первое же сохранение соседней настройки стёрло бы молча.

⚠ **Выключатели, пишущие наружу, по умолчанию сняты** — письма и реплики
уходят живым людям; единственный включённый по умолчанию — забор входящих
(`mail_check_on`): он только читает.

Выделен из `site_.py`: общий селект и сверка снимка тут ни при чём, а
страница добавляет подвкладки именно к этому набору ручек.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_admin_required, auth_required
# Смены — те же, что у анкеты, и разбирает их тот же код: второй набор правил
# разошёлся бы с первым (`schedule.py`).
from src.dating.schedule import (schedule_check, schedule_dump,
                                        schedule_load)
from src.dating.site_setting import site_seconds

router = APIRouter(prefix='/api/dating/site', tags=['dating'])

# ── Две записи на весь файл ──────────────────────────────────────────────────
#
# ⚠⚠ **Здесь было шестнадцать одинаковых `INSERT … ON DUPLICATE KEY UPDATE`** —
# по одному на каждый выключатель и каждое расписание. Различались они именем
# столбца, и больше ничем: тот же запрос, та же обёртка, тот же ответ.
#
# Шестнадцать копий — это шестнадцать мест, где правка доедет до пятнадцати.
# Довод не гипотетический: `favorite_mail_gap` уже жил тремя разошедшимися
# копиями (`percent.py`), и разошлись они молча.
#
# Имя столбца подставляется в SQL текстом — параметром оно не бывает, — поэтому
# берётся **только из белого списка** ниже. Список закрытый, и в нём ровно то,
# что этот файл вправе трогать.

# Столбцы-выключатели: `tinyint(1)`, значение приходит булевым.
SITE_FLAGS = (
    'mailing_schedule_on', 'mailing_send_on',
    'invite_schedule_on', 'invite_send_on',
    'template_schedule_on',
    'favorite_mail_make_on', 'favorite_mail_send_on',
    'reply_make_on', 'reply_send_on', 'mail_check_on',
    'chat_answer_on', 'chat_initiative_on', 'chat_initiative_send_on',
)

# Столбцы-расписания: `json`, значение — список смен.
#
# ⚠ Четвёртое здесь — `favorite_mail_schedule`, и **своего выключателя у него
# нет**: роль «работает ли занятие» играет `favorite_mail_make_on`, галочка
# «генерация писем» в полосе подвкладок. У рассылки её зовут
# `mailing_schedule_on`, у инвайтов `invite_schedule_on` — имя другое, смысл тот
# же. Вторая галочка рядом отвечала бы на тот же вопрос.
SITE_SCHEDULE_COLUMNS = (
    'mailing_schedule', 'invite_schedule', 'template_schedule',
    'favorite_mail_schedule',
)


async def site_flag_put(site_id: int, column: str, value: bool) -> dict:
    """Пишет один выключатель сайта. Ответ — готовое тело ручки.

    Args:
        site_id: сайт-закладка. Строки может не быть — заводится вставкой.
        column: имя столбца **из `SITE_FLAGS`**.
        value: поднят или снят.

    Raises:
        AssertionError: столбца нет в белом списке. Не `HTTPException`: сюда
            попадает только имя, записанное в коде, и это ошибка кода, а не
            запроса — отвечать на неё 4xx значило бы прятать её от себя.
            Имя столбца сюда приходит только из кода этого файла.
    """
    assert column in SITE_FLAGS, f'столбец не в белом списке: {column}'

    async with mysql_get_db_async() as db:
        await db.execute(
            f'INSERT INTO site (site_id, {column}) VALUES (%s, %s)'
            f' ON DUPLICATE KEY UPDATE {column} = VALUES({column})',
            (int(site_id), 1 if value else 0))
        await db.connection.commit()

    return {'result': {'site_id': int(site_id), column: bool(value)}}


async def site_schedule_put(site_id: int, column: str, shifts) -> dict:
    """Переписывает смены сайта. Ответ — готовое тело ручки.

    Args:
        site_id: сайт-закладка.
        column: имя столбца **из `SITE_SCHEDULE_COLUMNS`**.
        shifts: список смен, как прислала страница.

    Raises:
        HTTPException: 422 — расписание негодное; причина словами, как её вернул
            `schedule_check`. Это уже про запрос, а не про код.

    ⚠ Проверка и разбор — общие с расписанием анкеты (`schedule.py`): смена там
    и здесь одно и то же, и второй набор правил разошёлся бы с первым.
    """
    assert column in SITE_SCHEDULE_COLUMNS, \
        f'столбец не в белом списке: {column}'

    why = schedule_check(shifts)
    if why:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=why)

    rows = schedule_load(shifts)

    async with mysql_get_db_async() as db:
        await db.execute(
            f'INSERT INTO site (site_id, {column}) VALUES (%s, %s)'
            f' ON DUPLICATE KEY UPDATE {column} = VALUES({column})',
            (int(site_id), schedule_dump(rows)))
        await db.connection.commit()

    return {'result': {'site_id': int(site_id), 'schedule': rows}}


class DatingSiteMailingSchedule(BaseModel):
    """Смены массовой рассылки: когда с сайта уходят письма."""

    schedule: list[dict] = Field(default_factory=list,
                                 description='Смены вида {"from": "07:00", "to": "11:00"}')


class DatingSiteMailingScheduleOn(BaseModel):
    """Гнать ли рассылку по расписанию."""

    mailing_schedule_on: bool = Field(..., description='Исполнять расписание рассылки')


class DatingSiteInviteSchedule(BaseModel):
    """Смены инвайтов: когда круг набирает пул первых реплик."""

    schedule: list[dict] = Field(default_factory=list,
                                 description='Смены вида {"from": "07:00", "to": "11:00"}')


class DatingSiteInviteScheduleOn(BaseModel):
    """Набирать ли пул инвайтов по расписанию."""

    invite_schedule_on: bool = Field(..., description='Генерация инвайтов')


class DatingSiteInviteSendOn(BaseModel):
    """Разрешена ли отправка инвайтов кнопкой."""

    invite_send_on: bool = Field(..., description='Инвайты уходят живым людям')


class DatingSiteFavoriteMailSchedule(BaseModel):
    """Смены писем избранным: когда круг набирает пул."""

    schedule: list[dict] = Field(default_factory=list,
                                 description='Смены вида {"from": "07:00", "to": "11:00"}')


class DatingSiteFavoriteMailMakeOn(BaseModel):
    """Набирать ли пул писем избранным."""

    favorite_mail_make_on: bool = Field(..., description='Генерация писем избранным')


class DatingSiteFavoriteMailSendOn(BaseModel):
    """Уходят ли письма избранным. ⚠ Исполняет круг отправки, а не кнопка."""

    favorite_mail_send_on: bool = Field(..., description='Письма уходят живым людям')


class DatingSiteChatAnswerOn(BaseModel):
    """Отвечает ли агент в чате за анкеты сайта."""

    chat_answer_on: bool = Field(..., description='Автоответ в чате: ответы уходят '
                                                  'живым людям')


class DatingSiteChatInitiativeOn(BaseModel):
    """Пишет ли девушка первой, когда разговор затих."""

    chat_initiative_on: bool = Field(..., description='Инициатива: круг пишет '
                                                      'живым людям после молчания')


class DatingSiteChatInitiativeSendOn(BaseModel):
    """Уходят ли карточки инициативы живым людям."""

    chat_initiative_send_on: bool = Field(..., description='Отправка инициативы')


class DatingSiteTemplateSchedule(BaseModel):
    """Смены, в которые агент пишет новые шаблоны."""

    schedule: list = Field(default_factory=list, description='Смены суток')


class DatingSiteTemplateScheduleOn(BaseModel):
    """Исполнять ли расписание шаблонов."""

    template_schedule_on: bool = Field(..., description='Писать шаблоны по сменам')


class DatingSiteReplySendOn(BaseModel):
    """Отвечать ли на входящие письма."""

    reply_send_on: bool = Field(..., description='Ответы уходят живым людям')


class DatingSiteReplyMakeOn(BaseModel):
    """Готовить ли ответы на входящие письма."""

    reply_make_on: bool = Field(..., description='Ответы набираются в пул')


class DatingSiteMailCheckOn(BaseModel):
    """Забирать ли входящие письма кругом."""

    mail_check_on: bool = Field(..., description='Круг ходит за письмами сам')


class DatingSiteMailingSendOn(BaseModel):
    """Отправлять ли письма из пула."""

    mailing_send_on: bool = Field(..., description='Отправлять письма из пула')


# ── Расписание массовой рассылки ─────────────────────────────────────────────
#
# ⚠ **Оно про сайт, а не про анкету.** Расписание анкеты
# (`profile_.schedule`) отвечает на вопрос «когда ей быть в сети» — это про
# присутствие конкретной учётки. Здесь вопрос другой: «когда уходит рассылка», и
# ответ на него один на весь сайт. Рассылку шлют всем, кто онлайн; своего времени
# рассылки у каждой анкеты нет, и раскладывать общее решение по двадцати учёткам
# значило бы двадцать мест, где его правят.
#
# ⚠ **Свои колонки, а не ключ в `settings`.** Настройки переписываются целиком
# (`PUT /{site_id}` выше): страница «Сайты» шлёт то, что на ней, и ключа
# `mailing_schedule` она не знает — первое же сохранение соседней настройки стёрло
# бы расписание молча.
#
# Проверка и разбор — общие с расписанием анкеты (`schedule.py`): смена там
# и здесь это одно и то же, и второй набор правил разошёлся бы с первым.


@router.get('/{site_id}/mailing-schedule', include_in_schema=False)


async def site_mailing_schedule_get(site_id: int,
                                           user: AuthUser = Depends(auth_required)):
    """Расписание рассылки сайта. Нет строки — пустое, а не 404.

    Returns:
        dict: `schedule` — смены, `mailing_schedule_on` — исполнять ли их.

    Сайт, которому рассылку не настраивали, — законное состояние: пустое
    расписание значит «шлём руками», ровно как у анкеты.
    """
    found = await site_mailing_schedule(int(site_id))

    return {'result': {'site_id': int(site_id), **found}}


@router.put('/{site_id}/mailing-schedule', include_in_schema=False)


async def site_mailing_schedule_save(
        site_id: int, payload: DatingSiteMailingSchedule,
        user: AuthUser = Depends(auth_required)):
    """Переписывает смены рассылки.

    Raises:
        HTTPException: 422 — расписание негодное; причина словами, как её вернул
            `schedule_check`.

    ⚠ **Всем вошедшим, а не только админу.** Смены рассылки — распорядок работы, а
    не устройство установки: их ставит тот, кто ведёт анкеты. Что рассылка уходит
    от всех анкет сайта разом, а не от открытой, говорит подпись над лентой.
    """
    return await site_schedule_put(site_id, 'mailing_schedule', payload.schedule)


@router.put('/{site_id}/mailing-schedule-on', include_in_schema=False)


async def site_mailing_schedule_on(
        site_id: int, payload: DatingSiteMailingScheduleOn,
        user: AuthUser = Depends(auth_required)):
    """Включает и выключает исполнение расписания рассылки.

    ⚠ **Отдельно от самих смен**, и по той же причине, что у анкеты: смены правят
    ползунком, и промежуточные состояния законны — человек двигает ручку, смены на
    секунду налезают, потом расходятся. Исполняй мы «по факту наличия», рассылка
    уходила бы на каждое движение.
    """
    return await site_flag_put(site_id, 'mailing_schedule_on', payload.mailing_schedule_on)


@router.put('/{site_id}/mailing-send-on', include_in_schema=False)


async def site_mailing_send_on(site_id: int, payload: DatingSiteMailingSendOn,
                                      user: AuthUser = Depends(auth_required)):
    """Включает и выключает отправку писем из пула.

    ⚠ **Отдельно от расписания**, хотя оба про рассылку: расписание отвечает
    «когда», этот — «вообще ли». Пул можно набивать и править при выключенной
    отправке, и это обычный порядок работы: сперва посмотреть, что вышло.

    ⚠ **Умолчание — выключено**, и включать должен человек. Письма уходят живым
    людям; накат миграции, включивший рассылку, был бы худшим из возможных
    сюрпризов.
    """
    return await site_flag_put(site_id, 'mailing_send_on', payload.mailing_send_on)


@router.get('/{site_id}/invite-schedule', include_in_schema=False)


async def site_invite_schedule_get(site_id: int,
                                          user: AuthUser = Depends(auth_required)):
    """Расписание инвайтов сайта. Нет строки — пустое, а не 404.

    Returns:
        dict: `schedule` — смены, `invite_schedule_on` — набирать ли пул по ним,
        `invite_send_on` — разрешена ли отправка кнопкой, `invite_pool_max` —
        потолок пула (по нему страница решает, показывать ли «пул полон»).

    ⚠ Все четыре одной ручкой, а не порознь: они рисуются в одной полосе
    подвкладок и читаются как одно решение — «когда готовим и можно ли слать».
    Приди ответы вразнобой, полоса успевала бы показать половину состояния.
    """
    found = await site_invite_schedule(int(site_id))

    return {'result': {'site_id': int(site_id), **found}}


@router.put('/{site_id}/invite-schedule', include_in_schema=False)


async def site_invite_schedule_save(
        site_id: int, payload: DatingSiteInviteSchedule,
        user: AuthUser = Depends(auth_required)):
    """Переписывает смены инвайтов.

    Raises:
        HTTPException: 422 — расписание негодное; причина словами.

    ⚠ **Всем вошедшим, а не только админу**: смены инвайтов — тот же распорядок
    работы, что и смены рассылки.
    """
    return await site_schedule_put(site_id, 'invite_schedule', payload.schedule)


@router.put('/{site_id}/invite-schedule-on', include_in_schema=False)


async def site_invite_schedule_on(
        site_id: int, payload: DatingSiteInviteScheduleOn,
        user: AuthUser = Depends(auth_required)):
    """Включает и выключает генерацию инвайтов по расписанию.

    ⚠ **Отдельно от самих смен**, и по той же причине, что у рассылки: смены правят
    ползунком, промежуточные состояния законны, и «исполняй по факту наличия»
    означало бы набор пула на каждое движение ручки.
    """
    return await site_flag_put(site_id, 'invite_schedule_on', payload.invite_schedule_on)


@router.put('/{site_id}/invite-send-on', include_in_schema=False)


async def site_invite_send_on(site_id: int, payload: DatingSiteInviteSendOn,
                                     user: AuthUser = Depends(auth_required)):
    """Разрешает и запрещает отправку инвайтов.

    ⚠ **Флаг включает круг** (`invite_send_round.py`), а не только отпирает
    кнопку. Он отвечает на вопрос «можно ли сейчас писать наружу с этого сайта»;
    кнопка на карточке остаётся для проверки текста руками, и её тот же флаг
    запирает. Проверяют его оба — и круг, и ручка отправки: страницу открывают в
    двух вкладках, и запрет в разметке ни от чего не защищает.

    ⚠ **Умолчание — снято.** Реплика уходит живому человеку в чат; накат миграции,
    разрешивший это сам, был бы худшим из сюрпризов.
    """
    return await site_flag_put(site_id, 'invite_send_on', payload.invite_send_on)


@router.get('/{site_id}/chat-initiative', include_in_schema=False)
async def site_chat_initiative_get(site_id: int,
                                   user: AuthUser = Depends(auth_required)):
    """Включена ли инициатива у сайта. Нет строки — выключено, а не 404."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT chat_initiative_on, chat_initiative_send_on'
                         '  FROM site WHERE site_id = %s',
                         (int(site_id),))
        row = await db.fetchone()

    return {'result': {
        'site_id': int(site_id),
        'chat_initiative_on': bool((row or {}).get('chat_initiative_on')),
        'chat_initiative_send_on':
            bool((row or {}).get('chat_initiative_send_on'))}}


@router.put('/{site_id}/chat-initiative-on', include_in_schema=False)
async def site_chat_initiative_on(site_id: int,
                                  payload: DatingSiteChatInitiativeOn,
                                  user: AuthUser = Depends(auth_required)):
    """Включает и выключает круг инициативы.

    ## ⚠⚠ Это не то же, что автоответ

    Тот разрешает **отвечать** — говорить, когда с нами заговорили. Этот разрешает
    заговорить **первыми**, после молчания, и решение тут другое: ответ человек
    ждёт, а неожиданный вопрос через час тишины он не ждал.

    ⚠ **Флаг включает круг целиком** — и набор пула, и отправку. У инвайтов их
    два, потому что там пул читают глазами: полсотни первых реплик незнакомым
    людям. Здесь по карточке на пару в уже начатый разговор, и делить занятие
    надвое значило бы просить разрешения дважды.

    ⚠ **Умолчание — снято.** Круг пишет живым людям сам; накат миграции,
    разрешивший это, был бы худшим из сюрпризов.
    """
    return await site_flag_put(site_id, 'chat_initiative_on', payload.chat_initiative_on)


@router.put('/{site_id}/chat-initiative-send-on', include_in_schema=False)
async def site_chat_initiative_send_on(
        site_id: int, payload: DatingSiteChatInitiativeSendOn,
        user: AuthUser = Depends(auth_required)):
    """Разрешает и запрещает отправку карточек инициативы.

    ⚠⚠ **Отдельно от набора, и это главное.** Тот копит карточки, этот их
    отправляет. Порядок работы: поднять набор, прочитать пул глазами, и только
    потом поднять отправку.

    Инициатива требует такого разделения сильнее прочих занятий: текст сочиняется
    под тишину, приёмом из жребия — флирт, подколка, чёрный юмор, — и увидеть, что
    именно агент понял под «подколи по-доброму», надо **до** того, как оно ушло
    живому человеку.

    ⚠ **Умолчание — снято.**
    """
    return await site_flag_put(site_id, 'chat_initiative_send_on', payload.chat_initiative_send_on)


@router.get('/{site_id}/chat-answer', include_in_schema=False)


async def site_chat_answer_get(site_id: int,
                                      user: AuthUser = Depends(auth_required)):
    """Отвечает ли агент в чате этого сайта. Нет строки — выключено, а не 404."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT chat_answer_on FROM site WHERE site_id = %s',
                         (int(site_id),))
        row = await db.fetchone()

    return {'result': {'site_id': int(site_id),
                       'chat_answer_on': bool((row or {}).get('chat_answer_on'))}}


@router.put('/{site_id}/chat-answer-on', include_in_schema=False)


async def site_chat_answer_on(site_id: int, payload: DatingSiteChatAnswerOn,
                                     user: AuthUser = Depends(auth_required)):
    """Включает и выключает автоответ в чате для всех анкет сайта.

    ⚠ **Про сайт, а не про анкету**, и это третий такой переезд подряд: так же
    ушли на сайт генерация ответов на письма и отправка. Разложенный по учёткам
    флаг означал сорок включений, а забытая анкета молчала — и причину искали в
    поломке (миграция `20260911230000_dating_site_chat_answer_on.sql`).

    ⚠ **Умолчание — снято.** Ответ уходит живому человеку в чат от имени девушки;
    накат, разрешивший это сам, был бы худшим из сюрпризов. Прежние решения при
    этом перенесены: сайт, где автоответ хоть одной анкете включали, остался
    включённым.
    """
    return await site_flag_put(site_id, 'chat_answer_on', payload.chat_answer_on)


@router.get('/{site_id}/panel', include_in_schema=False)


async def site_panel_get(site_id: int,
                                user: AuthUser = Depends(auth_required)):
    """Админ-панель сайта: куда ведёт ссылка и жива ли сессия.

    Returns:
        dict: `on` — задан ли адрес (пусто — панели у сайта нет), `url`, `name`,
        `status` (`online` | `offline` | `error` | пусто), `checked_at`,
        `login_at`, `error`.

    ⚠ **Ручка только читает.** Она стоит в шапке каждой страницы раздела и
    спрашивается на каждое переключение сайта; ходи она за этим на саму панель,
    щелчок по селекту означал бы запрос к чужому серверу. Спрашивает панель круг
    (`panel_round.py`), здесь — его последний ответ.
    """
    from src.dating.panel import panel_show

    return {'result': {'site_id': int(site_id), **await panel_show(site_id)}}


@router.post('/{site_id}/panel/login', include_in_schema=False)


async def site_panel_login(site_id: int,
                                  user: AuthUser = Depends(auth_admin_required)):
    """Войти в панель сейчас — сценарием у соседа, не дожидаясь круга.

    ⚠ **Админу**, потому что это вход под учёткой агентства: действие на чужом
    сервере от нашего имени, а не чтение.

    Returns:
        dict: исход входа и свежий ответ панели — тот же вид, что у чтения.
    """
    from src.dating.panel import (panel_check, panel_login,
                                         panel_show)

    login = await panel_login(site_id)
    if login['status'] == 'ok':
        await panel_check(site_id)

    return {'result': {'site_id': int(site_id), 'login': login,
                       **await panel_show(site_id)}}


@router.get('/{site_id}/favorite-mail', include_in_schema=False)


async def site_favorite_mail_get(site_id: int,
                                        user: AuthUser = Depends(auth_required)):
    """Настройки переписки с избранными: оба выключателя и оба предела частоты.

    Returns:
        dict: `favorite_mail_make_on`, `favorite_mail_send_on`,
        `favorite_mail_per_day`, `favorite_mail_gap`.

    ⚠ **Одной ручкой, а не четырьмя.** Подвкладка показывает их одним блоком, и
    четыре запроса на её открытие дали бы четыре момента времени там, где нужен
    один. Тот же довод, что у расписания шаблонов с числом в сутки.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT favorite_mail_make_on, favorite_mail_send_on,'
            ' favorite_mail_schedule FROM site'
            ' WHERE site_id = %s', (int(site_id),))
        row = await db.fetchone()

    return {'result': {
        'site_id': int(site_id),
        'favorite_mail_make_on': bool((row or {}).get('favorite_mail_make_on')),
        'favorite_mail_send_on': bool((row or {}).get('favorite_mail_send_on')),
        # ⚠ Расписание — **той же ручкой**, а не своей. Подвкладка рисует смены и
        # выключатель одной полосой, и два запроса дали бы два момента времени:
        # человек увидел бы зелёную смену под снятой галочкой.
        'schedule': schedule_load((row or {}).get('favorite_mail_schedule')),
        'favorite_mail_per_day': await site_seconds(site_id,
                                                           'favorite_mail_per_day'),
        'favorite_mail_gap': await site_seconds(site_id, 'favorite_mail_gap'),
        # Потолок пула — странице, для счётчика «сколько из скольких». Без него
        # число в шапке отвечало бы только на половину вопроса: «17» не говорит,
        # много это или пора останавливаться.
        'favorite_mail_pool_max': await site_seconds(
            site_id, 'favorite_mail_pool_max')}}


@router.put('/{site_id}/favorite-mail-schedule', include_in_schema=False)


async def site_favorite_mail_schedule_save(
        site_id: int, payload: DatingSiteFavoriteMailSchedule,
        user: AuthUser = Depends(auth_required)):
    """Переписывает смены писем избранным.

    Raises:
        HTTPException: 422 — расписание негодное; причина словами.

    ⚠ **Своего выключателя у этих смен нет**, в отличие от рассылки и инвайтов:
    роль «работает ли занятие» играет `favorite_mail_make_on` — та самая галочка
    «генерация писем» в полосе подвкладок. Две галочки рядом отвечали бы на один
    вопрос, и человек гадал бы, какая из них главная.

    ⚠ **Всем вошедшим, а не только админу**: смены — распорядок работы, как у
    рассылки и инвайтов.
    """
    return await site_schedule_put(site_id, 'favorite_mail_schedule',
                                   payload.schedule)


@router.put('/{site_id}/favorite-mail-make-on', include_in_schema=False)


async def site_favorite_mail_make_on(
        site_id: int, payload: DatingSiteFavoriteMailMakeOn,
        user: AuthUser = Depends(auth_required)):
    """Включает и выключает набор пула писем избранным.

    ⚠ **Он же — выключатель расписания**: своей галочки у смен нет. У рассылки
    эту роль играет `mailing_schedule_on`, у инвайтов `invite_schedule_on` — имя
    другое, смысл тот же.

    ⚠⚠ **Снят — круг молчит совсем. Поднят, а смен нет — тоже молчит.** Пустое
    расписание значит «не работаем», а не «работаем всегда»: правило одно на
    проект (`mailing_round`, `invite_round`), и второе прочтение здесь означало
    бы, что человек, стерев все смены, получает круглосуточную работу.

    ⚠ Отправку эти смены **не** задают: письмо уносит круг отправки в часы
    `mailing_schedule`, общие на всю почту сайта. Здесь — только «когда
    сочиняем».
    """
    return await site_flag_put(site_id, 'favorite_mail_make_on', payload.favorite_mail_make_on)


@router.put('/{site_id}/favorite-mail-send-on', include_in_schema=False)


async def site_favorite_mail_send_on(
        site_id: int, payload: DatingSiteFavoriteMailSendOn,
        user: AuthUser = Depends(auth_required)):
    """Разрешает и запрещает отправку писем избранным.

    ⚠ **Этот флаг исполняет круг отправки**, в отличие от инвайтского: письма
    уходят сами, по одному, с паузой и потолком в сутки (`send_round.py`).
    Тем важнее его умолчание — снят: письмо уходит живому человеку от имени
    девушки, и накат миграции, разрешивший это сам, был бы худшим из сюрпризов.
    """
    return await site_flag_put(site_id, 'favorite_mail_send_on', payload.favorite_mail_send_on)


@router.get('/{site_id}/template-schedule', include_in_schema=False)


async def site_template_schedule_get(site_id: int,
                                            user: AuthUser = Depends(auth_required)):
    """Расписание написания шаблонов. Нет строки — пустое, а не 404.

    Returns:
        dict: `schedule` — смены, `template_schedule_on` — исполнять ли их,
        `template_per_day` — сколько заготовок в сутки (настройка сайта).

    ⚠ Число в сутки приходит **здесь же**: страница показывает расписание и селект
    одной строкой, и второй запрос ради одного числа был бы лишним.
    """
    found = await site_template_schedule(int(site_id))

    return {'result': {'site_id': int(site_id), **found}}


@router.put('/{site_id}/template-schedule', include_in_schema=False)


async def site_template_schedule_save(
        site_id: int, payload: DatingSiteTemplateSchedule,
        user: AuthUser = Depends(auth_required)):
    """Переписывает смены написания шаблонов.

    ⚠ Разбор и проверка смен — **общие** с расписанием анкеты и рассылки
    (`schedule.py`): «смена» здесь и там одно и то же, и третий набор правил
    разошёлся бы с первыми двумя.

    ## ⚠⚠ И расходился: проверки здесь не было

    Докстринг обещал общие правила, а код звал только разбор — `schedule_check`
    стоял у рассылки и инвайтов, но не здесь. Негодные смены этот путь принимал
    молча: `schedule_load` выбрасывает непонятое и возвращает, что разобрал, —
    то есть человек сохранял расписание и получал «сохранено», а уезжала его
    половина.

    Расхождение нашлось само, когда три одинаковые записи свелись к одной.
    Это и есть довод против копий: разницу между ними никто не замечает, пока
    они лежат порознь.
    """
    return await site_schedule_put(site_id, 'template_schedule',
                                   payload.schedule)


@router.put('/{site_id}/template-schedule-on', include_in_schema=False)


async def site_template_schedule_on(
        site_id: int, payload: DatingSiteTemplateScheduleOn,
        user: AuthUser = Depends(auth_required)):
    """Включает и выключает написание шаблонов по расписанию.

    ⚠ Отдельно от самих смен — та же причина, что у рассылки: смены правят
    ползунком, промежуточные состояния законны, и «по факту наличия» агент
    запускался бы на каждое движение ручки.
    """
    return await site_flag_put(site_id, 'template_schedule_on', payload.template_schedule_on)


@router.get('/{site_id}/reply', include_in_schema=False)


async def site_reply_get(site_id: int,
                                user: AuthUser = Depends(auth_required)):
    """Отвечаем ли на входящие письма этого сайта.

    Своей ручкой, а не полем в `/mailing-schedule`: та про рассылку — «когда шлём
    первыми», а это про ответы. Сложи мы их в один ответ ради экономии запроса, имя
    ручки перестало бы говорить, что в ней лежит.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT reply_send_on, reply_make_on FROM site'
                         ' WHERE site_id = %s', (int(site_id),))
        row = await db.fetchone()

    # ⚠ Оба флага одним ответом, и это не экономия запроса. Они **пара** —
    # «готовим» и «шлём», — и стоят на странице рядом; приди они порознь, панель
    # успевала бы показать один включённым, а второй ещё нет.
    return {'result': {'site_id': int(site_id),
                       'reply_send_on': bool((row or {}).get('reply_send_on')),
                       'reply_make_on': bool((row or {}).get('reply_make_on'))}}


@router.get('/{site_id}/mail-in', include_in_schema=False)


async def site_mail_in_get(site_id: int,
                                  user: AuthUser = Depends(auth_required)):
    """Забираем ли входящие письма этого сайта.

    Своей ручкой, а не полем в `/reply`: та про ответы — «что делаем с письмом,
    когда оно пришло», а это про то, придёт ли оно вообще. Сложи мы их в один
    ответ ради экономии запроса, имя ручки перестало бы говорить, что в ней лежит.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT mail_check_on FROM site'
                         ' WHERE site_id = %s', (int(site_id),))
        row = await db.fetchone()

    # ⚠ Умолчание **включено**, и это важно именно здесь: сайта может не быть в
    # снимке вовсе (`row is None`), и ответить «выключено» значило бы показать
    # снятую галочку там, где круг на самом деле пойдёт.
    return {'result': {'site_id': int(site_id),
                       'mail_check_on': bool((row or {}).get('mail_check_on', 1))}}


@router.put('/{site_id}/mail-check-on', include_in_schema=False)


async def site_mail_check_on(site_id: int, payload: DatingSiteMailCheckOn,
                                    user: AuthUser = Depends(auth_required)):
    """Включает и выключает забор входящих писем.

    ⚠ **Включено по умолчанию**, в отличие от трёх соседних выключателей почты.
    Разница не в смелости, а в том, куда направлено действие: те **пишут наружу**,
    а этот только читает. Забор ничего не отправляет, никому не показывает анкету
    и не делает её онлайн — он приносит в панель то, что человеку и так
    адресовано. Выключенный по умолчанию, он означал бы панель, которая по
    умолчанию не показывает почту.

    ⚠ **Первый в цепочке.** Сняв его, человек останавливает и ответы, и отправку:
    отвечать станет не на что. Поэтому подпись на странице говорит о круге, а не о
    галочке.
    """
    return await site_flag_put(site_id, 'mail_check_on', payload.mail_check_on)


@router.put('/{site_id}/reply-make-on', include_in_schema=False)


async def site_reply_make_on(site_id: int, payload: DatingSiteReplyMakeOn,
                                    user: AuthUser = Depends(auth_required)):
    """Включает и выключает **подготовку** ответов на входящие письма.

    ⚠ **Не отправку.** Пул можно копить и править молча — уходят ответы только со
    вторым выключателем (`reply-send-on`). Пара «готовим / шлём» та же, что у
    рассылки, и по той же причине: набор стоит вызовов LLM, отправка — репутации.

    ⚠ **Флаг у сайта, а не у анкеты**, и это переезд (миграция
    `20260910120000_dating_site_reply_make_on.sql`). Прежде решение принималось по
    анкете, но включать его приходилось сорок раз, а забытая молчала — причину
    искали в поломке. Пул ответов один на сайт, и вопрос «отвечаем ли вообще»
    задаётся один раз.
    """
    return await site_flag_put(site_id, 'reply_make_on', payload.reply_make_on)


@router.put('/{site_id}/reply-send-on', include_in_schema=False)


async def site_reply_send_on(site_id: int, payload: DatingSiteReplySendOn,
                                    user: AuthUser = Depends(auth_required)):
    """Включает и выключает ответы на входящие письма.

    ⚠ **Выключено по умолчанию**, и включает человек. Ответ уходит живому человеку
    от имени девушки; та же причина, по какой выключена отправка рассылки.

    ⚠ **Флаг у сайта, а не у анкеты.** Пул ответов один на сайт: письма приходят
    разным девушкам, а решение «отвечаем ли вообще» принимается один раз. Разложи мы
    его по анкетам — включать пришлось бы сорок раз, а забытая молчала бы, и причину
    искали бы в поломке.
    """
    return await site_flag_put(site_id, 'reply_send_on', payload.reply_send_on)


async def site_template_schedule(site_id: int) -> dict:
    """Расписание шаблонов сайта — для страницы и для круга.

    Returns:
        dict: `schedule`, `template_schedule_on`, `template_per_day`.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT template_schedule, template_schedule_on'
            ' FROM site WHERE site_id = %s', (int(site_id),))
        row = await db.fetchone()

    per_day = await site_seconds(site_id, 'template_per_day')
    # ⚠ Срок жизни приходит здесь же: список шаблонов подписывает каждой строке,
    # сколько ей осталось, и второй запрос ради одного числа был бы лишним.
    keep = await site_seconds(site_id, 'template_keep')

    if not row:
        return {'schedule': [], 'template_schedule_on': False,
                'template_per_day': per_day, 'template_keep': keep}

    return {'schedule': schedule_load(row['template_schedule']),
            'template_schedule_on': bool(row['template_schedule_on']),
            'template_per_day': per_day,
            'template_keep': keep}


async def site_mailing_schedule(site_id: int) -> dict:
    """Расписание рассылки сайта — для кода, которому оно понадобится.

    Returns:
        dict: `schedule` (список смен) и `mailing_schedule_on` (исполнять ли).

    Отдельной функцией, а не запросом на месте: расписание понадобится и странице,
    и кругу рассылки, когда тот появится, — а разбор JSON, размазанный по коду,
    разойдётся в мелочах.

    ⚠ Читает своим запросом, а не через `_settings_all`: тот берёт только колонку
    `settings`, и класть сюда расписание значило бы тащить его в каждый показ
    списка сайтов, где оно не нужно.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT mailing_schedule, mailing_schedule_on, mailing_send_on'
            ' FROM site WHERE site_id = %s', (int(site_id),))
        row = await db.fetchone()

    if not row:
        return {'schedule': [], 'mailing_schedule_on': False, 'mailing_send_on': False,
                'mailing_pool_max': await site_seconds(site_id,
                                                              'mailing_pool_max')}

    return {'schedule': schedule_load(row['mailing_schedule']),
            'mailing_schedule_on': bool(row['mailing_schedule_on']),
            # Отправка приходит здесь же: страница показывает оба выключателя на
            # одной вкладке, и второй запрос ради одного поля был бы лишним.
            'mailing_send_on': bool(row['mailing_send_on']),
            # ⚠ И потолок пула — тоже здесь. По нему страница решает, показывать ли
            # загрузчик: пул полон — набирать нечего, и крутилка врала бы.
            'mailing_pool_max': await site_seconds(site_id, 'mailing_pool_max')}


async def site_invite_schedule(site_id: int) -> dict:
    """Расписание инвайтов сайта и оба его выключателя.

    Returns:
        dict: `schedule`, `invite_schedule_on`, `invite_send_on`,
        `invite_pool_max`.

    Устроено так же, как у рассылки, и по тем же причинам: отдельной функцией,
    своим запросом (в `_settings_all` лежит только колонка `settings`), и все поля
    разом — они рисуются одной полосой и читаются как одно решение.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT invite_schedule, invite_schedule_on, invite_send_on'
            ' FROM site WHERE site_id = %s', (int(site_id),))
        row = await db.fetchone()

    pool_max = await site_seconds(site_id, 'invite_pool_max')

    if not row:
        return {'schedule': [], 'invite_schedule_on': False,
                'invite_send_on': False, 'invite_pool_max': pool_max}

    return {'schedule': schedule_load(row['invite_schedule']),
            'invite_schedule_on': bool(row['invite_schedule_on']),
            'invite_send_on': bool(row['invite_send_on']),
            'invite_pool_max': pool_max}
