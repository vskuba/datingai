"""Действия над учёткой: вход, проверка, отправка, опрос, автоответ.

Отдельно от CRUD (`dating_profile.py`) по одному признаку: **всё здесь ходит
наружу** — к сайту знакомств или к соседу за браузером и агентом. Отсюда и
свойства, которых нет у правки логина: занимает секунды, а вход и проверка —
минуты; падает от чужой недоступности (502); почти всегда пишет строку в журнал
учётки, потому что результат нужен человеку, который нажал кнопку.

«Залогиниться» — не отдельная механика, а прогон сценария входа этого сайта
(`browser_scenario`) в живой сессии: логин и пароль уходят туда `secrets`, то
есть мимо базы и журнала прогонов. Сценарий ищется по подстановкам, которые он
ждёт: тот, кому нужны и логин, и пароль, и есть вход.

Сам вход здесь не написан, а вызван: он живёт в `src/dating/dating_login.py`,
потому что входит не только человек кнопкой — смена держателя сокетов входит
сама, когда сессия сайта протухла. Ручка отвечает лишь за коды ответа. То же и с
остальным: проверка в `dating_check.py`, отправка в `dating_send.py`, ответ агента
в `dating_answer.py`. Здесь — HTTP-обвязка над ними.

Роутер тот же (`/api/dating/profile`), и это не случайность: снаружи ручки анкеты
остаются одной группой. Разделение — про то, где лежит код, а не про то, как он
виден клиенту.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_agent_find,
                                       rhapsody_api_error_write,
                                       rhapsody_api_session_open)
from src.dating.dating_agent import dating_agent_ensure
from src.dating.dating_answer import dating_answer_run
from src.dating.dating_scenario import dating_scenario_run
from src.dating.dating_chat import dating_chat_listen
from src.dating.dating_check import dating_check_run
from src.dating.dating_dialog import dating_dialog_sync
from src.dating.dating_disconnect import dating_disconnect_run
from src.dating.dating_json import dating_json_load
from src.dating.dating_schedule import (dating_schedule_check, dating_schedule_dump,
                                        dating_schedule_load)
from src.dating.dating_log import dating_log_write
from src.dating.dating_login import (DatingLoginError, dating_login_run,
                                     dating_login_session_all, dating_login_session_close,
                                     dating_login_session_close_old, dating_login_ws_capture)
from src.dating.dating_presence import dating_presence_probe, dating_presence_ready
from src.dating.dating_profile_cache import dating_profile_cache_refresh
from src.dating.dating_send import dating_send_message
from src.uvicorn.routers.dating.dating_profile import dating_profile_row, dating_profile_site

router = APIRouter(prefix='/api/dating/profile', tags=['dating'])

# Сколько учёток опрашиваем разом. Замер: один запрос к сайту идёт ~0.6с, значит
# двести учёток раз в 15 секунд требуют девяти параллельных — берём с запасом.
DATING_PROFILE_ACTION_PROBE_PARALLEL = 16


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
async def dating_profile_action_password(profile_id: int, data: DatingProfilePassword,
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
    profile = await dating_profile_row(profile_id)

    source = str(data.password_source or '').strip()
    if source not in ('site', 'own'):
        raise HTTPException(status_code=422, detail='источник пароля: `site` или `own`')

    password = str(data.password or '')

    async with mysql_get_db_async() as db:
        if password:
            await db.execute('UPDATE dating_profile SET password_source = %s, password = %s '
                             ' WHERE id = %s', (source, password, profile_id))
        else:
            await db.execute('UPDATE dating_profile SET password_source = %s WHERE id = %s',
                             (source, profile_id))
        await db.connection.commit()

    await dating_log_write(
        profile_id, 'info',
        ('Пароль: свой' if source == 'own' else 'Пароль: общий агентский из настроек сайта')
        + (', значение изменено' if password else ''))

    row = await dating_profile_row(profile_id)

    return {'result': {'password_source': source,
                       'has_password': bool(str(row.get('password') or ''))}}


class DatingProfileSchedule(BaseModel):
    """Расписание анкеты: смены суток целиком."""

    schedule: list[dict] = Field(default_factory=list,
                                 description='Смены: [{"from": "07:00", "to": "11:00"}]')


@router.put('/{profile_id}/schedule', include_in_schema=False)
async def dating_profile_action_schedule(profile_id: int, data: DatingProfileSchedule,
                                         user=Depends(auth_required)):
    """Сохраняет расписание анкеты — целиком, а не по смене.

    Страница держит расписание перед глазами и шлёт то, что на ней. Правка по одной
    смене означала бы, что удаление — отдельная ручка, а порядок двух правок подряд
    начинает значить.

    ⚠ **Негодное расписание отклоняется, а не правится молча.** Смены внахлёст —
    это вход поверх входа: анкета получит вторую сессию, не закрыв первую. Сказать
    об этом человеку полезнее, чем тихо сдвинуть его время.
    """
    await dating_profile_row(profile_id)

    why = dating_schedule_check(data.schedule)
    if why:
        raise HTTPException(status_code=422, detail=why)

    rows = dating_schedule_load(data.schedule)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET schedule = %s WHERE id = %s',
                         (dating_schedule_dump(rows), profile_id))
        await db.connection.commit()

    await dating_log_write(
        profile_id, 'info',
        'Расписание: ' + (', '.join(f"{r['from']}–{r['to']}" for r in rows)
                          if rows else 'снято, анкету ведут руками'))

    return {'result': {'schedule': rows}}


class DatingProfileEnabled(BaseModel):
    """Выключатель анкеты целиком."""

    enabled: bool = Field(..., description='Ведём ли анкету: выключенную воркер не трогает')


@router.put('/{profile_id}/enabled', include_in_schema=False)
async def dating_profile_action_enabled(profile_id: int, data: DatingProfileEnabled,
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
    await dating_profile_row(profile_id)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET enabled = %s WHERE id = %s',
                         (1 if data.enabled else 0, profile_id))
        await db.connection.commit()

    await dating_log_write(profile_id, 'info',
                           'Анкета включена' if data.enabled
                           else 'Анкета выключена: воркер её не трогает')

    return {'result': {'enabled': data.enabled}}


@router.post('/{profile_id}/login', include_in_schema=False)
async def dating_profile_action_login(profile_id: int, data: DatingProfileLogin,
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
    await dating_profile_row(profile_id)

    try:
        result = await dating_login_run(profile_id, int(data.scenario_id or 0),
                                        str(data.session_id or ''))
    except DatingLoginError as e:
        # «Войти нечем» — это ошибка запроса, а не сбой: нет пароля, нет сценария,
        # нет сайта. 422 отличает её от 502 «сосед не отвечает».
        raise HTTPException(status_code=422, detail=str(e))
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return {'result': result}


@router.post('/{profile_id}/send', include_in_schema=False)
async def dating_profile_action_send(profile_id: int, data: DatingProfileSend,
                           user=Depends(auth_required)):
    """Шлёт сообщение от имени анкеты — руками, без агента.

    Нужно там, где агент ни при чём: ответить самому, проверить доставку, дослать
    сорвавшееся. Путь тот же, что у агента, поэтому проверка здесь проверяет
    настоящую отправку, а не её подобие.
    """
    profile = await dating_profile_row(profile_id)
    site = await dating_profile_site(int(profile['site_id']))

    result = await dating_send_message(profile, dating_json_load(site.get('presence')), data.to, data.text)

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

    await dating_log_write(
        profile_id, 'out' if result['ok'] else 'fail',
        f"{me} → {who}: «{data.text[:300]}»" if result['ok']
        else f"Не ушло: {me} → {who} «{data.text[:120]}» — {result['error']}")

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"сообщение не ушло: {result['error']}",
                                  context={'profile_id': profile_id, 'to': data.to})

    return {'result': result}


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
            await db.execute("SELECT sender_name FROM dating_message "
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
    card = dating_json_load(profile.get('profile'))
    name = str(card.get('name') or '').strip()
    uid = str(card.get('site_uid') or card.get('id') or '').strip()

    return f'{name} {uid}'.strip() if name else uid


@router.put('/{profile_id}/auto', include_in_schema=False)
async def dating_profile_action_auto(profile_id: int, data: DatingProfileAuto, request: Request,
                           user=Depends(auth_required)):
    """Включает или выключает автоответ анкеты.

    Включение сразу разбирает накопившееся: человек нажал «отвечать» — значит
    ждать до следующего круга смены (до минуты) незачем, а неотвеченное письмо
    висит уже сейчас.
    """
    profile = await dating_profile_row(profile_id)

    async with mysql_get_db_async() as db:
        if data.auto_answer_only is None:
            await db.execute('UPDATE dating_profile SET auto_answer = %s WHERE id = %s',
                             (1 if data.auto_answer else 0, profile_id))
        else:
            await db.execute('UPDATE dating_profile SET auto_answer = %s, auto_answer_only = %s '
                             ' WHERE id = %s',
                             (1 if data.auto_answer else 0, data.auto_answer_only.strip(), profile_id))
        await db.connection.commit()

    profile = await dating_profile_row(profile_id)

    await dating_log_write(profile_id, 'info',
                           'Автоответ включён' if data.auto_answer else 'Автоответ выключен')

    answered = {}
    if data.auto_answer:
        site = await dating_profile_site(int(profile['site_id']))
        agent = await dating_agent_ensure(profile, dating_json_load(profile.get('profile')))
        agent_id = await _agent_id_by_name(agent['name'])
        if agent_id:
            answered = await dating_answer_run({**profile, 'auto_answer': 1},
                                               dating_json_load(site.get('presence')),
                                               agent_id)

    return {'result': {'auto_answer': data.auto_answer, 'answered': answered}}


@router.post('/{profile_id}/answer', include_in_schema=False)
async def dating_profile_action_answer(profile_id: int, request: Request,
                             user=Depends(auth_required)):
    """Отвечает собеседникам, чьё сообщение осталось без ответа.

    Текст сочиняет агент анкеты через `/chat/ask`; сюда он приходит готовым и
    уходит в чат. Пустой ответ означает «промолчать» — сообщение помечается
    отвеченным, но в чат ничего не идёт.
    """
    profile = await dating_profile_row(profile_id)
    site = await dating_profile_site(int(profile['site_id']))
    presence = dating_json_load(site.get('presence'))

    agent = await dating_agent_ensure(profile, dating_json_load(profile.get('profile')))
    agent_id = await _agent_id_by_name(agent['name'])
    if not agent_id:
        raise HTTPException(status_code=422,
                            detail=f"у анкеты нет агента «{agent['name']}»: {agent['error']}")

    result = await dating_answer_run(profile, presence, agent_id)

    for error in result['errors']:
        await rhapsody_api_error_write('dating', f'ответ не отправлен — {error}',
                                  context={'profile_id': profile_id, 'agent_id': agent_id,
                                           'where': 'кнопка «Ответить»'})

    return {'result': {**result, 'agent': agent['name']}}


@router.post('/{profile_id}/dialog', include_in_schema=False)
async def dating_profile_action_dialog(profile_id: int,
                             user=Depends(auth_required)):
    """Читает диалоги учётки со страницы чата и складывает переписку.

    Нужно потому, что сокет отдаёт только пришедшее при нём: всё, что было раньше,
    сайт в чат не возвращает. Открывать страницу дорого и она отбирает у учётки
    живое соединение, поэтому это кнопка, а не фон.
    """
    profile = await dating_profile_row(profile_id)
    site = await dating_profile_site(int(profile['site_id']))
    presence = dating_json_load(site.get('presence'))

    await dating_login_session_close_old(profile['login'], '')
    session_id = await _session_open_for(site, {**profile,
                                                'storage_state': dating_json_load(profile.get('storage_state'))})

    result = await dating_dialog_sync(profile, presence, session_id,
                                      _scenario_runner(profile))

    if result['error']:
        await rhapsody_api_error_write('dating', f"диалоги не прочитались: {result['error']}",
                                  session_id=session_id,
                                  context={'profile_id': profile_id, 'where': 'чтение диалогов'})

    # Страница чата отобрала у учётки сокет — возвращаем его сразу, иначе живой
    # приём молчал бы до ближайшей переподвязки держателя. И гасим вкладку: пока
    # она открыта, входящие достаются ей, а не держателю.
    await dating_login_ws_capture(await dating_profile_row(profile_id), session_id)
    await dating_login_session_close(session_id)

    return {'result': result}


@router.post('/{profile_id}/disconnect', include_in_schema=False)
async def dating_profile_action_disconnect(profile_id: int,
                                 user=Depends(auth_required)):
    """Отключает учётку: выход на сайте, закрытие вкладок, снятие с мониторинга.

    Не удаление: переписка, журнал и анкета остаются, а вернуть учётку в работу —
    это «Залогиниться». Выход на самом сайте необязателен и не отменяет локальную
    часть: сайт может не ответить, а отключить учётку надо именно тогда.
    """
    profile = await dating_profile_row(profile_id)
    site = await dating_profile_site(int(profile['site_id']))

    result = await dating_disconnect_run(
        {**profile, 'storage_state': dating_json_load(profile.get('storage_state'))},
        site, dating_json_load(site.get('presence')),
        _scenario_runner(profile), _session_open_for,
        dating_login_session_all, dating_login_session_close)

    if result['error']:
        await rhapsody_api_error_write('dating', f"выход на сайте не прошёл: {result['error']}",
                                  context={'profile_id': profile_id, 'site_id': profile['site_id']})

    return {'result': result}


@router.post('/probe', include_in_schema=False)
async def dating_profile_action_probe(site_id: int = 0,
                            user=Depends(auth_required)):
    """Опрашивает учётки сайта: держит онлайн и снимает счётчики.

    Идёт по HTTP с сохранёнными cookie, без браузера — замер на живом сайте:
    вкладка Chromium стоит ~143 МБ, а клиент с cookie единицы килобайт. Ради
    этого страница и зовёт эту ручку раз в 15 секунд, а не гоняет сценарии.

    Опрашиваются разом: у двухсот учёток последовательный круг занял бы минуты,
    а параллельный укладывается в секунды (`DATING_PROFILE_ACTION_PROBE_PARALLEL`).
    """
    site = await dating_profile_site(site_id) if site_id else None
    presence = dating_json_load((site or {}).get('presence'))
    if not dating_presence_ready(presence):
        raise HTTPException(status_code=422,
                            detail='у сайта не настроен опрос присутствия (browser_site.presence)')

    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, login, status, storage_state FROM dating_profile '
                         ' WHERE site_id = %s AND storage_state IS NOT NULL', (int(site_id),))
        profiles = [dict(row) for row in (await db.fetchall() or [])]

    if not profiles:
        return {'result': {'checked': 0, 'online': 0}}

    limit = asyncio.Semaphore(DATING_PROFILE_ACTION_PROBE_PARALLEL)

    async def probe(profile):
        async with limit:
            return profile, await dating_presence_probe(profile, presence)

    done = await asyncio.gather(*(probe(profile) for profile in profiles), return_exceptions=False)

    async with mysql_get_db_async() as db:
        for profile, result in done:
            await _probe_write(db, int(profile['id']), result)
        await db.connection.commit()

    # В журнал ошибок — только смена состояния, а не каждый неудачный круг: опрос
    # идёт раз в 15 секунд, и сотня учёток залила бы вкладку за минуту одним и тем
    # же. Текущее состояние и так видно на карточке.
    for profile, result in done:
        if result['status'] != 'online' and str(profile.get('status') or '') == 'online':
            await rhapsody_api_error_write('dating', f"учётка «{profile['login']}» ушла из сети: "
                                                f"{result['error'] or result['status']}",
                                      context={'profile_id': profile['id'], 'site_id': site_id,
                                               'where': 'опрос присутствия'})

    online = sum(1 for _, r in done if r['status'] == 'online')

    return {'result': {'checked': len(done), 'online': online,
                       'profiles': [{'id': g['id'], 'status': r['status'], 'unread': r['unread'],
                                  'error': r['error'], 'ms': r['ms']} for g, r in done]}}


@router.post('/{profile_id}/ping', include_in_schema=False)
async def dating_profile_action_ping(profile_id: int,
                              user=Depends(auth_required)):
    """Пинг одной учётки: жив ли вход, прямо сейчас.

    Тот же дешёвый опрос, что и у `/probe`, но по одной анкете и по нажатию.
    Идёт по HTTP с сохранёнными cookie, без браузера, и отвечает за секунды —
    в отличие от «Войти», где сценарий в живом Chromium занимает минуты.

    Побочного действия у него нет: сессию не заводит, вход не чинит. Ответ
    «сайт не признал вход» — это ответ, а не отказ ручки, и разбираться с ним
    человеку кнопкой «Войти».
    """
    profile = await dating_profile_row(profile_id)
    site = await dating_profile_site(int(profile['site_id']))

    presence = dating_json_load(site.get('presence'))
    if not dating_presence_ready(presence):
        raise HTTPException(status_code=422,
                            detail='у сайта не настроен опрос присутствия (browser_site.presence)')

    result = await dating_presence_probe(profile, presence)

    async with mysql_get_db_async() as db:
        await _probe_write(db, profile_id, result)
        await db.connection.commit()

    # В журнал — каждый пинг, в отличие от кругового опроса: там строка на каждую
    # из сотни учёток раз в 15 секунд залила бы ленту, здесь же это нажатие
    # человека, и след от него — ровно то, за чем в журнал потом и смотрят.
    await dating_log_write(profile_id, 'ok' if result['status'] == 'online' else 'fail',
                           f"Пинг: {result['error'] or result['status']}, {result['ms']} мс")

    return {'result': result}


@router.post('/{profile_id}/listen', include_in_schema=False)
async def dating_profile_action_listen(profile_id: int, seconds: int = 30,
                             user=Depends(auth_required)):
    """Слушает чат-сокет учётки и складывает входящие сообщения.

    Адрес сокета снимается браузером, если его ещё нет: в разметке страницы его
    нет, собирает его JS. Дальше подключение держит обычный клиент — вкладка
    Chromium стоила бы ~143 МБ, а сокет десятки килобайт.

    Ручка слушает ограниченное время и возвращает итог: постоянное подключение —
    работа воркера, а страница дёргает её, когда человек смотрит на карточку.
    """
    profile = await dating_profile_row(profile_id)
    # Настройка сайта нужна не только адресу: по ней выбирается драйвер протокола
    # чата — имена методов и форма кадров у каждого сайта свои.
    site = await dating_profile_site(int(profile['site_id']))
    presence = dating_json_load(site.get('presence'))

    if not str(profile.get('ws_url') or ''):
        try:
            profile['ws_url'] = await dating_login_ws_capture(profile)
        except DatingLoginError as e:
            # Модуль входа не знает HTTP-кодов и сигналит исключением; сайт, которого
            # уже нет у соседа, — это ошибка запроса (422), а не сбой сервера (500).
            raise HTTPException(status_code=422, detail=str(e))
        if not profile['ws_url']:
            raise HTTPException(status_code=422,
                                detail='не удалось снять адрес чат-сокета: сначала «Залогиниться»')

    result = await dating_chat_listen(profile, seconds=max(5, min(int(seconds or 30), 120)),
                                      presence=presence)

    # Протухший адрес — обычное дело: токены в нём живут не вечно. Снимаем заново
    # и пробуем ещё раз, чтобы человеку не приходилось гадать, почему пусто.
    if result.get('error') and not result.get('frames'):
        try:
            profile['ws_url'] = await dating_login_ws_capture(profile)
        except DatingLoginError as e:
            # Та же причина, что и выше: сайт мог пропасть между первой и повторной
            # попыткой — и это снова 422, а не 500.
            raise HTTPException(status_code=422, detail=str(e))
        if profile['ws_url']:
            result = await dating_chat_listen(profile, seconds=max(5, min(int(seconds or 30), 120)),
                                              presence=presence)

    if result.get('error'):
        await rhapsody_api_error_write('dating', f"чат-сокет: {result['error']}",
                                  context={'profile_id': profile_id})

    return {'result': result}


@router.post('/{profile_id}/check', include_in_schema=False)
async def dating_profile_action_check(profile_id: int, request: Request,
                            user=Depends(auth_required)):
    """Полная проверка учётки: вход, чат, чтение, пробная отправка, закрытие.

    Каждый шаг пишется в журнал (`dating_log`) — по нему на странице видно, где
    именно встало. Профиль и собеседницы обновляются заодно: раз уж вошли, брать
    их отдельным заходом незачем.
    """
    profile = await dating_profile_row(profile_id)
    site = await dating_profile_site(int(profile['site_id']))
    presence = dating_json_load(site.get('presence'))
    if not dating_presence_ready(presence):
        raise HTTPException(status_code=422, detail='у сайта не настроен опрос присутствия')

    result = await dating_check_run(profile, presence, dating_login_ws_capture)

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"проверка учётки «{profile['login']}» встала: {result['error']}",
                                  context={'profile_id': profile_id, 'site_id': profile['site_id'],
                                           'steps': result['steps'], 'where': 'кнопка «Проверка»'})

    agent = {}
    if result['ok']:
        profile = await dating_profile_row(profile_id)
        refreshed = await dating_profile_cache_refresh(profile, presence)
        await dating_log_write(profile_id, 'ok' if not refreshed['error'] else 'fail',
                               f"Профиль обновлён, собеседниц {refreshed['contacts']}"
                               if not refreshed['error'] else f"Профиль не обновился: {refreshed['error']}")

        # Последним шагом — агент анкеты: отвечать в чате будет он, и заводить
        # его руками на каждую новую девушку значит рано или поздно забыть.
        # Проверка идёт по имени: агента могли удалить или переименовать.
        #
        # Строку перечитываем: анкету только что обновили, а из неё берётся
        # биография, из которой workflow добывает факты. Со старой в руках
        # агент завёлся бы по вчерашнему тексту — или вовсе без него.
        profile = await dating_profile_row(profile_id)
        agent = await dating_agent_ensure(profile, dating_json_load(profile.get('profile')))
        if agent['error']:
            await rhapsody_api_error_write('dating', f"агент анкеты не заведён: {agent['error']}",
                                      context={'profile_id': profile_id, 'agent': agent['name']})

    result['agent'] = agent

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET status = %s, last_error = %s WHERE id = %s',
                         ('online' if result['ok'] else 'error', result['error'], profile_id))
        await db.connection.commit()

    return {'result': result}


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

    Модули предметной части (`dating_dialog`, `dating_disconnect`) знают про анкету,
    но не про журналы и роутеры — чем гонять, им передают снаружи. Замыкание
    подставляет сайт и анкету, чтобы прогон попал и в журнал прогонов, и в журнал
    самой анкеты (`dating_scenario.py`).

    `keep_session=True`: оба зовущих работают в уже открытой вкладке и закрывают
    её сами.
    """
    async def _run(scenario, session_id, variables):
        return await dating_scenario_run(scenario, int(profile['site_id']),
                                         int(profile['id']), session_id, variables, {},
                                         keep_session=True)

    return _run


async def _session_open_for(site: dict, profile: dict) -> str:
    """Сессия под уже снятый вход учётки — без прохода по форме.

    Нужна отключению: выходить на сайте надо из авторизованной вкладки, а живой
    вкладки к этому моменту может уже не быть.
    """
    return await rhapsody_api_session_open(url=site['url'], name=str(profile['login'])[:100],
                                          site_id=int(site['id']),
                                          storage_state=profile.get('storage_state') or {})


async def _probe_write(db, profile_id: int, result: dict) -> None:
    """Кладёт итог опроса присутствия в карточку учётки. Без `commit` — его делает
    вызвавший: круговой опрос пишет сотню строк одной транзакцией.

    Одна на круговой опрос (`/probe`) и на пинг одной анкеты (`/{id}/ping`): у
    результата есть правило, которое нельзя знать по-разному в двух местах, —
    **`last_seen_at` двигает только подтверждённый вход.** Смысл поля «когда сайт
    последний раз признал учётку», а не «когда мы стучались»; сдвинь его неудачный
    опрос — и по колонке «Вход» уже не отличить живую анкету от той, что отвалилась
    неделю назад.

    `unread = -1` («не спрашивали») кладём нулём: колонка целочисленная и означает
    количество, а минус первое письмо ничего не значит.
    """
    seen = 'NOW()' if result['status'] == 'online' else 'last_seen_at'
    await db.execute(
        f'UPDATE dating_profile SET status = %s, unread = %s, last_error = %s, '
        f'       last_seen_at = {seen} WHERE id = %s',
        (result['status'],
         result['unread'] if result['unread'] >= 0 else 0,
         result['error'], int(profile_id)))


