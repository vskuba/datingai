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
from src.dating.favorite import FAVORITE_FAIL, favorite_forget
from src.uvicorn.routers.dating.profile_ import profile_row

router = APIRouter(prefix='/api/dating/favorite', tags=['dating'])


@router.delete('', include_in_schema=False)
async def favorite_drop(profile_id: int = 0, uid: str = '',
                               user: AuthUser = Depends(auth_required)):
    """Забывает пару: гасит звезду сайта и убирает строку избранных с черновиком.

    Args:
        profile_id: номер анкеты девушки. Ноль — 422, пара не названа.
        uid: номер мужчины на сайте. Пусто — 422.

    Returns:
        dict: `state` (`done` — звезда сайта погасла нашей работой, `already` —
            её там и так не было) и `dropped` — была ли строка у нас. Удача без
            строки — не ошибка: кнопку жмут и по ошибке, а хотела она, чтобы он
            избранным не значился — а он и не значился.

    ⚠ **Отказ сайта — 409, словом из `error`.** Звезда не погасла — значит не
    убрано нигде: строка на месте, и человек видит причину, а не молчаливое
    «убрано», которое завтра перешибёт `favorite_sync`.

    ⚠ **Прогон идёт отсюда же, синхронно: ~40 секунд браузера** (шаги сценария
    «из избранных»). Кнопка наживая, человек ждёт — и ждёт ровно столько,
    сколько ждёт любой вход браузером под его анкетой.
    """
    if not int(profile_id or 0) or not str(uid or '').strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не названа пара — убирать некого')

    profile = await profile_row(int(profile_id))

    out = await favorite_forget(profile, str(uid))
    if out['state'] == FAVORITE_FAIL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=out['error'])

    return {'result': out}
