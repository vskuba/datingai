"""Переменные писем: реестр по сайту и значения у анкет.

Две вещи, разложенные по двум наборам ручек, потому что вопросы у них разные:

| Ручки | Отвечают на вопрос | Кто правит |
|-------|--------------------|------------|
| `/api/dating/mail/var` | какие переменные бывают на этом сайте | админ |
| `/api/dating/profile/{id}/var` | чему они равны у этой анкеты | все вошедшие |

⚠ **Права разные, и это не случайность.** Завести переменную — решение про сайт:
она появится у каждой анкеты, и письма начнут её требовать. Заполнить значение —
работа с конкретной анкетой, ровно та, ради которой панель и открывают.

## Как это работает вместе

1. человек заводит `firstName` на вкладке «Переменные» страницы «Письма»;
2. она появляется у каждой анкеты сайта — пустой, на вкладке «Переменные» в
   правой колонке «Анкет»;
3. значение проставляют: сейчас руками, потом агентом у соседа, которому отдадут
   сведения об анкете и попросят вернуть переменные;
4. письмо с `[[girl_name]]` уходит только тем анкетам, у которых значение есть, —
   у остальных рассылка его пропускает (`dating_mail_missing`).

## ⚠ Три породы переменных, и у анкеты живёт только одна

`source` различает их:

| `source` | Пример | Откуда значение |
|----------|--------|-----------------|
| `profile` | `girl_name` | лежит у анкеты, заполняется заранее |
| `companion` | `companion_name` | лежит в карточке получателя, читается при отправке |
| `live` | `question_to_companion` | нигде не лежит: вычисляется при отправке |

У двух последних значения в `dating_profile_var` нет вовсе: оно зависит от того,
кому пишем. Строка «имя собеседника у анкеты» означала бы неправду, которая молча
подставилась бы в письмо; строка «вопрос у анкеты» — вопрос, один на всех
получателей, то есть ровно то, от чего `live` и уводит.

Отсюда: вкладка «Переменные» у анкеты показывает **только `profile`**, и агент
заполняет тоже только их.

## ⚠ Значения перечисляются по реестру, а не по своей таблице

Список переменных анкеты собирается из реестра сайта, а значения к нему
приклеиваются. Обратный порядок («покажи, что лежит в `dating_profile_var`»)
скрыл бы главное: переменную завели, а у этой анкеты её нет вовсе — то есть
письмо с ней не уйдёт. Пустая строка в списке об этом говорит, отсутствие строки
молчит.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_admin_required, auth_required
from src.dating.dating_mail_agent import (DatingMailAgentError,
                                          dating_mail_agent_fill)
from src.dating.dating_json import dating_json_load
from src.dating.dating_mail import (DATING_MAIL_VAR_NAME_MAX,
                                    DATING_MAIL_VAR_TITLE_MAX,
                                    DATING_MAIL_VAR_VALUE_MAX,
                                    dating_mail_var_check)

router = APIRouter(tags=['dating'])


class DatingMailVarSave(BaseModel):
    """Тело заведения и правки переменной."""

    site_id: int = Field(..., gt=0, description='Сайт, которому принадлежит реестр')
    name: str = Field(..., min_length=1, max_length=DATING_MAIL_VAR_NAME_MAX,
                      description='Имя латиницей: girl_name, companion_name')
    # ⚠ Откуда берётся значение — три способа, и они разные по природе:
    #
    #   `profile`   — лежит у анкеты, заполняется заранее (руками или агентом);
    #   `companion` — лежит в карточке получателя, **читается** при отправке;
    #   `live`      — не лежит нигде: **вычисляется** при отправке агентом.
    #
    # Последний отличается от второго не формально: `companion_name` можно
    # посмотреть глазами и поправить, а `question_to_companion` существует только
    # в момент отправки и у каждой пары своё.
    source: str = Field('profile', pattern='^(profile|companion|live)$',
                        description='profile — у анкеты, companion — из карточки '
                                    'получателя, live — вычисляется при отправке')
    title: str = Field('', max_length=DATING_MAIL_VAR_TITLE_MAX,
                       description='Подпись для человека и подсказка агенту')
    default_value: str = Field('', max_length=DATING_MAIL_VAR_VALUE_MAX,
                               description='Чем подставлять, если у анкеты пусто')


class DatingProfileVarSave(BaseModel):
    """Значения переменных анкеты — набором, а не по одному.

    ⚠ **Набором намеренно.** Страница держит перед глазами весь список и правит
    его как форму; агент вернёт разом всё, что нашёл. Правка по одному имени
    означала бы запрос на каждое поле и вопрос «а что с остальными».
    """

    values: dict[str, str] = Field(default_factory=dict,
                                   description='Имя переменной → значение')
    # Кто заполнил. Страница шлёт `hand`, приход агента будет слать `agent`.
    # Хранится, чтобы отличить проставленное человеком от угаданного: агент
    # ошибается, и переписывать его правку человека — не то же, что наоборот.
    filled_by: str = Field('hand', pattern='^(hand|agent)$')


# ── Реестр сайта ─────────────────────────────────────────────────────────────

@router.get('/api/dating/mail/var', include_in_schema=False)
async def dating_mail_var_list(site_id: int = 0,
                               user: AuthUser = Depends(auth_required)):
    """Переменные сайта — в порядке заведения.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не все переменные всех
            сайтов: страница без выбранного сайта не должна показывать чужое.

    Порядок по номеру, а не по имени: человек заводит их в том порядке, в каком
    думает о письме, и алфавит развалил бы эту последовательность.
    """
    if not int(site_id or 0):
        return {'result': []}

    return {'result': await dating_mail_var_rows(int(site_id))}


@router.post('/api/dating/mail/var', include_in_schema=False)
async def dating_mail_var_create(payload: DatingMailVarSave,
                                 user: AuthUser = Depends(auth_admin_required)):
    """Заводит переменную.

    Raises:
        HTTPException: 422 — имя не годится; 409 — такая переменная на сайте уже
            есть.

    ⚠ **Одноимённых на сайте не бывает**: письмо ссылается на переменную по
    имени, и две одинаковых сделали бы ссылку неоднозначной. Держит это
    уникальный ключ в базе, а не проверка перед вставкой: два заведения подряд —
    гонка, и ключ ловит её там, где она случается.
    """
    _checked(payload)

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id FROM dating_mail_var WHERE site_id = %s AND name = %s',
            (int(payload.site_id), payload.name.strip()))
        if await db.fetchone():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'переменная «{payload.name.strip()}» на этом сайте уже есть')

        await db.execute(
            'INSERT INTO dating_mail_var (site_id, name, source, title, default_value)'
            ' VALUES (%s, %s, %s, %s, %s)',
            (int(payload.site_id), payload.name.strip(), payload.source,
             payload.title.strip(), payload.default_value))
        await db.connection.commit()
        var_id = db.lastrowid

    return {'result': await _var_row(int(var_id))}


@router.put('/api/dating/mail/var/{var_id}', include_in_schema=False)
async def dating_mail_var_update(var_id: int, payload: DatingMailVarSave,
                                 user: AuthUser = Depends(auth_admin_required)):
    """Правит подпись и умолчание переменной.

    Raises:
        HTTPException: 404 — такой переменной нет; 422 — имя не годится.

    ⚠ **Имя не меняется.** На него ссылаются шаблоны (`[[girl_name]]` в тексте) и
    значения анкет (`dating_profile_var.name`); переименуй мы его здесь — письма
    перестали бы находить переменную, а значения осиротели бы, причём молча.
    Нужно другое имя — заводят новую и убирают старую: тогда видно, что шаблоны
    надо поправить.

    ⚠ **Имя здесь и не проверяется** — в отличие от заведения. Проверять поле,
    которое не применяешь, значит отказывать в правке подписи из-за значения,
    которое никуда не попадёт. Поймано тестом `test_name_does_not_change_on_update`:
    правка падала с 422, хотя менять собиралась только подпись.
    """
    await _var_row(var_id)

    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE dating_mail_var SET title = %s, default_value = %s WHERE id = %s',
            (payload.title.strip(), payload.default_value, int(var_id)))
        await db.connection.commit()

    return {'result': await _var_row(var_id)}


@router.delete('/api/dating/mail/var/{var_id}', include_in_schema=False)
async def dating_mail_var_delete(var_id: int,
                                 user: AuthUser = Depends(auth_admin_required)):
    """Убирает переменную и все её значения у анкет сайта.

    Raises:
        HTTPException: 404 — такой переменной нет.

    ⚠ **Значения уносятся вместе с ней, и это делает код, а не внешний ключ.**
    Связь идёт по имени (`api.md`, §3.7), поэтому `ON DELETE CASCADE` тут
    невозможен: базе не из чего понять, что `dating_profile_var.name = 'firstName'`
    относится к удаляемой строке реестра. Оставь мы их — значения жили бы
    невидимками и всплыли бы у переменной с тем же именем, заведённой заново.

    ⚠ **Шаблоны при этом не правятся.** Письмо с `{{firstName}}` останется как
    есть и просто перестанет отправляться (`dating_mail_missing` увидит нехватку).
    Лезть в чужой текст и вырезать оттуда куски мы не вправе — а тихо испорченный
    шаблон хуже неотправленного письма.
    """
    row = await _var_row(var_id)

    async with mysql_get_db_async() as db:
        # Сперва значения, потом сама переменная: обратный порядок оставил бы
        # значения сиротами, оборвись выполнение между запросами.
        await db.execute(
            'DELETE v FROM dating_profile_var v'
            ' JOIN dating_profile p ON p.id = v.profile_id'
            ' WHERE p.site_id = %s AND v.name = %s',
            (int(row['site_id']), row['name']))
        await db.execute('DELETE FROM dating_mail_var WHERE id = %s', (int(var_id),))
        await db.connection.commit()

    return {'result': {'id': int(var_id), 'name': row['name']}}


# ── Значения у анкеты ────────────────────────────────────────────────────────

@router.get('/api/dating/profile/{profile_id}/var', include_in_schema=False)
async def dating_profile_var_list(profile_id: int,
                                  user: AuthUser = Depends(auth_required)):
    """Переменные анкеты: весь реестр её сайта со значениями.

    Returns:
        dict: `result` — список `{name, title, default_value, value, filled_by,
        filled}`. `filled` — есть ли чем подставлять: своё значение или умолчание.

    Raises:
        HTTPException: 404 — такой анкеты нет.

    ⚠ **Перечисляем по реестру, а не по своей таблице.** Переменная, заведённая на
    сайте, но не заполненная у этой анкеты, обязана быть видна пустой строкой:
    именно из-за неё письмо и не уйдёт. Спроси мы `dating_profile_var`, такой
    переменной в ответе не было бы вовсе — и страница молчала бы о главном.

    ⚠ **Только `profile`-переменные.** У `companion` значение зависит от адресата
    и появляется при отправке; показать его у анкеты значило бы соврать, а дать
    поле для правки — предложить вписать неправду.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id FROM dating_profile WHERE id = %s',
                         (int(profile_id),))
        profile = await db.fetchone()
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f'анкеты #{profile_id} нет')

        await db.execute(
            'SELECT name, value, filled_by FROM dating_profile_var'
            ' WHERE profile_id = %s', (int(profile_id),))
        mine = {row['name']: row for row in await db.fetchall()}

    rows = []
    for var in await dating_mail_var_rows(int(profile['site_id'])):
        if var['source'] != 'profile':
            continue

        found = mine.get(var['name']) or {}
        value = str(found.get('value') or '')

        rows.append({**var,
                     'value': value,
                     'filled_by': str(found.get('filled_by') or ''),
                     # ⚠ Есть чем подставить — это своё значение **или**
                     # умолчание реестра. Иначе страница показывала бы «пусто» у
                     # переменной, которая на самом деле подставится, и человек
                     # заполнял бы её зря.
                     'filled': bool(value or var['default_value'])})

    return {'result': rows}


@router.put('/api/dating/profile/{profile_id}/var', include_in_schema=False)
async def dating_profile_var_save(profile_id: int, payload: DatingProfileVarSave,
                                  user: AuthUser = Depends(auth_required)):
    """Проставляет значения переменных анкеты.

    Raises:
        HTTPException: 404 — такой анкеты нет.

    ⚠ **Принимаются только имена из реестра её сайта.** Незнакомое молча
    отбрасывается, а не заводится: реестр ведут на «Письмах», и запись мимо него
    завела бы переменную, которой нет ни в одном списке. Так же поступит и приход
    агента: он вернёт что угодно, а лечь должно только ожидаемое.

    ⚠ **Пустое значение стирает строку, а не хранится пустым.** «Значения нет» и
    «значение — пустая строка» для письма одно и то же (`dating_mail_missing`
    считает нехваткой оба), а две записи одного состояния однажды разойдутся.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id FROM dating_profile WHERE id = %s',
                         (int(profile_id),))
        profile = await db.fetchone()
        if not profile:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail=f'анкеты #{profile_id} нет')

        # ⚠ Только `profile`: значения `companion` и `live` у анкеты не хранятся
        # вовсе. Пришли они от страницы или агента — молча отбрасываем, как и
        # незнакомое имя.
        known = {row['name'] for row in
                 await dating_mail_var_rows(int(profile['site_id']))
                 if row['source'] == 'profile'}

        for name, value in (payload.values or {}).items():
            name = str(name).strip()
            if name not in known:
                continue

            value = str(value or '').strip()[:DATING_MAIL_VAR_VALUE_MAX]
            if not value:
                await db.execute(
                    'DELETE FROM dating_profile_var WHERE profile_id = %s AND name = %s',
                    (int(profile_id), name))
                continue

            await db.execute(
                'INSERT INTO dating_profile_var (profile_id, name, value, filled_by)'
                ' VALUES (%s, %s, %s, %s)'
                ' ON DUPLICATE KEY UPDATE value = VALUES(value),'
                ' filled_by = VALUES(filled_by)',
                (int(profile_id), name, value, payload.filled_by))

        await db.connection.commit()

    return await dating_profile_var_list(profile_id, user)


@router.post('/api/dating/profile/{profile_id}/var/agent', include_in_schema=False)
async def dating_profile_var_agent(profile_id: int,
                                   user: AuthUser = Depends(auth_required)):
    """Просит агента заполнить переменные анкеты.

    Returns:
        dict: `result` — тот же список, что у `GET`, плюс `filled` — сколько
        значений агент проставил, и `skipped` — сколько просили, а он не нашёл.

    Raises:
        HTTPException: 404 — такой анкеты нет; 502 — агент недоступен или не
            ответил.

    ⚠ **Сохраняем сразу, а не показываем на подтверждение.** Значения видны на той
    же вкладке и правятся руками — человек увидит их через секунду и поправит, что
    не так. Промежуточный экран «принять / отклонить» стоил бы лишнего шага в
    работе, ради которой кнопку и нажали.

    Метка `filled_by = agent` отличает угаданное от проставленного человеком:
    агент ошибается, и переписывать его правку человека — не то же, что наоборот.

    ⚠ **Заполняются только пустые.** Проставленное человеком агент не трогает: он
    видит анкету хуже, чем тот, кто её ведёт, и молча переписать выверенное имя
    было бы худшим, что он может сделать.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, login, profile FROM dating_profile WHERE id = %s',
            (int(profile_id),))
        profile = await db.fetchone()

    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'анкеты #{profile_id} нет')

    known = await dating_mail_var_rows(int(profile['site_id']))
    have = await dating_profile_var_values(int(profile_id))

    # ⚠ Просим только то, чего нет: у заполненного значения агенту делать нечего,
    # а лишние имена в промпте — лишние поводы выдумать.
    wanted = [row for row in known
              if row['source'] == 'profile' and not have.get(row['name'])]
    if not wanted:
        answer = await dating_profile_var_list(profile_id, user)

        return {'result': answer['result'], 'filled': 0, 'skipped': 0}

    try:
        values = await dating_mail_agent_fill(
            {**profile, 'profile': dating_json_load(profile.get('profile'))},
            int(profile['site_id']), wanted)
    except DatingMailAgentError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=str(e)) from e

    if values:
        await dating_profile_var_save(
            profile_id, DatingProfileVarSave(values=values, filled_by='agent'), user)

    answer = await dating_profile_var_list(profile_id, user)

    return {'result': answer['result'],
            'filled': len(values),
            'skipped': len(wanted) - len(values)}


# ── Для кода ─────────────────────────────────────────────────────────────────

async def dating_mail_var_rows(site_id: int) -> list[dict]:
    """Реестр переменных сайта.

    Отдельной функцией, потому что его спрашивают из трёх мест: список для
    страницы, проверка шаблона (что за имена вообще бывают) и сборка переменных
    анкеты. Три запроса на месте разошлись бы в порядке и в наборе полей.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, name, source, title, default_value'
            ' FROM dating_mail_var WHERE site_id = %s ORDER BY id', (int(site_id),))
        rows = await db.fetchall()

    return [{'id': int(row['id']),
             'site_id': int(row['site_id']),
             'name': str(row['name']),
             'source': str(row['source']),
             'title': str(row['title']),
             'default_value': str(row['default_value'])} for row in rows]


async def dating_mail_var_names(site_id: int) -> set[str]:
    """Имена переменных сайта — для проверки шаблона."""
    return {row['name'] for row in await dating_mail_var_rows(int(site_id))}


async def dating_profile_var_values(profile_id: int) -> dict[str, str]:
    """Значения переменных анкеты — «имя → чем подставлять».

    ⚠ **Умолчание реестра идёт в дело, если своего значения нет.** Так задумано:
    переменную заводят с умолчанием ровно затем, чтобы письмо ушло и у анкеты, где
    её не заполнили. Пусто и там и там — имени в ответе не будет вовсе, и
    `dating_mail_missing` посчитает это нехваткой.

    Для отправки: сюда ходит круг рассылки, когда он появится.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT site_id FROM dating_profile WHERE id = %s',
                         (int(profile_id),))
        profile = await db.fetchone()
        if not profile:
            return {}

        await db.execute(
            'SELECT name, value FROM dating_profile_var WHERE profile_id = %s',
            (int(profile_id),))
        mine = {row['name']: str(row['value'] or '') for row in await db.fetchall()}

    found = {}
    for var in await dating_mail_var_rows(int(profile['site_id'])):
        # `companion` и `live` сюда не попадают: первое даёт карточка адресата,
        # второе сочиняет агент — и подмешивает их отправка, а не анкета.
        if var['source'] != 'profile':
            continue

        value = mine.get(var['name']) or var['default_value']
        if value:
            found[var['name']] = value

    return found


# ── Приватное ────────────────────────────────────────────────────────────────

def _checked(payload: DatingMailVarSave) -> None:
    """Отказ с причиной словами, если имя не годится."""
    why = dating_mail_var_check(payload.name)
    if why:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=why)


async def _var_row(var_id: int) -> dict:
    """Переменная по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, name, source, title, default_value'
            ' FROM dating_mail_var WHERE id = %s', (int(var_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'переменной #{var_id} нет')

    return {'id': int(row['id']),
            'site_id': int(row['site_id']),
            'name': str(row['name']),
            'source': str(row['source']),
            'title': str(row['title']),
            'default_value': str(row['default_value'])}
