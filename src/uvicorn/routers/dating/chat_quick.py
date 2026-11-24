"""Шаблоны быстрого ответа: список, добавление, удаление.

Быстрый ответ — шаблонная реплика («hi», смайлик), которая уходит в паузе, если
агент не ответил за `chat_quick_after` секунд. Правило целиком в
`src/dating/chat_quick.py`, темп — в `chat_quick_round.py`; здесь только дверь.

⚠ **Дверь на сайт, а не на анкету.** Шаблоны общие для сайта: список их короче,
и человек чинит «быстрый ответ сайта», а не сорока анкет по очереди. Ровно так
же устроены запреты («Не пишем»); разница лишь в том, что у запретов есть
сузить до анкеты, а здесь сузить не к чему.

⚠ **Права — как у соседних вкладок «Чата», всем вошедшим.** Это работа
оператора: он видит, что пара ждёт ответа, и решает, чем её держать.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.auth.auth import AuthUser, auth_required
from src.dating.chat_quick import (QUICK_AFTER_SETTING, chat_quick_add,
                                   chat_quick_drop, chat_quick_list)
from src.dating.site_setting import site_seconds

router = APIRouter(prefix='/api/dating/chat-quick', tags=['dating'])


class DatingQuickRow(BaseModel):
    """Один шаблон: текст и когда завели."""

    id: int
    body: str
    created_at: datetime | None = None


class DatingQuickList(BaseModel):
    """Ответ чтения: список + текущий интервал, его подсказка показывает."""

    result: list[DatingQuickRow]
    after_sec: int


class DatingQuickAdd(BaseModel):
    """Добавить шаблон сайту. Пустым он не бывает — проверка ниже двери не ждёт."""

    site_id: int = Field(..., ge=1)
    body: str = Field(..., min_length=1, max_length=2000)


@router.get('', include_in_schema=False, response_model=DatingQuickList)
async def chat_quick_list_rows(site_id: int = 0,
                               user: AuthUser = Depends(auth_required)):
    """Шаблоны сайта, как заводились, плюс текущий интервал.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не шаблоны всех сайтов.

    Returns:
        dict: `result` — шаблоны, `after_sec` — настройка `chat_quick_after`:
        страница показывает его в подсказке («уходит через N с, если агент не
        ответил»), и просить его вторым походом было бы второй дверью.
    """
    if not int(site_id or 0):
        return {'result': [], 'after_sec': 0}

    return {'result': await chat_quick_list(int(site_id)),
            'after_sec': await site_seconds(int(site_id), QUICK_AFTER_SETTING)}


@router.post('', include_in_schema=False, response_model=DatingQuickRow)
async def chat_quick_put(payload: DatingQuickAdd,
                        user: AuthUser = Depends(auth_required)):
    """Заводит шаблон.

    ⚠ Пустой текст (и из одних пробелов) — отказ, а не пустая строка в списке:
    пустая реплика в чате — это ничего не отправивший «быстрый ответ», и
    жребий между шаблонами по нему сравнивать нечего. Переводы строк сворачивает
    `chat_quick_add` — шаблон обязан быть одной репликой.
    """
    try:
        return await chat_quick_add(payload.site_id, payload.body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=str(e)) from e


@router.delete('/{quick_id}', include_in_schema=False)
async def chat_quick_remove(quick_id: int,
                            user: AuthUser = Depends(auth_required)):
    """Убирает шаблон. Последним ушёл — 404: второй руке строки уже нет."""
    if not await chat_quick_drop(int(quick_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='шаблона нет')

    return {'result': 'ok'}
