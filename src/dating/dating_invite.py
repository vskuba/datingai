"""Инвайты: первая реплика в чат тому, с кем разговора ещё не было.

Пул устроен как пул писем — карточки «кто, кому, с каким текстом», которые человек
читает, правит и отправляет, — но занятие другое. Письмо продолжает знакомство,
инвайт его **начинает**: одна реплика, которая всплывёт у мужчины в окне чата.

## Кого зовём

Кандидаты берутся из снимка ленты «Все онлайн» — того же, что кормит рассылку, — и
из него вычитаются двое:

| Кто вычитается | Почему |
|----------------|--------|
| с кем у этой анкеты уже есть диалог | приветствие тому, с кем говорили, читается как сбой |
| кому инвайт уже готовили | пул набирает круг, и повтор был бы вторым приглашением |

⚠ **Диалог считается по анкете, а не по сайту.** Один и тот же мужчина незнаком с
Дианой и знаком с Еленой; для первой он кандидат, для второй нет. Это тот же довод,
по которому избранные лежат по анкете (`dating_favorite`).

⚠ **Онлайн, а не «все с сайта».** Реплика в чате застаёт человека или не застаёт:
написать тому, кого сейчас нет, значит положить сообщение в ленту, которую он
откроет через неделю. Приоритетные (`dating_companion.priority`) идут первыми —
порядок ленты для панели случаен, а пул обрывается на потолке.

## ⚠ Шаблонов у инвайта нет, и это не упущение

У письма есть шаблон: текст пишут заранее, а переменные подставляют при отправке.
Инвайт целиком сочиняет агент под конкретного человека — потому что вся его длина
это одно-два предложения, и «общая часть», которую можно было бы заготовить, в них
не остаётся. Заготовь мы рамку («%s, привет! Расскажи про…»), двадцать инвайтов
подряд читались бы как двадцать копий — ровно то, от чего первая реплика и должна
уберечь.

Отсюда следствие: **вызов LLM на каждого человека**, и потолок захода
(`invite_batch`) здесь про время, а не про вежливость.

## ⚠ Имя собеседника обязано быть в тексте

Требование, а не украшение: обращение по имени отличает написанное человеку от
рассылки. Проверяет его разбор ответа агента (`dating_mail_agent._invite`) — реплика
без имени в пул не попадает. Пропущенный человек не теряется: в пуле его нет, и
следующий заход возьмёт его заново.

## ⚠ Язык реплики задаётся явно, и это про доставку

Первый прогон дал все четыре реплики **по-русски** — мужчинам на англоязычном
сайте. Причина была в промпте: правило «пиши на языке его профиля», профиля у этих
людей нет, и модель ушла в язык, которым написан сам промпт.

Фильтр сайта такое отклоняет («Message not delivered. Unacceptable words: …», см.
`dating_answer.DATING_ANSWER_LANGUAGE`), то есть пул был бы пулом недоставляемых
сообщений. Теперь язык берётся из настройки сайта (`presence.answer_language`,
умолчание `en`) и уезжает агенту переменной — той же, что у писем и автоответа.

⚠ **Профиль собеседника решает, о чём спросить, а не на каком языке.** Иначе один
и тот же сайт получал бы реплики на трёх языках — по тому, чем каждый мужчина
заполнил свою анкету.

## Что делает круг, а что человек

Круг **готовит и не отправляет** — как у писем. Отправляет человек кнопкой на
карточке, и только когда у сайта поднят второй выключатель (`invite_send_on`):
реплика уходит живому человеку в чат, и «расписание включено» согласием на это не
является.
"""

import random

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_json import dating_json_load
# ⚠ Перестановка очереди — общая с рассылкой: «выбранного вперёд» должно значить
# одно и то же в обоих пулах, иначе человек, привыкший к одному, обманется вторым.
from src.dating.dating_mailing import dating_mailing_people_first
from src.dating.dating_mail_agent import (DATING_INVITE_LANGUAGE,
                                          DatingMailAgentError,
                                          dating_invite_write)
from src.dating.dating_people import (DATING_PEOPLE_NAME_MAX, DATING_PEOPLE_UID_MAX,
                                      dating_people_name_usable)

# Сколько кандидатов пробуем за заход. ⚠ Каждый — вызов LLM: полсотни это минуты
# ожидания у нажатой кнопки, и потому умолчание скромнее, чем у писем.
DATING_INVITE_BATCH_DEFAULT = 10
DATING_INVITE_BATCH_MAX = 50

# Сколько неотправленных держим в пуле. Круг добирает до этого числа и встаёт:
# инвайты ждут человека, а онлайн меняется — сотня приглашений успеет устареть
# раньше, чем до неё дойдёт очередь.
DATING_INVITE_POOL_DEFAULT = 20

# Настройки сайта. Ключи — в `DATING_SITE_KNOWN`.
DATING_INVITE_BATCH_SETTING = 'invite_batch'
DATING_INVITE_POOL_SETTING = 'invite_pool_max'

# Почему пара пропущена. Строки уезжают на страницу, и человек по ним понимает, что
# делать: «агент промолчал» — подождать, «без имени» — смотреть промпт у соседа.
DATING_INVITE_SKIP_SILENT = 'агент не написал реплику'
DATING_INVITE_SKIP_NAMELESS = 'в реплике нет имени собеседника'
# ⚠ Отдельная причина, и она **дешёвая**: такую пару отбрасывают до агента.
DATING_INVITE_SKIP_ALIEN = 'имя не латиницей — обратиться нечем'


class DatingInviteError(RuntimeError):
    """Набрать пул не вышло. Текст годен для страницы и журнала."""


async def dating_invite_make(site_id: int, limit: int, profile_id: int = 0,
                             first_uid: str = '') -> dict:
    """Набирает пул инвайтов: по реплике на пару «анкета — собеседник».

    Args:
        site_id: сайт-закладка.
        limit: сколько пар пробуем за заход. Каждая — вызов LLM.
        profile_id: звать только от этой анкеты. Ноль — от всех включённых.
            ⚠ Сужение, а не отбор заново: анкета всё равно должна быть с этого
            сайта и включённой, иначе инвайтов не будет вовсе.
        first_uid: кого позвать первым — карточка, выбранная в ленте «Все онлайн».
            ⚠ Перестановка, а не отбор: остальные в очереди остаются. Уже
            позванного пропустит `_invited`, как и всех прочих.

    Returns:
        dict: `made` — готово реплик, `skipped` — пар пропущено, `reasons` — чего
        не хватило (первые несколько), `left` — осталось на следующий заход,
        `stopped` — почему заход оборвался раньше (пусто — дошёл до конца).

    ⚠ **Повторный заход продолжает с того же места.** Пары, у которых инвайт уже
    лежит или ушёл, пропускаются ключом `(profile_id, uid)`; поэтому кнопку можно
    жать, пока пул не наберётся, а круг — будить набор по сменам, не заваливая пул
    повторами.
    """
    girls = await _girls(site_id)
    if profile_id:
        girls = [girl for girl in girls if girl['id'] == int(profile_id)]

    people = dating_mailing_people_first(await _people(site_id), first_uid)
    if not girls or not people:
        return {'made': 0, 'skipped': 0, 'reasons': [], 'left': 0, 'stopped': ''}

    invited = await _invited(site_id)
    talked = await _talked(site_id)
    # ⚠ Язык спрашиваем **один раз на заход**, а не на реплику: он про сайт, и
    # настройка живёт у соседа — запрос на каждого человека был бы запросом к
    # соседу на каждую реплику.
    language = await _language(site_id)

    made, tried, skipped, reasons, left, stopped = 0, 0, 0, [], 0, ''

    for girl, person in _pairs(girls, people, invited, talked):
        if tried >= max(int(limit), 0):
            left += 1
            continue

        # ⚠ **Имя проверяем до агента, а не после.** Реплика без обращения по
        # имени всё равно будет отброшена (`DATING_INVITE_SKIP_NAMELESS`), но
        # узнать об этом ценой вызова LLM — плата ни за что, и платится она
        # каждый заход: человек в сети, значит попадает в ленту снова.
        #
        # Поймано живьём: `許萊納` из ленты Ladadate. Агент пишет по-английски и
        # это имя не вставляет — возвращает пустую реплику. Проверено дважды,
        # исход один и тот же.
        if not dating_people_name_usable(person.get('name')):
            skipped += 1
            if DATING_INVITE_SKIP_ALIEN not in reasons:
                reasons.append(DATING_INVITE_SKIP_ALIEN)
            continue

        tried += 1

        try:
            text = await _body(girl, person, site_id, language)
        except DatingMailAgentError as e:
            # Агент недоступен — это не «реплика не годится», а «сейчас не выйдет
            # вовсе». Останавливаемся: следующие пары упрутся в то же самое.
            logger_info(f'[dating] инвайты остановлены: {e}')
            stopped = str(e)
            break

        if not text:
            skipped += 1
            why = DATING_INVITE_SKIP_NAMELESS if str(person.get('name') or '').strip() \
                else DATING_INVITE_SKIP_SILENT
            if why not in reasons:
                reasons.append(why)
            continue

        await _put(site_id, girl, person, text)
        made += 1
        # ⚠ Помечаем **человека**, а не пару: в этом же заходе он не должен
        # достаться второй девушке. Ключ в базе такого не поймает — там пара.
        invited.add(str(person['uid']))

    if made or skipped:
        logger_info(f'[dating] инвайты сайта #{site_id}: готово {made}'
                    + (f', пропущено {skipped}' if skipped else ''))

    return {'made': made, 'skipped': skipped, 'reasons': reasons, 'left': left,
            'stopped': stopped}


async def dating_invite_pool_left(site_id: int) -> int:
    """Сколько неотправленных инвайтов в пуле сайта.

    По нему круг решает, добирать ли: пул это **запас**, а не поток.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute("SELECT COUNT(*) AS n FROM dating_invite"
                             " WHERE site_id = %s AND status = 'draft'",
                             (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] пул инвайтов сайта #{site_id} не сосчитался: {e}')

        return 0

    return int((row or {}).get('n') or 0)


# ── Приватное ────────────────────────────────────────────────────────────────

def _pairs(girls: list, people: list, invited: set, talked: set):
    """Пары «анкета — собеседник», годные для приглашения.

    ⚠ **Один инвайт на человека, а не на пару.** Здесь правило строже, чем ключ в
    базе (`(profile_id, uid)`), и строже, чем у писем. Поймано живым прогоном: у
    агентства две Дианы (#849 и #850), и первый же заход выдал **два приветствия
    одному и тому же мужчине** — от обеих. Для него это не два знакомства, а
    агентство, которое выдало себя первой же репликой.

    Ключ в базе при этом остаётся прежним: он про то, что допустимо в истории
    (другая анкета того же человека позвать может — например, если первый инвайт
    удалили из пула), а это правило — про то, как набирается пул.

    ⚠ **Анкета выбирается наугад из свободных, а не первая по списку.** Первая
    означала бы, что весь пул уходит от одной девушки: у неё меньший `id`, и она
    выигрывает каждый раз. Круг (первая, вторая, третья, снова первая) тоже плох —
    по журналу сайта такое чередование читается как рассылка. Тот же довод и тот же
    приём, что у писем.

    ⚠ **Свободные — те, кто с ним не говорил.** Выбирай мы наугад из всех, случай
    раз за разом выпадал бы на анкету, у которой с ним уже переписка, и человек
    оставался без инвайта, хотя другая девушка была свободна.

    Args:
        invited: номера тех, кому инвайт уже готовили или отправляли, — **любой**
            анкетой этого сайта.
        talked: пары, у которых переписка уже есть.
    """
    for person in people:
        uid = str(person.get('uid') or '').strip()
        if not uid or uid in invited:
            continue

        free = [girl for girl in girls if (girl['id'], uid) not in talked]
        if not free:
            continue

        yield random.choice(free), person


async def _language(site_id: int) -> str:
    """Язык реплик этого сайта. Не задан — `en`.

    ⚠ **Берётся из `presence.answer_language` у соседа**, той же настройки, что у
    автоответа: язык — свойство сайта, а не занятия, и второе место для него
    разошлось бы с первым. Сосед недоступен — умолчание: инвайт на английском
    хуже идеального, а инвайт, не написанный вовсе, хуже любого.
    """
    from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_site_get

    try:
        site = await rhapsody_api_site_get(int(site_id))
    except RhapsodyError as e:
        logger_info(f'[dating] язык сайта #{site_id} не спросился: {e}')

        return DATING_INVITE_LANGUAGE

    presence = dating_json_load((site or {}).get('presence')) or {}

    return str(presence.get('answer_language') or DATING_INVITE_LANGUAGE)


async def _body(girl: dict, person: dict, site_id: int,
                language: str = DATING_INVITE_LANGUAGE) -> str:
    """Текст реплики: карточка собеседника плюс агент.

    ⚠ **Карточку берём, если она есть, но за ней не ходим.** Поход на анкету
    мужчины стоит ~50 секунд браузера (`dating_companion_about`), а инвайт — это
    первая реплика: она обязана быть быстрой и дешёвой. Нет карточки — агент
    напишет по имени и возрасту из ленты, и это законный исход.
    """
    from src.uvicorn.routers.dating.dating_companion import dating_companion_get

    uid = str(person.get('uid') or '')
    card = await dating_companion_get(int(site_id), uid) or {}

    # ⚠ Лента старше карточки в том, что знает обе: имя и фото она снимает каждые
    # две минуты, а карточка помнит их с первого раза. Поэтому сверху кладём ленту.
    companion = {**card, **{k: v for k, v in (person or {}).items() if v}}

    return await dating_invite_write(girl, int(site_id), companion, language)


async def _girls(site_id: int) -> list[dict]:
    """Анкеты, от которых уходят инвайты.

    ⚠ **Своего флага у инвайтов нет**, и заводить его незачем. У рассылки он есть
    (`mailing_on`), потому что письма шлют не все учётки; инвайт же — начало
    разговора в чате, то есть основное занятие панели. Четвёртую галочку в
    настройках анкеты пришлось бы поднимать всем.

    ⚠ **Зато обязателен онлайн** (`status = 'online'`). Инвайт — реплика в чат
    тому, кто **сейчас** на сайте, и уходит она от девушки: сайт покажет её
    собеседнику, а той в чате нет — ответить ему будет некому. Прежде условие было
    одно (`enabled`), и реплики готовились от спящих анкет; замечено на живом
    пуле. Тот же довод и то же правило, что у писем избранным
    (`dating_favorite_mail`) и у рассылки (`dating_mailing._girls`).
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, site_id, login, password, password_source, profile'
                ' FROM dating_profile'
                ' WHERE site_id = %s AND enabled = 1 AND role = %s'
                "   AND status = 'online'"
                ' ORDER BY id', (int(site_id), 'lady'))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] анкеты сайта #{site_id} для инвайтов '
                    f'не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def _people(site_id: int) -> list[dict]:
    """Кто сейчас в сети — из того же снимка, что показывает лента «Все онлайн».

    ⚠ **Из кэша, а не походом на сайт**, и ключ берём **у ленты**: разойдись они,
    инвайты уходили бы не тем, кого человек видит на экране. Пустой кэш означает
    «в сети никого не знаем» — честный ответ, а не повод лезть на сайт.
    """
    from src.dating.dating_mailing import _by_priority
    from src.uvicorn.routers.dating.dating_people import _cache_get, _key

    cached = _cache_get(_key('online', site_id=site_id))
    people = [row for row in ((cached or {}).get('people') or []) if row.get('uid')]

    return await _by_priority(site_id, people) if people else people


async def _invited(site_id: int) -> set:
    """Номера тех, кому инвайт уже готовили или отправили — **любой** анкетой.

    ⚠ **По человеку, а не по паре.** Приветствие он получает один раз: два от
    разных наших девушек читаются как агентство, а не как два знакомства
    (подробности — в `_pairs`).

    ⚠ **Считаем и ушедшие, и лежащие в пуле.** Ушедший инвайт — состоявшееся
    приглашение, и второе было бы навязчивостью; лежащий в пуле ждёт человека, и
    второй просто задвоил бы карточку.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT DISTINCT uid FROM dating_invite'
                             ' WHERE site_id = %s', (int(site_id),))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] готовые инвайты сайта #{site_id} не спросились: {e}')

        # ⚠ Сбой решается **в сторону бездействия**, как и у переписки: не зная,
        # кого уже позвали, мы рискуем позвать второй раз, а пустой набор означал
        # бы «никого не звали» — худшую из догадок.
        raise DatingInviteError(f'не спросить, кого уже звали: {e}') from e

    return {str(row['uid']) for row in rows or []}


async def _talked(site_id: int) -> set:
    """Пары, у которых переписка уже есть.

    ⚠ **По `dating_message`, а не по `dating_contact`.** Первое — сами реплики,
    второе — список собеседниц, каким его отдал сайт: он приходит не целиком и
    бывает пуст у анкеты, у которой переписка есть. Спрашивать «говорили ли» надо у
    того, что говорили.

    ⚠ **Одним запросом на сайт, а не по анкете.** Анкет тринадцать, и запрос на
    каждую превратил бы набор пула в тринадцать походов в базу до первой реплики.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT DISTINCT m.profile_id, m.sender_uid AS uid'
                '  FROM dating_message m'
                '  JOIN dating_profile p ON p.id = m.profile_id'
                ' WHERE p.site_id = %s AND m.sender_uid <> %s',
                (int(site_id), ''))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] переписки сайта #{site_id} не спросились: {e}')

        # ⚠ Сбой решается **в сторону бездействия**: не зная, с кем говорили, мы
        # рискуем написать «здравствуйте» тому, с кем переписываемся неделю. Пустой
        # набор означал бы «ни с кем не говорили», то есть худшую из догадок.
        raise DatingInviteError(f'не спросить, с кем уже говорили: {e}') from e

    return {(int(row['profile_id']), str(row['uid'])) for row in rows or []}


async def _put(site_id: int, girl: dict, person: dict, text: str) -> None:
    """Кладёт инвайт в пул. `INSERT IGNORE` ловит гонку двух заходов разом."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO dating_invite'
            ' (site_id, profile_id, uid, name, photo, body)'
            ' VALUES (%s, %s, %s, %s, %s, %s)',
            (int(site_id), int(girl['id']),
             str(person.get('uid') or '')[:DATING_PEOPLE_UID_MAX],
             str(person.get('name') or '')[:DATING_PEOPLE_NAME_MAX],
             str(person.get('photo') or '')[:500],
             str(text or '')))
        await db.connection.commit()
