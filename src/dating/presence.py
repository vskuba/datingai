"""Присутствие учётки: тонкая обёртка над общим движком heartbeat.

Здесь больше нет своего HTTP: запрос, приметы живого входа и разбор ответа живут в
`py_modules/http_/http_heartbeat.py`, а настройка сайта — в `heartbeat.py`.
Этот файл остался ради **вызывающих**: страница анкеты, круг восстановления и вход
зовут его функции по прежним именам, и менять их разом значило бы править пять
мест ради переезда.

## ⚠ Почему движок один, а не два

Своя реализация здесь была, и она разошлась с новой в тот же день, когда та
появилась. Ladadate настроили присутствие нашей настройкой (`site_.settings`),
круг честно держал анкету в сети — а кнопка «проверить» на карточке смотрела в
`browser_site.presence` соседа, не находила его и ставила учётке `status = error`.
Зелёная точка гасла у анкеты, которая в этот момент была онлайн.

Отсюда правило: **настройку разрешает `heartbeat_config`, и только он.**
Своя у сайта старше, соседская читается запасной — одно место, один порядок.

## Что осталось от прежнего файла

Имена функций и их смысл:

  `presence_probe` — полный опрос: отметиться, проверить вход, снять счётчик;
  `presence_beat`  — только удар, без проверок: он в разы дешевле;
  `presence_ready` — настроено ли присутствие у сайта.

⚠ `presence` вторым параметром сохранён **ради совместимости вызывающих**, но
берётся только как запасной источник: если у сайта есть своя настройка, аргумент
не смотрят вовсе. Убирать его из сигнатуры сейчас — значит трогать пять файлов
ради формы.
"""

from http_.http_heartbeat import (http_heartbeat_check, http_heartbeat_client,
                                  http_heartbeat_cookies, http_heartbeat_ready,
                                  http_heartbeat_send)

from src.dating.json_column import json_column_load

# Оставлено ради вызывающих, которые импортируют константу. Значения живут в
# движке: два набора разошлись бы молча.
from http_.http_heartbeat import HTTP_HEARTBEAT_ERROR_MAX as DATING_PRESENCE_ERROR_MAX
from http_.http_heartbeat import HTTP_HEARTBEAT_TIMEOUT as DATING_PRESENCE_TIMEOUT
from http_.http_heartbeat import HTTP_HEARTBEAT_UA as DATING_PRESENCE_UA


async def presence_probe(profile: dict, presence: dict = None) -> dict:
    """Один опрос учётки: держим онлайн, снимаем статус и счётчик.

    Args:
        profile: строка `profile` со `storage_state` и `site_id`.
        presence: описание сайта у соседа — **запасное**. Своя настройка сайта
            старше, и при её наличии этот аргумент не смотрят.

    Returns:
        dict: `status` (`online` | `offline` | `error`), `unread` (`-1` — не
        спрашивали), `error`, `ms`.
    """
    config = await _config(profile, presence)
    if not http_heartbeat_ready(config):
        return {'status': 'error', 'unread': -1, 'ms': 0,
                'error': 'у сайта не настроено присутствие: заполните «Присутствие: '
                         'настройка» на странице «Сайты»'}

    client = http_heartbeat_client()
    try:
        return await http_heartbeat_check(client, _cookies(profile, config), config,
                                          _values(profile))
    finally:
        await client.aclose()


async def presence_beat(profile: dict, presence: dict = None) -> bool:
    """Только удар — «я на сайте», без проверок и счётчиков.

    ⚠ Для многих учёток разом это **не тот путь**: клиент здесь свой на каждый
    вызов, и тысяча вызовов означала бы тысячу пулов соединений. Круг присутствия
    (`heartbeat_round.py`) бьёт всех одним клиентом — так и надо.
    """
    config = await _config(profile, presence)
    if not http_heartbeat_ready(config):
        return False

    client = http_heartbeat_client()
    try:
        return await http_heartbeat_send(client, _cookies(profile, config), config,
                                         _values(profile))
    finally:
        await client.aclose()


async def presence_ready(presence=None, site_id: int = 0) -> bool:
    """Настроено ли присутствие: без него карточка честно молчит, а не врёт нулём.

    ⚠ **Стала асинхронной**, и это осознанный слом: своя настройка сайта лежит в
    базе, а прежняя проверка судила по одному лишь аргументу — то есть по
    соседскому описанию. Сайт со своей настройкой она объявляла ненастроенным, и
    кнопка «проверить» отказывала там, где присутствие работало.

    Args:
        presence: описание соседа — запасное.
        site_id: сайт, чью настройку спросить. Ноль — судим только по аргументу.
    """
    if site_id:
        from src.dating.heartbeat import heartbeat_config

        if http_heartbeat_ready(await heartbeat_config(int(site_id))):
            return True

    return http_heartbeat_ready(_from_neighbour(presence))


# ── Приватное ────────────────────────────────────────────────────────────────

async def _config(profile: dict, presence) -> dict:
    """Настройка присутствия для этой учётки: своя у сайта, иначе соседская.

    ⚠ **Без `site_id` в строке настройку не найти**, и молчать об этом нельзя.
    Живой случай: круговой опрос брал анкеты запросом без `site_id`, резолвер
    честно проваливался на соседское описание — пустое у Ladadate, — и опрос
    ставил `error` анкете, которая в ту же минуту исправно держалась в сети.
    Ошибку в запросе вызывающего видно по логу, а не по загадочному статусу.
    """
    from logging_.logging_ import logger_info
    from src.dating.heartbeat import heartbeat_config

    site_id = int(profile.get('site_id') or 0)
    if not site_id:
        logger_info(f"[dating] присутствие #{profile.get('id')}: в строке нет "
                    '`site_id` — настройка сайта не спрошена')
    else:
        config = await heartbeat_config(site_id)
        if http_heartbeat_ready(config):
            return config

    return _from_neighbour(presence)


def _cookies(profile: dict, config: dict) -> dict:
    """Cookie сайта из снятого состояния входа."""
    return http_heartbeat_cookies(json_column_load(profile.get('storage_state')),
                                  str(config.get('base') or ''))


def _values(profile: dict) -> dict:
    """Значения `{меток}` адреса — одной функцией с кругом, без своей копии."""
    from src.dating.heartbeat import heartbeat_profile_values

    return heartbeat_profile_values(profile)


def _from_neighbour(presence) -> dict:
    """Старое описание соседа → формат движка. Один перевод на проект.

    ⚠ Зовёт `heartbeat._from_neighbour`, а не повторяет его: два перевода
    разошлись бы в первый же день, когда у движка появится новый ключ.
    """
    from src.dating.heartbeat import _from_neighbour as translate

    return translate(json_column_load(presence))
