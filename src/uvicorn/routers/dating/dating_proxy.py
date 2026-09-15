"""Пул выходов глазами панели: что есть в реестре и как оно у нас занято.

⚠ **Реестр живёт у соседа, а этот файл — про нашу сторону.** Он отвечает на
вопросы, которых у соседа нет и быть не может:

* сколько **наших анкет** сидит за каждым увиденным адресом;
* попадает ли адрес в предел сайта (`proxy_seats_per_ip`).

Про анкеты и сайты знакомств в реестре не знают — там только выходы и их
проверка (`RhapsodyAi/.claude/docs/browser.md`, §4.3).

⚠ **Пароль канала наружу не уходит.** Страница показывает адрес, пинг и
занятость; строка подключения нужна тому, кто через неё пойдёт, и берётся она в
`src/dating/dating_proxy.py`.
"""

from fastapi import APIRouter, Depends, HTTPException

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required, auth_admin_required
from http_.http_proxy import http_proxy_direct_ip

from src.dating.dating_proxy import (dating_proxy_countries, dating_proxy_enabled,
                                     dating_proxy_pool, dating_proxy_seats)
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_proxy_check_all

router = APIRouter(prefix='/api/dating/proxy', tags=['dating'])


@router.get('', include_in_schema=False)
async def dating_proxy_list(site_id: int = 0, user: AuthUser = Depends(auth_required)):
    """Пул выходов для страницы «Настройки сайтов» → вкладка «Прокси».

    Args:
        site_id: сайт, чьими глазами смотрим. Ноль — без отбора и без предела:
            просто что есть в реестре.

    Returns:
        dict: `enabled` — пользуется ли сайт выходами вообще; `result` — выходы в
        том порядке, в каком их показывать; `seats` — предел сайта; `direct` —
        наш собственный адрес, для сравнения; `countries` — какие страны есть в
        реестре и сколько в них живых каналов; `picked` — какие из них отмечены
        (пусто — берём из любых).

    ⚠ **Выключено — к соседу не идём и своего адреса не спрашиваем.** Обе строки
    ходят по сети, и делать это ради вкладки, где показывать нечего, незачем:
    страница рисует одну галочку. Пустой `result` при `enabled: false` значит «мы
    не смотрели», а не «реестр пуст», — страница эти случаи различает.

    ⚠ **Порядок задаёт сервер, а не страница.** Годные сверху, мёртвые вниз — и
    это не украшение: человек смотрит сюда, чтобы понять, чем сейчас можно
    работать. Сортируй страница сама, правило разошлось бы с тем, по которому
    раздаются выходы.
    """
    # ⚠ Проверяем **до** любого похода наружу: см. пояснение выше.
    if int(site_id or 0) and not await dating_proxy_enabled(int(site_id)):
        return {'result': [], 'enabled': False, 'seats': 0,
                'direct': {}, 'countries': [], 'picked': []}

    try:
        # ⚠ **Свой адрес — рядом со списком, а не отдельным запросом.** Он нужен
        # ровно для сравнения: выход, у которого увиденный IP совпал с нашим,
        # ничего не скрывает. Вторым запросом страница получала бы его позже
        # таблицы и перерисовывала её ещё раз.
        rows = await dating_proxy_pool(int(site_id or 0))

        return {'result': rows,
                'enabled': True,
                'seats': await dating_proxy_seats(int(site_id or 0)),
                'direct': await http_proxy_direct_ip(),
                # ⚠ Страны считаем **по всему реестру**, а не по отобранным:
                # чипсы должны показывать и те, что сейчас выключены, — иначе
                # включить их обратно будет нечем.
                'countries': _countries(rows),
                'picked': await dating_proxy_countries(int(site_id or 0))}
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=f'реестр выходов не ответил: {e}')


@router.post('/check', include_in_schema=False)
async def dating_proxy_check(user: AuthUser = Depends(auth_admin_required)):
    """Проверяет все выходы прямо сейчас — кнопкой, не дожидаясь круга.

    ⚠ **Проверку делает реестр, а не мы.** Своя пошла бы с адреса нашего
    контейнера, и увиденный IP оказался бы другим — тем, который мы же и
    считаем. Одна проверка, один источник.

    Админу, а не всем вошедшим: это поход наружу через два десятка каналов, и
    нажимать его из любопытства не стоит.
    """
    try:
        rows = await rhapsody_api_proxy_check_all()
    except RhapsodyError as e:
        raise HTTPException(status_code=502, detail=f'реестр не ответил: {e}')

    ok = sum(1 for row in rows if row.get('ok'))
    logger_info(f'[proxy] проверка по кнопке: {len(rows)} выходов, годных {ok}')

    return {'result': {'checked': len(rows), 'ok': ok}}


# ── Приватное ────────────────────────────────────────────────────────────────

def _countries(rows: list) -> list[dict]:
    """Страны реестра: код, сколько каналов и сколько из них живых.

    ⚠ **Живые считаем без оглядки на отбор.** Строка чипса отвечает на вопрос
    «что я получу, включив эту страну», и «0 живых» у выключенной страны, где на
    самом деле три рабочих канала, оттолкнул бы от единственного верного шага.

    Каналы без страны (не проверены либо служба её не назвала) в чипсы не идут:
    включать «пусто» человеку нечего, а место в полосе оно занимает.
    """
    seen: dict[str, dict] = {}

    for row in rows:
        code = str(row.get('check_country') or '')
        if not code:
            continue

        got = seen.setdefault(code, {'code': code, 'total': 0, 'live': 0})
        got['total'] += 1
        # «Живой» здесь — без страновой отсечки: см. пояснение выше.
        if row.get('check_ok') and row.get('is_enabled') and row.get('check_ip'):
            got['live'] += 1

    return sorted(seen.values(), key=lambda one: (-one['live'], one['code']))
