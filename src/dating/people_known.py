"""Люди, которых мы уже знаем: строки списка из наших карточек.

Третий источник строк для лент собеседников — после сценария у соседа
(`people.people_rows`) и API самого сайта (`people_api.people_api_rows`). Этот
не ходит никуда: номера ему приносит вызывающий, а имя, лицо и возраст берутся
из `companion` — карточек, которые панель и так заводит каждому адресату.

## ⚠⚠ Зачем он понадобился: сайт говорит «кто», а не «какие они»

У сайта без сценариев и без API список избранных всё-таки есть — он приезжает в
кадре авторизации сокета (`site/site_favorite.py`), но **одними номерами**.
Номер в ленте читается как ничто: `1821030` вместо «Lance, 58». Карточка же у
нас есть, и ходить за ней второй раз на сайт незачем.

## ⚠ Чем это **не** является

Карточка `companion` ключуется парой «сайт + номер», то есть одна на человека, и
«чей он избранный» в ней не записано. Это знание живёт в двух других местах: у
нас — в `favorite` (пара «анкета + номер»), а истина — на самом сайте, где
список свой у каждой девушки. Отсюда и разделение работы: **чьи** номера
спрашивает вызывающий, **кто эти люди** отвечает этот файл.

⚠ Набор полей — тот же, что у двух прежних дорог. Страница и кэш об источнике не
знают и знать не должны: разойдись поля, лента рисовала бы карточки без лиц
ровно у одного сайта.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.people import PEOPLE_NAME_MAX, PEOPLE_UID_MAX, people_photo_uid


async def people_known_rows(site_id: int, uids) -> list[dict]:
    """Строки ленты по номерам: что знаем — из карточек, остального нет.

    Args:
        site_id: сайт, на котором эти номера что-то значат.
        uids: номера людей **в том порядке**, в каком их показывать.

    Returns:
        list: записи `{uid, name, age, photo, photo_uid, online, is_new, link}`
        — ровно те же поля, что у сценария и у API.

    ⚠ **Порядок задаёт вызывающий**, а не база: у него он от сайта, а `IN (…)`
    вернул бы строки в своём.

    ⚠ **Незнакомый номер остаётся строкой**, а не выбрасывается: человека могли
    добавить на сайте руками, и список короче сайтового соврал бы именно там,
    где панель с сайтом и сверяют.

    ⚠ `online` здесь всегда `False`: этот источник про то, кто эти люди, а не
    про то, кто сейчас в сети. Точку ставит ручка ленты по снимку «Все онлайн»
    (`_online_now`), и выдумывать её тут значило бы спорить с ним.
    """
    want = [str(one).strip() for one in (uids or []) if str(one).strip()]
    if not want:
        return []

    cards = await _cards(int(site_id or 0), want)

    return [_row(uid, cards.get(uid) or {}) for uid in want]


# ── Приватное ────────────────────────────────────────────────────────────────


async def _cards(site_id: int, uids: list[str]) -> dict:
    """Карточки этих номеров: `{uid: строка}`. Сбой чтения — пустой словарь.

    ⚠ Молча и пусто, а не отказом: без карточек лента покажет номера, а без
    ленты человек не увидит ничего. Строка в журнале процесса остаётся.
    """
    holes = ', '.join(['%s'] * len(uids))

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT uid, name, photo, age FROM companion'
                f' WHERE site_id = %s AND uid IN ({holes})',
                tuple([int(site_id)] + uids))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] карточки собеседников сайта #{site_id} '
                    f'не прочитались: {e}')

        return {}

    return {str(row['uid']): dict(row) for row in rows or []}


def _row(uid: str, card: dict) -> dict:
    """Одна строка ленты. Пустая карточка — номер и ничего больше.

    ⚠ Имя не правится регистром: в `companion` оно уже легло через
    `people_name_case` (`companion_card.py` — единственная дверь записи), и
    второй правки тут быть не должно.
    """
    photo = str(card.get('photo') or '').strip()
    age = card.get('age')

    return {'uid': uid[:PEOPLE_UID_MAX],
            'name': str(card.get('name') or '')[:PEOPLE_NAME_MAX],
            'age': int(age) if isinstance(age, int) else 0,
            'photo': photo,
            'photo_uid': people_photo_uid(photo),
            'online': False,
            'is_new': False,
            'link': ''}
