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
| `profile_.py` | список, заведение, правка, удаление | своя база, наружу не ходит |
| `profile_action.py` | вход, проверка, отправка, опрос, автоответ | ходит к сайту и к соседу, идёт секундами и минутами |
| `profile_view.py` | переписка, журнал, карточка | только читает свою базу для страниц панели |

Граница проведена по тому, **куда файл ходит**: правка логина не может упасть
из-за недоступного соседа, а вход не может не ходить наружу. Пока всё лежало
одним файлом на 856 строк, это было незаметно — и роутер совмещал три разные
ответственности (`docs-common/code_rules.md`, §4).

Общее у трёх файлов — три функции отсюда: `profile_row` (строка или 404),
`profile_site` (живой сайт-закладка, для тех, кто к нему реально ходит) и
`profile_site_known` (заведён ли сайт, по нашему снимку — для заведения и
правки). Они здесь, потому что это про нашу запись и её связь с сайтом, а не
про действие над ней.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.dating.site_snapshot import site_rows
from src.mysql_.repository.profile_repository import DatingProfileRepository
from src.dating.json_column import json_column_load
from src.dating.proxy import proxy_enabled
from src.dating.schedule import SCHEDULE_DEFAULT, schedule_dump
from src.dating.revive import revive_state

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
    # ⚠ Поле обязано быть **в модели ответа**, а не только в строке: `response_model`
    # выбрасывает всё, чего в ней нет, и молча — страница получала бы `undefined`.
    # Так и вышло на первом прогоне.
    age: int = Field(0, description='Возраст из анкеты; 0 — анкету ещё не читали')
    site_uid: str = Field('', description='Номер анкеты на сайте; по нему её ищут в панели сайта')
    photo: str = Field('', description='Фотография из анкеты, полный адрес')
    has_password: bool = Field(..., description='Пароль задан — под учёткой можно входить')
    has_state: bool = Field(False, description='Вход снят: опрос идёт без браузера')
    contacts: int = Field(0, description='Сколько собеседниц писало в чат')
    unread_chat: int = Field(0, description='Непрочитанных сообщений чата — новые с прошлого просмотра')
    enabled: bool = Field(True, description='Ведём ли анкету: выключенную воркер не трогает вовсе')
    password_source: str = Field('site', description='Откуда пароль: `site` — общий, `own` — свой')
    auto_answer: bool = Field(True, description='Анкета отвечает сама, пока подключена')
    auto_answer_only: str = Field('', description='Кому отвечать: номера через запятую; пусто — всем')
    mailing_on: bool = Field(False, description='Слать ли от анкеты рассылку писем')
    status: str = Field('offline', description='online | offline | error — для индикатора')
    unread: int = Field(0, description='Непрочитанных на момент последнего опроса; -1 — не спрашивали')
    last_seen_at: datetime | None = Field(None, description='Когда сайт последний раз подтвердил вход')
    last_error: str = Field('', description='Чем кончился неудачный опрос')
    note: str = Field('', description='Пометка человека')
    keep_online: bool = Field(False, description='Под присмотром автовхода — держится в сети сама')
    revive_state: str = Field('ok', description='ok | failing | given_up — состояние автовхода')
    relogin_next_at: datetime | None = Field(
        None, description='Срок следующей попытки входа по лестнице отката; не признак тревоги')
    # Выход анкеты наружу. ⚠ **Номер и увиденный адрес, но не строка подключения**:
    # в ней пароль канала, и место ему в клиенте, который через канал идёт, а не
    # в ответе странице (`.claude/docs/proxy.md`).
    #
    # ⚠ Поля обязаны быть **здесь**, а не только в запросе: `response_model`
    # отбрасывает всё, чего нет в схеме, — молча, без единой ошибки. Так и вышло:
    # колонки уже уехали в `SELECT`, а до страницы не доезжали.
    proxy_id: int = Field(0, description='Выход в реестре RhapsodyAi; 0 — не закреплён')
    proxy_ip: str = Field('', description='Каким адресом анкету видит сайт')
    # ⚠ Свойство **сайта**, а не анкеты, и всё же едет в каждой строке: карточка
    # рисуется сразу по списку, и спроси она настройки сайта отдельным запросом —
    # блок «Выход наружу» успел бы мигнуть у сайта, где выходы выключены.
    proxy_on: bool = Field(False, description='Пользуется ли сайт выходами вообще')

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
async def profile_list(site_id: int = 0,
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
            '       g.mailing_on, '
            '       g.password_source, '
            '       g.keep_online, g.relogin_since, g.relogin_next_at, NOW() AS db_now, '
            # Выход анкеты: номер в реестре соседа и адрес, каким её видит сайт.
            # ⚠ **Только это, без `proxy_url`**: в нём пароль канала, и место ему
            # в клиенте, который через канал идёт, а не в ответе странице.
            '       g.proxy_id, g.proxy_ip, '
            '       g.password <> %s AS has_password, '
            '       g.storage_state IS NOT NULL AS has_state, '
            '       (SELECT COUNT(DISTINCT m.sender_uid) FROM message m '
            '         WHERE m.profile_id = g.id) AS contacts, '
            '       (SELECT COUNT(*) FROM message m '
            "         WHERE m.profile_id = g.id AND m.direction = 'in' AND m.is_read = 0) AS unread_chat "
            f'  FROM profile g{where.replace(" site_id", " g.site_id")}'
            # ⚠ **Сперва те, кто в сети, потом по алфавиту.** Список читают,
            # чтобы найти работу, а работа есть у тех, кто на сайте: круги
            # рассылки, инвайтов и переписки с избранными берут только их. При
            # сортировке по логину живая анкета оказывалась двадцатой строкой
            # среди спящих, и найти её можно было лишь поиском.
            #
            # ⚠ **Алфавит — по имени, а не по логину.** Логин это почта
            # (`natalypetranetskaya67@bigmir.net`), и упорядоченный по ней список
            # для человека случаен: в строке он видит имя. Имя лежит в JSON
            # анкеты, поэтому `JSON_EXTRACT`; пусто — падаем на логин, иначе
            # безымянные ушли бы все в начало.
            "  ORDER BY g.status = 'online' DESC,"
            "           COALESCE(NULLIF(JSON_UNQUOTE("
            "                      JSON_EXTRACT(g.profile, '$.name')), ''), g.login)",
            tuple([''] + params))
        rows = await db.fetchall()

    for row in rows or []:
        row['has_password'] = bool(row.get('has_password'))
        row['has_state'] = bool(row.get('has_state'))
        row['auto_answer'] = bool(row.get('auto_answer'))
        # Рассылка писем: по этому флагу «Профили» отбирают анкеты вкладки
        # «Письма рассылка». Без приведения строка отдавала бы 0 и 1, а страница
        # сравнивает с `true`.
        row['mailing_on'] = bool(row.get('mailing_on'))
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
        row['revive_state'] = revive_state(row.pop('relogin_since', None),
                                                  row.pop('db_now', None) or datetime.now())
        # Имя и фотография — в список: карточку узнают по лицу, а не по номеру.
        # Анкета целиком отдаётся отдельной ручкой: в списке из неё нужны немногие
        # поля.
        profile = json_column_load(row.pop('profile', None))
        row['name'] = str(profile.get('name') or '')
        row['photo'] = str(profile.get('photo') or '')
        # ⚠ Возраст — рядом с именем в списке. Анкеты у агентства по именам
        # повторяются (двух Диан и трёх Елен видно прямо сейчас), и различить их в
        # строке больше нечем: логин это почта, номер на сайте — число.
        row['age'] = int(profile.get('age') or 0)
        # Номер анкеты на сайте — в список: по нему ищут в поиске страницы, и по
        # нему же приходят просьбы «посмотри анкету 643694». Кладёт его импорт
        # (`import`), у заведённых руками он пуст.
        # ⚠ Имя поля наружу прежнее — его читает страница («ID 643694» в
        # карточке и отбор в списке), — а источник один: ключ `id` карточки.
        row['site_uid'] = str(profile.get('id') or '')

    # Выключатель выходов — свойство сайта, и спрашиваем мы его **по сайту**, а не
    # по строке: без `site_id` список идёт по всем сайтам сразу, и запрос на
    # каждую из шестидесяти анкет дал бы шестьдесят одинаковых ответов.
    known: dict[int, bool] = {}
    for row in rows or []:
        one = int(row.get('site_id') or 0)
        if one not in known:
            known[one] = await proxy_enabled(one)
        row['proxy_on'] = known[one]

    return {'result': rows}


@router.post('', status_code=201, include_in_schema=False)
async def profile_create(data: DatingProfileSave,
                             user=Depends(auth_required)):
    """Заводит учётку. Тот же логин на том же сайте — 409, а не второй такой же.

    Сайт проверяет наш снимок (`profile_site_known`), а не сосед: строка
    ложится в нашу базу, и висящий RhapsodyAi не должен мешать местной работе.
    """
    await profile_site_known(data.site_id)

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
            'schedule': schedule_dump(SCHEDULE_DEFAULT)})
    except Exception as e:
        raise HTTPException(status_code=409, detail=_save_error(e, data.login))

    return {'result': profile_id}


@router.put('/{profile_id}', include_in_schema=False)
async def profile_update(profile_id: int, data: DatingProfileSave,
                             user=Depends(auth_required)):
    """Правит учётку. Пустой пароль оставляет прежний: форма его не показывает,
    и пустое поле означает «не трогать», а не «стереть»."""
    await profile_row(profile_id)
    await profile_site_known(data.site_id)

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
async def profile_delete(profile_id: int,
                             user=Depends(auth_required)):
    """Удаляет учётку. Живые сессии браузера не трогает — они сами по себе."""
    await profile_row(profile_id)
    await DatingProfileRepository().delete(profile_id)

    return {'status': 'success'}


async def profile_row(profile_id: int) -> dict:
    """Учётка из базы или 404 — правка несуществующей молча ничего не делает."""
    row = await DatingProfileRepository().find(profile_id)
    if not row:
        raise HTTPException(status_code=404, detail='учётка не найдена')

    return dict(row)


async def profile_site(site_id: int) -> dict:
    """Сайт анкеты: номер, имя, адрес и описание — всё из нашей базы.

    Вид ответа прежний (`id`, `name`, `url`, `presence`), потому что его читают
    девять ручок раздела; изменился источник.

    ⚠⚠ **Прежде это был живой запрос карточки у соседа**, и стоил он больше, чем
    казалось. Ручку `/probe` дёргает открытая страница «Анкеты» раз в две минуты,
    `/ping` — по кнопке, а карточка приезжала ради одного `presence`, который
    переехал к нам (`site_presence.py`). По журналу прода: четыре десятка
    обращений в час от одной открытой вкладки.

    Хуже цены была зависимость: `RhapsodyError` превращался здесь в `502`, то есть
    лежащий сосед отнимал **отправку сообщения и проверку входа** — работу,
    которая идёт ручкой самого сайта по нашим cookie и браузера не касается.

    ⚠ **Сосед по-прежнему нужен, но за другим** — за сценариями входа и проверки.
    Там его молчание и видно, там оно и уместно.

    Raises:
        HTTPException: 422 — сайта нет ни в снимке списка, ни в настройках. Это
            «анкета привязана к сайту, которого не существует», то есть ошибка
            запроса, а не сбой шлюза — код прежний.
    """
    from src.dating.site_presence import site_presence
    presence = await site_presence(int(site_id))
    row = next((one for one in await site_rows()
                if int(one['id']) == int(site_id)), None)

    if not row and not presence:
        raise HTTPException(status_code=422, detail=f'нет сайта с номером {site_id}')

    return {'id': int(site_id),
            'name': str((row or {}).get('name') or ''),
            # ⚠ Адрес из снимка, а пусто — из описания: `base` говорит то же
            # самое, и сайт, который ещё не сверяли, иначе остался бы без адреса.
            'url': str((row or {}).get('url') or presence.get('base') or ''),
            'presence': presence}


async def profile_site_known(site_id: int) -> dict:
    """Заведён ли сайт — по нашему снимку (`site_`), без похода к соседу.

    Заведение и правка учётки — работа нашей базы, и сосед нужен им ровно в одном
    факте: что закладка с таким номером существует. Спрашивать его вживую
    значило бы повесить местную работу на чужой uptime: лежащий RhapsodyAi
    отвечал 502 на заведомо правильный номер, а сюита — которая по своему правилу
    к соседу не ходит, — краснела на каждом его перезапуске.

    Снимок для этого как раз годится (§3.5): селект на странице предлагает
    только известные ему номера, а свежесть держит фоновая сверка. Номер, о
    котором снимок не слыхал вовсе, не заведётся и здесь — тот же 422, что
    раньше приходил от соседа.

    ⚠ `is_gone` не спрашиваем, и это не забывчивость. Метку ставит сверка, а она
    спрашивает у соседа список **с меткой** `dating` (`rhapsody_api_site_list`):
    всё, что лежит у него без этой метки — стенды, его собственная админка, —
    в снимке помечено ушедшим, хотя закладка жива. Живую проверку делает каждая
    ручка, которая к соседу реально ходит (`profile_site`).
    Признак пересмотреть: если панель научат заводить анкету на номер, вписанный
    человеком руками, строгую соседскую проверку придётся вернуть.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT site_id, name FROM site WHERE site_id = %s',
                         (int(site_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=422, detail=f'нет сайта с номером {site_id}')

    return dict(row)


def _save_error(error: Exception, login: str) -> str:
    """Человеческая причина отказа вместо текста драйвера."""
    text = str(error)
    if '1062' in text or 'Duplicate entry' in text:
        return f'учётка «{login}» на этом сайте уже заведена'

    return f'не удалось сохранить учётку: {text}'
