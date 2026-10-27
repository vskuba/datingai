"""Шаблон работы с анкетой: разметка `#work` под конкретный сайт.

Страница «Профили» состоит из двух частей. **Каркас** — список анкет, полоса
вкладок и журнал под ними — один на все сайты и лежит в `admin/profile_.html`.
**Работа с анкетой** (`#work`) у сайтов может расходиться: где-то есть лента «кто в
сети», где-то её нет, где-то понадобится свой блок. Она отдаётся отсюда.

## Почему фрагментом, а не в самой странице

Сайт переключают селектом, **без перезагрузки страницы**. Собери мы разметку при
отдаче страницы — переключение сайта требовало бы перезагрузки, то есть потери
выбранной анкеты, вкладки и прокрутки. Поэтому страница спрашивает фрагмент, когда
сайт сменился.

## ⚠ Имя шаблона приходит из данных, и это опасно

`site_.settings['template']` правит человек на странице «Сайты». Возьми мы
это значение как путь — получили бы чтение любого файла, до которого дотянется
Jinja (`../../../etc/passwd` и тому подобное). Поэтому имя **сверяется со списком
файлов в каталоге**, а не подставляется в путь: не нашлось — берём умолчание.

## Как выбирается шаблон

По порядку, до первого попадания:

1. `site_.settings['template']` — если сайту назначили шаблон руками;
2. имя сайта у соседа, приведённое к нижнему регистру (`Ladadate` → `ladadate`);
3. `default` — общий вид.

Второй пункт делает заведение сайта бесплатным: положил `newsite.html` — он и
подхватился. Первый нужен там, где имя не годится в имя файла или два сайта делят
один шаблон.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from logging_.logging_ import logger_info

from src.auth.auth import AuthUser, auth_required
from src.dating.site_setting import site_boxes, site_setting
from src.dating.site_snapshot import site_rows
from uvicorn_.uvicorn_base import uvicorn_base_path

router = APIRouter(prefix='/admin/dating/profile', tags=['pages'])

# Где лежат шаблоны сайтов. Путь от корня шаблонов — таким его ждёт Jinja.
WORK_DIR = 'admin/dating/profile/site'

# Шаблон для сайта, у которого своего нет. Не пустой на всякий случай: новый сайт
# должен открыться и работать до того, как под него напишут свой.
WORK_DEFAULT = 'default'

# Ключ настройки сайта, которым шаблон назначают руками (страница «Сайты»).
WORK_SETTING = 'template'

# Ключ настройки-галочек «Активные ленты» — какие ленты показывать на «Письмах
# рассылка». Шаблон не выбирает их сам: `{% if %}` по готовому списку (реестр и
# разбор — в `site_setting.py`, там же и значение по умолчанию).
FEEDS_SETTING = 'mailing_feeds'

# Что считаем именем шаблона. Строгий набор, потому что значение приходит из базы:
# буквы, цифры, дефис и подчёркивание. Точки нет намеренно — с ней в имя пролезает
# `..`, то есть выход из каталога.
WORK_NAME_RE = re.compile(r'^[a-z0-9_-]{1,64}$')

# Файлы, начинающиеся с подчёркивания, — не шаблоны сайтов, а основа для них
# (`_base.html`). Отдать такой напрямую значит показать разметку с
# неопределёнными блоками.
WORK_HIDDEN = '_'


@router.get('/work', response_class=HTMLResponse, include_in_schema=False)
async def work_template(request: Request, site_id: int = 0,
                               user: AuthUser = Depends(auth_required)):
    """Разметка `#work` для сайта.

    Args:
        site_id: сайт-закладка у соседа.

    Returns:
        HTMLResponse: фрагмент разметки — вкладки работы с анкетой без журнала.

    Raises:
        HTTPException: 422 — сайт не выбран.

    Отдаётся **всем вошедшим**: это разметка страницы, которая и так открыта
    оператору. Данных в ней нет — их наполняет страница по своим ручкам.
    """
    if not site_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='не выбран сайт')

    name = await _template_name(site_id, request)

    return request.app.state.jinja2_template.TemplateResponse(
        f'{WORK_DIR}/{name}.html',
        {'request': request, 'user': user, 'base': uvicorn_base_path(),
         'site_id': site_id,
         # Настройки читаются здесь, а не в шаблоне: шаблон сайта — данные,
         # ему показывают готовый список, а не дают доступ к базе.
         'mailing_feeds': await site_boxes(site_id, FEEDS_SETTING)})


# ── Приватное ────────────────────────────────────────────────────────────────

async def _template_name(site_id: int, request: Request) -> str:
    """Имя шаблона сайта — сверенное с тем, что есть на диске.

    ⚠ Возвращает только то, что **существует**: назначенный руками шаблон могли
    удалить, а имя сайта у соседа — переименовать. Молча отдать умолчание лучше,
    чем 500 из Jinja: страница откроется и будет работать, а расхождение видно в
    логе.
    """
    known = _known(request)

    chosen = str(await site_setting(site_id, WORK_SETTING)).strip().lower()
    if chosen:
        if chosen in known:
            return chosen

        logger_info(f'[dating] шаблон «{chosen}» сайту #{site_id} назначен, но файла нет')

    by_name = _slug(await _site_name(site_id))
    if by_name and by_name in known:
        return by_name

    return WORK_DEFAULT


def _known(request: Request) -> set[str]:
    """Какие шаблоны сайтов есть.

    Спрашиваем **у самой Jinja** (`loader.list_templates`), а не читаем каталог
    своим путём: путь к шаблонам задан один раз, при сборке приложения, и второе
    его написание разошлось бы с первым при переносе каталога.

    Читается каждый раз, а не запоминается: шаблоны кладут руками, и перезапуск
    ради нового файла — лишний повод. Список маленький, чтение дешевле, чем
    объяснение, почему новый файл не подхватился.
    """
    prefix = WORK_DIR + '/'
    try:
        names = request.app.state.jinja2_template.env.loader.list_templates()
    except Exception as e:
        logger_info(f'[dating] список шаблонов сайтов не прочитался: {e}')

        return {WORK_DEFAULT}

    found = set()
    for name in names:
        if not name.startswith(prefix) or not name.endswith('.html'):
            continue
        short = name[len(prefix):-5]
        if short and not short.startswith(WORK_HIDDEN):
            found.add(short)

    return found


def _slug(name: str) -> str:
    """Имя сайта в имя файла: `Ladadate` → `ladadate`.

    Не годится в имя — пустая строка, а не искажённое: подобрать шаблон по
    искажённому имени всё равно не выйдет, а назначить его руками можно настройкой.
    """
    slug = str(name or '').strip().lower().replace(' ', '-')

    return slug if WORK_NAME_RE.match(slug) else ''


async def _site_name(site_id: int) -> str:
    """Имя сайта у соседа. Не ответил — пусто: шаблон подберётся умолчанием.

    Отказ соседа не должен закрывать страницу: разметка `#work` от него не
    зависит, а без имени мы теряем только автоподбор файла.
    """
    # ⚠ Из нашего снимка списка сайтов, а не карточкой у соседа: страница
    # открывается человеком, и лежащий сосед не должен ронять выбор разметки.
    row = next((one for one in await site_rows()
                if int(one['id']) == int(site_id)), {})

    return str(row.get('name') or '')
