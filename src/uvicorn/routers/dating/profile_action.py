"""Действия над учёткой: вход, проверка, отправка, опрос, автоответ.

Отдельно от CRUD (`profile_.py`) по одному признаку: **всё здесь ходит
наружу** — к сайту знакомств или к соседу за браузером и агентом. Отсюда и
свойства, которых нет у правки логина: занимает секунды, а вход и проверка —
минуты; падает от чужой недоступности (502); почти всегда пишет строку в журнал
учётки, потому что результат нужен человеку, который нажал кнопку.

«Залогиниться» — не отдельная механика, а прогон сценария входа этого сайта
(`browser_scenario`) в живой сессии: логин и пароль уходят туда `secrets`, то
есть мимо базы и журнала прогонов. Сценарий ищется по подстановкам, которые он
ждёт: тот, кому нужны и логин, и пароль, и есть вход.

Сам вход здесь не написан, а вызван: он живёт в `src/dating/login.py`,
потому что входит не только человек кнопкой — смена держателя сокетов входит
сама, когда сессия сайта протухла. Ручка отвечает лишь за коды ответа. То же и с
остальным: проверка в `check.py`, отправка в `send.py`, ответ агента
в `answer.py`. Здесь — HTTP-обвязка над ними.

Роутер тот же (`/api/dating/profile`), и это не случайность: снаружи ручки анкеты
остаются одной группой. Разделение — про то, где лежит код, а не про то, как он
виден клиенту.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_agent_find,
                                       rhapsody_api_proxy_list,
                                       rhapsody_api_error_write,
                                       rhapsody_api_session_open)
from src.dating.agent import agent_ensure
from src.dating.answer import answer_run
from src.dating.scenario import scenario_run
from src.dating.chat import chat_listen
from src.dating.check import check_run
from src.dating.dialog import dialog_sync
from src.dating.disconnect import disconnect_run
from src.dating.json_column import json_column_load
from src.dating.schedule import (schedule_check, schedule_dump,
                                        schedule_load)
from src.dating.schedule_round import schedule_round_drop
from src.dating.log import log_write
from src.dating.login import (DatingLoginError, login_run,
                                     login_session_all, login_session_close,
                                     login_session_close_old, login_ws_capture)
from src.dating.presence import presence_probe, presence_ready
from src.dating.status import status_probe_site, status_write
from src.dating.profile_cache import profile_cache_refresh
from src.dating.proxy import (proxy_countries, proxy_forget,
                                     proxy_seats, proxy_set)
from src.dating.send import send_message
from src.uvicorn.routers.dating.profile_ import profile_row, profile_site

router = APIRouter(prefix='/api/dating/profile', tags=['dating'])

class DatingProfileLogin(BaseModel):
    """Вход под учёткой: в какой сессии и каким сценарием."""

    session_id: str = Field('', description='Пусто — заведётся своя сессия на адрес сайта')
    scenario_id: int | None = Field(None, description='Пусто — сценарий входа ищется сам')


class DatingProfileSend(BaseModel):
    """Ручная отправка сообщения собеседнику."""

    to: str = Field(..., min_length=1, max_length=32, description='Номер собеседника на сайте')
    text: str = Field(..., min_length=1, max_length=2000)


class DatingProfileAuto(BaseModel):
    """Переключатель автоответа у анкеты."""

    auto_answer: bool = Field(..., description='Отвечать ли самостоятельно')
    auto_answer_only: str | None = Field(
        None, max_length=500,
        description='Кому отвечать: номера через запятую. Пусто — всем; не передано — как было')


class DatingProfilePassword(BaseModel):
    """Откуда у анкеты пароль и, если свой, какой."""

    password_source: str = Field(..., description='`site` — общий агентский, `own` — свой')
    password: str = Field('', max_length=255,
                          description='Свой пароль; пусто — оставить прежний')


@router.put('/{profile_id}/password', include_in_schema=False)
async def profile_action_password(profile_id: int, data: DatingProfilePassword,
                                         user=Depends(auth_required)):
    """Меняет источник пароля и сам пароль анкеты.

    ⚠ **Пустой пароль означает «оставить прежний», а не «стереть».** Форма его не
    показывает (в поле маска), и по пустому полю не отличить «не трогал» от
    «хочу стереть». Отсюда правило: стирается пароль только сменой источника на
    общий — там он и так перестаёт использоваться.

    ⚠ **Своё поле при переходе на общий не чистим.** Человек переключается туда и
    обратно, проверяя, каким паролем анкета войдёт; стёртое поле означало бы, что
    обратный переход требует вписать пароль заново — а он его может и не помнить.
    """
    profile = await profile_row(profile_id)

    source = str(data.password_source or '').strip()
    if source not in ('site', 'own'):
        raise HTTPException(status_code=422, detail='источник пароля: `site` или `own`')

    password = str(data.password or '')

    async with mysql_get_db_async() as db:
        if password:
            await db.execute('UPDATE profile SET password_source = %s, password = %s '
                             ' WHERE id = %s', (source, password, profile_id))
        else:
            await db.execute('UPDATE profile SET password_source = %s WHERE id = %s',
                             (source, profile_id))
        await db.connection.commit()

    await log_write(
        profile_id, 'info',
        ('Пароль: свой' if source == 'own' else 'Пароль: общий агентский из настроек сайта')
        + (', значение изменено' if password else ''))

    row = await profile_row(profile_id)

    return {'result': {'password_source': source,
                       'has_password': bool(str(row.get('password') or ''))}}


class DatingProfileProxy(BaseModel):
    """Какой выход наружу у анкеты."""

    proxy_id: int = Field(..., ge=0,
                          description='Номер выхода в реестре соседа; 0 — выбрать самим')


@router.put('/{profile_id}/proxy', include_in_schema=False)
async def profile_action_proxy(profile_id: int, data: DatingProfileProxy,
                                      user=Depends(auth_required)):
    """Ставит анкете выход наружу — руками.

    Обычно выход достаётся сам: первая же дверь, собравшаяся к сайту, берёт
    свободный адрес (`proxy.py`). Руками его меняют, когда у человека есть
    знание, которого у раздачи нет: этот канал быстрее, тот из нужной страны, а
    вот этим анкета уже входила месяц назад и сайт её с него помнит.

    `proxy_id = 0` — «выбирай сам»: кэш чистится, и следующая же дверь возьмёт
    выход по общему правилу.

    ⚠ **Предел «анкет на IP» здесь не проверяется, и это намеренно.** Раздача его
    соблюдает, а человек — решает: он мог узнать, что сайт терпит больше, или
    сажает вторую анкету на адрес осознанно и ненадолго. Молча отказать тому, кто
    выбрал вручную, значило бы спорить с ним настройкой, которую он же и ставил.
    Сколько анкет уже сидит за этим адресом, ответ показывает — пусть видит.

    ⚠ **Смена выхода у живой анкеты видна сайту.** Он помнит, с какого адреса она
    входила, и переезд выглядит подозрительнее паузы. Поэтому действие ручное и
    остаётся в журнале анкеты.

    ⚠ **Страна не запрещается, но называется.** Отбор стран человек ставил сам,
    чипсами; выбрать вручную канал из другой он может осознанно. Но держаться
    такой выбор будет лишь до следующего перезахвата: раздача фильтрует по
    стране и переселит анкету обратно — ответ говорит об этом заранее, иначе это
    выглядит как «панель сама всё меняет».
    """
    profile = await profile_row(profile_id)
    await _proxy_on(profile)
    proxy_id = int(data.proxy_id or 0)

    if not proxy_id:
        await proxy_forget(profile_id)
        await log_write(profile_id, 'info',
                               'Выход наружу: выбирается автоматически')

        return {'result': {'proxy_id': 0, 'proxy_ip': '', 'neighbours': 0}}

    rows = await rhapsody_api_proxy_list(ready=False)
    got = next((row for row in rows if int(row['id']) == proxy_id), None)

    if not got:
        raise HTTPException(status_code=404, detail=f'выхода #{proxy_id} нет в реестре')
    if not got.get('check_ok') or not got.get('check_ip'):
        # ⚠ Непроверенный выход поставить нельзя даже руками: неизвестно, каким
        # адресом он виден, а значит и считать его не с чем — ни нам, ни сайту.
        raise HTTPException(status_code=409,
                            detail=f'выход «{got["name"]}» не проверен: нажмите «Проверить» '
                                   f'на странице «Браузер → Прокси» у соседа')
    if not got.get('is_enabled'):
        raise HTTPException(status_code=409, detail=f'выход «{got["name"]}» выключен')

    await proxy_set(profile_id, got)

    async with mysql_get_db_async() as db:
        await db.execute("SELECT COUNT(*) AS n FROM profile "
                         " WHERE proxy_ip = %s AND id <> %s",
                         (str(got['check_ip']), profile_id))
        neighbours = int(((await db.fetchone()) or {}).get('n') or 0)

    # ⚠ **Страну при ручном выборе не запрещаем, но говорим о ней.** Отбор стран
    # человек ставил сам, чипсами, — и поставить вручную канал из другой страны
    # он может осознанно («на разок, проверить»). А может и промахнуться: в
    # списке видны все каналы, и турецкий стоит рядом с украинским.
    #
    # Молчать тут нельзя: следующий круг переселит анкету обратно (раздача
    # фильтрует по стране), и выглядеть это будет как «панель сама всё меняет».
    countries = await proxy_countries(int(profile.get('site_id') or 0))
    country = str(got.get('check_country') or '')
    aside = bool(countries) and country not in countries

    await log_write(profile_id, 'info',
                           f'Выход наружу выбран вручную: «{got["name"]}» ({got["check_ip"]})'
                           + (f' — страна {country or "неизвестна"} вне отбора сайта'
                              if aside else ''))

    return {'result': {'proxy_id': proxy_id, 'proxy_ip': got['check_ip'],
                       'name': got['name'], 'neighbours': neighbours,
                       'country': country,
                       # Страница скажет это человеку словами: выбор принят, но
                       # держаться он будет лишь до следующего перезахвата.
                       'country_aside': aside}}


@router.get('/{profile_id}/proxy', include_in_schema=False)
async def profile_action_proxy_list(profile_id: int, user=Depends(auth_required)):
    """Выходы, из которых можно выбрать, — с занятостью **нашими** анкетами.

    Занятость считает эта ручка, а не реестр: он про наши анкеты не знает.
    Считается по увиденному адресу — два выхода одного пула делят предел сайта,
    и человек, выбирающий руками, должен это видеть.

    ⚠ Пароль канала в ответ не идёт: страница его не показывает и не должна.
    """
    profile = await profile_row(profile_id)
    # ⚠ До похода к соседу: у сайта без выходов выбирать не из чего, и спрашивать
    # ради этого чужой сервер незачем.
    await _proxy_on(profile)

    try:
        rows = await rhapsody_api_proxy_list(ready=False)
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=f'реестр выходов не ответил: {e}')

    async with mysql_get_db_async() as db:
        await db.execute("SELECT proxy_ip, COUNT(*) AS n FROM profile "
                         " WHERE proxy_ip <> '' AND id <> %s GROUP BY proxy_ip",
                         (profile_id,))
        busy = {str(row['proxy_ip']): int(row['n']) for row in await db.fetchall() or []}

    seats = await proxy_seats(int(profile.get('site_id') or 0))

    return {'result': [{'id': int(row['id']), 'name': row['name'],
                        'check_ip': row.get('check_ip') or '',
                        'check_ok': bool(row.get('check_ok')),
                        'is_enabled': bool(row.get('is_enabled')),
                        'taken': busy.get(str(row.get('check_ip') or ''), 0)}
                       for row in rows],
            'seats': seats, 'current': int(profile.get('proxy_id') or 0)}
class DatingProfileSchedule(BaseModel):
    """Расписание анкеты: смены суток целиком."""

    schedule: list[dict] = Field(default_factory=list,
                                 description='Смены: [{"from": "07:00", "to": "11:00"}]')


@router.put('/{profile_id}/schedule', include_in_schema=False)
async def profile_action_schedule(profile_id: int, data: DatingProfileSchedule,
                                         user=Depends(auth_required)):
    """Сохраняет расписание анкеты — целиком, а не по смене.

    Страница держит расписание перед глазами и шлёт то, что на ней. Правка по одной
    смене означала бы, что удаление — отдельная ручка, а порядок двух правок подряд
    начинает значить.

    ⚠ **Негодное расписание отклоняется, а не правится молча.** Смены внахлёст —
    это вход поверх входа: анкета получит вторую сессию, не закрыв первую. Сказать
    об этом человеку полезнее, чем тихо сдвинуть его время.
    """
    await profile_row(profile_id)

    why = schedule_check(data.schedule)
    if why:
        raise HTTPException(status_code=422, detail=why)

    rows = schedule_load(data.schedule)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET schedule = %s WHERE id = %s',
                         (schedule_dump(rows), profile_id))
        await db.connection.commit()

    await log_write(
        profile_id, 'info',
        'Расписание: ' + (', '.join(f"{r['from']}–{r['to']}" for r in rows)
                          if rows else 'снято, анкету ведут руками'))

    return {'result': {'schedule': rows}}


class DatingProfileScheduleOn(BaseModel):
    """Исполнять ли расписание: воркер входит и выходит по сменам."""

    schedule_on: bool = Field(..., description='Включить исполнение расписания')


@router.put('/{profile_id}/schedule-on', include_in_schema=False)
async def profile_action_schedule_on(profile_id: int, data: DatingProfileScheduleOn,
                                            user=Depends(auth_required)):
    """Включает или выключает исполнение расписания.

    ⚠ **Отдельно от самого расписания** (`PUT /{id}/schedule`): смены правят
    ползунком, и промежуточные состояния законны — человек двигает ручку, смены на
    секунду налезают, потом расходятся. Исполняй мы «по факту наличия смен», воркер
    входил бы и выходил на каждое движение.

    ⚠ **Выключение рвёт сокет немедленно**, не дожидаясь конца смены: человек снял
    галочку, чтобы анкета перестала работать сейчас. Рвёт круг воркера в течение
    минуты — здесь только флаг, потому что соединение живёт в его памяти, а не в
    базе.

    ⚠ **Выключение снимает и присмотр автомата** (`keep_online`), и без этого две
    наши же части дрались. Поймано живьём: галочку сняли в 16:33, сессия стёрлась
    («Расписание выключено, сокет снят»), а через минуту круг восстановления вошёл
    заново — «Вхожу заново (попытка 1)», «Вход восстановлен» — и анкета снова
    оказалась в сети. Он берёт всех с `keep_online = 1`, а анкету без расписания
    считает дежурящей круглосуточно (`revive_db._on_duty`).

    Смысл галочки — «перестань водить её сама». Присмотр, переживший снятие,
    отменял это решение молча. Понадобится ручное ведение — человек нажмёт
    «Войти», и удачный вход поднимет `keep_online` обратно (`login.py`).
    """
    await profile_row(profile_id)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET schedule_on = %s WHERE id = %s',
                         (1 if data.schedule_on else 0, profile_id))
        await db.connection.commit()

    # ⚠ **Сокет рвём здесь, а не в круге воркера.** Ручка знает, что переход
    # случился; круг знает только текущее состояние — и, отпуская всё без галочки,
    # он выкидывал бы анкеты, залогиненные **вручную**. Проверено на живой базе:
    # так и вышло, семь ручных входов сброшено.
    #
    # Заодно это быстрее: мгновенно вместо «в течение минуты».
    dropped = False
    if not data.schedule_on:
        dropped = await schedule_round_drop(profile_id)
        await _keep_online_clear(profile_id)

    await log_write(
        profile_id, 'info',
        'Расписание включено: воркер поведёт по сменам' if data.schedule_on
        else ('Расписание выключено, сокет снят' if dropped
              else 'Расписание выключено'))

    return {'result': {'schedule_on': data.schedule_on, 'dropped': dropped}}
class DatingProfileEnabled(BaseModel):
    """Выключатель анкеты целиком."""

    enabled: bool = Field(..., description='Ведём ли анкету: выключенную воркер не трогает')


@router.put('/{profile_id}/enabled', include_in_schema=False)
async def profile_action_enabled(profile_id: int, data: DatingProfileEnabled,
                                        user=Depends(auth_required)):
    """Включает или выключает анкету целиком.

    ⚠ **Не то же, что автоответ, и отдельной ручкой поэтому.** `auto_answer` — «кто
    отвечает, агент или человек»; здесь — «ведём ли анкету вовсе». Выключенная не
    пишет никому и ничем: ни автоответ, ни круг воркера, ни кнопка в окне переписки.

    Входящие при этом принимаются: держатель сокета выключенную анкету не бросает,
    переписка копится, и включив её обратно, человек видит, что писали без него.

    Ничего не разбирает по включении — в отличие от автоответа. Включение анкеты
    само по себе не поручение отвечать: за это отвечает свой флаг, и если он снят,
    анкету ведут руками.
    """
    await profile_row(profile_id)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET enabled = %s WHERE id = %s',
                         (1 if data.enabled else 0, profile_id))
        await db.connection.commit()

    await log_write(profile_id, 'info',
                           'Анкета включена' if data.enabled
                           else 'Анкета выключена: воркер её не трогает')

    return {'result': {'enabled': data.enabled}}


class DatingProfileMailing(BaseModel):
    """Рассылка писем: флаг задела, исполнителя пока нет."""

    mailing_on: bool = Field(..., description='Слать ли от анкеты рассылку писем')


class DatingProfileReply(BaseModel):
    """Готовим ли ответы на входящие письма этой анкеты."""

    reply_on: bool = Field(..., description='Генерация ответов в пул')


@router.put('/{profile_id}/reply', include_in_schema=False)
async def profile_action_reply(profile_id: int, data: DatingProfileReply,
                                      user=Depends(auth_required)):
    """Включает или выключает **генерацию ответов** на входящие письма.

    ⚠ **Это не `schedule_on`, и путать их нельзя.** Тот выключатель — на «Анкетах»,
    и он включает вход по сменам: воркер логинит девушку и держит сокет. Здесь речь
    только о том, готовить ли ответы в пул; наружу от этого ничего не уходит —
    отправка это третий, свой выключатель (`reply_send_on` у сайта).

    Галочка стояла в блоке расписания и писала в `schedule_on`, то есть человек,
    разрешая готовить ответы, отправлял анкету в сеть. Вход — действие наружу, самое
    заметное из всех, и побочным следствием чужого решения он быть не должен.

    ⚠ **Пара к `mailing_on`.** Тот — «участвует ли анкета в рассылке», этот —
    «готовим ли ответы от её имени». Оба про то, что панель делает от лица девушки, и
    оба выключены по умолчанию.
    """
    await profile_row(profile_id)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET reply_on = %s WHERE id = %s',
                         (1 if data.reply_on else 0, profile_id))
        await db.connection.commit()

    await log_write(profile_id, 'info',
                           'Генерация ответов включена' if data.reply_on
                           else 'Генерация ответов выключена')

    return {'result': {'reply_on': data.reply_on}}


@router.put('/{profile_id}/mailing', include_in_schema=False)
async def profile_action_mailing(profile_id: int, data: DatingProfileMailing,
                                        user=Depends(auth_required)):
    """Включает или выключает рассылку писем.

    ⚠ **Исполнителя у флага нет, и это не забытый баг.** Круга рассылки в проекте
    не существует: ручка хранит решение человека, чтобы оно не пропадало до того
    дня, когда рассылку напишут. Колонку никто не читает — ни воркер, ни отправка.

    ⚠ **Третий выключатель анкеты, и он про своё.** `enabled` — «ведём ли анкету
    вовсе», `auto_answer` — «отвечает ли за неё агент в чате», здесь — «шлём ли от
    неё рассылку писем». Состояния «отвечаем в чате, но не рассылаем» и «ведём
    руками, а рассылка идёт» законны, и каждое достижимо только своим флагом.

    Побочных действий нет — в отличие от `/auto`, где включение сразу разбирает
    накопившееся. Разбирать здесь нечего.
    """
    await profile_row(profile_id)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET mailing_on = %s WHERE id = %s',
                         (1 if data.mailing_on else 0, profile_id))
        await db.connection.commit()

    await log_write(profile_id, 'info',
                           'Рассылка писем включена' if data.mailing_on
                           else 'Рассылка писем выключена')

    return {'result': {'mailing_on': data.mailing_on}}


@router.post('/{profile_id}/login', include_in_schema=False)
async def profile_action_login(profile_id: int, data: DatingProfileLogin,
                            user=Depends(auth_required)):
    """Входит под учёткой в живой сессии браузера.

    Сессию заводим свою на адрес сайта, если её не передали: кнопку жмут, чтобы
    оказаться внутри, а не чтобы узнать, что сессию надо создать самому. Гасить
    её потом нельзя — ради неё всё и затевалось (`keep_session`).

    Логин и пароль уходят `secrets`: в базу прогонов и в трассу они не попадают,
    там остаётся `{{password}}` (раздел «Переменные и секреты» в `browser.md`).
    """
    # Существование учётки проверяем здесь, хотя модуль входа проверит его сам:
    # «нет объекта» — это 404, а всё, что модуль зовёт «войти нечем», отдаётся
    # ниже как 422. Различать их внутри модуля нечем: HTTP-кодов он не знает.
    await profile_row(profile_id)

    try:
        # ⚠ `force=True` — вход по кнопке проходит и паузу после неудач, и
        # исчерпанный счётчик. Человек у экрана знает больше автомата: он мог
        # сменить пароль, разблокировать учётку, дождаться конца чужой
        # блокировки. Отказывать ему значило бы чинить сайт через базу.
        result = await login_run(profile_id, int(data.scenario_id or 0),
                                        str(data.session_id or ''), force=True)
    except DatingLoginError as e:
        # «Войти нечем» — это ошибка запроса, а не сбой: нет пароля, нет сценария,
        # нет сайта. 422 отличает её от 502 «сосед не отвечает».
        raise HTTPException(status_code=422, detail=str(e))
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {'result': result}


@router.post('/{profile_id}/send', include_in_schema=False)
async def profile_action_send(profile_id: int, data: DatingProfileSend,
                           user=Depends(auth_required)):
    """Шлёт сообщение от имени анкеты — руками, без агента.

    Нужно там, где агент ни при чём: ответить самому, проверить доставку, дослать
    сорвавшееся. Путь тот же, что у агента, поэтому проверка здесь проверяет
    настоящую отправку, а не её подобие.
    """
    profile = await profile_row(profile_id)
    site = await profile_site(int(profile['site_id']))

    result = await send_message(profile, json_column_load(site.get('presence')), data.to, data.text)

    # ⚠ **В журнале — кто, кому и что.** Прежде стояло «#181685: текст», и по нему
    # нельзя было ответить ни на один вопрос разбора: от какой анкеты ушло (в
    # журнале раздела строки всех анкет вперемешку), кому (номер без имени ничего
    # не говорит), а при отказе — и что именно не ушло.
    #
    # Имя собеседника берём из переписки: сайт присылает его с каждым сообщением,
    # и своей таблицы имён для этого заводить не надо. Нет имени — остаётся номер,
    # он есть всегда.
    who = await _contact_name(profile_id, data.to)
    me = _profile_name(profile) or profile['login']

    await log_write(
        profile_id, 'out' if result['ok'] else 'fail',
        f"{me} → {who}: «{data.text[:300]}»" if result['ok']
        else f"Не ушло: {me} → {who} «{data.text[:120]}» — {result['error']}")

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"сообщение не ушло: {result['error']}",
                                  context={'profile_id': profile_id, 'to': data.to})

    return {'result': result}
@router.put('/{profile_id}/auto', include_in_schema=False)
async def profile_action_auto(profile_id: int, data: DatingProfileAuto, request: Request,
                           user=Depends(auth_required)):
    """Включает или выключает автоответ анкеты.

    Включение сразу разбирает накопившееся: человек нажал «отвечать» — значит
    ждать до следующего круга смены (до минуты) незачем, а неотвеченное письмо
    висит уже сейчас.
    """
    profile = await profile_row(profile_id)

    async with mysql_get_db_async() as db:
        if data.auto_answer_only is None:
            await db.execute('UPDATE profile SET auto_answer = %s WHERE id = %s',
                             (1 if data.auto_answer else 0, profile_id))
        else:
            await db.execute('UPDATE profile SET auto_answer = %s, auto_answer_only = %s '
                             ' WHERE id = %s',
                             (1 if data.auto_answer else 0, data.auto_answer_only.strip(), profile_id))
        await db.connection.commit()

    profile = await profile_row(profile_id)

    await log_write(profile_id, 'info',
                           'Автоответ включён' if data.auto_answer else 'Автоответ выключен')

    answered = {}
    if data.auto_answer:
        site = await profile_site(int(profile['site_id']))
        agent = await agent_ensure(profile, json_column_load(profile.get('profile')))
        agent_id = await _agent_id_by_name(agent['name'])
        if agent_id:
            answered = await answer_run({**profile, 'auto_answer': 1},
                                               json_column_load(site.get('presence')),
                                               agent_id)

    return {'result': {'auto_answer': data.auto_answer, 'answered': answered}}


@router.post('/{profile_id}/answer', include_in_schema=False)
async def profile_action_answer(profile_id: int, request: Request,
                             user=Depends(auth_required)):
    """Отвечает собеседникам, чьё сообщение осталось без ответа.

    Текст сочиняет агент анкеты через `/chat/ask`; сюда он приходит готовым и
    уходит в чат. Пустой ответ означает «промолчать» — сообщение помечается
    отвеченным, но в чат ничего не идёт.
    """
    profile = await profile_row(profile_id)
    site = await profile_site(int(profile['site_id']))
    presence = json_column_load(site.get('presence'))

    agent = await agent_ensure(profile, json_column_load(profile.get('profile')))
    agent_id = await _agent_id_by_name(agent['name'])
    if not agent_id:
        raise HTTPException(status_code=422,
                            detail=f"у анкеты нет агента «{agent['name']}»: {agent['error']}")

    result = await answer_run(profile, presence, agent_id)

    for error in result['errors']:
        await rhapsody_api_error_write('dating', f'ответ не отправлен — {error}',
                                  context={'profile_id': profile_id, 'agent_id': agent_id,
                                           'where': 'кнопка «Ответить»'})

    return {'result': {**result, 'agent': agent['name']}}


@router.post('/{profile_id}/dialog', include_in_schema=False)
async def profile_action_dialog(profile_id: int,
                             user=Depends(auth_required)):
    """Читает диалоги учётки со страницы чата и складывает переписку.

    Нужно потому, что сокет отдаёт только пришедшее при нём: всё, что было раньше,
    сайт в чат не возвращает. Открывать страницу дорого и она отбирает у учётки
    живое соединение, поэтому это кнопка, а не фон.
    """
    profile = await profile_row(profile_id)
    site = await profile_site(int(profile['site_id']))
    presence = json_column_load(site.get('presence'))

    await login_session_close_old(profile['login'], '')
    session_id = await _session_open_for(site, {**profile,
                                                'storage_state': json_column_load(profile.get('storage_state'))})

    result = await dialog_sync(profile, presence, session_id,
                                      _scenario_runner(profile))

    if result['error']:
        await rhapsody_api_error_write('dating', f"диалоги не прочитались: {result['error']}",
                                  session_id=session_id,
                                  context={'profile_id': profile_id, 'where': 'чтение диалогов'})

    # Страница чата отобрала у учётки сокет — возвращаем его сразу, иначе живой
    # приём молчал бы до ближайшей переподвязки держателя. И гасим вкладку: пока
    # она открыта, входящие достаются ей, а не держателю.
    await login_ws_capture(await profile_row(profile_id), session_id)
    await login_session_close(session_id)

    return {'result': result}


@router.post('/{profile_id}/disconnect', include_in_schema=False)
async def profile_action_disconnect(profile_id: int,
                                 user=Depends(auth_required)):
    """Отключает учётку: выход на сайте, закрытие вкладок, снятие с мониторинга.

    Не удаление: переписка, журнал и анкета остаются, а вернуть учётку в работу —
    это «Залогиниться». Выход на самом сайте необязателен и не отменяет локальную
    часть: сайт может не ответить, а отключить учётку надо именно тогда.
    """
    profile = await profile_row(profile_id)
    site = await profile_site(int(profile['site_id']))

    result = await disconnect_run(
        {**profile, 'storage_state': json_column_load(profile.get('storage_state'))},
        site, json_column_load(site.get('presence')),
        _scenario_runner(profile), _session_open_for,
        login_session_all, login_session_close)

    if result['error']:
        await rhapsody_api_error_write('dating', f"выход на сайте не прошёл: {result['error']}",
                                  context={'profile_id': profile_id, 'site_id': profile['site_id']})

    return {'result': result}


@router.post('/probe', include_in_schema=False)
async def profile_action_probe(site_id: int = 0,
                            user=Depends(auth_required)):
    """Опрашивает учётки сайта: держит онлайн и снимает счётчики.

    Идёт по HTTP с сохранёнными cookie, без браузера — замер на живом сайте:
    вкладка Chromium стоит ~143 МБ, а клиент с cookie единицы килобайт.

    ⚠ **Теперь это не единственный опрашивающий.** То же самое делает круг
    воркера (`status_round.py`), и делает независимо от того, открыта ли
    панель: прежде статус двигала только эта ручка, и у анкеты без смен он не
    менялся неделями, пока никто не смотрел. Ручка осталась ради **немедленного**
    ответа — открыл страницу, нажал, увидел сейчас, а не через минуту.

    Работа у обоих одна (`status_probe_site`): правило «`last_seen_at`
    двигает только подтверждённый вход» и правило «в «Ошибки» — только смену
    состояния» знать по-разному им нельзя.
    """
    site = await profile_site(site_id) if site_id else None
    got = await status_probe_site(int(site_id),
                                         json_column_load((site or {}).get('presence')))
    if not got['ready']:
        raise HTTPException(
            status_code=422,
            detail='у сайта не настроено присутствие: заполните «Присутствие: '
                   'настройка» на странице «Сайты»')

    return {'result': {'checked': got['checked'], 'online': got['online'],
                       'profiles': got['profiles']}}


@router.post('/{profile_id}/ping', include_in_schema=False)
async def profile_action_ping(profile_id: int,
                              user=Depends(auth_required)):
    """Пинг одной учётки: жив ли вход, прямо сейчас.

    Тот же дешёвый опрос, что и у `/probe`, но по одной анкете и по нажатию.
    Идёт по HTTP с сохранёнными cookie, без браузера, и отвечает за секунды —
    в отличие от «Войти», где сценарий в живом Chromium занимает минуты.

    Побочного действия у него нет: сессию не заводит, вход не чинит. Ответ
    «сайт не признал вход» — это ответ, а не отказ ручки, и разбираться с ним
    человеку кнопкой «Войти».
    """
    profile = await profile_row(profile_id)
    site = await profile_site(int(profile['site_id']))

    presence = json_column_load(site.get('presence'))
    if not await presence_ready(presence, int(profile['site_id'])):
        raise HTTPException(
            status_code=422,
            detail='у сайта не настроено присутствие: заполните «Присутствие: '
                   'настройка» на странице «Сайты»')

    result = await presence_probe(profile, presence)

    async with mysql_get_db_async() as db:
        await status_write(db, profile_id, result)
        await db.connection.commit()

    # В журнал — каждый пинг, в отличие от кругового опроса: там строка на каждую
    # из сотни учёток раз в 15 секунд залила бы ленту, здесь же это нажатие
    # человека, и след от него — ровно то, за чем в журнал потом и смотрят.
    await log_write(profile_id, 'ok' if result['status'] == 'online' else 'fail',
                           f"Пинг: {result['error'] or result['status']}, {result['ms']} мс")

    return {'result': result}


@router.post('/{profile_id}/listen', include_in_schema=False)
async def profile_action_listen(profile_id: int, seconds: int = 30,
                             user=Depends(auth_required)):
    """Слушает чат-сокет учётки и складывает входящие сообщения.

    Адрес сокета снимается браузером, если его ещё нет: в разметке страницы его
    нет, собирает его JS. Дальше подключение держит обычный клиент — вкладка
    Chromium стоила бы ~143 МБ, а сокет десятки килобайт.

    Ручка слушает ограниченное время и возвращает итог: постоянное подключение —
    работа воркера, а страница дёргает её, когда человек смотрит на карточку.
    """
    profile = await profile_row(profile_id)
    # Настройка сайта нужна не только адресу: по ней выбирается драйвер протокола
    # чата — имена методов и форма кадров у каждого сайта свои.
    site = await profile_site(int(profile['site_id']))
    presence = json_column_load(site.get('presence'))

    if not str(profile.get('ws_url') or ''):
        try:
            profile['ws_url'] = await login_ws_capture(profile)
        except DatingLoginError as e:
            # Модуль входа не знает HTTP-кодов и сигналит исключением; сайт, которого
            # уже нет у соседа, — это ошибка запроса (422), а не сбой сервера (500).
            raise HTTPException(status_code=422, detail=str(e))
        if not profile['ws_url']:
            raise HTTPException(status_code=422,
                                detail='не удалось снять адрес чат-сокета: сначала «Залогиниться»')

    result = await chat_listen(profile, seconds=max(5, min(int(seconds or 30), 120)),
                                      presence=presence)

    # Протухший адрес — обычное дело: токены в нём живут не вечно. Снимаем заново
    # и пробуем ещё раз, чтобы человеку не приходилось гадать, почему пусто.
    if result.get('error') and not result.get('frames'):
        try:
            profile['ws_url'] = await login_ws_capture(profile)
        except DatingLoginError as e:
            # Та же причина, что и выше: сайт мог пропасть между первой и повторной
            # попыткой — и это снова 422, а не 500.
            raise HTTPException(status_code=422, detail=str(e))
        if profile['ws_url']:
            result = await chat_listen(profile, seconds=max(5, min(int(seconds or 30), 120)),
                                              presence=presence)

    if result.get('error'):
        await rhapsody_api_error_write('dating', f"чат-сокет: {result['error']}",
                                  context={'profile_id': profile_id})

    return {'result': result}


@router.post('/{profile_id}/check', include_in_schema=False)
async def profile_action_check(profile_id: int, request: Request,
                            user=Depends(auth_required)):
    """Полная проверка учётки: вход, чат, чтение, пробная отправка, закрытие.

    Каждый шаг пишется в журнал (`log`) — по нему на странице видно, где
    именно встало. Профиль и собеседницы обновляются заодно: раз уж вошли, брать
    их отдельным заходом незачем.
    """
    profile = await profile_row(profile_id)
    site = await profile_site(int(profile['site_id']))
    presence = json_column_load(site.get('presence'))
    if not await presence_ready(presence, int(profile['site_id'])):
        raise HTTPException(
            status_code=422,
            detail='у сайта не настроено присутствие: заполните «Присутствие: '
                   'настройка» на странице «Сайты»')

    result = await check_run(profile, presence, login_ws_capture)

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"проверка учётки «{profile['login']}» встала: {result['error']}",
                                  context={'profile_id': profile_id, 'site_id': profile['site_id'],
                                           'steps': result['steps'], 'where': 'кнопка «Проверка»'})

    agent = {}
    if result['ok']:
        profile = await profile_row(profile_id)
        refreshed = await profile_cache_refresh(profile, presence)
        await log_write(profile_id, 'ok' if not refreshed['error'] else 'fail',
                               f"Профиль обновлён, собеседниц {refreshed['contacts']}"
                               if not refreshed['error'] else f"Профиль не обновился: {refreshed['error']}")

        # Последним шагом — агент анкеты: отвечать в чате будет он, и заводить
        # его руками на каждую новую девушку значит рано или поздно забыть.
        # Проверка идёт по имени: агента могли удалить или переименовать.
        #
        # Строку перечитываем: анкету только что обновили, а из неё берётся
        # биография, из которой workflow добывает факты. Со старой в руках
        # агент завёлся бы по вчерашнему тексту — или вовсе без него.
        profile = await profile_row(profile_id)
        agent = await agent_ensure(profile, json_column_load(profile.get('profile')))
        if agent['error']:
            await rhapsody_api_error_write('dating', f"агент анкеты не заведён: {agent['error']}",
                                      context={'profile_id': profile_id, 'agent': agent['name']})

    result['agent'] = agent

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET status = %s, last_error = %s WHERE id = %s',
                         ('online' if result['ok'] else 'error', result['error'], profile_id))
        await db.connection.commit()

    return {'result': result}


# ── Приватное ────────────────────────────────────────────────────────────────


async def _proxy_on(profile: dict) -> None:
    """Пользуется ли сайт анкеты выходами. Нет — 409 с тем, что нажать.

    ⚠ **Отказ, а не молчаливое согласие.** Поставить выход анкете сайта, где
    прокси выключены, значит записать в базу адрес, которым никто не пойдёт:
    двери спрашивают выключатель и получают пусто. Человек видел бы выбранный
    канал в карточке и считал бы, что анкета ходит им.

    Код 409, а не 422: тело запроса правильное, не сходится состояние — и чинится
    оно в другом месте, о котором и говорит текст.
    """
    from src.dating.proxy import proxy_enabled

    if not await proxy_enabled(int(profile.get('site_id') or 0)):
        raise HTTPException(
            status_code=409,
            detail='у сайта выключены выходы: включите галочку «Использовать '
                   'выходы» на вкладке «Прокси» в настройках сайта')


async def _keep_online_clear(profile_id: int) -> None:
    """Снимает присмотр автомата: круг восстановления больше не вернёт анкету в сеть.

    ⚠ **Не бросает.** Флаг расписания уже записан, и падать здесь — значит отвечать
    человеку отказом на действие, которое состоялось. Не снялось — круг вернёт
    анкету, и это видно в журнале; отказ же спрятал бы и саму смену флага.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET keep_online = 0 WHERE id = %s',
                             (int(profile_id),))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] присмотр анкеты #{profile_id} не снялся: {e}')


async def _contact_name(profile_id: int, uid: str) -> str:
    """Имя собеседника для журнала: «Semen Gorbunkov 181685».

    Имя приходит с каждым сообщением от сайта и лежит в переписке — своей таблицы
    имён для этого заводить не надо. Берём последнее непустое: люди меняют имя в
    анкете, и свежее ближе к правде.

    Номер остаётся всегда, даже когда имени нет: по нему собеседника ищут и в
    панели сайта, и в его чате.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute("SELECT sender_name FROM message "
                             " WHERE profile_id = %s AND sender_uid = %s "
                             "   AND sender_name <> '' "
                             " ORDER BY created_at DESC, id DESC LIMIT 1",
                             (int(profile_id), str(uid)[:32]))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] имя собеседника не прочиталось: {e}')

        return str(uid)

    name = str((row or {}).get('sender_name') or '').strip()

    return f'{name} {uid}' if name else str(uid)


def _profile_name(profile: dict) -> str:
    """Имя анкеты с сайта — то, под которым её видит собеседник.

    Оно в JSON-столбце `profile`, а не отдельной колонкой: анкета целиком приходит
    от сайта, и вытаскивать из неё поля в свои колонки значило бы держать вторую
    копию (`dating.md`, §6).
    """
    card = json_column_load(profile.get('profile'))
    name = str(card.get('name') or '').strip()
    uid = str(card.get('id') or '').strip()

    return f'{name} {uid}'.strip() if name else uid


async def _agent_id_by_name(name: str) -> int:
    """Номер агента у соседа. Своей таблицы агентов здесь нет: они, workflow и
    факты — хозяйство RhapsodyAi, и вторая копия расходилась бы с первой."""
    try:
        found = await rhapsody_api_agent_find(str(name))
    except RhapsodyError as e:
        logger_info(f'[dating] агент «{name}» не спросился: {e}')
        return 0

    return int((found or {}).get('id') or 0)


def _scenario_runner(profile: dict):
    """Чем гонять сценарии этой анкеты: общая дверь с уже подставленными номерами.

    Модули предметной части (`dialog`, `disconnect`) знают про анкету,
    но не про журналы и роутеры — чем гонять, им передают снаружи. Замыкание
    подставляет сайт и анкету, чтобы прогон попал и в журнал прогонов, и в журнал
    самой анкеты (`scenario.py`).

    `keep_session=True`: оба зовущих работают в уже открытой вкладке и закрывают
    её сами.
    """
    async def _run(scenario, session_id, variables):
        return await scenario_run(scenario, int(profile['site_id']),
                                         int(profile['id']), session_id, variables, {},
                                         keep_session=True)

    return _run


async def _session_open_for(site: dict, profile: dict) -> str:
    """Сессия под уже снятый вход учётки — без прохода по форме.

    Нужна отключению: выходить на сайте надо из авторизованной вкладки, а живой
    вкладки к этому моменту может уже не быть.
    """
    # Выход тот же, что у входа: сайт сверяет адрес и с выходом тоже, а «вышел с
    # чужого IP» — это для него не выход, а подозрение.
    return await rhapsody_api_session_open(url=site['url'], name=str(profile['login'])[:100],
                                          site_id=int(site['id']),
                                          proxy_id=int(profile.get('proxy_id') or 0),
                                          storage_state=profile.get('storage_state') or {})
