"""Учётки девушек на сайтах знакомств и вход под ними в живом браузере.

Работа на сайте знакомств идёт от анкеты: под одной отвечают в чате, под другой
разбирают почту. Учётки лежат здесь, а не в голове у человека, и привязаны к
сайту-закладке (`browser_site`) — одна и та же почта на разных сайтах это разные
анкеты.

**Пароль наружу не отдаётся.** В списке приходит только `has_password`: странице
он не нужен, а утечь через журнал, скриншот или чужой глаз не должен. В форму
входа его подставляет сервер, беря из базы в момент прогона.

«Залогиниться» — не отдельная механика, а прогон сценария входа этого сайта
(`browser_scenario`) в живой сессии: логин и пароль уходят туда `secrets`, то
есть мимо базы и журнала прогонов. Сценарий ищется по подстановкам, которые он
ждёт: тот, кому нужны и логин, и пароль, и есть вход.
"""

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.mysql_.repository.dating_girl_repository import DatingGirlRepository
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_agent_find, rhapsody_api_error_write,
                                       rhapsody_api_scenario_find_by_id, rhapsody_api_scenario_login_find,
                                       rhapsody_api_scenario_run, rhapsody_api_session_close,
                                       rhapsody_api_session_list, rhapsody_api_session_open,
                                       rhapsody_api_session_state, rhapsody_api_site_get)
from src.dating.dating_chat import dating_chat_listen
from src.dating.dating_agent import dating_agent_ensure
from src.dating.dating_answer import dating_answer_run
from src.dating.dating_check import dating_check_run
from src.dating.dating_dialog import dating_dialog_sync
from src.dating.dating_disconnect import dating_disconnect_run
from src.dating.dating_log import dating_log_write
from src.dating.dating_profile import dating_profile_refresh
from src.dating.dating_send import dating_send_message
from src.dating.dating_profile_page import dating_profile_page_read
from src.dating.dating_presence import dating_presence_probe, dating_presence_ready
from src.dating.dating_ws import dating_ws_capture

router = APIRouter(prefix='/api/dating/girl', tags=['dating'])

# Имена подстановок, по которым узнаётся сценарий входа. Сайты называют поля
# по-разному, поэтому список, а не одно имя.
DATING_GIRL_LOGIN_KEYS = ('login', 'username', 'email', 'user')
DATING_GIRL_PASSWORD_KEYS = ('password', 'pass', 'pwd')

# Сколько диалогов отдаём в панель. Больше пятидесяти за раз никто не просматривает,
# а страница на них заметно тяжелеет: у каждого ещё и последние десять реплик.
DATING_GIRL_CONTACTS_LIMIT = 50

# Сколько учёток опрашиваем разом. Замер: один запрос к сайту идёт ~0.6с, значит
# двести учёток раз в 15 секунд требуют девяти параллельных — берём с запасом.
DATING_GIRL_PROBE_PARALLEL = 16


class DatingGirlSave(BaseModel):
    """Тело заведения и правки учётки."""

    site_id: int = Field(..., gt=0, description='Сайт-закладка, к которому относится анкета')
    login: str = Field(..., min_length=1, max_length=190)
    password: str = Field('', max_length=255, description='Пусто при правке — прежний остаётся')
    note: str = Field('', max_length=255)

    @field_validator('login', 'note')
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class DatingGirlLogin(BaseModel):
    """Вход под учёткой: в какой сессии и каким сценарием."""

    session_id: str = Field('', description='Пусто — заведётся своя сессия на адрес сайта')
    scenario_id: int | None = Field(None, description='Пусто — сценарий входа ищется сам')


class DatingGirlResponse(BaseModel):
    """Учётка в списке. Пароля здесь нет и не будет."""

    id: int = Field(..., description='Идентификатор учётки', examples=[3])
    site_id: int = Field(..., description='Сайт-закладка', examples=[19])
    login: str = Field(..., description='Логин или почта', examples=['natalia@example.com'])
    name: str = Field('', description='Имя из анкеты; пусто — анкету ещё не читали')
    photo: str = Field('', description='Фотография из анкеты, полный адрес')
    has_password: bool = Field(..., description='Пароль задан — под учёткой можно входить')
    has_state: bool = Field(False, description='Вход снят: опрос идёт без браузера')
    contacts: int = Field(0, description='Сколько собеседниц писало в чат')
    unread_chat: int = Field(0, description='Непрочитанных сообщений чата — новые с прошлого просмотра')
    auto_answer: bool = Field(True, description='Анкета отвечает сама, пока подключена')
    auto_answer_only: str = Field('', description='Кому отвечать: номера через запятую; пусто — всем')
    status: str = Field('offline', description='online | offline | error — для индикатора')
    unread: int = Field(0, description='Непрочитанных на момент последнего опроса; -1 — не спрашивали')
    last_seen_at: datetime | None = Field(None, description='Когда сайт последний раз подтвердил вход')
    last_error: str = Field('', description='Чем кончился неудачный опрос')
    note: str = Field('', description='Пометка человека')

    model_config = ConfigDict(json_schema_extra={
        'example': {'id': 3, 'site_id': 19, 'login': 'natalia@example.com',
                    'has_password': True, 'note': 'основная анкета'}})


class DatingGirlListResponse(BaseModel):
    result: list[DatingGirlResponse] = Field(..., description='Учётки сайта по алфавиту')


@router.get(
    '',
    response_model=DatingGirlListResponse,
    summary='Получить список учёток сайта знакомств',
    description='Учётки, заведённые для сайта-закладки (`browser_site`). Пароль в ответ '
                'не входит — только признак `has_password`. Нужна сессия админки или '
                'заголовок `X-API-KEY` с правом `DATING_VIEW`.',
    response_description='Успешный ответ со списком учёток',
)
async def dating_girl_list(site_id: int = 0,
                           user=Depends(auth_required)):
    """Учётки сайта. Без `site_id` — все, чтобы страница могла показать общий счёт."""
    where, params = '', []
    if int(site_id or 0):
        where = ' WHERE site_id = %s'
        params.append(int(site_id))

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT g.id, g.site_id, g.login, g.note, g.status, g.unread, g.last_seen_at, '
            '       g.last_error, g.profile, g.auto_answer, g.auto_answer_only, '
            '       g.password <> %s AS has_password, '
            '       g.storage_state IS NOT NULL AS has_state, '
            '       (SELECT COUNT(DISTINCT m.sender_uid) FROM dating_message m '
            '         WHERE m.girl_id = g.id) AS contacts, '
            '       (SELECT COUNT(*) FROM dating_message m '
            "         WHERE m.girl_id = g.id AND m.direction = 'in' AND m.is_read = 0) AS unread_chat "
            f'  FROM dating_girl g{where.replace(" site_id", " g.site_id")} ORDER BY g.login',
            tuple([''] + params))
        rows = await db.fetchall()

    for row in rows or []:
        row['has_password'] = bool(row.get('has_password'))
        row['has_state'] = bool(row.get('has_state'))
        row['auto_answer'] = bool(row.get('auto_answer'))
        # Имя и фотография — в список: карточку узнают по лицу, а не по номеру.
        # Анкета целиком отдаётся отдельной ручкой: в списке из неё нужны два поля.
        profile = _json_load(row.pop('profile', None))
        row['name'] = str(profile.get('name') or '')
        row['photo'] = str(profile.get('photo') or '')

    return {'result': rows}


@router.post('', status_code=201, include_in_schema=False)
async def dating_girl_create(data: DatingGirlSave,
                             user=Depends(auth_required)):
    """Заводит учётку. Тот же логин на том же сайте — 409, а не второй такой же."""
    await _site_check(data.site_id)

    try:
        girl_id = await DatingGirlRepository().add({
            'site_id': data.site_id, 'login': data.login,
            'password': data.password, 'note': data.note})
    except Exception as e:
        raise HTTPException(status_code=409, detail=_save_error(e, data.login))

    return {'result': girl_id}


@router.put('/{girl_id}', include_in_schema=False)
async def dating_girl_update(girl_id: int, data: DatingGirlSave,
                             user=Depends(auth_required)):
    """Правит учётку. Пустой пароль оставляет прежний: форма его не показывает,
    и пустое поле означает «не трогать», а не «стереть»."""
    await _girl_row(girl_id)
    await _site_check(data.site_id)

    values = {'site_id': data.site_id, 'login': data.login, 'note': data.note}
    if data.password:
        values['password'] = data.password

    try:
        await DatingGirlRepository().update(girl_id, values)
    except Exception as e:
        raise HTTPException(status_code=409, detail=_save_error(e, data.login))

    return {'status': 'success'}


@router.delete('/{girl_id}', include_in_schema=False)
async def dating_girl_delete(girl_id: int,
                             user=Depends(auth_required)):
    """Удаляет учётку. Живые сессии браузера не трогает — они сами по себе."""
    await _girl_row(girl_id)
    await DatingGirlRepository().delete(girl_id)

    return {'status': 'success'}


@router.post('/{girl_id}/login', include_in_schema=False)
async def dating_girl_login(girl_id: int, data: DatingGirlLogin,
                            user=Depends(auth_required)):
    """Входит под учёткой в живой сессии браузера.

    Сессию заводим свою на адрес сайта, если её не передали: кнопку жмут, чтобы
    оказаться внутри, а не чтобы узнать, что сессию надо создать самому. Гасить
    её потом нельзя — ради неё всё и затевалось (`keep_session`).

    Логин и пароль уходят `secrets`: в базу прогонов и в трассу они не попадают,
    там остаётся `{{password}}` (раздел «Переменные и секреты» в `browser.md`).
    """
    girl = await _girl_row(girl_id)
    site = await _site_check(int(girl['site_id']))
    if not str(girl.get('password') or ''):
        raise HTTPException(status_code=422, detail='у учётки не задан пароль')

    scenario = await _scenario_login(int(girl['site_id']), data.scenario_id)
    if not scenario:
        raise HTTPException(
            status_code=422,
            detail='для сайта нет сценария входа: заведите его на вкладке «Сценарии» '
                   'с подстановками логина и пароля')

    # Прежнюю сессию этой анкеты гасим: сайт держит одну на учётку и при втором
    # входе выбрасывает первую. Две живые вкладки кончались тем, что обе теряли
    # вход, а страница показывала «сессия завершена» на ровном месте.
    # Журнал ведём по ходу, а не итогом: вход идёт до пары минут, и всё это время
    # страница показывала бы пустоту. Каждая строка — ответ на вопрос «где оно
    # сейчас», ради которого человек и смотрит в журнал.
    await dating_log_write(girl_id, 'info', 'Вход запущен')

    closed = await _session_close_old(girl['login'], data.session_id)
    if closed:
        await dating_log_write(girl_id, 'info', f'Прежних вкладок закрыто: {closed}')

    session_id = data.session_id or await _session_open(site, girl['login'])
    secrets = {name: (girl['login'] if name in DATING_GIRL_LOGIN_KEYS else girl['password'])
               for name in scenario['keys']}

    await dating_log_write(girl_id, 'info', f"Прохожу форму входа сценарием «{scenario['name']}»")

    result = await rhapsody_api_scenario_run(int(scenario['id']), session_id, {}, secrets, True)

    profile = {}
    if result.get('status') == 'error':
        await dating_log_write(girl_id, 'fail',
                               f"Вход не прошёл на шаге {result.get('step_no', 0)}: "
                               f"{str(result.get('error') or '')[:300]}")
    else:
        await dating_log_write(girl_id, 'ok', 'Форма входа пройдена, жду кабинета')
        # Анкета читается первой не ради анкеты: её сценарий ждёт появления
        # кабинета, то есть настоящего конца входа. Нажатие кнопки «войти» ещё
        # не сессия — снятое сразу после него состояние приходило без cookie
        # сайта, и опрос честно отвечал «сайт не признал вход».
        profile = await _profile_page(await _girl_row(girl_id), _json_load(site.get('presence')),
                                      session_id)
        # Снимаем вход: дальше присутствие держит HTTP-опрос по этим cookie, и
        # второй раз через браузер логиниться не придётся.
        await _state_save(girl_id, session_id)

        # И тут же спрашиваем сайт, признал ли он вход. Сценарий этого не скажет:
        # его последние шаги — ожидания кабинета, и они помечены необязательными,
        # потому что у мужской и женской анкеты кабинет разный. Из-за этого
        # сценарий отчитывался успехом и на неверном пароле: сайт показывал «Bad
        # credentials», а карточка получала бесполезное состояние и выглядела
        # рабочей до первой попытки что-нибудь отправить.
        #
        # Опрос годится обеим ролям — в отличие от чтения анкеты, сценарий которой
        # заведён только для женской.
        verify = await dating_presence_probe(await _girl_row(girl_id),
                                             _json_load(site.get('presence')))
        if verify['status'] != 'online':
            # Состояние от неудачного входа стираем: держать cookie, по которым
            # сайт не пускает, значит показывать в карточке вход, которого нет.
            await _state_clear(girl_id)
            await dating_log_write(girl_id, 'fail',
                                   f"Сайт вход не признал: {verify['error']}")
            await _session_close(session_id)
            await rhapsody_api_error_write(
                'dating', f"вход под «{girl['login']}» не признан сайтом: {verify['error']}",
                context={'girl_id': girl_id, 'site_id': girl['site_id'],
                         'scenario_id': scenario['id'], 'where': 'проверка входа опросом'})

            return {'result': {'status': 'error', 'session_id': '',
                               'scenario': scenario['name'], 'error': verify['error'],
                               'profile': {}}}

        await dating_log_write(girl_id, 'ok',
                               'Вход признан сайтом, состояние снято — опрос пойдёт без браузера')

        # И сразу новый адрес чат-сокета: токены в прежнем привязаны к прежней
        # сессии, и после входа он мёртв. Без этого держатель вечно висел на
        # мёртвом адресе — соединение выглядело живым, а сообщения уходили в
        # «пропущенные». Снимаем в этой же вкладке: вторая выбила бы первую.
        await dating_log_write(girl_id, 'info', 'Снимаю адрес чат-сокета')
        ws_url = await _ws_capture(await _girl_row(girl_id), session_id)
        await dating_log_write(
            girl_id, 'ok' if ws_url else 'fail',
            'Адрес чат-сокета снят, чат подхватит держатель в течение минуты' if ws_url
            else 'Адрес чат-сокета снять не вышло — живой чат работать не будет')

        # И закрываем вкладку. Сайт отдаёт сообщение одному соединению, а
        # оставленная страница чата — тоже соединение: она молча забирала
        # входящие себе, и держатель не получал ничего. Вход нужен ради cookie и
        # адреса сокета, а не ради открытого окна.
        await _session_close(session_id)
        session_id = ''

    if result.get('status') == 'error':
        await rhapsody_api_error_write(
            'dating', f"вход под «{girl['login']}» не прошёл: {result.get('error', '')}",
            session_id=session_id,
            context={'girl_id': girl_id, 'site_id': girl['site_id'],
                     'scenario_id': scenario['id'], 'step_no': result.get('step_no', 0)})

    return {'result': {'status': result.get('status'), 'session_id': session_id,
                       'scenario': scenario['name'], 'error': result.get('error', ''),
                       'profile': profile}}


class DatingGirlSend(BaseModel):
    """Ручная отправка сообщения собеседнику."""

    to: str = Field(..., min_length=1, max_length=32, description='Номер собеседника на сайте')
    text: str = Field(..., min_length=1, max_length=2000)


@router.post('/{girl_id}/send', include_in_schema=False)
async def dating_girl_send(girl_id: int, data: DatingGirlSend,
                           user=Depends(auth_required)):
    """Шлёт сообщение от имени анкеты — руками, без агента.

    Нужно там, где агент ни при чём: ответить самому, проверить доставку, дослать
    сорвавшееся. Путь тот же, что у агента, поэтому проверка здесь проверяет
    настоящую отправку, а не её подобие.
    """
    girl = await _girl_row(girl_id)
    site = await _site_check(int(girl['site_id']))

    result = await dating_send_message(girl, _json_load(site.get('presence')), data.to, data.text)

    await dating_log_write(girl_id, 'out' if result['ok'] else 'fail',
                           f"#{data.to}: {data.text[:300]}" if result['ok']
                           else f"Не ушло #{data.to}: {result['error']}")

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"сообщение не ушло: {result['error']}",
                                  context={'girl_id': girl_id, 'to': data.to})

    return {'result': result}


class DatingGirlAuto(BaseModel):
    """Переключатель автоответа у анкеты."""

    auto_answer: bool = Field(..., description='Отвечать ли самостоятельно')
    auto_answer_only: str | None = Field(
        None, max_length=500,
        description='Кому отвечать: номера через запятую. Пусто — всем; не передано — как было')


@router.put('/{girl_id}/auto', include_in_schema=False)
async def dating_girl_auto(girl_id: int, data: DatingGirlAuto, request: Request,
                           user=Depends(auth_required)):
    """Включает или выключает автоответ анкеты.

    Включение сразу разбирает накопившееся: человек нажал «отвечать» — значит
    ждать до следующего круга смены (до минуты) незачем, а неотвеченное письмо
    висит уже сейчас.
    """
    girl = await _girl_row(girl_id)

    async with mysql_get_db_async() as db:
        if data.auto_answer_only is None:
            await db.execute('UPDATE dating_girl SET auto_answer = %s WHERE id = %s',
                             (1 if data.auto_answer else 0, girl_id))
        else:
            await db.execute('UPDATE dating_girl SET auto_answer = %s, auto_answer_only = %s '
                             ' WHERE id = %s',
                             (1 if data.auto_answer else 0, data.auto_answer_only.strip(), girl_id))
        await db.connection.commit()

    girl = await _girl_row(girl_id)

    await dating_log_write(girl_id, 'info',
                           'Автоответ включён' if data.auto_answer else 'Автоответ выключен')

    answered = {}
    if data.auto_answer:
        site = await _site_check(int(girl['site_id']))
        agent = await dating_agent_ensure(girl, _json_load(girl.get('profile')))
        agent_id = await _agent_id_by_name(agent['name'])
        if agent_id:
            answered = await dating_answer_run({**girl, 'auto_answer': 1},
                                               _json_load(site.get('presence')),
                                               agent_id)

    return {'result': {'auto_answer': data.auto_answer, 'answered': answered}}


@router.post('/{girl_id}/answer', include_in_schema=False)
async def dating_girl_answer(girl_id: int, request: Request,
                             user=Depends(auth_required)):
    """Отвечает собеседникам, чьё сообщение осталось без ответа.

    Текст сочиняет агент анкеты через `/chat/ask`; сюда он приходит готовым и
    уходит в чат. Пустой ответ означает «промолчать» — сообщение помечается
    отвеченным, но в чат ничего не идёт.
    """
    girl = await _girl_row(girl_id)
    site = await _site_check(int(girl['site_id']))
    presence = _json_load(site.get('presence'))

    agent = await dating_agent_ensure(girl, _json_load(girl.get('profile')))
    agent_id = await _agent_id_by_name(agent['name'])
    if not agent_id:
        raise HTTPException(status_code=422,
                            detail=f"у анкеты нет агента «{agent['name']}»: {agent['error']}")

    result = await dating_answer_run(girl, presence, agent_id)

    for error in result['errors']:
        await rhapsody_api_error_write('dating', f'ответ не отправлен — {error}',
                                  context={'girl_id': girl_id, 'agent_id': agent_id,
                                           'where': 'кнопка «Ответить»'})

    return {'result': {**result, 'agent': agent['name']}}


@router.post('/{girl_id}/dialog', include_in_schema=False)
async def dating_girl_dialog(girl_id: int,
                             user=Depends(auth_required)):
    """Читает диалоги учётки со страницы чата и складывает переписку.

    Нужно потому, что сокет отдаёт только пришедшее при нём: всё, что было раньше,
    сайт в чат не возвращает. Открывать страницу дорого и она отбирает у учётки
    живое соединение, поэтому это кнопка, а не фон.
    """
    girl = await _girl_row(girl_id)
    site = await _site_check(int(girl['site_id']))
    presence = _json_load(site.get('presence'))

    await _session_close_old(girl['login'], '')
    session_id = await _session_open_for(site, {**girl,
                                                'storage_state': _json_load(girl.get('storage_state'))})

    result = await dating_dialog_sync(girl, presence, session_id, rhapsody_api_scenario_run)

    if result['error']:
        await rhapsody_api_error_write('dating', f"диалоги не прочитались: {result['error']}",
                                  session_id=session_id,
                                  context={'girl_id': girl_id, 'where': 'чтение диалогов'})

    # Страница чата отобрала у учётки сокет — возвращаем его сразу, иначе живой
    # приём молчал бы до ближайшей переподвязки держателя. И гасим вкладку: пока
    # она открыта, входящие достаются ей, а не держателю.
    await _ws_capture(await _girl_row(girl_id), session_id)
    await _session_close(session_id)

    return {'result': result}


@router.post('/{girl_id}/disconnect', include_in_schema=False)
async def dating_girl_disconnect(girl_id: int,
                                 user=Depends(auth_required)):
    """Отключает учётку: выход на сайте, закрытие вкладок, снятие с мониторинга.

    Не удаление: переписка, журнал и анкета остаются, а вернуть учётку в работу —
    это «Залогиниться». Выход на самом сайте необязателен и не отменяет локальную
    часть: сайт может не ответить, а отключить учётку надо именно тогда.
    """
    girl = await _girl_row(girl_id)
    site = await _site_check(int(girl['site_id']))

    result = await dating_disconnect_run(
        {**girl, 'storage_state': _json_load(girl.get('storage_state'))},
        site, _json_load(site.get('presence')),
        rhapsody_api_scenario_run, _session_open_for, _session_all, _session_close)

    if result['error']:
        await rhapsody_api_error_write('dating', f"выход на сайте не прошёл: {result['error']}",
                                  context={'girl_id': girl_id, 'site_id': girl['site_id']})

    return {'result': result}


@router.post('/probe', include_in_schema=False)
async def dating_girl_probe(site_id: int = 0,
                            user=Depends(auth_required)):
    """Опрашивает учётки сайта: держит онлайн и снимает счётчики.

    Идёт по HTTP с сохранёнными cookie, без браузера — замер на живом сайте:
    вкладка Chromium стоит ~143 МБ, а клиент с cookie единицы килобайт. Ради
    этого страница и зовёт эту ручку раз в 15 секунд, а не гоняет сценарии.

    Опрашиваются разом: у двухсот учёток последовательный круг занял бы минуты,
    а параллельный укладывается в секунды (`DATING_GIRL_PROBE_PARALLEL`).
    """
    site = await _site_check(site_id) if site_id else None
    presence = _json_load((site or {}).get('presence'))
    if not dating_presence_ready(presence):
        raise HTTPException(status_code=422,
                            detail='у сайта не настроен опрос присутствия (browser_site.presence)')

    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, login, status, storage_state FROM dating_girl '
                         ' WHERE site_id = %s AND storage_state IS NOT NULL', (int(site_id),))
        girls = [dict(row) for row in (await db.fetchall() or [])]

    if not girls:
        return {'result': {'checked': 0, 'online': 0}}

    limit = asyncio.Semaphore(DATING_GIRL_PROBE_PARALLEL)

    async def probe(girl):
        async with limit:
            return girl, await dating_presence_probe(girl, presence)

    done = await asyncio.gather(*(probe(girl) for girl in girls), return_exceptions=False)

    async with mysql_get_db_async() as db:
        for girl, result in done:
            # `last_seen_at` двигаем только у подтверждённого входа: у него смысл
            # «когда сайт последний раз признал учётку», а не «когда мы стучались».
            seen = 'NOW()' if result['status'] == 'online' else 'last_seen_at'
            await db.execute(
                f'UPDATE dating_girl SET status = %s, unread = %s, last_error = %s, '
                f'       last_seen_at = {seen} WHERE id = %s',
                (result['status'],
                 result['unread'] if result['unread'] >= 0 else 0,
                 result['error'], girl['id']))
        await db.connection.commit()

    # В журнал ошибок — только смена состояния, а не каждый неудачный круг: опрос
    # идёт раз в 15 секунд, и сотня учёток залила бы вкладку за минуту одним и тем
    # же. Текущее состояние и так видно на карточке.
    for girl, result in done:
        if result['status'] != 'online' and str(girl.get('status') or '') == 'online':
            await rhapsody_api_error_write('dating', f"учётка «{girl['login']}» ушла из сети: "
                                                f"{result['error'] or result['status']}",
                                      context={'girl_id': girl['id'], 'site_id': site_id,
                                               'where': 'опрос присутствия'})

    online = sum(1 for _, r in done if r['status'] == 'online')

    return {'result': {'checked': len(done), 'online': online,
                       'girls': [{'id': g['id'], 'status': r['status'], 'unread': r['unread'],
                                  'error': r['error'], 'ms': r['ms']} for g, r in done]}}


@router.get('/{girl_id}/message', include_in_schema=False)
async def dating_girl_messages(girl_id: int, limit: int = 30,
                               user=Depends(auth_required)):
    """Сообщения чата этой учётки, свежие сверху."""
    await _girl_row(girl_id)
    limit = max(1, min(int(limit or 30), 200))

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, sender_uid, sender_name, text, is_read, created_at '
            f'FROM dating_message WHERE girl_id = %s ORDER BY id DESC LIMIT {limit}', (girl_id,))
        rows = await db.fetchall()

    return {'result': rows}


@router.post('/{girl_id}/read', include_in_schema=False)
async def dating_girl_read(girl_id: int,
                           user=Depends(auth_required)):
    """Помечает сообщения учётки прочитанными — человек открыл её карточку.

    Право на просмотр, а не на правку: пометка «я это видел» — часть чтения, и
    требовать за неё право создавать значило бы запретить смотреть без него.
    """
    await _girl_row(girl_id)

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE dating_message SET is_read = 1 "
                         " WHERE girl_id = %s AND direction = 'in' AND is_read = 0", (girl_id,))
        read = db.rowcount
        await db.connection.commit()

    return {'result': {'read': read}}


@router.post('/{girl_id}/listen', include_in_schema=False)
async def dating_girl_listen(girl_id: int, seconds: int = 30,
                             user=Depends(auth_required)):
    """Слушает чат-сокет учётки и складывает входящие сообщения.

    Адрес сокета снимается браузером, если его ещё нет: в разметке страницы его
    нет, собирает его JS. Дальше подключение держит обычный клиент — вкладка
    Chromium стоила бы ~143 МБ, а сокет десятки килобайт.

    Ручка слушает ограниченное время и возвращает итог: постоянное подключение —
    работа воркера, а страница дёргает её, когда человек смотрит на карточку.
    """
    girl = await _girl_row(girl_id)
    if not str(girl.get('ws_url') or ''):
        girl['ws_url'] = await _ws_capture(girl)
        if not girl['ws_url']:
            raise HTTPException(status_code=422,
                                detail='не удалось снять адрес чат-сокета: сначала «Залогиниться»')

    result = await dating_chat_listen(girl, seconds=max(5, min(int(seconds or 30), 120)))

    # Протухший адрес — обычное дело: токены в нём живут не вечно. Снимаем заново
    # и пробуем ещё раз, чтобы человеку не приходилось гадать, почему пусто.
    if result.get('error') and not result.get('frames'):
        girl['ws_url'] = await _ws_capture(girl)
        if girl['ws_url']:
            result = await dating_chat_listen(girl, seconds=max(5, min(int(seconds or 30), 120)))

    if result.get('error'):
        await rhapsody_api_error_write('dating', f"чат-сокет: {result['error']}",
                                  context={'girl_id': girl_id})

    return {'result': result}


@router.post('/{girl_id}/check', include_in_schema=False)
async def dating_girl_check(girl_id: int, request: Request,
                            user=Depends(auth_required)):
    """Полная проверка учётки: вход, чат, чтение, пробная отправка, закрытие.

    Каждый шаг пишется в журнал (`dating_log`) — по нему на странице видно, где
    именно встало. Профиль и собеседницы обновляются заодно: раз уж вошли, брать
    их отдельным заходом незачем.
    """
    girl = await _girl_row(girl_id)
    site = await _site_check(int(girl['site_id']))
    presence = _json_load(site.get('presence'))
    if not dating_presence_ready(presence):
        raise HTTPException(status_code=422, detail='у сайта не настроен опрос присутствия')

    result = await dating_check_run(girl, presence, _ws_capture)

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"проверка учётки «{girl['login']}» встала: {result['error']}",
                                  context={'girl_id': girl_id, 'site_id': girl['site_id'],
                                           'steps': result['steps'], 'where': 'кнопка «Проверка»'})

    agent = {}
    if result['ok']:
        girl = await _girl_row(girl_id)
        refreshed = await dating_profile_refresh(girl, presence)
        await dating_log_write(girl_id, 'ok' if not refreshed['error'] else 'fail',
                               f"Профиль обновлён, собеседниц {refreshed['contacts']}"
                               if not refreshed['error'] else f"Профиль не обновился: {refreshed['error']}")

        # Последним шагом — агент анкеты: отвечать в чате будет он, и заводить
        # его руками на каждую новую девушку значит рано или поздно забыть.
        # Проверка идёт по имени: агента могли удалить или переименовать.
        #
        # Строку перечитываем: анкету только что обновили, а из неё берётся
        # биография, из которой workflow добывает факты. Со старой в руках
        # агент завёлся бы по вчерашнему тексту — или вовсе без него.
        girl = await _girl_row(girl_id)
        agent = await dating_agent_ensure(girl, _json_load(girl.get('profile')))
        if agent['error']:
            await rhapsody_api_error_write('dating', f"агент анкеты не заведён: {agent['error']}",
                                      context={'girl_id': girl_id, 'agent': agent['name']})

    result['agent'] = agent

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_girl SET status = %s, last_error = %s WHERE id = %s',
                         ('online' if result['ok'] else 'error', result['error'], girl_id))
        await db.connection.commit()

    return {'result': result}


@router.get('/{girl_id}/log', include_in_schema=False)
async def dating_girl_log(girl_id: int, limit: int = 100,
                          user=Depends(auth_required)):
    """Журнал по учётке, свежие сверху."""
    await _girl_row(girl_id)
    limit = max(1, min(int(limit or 100), 500))

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, level, text, created_at FROM dating_log '
            f' WHERE girl_id = %s ORDER BY id DESC LIMIT {limit}', (girl_id,))
        rows = await db.fetchall()

    return {'result': rows}


@router.get('/{girl_id}/profile', include_in_schema=False)
async def dating_girl_profile(girl_id: int,
                             user=Depends(auth_required)):
    """Профиль учётки и её собеседницы с последними сообщениями.

    Сообщений на собеседницу берём десять и обеими сторонами: диалог читают как
    разговор, а не как список чужих реплик. Отдаём в порядке разговора — сверху
    старое, снизу свежее, как в самом чате.

    `needs_answer` — последнее слово осталось за собеседником. Это и есть работа,
    которую видно в карточке: не «сколько всего сообщений», а «кому не ответили».
    """
    girl = await _girl_row(girl_id)

    # Список ведём **от переписки**, а не от таблицы собеседников. Раньше он шёл
    # от `dating_contact`, куда попадают только те, кого отдал сайт своим списком
    # «кто онлайн» (двести штук), — и диалог с человеком, которого в том списке
    # нет, в панель не попадал вовсе. Счётчик же считался по переписке, поэтому
    # цифры расходились втрое: 549 в счётчике против 59 показанных.
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.sender_uid AS uid, COUNT(*) AS messages, MAX(m.id) AS last_id, '
            '       COALESCE(c.name, MAX(m.sender_name)) AS name, '
            '       c.photo, c.age, c.city, c.is_online '
            '  FROM dating_message m '
            '  LEFT JOIN dating_contact c ON c.girl_id = m.girl_id AND c.uid = m.sender_uid '
            ' WHERE m.girl_id = %s '
            ' GROUP BY m.sender_uid, c.name, c.photo, c.age, c.city, c.is_online '
            # По свежести, а не по числу сообщений: работа идёт с теми, кто писал
            # недавно, а не с теми, кто когда-то написал больше всех.
            ' ORDER BY last_id DESC LIMIT %s', (girl_id, DATING_GIRL_CONTACTS_LIMIT))
        contacts = [dict(row) for row in (await db.fetchall() or [])]

        for contact in contacts:
            await db.execute(
                'SELECT text, direction, is_answered, created_at FROM dating_message '
                ' WHERE girl_id = %s AND sender_uid = %s ORDER BY id DESC LIMIT 10',
                (girl_id, contact['uid']))
            last = [dict(row) for row in (await db.fetchall() or [])]
            # Разворачиваем: выбирали свежие, а читать надо сверху вниз.
            contact['messages_last'] = list(reversed(last))
            # Ждёт ответа тот, чья последняя реплика входящая **и** не помечена
            # отвеченной. Пометка нужна отдельно от направления: агент может
            # намеренно промолчать, и тогда диалог закрыт, хотя последнее слово
            # осталось за собеседником.
            contact['needs_answer'] = bool(last and last[0]['direction'] == 'in'
                                           and not last[0]['is_answered'])

    return {'result': {'profile': _json_load(girl.get('profile')),
                       'login': girl['login'], 'contacts': contacts}}


async def _agent_id_by_name(name: str) -> int:
    """Номер агента у соседа. Своей таблицы агентов здесь нет: они, workflow и
    факты — хозяйство RhapsodyAi, и вторая копия расходилась бы с первой."""
    try:
        found = await rhapsody_api_agent_find(str(name))
    except RhapsodyError as e:
        logger_info(f'[dating] агент «{name}» не спросился: {e}')
        return 0

    return int((found or {}).get('id') or 0)


async def _profile_page(girl: dict, presence: dict, session_id: str) -> dict:
    """Читает анкету со страницы кабинета и пишет итог в журнал учётки.

    Неудача входа не роняет: анкета — украшение карточки, а вход уже состоялся, и
    отменять его из-за нечитаемой анкеты значило бы отобрать у человека сессию.
    """
    result = await dating_profile_page_read(girl, presence, session_id, rhapsody_api_scenario_run)
    await dating_log_write(int(girl['id']), 'ok' if result['ok'] else 'info',
                           f"Анкета прочитана: {result['profile'].get('name', '')}"
                           if result['ok'] else f"Анкета не прочиталась: {result['error']}")

    if not result['ok']:
        await rhapsody_api_error_write('dating', f"анкета не прочиталась: {result['error']}",
                                  session_id=session_id,
                                  context={'girl_id': girl['id'], 'where': 'чтение анкеты'})

    return result['profile'] if result['ok'] else {}


async def _ws_capture(girl: dict, session_id: str = '') -> str:
    """Снимает адрес чат-сокета учётки — общей механикой, а не своей копией.

    Тот же перехват нужен держателю сокетов (`dating_chat_hold`), и две копии кода
    разошлись бы первой же правкой: одна половина системы обновляла бы адрес,
    другая держалась за старый.
    """
    site = await _site_check(int(girl['site_id']))
    url = await dating_ws_capture({**girl, 'storage_state': _json_load(girl.get('storage_state'))},
                                  _json_load(site.get('presence')), site['url'], session_id)
    if not url:
        await rhapsody_api_error_write('dating', 'адрес чат-сокета не снялся',
                                  context={'girl_id': girl['id']})

    return url


async def _state_save(girl_id: int, session_id: str) -> None:
    """Снимает `storage_state` сессии и кладёт его учётке.

    Не роняет вход, если не получилось: сценарий уже отработал, и терять его
    результат из-за неснятого состояния незачем — снимем при следующем входе.
    """
    try:
        state = await rhapsody_api_session_state(session_id)
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_girl SET storage_state = %s, status = %s, '
                             '       last_seen_at = NOW(), last_error = %s WHERE id = %s',
                             (json.dumps(state, ensure_ascii=False), 'online', '', girl_id))
            await db.connection.commit()
    except Exception as e:
        await rhapsody_api_error_write('dating', f'состояние входа не снялось: {e}',
                                  session_id=session_id, context={'girl_id': girl_id})


async def _state_clear(girl_id: int) -> None:
    """Стирает состояние входа: сайт по нему не пускает, и держать его вредно.

    Заодно гасит адрес сокета: он снят в той же мёртвой сессии, и держатель чата
    иначе будет ломиться по нему, показывая живое соединение вместо неудачи.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_girl SET storage_state = NULL, ws_url = %s, '
                             '       status = %s, last_error = %s WHERE id = %s',
                             ('', 'error', 'сайт не признал вход', girl_id))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] состояние входа не стёрлось: {e}')


async def _session_close_old(login: str, keep_session_id: str) -> int:
    """Гасит живые сессии этой анкеты, кроме той, куда попросили войти.

    Не роняет вход, если контейнер недоступен: сессии — его хозяйство, и вход по
    сценарию всё равно попробуется.
    """
    closed = 0
    for session in await _session_all():
        if str(session.get('name') or '') != str(login) or session.get('session_id') == keep_session_id:
            continue
        try:
            await _session_close(str(session.get('session_id') or ''))
            closed += 1
        except Exception as e:
            await rhapsody_api_error_write('dating', f'прежняя сессия не закрылась: {e}',
                                      session_id=str(session.get('session_id') or ''))

    return closed


async def _session_all() -> list:
    """Живые сессии контейнера. Недоступен — пустой список, а не исключение:
    вызывающие умеют работать без браузера, а падать из-за него не должны."""
    try:
        return await rhapsody_api_session_list()
    except Exception:
        return []


async def _session_close(session_id: str) -> None:
    """Гасит вкладку. Уже погасла — не беда: важен итог, а не кто её закрыл."""
    if not session_id:
        return

    try:
        await rhapsody_api_session_close(session_id)
    except Exception as e:
        logger_info(f'[dating] вкладка {session_id} не закрылась: {e}')


async def _session_open(site: dict, name: str) -> str:
    """Заводит сессию браузера на адрес сайта — под именем учётки, чтобы её было
    видно в полосе вкладок `/admin/browser`."""
    return await rhapsody_api_session_open(url=site['url'], name=name[:100],
                                          site_id=int(site['id']))


async def _session_open_for(site: dict, girl: dict) -> str:
    """Сессия под уже снятый вход учётки — без прохода по форме.

    Нужна отключению: выходить на сайте надо из авторизованной вкладки, а живой
    вкладки к этому моменту может уже не быть.
    """
    return await rhapsody_api_session_open(url=site['url'], name=str(girl['login'])[:100],
                                          site_id=int(site['id']),
                                          storage_state=girl.get('storage_state') or {})


async def _scenario_login(site_id: int, scenario_id: int | None) -> dict | None:
    """Сценарий входа этого сайта: тот, что ждёт и логин, и пароль.

    Ищет его RhapsodyAi — там сценарии и лежат. Явно переданный `scenario_id`
    берётся как есть: человек лучше знает, чем он входит.
    """
    try:
        if scenario_id:
            scenario = await rhapsody_api_scenario_find_by_id(int(scenario_id))
        else:
            scenario = await rhapsody_api_scenario_login_find(int(site_id))
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not scenario:
        return None

    return {'id': scenario['id'], 'name': scenario.get('name', ''),
            'keys': list(DATING_GIRL_LOGIN_KEYS[:1] + DATING_GIRL_PASSWORD_KEYS[:1])}


async def _girl_row(girl_id: int) -> dict:
    """Учётка из базы или 404 — правка несуществующей молча ничего не делает."""
    row = await DatingGirlRepository().find(girl_id)
    if not row:
        raise HTTPException(status_code=404, detail='учётка не найдена')

    return dict(row)


async def _site_check(site_id: int) -> dict:
    """Сайт-закладка, к которой относится учётка. Живёт в RhapsodyAi.

    Своей таблицы сайтов здесь нет и быть не должно: закладки, сценарии и пул
    Chromium — хозяйство соседа, и вторая копия настройки разошлась бы с первой
    в тот день, когда сайт переверстает форму входа.
    """
    try:
        row = await rhapsody_api_site_get(int(site_id))
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not row:
        raise HTTPException(status_code=422, detail=f'нет сайта с номером {site_id}')

    return dict(row)


def _json_load(value) -> dict:
    """JSON-столбец приезжает то структурой, то строкой — зависит от драйвера."""
    if isinstance(value, str):
        try:
            return json.loads(value or '{}')
        except Exception:
            return {}

    return value if isinstance(value, dict) else {}


def _save_error(error: Exception, login: str) -> str:
    """Человеческая причина отказа вместо текста драйвера."""
    text = str(error)
    if '1062' in text or 'Duplicate entry' in text:
        return f'учётка «{login}» на этом сайте уже заведена'

    return f'не удалось сохранить учётку: {text}'
