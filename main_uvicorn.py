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
from logging_.logging_ import logger_exception, logger_info, logging_init
from mysql_.migration.mysql_migration_up import mysql_migration_up
from mysql_.mysql_ import mysql_pool_close, mysql_pool_get

from src.auth.auth import AuthUser
from src.auth.auth_seed import auth_seed_first_user, auth_seed_operator
from src.uvicorn.routers import auth as auth_router
from src.uvicorn.routers import backup as backup_router
from src.uvicorn.routers import dashboard
from src.uvicorn.routers import seed as seed_router
from src.uvicorn.routers import setting as setting_router
from src.uvicorn.routers import site as site_router
from src.uvicorn.routers import users as users_router
from src.uvicorn.routers.dating import export
from src.uvicorn.routers.dating import import_
from src.uvicorn.routers.dating import chat as dating_chat_router
from src.uvicorn.routers.dating import chat_initiative as dating_chat_initiative_router
from src.uvicorn.routers.dating import companion as dating_companion_router
from src.uvicorn.routers.dating import error as dating_error_router
from src.uvicorn.routers.dating import feed as dating_feed_router
from src.uvicorn.routers.dating import gallery as dating_gallery_router
from src.uvicorn.routers.dating import log as dating_log_router
from src.uvicorn.routers.dating import request as dating_request_router
from src.uvicorn.routers.dating import mail as dating_mail_router
from src.uvicorn.routers.dating import mail_var as dating_mail_var_router
from src.uvicorn.routers.dating import mail_in as dating_mail_in_router
from src.uvicorn.routers.dating import heartbeat as dating_heartbeat_router
from src.uvicorn.routers.dating import favorite as dating_favorite_router
from src.uvicorn.routers.dating import favorite_mail as dating_favorite_mail_router
from src.uvicorn.routers.dating import block as dating_block_router
from src.uvicorn.routers.dating import invite as dating_invite_router
from src.uvicorn.routers.dating import stats as dating_stats_router
from src.uvicorn.routers.dating import mailing as dating_mailing_router
from src.uvicorn.routers.dating import people
from src.uvicorn.routers.dating import site_ as dating_site_router
from src.uvicorn.routers.dating import site_switch as dating_site_switch_router
from src.uvicorn.routers.dating import socket_ as dating_socket_router
from src.uvicorn.routers.dating import work as dating_work_router
# Ручки анкеты разложены на три файла по тому, куда они ходят: CRUD своей базы,
# действия наружу, чтение для страниц. Роутер у всех трёх один и тот же
# (`/api/dating/profile`) — снаружи это одна группа ручек, см. шапку `profile_.py`.
from src.uvicorn.routers.dating import profile_
from src.uvicorn.routers.dating import profile_action
from src.uvicorn.routers.dating import profile_view
from src.uvicorn.routers.dating import proxy as dating_proxy_router
from src.uvicorn.uvicorn_base import uvicorn_base_path
from src.uvicorn.uvicorn_validation import uvicorn_validation_handler
from src.worker.dating.chat_worker import chat_worker_keep_stop, chat_worker_recover

# Куда пускаем без входа. Список короткий и явный: всё остальное — под сессией,
# и забыть закрыть новую страницу нельзя, потому что закрыто по умолчанию.
# `/auth/logout` здесь не по недосмотру: выход обязан работать всегда. Без сессии
# он ничего не делает, а 401 в ответ на «выйди» — это шум, из-за которого клиент
# начинает разбирать, был ли он вообще внутри.
MAIN_UVICORN_PUBLIC = ('/login', '/auth/login', '/auth/logout', '/static', '/health',
                       '/docs', '/openapi.json', '/redoc')

# Сколько живёт сессия панели. Четыре часа: смена оператора не должна оставлять
# открытую панель на чужом экране, но час выгонял человека посреди работы —
# переписку ведут дольше, и перелогин приходился на разговор.
MAIN_UVICORN_SESSION_MAX_AGE = 4 * 3600


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
    """Необработанная ошибка — 500, но **без внутренностей наружу**.

    Текст исключения остаётся **только в журнале**, наружу уходит общая фраза:
    в строке исключения живут адреса, пароли и фрагменты чужих данных (у
    `aiomysql` в текст попадает весь запрос с аргументами), а сам тип и текст —
    карта внутренностей для того, кто постучится не туда.

    В журнал пишем с трейсбеком: без кадров строчка «'NoneType' object has no
    attribute 'get'» не показывает, где именно, и искать её приходится по всему
    обработчику.
    """
    try:
        return await call_next(request)
    except Exception as e:
        logger_exception(f'[uvicorn] {request.method} {request.url.path}: {type(e).__name__}',
                         exc=e)

        return JSONResponse(status_code=500, content={
            'detail': 'внутренняя ошибка панели; подробности — в журнале'})


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
    #
    # ⚠ Вызов не разовый: он оставляет за собой сторожа, который возвращает смену,
    # если та оборвалась. Смена ставит себе следующую сама, но лишь дожив до конца
    # работы, — перезапуск контейнера воркера её обрывает раньше, и без сторожа
    # установка молча остаётся без держателя (`chat_worker.py`).
    await chat_worker_recover()

    logger_info('[uvicorn] DatingAi поднят')


@app.on_event('shutdown')
async def main_uvicorn_shutdown():
    # Сторож смены бесконечен: снимаем его сами, иначе остановка сервера ждала бы
    # его очередного круга либо ругалась незавершённой задачей.
    await chat_worker_keep_stop()
    await mysql_pool_close()


@app.get('/health', include_in_schema=False)
async def main_uvicorn_health():
    """Живность сервиса — для compose и внешнего опроса."""
    return {'status': 'ok'}


current_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(current_dir, config_get('DATA_DIR', 'data'), config_get('UVICORN_DIR', 'uvicorn'))

app.state.jinja2_template = Jinja2Templates(directory=os.path.join(data_dir, 'template'))
class StaticNoCache(StaticFiles):
    """Статика с обязательной перепроверкой у сервера.

    ⚠ **Без этого браузер держит старый файл неопределённо долго.** Starlette
    отдаёт `ETag` и `Last-Modified`, но не `Cache-Control` — а без него браузер
    решает сам и обычно кэширует эвристически, по возрасту файла. Для панели это
    ломается на первой же правке: страница грузит новый HTML со старым модулем и
    падает с «не экспортирует timeFull», хотя на сервере экспорт есть.

    Больнее всего с ES-модулями: их браузер держит и в памяти вкладки, так что
    обычное обновление страницы не помогает — нужно очищать кэш вручную.

    `no-cache` — не «не кэшировать», а «перепроверить перед использованием». Файл
    остаётся в кэше, но браузер спрашивает `ETag` и почти всегда получает 304 без
    тела: расход — один короткий запрос, зато панель никогда не показывает
    вчерашний код.

    Цена приемлема, потому что панель внутренняя: десятки файлов, единицы
    пользователей. Публичному сайту так делать не следует — там статику версионируют
    в имени файла, и тогда её можно кэшировать навсегда.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers['Cache-Control'] = 'no-cache'

        return response


app.mount('/static', StaticNoCache(directory=os.path.join(data_dir, 'public')), name='static')

# Адрес phpMyAdmin в шапке. Читается здесь, а не в шаблоне: шаблон не должен
# знать про переменные окружения, а ссылка нужна на всех страницах сразу.
#
# Ссылка ведёт на `localhost` браузера, а не на имя контейнера: открывает её
# человек со своей машины. До прод-сервисов он ходит SSH-туннелем, до локальных
# — напрямую, и в обоих случаях адрес свой — отсюда переменная, а не константа.
app.state.jinja2_template.env.globals['PHPMYADMIN_URL'] = config_get('PHPMYADMIN_URL', '')


def static_url(path: str) -> str:
    """Адрес файла статики с меткой его правки: `/static/js/x.js?v=1756800000`.

    ⚠ **Без метки браузер показывает вчерашний код.** `Cache-Control: no-cache`
    (см. `StaticNoCache`) заставляет перепроверять файл, но **только при новом
    запросе**. ES-модуль, уже загруженный во вкладку, не перезапрашивается вовсе:
    страница берёт его из памяти, и обычное обновление не помогает — нужно
    очищать кэш вручную.

    Так и было: страница грузила новый HTML со старым модулем и падала с «не
    экспортирует timeFull», хотя экспорт на сервере был.

    Метка — время правки файла. Правка меняет адрес, а другой адрес для браузера
    другой модуль: старый он не подставит при всём желании. Гадать при этом не
    приходится — метка меняется ровно тогда, когда меняется файл.

    Читается на каждый показ, а не запоминается: правят статику руками, и
    перезапуск ради подхвата новой версии — лишний повод. Файл на диске, чтение
    дешевле объяснения, почему правка не видна.
    """
    full = os.path.join(data_dir, 'public', str(path).lstrip('/'))
    try:
        mark = int(os.path.getmtime(full))
    except OSError:
        # Файла нет — отдаём адрес как есть: пусть браузер получит честный 404, а
        # не молчаливую подмену. Ошибка в имени должна быть видна.
        return f'/static/{str(path).lstrip("/")}'

    return f'/static/{str(path).lstrip("/")}?v={mark}'


app.state.jinja2_template.env.globals['static_url'] = static_url

app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(site_router.router)
app.include_router(setting_router.router)
app.include_router(profile_.router)
app.include_router(profile_action.router)
app.include_router(profile_view.router)
app.include_router(dating_proxy_router.router)
app.include_router(export.router)
app.include_router(import_.router)
app.include_router(dating_log_router.router)
app.include_router(dating_request_router.router)
app.include_router(dating_feed_router.router)
app.include_router(dating_gallery_router.router)
# ⚠ Реестр переменных — **до** шаблонов: его пути начинаются с
# `/api/dating/mail/var`, и запиши мы их после `/api/dating/mail/{mail_id}`,
# слово «var» однажды прочиталось бы как номер шаблона.
app.include_router(dating_companion_router.router)
app.include_router(dating_mail_var_router.router)
app.include_router(dating_mail_router.router)
app.include_router(dating_mail_in_router.router)
app.include_router(dating_mailing_router.router)
app.include_router(dating_invite_router.router)
app.include_router(dating_block_router.router)
app.include_router(dating_favorite_router.router)
app.include_router(dating_favorite_mail_router.router)
app.include_router(dating_stats_router.router)
app.include_router(dating_heartbeat_router.router)
app.include_router(dating_site_router.router)
# Тот же префикс, что у «Сайтов»: переключатели и расписания — продолжение той же
# страницы, разложенное по файлам (`site_switch.py`), а не новый раздел.
app.include_router(dating_site_switch_router.router)
app.include_router(dating_socket_router.router)
app.include_router(dating_error_router.router)
app.include_router(dating_chat_router.router)
app.include_router(dating_chat_initiative_router.router)
app.include_router(dating_work_router.router)
app.include_router(people.router)
app.include_router(seed_router.router)
app.include_router(backup_router.router)
app.include_router(users_router.router)
