"""Шаблоны писем: список, заведение, правка, удаление.

Письмо рассылки собирается из шаблона и двух участников — анкеты и адресата.
Здесь только шаблоны, то есть работа с нашей строкой в нашей базе; переменные,
подстановка и проверка — в `src/dating/mail.py`.

## Шаблоны у сайтов свои, и у каждой анкеты — свои

`site_id` в каждой строке, и список на странице отбирается по нему — как и всё
в разделе. У сайтов разная аудитория и разные правила модерации, и текст,
написанный под один, на другом может не пройти вовсе.

С 2026-10-16 у строки есть и владелица — `profile_id`, номер анкеты. Круг пишет
заготовки каждой включённой анкете своей рукой и зовёт их числом
(`template_1`), а владелицу строки показывает аватарка в списке; список на
«Шаблонах» показывает **выбранную анкету**: ручка принимает `profile_id` и по
нему сужает выборку. Без номера страница показала бы весь сайт — и человек
читал бы чужие тексты как её.

## Права: и читают, и правят все вошедшие

⚠ **Прежде правка была админской**, и довод звучал так: письмо уходит от всех
анкет сайта многим людям, ошибка в шаблоне — массовая рассылка ерунды от нашего
имени, а значит это ближе к настройке установки, чем к работе с анкетой.

Довод не выдержал живого употребления. Текст письма — **первое**, что правят,
когда рассылка идёт плохо: не так здоровается, слишком длинно, не о том спрашивает.
Это ровно работа того, кто ведёт анкеты, и запирать её значило звать админа на
каждую переформулировку. Оператор открывал «Письма → Шаблоны» и не видел даже
кнопки «завести».

Риск при этом остался прежним — и закрыт он не правами, а проверкой: шаблон с
неизвестной переменной не сохранится (`mail_check`), а письмо с
незаполненной — не уйдёт (`mail_missing`). Тот же разбор границы, что у
выключателей сайта: устройство установки — админу, работа — оператору
(`auth.md`, §7).

## ⚠ Журнал отправок ручек здесь пока нет

`mailing` заведён миграцией и ждёт круга рассылки: писать в него некому,
пока письма не уходят. Заводить ручку «покажи журнал» до того, как в журнале
что-то появляется, значило бы отдавать пустой список и гадать, чего в нём не
хватает.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.json_column import json_column_load
from src.dating.mail import (MAIL_BODY_MAX, MAIL_NAME_MAX,
                                    mail_check,
                                    mail_vars_found)
from src.dating.mail_agent import DatingMailAgentError, mail_write
from src.uvicorn.routers.dating.mail_var import (mail_var_names,
                                                        mail_var_rows)

router = APIRouter(prefix='/api/dating/mail', tags=['dating'])


class DatingMailSave(BaseModel):
    """Тело заведения и правки шаблона."""

    site_id: int = Field(..., gt=0, description='Сайт, которому принадлежит шаблон')
    profile_id: int = Field(0, ge=0,
                            description='Чья это заготовка (profile.id); заводится '
                                        'с 2026-10-16 всегда с анкетой — круг пишет '
                                        'каждой включённой своей рукой')
    name: str = Field(..., min_length=1, max_length=MAIL_NAME_MAX,
                      description='Название для списка в панели, на сайт не уходит; '
                                  'имя только считает место в норме анкеты '
                                  '(template_1), владелицу показывает аватарка')
    body: str = Field(..., min_length=1, max_length=MAIL_BODY_MAX,
                      description='Текст письма с переменными вида {{firstName}}')
    enabled: bool = Field(True, description='Участвует ли шаблон в рассылке')


@router.get('', include_in_schema=False)
async def mail_list(site_id: int = 0, profile_id: int = 0,
                    user: AuthUser = Depends(auth_required)):
    """Шаблоны сайта, свежие сверху; если названа анкета — только её.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не все шаблоны всех
            сайтов: страница без выбранного сайта не должна показывать чужое.
        profile_id: анкета, чьи тексты смотрят. Вкладка «Шаблоны» на «Профилях»
            приходит с выбранной анкетой всегда: у текста теперь есть владелица,
            и человек смотрит, что написано **ей**. Ноль — весь сайт: так зовёт
            форма генерации на «Рассылке», где выбирают из всех текстов сразу.

    Returns:
        dict: `result` — список шаблонов. У каждого, помимо полей строки, `vars` —
        какие переменные он использует, и `profile_id` — чей он (пусто — общий,
        заведённый до правила 2026-10-16).
    """
    if not int(site_id or 0):
        return {'result': []}

    where, args = _where(site_id, profile_id)
    sql = ('SELECT id, site_id, profile_id, name, body, enabled,'
           ' created_at, updated_at FROM mail ' + where + ' ORDER BY id DESC')

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    return {'result': [_answer(row) for row in rows]}


class DatingMailDraft(BaseModel):
    """Что просят у агента, когда жмут «AI сгенерировать».

    ⚠ **Собеседника здесь нет — он и не был.** Тело шаблона — метки, а не
    конкретика про адресата: сведения о живом человеке превратились бы в зашитую
    правду — так и было: агент возвращал «Diana here! I'm a choreographer living
    in Kyiv». Про интересы получателя спросит `question_to_companion` — при
    сборке пула, а не сейчас.

    ⚠ **А владелица — здесь, и это перемена 2026-10-17.** С тех пор как шаблон
    принадлежит анкете, её карточка — якорь рассказа о себе: круг зовёт
    `mail_write` с ней, и кнопка, стоящая на открытой анкете, обязана звать так
    же — иначе кнопка писала бы владелице вслепую, вразрез с кругом.
    """

    site_id: int = Field(..., gt=0, description='Сайт: его переменные')
    hint: str = Field('', max_length=500,
                      description='Пожелание: о чём письмо, какой тон')
    profile_id: int = Field(0, ge=0,
                            description='Открытая анкета: её карточка — основа '
                            'рассказа о себе. Ноль — анкета не спрашивается')


@router.post('/draft', include_in_schema=False)
async def mail_draft(payload: DatingMailDraft,
                            user: AuthUser = Depends(auth_required)):
    """Просит агента написать черновик шаблона.

    Returns:
        dict: `body` — текст, `vars` — какие переменные он вставил, `unknown` —
        какие из них не из реестра (агент выдумал).

    Raises:
        HTTPException: 502 — агент недоступен или не ответил.

    ⚠ **Черновик не сохраняется.** Он возвращается в поле письма, и человек правит
    его глазами: агент пишет живым людям, и отправлять непрочитанное значило бы
    доверять ему больше, чем стоит. Сохранение — обычным `POST`, со всеми
    проверками.

    ⚠ **Карточка открытой анкеты — якорь, а не цитата.** Поле «Название» страница
    подставит сама — числом и аватаркой владелицы — и сохранит уже `POST` с
    `profile_id`; сам текст модель пишет своими словами, сверяясь с карточкой.
    """
    known = await mail_var_rows(int(payload.site_id))
    girl = await _girl(payload.profile_id)

    try:
        drafted = await mail_write(int(payload.site_id), known, payload.hint,
                                   girl=girl)
    except DatingMailAgentError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=str(e)) from e

    body = drafted or ''
    found = mail_vars_found(body)
    names = {row['name'] for row in known}

    return {'result': {'body': body,
                       # ⚠ Названия в ответе нет: поле «Название» страница берёт
                       # по узору со своих чисел (`template_{номер}`) — ответ
                       # агента про сам текст, не про имя.
                       'vars': found,
                       # ⚠ Выдуманные имена называем сразу, а не ждём отказа при
                       # сохранении: человек увидит их в тексте и поправит, пока
                       # письмо перед глазами.
                       'unknown': [name for name in found if name not in names]}}


@router.post('', include_in_schema=False)
async def mail_create(payload: DatingMailSave,
                             user: AuthUser = Depends(auth_required)):
    """Заводит шаблон.

    ⚠ **Владелица — из формы.** Страница «Шаблоны» зовёт ручку с выбранной анкетой,
    и её номер ложится в строку: с этого дня шаблон чей-то. `profile_id` ноль —
    общий текст без владелицы, как было до правила; рассылка такое не берёт.

    Returns:
        dict: `result` — заведённый шаблон целиком, а не один номер: страница
        рисует его в списке сразу, не перечитывая всё заново.

    Raises:
        HTTPException: 422 — шаблон негодный; причина словами, как её вернул
            `mail_check`.
    """
    await _checked(payload)

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail (site_id, profile_id, name, body, enabled)'
            ' VALUES (%s, %s, %s, %s, %s)',
            (int(payload.site_id), int(payload.profile_id) or None,
             payload.name.strip(), payload.body, 1 if payload.enabled else 0))
        await db.connection.commit()
        mail_id = db.lastrowid

    return {'result': await _row(int(mail_id))}


@router.put('/{mail_id}', include_in_schema=False)
async def mail_update(mail_id: int, payload: DatingMailSave,
                             user: AuthUser = Depends(auth_required)):
    """Правит шаблон целиком.

    Raises:
        HTTPException: 404 — такого шаблона нет; 422 — шаблон негодный.

    ⚠ **Ни сайт, ни владелица шаблона не меняются.** Переезд — не правка текста, а
    другое действие: у журнала отправок сайт свой, и уехавший шаблон рассорил бы
    его с историей. Поля в теле есть (форма одна на заведение и правку), но здесь
    они не читаются: правят текст, имя и галочку, а чей текст — решает заведение.
    """
    await _row(mail_id)
    await _checked(payload)

    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE mail SET name = %s, body = %s, enabled = %s WHERE id = %s',
            (payload.name.strip(), payload.body, 1 if payload.enabled else 0,
             int(mail_id)))
        await db.connection.commit()

    return {'result': await _row(mail_id)}


@router.delete('/{mail_id}', include_in_schema=False)
async def mail_delete(mail_id: int,
                             user: AuthUser = Depends(auth_required)):
    """Удаляет шаблон.

    Raises:
        HTTPException: 404 — такого шаблона нет.

    ⚠ **Журнал отправок при этом остаётся**: у `mailing.mail_id` стоит
    `ON DELETE SET NULL`. Журнал про то, что уже случилось, и удаление шаблона
    прошлого не отменяет; текст ушедшего письма хранится там целиком и от шаблона
    не зависит.

    Не выключение вместо удаления, потому что выключение уже есть отдельно
    (`enabled`) и значит другое: «пусть полежит, но не рассылается».
    """
    await _row(mail_id)

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM mail WHERE id = %s', (int(mail_id),))
        await db.connection.commit()

    return {'result': {'id': int(mail_id)}}


@router.delete('', include_in_schema=False)
async def mail_delete_all(site_id: int = 0, profile_id: int = 0,
                          user: AuthUser = Depends(auth_required)):
    """Удаляет все шаблоны разом — корзинка в шапке списка.

    Args:
        site_id: сайт; ноль — нечего удалять, как и в списке.
        profile_id: анкета; названа — уходят только её тексты.

    Returns:
        dict: `result.deleted` — сколько строк ушло. Человек сверит число с тем,
        что видел в списке; молча промолчать было бы соврать, если шаблон успел
        уехать или исчезнуть, пока вкладка открыта.

    ⚠ **Отбор — общий со списком, одна `_where`**: что человек видел на экране,
    то и исчезло. Своя вторая копия условий разошлась бы со списком на первой
    же правке — грабля, на которую уже наступали в рассылке.

    Журнал отправок остаётся, как и при удалении одного шаблона
    (`ON DELETE SET NULL`): ушедшие письма хранят текст в себе и от шаблона не
    зависят.
    """
    if not int(site_id or 0):
        return {'result': {'deleted': 0}}

    where, params = _where(site_id, profile_id)

    async with mysql_get_db_async() as db:
        await db.execute(f'DELETE FROM mail {where}', tuple(params))
        deleted = int(db.rowcount or 0)
        await db.connection.commit()

    return {'result': {'deleted': deleted}}


# ── Приватное ────────────────────────────────────────────────────────────────

def _where(site_id: int, profile_id: int) -> tuple[str, list]:
    """Кусок WHERE для списка и корзинки: сайт всегда, анкета — если названа.

    Отбор один на список и на удаление разом: ручка корзинки обязана убрать
    ровно то, что человек видит на экране, а не второй раз угадать, что это.
    Вторая копия условий разошлась бы с первой на первой же правке.
    """
    where, params = 'WHERE site_id = %s', [int(site_id)]
    if int(profile_id or 0):
        where += ' AND profile_id = %s'
        params.append(int(profile_id))

    return where, params


async def _girl(profile_id: int) -> dict | None:
    """Карточка владелицы для черновика — тем же видом, что у круга."""
    if not int(profile_id or 0):
        return None

    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, profile FROM profile WHERE id = %s',
                         (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'анкеты #{profile_id} нет')
    row['profile'] = json_column_load(row['profile'])

    return row


async def _checked(payload: DatingMailSave) -> None:
    """Отказ с причиной словами, если шаблон негодный.

    Проверка одна на заведение и правку: два места разошлись бы, и правкой можно
    было бы завести то, что не пропускает заведение.

    ⚠ **Годность зависит от сайта**: переменные заводят по сайтам, и один и тот же
    текст на одном сайте годен, а на другом ссылается на несуществующее имя.
    Отсюда и поход в базу за реестром — проверка перестала быть чистой функцией.
    """
    known = await mail_var_names(int(payload.site_id))
    why = mail_check(payload.name, payload.body, known)
    if why:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=why)


async def _row(mail_id: int) -> dict:
    """Шаблон по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, profile_id, name, body, enabled,'
            ' created_at, updated_at FROM mail WHERE id = %s', (int(mail_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'шаблона #{mail_id} нет')

    return _answer(row)


def _answer(row: dict) -> dict:
    """Строка базы в ответ странице.

    Добавляет `vars` — какие переменные шаблон использует: страница красит по ним
    пилюли и ловит незнакомое имя, а считать это должен сервер, у которого
    разбор один на всех.

    ⚠ **`preview` отсюда убран.** Он показывал шаблон с подставленными
    умолчаниями, и его блок на странице повторял поле письма слово в слово:
    умолчаний у переменных нет, подставлять нечего. Считался он при этом на каждый
    шаблон в каждом ответе — вместе с лишним чтением реестра переменных.

    Подстановка настоящих значений живёт в пуле (`mailing.py`), и читают её
    там же, в карточке готового письма.
    """
    return {'id': int(row['id']),
            'site_id': int(row['site_id']),
            # Чей это текст; пусто — общий, заведённый до правила 2026-10-16.
            'profile_id': (int(row['profile_id'])
                           if row.get('profile_id') is not None else None),
            'name': str(row['name']),
            'body': str(row['body']),
            'enabled': bool(row['enabled']),
            'vars': mail_vars_found(row['body']),
            'created_at': row['created_at'],
            'updated_at': row['updated_at']}

