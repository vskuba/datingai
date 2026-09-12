"""Отправка одной реплики из пула инвайтов: одна дорога на кнопку и на круг.

Зовут это двое — кнопка на карточке собеседника
(`routers/dating/dating_invite.py`) и круг отправки
(`dating_invite_send_round.py`), — и разойтись им нельзя: отметка исхода, запись
в журнал анкеты и то, что считать удачей, обязаны быть одни и те же. Разведи мы
их, кнопка и круг однажды по-разному ответили бы на вопрос «ушло ли».

⚠ **Ничего не бросает.** Исход возвращается словами: кнопка превращает его в
409 с причиной, круг — в строку журнала. Исключение вместо ответа означало бы,
что круг обязан ловить `HTTPException` — то есть знать про HTTP, которого у него
нет.

⚠ **`ok` от сайта — это «принял кадр», а не «дошло до адресата».** У LadaDate
сообщение от женской анкеты сайт принимает и показывает в её же переписке, а
мужчине оно не приходит, пока тот не пригласит первым (`needInvite`, подробности
в `dating_send.py`). «Ушло» здесь означает ровно то, что сказал сайт.

⚠ **Неудача оставляет инвайт в пуле** (`draft` с причиной в `error`), а не
переводит в `failed`. Отказ обычно временный — собеседник вышел из чата, вход не
прошёл, — и повторить надо тем же нажатием. `failed` означал бы «больше не
пробуем», чего никто не решал. То же правило, что у писем.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_feed import dating_feed_write
from src.dating.dating_json import dating_json_load
from src.dating.dating_log import dating_log_write
from src.dating.dating_send import dating_send_message
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_get


async def dating_invite_send_one(invite: dict) -> dict:
    """Отправляет реплику живому человеку в чат и отмечает исход.

    Args:
        invite: строка `dating_invite` — нужны `id`, `site_id`, `profile_id`,
            `uid`, `body` и `name` (последнее только для журнала).

    Returns:
        dict: `ok` — приняла ли отправка, `error` — причина словами, если нет.

    ⚠ **Состояние строки не проверяется здесь.** «Уже ушёл» — вопрос очереди, и
    отвечают на него по-разному: кнопка отдаёт 409 человеку, круг такую строку
    в выборку не берёт вовсе. Проверь мы это внутри, оба получили бы один и тот
    же безликий отказ вместо своего.
    """
    invite_id = int(invite['id'])
    site_id = int(invite['site_id'])
    profile_id = int(invite['profile_id'])
    who = str(invite.get('name') or '') or str(invite['uid'])

    # ⚠⚠ **Занимаем строку до отправки — иначе одну реплику пошлют несколько раз.**
    #
    # Поймано живьём: Semen Gorbunkov получил **четыре одинаковых** «Semen, what do
    # you do for fun…» с разницей в 12 секунд. Отправка сценарием идёт десятками
    # секунд, кнопка на карточке при этом оставалась живой — человек, не увидев
    # отклика, нажал ещё трижды. Четыре запроса прочитали строку как `draft`
    # раньше, чем первый успел её отметить, и все четыре ушли.
    #
    # Защита «тот же текст подряд не шлём» (`dating_send._said_already`) здесь не
    # помогает **по устройству**: она читает последнюю реплику диалога, а все
    # четыре успели прочитать её до того, как хоть одна записалась. Проверка и
    # запись должны быть одним действием, и здесь это условный `UPDATE`.
    #
    # Гонка не только человеческая: круг отправки и кнопка ходят одной дорогой, и
    # круг вправе взять строку ровно в тот миг, когда по ней щёлкнули.
    if not await _claim(invite_id):
        return {'ok': False, 'error': 'инвайт уже отправляется или отправлен'}

    profile = await _profile(profile_id)
    if not profile:
        return await _fail(site_id, invite_id, profile_id, who, 'анкеты больше нет')

    try:
        site = await rhapsody_api_site_get(site_id)
    except RhapsodyError as e:
        # Сосед лежит — это «сейчас не отправить», а не «не отправлять».
        # Инвайт остаётся в пуле с причиной и уйдёт следующим заходом.
        return await _fail(site_id, invite_id, profile_id, who, str(e))

    if not site:
        return await _fail(site_id, invite_id, profile_id, who, f'у соседа нет сайта {site_id}')

    try:
        got = await dating_send_message(profile, dating_json_load(site.get('presence')),
                                        str(invite['uid']), str(invite['body']))
    except RhapsodyError as e:
        return await _fail(site_id, invite_id, profile_id, who, str(e))
    except Exception as e:
        # Чего не предусмотрели. Круг обязан пережить это и пойти дальше: один
        # странный ответ сайта не повод останавливать отправку по всему сайту.
        logger_info(f'[dating] инвайт #{invite_id} сорвался: {type(e).__name__}: {e}')

        return await _fail(site_id, invite_id, profile_id, who, f'{type(e).__name__}: {e}')

    if not got.get('ok'):
        why = str(got.get('error') or '') or (
            'собеседника нет в чате' if got.get('offline') else 'сайт не принял')

        return await _fail(site_id, invite_id, profile_id, who, why)

    await dating_invite_send_mark(invite_id, sent=True, error='')
    await dating_log_write(profile_id, 'ok', f'Инвайт ушёл: {who}')
    # ⚠ **И в ленту событий, а не только в журнал анкеты.** Журнал открывают у
    # конкретной девушки и постфактум, а вопрос «панель сейчас пишет живым людям?»
    # задают о сайте целиком и в тот же момент. Отправку видно с любой страницы
    # раздела — она стоит в полосе вкладок (`api.md`, §3.12).
    #
    # «От → кому», а не «инвайт ушёл»: одна девушка зовёт многих, и без обеих
    # сторон строка не отвечает ни на «кто пишет», ни на «кого позвали».
    dating_feed_write(site_id, 'invite', f'Инвайт: {_girl(profile)} → {who}', profile_id)

    return {'ok': True, 'error': ''}


async def dating_invite_send_mark(invite_id: int, sent: bool, error: str) -> None:
    """Отмечает исход отправки в пуле.

    ⚠ Неудача возвращает строку в `draft` — см. заголовок модуля — **и снимает
    `sent_at`**, поставленный при занятии (`_claim`). Поле отвечает на вопрос
    «когда человек это прочитал», и у неотправленного ответа нет; оставь мы метку,
    неудачная попытка выглядела бы удачной и держала бы человека паузой повтора.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE dating_invite'
            ' SET status = %s, error = %s, sent_at = IF(%s, NOW(), NULL)'
            ' WHERE id = %s',
            ('sent' if sent else 'draft', str(error or '')[:500],
             1 if sent else 0, int(invite_id)))
        await db.connection.commit()


# ── Приватное ────────────────────────────────────────────────────────────────

async def _claim(invite_id: int) -> bool:
    """Занимает строку под отправку. `False` — её уже взял кто-то другой.

    Условный `UPDATE` — единственный способ сделать «проверить и занять» одним
    действием: между `SELECT status` и `UPDATE` помещается сколько угодно чужих
    запросов, а между условием и записью внутри одного `UPDATE` — ни одного.

    ⚠ **Занимаем как «отправлено», а не как «отправляется».** Отдельного состояния
    нет, и заводить его миграцией ради нескольких секунд полёта не стоит: неудача
    вернёт строку в `draft` тем же `dating_invite_send_mark`. Цена размена —
    процесс, умерший ровно посреди отправки, оставит строку `sent` при недошедшей
    реплике. Обратный порядок хуже: он терял бы запись о **дошедшей**, а
    непоказанная доставка дороже показанной лишней.

    Сбой базы — отказ занять: не сумев отметить, мы не имеем права слать.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                "UPDATE dating_invite SET status = 'sent', sent_at = NOW()"
                " WHERE id = %s AND status = 'draft'", (int(invite_id),))
            taken = int(db.rowcount or 0)
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] инвайт #{invite_id} не занялся: {e}')

        return False

    return taken == 1


async def _profile(profile_id: int) -> dict:
    """Анкета, от имени которой пишем. Пусто — её удалили, пока реплика лежала."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM dating_profile WHERE id = %s',
                             (int(profile_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] анкета #{profile_id} для инвайта не прочиталась: {e}')

        return {}

    return dict(row) if row else {}


def _girl(profile: dict) -> str:
    """Как назвать девушку в ленте: имя из анкеты, а нет его — логин.

    Имя привозит чтение анкеты с сайта и заполнено не у всех; логин есть всегда.
    Пустая левая половина строки («Инвайт:  → David») читалась бы как сбой.
    """
    card = dating_json_load(profile.get('profile')) or {}

    return str(card.get('name') or '').strip() or str(profile.get('login') or '')


async def _fail(site_id: int, invite_id: int, profile_id: int, who: str,
                why: str) -> dict:
    """Причина — в строку пула, в журнал анкеты и в ленту; исход — вызывающему.

    ⚠ `site_id` обязателен и стоит первым: лента отбрасывает событие с нулевым
    сайтом молча, и забытый аргумент означал бы неудачу, которой не видно нигде,
    кроме журнала конкретной девушки.

    ⚠ Причину храним **до** ответа: страница покажет её на карточке, а человек
    закроет вкладку и вернётся завтра — в ответе HTTP её к тому времени нет.
    """
    await dating_invite_send_mark(invite_id, sent=False, error=why)
    await dating_log_write(profile_id, 'fail', f'Инвайт {who}: {why}')
    logger_info(f'[dating] инвайт #{invite_id} не ушёл: {why}')
    # Неудача — тоже событие сайта, и видом `warn`: «пул полон, а ничего не
    # уходит» иначе выглядит как остановившийся круг, хотя круг работает и
    # получает отказ за отказом.
    dating_feed_write(site_id, 'warn', f'Инвайт не ушёл: {who} — {why}', profile_id)

    return {'ok': False, 'error': why}
