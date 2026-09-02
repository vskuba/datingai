"""Сайты: снимок списка соседа и наши настройки к нему.

Сам сайт живёт в RhapsodyAi — там его адрес, метки, `presence` и сценарии. Здесь
две вещи: **снимок его списка**, чтобы панель работала, когда сосед недоступен, и
настройки, которые про **нашу** работу с сайтом и у соседа хранить негде.

## ⚠ Снимок — не свой список сайтов

Правило проекта («своей копии чужих данных здесь не заводится») ослаблено ровно на
одну вещь, и вот почему. Селект сайта стоит в шапке **каждой** страницы раздела, и
список для него брали у соседа при каждом открытии. Лежит сосед — селект пуст, и
панель целиком выглядит сломанной: без выбранного сайта ни анкет, ни профилей, ни
журналов. Причём наши-то данные на месте.

Размен принят осознанно:

* было: копии нет, разойтись не с чем, но лежащий сосед гасит всю панель;
* стало: копия есть и может разойтись с оригиналом — ценой пустого селекта.

Расхождение лечится само: каждый показ списка заводит фоновую сверку, и
переименованный сайт получает новое имя в ту же секунду, когда сосед отвечает.
Живёт оно ровно столько, сколько сосед недоступен.

⚠ **Список по-прежнему ведёт сосед.** Строки здесь — снимок его ответа: сайт,
которого у него нет, помечается `is_gone` при первой удачной сверке и из селекта
исчезает. Ручки «завести сайт» здесь нет и не будет — заводят их у него.

Отсюда форма ответа: ручка складывает **снимок списка** и **наши настройки**. Сайт
без сохранённых настроек в списке всё равно есть — с пустым набором.

## Почему JSON, а не колонка на настройку

Настройки сайта только заводятся, и какие понадобятся, сейчас не знает никто.
Колонка под каждую означала бы миграцию на каждую. Когда список устоится, частые
переедут в свои колонки — тогда будет что переносить.

## Права

Правка — только администратору: настройка сайта меняет работу всех анкет на нём, а
не того, кто её открыл. Чтение открыто всем вошедшим: оператору полезно видеть, с
чем он работает, и прятать это значило бы звать админа на каждый вопрос.
"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from datetime_.datetime_ import datetime_utc_now
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_admin_required, auth_required
from src.dating.dating_error import (DATING_ERROR_SITES_SYNC, dating_error_clear,
                                     dating_error_write)
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_list

router = APIRouter(prefix='/api/dating/site', tags=['dating'])

# Длина ключа настройки. Та же, что у настроек панели (`setting.key`): человек
# читает их одними глазами, и разные пределы в двух местах пришлось бы помнить.
DATING_SITE_KEY_MAX = 50

# Известные настройки: те, у которых есть смысл и заданный набор значений.
#
# ⚠ **Реестр живёт здесь, а не на странице.** Значение читает код, и набор
# допустимых значений — его условие, а не украшение формы. Знай его только
# страница, вписанное мимо набора (руками, скриптом, чужой правкой) прошло бы молча
# и обнаружилось поведением, которого никто не просил.
#
# Незнакомые ключи при этом не запрещены: сайт настраивают по ходу дела, и
# требовать записи в реестре на каждый новый — значит останавливать работу ради
# правки кода. Такие страница показывает полем ввода.
#
# `seconds` в значениях, а не «7д»: их читает код, и таблица разбора «7д → 604800»
# была бы вторым местом, где это знание живёт. Смысл несут подписи в наборе.
#
# `options` может не быть — тогда значение свободное (адрес, пароль), и страница
# рисует поле ввода. `secret: True` — в списке приходит маской, а настоящее значение
# только по нажатию «показать» и только админу (`DATING_SITE_SECRET_MASK`).
DATING_SITE_KNOWN = {
    'favorites_keep': {
        'title': 'Кеш My Favorites',
        'note': 'сколько держим снятый список избранных у каждой анкеты',
        'default': '604800',
        'options': [('604800', '7 дней'), ('259200', '3 дня'), ('86400', '24 часа')],
    },
    'chat_check_every': {
        'title': 'Частота проверки входящих сообщений',
        'note': 'как часто забирать новые сообщения чата из панели агентства',
        'default': '300',
        'options': [('300', '5 минут'), ('1800', '30 минут'), ('3600', '1 час')],
    },
    'people_source': {
        'title': 'Откуда берём списки собеседников',
        'note': 'API сайта отвечает за десятые доли секунды; сценарий водит браузер '
                'соседа и стоит 35–40 секунд',
        'default': 'scenario',
        'options': [('scenario', 'Сценарий у соседа'), ('api', 'API сайта')],
    },
    'api_base': {
        'title': 'Адрес API сайта',
        'note': 'корень API этого сайта, без косой черты в конце',
        'default': '',
    },
    'api_password': {
        'title': 'Пароль анкет для API',
        'note': 'общий пароль агентства; свой пароль анкеты, если он задан, сильнее',
        'default': '',
        'secret': True,
    },
}

# Как часто сверяем снимок с соседом. Полминуты: список сайтов меняют руками и
# редко, а страниц в разделе пять, и открывают их подряд — сверка на каждое
# открытие была бы запросом к соседу на каждый щелчок по меню.
DATING_SITE_SYNC_EVERY_SEC = 30

# Идущая сверка и её память. Списком из одного значения, а не переменной: так их
# видно из вложенных функций без `global` в каждой.
_sync_task = None
_sync_at = [0.0]
_sync_done = [0.0]
_sync_error = ['']

# Чем секрет выглядит наружу. ⚠ **В списке и в чтении одного сайта — только маска,
# никогда настоящее значение.** Эти ответы уходят на каждую смену сайта и оседают в
# журналах запросов; пароль в них попадать не должен.
#
# Посмотреть пароль всё же можно — отдельной ручкой `/{site_id}/reveal/{key}` по
# нажатию «показать», и только администратору. Так обращение к паролю видно как
# отдельное событие с именем ключа, а не растворяется в обычном показе страницы.
# Тот же приём и то же разделение, что у переменных сайта у соседа.
#
# Точки, а не пустая строка: пустое значение на странице читалось бы как «пароль не
# задан», и человек вписал бы его второй раз, гадая, почему не работало.
DATING_SITE_SECRET_MASK = '••••••••'


class DatingSiteSave(BaseModel):
    """Настройки сайта целиком: страница шлёт весь набор, а не по одной."""

    settings: dict[str, str] = Field(default_factory=dict)


@router.get('', include_in_schema=False)
async def dating_site_list(user: AuthUser = Depends(auth_required)):
    """Сайты вместе с нашими настройками — из снимка, а не от соседа.

    Returns:
        dict: `sites` — записи `id`, `name`, `url`, `settings`; `known` — реестр
        известных настроек, по которому страница рисует их набором значений, а не
        полем ввода; `sync` — когда снимок сверяли и идёт ли сверка сейчас.

    ⚠ **502 отсюда не приходит.** Отвечаем снимком и заводим сверку в фоне: лежащий
    сосед не повод показывать пустую страницу настроек. Отказ сверки виден на
    «Ошибках» (`dating_error.py`), а не вместо списка.
    """
    saved = await _settings_all()
    rows = await dating_site_rows()

    return {'result': {'sites': [{'id': row['id'],
                                  'name': row['name'],
                                  'url': row['url'],
                                  'settings': _masked(saved.get(row['id'], {}))}
                                 for row in rows],
                       'known': _known(),
                       'sync': _sync_answer(dating_site_sync_start())}}


@router.get('/{site_id}', include_in_schema=False)
async def dating_site_get(site_id: int, user: AuthUser = Depends(auth_required)):
    """Настройки одного сайта. Нет строки — пустой набор, а не 404.

    Сайт, который ещё не настраивали, — законное состояние, а не ошибка: строка
    появляется при первом сохранении.
    """
    return {'result': {'site_id': site_id,
                       'settings': _masked((await _settings_all()).get(site_id, {}))}}


@router.get('/{site_id}/reveal/{key}', include_in_schema=False)
async def dating_site_reveal(site_id: int, key: str,
                             user: AuthUser = Depends(auth_admin_required)):
    """Настоящее значение секретной настройки — по нажатию «показать».

    Returns:
        dict: `key` и `value`. Не секретный ключ отдаётся как есть: прятать то, что
        и так видно в списке, значило бы делать вид, что оно секрет.

    Raises:
        HTTPException: 404 — у сайта нет такой настройки.

    ⚠ **Отдельной ручкой, а не полем списка, и это не формальность.** Список
    перерисовывается на каждой смене сайта, и отдавай он пароли — они оседали бы в
    журналах запросов десятками раз в день. Здесь же обращение к паролю видно как
    отдельное событие с именем ключа: кто-то нажал «показать».

    ⚠ **Только администратору.** Правка настроек и так его право (настройка меняет
    работу всех анкет на сайте), а пароль тем более. У соседа под это заведено своё
    право (`BROWSER_SECRET_VIEW`), отличное от «смотреть»; здесь ролей две, и
    третьей ради одной ручки не заводим — расхождение записано в `auth.md`, §7.
    """
    value = (await _settings_all()).get(int(site_id), {}).get(str(key))
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'у сайта нет настройки «{key}»')

    return {'result': {'key': str(key), 'value': str(value)}}


@router.put('/{site_id}', include_in_schema=False)
async def dating_site_save(site_id: int, payload: DatingSiteSave,
                           user: AuthUser = Depends(auth_admin_required)):
    """Переписывает настройки сайта целиком.

    ⚠ **Целиком, а не по ключу.** Страница держит весь набор перед глазами и шлёт
    то, что на ней; правка по одному ключу означала бы, что удаление — отдельная
    ручка, а порядок двух правок подряд начинает значить.

    Raises:
        HTTPException: 422 — пустой или слишком длинный ключ.
    """
    before = (await _settings_all()).get(int(site_id), {})

    clean = {}
    for key, value in (payload.settings or {}).items():
        key = str(key).strip()
        if not key:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail='ключ не может быть пустым')
        if len(key) > DATING_SITE_KEY_MAX:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f'ключ длиннее {DATING_SITE_KEY_MAX} знаков: «{key[:60]}…»')

        # ⚠ **Маска вместо секрета означает «не трогать».** Страница настоящего
        # значения не знает и прислать его не может; прими мы точки как значение —
        # первое же сохранение любой соседней настройки затёрло бы пароль строкой
        # «••••••••», и вход перестал бы работать без единого сообщения.
        if str(value) == DATING_SITE_SECRET_MASK:
            was = before.get(key, '')
            if was:
                clean[key] = was
            continue

        # Пустое значение выбрасывается, а не хранится: «настройки нет» и
        # «настройка пустая» — разные вещи, и путать их значит получать
        # необъяснимое поведение там, где умолчание кода было осмысленным. То же
        # правило, что у настроек панели (`api.md`, §6.2).
        if str(value) != '':
            clean[key] = str(value)

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_site (site_id, settings) VALUES (%s, %s)'
            ' ON DUPLICATE KEY UPDATE settings = VALUES(settings)',
            (int(site_id), json.dumps(clean, ensure_ascii=False)))
        await db.connection.commit()

    return {'result': {'site_id': site_id, 'settings': _masked(clean)}}


async def dating_site_setting(site_id: int, key: str, default: str = '') -> str:
    """Одна настройка сайта — для кода, которому она понадобится.

    Отдельной функцией, а не запросом на месте: настройки читают из разных мест, и
    разбор JSON, размазанный по коду, разойдётся в мелочах — например, в том, что
    делать с отсутствующей строкой.

    Умолчание берётся из реестра, если своё не передали: код, который спрашивает
    известную настройку, не должен повторять её умолчание — два места разошлись бы.
    """
    key = str(key)
    if not default:
        default = str(DATING_SITE_KNOWN.get(key, {}).get('default') or '')

    return (await _settings_all()).get(int(site_id), {}).get(key, default)


async def dating_site_seconds(site_id: int, key: str) -> int:
    """Настройка-срок в секундах. Мусор и пустота — умолчание из реестра.

    ⚠ **Значение вне набора не принимается.** Набор — условие кода, а не украшение
    формы: впиши кто-нибудь в базу «месяц», и снимок жил бы месяц, хотя ни одна
    страница такого не предлагала. Берём умолчание и говорим об этом в лог.
    """
    known = DATING_SITE_KNOWN.get(str(key), {})
    fallback = int(known.get('default') or 0)
    allowed = {value for value, _ in known.get('options') or ()}

    raw = str(await dating_site_setting(site_id, key)).strip()
    if allowed and raw not in allowed:
        if raw:
            logger_info(f'[dating] «{key}» у сайта #{site_id} вне набора: {raw!r}')

        return fallback

    return int(raw) if raw.isdigit() else fallback


async def dating_site_rows() -> list[dict]:
    """Снимок списка сайтов: то, что показываем, пока сверка не принесла нового.

    Порядок — по имени: селект читают глазами, а порядок вставки в базу для
    человека случаен. Ушедшие у соседа (`is_gone`) не показываем: закладку убрали,
    и предлагать её к выбору значит обещать работу, которой не будет.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT site_id, name, url, tags, synced_at FROM dating_site'
            ' WHERE is_gone = 0 AND name <> %s ORDER BY name, site_id', ('',))
        rows = await db.fetchall()

    return [{'id': int(row['site_id']),
             'name': row['name'],
             'url': row['url'],
             'tags': row['tags'],
             'synced_at': row['synced_at']} for row in rows]


def dating_site_sync_start() -> bool:
    """Заводит фоновую сверку снимка с соседом, если её пора и можно заводить.

    Returns:
        bool: идёт ли сверка прямо сейчас — своя или начатая раньше.

    ⚠ **Два сторожа, и оба обязательны** — те же, что у списков собеседников
    (`dating_people.py`). Первый: сверка уже идёт, и каждая страница раздела не
    должна заводить свою. Второй: выдержка между попытками — иначе лежащий сосед
    получал бы запрос на каждое открытие любой страницы, а их у раздела пять.

    Ждать сверку **никто не ждёт**: ответ уходит со снимком, а новое имя сайта
    появится в следующем ответе. Список сайтов — не то, ради чего человека держат
    на пустой странице.
    """
    global _sync_task

    if _sync_task and not _sync_task.done():
        return True

    if time.time() - _sync_at[0] < DATING_SITE_SYNC_EVERY_SEC:
        return False

    _sync_at[0] = time.time()
    _sync_task = asyncio.ensure_future(_sync_run())

    return True


async def dating_site_sync_now() -> dict:
    """Сверка прямо сейчас, с ожиданием. Для кнопки «обновить» и для тестов."""
    return await _sync_run()


# ── Приватное ────────────────────────────────────────────────────────────────

async def _sync_run() -> dict:
    """Спрашивает список у соседа и переписывает снимок. Не бросает.

    Returns:
        dict: `synced` — сколько сайтов в снимке, `error` — причина, если не вышло.

    ⚠ **Неудачная сверка снимок не трогает.** Сосед недоступен — это «сейчас не
    узнали», а не «сайтов больше нет»; стерев снимок, мы получили бы ровно тот
    пустой селект, от которого уходим.
    """
    try:
        sites = await rhapsody_api_site_list()
    except RhapsodyError as e:
        _sync_error[0] = str(e)
        await dating_error_write(DATING_ERROR_SITES_SYNC, str(e))
        logger_info(f'[dating] список сайтов не сверился: {e}')

        return {'synced': 0, 'error': str(e)}
    except Exception as e:
        # Сюда падает то, чего мы не предусмотрели. Ждать эту задачу некому, и без
        # перехвата исключение утонуло бы в логе `asyncio` без следа на странице.
        _sync_error[0] = f'сверка сорвалась: {e}'
        await dating_error_write(DATING_ERROR_SITES_SYNC, _sync_error[0])
        logger_info(f'[dating] сверка списка сайтов упала: {e}')

        return {'synced': 0, 'error': _sync_error[0]}

    await _snapshot_write(sites)

    _sync_error[0] = ''
    _sync_done[0] = time.time()
    await dating_error_clear(DATING_ERROR_SITES_SYNC)

    return {'synced': len(sites), 'error': ''}


async def _snapshot_write(sites: list) -> None:
    """Кладёт ответ соседа в снимок: заводит новые, обновляет прежние, метит ушедшие.

    ⚠ **Ушедшие метятся, а не удаляются.** Вместе со строкой ушли бы и наши
    настройки этого сайта, а закладку могли убрать по ошибке или на время — и
    восстановить её у соседа куда проще, чем вспомнить, что было настроено здесь.
    """
    now = datetime_utc_now().strftime('%Y-%m-%d %H:%M:%S')
    seen = []

    async with mysql_get_db_async() as db:
        for site in sites:
            site_id = int(site.get('id') or 0)
            if not site_id:
                continue
            seen.append(site_id)

            await db.execute(
                'INSERT INTO dating_site (site_id, name, url, tags, is_gone, synced_at)'
                ' VALUES (%s, %s, %s, %s, 0, %s)'
                ' ON DUPLICATE KEY UPDATE name = VALUES(name), url = VALUES(url),'
                ' tags = VALUES(tags), is_gone = 0, synced_at = VALUES(synced_at)',
                (site_id, str(site.get('name') or '')[:190],
                 str(site.get('url') or '')[:255], str(site.get('tags') or '')[:190], now))

        # Всё, чего в ответе не было, у соседа больше нет. Считаем это правдой
        # только потому, что ответ **удался**: неудачная сверка сюда не доходит.
        if seen:
            marks = ', '.join(['%s'] * len(seen))
            await db.execute(
                f'UPDATE dating_site SET is_gone = 1 WHERE site_id NOT IN ({marks})', seen)

        await db.connection.commit()


def _sync_answer(refreshing: bool) -> dict:
    """Что странице знать про сверку: когда сверяли, идёт ли сейчас, чем кончилась."""
    return {'refreshing': bool(refreshing),
            'error': _sync_error[0],
            'at': int(_sync_done[0]) or 0}


def _known() -> list[dict]:
    """Реестр в виде, пригодном для страницы: список, а не словарь.

    Списком, потому что порядок значит: настройки показываются в нём, а у словаря
    порядок — случайность его сборки.
    """
    return [{'key': key,
             'title': spec['title'],
             'note': spec.get('note', ''),
             'default': spec['default'],
             'secret': bool(spec.get('secret')),
             'options': [{'value': value, 'title': title}
                         for value, title in spec.get('options') or ()]}
            for key, spec in DATING_SITE_KNOWN.items()]


def _masked(settings: dict) -> dict:
    """Набор настроек, годный к отправке наружу: секреты заменены точками.

    ⚠ **В этих ответах секрета нет ни для кого, включая администратора.** Список и
    чтение одного сайта уходят на каждую смену сайта и оседают в журналах запросов;
    пароль в них попадать не должен.

    Правке это не мешает: страница шлёт маску обратно, и сохранение понимает её как
    «оставить прежний» (`dating_site_save`). А посмотреть пароль можно отдельной
    ручкой (`dating_site_reveal`) — по нажатию и только админу, чтобы обращение к
    нему было видно отдельным событием.
    """
    secret = {key for key, spec in DATING_SITE_KNOWN.items() if spec.get('secret')}

    return {key: (DATING_SITE_SECRET_MASK if key in secret and value else value)
            for key, value in settings.items()}


async def _settings_all() -> dict[int, dict]:
    """Все сохранённые настройки: номер сайта → набор.

    Одним запросом, а не по сайту: строк здесь единицы (сайтов у соседа десятки), и
    запрос на каждый сайт при показе списка означал бы десяток запросов ради
    десятка строк.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT site_id, settings FROM dating_site')
        rows = await db.fetchall()

    found = {}
    for row in rows:
        raw = row['settings']
        try:
            # MySQL отдаёт JSON строкой, а не разобранным словарём — разбираем сами.
            # Битую строку пропускаем: настройка одного сайта не должна ронять
            # список всех.
            value = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except ValueError as e:
            logger_info(f"[dating] настройки сайта #{row['site_id']} не разобрались: {e}")
            continue

        found[int(row['site_id'])] = value if isinstance(value, dict) else {}

    return found
