"""Собеседники сайта: карточка человека, которому пишут.

Рассылка уходит тем, кто **сейчас в сети**, и письмо должно спрашивать про их
интересы. Знать интересы неоткуда: списки «Все онлайн» живут неделю в Redis и в
базу не ложатся, а `dating_contact` — это собеседницы конкретной анкеты, с
которыми уже есть переписка. Рассылка пишет незнакомым.

## ⚠ Карточка заводится по требованию, а не списком целиком

Людей в сети на живом сайте сто с лишним, всего на сайте — тысячи. Свалить их в
базу разом значило бы хранить тысячи карточек, из которых понадобятся единицы:
письмо уходит тем, кого выбрали.

Поэтому строка появляется, когда о человеке **спросили**: нажали на него в ленте
«Все онлайн» или до него дошла очередь рассылки. Заводится она из того, что лента
и так знает, — номер, имя, фото, возраст, город, страна. Этого хватает, чтобы
карточка была; подробности добавятся позже.

## ⚠ Два разных «пусто» у описания

`about_state` различает «не собирали» и «сходили, там ничего нет». Одним пустым
полем их не отличить, а решения они требуют разных: в первом случае идти
собирать, во втором — не ходить второй раз.

Сейчас состояние всегда `none`: собирать нечем. API сайта описания не отдаёт —
проверено живьём на LadaDate, и список, и попытки достать карточку одного
человека дают только номер, имя, возраст, дату рождения, страну, город и фото.
Подробности снимает браузер, и сценария «карточка мужчины» у соседа ещё нет.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.dating_mailing import dating_mailing_companion_vars
from src.dating.dating_online import dating_online_show

router = APIRouter(prefix='/api/dating/companion', tags=['dating'])

# Пределы — по колонкам `dating_companion`.
DATING_COMPANION_UID_MAX = 32
DATING_COMPANION_NAME_MAX = 190
DATING_COMPANION_PHOTO_MAX = 500
DATING_COMPANION_ABOUT_MAX = 4000

# Сколько карточек отдаём странице разом. Список читают глазами, и трёхсотая строка
# не помогает никому: страница листает его своим пейджером, а не тянет всё сразу.
#
# ⚠ **Вместе со списком уезжает `total` — полный счёт по сайту.** Без него счётчик
# в шапке показывал длину **выдачи**, то есть замирал на потолке: карточек 410, а
# на странице вечные «200», и выглядело это как «собеседники перестали
# добавляться». Считать их на странице нечем — она видит только присланное.
DATING_COMPANION_LIMIT = 200


class DatingCompanionSave(BaseModel):
    """Карточка собеседника — то, что о нём известно из ленты.

    ⚠ **Всё, кроме сайта и номера, необязательно.** Лента даёт разный набор: у
    кого-то нет города, у кого-то возраста. Требовать их значило бы отказывать в
    заведении карточки из-за поля, которого у сайта нет.
    """

    site_id: int = Field(..., gt=0, description='Сайт, которому принадлежит человек')
    uid: str = Field(..., min_length=1, max_length=DATING_COMPANION_UID_MAX,
                     description='Номер человека на сайте')
    name: str = Field('', max_length=DATING_COMPANION_NAME_MAX)
    photo: str = Field('', max_length=DATING_COMPANION_PHOTO_MAX)
    age: int = Field(0, ge=0, le=150)
    city: str = Field('', max_length=DATING_COMPANION_NAME_MAX)
    country: str = Field('', max_length=DATING_COMPANION_NAME_MAX)


class DatingCompanionEdit(BaseModel):
    """Что правят руками: почта и описание.

    Отдельно от заведения, потому что источники разные. Заведение приносит то, что
    отдала лента, и повторяется при каждом заходе; правка — работа человека, и
    затирать её лентой нельзя.
    """

    email: str = Field('', max_length=DATING_COMPANION_NAME_MAX)
    about: str = Field('', max_length=DATING_COMPANION_ABOUT_MAX)


@router.get('', include_in_schema=False)
async def dating_companion_list(site_id: int = 0, find: str = '',
                                page: int = 0,
                                user: AuthUser = Depends(auth_required)):
    """Собеседники сайта — свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не все собеседники всех
            сайтов: страница без выбранного сайта не должна показывать чужое.
        find: поиск по имени и номеру. Режем на сервере, а не на странице: карточек
            накопятся сотни, и слать их целиком ради поиска незачем.
        page: какую сотню отдать, с нуля. Карточек у живого сайта уже за четыре
            сотни, и одна выдача их не вмещает.

    Returns:
        dict: `result` — карточки, `total` — **сколько их всего** по этому отбору,
        `page` и `limit` — что именно отдали.

        ⚠ `total` обязателен: без него счётчик в шапке показывал длину выдачи и
        замирал на потолке — «200» при четырёхстах карточках выглядело как
        «собеседники перестали добавляться».

    ⚠ **Порядок — по дате появления карточки, а не по последней правке.** Прежде
    сортировали по `updated_at`, и список перетасовывался под руками: допишешь
    описание — карточка прыгает наверх, а соседняя, которую собирался открыть
    следующей, уезжает вниз.

    Свежие сверху — это те, о ком спросили последними, то есть те, с кем сейчас и
    работают. Правка этого не меняет: человек тот же, узнали о нём тогда же.
    """
    if not int(site_id or 0):
        return {'result': [], 'total': 0, 'limit': DATING_COMPANION_LIMIT}

    sql = ('SELECT id, site_id, uid, name, photo, age, city, country, email,'
           ' priority, about, about_state, about_error, about_at, created_at,'
           ' updated_at FROM dating_companion WHERE site_id = %s')
    args = [int(site_id)]

    find = str(find or '').strip()
    if find:
        sql += ' AND (name LIKE %s OR uid LIKE %s)'
        args += [f'%{find}%', f'%{find}%']

    # ⚠ Счёт берётся **тем же отбором**, что и список, — включая поиск: «найдено 3
    # из 410» отвечало бы на вопрос, которого никто не задавал.
    считаем = 'SELECT COUNT(*) AS n FROM' + sql.split('FROM', 1)[1]

    sql += ' ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s'
    страница = max(0, int(page or 0))
    args_rows = args + [DATING_COMPANION_LIMIT, страница * DATING_COMPANION_LIMIT]

    async with mysql_get_db_async() as db:
        await db.execute(считаем, tuple(args))
        total = int((await db.fetchone())['n'])

        await db.execute(sql, tuple(args_rows))
        rows = await db.fetchall()

    # ⚠ **В сети или нет — из снимка ленты, а не походом на сайт.** Лента «Все
    # онлайн» снимается своим кругом и живёт в Redis; сходи мы за ней ради списка
    # карточек, открытие вкладки стоило бы сорока секунд браузера.
    #
    # Отсюда и три состояния вместо двух: `known: False` значит «снимка нет или он
    # протух» — и это не то же самое, что «не в сети». Точку в таком случае страница
    # не рисует вовсе: серая точка читается как «оффлайн», а мы не знаем.
    return {'result': [_answer(row, dating_online_show(int(site_id), row['uid']))
                       for row in rows],
            'total': total,
            'page': страница,
            'limit': DATING_COMPANION_LIMIT}


@router.post('', include_in_schema=False)
async def dating_companion_save(payload: DatingCompanionSave,
                                user: AuthUser = Depends(auth_required)):
    """Заводит карточку собеседника или обновляет то, что о нём знает лента.

    Returns:
        dict: карточка целиком — страница показывает её сразу, не перечитывая
        список.

    ⚠ **Правку человека лента не затирает.** `email` и `about` здесь не
    упоминаются вовсе: их правят руками (`PUT /{id}`), а этот вызов повторяется
    при каждом заходе на человека — и стирал бы написанное каждый раз.

    ⚠ **Пустое поле не затирает известное.** Лента отдаёт разный набор, и заход,
    в котором города не оказалось, не должен стирать город, снятый прошлым.
    Поэтому `COALESCE`-подобная склейка: пустое значение оставляет прежнее.

    Открыто всем вошедшим: это работа с людьми сайта, ровно та, ради которой
    панель и открывают.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_companion (site_id, uid, name, photo, age, city, country)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE'
            # ⚠ `IF(%s = '', name, %s)` вместо простого присваивания: пустое
            # значение из ленты означает «не знаю», а не «сотри».
            "  name = IF(VALUES(name) = '', name, VALUES(name)),"
            "  photo = IF(VALUES(photo) = '', photo, VALUES(photo)),"
            '  age = IF(VALUES(age) = 0, age, VALUES(age)),'
            "  city = IF(VALUES(city) = '', city, VALUES(city)),"
            "  country = IF(VALUES(country) = '', country, VALUES(country))",
            (int(payload.site_id), payload.uid.strip(), payload.name.strip(),
             payload.photo.strip(), int(payload.age), payload.city.strip(),
             payload.country.strip()))
        await db.connection.commit()

    return {'result': await _row_by_uid(int(payload.site_id), payload.uid.strip())}


@router.put('/{companion_id}', include_in_schema=False)
async def dating_companion_edit(companion_id: int, payload: DatingCompanionEdit,
                                user: AuthUser = Depends(auth_required)):
    """Правит почту и описание — то, что заполняет человек.

    Raises:
        HTTPException: 404 — такой карточки нет.

    ⚠ **`about_state` ставится в `done`, если текст есть.** Состояние отвечает на
    вопрос «искали ли подробности», и вписанный руками текст — такой же ответ, как
    снятый браузером: искать второй раз незачем. Стёрли текст — состояние
    возвращается в `none`: значит, за ним ещё сходят.
    """
    await _row(companion_id)

    about = str(payload.about or '').strip()

    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE dating_companion SET email = %s, about = %s, about_state = %s,'
            " about_error = '', about_at = NOW() WHERE id = %s",
            (payload.email.strip(), about, 'done' if about else 'none',
             int(companion_id)))
        await db.connection.commit()

    return {'result': await _row(companion_id)}


class DatingCompanionPriority(BaseModel):
    """Отметка «писать этому в первую очередь»."""

    priority: bool = Field(..., description='Приоритет в рассылке')


@router.put('/{companion_id}/priority', include_in_schema=False)
async def dating_companion_priority(companion_id: int,
                                    payload: DatingCompanionPriority,
                                    user: AuthUser = Depends(auth_required)):
    """Включает и выключает приоритет собеседника в рассылке.

    ⚠ **Своей ручкой, а не полем в общей правке.** `PUT /{id}` пишет почту и
    описание разом, и галочка, уехавшая вместе с ними, гасла бы при каждом
    сохранении текста — который правят чаще. Отдельная ручка отвечает на отдельный
    вопрос и переключается одним щелчком, без формы.

    ⚠ **Это порядок, а не отбор.** Отмеченные идут первыми, остальные — следом.
    Сделай мы из этого фильтр, отметка у одного человека молча выключила бы рассылку
    всем прочим.
    """
    await _row(companion_id)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_companion SET priority = %s WHERE id = %s',
                         (1 if payload.priority else 0, int(companion_id)))
        await db.connection.commit()

    return {'result': await _row(companion_id)}


@router.get('/{companion_id}/var', include_in_schema=False)
async def dating_companion_vars(companion_id: int,
                                user: AuthUser = Depends(auth_required)):
    """Чем подставятся переменные писем для этого человека.

    Returns:
        dict: список записей — `name`, `title`, `field` (поле карточки, откуда
        берётся значение), `value`, `from_default` и `filled`.

    Raises:
        HTTPException: 404 — такой карточки нет.

    ⚠ **Своего хранилища у этих значений нет** — они выводятся из карточки:
    `companion_name` это `name` адресата. Ручка отвечает на вопрос «уйдёт ли письмо
    этому человеку и как в нём обратятся», а правят при этом саму карточку.

    Тем и отличается от переменных анкеты (`/api/dating/profile/{id}/var`): там
    значения хранят и проставляют, здесь — вычисляют и показывают.
    """
    # ⚠ Реестр берём **лениво**: импорт наверху свёл бы два роутера в круг —
    # `dating_mail_var` уже знает про карточки собеседников.
    from src.uvicorn.routers.dating.dating_mail_var import dating_mail_var_rows

    row = await _row(companion_id)
    known = await dating_mail_var_rows(int(row['site_id']))

    return {'result': dating_mailing_companion_vars(_answer(row), known)}


async def dating_companion_get(site_id: int, uid: str) -> dict:
    """Карточка человека для кода. Нет такой — пустой словарь.

    Понадобится рассылке: она спросит, что известно про адресата, чтобы подставить
    `companion`-переменные и решить, есть ли чем.
    """
    found = await _row_by_uid(int(site_id), str(uid).strip(), soft=True)

    return found or {}


@router.delete('/{companion_id}', include_in_schema=False)
async def dating_companion_drop(companion_id: int,
                                user: AuthUser = Depends(auth_required)):
    """Убирает карточку собеседника.

    Raises:
        HTTPException: 404 — такой карточки нет.

    ⚠ **Прежде удаления здесь не было, и это было осознанным решением**: карточки
    накапливают, а не удаляют — человек на сайте никуда не девается. Решение
    оказалось неполным. Карточка заводится по щелчку в ленте и сбором описания, то
    есть иногда — по ошибке или о том, кто больше не нужен; а список читают глазами,
    и лишняя запись в нём мешает так же, как лишняя строка везде.

    ⚠ **Письма в пуле переживут удаление.** `dating_mailing.uid` — просто строка,
    внешнего ключа на карточку нет: письмо помнит, кому оно, само по себе. Иначе
    удаление карточки уносило бы готовую рассылку.

    ⚠ **Описание при этом теряется навсегда**, а стоило оно ~50 секунд живого
    браузера. Поэтому страница спрашивает подтверждение и говорит об этом прямо.
    """
    await _row(companion_id)

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_companion WHERE id = %s',
                         (int(companion_id),))
        await db.connection.commit()

    return {'result': {'id': int(companion_id)}}


async def dating_companion_touch(site_id: int, uid: str, **fields) -> dict:
    """Дописывает в карточку то, что стало известно. Нет карточки — заводит.

    Args:
        **fields: любые из `about`, `about_state`, `about_error`, `city`,
            `country`, `name`, `photo`, `age`. Что не назвали — не трогаем.

    Returns:
        dict: карточка после правки.

    ⚠ **Правим только названное.** Сведения о человеке приходят из разных мест:
    имя и фото — из ленты, описание и город — со страницы анкеты. Пиши мы всю
    строку целиком, каждый источник затирал бы чужое своими пустотами.

    ⚠ Для кода, а не ручкой: собирает описание круг рассылки, у которого ни сессии,
    ни прав нет. Ручка `POST` рядом — про другое: там лента заводит карточку по
    щелчку человека.
    """
    uid = str(uid or '').strip()
    if not uid:
        return {}

    # Карточки может не быть вовсе: описание собирают и для того, кого в лентах
    # ещё не видели.
    await _ensure(int(site_id), uid)

    known = ('about', 'about_state', 'about_error', 'city', 'country',
             'name', 'photo', 'age')
    parts, args = [], []
    for name in known:
        if name not in fields:
            continue

        parts.append(f'{name} = %s')
        args.append(int(fields[name] or 0) if name == 'age' else str(fields[name] or ''))

    if 'about_state' in fields:
        # Время ответа сайта — вместе с состоянием: по нему видно, давно ли
        # спрашивали, и оно же отвечает на «не пора ли ещё раз».
        parts.append('about_at = NOW()')

    if parts:
        args.extend([int(site_id), uid])
        async with mysql_get_db_async() as db:
            await db.execute(
                f"UPDATE dating_companion SET {', '.join(parts)}"
                ' WHERE site_id = %s AND uid = %s', tuple(args))
            await db.connection.commit()

    return await dating_companion_get(int(site_id), uid)


# ── Приватное ────────────────────────────────────────────────────────────────

async def _ensure(site_id: int, uid: str) -> None:
    """Заводит пустую карточку, если её ещё нет. `INSERT IGNORE` ловит гонку."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO dating_companion (site_id, uid, name)'
            " VALUES (%s, %s, '')", (int(site_id), str(uid)))
        await db.connection.commit()


async def _row(companion_id: int) -> dict:
    """Карточка по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, uid, name, photo, age, city, country, email,'
            ' priority, about, about_state, about_error, about_at, created_at,'
            ' updated_at FROM dating_companion WHERE id = %s', (int(companion_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'карточки #{companion_id} нет')

    return _answer(row, dating_online_show(int(row['site_id']), row['uid']))


async def _row_by_uid(site_id: int, uid: str, soft: bool = False) -> dict:
    """Карточка по сайту и номеру. `soft` — пустой словарь вместо 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, uid, name, photo, age, city, country, email,'
            ' priority, about, about_state, about_error, about_at, created_at,'
            ' updated_at FROM dating_companion WHERE site_id = %s AND uid = %s',
            (int(site_id), str(uid)))
        row = await db.fetchone()

    if not row:
        if soft:
            return {}

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'карточки {uid} на сайте #{site_id} нет')

    return _answer(row, dating_online_show(int(site_id), uid))


def _answer(row: dict, online: dict = None) -> dict:
    """Строка базы в ответ странице."""
    about = str(row['about'] or '')

    return {'id': int(row['id']),
            'site_id': int(row['site_id']),
            'uid': str(row['uid']),
            'name': str(row['name']),
            'photo': str(row['photo']),
            'age': int(row['age'] or 0),
            'city': str(row['city']),
            'country': str(row['country']),
            'email': str(row['email']),
            'priority': bool(row.get('priority')),
            'about': about,
            'about_state': str(row['about_state']),
            'about_error': str(row['about_error']),
            'about_at': row['about_at'],
            # Готов ли человек к письму, которое спрашивает про интересы. Считает
            # сервер, а не страница: правило одно, и вторая копия разошлась бы.
            'has_about': bool(about),
            # ⚠ Два поля, а не одно: «не в сети» и «не знаем» подписываются
            # по-разному, и слить их в `online: False` значило бы соврать про
            # каждого, кого не было в последнем снимке.
            'online': bool((online or {}).get('online')),
            'online_known': bool((online or {}).get('known')),
            # ⚠ Возраст снимка наружу: без него «точек нет» не отличить от «ленту
            # давно не снимали», а это разные вещи — и разные действия человека.
            'online_age': int((online or {}).get('age_sec') or -1),
            'created_at': row['created_at'],
            'updated_at': row['updated_at']}
