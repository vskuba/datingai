"""Чёрный список: кому эта анкета больше не пишет.

Три ручки — посмотреть, занести руками, снять. Само правило и его цена расписаны
в `src/dating/block.py`; здесь только дверь к нему.

⚠ **Своя дверь, а не хвост инвайтов.** Запрет действует на инвайты, рассылку и
письма избранным разом: повесь мы его на `/api/dating/invite`, адрес обещал бы
только чат — и второй канал искали бы в другом месте.

⚠ **Права — как у пулов, всем вошедшим.** Это работа оператора: он видит в
доставке отказ и решает, писать ли этому человеку дальше. Админского здесь нет
ничего — ни адресов, ни паролей.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.auth.auth import AuthUser, auth_required
from src.dating.block import (block_add, block_drop,
                                     block_rows)

router = APIRouter(prefix='/api/dating/block', tags=['dating'])


class DatingBlockAdd(BaseModel):
    """Занести пару руками: «этой девушке ему больше не писать»."""

    site_id: int = Field(..., ge=1)
    profile_id: int = Field(..., ge=1)
    uid: str = Field(..., min_length=1, max_length=32)
    name: str = Field('', max_length=190)
    reason: str = Field('', max_length=500)


@router.get('', include_in_schema=False)
async def block_list(site_id: int = 0, profile_id: int = 0, page: int = 1,
                            per: int = 0,
                            user: AuthUser = Depends(auth_required)):
    """Чёрный список сайта, свежие сверху, страницами.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не запреты всех сайтов.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.
        page: страница, с единицы. За концом — поправится сама.
        per: строк на странице. Ноль — предел по умолчанию.

    Returns:
        dict: `result` — записи страницы, рядом `page`, `pages`, `total`.

    ⚠ **Страницы на сервере.** Список копится сам и не убывает: запрет не истекает,
    и каждый отказ сайта добавляет строку.
    """
    got = await block_rows(int(site_id or 0), int(profile_id or 0),
                                  int(page or 1), int(per or 0))

    return {'result': got['rows'], 'page': got['page'], 'pages': got['pages'],
            'total': got['total']}


@router.post('', include_in_schema=False)
async def block_put(payload: DatingBlockAdd,
                           user: AuthUser = Depends(auth_required)):
    """Заносит пару в список руками.

    ⚠ **Руками — полноправный путь, а не отладочный.** Сайт говорит «он закрылся»
    не всегда: человек может просто не отвечать полгода, и решение прекратить
    попытки принимает оператор. Пометка `source` хранит, кто решил.

    Повтор не ошибка: пара уже в списке — отвечаем тем же `ok`, а не отказом.
    Нажавший хотел, чтобы ей не писали, и это уже так.
    """
    await block_add(payload.site_id, payload.profile_id, payload.uid,
                           payload.name, payload.reason or 'решение оператора',
                           source='hand')

    return {'result': 'ok'}


@router.delete('/{block_id}', include_in_schema=False)
async def block_remove(block_id: int,
                              user: AuthUser = Depends(auth_required)):
    """Снимает запрет: этой паре снова можно писать.

    ⚠ **Снять его может только человек.** Сайт не сообщает, когда собеседник
    передумал, и сроком жизни записи мы бы решили за него.
    """
    if not await block_drop(int(block_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='запрета нет')

    return {'result': 'ok'}
