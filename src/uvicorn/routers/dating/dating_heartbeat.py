"""Присутствие на сайте: ручки страницы и проверка настройки.

Три ручки, и каждая отвечает на свой вопрос человека, настраивающего новый сайт:

  **настроено ли** и кого сейчас держим — `GET /api/dating/heartbeat`;
  **правильно ли настроено** — `POST /api/dating/heartbeat/check`, один живой
  заход под выбранной анкетой;
  **работает ли прямо сейчас** — `POST /api/dating/heartbeat/beat`, удар вне круга.

⚠ **Проверка нужна именно ручкой.** Настройка присутствия — JSON с путями чужого
сайта, и написать его с первого раза не выходит: ошибёшься в пути — круг молча
бьёт в никуда, ошибёшься в примете живого входа — сайт «всегда онлайн». Без
кнопки «проверить» это обнаружилось бы через сутки и по косвенным признакам.

Устройство самого присутствия — `.claude/docs/heartbeat.md`, движок —
`docs-common/http_heartbeat.md`.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mysql_.mysql_ import mysql_get_db_async

from http_.http_heartbeat import (http_heartbeat_check, http_heartbeat_client,
                                  http_heartbeat_cookies, http_heartbeat_ready,
                                  http_heartbeat_send)

from src.auth.auth import AuthUser, auth_required
from src.dating.dating_heartbeat import (dating_heartbeat_config,
                                         dating_heartbeat_every,
                                         dating_heartbeat_sessions)
from src.dating.dating_json import dating_json_load

router = APIRouter(prefix='/api/dating/heartbeat', tags=['dating'])


class DatingHeartbeatAsk(BaseModel):
    """Под какой анкетой проверять настройку сайта."""

    profile_id: int = Field(..., gt=0, description='Анкета: её вход и её сессия')


@router.get('', include_in_schema=False)
async def dating_heartbeat_state(site_id: int = 0,
                                 user: AuthUser = Depends(auth_required)):
    """Настроено ли присутствие у сайта и сколько анкет готовы его держать.

    Args:
        site_id: сайт-закладка. Ноль — пустой ответ: настройка живёт у сайта.

    Returns:
        dict: `ready` — настроено ли; `every` — период удара в секундах; `base` —
        корень сайта из настройки; `sessions` — сколько анкет с живой сессией;
        `steps` — какие шаги описаны.

    ⚠ **Саму настройку наружу не отдаём** — только её очертания. В ней бывают
    заголовки и тела запросов чужого сайта; читать и править её положено на
    странице «Сайты», где у настроек свои права и своя маскировка.
    """
    if not int(site_id or 0):
        return {'result': {'ready': False, 'every': 0, 'base': '',
                           'sessions': 0, 'steps': []}}

    config = await dating_heartbeat_config(int(site_id))
    sessions = await dating_heartbeat_sessions(int(site_id))

    return {'result': {
        'ready': http_heartbeat_ready(config),
        'every': await dating_heartbeat_every(int(site_id)),
        'base': str(config.get('base') or ''),
        'sessions': len(sessions),
        'steps': [name for name in ('beat', 'alive', 'unread') if config.get(name)],
    }}


@router.post('/check', include_in_schema=False)
async def dating_heartbeat_check_run(payload: DatingHeartbeatAsk,
                                     user: AuthUser = Depends(auth_required)):
    """Полная проверка настройки под одной анкетой: удар, вход, счётчик.

    Отвечает тем же, чем движок: `online` — настройка верна и вход жив; `offline`
    — сайт нас не признал (нужен повторный вход **или** неверна примета);
    `error` — до сайта не достучались.

    Raises:
        HTTPException: 404 — анкеты нет; 409 — у сайта нет настройки присутствия.

    ⚠ **Заход живой и идёт на сайт.** Он безобиден — тот же запрос шлёт обычная
    страница, — но это обращение наружу, и делается оно по нажатию человека, а не
    само.
    """
    profile = await _profile(int(payload.profile_id))
    config = await dating_heartbeat_config(int(profile['site_id']))

    if not http_heartbeat_ready(config):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='у сайта не настроено присутствие: заполните «Присутствие: '
                   'настройка» на странице «Сайты»')

    cookies = http_heartbeat_cookies(dating_json_load(profile['storage_state']),
                                     str(config.get('base') or ''))

    client = http_heartbeat_client()
    try:
        result = await http_heartbeat_check(client, cookies, config)
    finally:
        await client.aclose()

    return {'result': result}


@router.post('/beat', include_in_schema=False)
async def dating_heartbeat_beat_run(payload: DatingHeartbeatAsk,
                                    user: AuthUser = Depends(auth_required)):
    """Один удар за анкету, вне круга и вне расписания.

    Нужен там, где круг ждать нельзя: человек правит настройку и хочет увидеть
    результат сразу, а не через полминуты.

    ⚠ **Расписание здесь не спрашивается намеренно.** Это отладочный удар по
    кнопке, а не работа по часам: спроси мы смену, проверить настройку днём было
    бы невозможно у анкеты, работающей ночью.

    Raises:
        HTTPException: 404 — анкеты нет; 409 — нет настройки или нет сессии.
    """
    profile = await _profile(int(payload.profile_id))
    config = await dating_heartbeat_config(int(profile['site_id']))

    if not config.get('beat'):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='у сайта не описан удар («beat» в настройке)')

    cookies = http_heartbeat_cookies(dating_json_load(profile['storage_state']),
                                     str(config.get('base') or ''))
    if not cookies:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='анкета не входила на сайт: удар слать некуда — нажмите «Войти»')

    client = http_heartbeat_client()
    try:
        sent = await http_heartbeat_send(client, cookies, config)
    finally:
        await client.aclose()

    return {'result': {'sent': bool(sent)}}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _profile(profile_id: int) -> dict:
    """Анкета со всем, что нужно для удара. 404 — если её нет."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, login, storage_state'
                         ' FROM dating_profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='анкета не найдена')

    return dict(row)
