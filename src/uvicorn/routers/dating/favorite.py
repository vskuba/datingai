"""Избранные: ручка «убрать из фаворитов».

Дверь к `favorite_forget`: человек видит в журнале доставки «Фаворитов» красную
строку — письмо не ушло, потому что его в избранных у этой анкеты уже нет, —
и не дожидаясь круга, велит забыть его. Само правило и его цена расписаны
в `src/dating/favorite.py`; здесь только дверь.

⚠ **Своя дверь, а не хвост `favorite-mail`.** Убирается пара, а не письмо:
пул приставания и журнал переписки спрашивают про звезду, а не про пул. Повесь
мы ручку на `/api/dating/favorite-mail`, и «кто здесь избранный» искали бы
среди писем.

⚠ **Права — как у чёрного списка (`block.py`), всем вошедшим.** Это работа
оператора: он смотрит на отказ в доставке и решает, считать ли человека
избранным дальше. Ничего админского здесь нет.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from src.auth.auth import AuthUser, auth_required
from src.dating.favorite import favorite_forget

router = APIRouter(prefix='/api/dating/favorite', tags=['dating'])


@router.delete('', include_in_schema=False)
async def favorite_drop(profile_id: int = 0, uid: str = '',
                                user: AuthUser = Depends(auth_required)):
    """Забывает пару: убирает строку избранных и черновик приставания.

    Args:
        profile_id: номер анкеты девушки. Ноль — 422, пара не названа.
        uid: номер мужчины на сайте. Пусто — 422.

    Returns:
        dict: `dropped` — была ли строка. Нет — это не ошибка: кнопку жмут
            и по ошибке, а хотела она, чтобы он избранным не значился, — что
            уже и так так.

    ⚠ **Звезду на сайте ручка не снимает** — у сайта такой ручки нет, а наш
    сценарий зажжённую звезду не жмёт вовсе. Убранный со звёздочкой на сайте
    вернётся строкой при следующем обходе избранных: сайту верить про избранных
    больше некому (`favorite_sync`).
    """
    if not int(profile_id or 0) or not str(uid or '').strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не названа пара — убирать некого')

    return {'result': {'dropped': await favorite_forget(int(profile_id), str(uid))}}
