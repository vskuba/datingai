"""Ручное письмо из «Фавориты → Диалоги» — **ответом на его письмо**, если оно ждёт.

## ⚠⚠ Что чинится

Кнопка «Отправить» под разговором клала текст в пул «Приставания» и уносила его
ручкой API под анкетой — **новым письмом**. Агент же отвечает на его письмо формой
«Reply» панели агентства (`mail_reply_panel.py`), и только так панель считает
письмо отвеченным. Человек, ответивший руками, оставлял письмо в панели
«Unanswered» — ровно то, ради чего панельный путь заводили (`mail_reply_panel.py`,
«Зачем второй путь»).

## Правило (владельца, 28.11.2026)

Сперва — есть ли у пары **его неотвеченное письмо, на которое панель ещё даёт
ответить**. Есть — текст уходит ответом на него, **той же дверью, что у агента**:
строка пула `mail_reply` и `mail_send_one(table='mail_reply')` со всей развилкой
(живой взгляд «не ответил ли уже человек», панель, досылка ручкой по настройке).
Нет — прежняя дорога письма избранному.

⚠ Спрашиваем **панель**, а не `mail_in.is_replied`: флаг ставит только забор, и
между ним и кнопкой бывают часы; форма ответа в панели — та самая правда, по
которой агентство считает письмо отвеченным.

⚠ Только там, где ответы и так ходят панелью (`mail_reply_panel_due`): на прочих
сайтах развилки «панель или ручка» нет, и правило про неё не о чем.
"""
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_reply_panel import (MAIL_REPLY_PANEL_PATH,
                                         mail_reply_panel_can,
                                         mail_reply_panel_due)
from src.dating.mail_send import DatingMailSendError
from src.dating.panel_fetch import DatingPanelFetchError, panel_fetch_html

# Сколько его последних писем спрашивать у панели. Ответ уходит на **самое
# свежее** открытое; глубже пяти неотвеченных писем одной пары не бывает
# по делу, а каждый вопрос — поход в панель.
MAIL_REPLY_MANUAL_DEPTH = 5


async def mail_reply_manual_target(site_id: int, profile_id: int,
                                   uid: str) -> dict | None:
    """Его письмо, на которое ручной текст обязан уйти ответом. `None` — такого нет.

    Returns:
        dict | None: строка `mail_in` (`id`, `uid`, `name`, `message_id`) и
        `reply_id` — черновик агента на это письмо, если лежит.

    Raises:
        DatingMailSendError: панель не открылась — неизвестно, ждёт ли письмо
            ответа. Слать новым письмом наугад нельзя: это ровно та ошибка,
            которую здесь чинят.
    """
    if not await mail_reply_panel_due(int(site_id), int(profile_id), str(uid)):
        return None

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT i.id, i.uid, i.name, i.message_id, r.id AS reply_id'
            '  FROM mail_in i'
            '  LEFT JOIN mail_reply r ON r.mail_in_id = i.id'
            ' WHERE i.profile_id = %s AND i.uid = %s AND i.message_id > 0'
            "   AND (r.id IS NULL OR r.status = 'draft')"
            ' ORDER BY i.date_post DESC, i.id DESC LIMIT %s',
            (int(profile_id), str(uid), MAIL_REPLY_MANUAL_DEPTH))
        rows = [dict(one) for one in await db.fetchall() or []]

    for row in rows:
        try:
            page = await panel_fetch_html(
                int(site_id), f'{MAIL_REPLY_PANEL_PATH}/{int(row["message_id"])}')
        except DatingPanelFetchError as e:
            raise DatingMailSendError(
                f'панель не открыла его письмо — не знаем, ждёт ли оно ответа: {e}')

        if mail_reply_panel_can(page, int(row['message_id'])):
            return row

    return None


async def mail_reply_manual_put(site_id: int, profile_id: int, target: dict,
                                body: str) -> dict:
    """Кладёт ручной ответ в пул ответов и отдаёт строку, готовую к отправке.

    ⚠⚠ **Черновик агента на то же письмо уступает место**: ответ на письмо один
    (`uq_dating_mail_reply`), и человек, ответивший руками, решил за агента. Его
    обмен у соседа остаётся недоставленным — агент его не увидит
    (`delivered_at`), то есть сказанным не считает.

    ⚠ `message_id` пуст: обмена у соседа этот текст не рождал. В историю агента
    он ложится после удачи (`mail_send_history`), как ответ, написанный руками.
    """
    async with mysql_get_db_async() as db:
        if int(target.get('reply_id') or 0):
            await db.execute("DELETE FROM mail_reply WHERE id = %s AND status = 'draft'",
                             (int(target['reply_id']),))
            logger_info(f'[dating] черновик агента #{target["reply_id"]} уступил '
                        f'ручному ответу на письмо #{target["id"]}')

        await db.execute(
            'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid, name,'
            " body, status, gallery_file_id) VALUES (%s, %s, %s, %s, %s, %s,"
            " 'draft', 0)",
            (int(site_id), int(profile_id), int(target['id']),
             str(target.get('uid') or ''), str(target.get('name') or ''), str(body)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')
        reply_id = int((await db.fetchone())['id'])

    return {'id': reply_id, 'site_id': int(site_id), 'profile_id': int(profile_id),
            'mail_in_id': int(target['id']), 'uid': str(target.get('uid') or ''),
            'name': str(target.get('name') or ''), 'body': str(body),
            'status': 'draft', 'reply_to': int(target['message_id']),
            'message_id': None, 'gallery_file_id': 0}
