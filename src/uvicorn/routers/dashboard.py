"""Страницы панели: вход, анкеты, seed и бэкап.

Отдаются сервером (Jinja2), а не собираются на клиенте: страниц несколько,
состояние живёт в базе, и городить сборку фронтенда ради четырёх шаблонов нечем
оправдать.

Все страницы под входом наследуют `base.html` — шапку с разделами. Исключение
одно, `login.html`: на форме входа навигация по закрытой панели бессмысленна.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.auth.auth import AuthUser, auth_admin_required, auth_required
from src.uvicorn.routers.setting import setting_timezone_hours
from src.uvicorn.uvicorn_base import uvicorn_base_path

router = APIRouter(tags=['pages'])


@router.get('/', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_index(request: Request, user: AuthUser = Depends(auth_required)):
    """Корень — это и есть панель анкет: главной её в проекте нет."""
    return await _dashboard_page(request, user, 'admin/dating.html')


@router.get('/admin/dating', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating(request: Request, user: AuthUser = Depends(auth_required)):
    """Тот же адрес, что был в RhapsodyAi. Оставлен ради закладок и ссылок в
    переписке: переезд не должен ломать то, что люди уже сохранили."""
    return await _dashboard_page(request, user, 'admin/dating.html')


@router.get('/admin/dating/profile', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating_profile(request: Request, user: AuthUser = Depends(auth_required)):
    """Страница «Профили»: учётки сайта таблицей — состояние, работа, вход.

    Открыта всем вошедшим, а не только админу: это работа с анкетами, ради которой
    панель и открывают, а не операция над устройством установки.
    """
    return await _dashboard_page(request, user, 'admin/dating_profile.html')


@router.get('/admin/dating/mail', include_in_schema=False)
async def dashboard_dating_mail(user: AuthUser = Depends(auth_required)):
    """Страницы «Письма» больше нет — уводим на её содержимое.

    Шаблоны и переменные переехали подвкладками в «Профили» → «Письма»: текст
    письма правят там же, где смотрят на пул и на входящие, и уходить со страницы
    ради правки шаблона больше не надо.

    ⚠ **Отвечаем переадресацией, а не 404.** Адрес прожил месяцы: он в закладках,
    в переписке и в старых снимках экрана. Отказ на нём читался бы как поломка
    панели, а не как переезд.
    """
    return RedirectResponse(url=f'{uvicorn_base_path()}/admin/dating/profile',
                            status_code=302)


@router.get('/admin/dating/log', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating_log(request: Request, user: AuthUser = Depends(auth_required)):
    """Страница «Журналы»: общая лента работы по учёткам сайта.

    Открыта всем вошедшим, как и остальные страницы раздела: журнал — это ответ
    на вопрос «почему анкета не отвечает», и держать его за админской дверью
    значит звать админа на каждый разбор.
    """
    return await _dashboard_page(request, user, 'admin/dating_log.html')


@router.get('/admin/dating/error', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating_error(request: Request, user: AuthUser = Depends(auth_required)):
    """Страница «Ошибки»: отказы по учёткам сайта одним списком.

    Отдельно от «Журналов», а не фильтром внутри них: за отказами приходят
    прицельно, и путь до них не должен идти через переключатель.
    """
    return await _dashboard_page(request, user, 'admin/dating_error.html')


@router.get('/admin/dating/site', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating_site(request: Request, user: AuthUser = Depends(auth_required)):
    """Страница «Настройки сайтов»: наши настройки к сайту соседа.

    ⚠ Называлась «Сайты» — и обещала больше, чем даёт: заводить и удалять сайты
    здесь нельзя, их ведёт RhapsodyAi. На странице только настройки, и имя теперь
    об этом говорит.

    Открыта всем вошедшим, как остальные страницы раздела: оператору полезно
    видеть, с чем он работает. Правка при этом закрыта админом — на уровне ручки
    (`api.md`, §3.5), а страница прячет форму от того, кому её не сохранить.
    """
    return await _dashboard_page(request, user, 'admin/dating_site.html')


@router.get('/admin/dating/socket', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_dating_socket(request: Request, user: AuthUser = Depends(auth_required)):
    """Страница «Онлайн»: чем анкета держится на сайте прямо сейчас.

    Два разных состояния, и на странице они рядом, потому что путают их постоянно:

      **присутствие** — HTTP-удар по таймеру, «видно в списке онлайн». Нужно
      всем сайтам (`heartbeat.md`);
      **сокеты** — вебсокет чата, «сообщения приходят живыми». Есть не у всех
      сайтов: у Ladadate вебсокета нет вовсе.

    Отвечает на вопрос «почему анкета молчит», который по базе не разобрать:
    присутствия нет, адреса сокета нет, адрес есть но соединение рвётся,
    соединение живо но сообщений нет — состояния разные, а в анкете всё выглядит
    одинаково.

    ⚠ Адрес страницы прежний (`/admin/dating/socket`): переименование заголовка
    не повод ломать закладки и ссылки в переписке.
    """
    return await _dashboard_page(request, user, 'admin/dating_socket.html')


@router.get('/admin/setting', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_setting(request: Request, user: AuthUser = Depends(auth_admin_required)):
    """Значения настроек: ключ и значение, правятся на месте. Только админу —
    настройка меняет поведение панели для всех, а не для того, кто её открыл."""
    return await _dashboard_page(request, user, 'admin/setting.html')


@router.get('/admin/seed', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_seed(request: Request, user: AuthUser = Depends(auth_admin_required)):
    """Seed SQL: выгрузка выбранных таблиц и заливка обратно. Только админу."""
    return await _dashboard_page(request, user, 'admin/seed.html')


@router.get('/admin/users', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_users(request: Request, user: AuthUser = Depends(auth_admin_required)):
    """Пользователи: учётки, роли и токены доступа. Только админу — отсюда
    раздают права на саму панель."""
    return await _dashboard_page(request, user, 'admin/users.html')


@router.get('/admin/backup', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_backup(request: Request, user: AuthUser = Depends(auth_admin_required)):
    """Бэкапы: полный дамп базы и восстановление. Только админу — восстановление
    перезаписывает базу целиком."""
    return await _dashboard_page(request, user, 'admin/backup.html')


@router.get('/login', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_login(request: Request):
    """Форма входа. Открыта всем — иначе войти было бы некуда."""
    return request.app.state.jinja2_template.TemplateResponse(
        'login.html', {'request': request, 'base': uvicorn_base_path(),
                       'next': request.query_params.get('next', '/')})


# ── Приватное ────────────────────────────────────────────────────────────────

async def _dashboard_page(request: Request, user: AuthUser, template: str):
    """Страница под входом: шаблон плюс общий контекст.

    Контекст один на все страницы, и собирается он здесь, а не в каждой точке:
    забытый `base` в одной из них ломает на проде все её ссылки разом, а
    заметно это только под общим доменом.

    ⚠ **Часовой пояс уезжает в разметку, а не спрашивается запросом.** Времена
    приходят с сервера в UTC, и сдвигает их страница; спроси она смещение
    отдельным запросом — первые кадры успели бы нарисоваться со старым, и человек
    видел бы, как время прыгает на три часа у него на глазах. Второй довод: ручка
    настроек закрыта админом, а правильное время нужно и оператору.
    """
    return request.app.state.jinja2_template.TemplateResponse(
        template, {'request': request, 'user': user, 'base': uvicorn_base_path(),
                   'tz_offset': await setting_timezone_hours()})
