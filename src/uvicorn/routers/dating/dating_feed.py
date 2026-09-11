"""Ручка ленты событий: что круги сделали только что.

Читает кольцо в Redis, которое ведут круги воркера (`src/dating/dating_feed.py`).
Показывается в полосе вкладок «Профилей» двумя строками.

⚠ **Только чтение и только из Redis.** Ручку спрашивают с открытой страницы раз в
несколько секунд; сходи она в базу — и лента стоила бы запроса на каждый тик у
каждого открытого окна. В кольце же лежит готовый ответ.
"""

from fastapi import APIRouter, Depends

from src.auth.auth import auth_required
from src.dating.dating_feed import DATING_FEED_KEEP, dating_feed_read

router = APIRouter(prefix='/api/dating/feed', tags=['dating'])

# Сколько отдаём по умолчанию. Страница рисует две строки, но берёт с запасом:
# между опросами могло случиться больше, и вспышка должна достаться всем новым.
DATING_FEED_PAGE = 10


@router.get('', include_in_schema=False)
async def dating_feed_list(site_id: int = 0, limit: int = DATING_FEED_PAGE,
                           user=Depends(auth_required)):
    """События выбранного сайта, свежие первыми.

    Args:
        site_id: сайт. Без него лента пуста — событий «вообще» не бывает: они
            всегда про конкретный сайт, и общая лента показывала бы забор писем у
            соседнего вперемешку со своим.
        limit: сколько отдать, не больше кольца.

    Returns:
        dict: `result` — список `{at, kind, text, profile_id, age_sec}`.

        ⚠ **Пусто — не ошибка.** Круги могли ничего не делать последние полчаса,
        а Redis — не ответить; для показа это одно и то же, и различать их на
        странице нечем и незачем.
    """
    limit = max(1, min(int(limit or DATING_FEED_PAGE), DATING_FEED_KEEP))

    return {'result': dating_feed_read(int(site_id or 0), limit)}
