"""Учётки анкет: список, заведение, правка, удаление.

Работа на сайте знакомств идёт от анкеты: под одной отвечают в чате, под другой
разбирают почту. Учётки лежат здесь, а не в голове у человека, и привязаны к
сайту-закладке (`browser_site`) — одна и та же почта на разных сайтах это разные
анкеты.

**Пароль наружу не отдаётся.** В списке приходит только `has_password`: странице
он не нужен, а утечь через журнал, скриншот или чужой глаз не должен. В форму
входа его подставляет сервер, беря из базы в момент прогона.

## Ручки анкеты разложены на три файла

Здесь — только CRUD, то есть работа с **нашей строкой в нашей базе**. Всё
остальное лежит рядом:

| Файл | Что в нём | Чем отличается |
|------|-----------|----------------|
| `dating_profile.py` | список, заведение, правка, удаление | своя база, наружу не ходит |
| `dating_profile_action.py` | вход, проверка, отправка, опрос, автоответ | ходит к сайту и к соседу, идёт секундами и минутами |
| `dating_profile_view.py` | переписка, журнал, карточка | только читает свою базу для страниц панели |

Граница проведена по тому, **куда файл ходит**: правка логина не может упасть
из-за недоступного соседа, а вход не может не ходить наружу. Пока всё лежало
одним файлом на 856 строк, это было незаметно — и роутер совмещал три разные
ответственности (`docs-common/code_rules.md`, §4).

Общее у трёх файлов — две функции отсюда: `dating_profile_row` (строка или 404) и
`dating_profile_site` (сайт-закладка или 422). Они здесь, потому что это про нашу
запись и её связь с сайтом, а не про действие над ней.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.mysql_.repository.dating_profile_repository import DatingProfileRepository
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_get
from src.dating.dating_json import dating_json_load
from src.dating.dating_schedule import DATING_SCHEDULE_DEFAULT, dating_schedule_dump
from src.dating.dating_revive import dating_revive_state

router = APIRouter(prefix='/api/dating/profile', tags=['dating'])


class DatingProfileSave(BaseModel):
    """Тело заведения и правки учётки."""

    site_id: int = Field(..., gt=0, description='Сайт-закладка, к которому относится анкета')
    login: str = Field(..., min_length=1, max_length=190)
    password: str = Field('', max_length=255, description='Пусто при правке — прежний остаётся')
    note: str = Field('', max_length=255)
    # Роль решает, каким сценарием входить: мужской и женский кабинеты — разные
    # приложения на одном домене. По умолчанию женская: анкеты заводят под неё, а
    # форма заведения на «Анкетах» роль не спрашивает и это поле не шлёт.
    role: str = Field('lady', pattern='^(lady|man)$',
                      description='Чей кабинет на сайте: lady | man')

    @field_validator('login', 'note')
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class DatingProfileResponse(BaseModel):
    """Учётка в списке. Пароля здесь нет и не будет."""

    id: int = Field(..., description='Идентификатор учётки', examples=[3])
    site_id: int = Field(..., description='Сайт-закладка', examples=[19])
    login: str = Field(..., description='Логин или почта', examples=['natalia@example.com'])
    role: str = Field('lady', description='Чей кабинет на сайте: lady | man')
    name: str = Field('', description='Имя из анкеты; пусто — анкету ещё не читали')
    site_uid: str = Field('', description='Номер анкеты на сайте; по нему её ищут в панели сайта')
    photo: str = Field('', description='Фотография из анкеты, полный адрес')
    has_password: bool = Field(..., description='Пароль задан — под учёткой можно входить')
    has_state: bool = Field(False, description='Вход снят: опрос идёт без браузера')
    contacts: int = Field(0, description='Сколько собеседниц писало в чат')
    unread_chat: int = Field(0, description='Непрочитанных сообщений чата — новые с прошлого просмотра')
    enabled: bool = Field(True, description='Ведём ли анкету: выключенную воркер не трогает вовсе')
    auto_answer: bool = Field(True, description='Анкета отвечает сама, пока подключена')
    auto_answer_only: str = Field('', description='Кому отвечать: номера через запятую; пусто — всем')
    status: str = Field('offline', description='online | offline | error — для индикатора')
    unread: int = Field(0, description='Непрочитанных на момент последнего опроса; -1 — не спрашивали')
    last_seen_at: datetime | None = Field(None, description='Когда сайт последний раз подтвердил вход')
    last_error: str = Field('', description='Чем кончился неудачный опрос')
    note: str = Field('', description='Пометка человека')
    keep_online: bool = Field(False, description='Под присмотром автовхода — держится в сети сама')
    revive_state: str = Field('ok', description='ok | failing | given_up — состояние автовхода')
    relogin_next_at: datetime | None = Field(
        None, description='Срок следующей попытки входа по лестнице отката; не признак тревоги')

    model_config = ConfigDict(json_schema_extra={
        'example': {'id': 3, 'site_id': 19, 'login': 'natalia@example.com',
                    'has_password': True, 'note': 'основная анкета'}})


class DatingProfileListResponse(BaseModel):
    result: list[DatingProfileResponse] = Field(..., description='Учётки сайта по алфавиту')


@router.get(
    '',
    response_model=DatingProfileListResponse,
    summary='Получить список учёток сайта знакомств',
    description='Учётки, заведённые для сайта-закладки (`browser_site`). Пароль в ответ '
                'не входит — только признак `has_password`. Нужна сессия админки или '
                'заголовок `X-API-KEY` с правом `DATING_VIEW`.',
    response_description='Успешный ответ со списком учёток',
)
async def dating_profile_list(site_id: int = 0,
                           user=Depends(auth_required)):
    """Учётки сайта. Без `site_id` — все, чтобы страница могла показать общий счёт."""
    where, params = '', []
    if int(site_id or 0):
        where = ' WHERE site_id = %s'
        params.append(int(site_id))

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT g.id, g.site_id, g.login, g.note, g.role, g.status, g.unread, g.last_seen_at, '
            '       g.last_error, g.profile, g.auto_answer, g.auto_answer_only, g.enabled, '
            '       g.keep_online, g.relogin_since, g.relogin_next_at, NOW() AS db_now, '
            '       g.password <> %s AS has_password, '
            '       g.storage_state IS NOT NULL AS has_state, '
            '       (SELECT COUNT(DISTINCT m.sender_uid) FROM dating_message m '
            '         WHERE m.profile_id = g.id) AS contacts, '
            '       (SELECT COUNT(*) FROM dating_message m '
            "         WHERE m.profile_id = g.id AND m.direction = 'in' AND m.is_read = 0) AS unread_chat "
            f'  FROM dating_profile g{where.replace(" site_id", " g.site_id")} ORDER BY g.login',
            tuple([''] + params))
        rows = await db.fetchall()

    for row in rows or []:
        row['has_password'] = bool(row.get('has_password'))
        row['has_state'] = bool(row.get('has_state'))
        row['auto_answer'] = bool(row.get('auto_answer'))
        # Умолчание `True`, а не `False`: колонка появилась при 64 работающих
        # анкетах, и строка без неё (старый снимок, свой SELECT) должна читаться
        # как «ведём», иначе панель разом покажет всех выключенными.
        row['enabled'] = bool(row.get('enabled', 1))
        row['keep_online'] = bool(row.get('keep_online'))
        # Состояние считает сервер: страница иначе повторяла бы арифметику суток,
        # и «мигает или горит ровно» решалось бы в двух местах по-разному.
        #
        # Время — из той же строки (`NOW()`), а не `datetime.now()`: все времена
        # анкеты живут по часам базы, и сутки тревоги обязаны считаться ими же.
        # Часы контейнера и MySQL расходятся молча — `TZ` не задан ни в одном
        # compose, а прод-MySQL стоит на чужом сервере, — и «сдались» показывалось
        # бы на час раньше или позже срока, который просил заказчик.
        row['revive_state'] = dating_revive_state(row.pop('relogin_since', None),
                                                  row.pop('db_now', None) or datetime.now())
        # Имя и фотография — в список: карточку узнают по лицу, а не по номеру.
        # Анкета целиком отдаётся отдельной ручкой: в списке из неё нужны два поля.
        profile = dating_json_load(row.pop('profile', None))
        row['name'] = str(profile.get('name') or '')
        row['photo'] = str(profile.get('photo') or '')
        # Номер анкеты на сайте — в список: по нему ищут в поиске страницы, и по
        # нему же приходят просьбы «посмотри анкету 643694». Кладёт его импорт
        # (`dating_import`), у заведённых руками он пуст.
        row['site_uid'] = str(profile.get('site_uid') or '')

    return {'result': rows}


@router.post('', status_code=201, include_in_schema=False)
async def dating_profile_create(data: DatingProfileSave,
                             user=Depends(auth_required)):
    """Заводит учётку. Тот же логин на том же сайте — 409, а не второй такой же."""
    await dating_profile_site(data.site_id)

    try:
        profile_id = await DatingProfileRepository().add({
            'site_id': data.site_id, 'login': data.login,
            'password': data.password, 'note': data.note, 'role': data.role,
            # ⚠ Расписание сразу: анкету заводят, чтобы она работала. Пустое
            # означало бы, что каждую надо настраивать руками, прежде чем она
            # войдёт хоть раз, — а умолчание в базе для JSON-колонки не поставить.
            #
            # Поймано тестом: миграция проставила расписание существующим анкетам,
            # а заведение новой про него не знало.
            'schedule': dating_schedule_dump(DATING_SCHEDULE_DEFAULT)})
    except Exception as e:
        raise HTTPException(status_code=409, detail=_save_error(e, data.login))

    return {'result': profile_id}


@router.put('/{profile_id}', include_in_schema=False)
async def dating_profile_update(profile_id: int, data: DatingProfileSave,
                             user=Depends(auth_required)):
    """Правит учётку. Пустой пароль оставляет прежний: форма его не показывает,
    и пустое поле означает «не трогать», а не «стереть»."""
    await dating_profile_row(profile_id)
    await dating_profile_site(data.site_id)

    values = {'site_id': data.site_id, 'login': data.login,
              'note': data.note, 'role': data.role}
    if data.password:
        values['password'] = data.password

    try:
        await DatingProfileRepository().update(profile_id, values)
    except Exception as e:
        raise HTTPException(status_code=409, detail=_save_error(e, data.login))

    return {'status': 'success'}


@router.delete('/{profile_id}', include_in_schema=False)
async def dating_profile_delete(profile_id: int,
                             user=Depends(auth_required)):
    """Удаляет учётку. Живые сессии браузера не трогает — они сами по себе."""
    await dating_profile_row(profile_id)
    await DatingProfileRepository().delete(profile_id)

    return {'status': 'success'}


async def dating_profile_row(profile_id: int) -> dict:
    """Учётка из базы или 404 — правка несуществующей молча ничего не делает."""
    row = await DatingProfileRepository().find(profile_id)
    if not row:
        raise HTTPException(status_code=404, detail='учётка не найдена')

    return dict(row)


async def dating_profile_site(site_id: int) -> dict:
    """Сайт-закладка, к которой относится учётка. Живёт в RhapsodyAi.

    Своей таблицы сайтов здесь нет и быть не должно: закладки, сценарии и пул
    Chromium — хозяйство соседа, и вторая копия настройки разошлась бы с первой
    в тот день, когда сайт переверстает форму входа.

    В `dating_login` живёт вторая такая же функция, и это не дубль под склейку:
    здесь отказ превращается в `HTTPException` с кодом, а там — в
    `DatingLoginError`, потому что модуль входа зовут из воркера `rq`, где FastAPI
    нет вовсе. Слить их значит либо втащить FastAPI в воркер, либо потерять коды
    ответа у ручек.
    """
    try:
        row = await rhapsody_api_site_get(int(site_id))
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if not row:
        raise HTTPException(status_code=422, detail=f'нет сайта с номером {site_id}')

    return dict(row)


def _save_error(error: Exception, login: str) -> str:
    """Человеческая причина отказа вместо текста драйвера."""
    text = str(error)
    if '1062' in text or 'Duplicate entry' in text:
        return f'учётка «{login}» на этом сайте уже заведена'

    return f'не удалось сохранить учётку: {text}'
