import asyncio
import re

from mysql_.mysql_ import mysql_get_db_async
from src.dating.json_column import json_column_load
from src.dating.mail_agent import mail_write
from src.uvicorn.routers.dating.mail_var import mail_var_rows

INVENT = r'\b(cat|dog|kitten|puppy|balcony|bakery|piano|guitar|hamster|parrot)\b'


async def main():
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, login, profile FROM profile'
                         ' WHERE site_id=343 AND enabled=1 ORDER BY id LIMIT 3')
        girls = await db.fetchall()

    rows = await mail_var_rows(343)
    for girl in girls:
        card = json_column_load(girl.get('profile')) or {}
        name = str(card.get('name') or '')
        body = await mail_write(343, rows, girl=girl)
        head = body.strip().split('\n')[0][:52]

        own = bool(name and re.search(r'\b' + re.escape(name) + r'\b', head))
        mark = '[[companion_name]]' in body
        invent = sorted(set(re.findall(INVENT, body.lower())))

        print(f"  {name:10} метка:{'да ' if mark else 'нет'}"
              f"  своим именем:{'ДА ⚠' if own else 'нет'}"
              f"  выдумка:{invent or '—'}", flush=True)
        print(f"    «{head}»", flush=True)


asyncio.run(main())
