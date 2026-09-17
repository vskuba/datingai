"""Ручка страницы «Сокеты»: кто сейчас держит соединение с чатом сайта.

Сводит два источника, и порознь они на вопрос не отвечают:

* **снимок держателей** из Redis (`dating_socket.py`) — что происходит сейчас:
  соединение живо, сколько сообщений поймано за смену;
* **анкеты из базы** — у кого вообще есть чем подключаться (`ws_url`), а у кого
  нет и почему.

⚠ **Анкета без адреса сокета — не ошибка.** У сайта без чат-сокета (Ladadate) его
не бывает ни у одной анкеты: там пишут сценарием, а входящие приезжают импортом из
панели. Страница должна это говорить, а не показывать шестьдесят красных строк.
"""

from fastapi import APIRouter, Depends

from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_required
from src.dating.dating_socket import dating_socket_read

router = APIRouter(prefix='/api/dating/socket', tags=['dating'])


@router.get('', include_in_schema=False)
async def dating_socket_list(site_id: int = 0, user=Depends(auth_required)):
    """Сокеты сайта: что держится сейчас и что могло бы держаться.

    Returns:
        dict: `sockets` — по строке на анкету, `alive` — жив ли воркер (есть ли
        снимок), `age_sec` — возраст снимка, `shift_left` — сколько осталось смене.

        У строки: `profile_id`, `login`, `has_url` (есть ли адрес), `ws_url`
        (обрезанный, без токена), `held` — держится ли прямо сейчас, `alive` —
        живо ли соединение, `messages` — поймано за смену, `status`, `last_error`.

    ⚠ **Отдаём и тех, у кого адреса нет.** Страница отвечает на вопрос «почему
    анкета молчит», и «адрес не снят» — самый частый ответ; спрячь мы такие
    строки, страница отвечала бы только на половину вопроса.
    """
    snapshot = dating_socket_read()
    by_profile = {int(row['profile_id']): row for row in snapshot['holders']}

    where, params = '', []
    if int(site_id or 0):
        where = ' WHERE site_id = %s'
        params.append(int(site_id))

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, login, site_id, ws_url, status, last_error, enabled, '
            '       storage_state IS NOT NULL AS has_state '
            f'  FROM profile{where} ORDER BY id', tuple(params))
        rows = await db.fetchall()

    sockets = []
    for row in rows or []:
        held = by_profile.get(int(row['id']))
        url = str(row['ws_url'] or '')

        sockets.append({
            'profile_id': int(row['id']),
            'login': str(row['login'] or ''),
            'site_id': int(row['site_id'] or 0),
            'enabled': bool(row['enabled']),
            'has_state': bool(row['has_state']),
            'has_url': bool(url),
            # Из снимка, если держится: там адрес уже обрезан. Иначе режем сами —
            # ⚠ токен сессии наружу не отдаём ни из одного из двух путей.
            'ws_url': (held or {}).get('ws_url') or _short(url),
            'held': held is not None,
            'alive': bool((held or {}).get('alive')),
            'messages': int((held or {}).get('messages') or 0),
            'status': str(row['status'] or ''),
            'last_error': str(row['last_error'] or ''),
        })

    return {'result': {'sockets': sockets,
                       'alive': snapshot['alive'],
                       'age_sec': snapshot['age_sec'],
                       'shift_left': snapshot.get('shift_left', 0),
                       'held': sum(1 for s in sockets if s['held']),
                       'ready': sum(1 for s in sockets if s['has_url'])}}


def _short(url: str) -> str:
    """Адрес без токена — тем же правилом, что в снимке."""
    if not url:
        return ''

    head = url.split('?')[0]

    return f'{head}…{url[-6:]}' if len(url) > len(head) + 6 else head
