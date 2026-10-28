"""Круг гостей: лента «Гости» наполняется сама, анкета за анкетой.

Гости — единственная наша лента, которую снимает **сама анкета**, а не панель:
страница визитов стоит в её кабинете, и под чужой сессией она пустая. Значит
заход — это обход анкет сайта, а не сайта целиком, и темп у него **анкетный**:
слот на анкету, настройка `mailing_guests_every`.

## Что делает заход

Сайтам с галочкой «Гости» в «Активных лентах» берёт анкеты, у которых срок
слота истёк, и по очереди прогоняет сценарий ленты под каждой — имя сценария и
разбор его ответа у драйвера сайта (`site/site_guests.py`). Съём —
тот же, что у кнопки на странице: `scenario_run` с логином анкеты; визиты
кладёт дверь `guests_seen`.

## ⚠⚠ Слот на анкету, а не на сайт

Настройка отвечает «как часто обновлять **ленту гостей**», а ленту делает
совокупность анкет, включая выключенные. Один общий слот на сайт значил бы, что
за ход снятся две-три анкеты, и чей именно черёд — случайность обхода: анкета,
попавшая в хвост, не обновлялась бы месяцами. Слот `dating:guests:profile:{id}`
отвечает честно: у каждой анкеты свой срок, и «давно ли сняты её гости» —
вопрос с одним адресатом.

## ⚠ Заход не монопольный

Браузерный пул у круга общий с чатом, рассылкой и прочими: три анкеты за тик —
потолок, нужный, чтобы час пик на «Фаворах» не встал из-за гостей. Остальные
анкеты дождутся следующего тика — их слоты никуда не денутся.

## ⚠ Отказ — отдых, а не стук в ту же дверь

У упавшего прогона слот **не ставится**: это значило бы заморозить ленту на
часы из-за одной неудачной минуты. Вместо него память процесса держит остуду
`GUESTS_FAIL_BACKOFF_SEC` — пережил сосед, и следующий тик пробует снова.
"""

from logging_.logging_ import logger_info
from src.rhapsody.rhapsody_api import (RhapsodyError,
                                       rhapsody_api_scenario_forget)

from src.dating.guests import (guests_feed_on, guests_seen,
                               guests_site_profiles)
from src.dating.round import (round_mem_due, round_mem_mark, round_slot_due,
                              round_slot_mark, round_sites, round_sites_run)
from src.dating.scenario import scenario_find, scenario_run
from src.dating.site.site_registry import site_registry_guests
from src.dating.site_presence import site_presence
from src.dating.site_setting import site_seconds

# Частота пробуждения круга. Как у прочих: слоты меряются часами, и чаще
# смотреть нечего.
GUESTS_ROUND_TICK_SEC = 60

# Сколько анкет успевает сняться за один тик. ⚠ Это **не** частота съёма —
# частота у сценария, прогон которого идёт под её входом, — а потолок очереди
# к общему браузерному пулу на один тик.
GUESTS_PROFILES_MAX = 3

# Остуда после неудачи. Час слота — много, минута — столбы в журнале; четверть
# часа ровно между ними: сбой соседа переживается без лавины повторных попыток.
GUESTS_FAIL_BACKOFF_SEC = 15 * 60

# Слот темпа на анкету. ⚠ Метка `{id}` текстом, а не `{site_id}`: чужая метка
# осталась бы в ключе литералом — один общий слот на все анкеты (round.md).
GUESTS_SLOT = 'dating:guests:profile:{id}'

# Остуды и «сказано о сценарии» — память процесса, свои на каждый круг.
_guests_fail = {}
_guests_told = {}


async def guests_round_run() -> dict:
    """Один круг: снять гостей там, где срок истёк.

    Returns:
        dict: `checked` — сайтов посмотрели, `profiles` — анкет сняли,
        `guests` — строк визитов легло.

    ⚠ Списка сайтов-выключателей у занятия нет: галочка живёт в настройке
    `mailing_feeds` (коробки «Активные ленты»), и её спрашивает шлюз, а не
    `round_sites`.
    """
    sites = await round_sites(what='гостям')

    async def _gate(row: dict, minutes: int) -> bool:
        return await guests_feed_on(row['site_id'])

    async def _work(row: dict, minutes: int) -> dict:
        return await guests_site_sweep(row['site_id'])

    # `keep_going`: занятый пул у одного сайта не глушит обход остальных.
    got = await round_sites_run(sites, gate=_gate, work=_work,
                                what='гостям', keep_going=True)

    return {'checked': len(sites),
            'profiles': sum(int(one.get('profiles') or 0)
                            for _, one in got if isinstance(one, dict)),
            'guests': sum(int(one.get('guests') or 0)
                          for _, one in got if isinstance(one, dict))}


async def guests_slot_every_sec(site_id: int) -> int:
    """Через сколько секунд анкета этого сайта снова зовёт своих гостей.

    Настройка хранит часы, код умножает на 3600 — примечание к реестру
    `mailing_guests_every`. Считаем здесь, а не на месте: таймер «когда лента
    гостей обновится сама» на странице читает это же число, и своя формула
    разъехалась бы со слотом круга на первой же правке.
    """
    return 3600 * await site_seconds(site_id, 'mailing_guests_every')


async def guests_site_sweep(site_id: int, due_only: bool = True,
                            cap: int = GUESTS_PROFILES_MAX) -> dict:
    """Снимает гостей сайта — по анкетам, чей срок истёк.

    Args:
        site_id: сайт.
        due_only: `False` — снимать всех, независимо от срока и остуды: кнопка
            «обновить» на странице, человек ждёт ответа сейчас. Обходятся оба
            тормоза: и слот, и память о неудаче — «пять минут назад не вышло»
            не ответ человеку, который нажал кнопку.
        cap: сколько анкет за заход; `0` — без потолка. Потолок — защита
            общего пула от круга, а не от человека, который жмёт кнопку.

    Returns:
        dict: `profiles` — скольким анкетам прогнали сценарий, `guests` —
        сколько строк визитов легло всего; `error` — почему не снялось вовсе
        (пока один случай: нет сценария). Кнопка передаёт его человеку, круг
        его не показывает — у круга свой канал, журнал.

    ⚠ **Пустая страница — тоже успех.** У анкеты без гостей слот ставится:
    «сняли — никого» не значит «не снялось», и гонять прогон ради пустой
    страницы каждые полминуты незачем.
    """
    every = await guests_slot_every_sec(site_id)
    profiles = await guests_site_profiles(site_id)

    due = [one for one in profiles
           if not due_only or (round_mem_due(_guests_fail, one['id'],
                                             GUESTS_FAIL_BACKOFF_SEC)
                               and round_slot_due(GUESTS_SLOT, one['id'],
                                                  what='гостям',
                                                  every=every))]

    due = due[:cap] if cap else due
    if not due:
        return {'profiles': 0, 'guests': 0}

    # Драйвер сайта: он знает и имя сценария, и то, как из его ответа вынуть
    # человека с датой визита. У сайтов это разные страницы и разная вёрстка —
    # круг про это спрашивать не должен (см. `site/site_guests.py`).
    driver = site_registry_guests(await site_presence(site_id))
    name = driver.scenario_name()
    scenario = await scenario_find(site_id, name)
    if not scenario:
        # смолчим минуту — но и повторять одну и ту же строку каждый тик
        # нельзя: «Журналы» утонут в ней. Раз в остуду на сайт — честно и не
        # шумно; анкеты при этом получают остуду, как от упавшего прогона.
        if round_mem_due(_guests_told, site_id, GUESTS_FAIL_BACKOFF_SEC):
            round_mem_mark(_guests_told, site_id)
            logger_info(f'[dating] сайту #{site_id} нечем снимать гостей: '
                        f'нет сценария «{name}» — заведите его в '
                        f'RhapsodyAi на вкладке «Сценарии»')

        for one in due:
            round_mem_mark(_guests_fail, one['id'])

        return {'profiles': 0, 'guests': 0,
                'error': f'нет сценария «{name}» — заведите его у соседа '
                         f'на вкладке «Сценарии»'}

    gone = 0
    for one in due:
        got = await _profile_guests(scenario, driver, site_id, one)
        if got is None:
            round_mem_mark(_guests_fail, one['id'])
            continue

        _guests_fail.pop(one['id'], None)

        # ⚠ Отметка — **после** удачи: раньше значит заморозить ленту на срок
        # настройки даже тогда, когда ничего и не записалось (round.md).
        round_slot_mark(GUESTS_SLOT, one['id'], every=every)
        gone += got

    return {'profiles': len(due), 'guests': gone}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _profile_guests(scenario: dict, driver, site_id: int,
                          profile: dict) -> int | None:
    """Гости одной анкеты: прогон под её входом и запись визитов.

    Args:
        scenario: найденный сценарий.
        driver: драйвер сайта — ему принадлежит разбор ответа прогона.
        site_id: сайт.
        profile: анкета (`id`, `login`, `proxy_id`).

    Returns:
        `None` — неудача (причина в журнале прогона и в этой строке), иначе
        число легших строк, возможно ноль.
    """
    try:
        # `keep_session=False`: вкладка нужна на один прогон — она стоит ~143 МБ
        # в пуле, общем с соседом, а анкет в заходе несколько.
        # Выход — у анкеты: сценарий входит под её учёткой, и общий адрес
        # сервера сложил бы лимит входов на нас (proxy.md).
        run = await scenario_run(
            scenario, site_id, int(profile['id']), '',
            {'profile_login': str(profile.get('login') or '')}, {},
            keep_session=False, proxy_id=int(profile.get('proxy_id') or 0))
    except RhapsodyError as e:
        # «Нет такого сценария» у соседа — повод забыть его номер: сценарий
        # переименовали или удалили, а мы держим номер и стучимся по нему на
        # каждый тик. Прочие отказы номера не порочат.
        if ' → 404' in str(e):
            rhapsody_api_scenario_forget(site_id, driver.scenario_name())

        logger_info(f"[dating] гости анкеты #{profile['id']}: {e}")

        return None

    if str(run.get('status')) == 'error':
        logger_info(f"[dating] гости анкеты #{profile['id']}: сценарий встал "
                    f"на шаге {run.get('step_no')}: {run.get('error')}")

        return None

    rows = _freshest_first(driver.rows(run.get('extract') or {}))

    # ⚠ Лимит `mailing_guests_max` режет **до записи**: лента строится по всей
    # таблице, и раздувать её гостями, которых настройка всё равно не покажет,
    # незачем.
    cap = await site_seconds(site_id, 'mailing_guests_max')

    return await guests_seen(site_id, int(profile['id']), rows[:cap])


def _freshest_first(rows: list[dict]) -> list[dict]:
    """Свежие визиты впереди, бездатые — в хвосте; порядок страницы сохранён.

    ⚠ Страница уже отсортирована по свежести, и сортировка обязана **беречь**
    этот порядок, а не переставлять равных: Python сортирует устойчиво, и мы на
    это опираемся — бездатые (`None`) уходят в конец, а равные по дате стоят
    как пришли.
    """
    return sorted(rows, key=lambda one: (one.get('visit') is None,
                                         -(one['visit'].toordinal()
                                           if one.get('visit') else 0)))