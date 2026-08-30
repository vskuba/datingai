"""DatingAi — панель анкет на сайтах знакомств.

Проект делает ровно одно: держит учётки девушек, показывает их страницей и ведёт
живой чат вебсокетом. Всё, что требует браузера и мозгов, живёт в RhapsodyAi и
приходит сюда по HTTP (`src/rhapsody/rhapsody_api.py`):

* сценарии сайтов и пул Chromium — вход, чтение анкеты, диалоги;
* агенты, workflow, факты и LLM — ответы в чате.

Отсюда и состав: база из шести таблиц, страница, вебсокет-клиент и один воркер.
Ни очередей на все случаи жизни, ни ролевой матрицы, ни поиска по векторам —
всего этого здесь нет намеренно, а не потому, что не дошли руки.

Запуск: `uvicorn main_uvicorn:app --host 0.0.0.0 --port 8000` (см. compose).
"""

import os

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from config.config import config_get
from logging_.logging_ import logger_info, logging_init
from mysql_.migration.mysql_migration_up import mysql_migration_up
from mysql_.mysql_ import mysql_pool_close, mysql_pool_get

from src.auth.auth import AuthUser
from src.auth.auth_seed import auth_seed_first_user, auth_seed_operator
from src.uvicorn.routers import auth as auth_router
from src.uvicorn.routers import backup as backup_router
from src.uvicorn.routers import dashboard
from src.uvicorn.routers import seed as seed_router
from src.uvicorn.routers import site as site_router
from src.uvicorn.routers import users as users_router
from src.uvicorn.routers.dating import dating_export
# Ручки анкеты разложены на три файла по тому, куда они ходят: CRUD своей базы,
# действия наружу, чтение для страниц. Роутер у всех трёх один и тот же
# (`/api/dating/profile`) — снаружи это одна группа ручек, см. шапку `dating_profile.py`.
from src.uvicorn.routers.dating import dating_profile
from src.uvicorn.routers.dating import dating_profile_action
from src.uvicorn.routers.dating import dating_profile_view
from src.uvicorn.uvicorn_base import uvicorn_base_path
from src.uvicorn.uvicorn_validation import uvicorn_validation_handler
from src.worker.dating.chat_worker import chat_worker_recover

# Куда пускаем без входа. Список короткий и явный: всё остальное — под сессией,
# и забыть закрыть новую страницу нельзя, потому что закрыто по умолчанию.
# `/auth/logout` здесь не по недосмотру: выход обязан работать всегда. Без сессии
# он ничего не делает, а 401 в ответ на «выйди» — это шум, из-за которого клиент
# начинает разбирать, был ли он вообще внутри.
MAIN_UVICORN_PUBLIC = ('/login', '/auth/login', '/auth/logout', '/static', '/health',
                       '/docs', '/openapi.json', '/redoc')

# Сколько живёт сессия панели. Час — как у соседа: смена оператора не должна
# оставлять открытую панель на чужом экране.
MAIN_UVICORN_SESSION_MAX_AGE = 3600


async def main_uvicorn_auth_middleware(request: Request, call_next):
    """Неавторизованного человека уводит на вход, скрипту отвечает 401.

    Разница по типу ответа, а не по пути: страницу открывает браузер, и редирект
    для него естественен; клиенту API редирект на HTML-форму сказал бы «всё
    хорошо, вот вам страница входа» — и он бы разбирал её как ответ.
    """
    path = request.url.path

    if request.method == 'OPTIONS' or any(path.startswith(p) for p in MAIN_UVICORN_PUBLIC):
        return await call_next(request)

    if request.session.get('user_id'):
        return await call_next(request)

    # Токен проверяет сама точка (`auth_required`): здесь он лишь повод пропустить
    # запрос дальше, не подменяя ответ редиректом.
    if request.headers.get('authorization') or request.headers.get('x-api-key'):
        return await call_next(request)

    if 'text/html' in str(request.headers.get('accept') or ''):
        # Префикс дописываем сами: прокси его срезал, и `path` пришёл без него.
        # Без этого редирект уводил бы на `/login` соседнего проекта.
        base = uvicorn_base_path()

        return RedirectResponse(url=f'{base}/login?next={base}{path}', status_code=302)

    return JSONResponse(status_code=401, content={'detail': 'нужен вход'})


async def main_uvicorn_forbidden_handler(request: Request, exc: HTTPException):
    """403 браузеру — страницей, скрипту — прежним `{"detail": …}`.

    Различаем по типу ответа, которого ждут, — тем же правилом, что и
    `main_uvicorn_auth_middleware`. Причина та же: голый JSON в окне браузера
    человек читает как поломку панели и идёт выяснять, почему она сломалась,
    вместо того чтобы попросить доступ.

    Остальные коды отдаются как раньше: обработчик подменяет ответ только там,
    где ему есть что улучшить.
    """
    if exc.status_code == status.HTTP_403_FORBIDDEN and 'text/html' in str(request.headers.get('accept') or ''):
        # Пользователя собираем из сессии: страница отказа наследует общий
        # шаблон, а тому нужны имя в шапке и роль, чтобы решить, какие разделы
        # рисовать. Сюда попадают только вошедшие — не вошедших забирает
        # middleware раньше, на подступах.
        user = AuthUser(request.session.get('user_id', 0),
                        request.session.get('username', ''),
                        request.session.get('role', ''))
        return request.app.state.jinja2_template.TemplateResponse(
            'admin/forbidden.html',
            {'request': request, 'user': user, 'base': uvicorn_base_path()},
            status_code=exc.status_code)

    return await http_exception_handler(request, exc)


async def main_uvicorn_error_middleware(request: Request, call_next):
    """Необработанная ошибка — 500 с текстом, а не пустой обрыв соединения."""
    try:
        return await call_next(request)
    except Exception as e:
        logger_info(f'[uvicorn] {request.method} {request.url.path}: {type(e).__name__}: {e}')

        return JSONResponse(status_code=500, content={'detail': f'{type(e).__name__}: {e}'})


app = FastAPI(
    title='DatingAi',
    description='Учётки анкет на сайтах знакомств: страница, живой чат, автоответ.',
    version='1.0.0',
    middleware=[
        Middleware(SessionMiddleware, secret_key=config_get('SESSION_SECRET_KEY', 'dating-dev'),
                   session_cookie='dating_session', max_age=MAIN_UVICORN_SESSION_MAX_AGE,
                   same_site='lax', https_only=False),
        Middleware(BaseHTTPMiddleware, dispatch=main_uvicorn_error_middleware),
        Middleware(BaseHTTPMiddleware, dispatch=main_uvicorn_auth_middleware),
    ],
)


# Неверный ввод объясняем словами, а не списком объектов pydantic: этот текст
# попадает прямо в форму, и «String should have at least 1 character» в ней
# читать некому.
app.add_exception_handler(RequestValidationError, uvicorn_validation_handler)

# Отказ по правам браузеру показываем страницей, а не строкой JSON.
app.add_exception_handler(HTTPException, main_uvicorn_forbidden_handler)


@app.on_event('startup')
async def main_uvicorn_startup():
    """Журнал, схема, пул. Схема накатывается на старте — как у соседа: установка
    из чистого репозитория должна подниматься одной командой."""
    logging_init()
    mysql_migration_up()
    await mysql_pool_get()
    await auth_seed_first_user()
    await auth_seed_operator()

    # Смену держателя сокетов поднимаем сами: воркер стоит с пустой очередью, а
    # ставить задание руками после каждого перезапуска — верный способ забыть.
    await chat_worker_recover()

    logger_info('[uvicorn] DatingAi поднят')


@app.on_event('shutdown')
async def main_uvicorn_shutdown():
    await mysql_pool_close()


@app.get('/health', include_in_schema=False)
async def main_uvicorn_health():
    """Живность сервиса — для compose и внешнего опроса."""
    return {'status': 'ok'}


current_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(current_dir, config_get('DATA_DIR', 'data'), config_get('UVICORN_DIR', 'uvicorn'))

app.state.jinja2_template = Jinja2Templates(directory=os.path.join(data_dir, 'template'))
app.mount('/static', StaticFiles(directory=os.path.join(data_dir, 'public')), name='static')

# Адрес phpMyAdmin в шапке. Читается здесь, а не в шаблоне: шаблон не должен
# знать про переменные окружения, а ссылка нужна на всех страницах сразу.
#
# Ссылка ведёт на `localhost` браузера, а не на имя контейнера: открывает её
# человек со своей машины. До прод-сервисов он ходит SSH-туннелем, до локальных
# — напрямую, и в обоих случаях адрес свой — отсюда переменная, а не константа.
app.state.jinja2_template.env.globals['PHPMYADMIN_URL'] = config_get('PHPMYADMIN_URL', '')

app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(site_router.router)
app.include_router(dating_profile.router)
app.include_router(dating_profile_action.router)
app.include_router(dating_profile_view.router)
app.include_router(dating_export.router)
app.include_router(seed_router.router)
app.include_router(backup_router.router)
app.include_router(users_router.router)
