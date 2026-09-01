"""Настройки сайта на нашей стороне: ключ → значение по номеру у соседа.

Сам сайт живёт в RhapsodyAi — там его имя, адрес, метки и `presence`. Здесь только
то, что относится к **нашей** работе с ним и у соседа хранить негде.

## ⚠ Это не копия списка сайтов

Правило проекта в силе: своей таблицы сайтов здесь нет (`CLAUDE.md`). Имени и
адреса в `dating_site` нет — они приходят от соседа при каждом показе. Хранится
только номер как ключ и наши значения; вторая копия имени разошлась бы с первой в
тот день, когда сайт переименуют.

Отсюда форма ответа: ручка складывает **список соседа** и **наши настройки**, а не
отдаёт одно вместо другого. Сайт без сохранённых настроек в списке всё равно есть —
с пустым набором.

## Почему JSON, а не колонка на настройку

Настройки сайта только заводятся, и какие понадобятся, сейчас не знает никто.
Колонка под каждую означала бы миграцию на каждую. Когда список устоится, частые
переедут в свои колонки — тогда будет что переносить.

## Права

Правка — только администратору: настройка сайта меняет работу всех анкет на нём, а
не того, кто её открыл. Чтение открыто всем вошедшим: оператору полезно видеть, с
чем он работает, и прятать это значило бы звать админа на каждый вопрос.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_admin_required, auth_required
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
# рисует поле ввода. `secret: True` — значение наружу не отдаётся вовсе, см.
# `DATING_SITE_SECRET_MASK`.
DATING_SITE_KNOWN = {
    'favorites_keep': {
        'title': 'Кеш My Favorites',
        'note': 'сколько держим снятый список избранных у каждой анкеты',
        'default': '604800',
        'options': [('604800', '7 дней'), ('259200', '3 дня'), ('86400', '24 часа')],
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

# Чем секрет выглядит наружу. ⚠ **Настоящее значение из ручки не уходит никогда** —
# ни оператору, ни администратору: страница его не показывает, а значит и получать
# ей нечего. Прочесть пароль можно там же, где он и лежит, — в базе.
#
# Точки, а не пустая строка: пустое значение на странице читалось бы как «пароль не
# задан», и человек вписал бы его второй раз, гадая, почему не работало.
DATING_SITE_SECRET_MASK = '••••••••'


class DatingSiteSave(BaseModel):
    """Настройки сайта целиком: страница шлёт весь набор, а не по одной."""

    settings: dict[str, str] = Field(default_factory=dict)


@router.get('', include_in_schema=False)
async def dating_site_list(user: AuthUser = Depends(auth_required)):
    """Сайты соседа вместе с нашими настройками.

    Returns:
        dict: `sites` — записи `id`, `name`, `url`, `settings`; `known` — реестр
        известных настроек, по которому страница рисует их набором значений, а не
        полем ввода.

    Raises:
        HTTPException: 502 — сосед не ответил.

    Список ведёт **сосед**: сайт, которого у него нет, показывать незачем, даже
    если наши настройки для него в базе остались. Обратное тоже верно — сайт без
    настроек виден с пустым набором, иначе его нельзя было бы настроить в первый раз.
    """
    try:
        sites = await rhapsody_api_site_list()
    except RhapsodyError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    saved = await _settings_all()

    return {'result': {'sites': [{'id': int(site.get('id') or 0),
                                  'name': str(site.get('name') or ''),
                                  'url': str(site.get('url') or ''),
                                  'settings': _masked(saved.get(int(site.get('id') or 0), {}))}
                                 for site in sites],
                       'known': _known()}}


@router.get('/{site_id}', include_in_schema=False)
async def dating_site_get(site_id: int, user: AuthUser = Depends(auth_required)):
    """Настройки одного сайта. Нет строки — пустой набор, а не 404.

    Сайт, который ещё не настраивали, — законное состояние, а не ошибка: строка
    появляется при первом сохранении.
    """
    return {'result': {'site_id': site_id,
                       'settings': _masked((await _settings_all()).get(site_id, {}))}}


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


# ── Приватное ────────────────────────────────────────────────────────────────

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

    ⚠ **Секрет не отдаётся никому, включая администратора.** Правку это не мешает:
    страница шлёт маску обратно, и сохранение понимает её как «оставить прежний»
    (`dating_site_save`). А вот утечь через журнал запросов, скриншот или чужой
    глаз за плечом значению больше нечем.
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
