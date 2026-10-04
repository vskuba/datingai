"""Учётки, которые заводятся сами: чтобы в свежую панель было чем войти.

Две штуки, по числу ролей:

* **администратор** (`ADMIN_USERNAME`) — заводится, только когда таблица пуста.
  Это хозяин установки: seed, бэкапы, база.
* **оператор** (`DATING_USERNAME`) — заводится, когда такого логина ещё нет. Ему
  доступны анкеты и больше ничего.

Разница в условии не случайна. Администратор — «первый пользователь»: пустая
таблица и есть признак свежей установки, а на живой заводить второго админа по
переменной окружения нельзя. Оператор же появляется и на живой установке — роль
добавили позже, чем саму панель, — поэтому проверяется не пустота таблицы, а
наличие конкретного логина.

Ни один из них не «создать или обновить»: правка пароля в `.env` молча меняла бы
его у живой учётки, а забытый в образце `change-me` возвращался бы после каждого
перезапуска.

Пароль берётся из окружения и в базу ложится хешем. В журнал не попадает даже
частично: строка «завёл admin» полезна, строка с паролем — нет.
"""

from auth_.auth_primitive import auth_password_hash, auth_token_new
from config.config import config_get
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AUTH_ROLE_ADMIN, AUTH_ROLE_DATING

# Пометка токена, который заводится вместе с первым пользователем: им ходят
# проверки и скрипты, и по пометке видно, что он служебный, а не чей-то личный.
AUTH_SEED_TOKEN_NOTE = 'первый токен установки'


async def auth_seed_first_user() -> None:
    """Заводит администратора, если учёток ещё нет. Молчит, если есть."""
    username = str(config_get('ADMIN_USERNAME', '')).strip()
    password = str(config_get('ADMIN_PASSWORD', ''))

    if not username or not password:
        logger_info('[auth] первый пользователь не заведён: нет ADMIN_USERNAME/ADMIN_PASSWORD')
        return

    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM user')
        row = await db.fetchone()
        if int((row or {}).get('n') or 0):
            return

        await db.execute('INSERT INTO user (username, password_hash, role) VALUES (%s, %s, %s)',
                         (username, auth_password_hash(password), AUTH_ROLE_ADMIN))
        user_id = db.lastrowid

        # Токен заводим сразу: без него первую же проверку API пришлось бы делать
        # куками из браузера, а это не тот способ, которым живут скрипты.
        await db.execute('INSERT INTO user_access_token (user_id, token, note) VALUES (%s, %s, %s)',
                         (user_id, auth_token_new(), AUTH_SEED_TOKEN_NOTE))
        await db.connection.commit()

    logger_info(f'[auth] заведён первый пользователь «{username}» и токен доступа к нему')


async def auth_seed_operator() -> None:
    """Заводит оператора анкет, если такого логина ещё нет.

    Токена ему не полагается: оператор — человек за страницей, а не скрипт. Нужен
    токен — он заводится осознанно и с пометкой, кому выдан.
    """
    username = str(config_get('DATING_USERNAME', '')).strip()
    password = str(config_get('DATING_PASSWORD', ''))

    if not username or not password:
        logger_info('[auth] оператор не заведён: нет DATING_USERNAME/DATING_PASSWORD')
        return

    async with mysql_get_db_async() as db:
        await db.execute('SELECT id FROM user WHERE username = %s', (username,))
        if await db.fetchone():
            return

        await db.execute('INSERT INTO user (username, password_hash, role) VALUES (%s, %s, %s)',
                         (username, auth_password_hash(password), AUTH_ROLE_DATING))
        await db.connection.commit()

    logger_info(f'[auth] заведён оператор анкет «{username}»')
