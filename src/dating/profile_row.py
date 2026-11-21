"""Строка анкеты по номеру — для кругов и исполнителей, которым нужна вся учётка.

⚠ Одна дверь на дело, которое стояло приватными копиями в каждом круге
(`initiative_round`, `invite_send`, `mail_reply`, `panel_presence`).
Отправке нужна строка целиком — `send_message` читает из неё и сайт, и
выключатель, и номер на сайте, — а урезанная копия однажды не донесла бы
нужную колонку молча.

⚠ Ручкам HTTP это не замена: `routers/dating/profile_.profile_row` отвечает
`404`, а здесь пустой словарь — круг решает сам, что делать без анкеты.
"""

from mysql_.mysql_ import mysql_get_db_async


async def profile_row_get(profile_id: int) -> dict:
    """Анкета целиком; нет такой — пустой словарь."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    return dict(row) if row else {}
