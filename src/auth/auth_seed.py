"""Первый пользователь: чтобы в свежую панель было чем войти.

Заводится на старте и **только когда таблица пуста**. Не «создать или обновить»:
иначе правка пароля в `.env` молча меняла бы его у живого пользователя, а забытый
в образце `change-me` возвращался бы после каждого перезапуска.

Пароль берётся из окружения и в базу ложится хешем. В журнал не попадает даже
частично: строка «завёл admin» полезна, строка с паролем — нет.
"""

from config.config import config_get
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import auth_password_hash, auth_token_new

# Пометка токена, который заводится вместе с первым пользователем: им ходят
# проверки и скрипты, и по пометке видно, что он служебный, а не чей-то личный.
AUTH_SEED_TOKEN_NOTE = 'первый токен установки'


async def auth_seed_first_user() -> None:
    """Заводит первого пользователя, если их ещё нет. Молчит, если есть."""
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

        await db.execute('INSERT INTO user (username, password_hash) VALUES (%s, %s)',
                         (username, auth_password_hash(password)))
        user_id = db.lastrowid

        # Токен заводим сразу: без него первую же проверку API пришлось бы делать
        # куками из браузера, а это не тот способ, которым живут скрипты.
        await db.execute('INSERT INTO user_access_token (user_id, token, note) VALUES (%s, %s, %s)',
                         (user_id, auth_token_new(), AUTH_SEED_TOKEN_NOTE))
        await db.connection.commit()

    logger_info(f'[auth] заведён первый пользователь «{username}» и токен доступа к нему')
