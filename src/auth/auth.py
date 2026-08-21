"""Вход в панель и проверка «свой ли».

Ролей две, и это не матрица прав, а один вопрос: пускать ли к устройству
установки.

| Роль | Что доступно |
|------|--------------|
| `dating` | анкеты: список, чат, ответы, журнал — всё, ради чего панель открывают |
| `admin` | то же плюс seed, бэкапы и веб-морда к базе |

Дробить дальше не надо: работа с анкетой неделима — кто видит переписку, тот на
неё и отвечает. Разделение появилось ровно тогда, когда появился второй род
работы: операции, которые правят или выносят базу целиком.

Два способа представиться, как и у соседа:

* **сессия** — кука админки, ею ходит человек со страницы;
* **токен** — `Authorization: Bearer <token>` или `X-API-KEY`, им ходят скрипты и
  проверки; сессии у них нет и быть не может.

Пароли лежат хешем (`bcrypt`). Сравнение всегда идёт по хешу, даже когда логина
нет в базе: иначе время ответа выдало бы, существует ли такой пользователь.
"""

import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request, status

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Заголовки, которыми принимается токен. Два, потому что `Bearer` общепринят, а
# `X-API-KEY` привычен интеграциям.
AUTH_TOKEN_HEADERS = ('authorization', 'x-api-key')
AUTH_TOKEN_PREFIX = 'bearer '

# Хеш-заглушка для несуществующего логина: сравнение с ней стоит столько же,
# сколько с настоящим, и по времени ответа перебрать имена не выйдет.
AUTH_DUMMY_HASH = bcrypt.hashpw(b'-', bcrypt.gensalt()).decode()

# Роли. `admin` — единственная, дающая доступ к устройству установки; всё
# остальное считается работой с анкетами. Сравнение идёт с этой константой, а не
# со строкой на месте: опечатка в строковом литерале открыла бы доступ молча.
AUTH_ROLE_ADMIN = 'admin'
AUTH_ROLE_DATING = 'dating'


class AuthUser:
    """Кто выполняет запрос. Ровно то, что нужно журналу, страницам и правам."""

    def __init__(self, user_id: int, username: str, role: str = AUTH_ROLE_DATING):
        self.user_id = int(user_id)
        self.username = str(username)
        # Неизвестное значение роли считается наименьшими правами: строка в базе
        # может оказаться какой угодно, и трактовать «непонятно что» как админа
        # означало бы открывать доступ по опечатке.
        self.role = AUTH_ROLE_ADMIN if str(role) == AUTH_ROLE_ADMIN else AUTH_ROLE_DATING

    @property
    def is_admin(self) -> bool:
        """Пускать ли к seed, бэкапам и прочему устройству установки."""
        return self.role == AUTH_ROLE_ADMIN

    def __repr__(self) -> str:
        return f'#{self.user_id} {self.username} ({self.role})'


async def auth_required(request: Request) -> AuthUser:
    """Зависимость точек: пускает своего, чужому отвечает 401.

    Одна на весь проект: новый способ представиться появляется сразу везде, а не
    в тех точках, куда его не забыли добавить.
    """
    user_id = request.session.get('user_id', 0)
    username = request.session.get('username', '')

    if user_id and username:
        # Роль читается из куки, а не из базы: `auth_required` висит на каждой
        # точке, а страница опрашивает их пачками каждые пятнадцать секунд —
        # запрос к базе на каждый вызов заметен. Плата за это одна: смена роли
        # доезжает после перелогина, но не позже — кука живёт час.
        return AuthUser(user_id, username, request.session.get('role', AUTH_ROLE_DATING))

    token = auth_token_of(request)
    if token:
        user = await auth_token_user(token)
        if user:
            return user

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='нужен вход')


async def auth_admin_required(user: AuthUser = Depends(auth_required)) -> AuthUser:
    """Зависимость точек, которые правят или выносят установку: seed, бэкапы.

    Отдельная зависимость, а не проверка в теле: право видно в объявлении роута,
    и точку, где её забыли, находит чтение файла, а не разбор происшествия.

    403, а не 404: скрывать существование страницы незачем — она есть, и человек
    видит её у коллеги. «Нельзя» понятнее, чем «нет такой».
    """
    if not user.is_admin:
        logger_info(f'[auth] отказано {user}: нужна роль {AUTH_ROLE_ADMIN}')
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail='нужна роль администратора')

    return user


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
                'SELECT u.id, u.username, u.role FROM user_access_token t '
                '  JOIN user u ON u.id = t.user_id WHERE t.token = %s', (str(token),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[auth] токен не проверился: {e}')
        return None

    # У токена роль всегда свежая: он читается из базы на каждый запрос, сессии
    # у скрипта нет и кэшировать нечего.
    return AuthUser(int(row['id']), str(row['username']), str(row['role'])) if row else None


async def auth_user_check(username: str, password: str) -> AuthUser | None:
    """Проверяет пару логин-пароль. Не сошлось — None, без подробностей.

    Почему именно не сошлось, наружу не уходит: «нет такого пользователя» и
    «неверный пароль» — разные ответы только для того, кто перебирает логины.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, username, password_hash, role FROM user WHERE username = %s',
                         (str(username).strip(),))
        row = await db.fetchone()

    stored = str(row['password_hash']) if row else AUTH_DUMMY_HASH
    try:
        ok = bcrypt.checkpw(str(password).encode(), stored.encode())
    except (ValueError, TypeError):
        ok = False

    if not row or not ok:
        return None

    return AuthUser(int(row['id']), str(row['username']), str(row['role']))


def auth_password_hash(password: str) -> str:
    """Хеш пароля для записи в базу. Открытый пароль не хранится нигде."""
    return bcrypt.hashpw(str(password).encode(), bcrypt.gensalt()).decode()


def auth_token_new() -> str:
    """Новый токен доступа. 32 байта случайности — перебирать нечего."""
    return secrets.token_urlsafe(32)
