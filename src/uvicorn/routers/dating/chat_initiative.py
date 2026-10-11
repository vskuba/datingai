"""Пул инициативы: чем вернуться к затихшему разговору.

Разговор затихает не потому, что кончился, а потому, что последнее слово осталось
за нами и человек не ответил. Круг отвечает только на входящее, а сам не пишет —
такая пара лежит мёртвой.

Пул собирает, **чем** заговорить снова: вопрос, повод, короткий текст под
конкретную пару. Человек читает и правит до отправки — как в трёх соседних пулах
(рассылка, инвайты, приставания).

Набирает пул круг инициативы (`initiative_round.py`), он же уносит готовое в чат
тем, кто сейчас в сети. У занятия **два выключателя**: `chat_initiative_on`
разрешает набирать, `chat_initiative_send_on` — отправлять; оба сняты по
умолчанию. Сроки и пределы — настройки сайта `chat_initiative_after`,
`chat_initiative_after_max`, `chat_initiative_repeat` (сколько раз **подряд**
звать молчащего, его ответ счёт обнуляет) и `chat_initiative_max_day` (дневной
потолок на пару); все пять стоят подразделом «Инициатива» на вкладке «Инвайты и
чат».

⚠ **Полный пул, из которого уходит по одной реплике в несколько минут, — это не
поломка**, а пауза `chat_initiative_send_every`: карточки набираются сразу всем
анкетам сайта, и без паузы включённая инициатива уходила бы залпом.

⚠ **Пустоту объясняет сервер** (`answer.why`), и повод считает сам отбор
(`initiative.initiative_why`): он решает, брать ли пару, и причина простоя
обязана спрашиваться теми же вопросами и теми же функциями.

Ручки повторяют пул инвайтов (`invite.py`): чтение, удаление. Правки текста
здесь нет намеренно — см. ⚠⚠ ниже. Отправки своей ручки тоже нет: карточка
уходит тем же путём, что и всё в чат (`send.py`), кругом.
"""

from fastapi import APIRouter, Depends, HTTPException

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.initiative import initiative_explain, initiative_why
from src.dating.json_column import json_column_load
from src.dating.online import online_show
from src.dating.pool_pair import pool_pair_girl
from src.uvicorn.routers.dating.pool_handle import (pool_handle_clear,
                                                    pool_handle_drop)

router = APIRouter(prefix='/api/dating/chat-initiative', tags=['dating'])

# Сколько карточек отдаём страницей. То же число, что у пула инвайтов: колонки
# стоят на одной вкладке, и два разных размера читались бы как разные правила.
CHAT_INITIATIVE_LIMIT = 300


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
            '       i.updated_at,'
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


# ⚠⚠ **Правки текста карточки здесь нет, и это решение.** Карточку сочиняет
# агент `dating_talk`, и сосед записывает сказанное в свой `companion_session`
# **внутри прогона** — то есть до того, как мы вообще постучимся к сайту. Исправь
# человек текст здесь, и уйдёт одно, а в памяти агента останется другое: в
# следующем разговоре он продолжит от слов, которых собеседник не читал.
#
# Расхождение это **молчаливое** — ни в журнале, ни на странице его не видно,
# всплывает оно только странным ответом агента через неделю.
#
# ⚠ Не нравится черновик — его **удаляют** (`DELETE` ниже, поштучно и пулом), а
# не переписывают. Круг сочинит новый.


@router.get('/explain', include_in_schema=False)
async def chat_initiative_explain(profile_id: int = 0, uid: str = '',
                                  user: AuthUser = Depends(auth_required)):
    """Что с инициативой у этой пары: счета, сроки, потолки и помехи.

    ⚠ **Считает домен** (`initiative.initiative_explain`) теми же выражениями,
    что и круг. Своя копия счёта в ручке разошлась бы с ним на первой правке —
    ровно та грабля, что уже была с `initiative_why` и со значком потолка.

    ⚠ Стоит **выше** `/{initiative_id}`, хотя тот и `DELETE`: соседний роутер уже
    ловил на этом — путь-слово, объявленный ниже пути-числа, достаётся
    преобразователю номера, и `explain` отвечал бы 422.

    ⚠ Неизвестная пара — не ошибка, а обычный исход: человек открывает окно у
    разговора, которого в `message` ещё нет. Отвечаем `known: false` со словами,
    а не 404: 404 страница нарисовала бы красным.
    """
    if not int(profile_id or 0) or not str(uid or ''):
        raise HTTPException(status_code=422, detail='нужны profile_id и uid')

    return {'result': await initiative_explain(int(profile_id), str(uid))}


@router.delete('', include_in_schema=False)
async def chat_initiative_clear(site_id: int = 0, profile_id: int = 0,
                                user: AuthUser = Depends(auth_required)):
    """Чистит пул сайта — всё неотправленное.

    ⚠ Ушедшие не трогаем: они часть переписки, а не пула.
    """
    return await pool_handle_clear('chat_initiative', site_id,
                                   what='инициативы', profile_id=profile_id)


@router.delete('/{initiative_id}', include_in_schema=False)
async def chat_initiative_drop(initiative_id: int,
                               user: AuthUser = Depends(auth_required)):
    """Убирает одну карточку из пула."""
    return await pool_handle_drop(
        'chat_initiative', initiative_id,
        missing=f'карточки #{initiative_id} нет',
        sent='карточка уже ушла — из пула её не убрать')


async def _why(site_id: int, pool: int) -> str:
    """Почему пул пуст — словами.

    ⚠⚠ **Пустое место не отличается от поломки.** Человек открывает колонку,
    видит пустоту и не знает: набор снят, настройка обнулена, девушки спят или
    сломалось.

    ⚠ **Считает это модуль занятия, а не ручка** (`initiative.initiative_why`):
    там же решается, брать ли пару, и повод обязан спрашиваться теми же
    вопросами. Своя копия правил здесь разошлась бы с отбором на первой же
    правке — ровно так эта подсказка и устарела в прошлый раз, пережив
    появление самого круга.
    """
    if pool:
        return ''

    return await initiative_why(int(site_id or 0))


def _answer(row) -> dict:
    """Строка пула в вид для страницы.

    ⚠ Имя, лицо и номер девушки — общей `pool_pair_girl`, той же, что у пулов
    писем и у инвайтов. Своя копия стояла здесь и **теряла два поля из
    четырёх**: `profile_uid` (номер девушки на сайте) и `girl`, под которым
    сторону разговора читает общий вид `talkSide`. Заметить это глазами было
    нечем — карточка рисуется и без них, просто беднее.
    """
    got = dict(row)
    pool_pair_girl(got, json_column_load(got.pop('profile_card', None)) or {})
    got['sent_at'] = str(got['sent_at']) if got.get('sent_at') else ''
    # ⚠ Время события у **неудачной** карточки: `sent_at` у неё снят самой
    # неудачей, и без этого поля показ (`_sentWhen`) доходит до `created_at` —
    # то есть до времени, когда текст сочинили. Между сочинением и отказом
    # проходят часы: карточку готовит круг набора, уносит круг отправки.
    got['updated_at'] = str(got['updated_at']) if got.get('updated_at') else ''
    got['created_at'] = str(got.get('created_at') or '')

    # В сети ли адресат — тем же `online_show`, что у трёх соседних пулов: правило
    # одно на всех, и своя копия разошлась бы с ними на первой правке срока.
    online = online_show(int(got.get('site_id') or 0), str(got.get('uid') or ''))
    got['online'] = bool(online.get('online'))
    got['online_known'] = bool(online.get('known'))

    return got
