"""Шаблоны писем: список, заведение, правка, удаление.

Письмо рассылки собирается из шаблона и двух участников — анкеты и адресата.
Здесь только шаблоны, то есть работа с нашей строкой в нашей базе; переменные,
подстановка и проверка — в `src/dating/dating_mail.py`.

## Шаблоны у сайтов свои

`site_id` в каждой строке, и список на странице отбирается по нему — как и всё
в разделе. У сайтов разная аудитория и разные правила модерации, и текст,
написанный под один, на другом может не пройти вовсе.

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
неизвестной переменной не сохранится (`dating_mail_check`), а письмо с
незаполненной — не уйдёт (`dating_mail_missing`). Тот же разбор границы, что у
выключателей сайта: устройство установки — админу, работа — оператору
(`auth.md`, §7).

## ⚠ Журнал отправок ручек здесь пока нет

`dating_mailing` заведён миграцией и ждёт круга рассылки: писать в него некому,
пока письма не уходят. Заводить ручку «покажи журнал» до того, как в журнале
что-то появляется, значило бы отдавать пустой список и гадать, чего в нём не
хватает.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.dating_mail import (DATING_MAIL_BODY_MAX, DATING_MAIL_NAME_MAX,
                                    dating_mail_check,
                                    dating_mail_vars_found)
from src.dating.dating_mail_agent import DatingMailAgentError, dating_mail_write
from src.uvicorn.routers.dating.dating_mail_var import (dating_mail_var_names,
                                                        dating_mail_var_rows)

router = APIRouter(prefix='/api/dating/mail', tags=['dating'])


class DatingMailSave(BaseModel):
    """Тело заведения и правки шаблона."""

    site_id: int = Field(..., gt=0, description='Сайт, которому принадлежит шаблон')
    name: str = Field(..., min_length=1, max_length=DATING_MAIL_NAME_MAX,
                      description='Название для списка в панели, на сайт не уходит')
    body: str = Field(..., min_length=1, max_length=DATING_MAIL_BODY_MAX,
                      description='Текст письма с переменными вида {{firstName}}')
    enabled: bool = Field(True, description='Участвует ли шаблон в рассылке')


@router.get('', include_in_schema=False)
async def dating_mail_list(site_id: int = 0, user: AuthUser = Depends(auth_required)):
    """Шаблоны сайта, свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не все шаблоны всех
            сайтов: страница без выбранного сайта не должна показывать чужое.

    Returns:
        dict: `result` — список шаблонов. У каждого, помимо полей строки, `vars` —
        какие переменные он использует.
    """
    if not int(site_id or 0):
        return {'result': []}

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, name, body, enabled, created_at, updated_at'
            ' FROM mail WHERE site_id = %s ORDER BY id DESC',
            (int(site_id),))
        rows = await db.fetchall()

    return {'result': [_answer(row) for row in rows]}


class DatingMailDraft(BaseModel):
    """Чего просят у агента, когда жмут «AI сгенерировать».

    ⚠ **Ни анкеты, ни собеседника здесь нет, и это главное.** Шаблон — заготовка
    **для всех**: одним и тем же текстом напишут разные девушки разным мужчинам.
    Сведения о конкретных участниках в нём превратились бы в зашитую конкретику —
    так и было: агент возвращал «Diana here! I'm a choreographer living in Kyiv»,
    и такой шаблон годился одной анкете из сорока.

    Кто напишет и кому — выяснится при генерации пула, где переменные и
    подставятся. Про интересы адресата спросит `question_to_companion` — тоже
    тогда, а не сейчас.
    """

    site_id: int = Field(..., gt=0, description='Сайт: его переменные')
    hint: str = Field('', max_length=500,
                      description='Пожелание: о чём письмо, какой тон')


@router.post('/draft', include_in_schema=False)
async def dating_mail_draft(payload: DatingMailDraft,
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

    ⚠ **Анкета для этого не нужна вовсе.** Прежде ручка искала любую анкету сайта
    и отказывала, если их нет: письмо, дескать, «не от кого писать». Это было
    неверно — шаблон пишется до того, как известно, кто им воспользуется, и
    заготовку можно готовить на пустом сайте.
    """
    known = await dating_mail_var_rows(int(payload.site_id))

    try:
        drafted = await dating_mail_write(int(payload.site_id), known, payload.hint)
    except DatingMailAgentError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=str(e)) from e

    body = str((drafted or {}).get('body') or '')
    found = dating_mail_vars_found(body)
    names = {row['name'] for row in known}

    return {'result': {'body': body,
                       # ⚠ Название придумывает агент — оно про сам текст. Страница
                       # подставит его в поле имени, если человек своего не вписал.
                       'title': str((drafted or {}).get('title') or ''),
                       'vars': found,
                       # ⚠ Выдуманные имена называем сразу, а не ждём отказа при
                       # сохранении: человек увидит их в тексте и поправит, пока
                       # письмо перед глазами.
                       'unknown': [name for name in found if name not in names]}}


@router.post('', include_in_schema=False)
async def dating_mail_create(payload: DatingMailSave,
                             user: AuthUser = Depends(auth_required)):
    """Заводит шаблон.

    Returns:
        dict: `result` — заведённый шаблон целиком, а не один номер: страница
        рисует его в списке сразу, не перечитывая всё заново.

    Raises:
        HTTPException: 422 — шаблон негодный; причина словами, как её вернул
            `dating_mail_check`.
    """
    await _checked(payload)

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail (site_id, name, body, enabled)'
            ' VALUES (%s, %s, %s, %s)',
            (int(payload.site_id), payload.name.strip(), payload.body,
             1 if payload.enabled else 0))
        await db.connection.commit()
        mail_id = db.lastrowid

    return {'result': await _row(int(mail_id))}


@router.put('/{mail_id}', include_in_schema=False)
async def dating_mail_update(mail_id: int, payload: DatingMailSave,
                             user: AuthUser = Depends(auth_required)):
    """Правит шаблон целиком.

    Raises:
        HTTPException: 404 — такого шаблона нет; 422 — шаблон негодный.

    ⚠ **Сайт шаблона не меняется.** Переезд на другой сайт — не правка текста, а
    другое действие: у журнала отправок сайт свой, и уехавший шаблон рассорил бы
    его с историей. Поле в теле есть (форма одна на заведение и правку), но здесь
    оно не читается.
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
async def dating_mail_delete(mail_id: int,
                             user: AuthUser = Depends(auth_required)):
    """Удаляет шаблон.

    Raises:
        HTTPException: 404 — такого шаблона нет.

    ⚠ **Журнал отправок при этом остаётся**: у `dating_mailing.mail_id` стоит
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


# ── Приватное ────────────────────────────────────────────────────────────────

async def _checked(payload: DatingMailSave) -> None:
    """Отказ с причиной словами, если шаблон негодный.

    Проверка одна на заведение и правку: два места разошлись бы, и правкой можно
    было бы завести то, что не пропускает заведение.

    ⚠ **Годность зависит от сайта**: переменные заводят по сайтам, и один и тот же
    текст на одном сайте годен, а на другом ссылается на несуществующее имя.
    Отсюда и поход в базу за реестром — проверка перестала быть чистой функцией.
    """
    known = await dating_mail_var_names(int(payload.site_id))
    why = dating_mail_check(payload.name, payload.body, known)
    if why:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=why)


async def _row(mail_id: int) -> dict:
    """Шаблон по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, site_id, name, body, enabled, created_at, updated_at'
            ' FROM mail WHERE id = %s', (int(mail_id),))
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

    Подстановка настоящих значений живёт в пуле (`dating_mailing.py`), и читают её
    там же, в карточке готового письма.
    """
    return {'id': int(row['id']),
            'site_id': int(row['site_id']),
            'name': str(row['name']),
            'body': str(row['body']),
            'enabled': bool(row['enabled']),
            'vars': dating_mail_vars_found(row['body']),
            'created_at': row['created_at'],
            'updated_at': row['updated_at']}
