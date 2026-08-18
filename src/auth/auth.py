"""Вход в панель и проверка «свой ли».

Прав здесь ровно два состояния: вошёл или нет. Ролевой матрицы нет намеренно —
весь проект про одну вещь, анкеты на сайтах знакомств, и делить его на «смотреть»
и «править» значило бы завести бухгалтерию ради одной страницы. Понадобится
разделение — оно появится тогда, когда появится второй род работы.

Два способа представиться, как и у соседа:

* **сессия** — кука админки, ею ходит человек со страницы;
* **токен** — `Authorization: Bearer <token>` или `X-API-KEY`, им ходят скрипты и
  проверки; сессии у них нет и быть не может.

Пароли лежат хешем (`bcrypt`). Сравнение всегда идёт по хешу, даже когда логина
нет в базе: иначе время ответа выдало бы, существует ли такой пользователь.
"""

import secrets

import bcrypt
from fastapi import HTTPException, Request, status

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Заголовки, которыми принимается токен. Два, потому что `Bearer` общепринят, а
# `X-API-KEY` привычен интеграциям.
AUTH_TOKEN_HEADERS = ('authorization', 'x-api-key')
AUTH_TOKEN_PREFIX = 'bearer '

# Хеш-заглушка для несуществующего логина: сравнение с ней стоит столько же,
# сколько с настоящим, и по времени ответа перебрать имена не выйдет.
AUTH_DUMMY_HASH = bcrypt.hashpw(b'-', bcrypt.gensalt()).decode()


class AuthUser:
    """Кто выполняет запрос. Ровно то, что нужно журналу и страницам."""

    def __init__(self, user_id: int, username: str):
        self.user_id = int(user_id)
        self.username = str(username)

    def __repr__(self) -> str:
        return f'#{self.user_id} {self.username}'


async def auth_required(request: Request) -> AuthUser:
    """Зависимость точек: пускает своего, чужому отвечает 401.

    Одна на весь проект: новый способ представиться появляется сразу везде, а не
    в тех точках, куда его не забыли добавить.
    """
    user_id = request.session.get('user_id', 0)
    username = request.session.get('username', '')

    if user_id and username:
        return AuthUser(user_id, username)

    token = auth_token_of(request)
    if token:
        user = await auth_token_user(token)
        if user:
            return user

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='нужен вход')


def auth_token_of(request: Request) -> str:
    """Токен из заголовков запроса. Нет ни одного — пустая строка.

    `Bearer` необязателен: голый токен в `Authorization` тоже принимается — так
    его шлют из curl чаще, чем по спецификации, и отвергать за это жестоко.
    """
    for name in AUTH_TOKEN_HEADERS:
        value = str(request.headers.get(name) or '').strip()
        if not value:
            continue
        if value.lower().startswith(AUTH_TOKEN_PREFIX):
            return value[len(AUTH_TOKEN_PREFIX):].strip()

        return value

    return ''


async def auth_token_user(token: str) -> AuthUser | None:
    """Владелец токена. Нет такого — None, решает вызывающий."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT u.id, u.username FROM user_access_token t '
                '  JOIN user u ON u.id = t.user_id WHERE t.token = %s', (str(token),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[auth] токен не проверился: {e}')
        return None

    return AuthUser(int(row['id']), str(row['username'])) if row else None


async def auth_user_check(username: str, password: str) -> AuthUser | None:
    """Проверяет пару логин-пароль. Не сошлось — None, без подробностей.

    Почему именно не сошлось, наружу не уходит: «нет такого пользователя» и
    «неверный пароль» — разные ответы только для того, кто перебирает логины.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, username, password_hash FROM user WHERE username = %s',
                         (str(username).strip(),))
        row = await db.fetchone()

    stored = str(row['password_hash']) if row else AUTH_DUMMY_HASH
    try:
        ok = bcrypt.checkpw(str(password).encode(), stored.encode())
    except (ValueError, TypeError):
        ok = False

    if not row or not ok:
        return None

    return AuthUser(int(row['id']), str(row['username']))


def auth_password_hash(password: str) -> str:
    """Хеш пароля для записи в базу. Открытый пароль не хранится нигде."""
    return bcrypt.hashpw(str(password).encode(), bcrypt.gensalt()).decode()


def auth_token_new() -> str:
    """Новый токен доступа. 32 байта случайности — перебирать нечего."""
    return secrets.token_urlsafe(32)
