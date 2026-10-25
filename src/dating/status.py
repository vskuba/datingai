"""Опрос присутствия по сайту: чей вход ещё жив, и запись итога в карточки.

Один дешёвый HTTP-запрос на анкету по сохранённым cookie — без браузера и без
соседа: вкладка Chromium стоит ~143 МБ, а клиент с cookie единицы килобайт.
Отсюда и право звать это часто.

## Почему отдельным модулем, а не в ручке

Звать это стало **двое**: ручка `/api/dating/profile/probe` (её раз в 15 секунд
дёргает открытая страница «Анкеты») и круг воркера
(`status_round.py`). Правил тут ровно два, и оба такие, что второй копии
им заводить нельзя:

* **`last_seen_at` двигает только подтверждённый вход** — иначе по колонке «Вход»
  не отличить живую анкету от отвалившейся неделю назад;
* **в «Ошибки» пишем только смену состояния**, а не каждый неудачный заход: сотня
  анкет раз в пятнадцать секунд залила бы вкладку за минуту одним и тем же.

⚠ **Опрос ничего не чинит.** Он спрашивает и записывает; вход заводит
`schedule_round` в начале смены, восстанавливает — `revive_round`.
Смешай мы это, два круга гоняли бы браузер наперегонки, а вход стоит минут живого
Chromium.

⚠ **Настройка присутствия берётся у сайта, своя** (`heartbeat_config`), и
только если её нет — соседское описание. Без неё сайт не опрашивается вовсе:
без адреса удара бить некуда, и объявлять анкету сломанной за это нельзя.
"""

import asyncio

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.presence import presence_probe, presence_ready
from src.rhapsody.rhapsody_api import rhapsody_api_error_write

# Сколько анкет опрашиваем разом. У двухсот учёток последовательный круг занял бы
# минуты, а параллельный укладывается в секунды.
STATUS_PARALLEL = 16


async def status_probe_site(site_id: int, presence: dict = None) -> dict:
    """Опрашивает анкеты сайта, у которых есть чем ходить, и пишет итог в карточки.

    Args:
        site_id: сайт-закладка. ⚠ Обязателен: по нему разрешается настройка
            присутствия. Без него резолвер уходил на соседское описание — пустое
            у Ladadate, — и опрос ставил `error` анкете, которая в ту же минуту
            исправно держалась в сети.
        presence: соседское описание сайта — **запасное**, на случай сайта без
            своей настройки. Круг его не передаёт: к соседу он не ходит.

    Returns:
        dict: `ready` — настроено ли присутствие (нет — остальное нули и никого
        не трогали), `checked`, `online`, `profiles` — по строке на анкету.

    ⚠ **Выключенные анкеты не опрашиваются.** «Не ведём» значит не ведём совсем:
    ходить на сайт под учёткой, снятой с ведения, панель права не имеет.
    """
    site_id = int(site_id or 0)
    if not await presence_ready(presence, site_id):
        return {'ready': False, 'checked': 0, 'online': 0, 'profiles': []}

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, login, profile_id, site_id, status, storage_state'
            ' FROM profile'
            ' WHERE site_id = %s AND enabled = 1 AND storage_state IS NOT NULL',
            (site_id,))
        profiles = [dict(row) for row in (await db.fetchall() or [])]

    if not profiles:
        return {'ready': True, 'checked': 0, 'online': 0, 'profiles': []}

    limit = asyncio.Semaphore(STATUS_PARALLEL)

    async def probe(profile):
        async with limit:
            return profile, await presence_probe(profile, presence)

    done = await asyncio.gather(*(probe(profile) for profile in profiles),
                                return_exceptions=False)

    async with mysql_get_db_async() as db:
        for profile, result in done:
            await status_write(db, int(profile['id']), result)
        await db.connection.commit()

    await _gone_write(site_id, done)

    return {'ready': True,
            'checked': len(done),
            'online': sum(1 for _, result in done if result['status'] == 'online'),
            'profiles': [{'id': profile['id'], 'status': result['status'],
                          'unread': result['unread'], 'error': result['error'],
                          'ms': result['ms']} for profile, result in done]}


async def status_write(db, profile_id: int, result: dict) -> None:
    """Кладёт итог опроса в карточку учётки. Без `commit` — его делает вызвавший.

    Одна на круговой опрос и на пинг одной анкеты: у результата есть правило,
    которое нельзя знать по-разному в двух местах, — **`last_seen_at` двигает
    только подтверждённый вход.** Смысл поля «когда сайт последний раз признал
    учётку», а не «когда мы стучались»; сдвинь его неудачный опрос — и по колонке
    «Вход» уже не отличить живую анкету от той, что отвалилась неделю назад.

    `unread = -1` («не спрашивали») кладём нулём: колонка целочисленная и означает
    количество, а минус первое письмо ничего не значит.
    """
    seen = 'NOW()' if result['status'] == 'online' else 'last_seen_at'
    await db.execute(
        f'UPDATE profile SET status = %s, unread = %s, last_error = %s, '
        f'       last_seen_at = {seen} WHERE id = %s',
        (result['status'],
         result['unread'] if result['unread'] >= 0 else 0,
         result['error'], int(profile_id)))


# ── Приватное ────────────────────────────────────────────────────────────────

async def _gone_write(site_id: int, done: list) -> None:
    """В «Ошибки» — только тех, кто **только что** ушёл из сети.

    Не каждый неудачный заход: опрос идёт раз в минуту у круга и раз в пятнадцать
    секунд у открытой страницы, и сотня учёток залила бы вкладку одним и тем же.
    Текущее состояние и так видно в списке.

    Не бросает: журнал не имеет права уронить опрос, ради которого его писали.
    """
    for profile, result in done:
        if result['status'] == 'online' or str(profile.get('status') or '') != 'online':
            continue

        try:
            await rhapsody_api_error_write(
                'dating',
                f"учётка «{profile['login']}» ушла из сети: "
                f"{result['error'] or result['status']}",
                context={'profile_id': profile['id'], 'site_id': site_id,
                         'where': 'опрос присутствия'})
        except Exception as e:
            logger_info(f'[dating] уход из сети не записался: {e}')
