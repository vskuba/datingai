"""Пул инициативы: чем вернуться к затихшему разговору.

Разговор затихает не потому, что кончился, а потому, что последнее слово осталось
за нами и человек не ответил. Круг отвечает только на входящее, а сам не пишет —
такая пара лежит мёртвой.

Пул собирает, **чем** заговорить снова: вопрос, повод, короткий текст под
конкретную пару. Человек читает и правит до отправки — как в трёх соседних пулах
(рассылка, инвайты, приставания).

⚠⚠ **Работы за пулом пока нет.** Таблица, ручки и колонка на странице заведены, а
набирать карточки некому: круга инициативы не существует. Пул будет пустым, и это
ожидаемо — см. подсказки настроек `chat_initiative_after` и
`chat_initiative_repeat`.

Ручки повторяют пул инвайтов (`invite.py`): чтение, правка, удаление. Отправки
здесь нет и не будет отдельной — уйдёт тем же путём, что и всё в чат
(`send.py`), когда появится круг.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.json_column import json_column_load
from src.dating.online import online_show

router = APIRouter(prefix='/api/dating/chat-initiative', tags=['dating'])

# Сколько карточек отдаём страницей. То же число, что у пула инвайтов: колонки
# стоят на одной вкладке, и два разных размера читались бы как разные правила.
CHAT_INITIATIVE_LIMIT = 300


class DatingChatInitiativeEdit(BaseModel):
    """Правка текста карточки перед отправкой."""

    body: str = Field(min_length=1, max_length=4000)


@router.get('', include_in_schema=False)
async def chat_initiative_list(site_id: int = 0, status_: str = 'draft',
                               profile_id: int = 0,
                               user: AuthUser = Depends(auth_required)):
    """Пул сайта, свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не пул всех сайтов.
        status_: `draft` — ждущие отправки, `sent` — ушедшие.
        profile_id: сузить до одной анкеты. Ноль — все.

    Returns:
        dict: `result` — карточки, `answer` — счёт пула и повод его пустоты.

    ⚠ **Анкета — фильтр, а не условие**: та же мерка, что у списка диалогов рядом.
    Человек смотрит, что происходит на сайте, и сужает до девушки, когда нужно.
    """
    if not int(site_id or 0):
        return {'result': [], 'answer': {'pool': 0, 'why': 'сайт не выбран'}}

    status_ = 'sent' if str(status_ or '') == 'sent' else 'draft'

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT i.id, i.site_id, i.profile_id, i.uid, i.name, i.body,'
            '       i.try_no, i.status, i.error, i.sent_at, i.created_at,'
            '       p.login AS profile_login, p.profile AS profile_card'
            '  FROM chat_initiative i'
            '  LEFT JOIN profile p ON p.id = i.profile_id'
            ' WHERE i.site_id = %s AND i.status = %s'
            '   AND (%s = 0 OR i.profile_id = %s)'
            ' ORDER BY i.id DESC LIMIT %s',
            (int(site_id), status_, int(profile_id), int(profile_id),
             CHAT_INITIATIVE_LIMIT))
        rows = await db.fetchall()

        # ⚠ Счёт **отдельным запросом**, а не длиной списка: список обрезан
        # пределом показа, и «300 / 300» читалось бы как полный пул, когда в нём
        # тысяча. На инвайтах этой ошибкой уже обжигались.
        await db.execute(
            'SELECT COUNT(*) AS n FROM chat_initiative'
            " WHERE site_id = %s AND status = 'draft'", (int(site_id),))
        pool = int(((await db.fetchone()) or {}).get('n') or 0)

    return {'result': [_answer(row) for row in (rows or [])],
            'answer': {'pool': pool, 'why': await _why(site_id, pool)}}


@router.put('/{initiative_id}', include_in_schema=False)
async def chat_initiative_edit(initiative_id: int,
                               payload: DatingChatInitiativeEdit,
                               user: AuthUser = Depends(auth_required)):
    """Правит текст карточки — только пока она в пуле.

    ⚠ Ушедшее не правится: это уже сказанное, и подменять его в базе значило бы
    держать переписку, которой не было.
    """
    row = await _row(initiative_id)

    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='карточка уже ушла — править нечего')

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE chat_initiative SET body = %s WHERE id = %s',
                         (str(payload.body).strip(), int(initiative_id)))
        await db.connection.commit()

    return {'result': await _row(initiative_id)}


@router.delete('', include_in_schema=False)
async def chat_initiative_clear(site_id: int = 0, profile_id: int = 0,
                                user: AuthUser = Depends(auth_required)):
    """Чистит пул сайта — всё неотправленное.

    ⚠ Ушедшие не трогаем: они часть переписки, а не пула.
    """
    if not int(site_id or 0):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='сайт не выбран')

    async with mysql_get_db_async() as db:
        await db.execute(
            "DELETE FROM chat_initiative WHERE site_id = %s AND status = 'draft'"
            ' AND (%s = 0 OR profile_id = %s)',
            (int(site_id), int(profile_id), int(profile_id)))
        gone = int(db.rowcount or 0)
        await db.connection.commit()

    return {'result': {'deleted': gone}}


@router.delete('/{initiative_id}', include_in_schema=False)
async def chat_initiative_drop(initiative_id: int,
                               user: AuthUser = Depends(auth_required)):
    """Убирает одну карточку из пула."""
    row = await _row(initiative_id)

    if row['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='карточка уже ушла — из пула её не убрать')

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM chat_initiative WHERE id = %s',
                         (int(initiative_id),))
        await db.connection.commit()

    return {'result': {'deleted': 1}}


async def _why(site_id: int, pool: int) -> str:
    """Почему пул пуст — словами.

    ⚠⚠ **Пустое место не отличается от поломки.** Человек открывает колонку,
    видит пустоту и не знает: набор молчит, настройка снята или сломалось. Пока
    круга инициативы нет вовсе, честнее сказать об этом прямо, чем оставить
    пустоту без объяснения.
    """
    if pool:
        return ''

    return ('набирать карточки пока некому: круг инициативы ещё не сделан. '
            'Сроки уже настраиваются — «Инициатива, проявить через минут» и '
            '«Инициатива, сколько раз повторить» на вкладке «Настройки сайтов»')


async def _row(initiative_id: int) -> dict:
    """Карточка по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT i.id, i.site_id, i.profile_id, i.uid, i.name, i.body,'
            '       i.try_no, i.status, i.error, i.sent_at, i.created_at,'
            '       p.login AS profile_login, p.profile AS profile_card'
            '  FROM chat_initiative i'
            '  LEFT JOIN profile p ON p.id = i.profile_id'
            ' WHERE i.id = %s', (int(initiative_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'карточки #{initiative_id} нет')

    return _answer(row)


def _answer(row) -> dict:
    """Строка пула в вид для страницы."""
    got = dict(row)
    card = json_column_load(got.pop('profile_card', None)) or {}
    got['profile_name'] = str(card.get('name') or got.get('profile_login') or '')
    got['profile_photo'] = str(card.get('photo') or '')
    got['sent_at'] = str(got['sent_at']) if got.get('sent_at') else ''
    got['created_at'] = str(got.get('created_at') or '')

    # В сети ли адресат — тем же `online_show`, что у трёх соседних пулов: правило
    # одно на всех, и своя копия разошлась бы с ними на первой правке срока.
    online = online_show(int(got.get('site_id') or 0), str(got.get('uid') or ''))
    got['online'] = bool(online.get('online'))
    got['online_known'] = bool(online.get('known'))

    return got
