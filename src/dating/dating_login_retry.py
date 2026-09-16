"""Сколько раз подряд пробовать вход и с какой паузой — две настройки сайта.

## ⚠⚠ Зачем заведено: девять входов за четырнадцать минут

Живой случай на проде, анкета #41 (Ladadate). Сценарий проходил форму, сайт
кабинет не подтверждал, и круг расписания пробовал снова — **каждые 86 секунд,
безостановочно**:

    17:11:17  Прохожу форму входа сценарием «вход под анкетой»
    17:11:42  Сайт вход не признал: нужен повторный логин
    17:12:43  Прохожу форму входа сценарием «вход под анкетой»
    17:13:09  Сайт вход не признал: нужен повторный логин
    …

Ровно то, за что сайт закрывает вход по внешнему адресу, и ровно та беда, из-за
которой мы уже сутки просидели в блокировке Ladadate.

## ⚠ Почему лестница отката это не поймала

Лестница пауз (`dating_revive.py`) была, работала и к этому входу **отношения не
имела**: её спрашивает круг восстановления, а входил круг расписания — прямым
вызовом `dating_login_run`, минуя все правила.

Отсюда первое решение: **правило живёт у самой двери**. Проверка стоит в
`dating_login_run`, то есть перед любым входом — расписание, восстановление,
кнопка, — и обойти её нельзя, не переписав вход.

## ⚠ Почему параметрами, а не константами

Прежние сроки были зашиты в код лестницей `(1, 5, 15, 45, 90)` минут. Спорить с
ними человек не мог, и — что хуже — не мог **узнать**, чего ждать: в панели это
выглядело как «оно почему-то не входит». Теперь в настройках сайта две строки, и
обе отвечают на вопрос словами:

| Ключ | Что задаёт | По умолчанию |
|------|-----------|--------------|
| `login_retry_max` | сколько неудач подряд терпим | 3 |
| `login_retry_every` | пауза между попытками | 15 минут |

Кончились попытки — автомат к сайту не идёт вовсе, пока человек не нажмёт «Войти»
руками. Это и есть предел: неудачи копятся, счётчик не сбрасывается сам по
времени, а гаснет **только удавшимся входом**.

⚠ **Пауза одна на все попытки, а не лестница.** Растущая пауза точнее в теории —
частые беды рассасываются за минуту, редкие требуют часа, — но объяснить её
человеку нечем: он видит «следующая попытка через 45 минут» и не понимает, откуда
число. Одна пауза и один потолок понятны с первого взгляда, а разницу в цене
покрывает потолок: три попытки по четверти часа — это сорок пять минут, после
которых автомат замолкает совсем.

## Кнопка человека сильнее

`force=True` проходит и через исчерпанный счётчик, и через паузу: человек у экрана
видит больше автомата — он мог сменить пароль, разблокировать учётку, дождаться
конца чужой блокировки. Отказывать ему значило бы чинить сайт через базу.
"""

from datetime import timedelta

from datetime_.datetime_ import datetime_utc_now
from mysql_.mysql_ import mysql_get_db_async

# Настройки сайта. Ключи — в `DATING_SITE_KNOWN`.
DATING_LOGIN_RETRY_MAX_SETTING = 'login_retry_max'
DATING_LOGIN_RETRY_EVERY_SETTING = 'login_retry_every'

# Умолчания на случай, если настройки у сайта нет. Три попытки по четверти часа:
# сорок пять минут на беду, которая проходит сама, и полная тишина после.
DATING_LOGIN_RETRY_MAX_DEFAULT = 3
DATING_LOGIN_RETRY_EVERY_DEFAULT = 900


class DatingLoginHeld(Exception):
    """Вход сейчас запрещён: идёт пауза или кончились попытки.

    ⚠ **Своё исключение, а не `DatingLoginError`.** Тот значит «войти нечем» —
    нет пароля, сценария, сайта, — и человек по нему идёт что-то исправлять.
    Здесь же всё настроено, просто рано: круг обязан промолчать и уйти, а не
    писать в журнал строку, похожую на поломку.
    """


async def dating_login_retry_allowed(profile: dict) -> tuple[bool, str]:
    """Можно ли пробовать вход прямо сейчас.

    Returns:
        tuple: `(можно, причина)`. Причина непустая только у запрета и написана
        для человека: она уходит в журнал анкеты.
    """
    site_id = int(profile.get('site_id') or 0)
    tries = int(profile.get('login_fail_tries') or 0)
    if not tries:
        return True, ''

    retry_max = await _retry_max(site_id)
    if tries >= retry_max:
        return False, (f'вход не идёт: {tries} неудач подряд из {retry_max} '
                       f'разрешённых. Автомат больше не пробует — нажмите «Войти» '
                       f'на карточке анкеты, когда будет что менять')

    every = await _retry_every(site_id)
    last = profile.get('login_fail_at')
    if not last:
        return True, ''

    left = int(every - (datetime_utc_now() - last).total_seconds())
    if left > 0:
        return False, (f'вход не идёт: после неудачи ждём {_minutes(every)}, '
                       f'осталось {_minutes(left)}. Попытка {tries + 1} из {retry_max}')

    return True, ''


async def dating_login_retry_fail(profile_id: int) -> None:
    """Записывает неудачу входа: счётчик плюс один, отсчёт паузы с этой минуты.

    ⚠ **Счётчик не сбрасывается временем.** Пауза кончилась — значит можно
    пробовать снова, но попытка эта уже вторая, а не первая. Считай мы «давно
    было, начнём заново», потолок не наступил бы никогда: сайт, отвергающий вход
    ровно раз в четверть часа, получал бы их бесконечно.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE dating_profile'
            '   SET login_fail_tries = login_fail_tries + 1, login_fail_at = NOW()'
            ' WHERE id = %s', (int(profile_id),))
        await db.connection.commit()


async def dating_login_retry_clear(profile_id: int) -> None:
    """Гасит счётчик — только удавшийся вход и делает это.

    Зовётся оттуда же, где снимается тревога автовосстановления: удача по одной
    двери не должна означать разного в двух местах.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE dating_profile SET login_fail_tries = 0, login_fail_at = NULL'
            ' WHERE id = %s', (int(profile_id),))
        await db.connection.commit()


# ── Приватное ────────────────────────────────────────────────────────────────

async def _retry_max(site_id: int) -> int:
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id, DATING_LOGIN_RETRY_MAX_SETTING)
               or DATING_LOGIN_RETRY_MAX_DEFAULT)


async def _retry_every(site_id: int) -> int:
    from src.uvicorn.routers.dating.dating_site import dating_site_seconds

    return int(await dating_site_seconds(site_id, DATING_LOGIN_RETRY_EVERY_SETTING)
               or DATING_LOGIN_RETRY_EVERY_DEFAULT)


def _minutes(seconds: int) -> str:
    """Секунды словами: «15 минут», «90 секунд». Человек читает журнал глазами."""
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f'{seconds} сек'

    return f'{round(seconds / 60)} мин'
