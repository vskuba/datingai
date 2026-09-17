"""Отправка письма из пула — **через API сайта**, без браузера и без соседа.

У сайта есть свои ручки для писем (`https://ladadate.com/api.html`), и их две:

| Шаг | Ручка | Что отдаёт |
|-----|-------|-----------|
| спросить разрешение | `GET /messages/check-send/{accountId}` | `CheckId` |
| отправить | `POST /messages/send` | `Status: OK` |

## ⚠ Два шага, и первый пропустить нельзя

`CheckId` — не формальность: сайт им отвечает «этому человеку писать можно», и
живёт этот ответ **десять секунд**. Без него отправка не принимается вовсе.

Отсюда правило: между проверкой и отправкой **не делаем ничего** — ни походов в
базу, ни разбора, ни записи в журнал. Всё, что можно приготовить, готовится до
проверки; всё, что можно записать, записывается после отправки. Вклинься между ними
запрос к MySQL на плохой его секунде — и `CheckId` протухнет в руках.

## Кто здесь ходит

Дверь к сайту одна — `dating_site_api.py`, и токен у неё общий со списками
собеседников: письмо уходит **под той же анкетой**, под которой снимают ленту. Заведи
отправка свой вход, на анкету приходилось бы два захода вместо одного, а частые
входы сайт считает подозрением.

## ⚠ Неудача **не сжигает письмо**

Не ушло — строка остаётся `draft`, а причина ложится в `error`. Так письмо можно
отправить ещё раз: причина обычно временная (сайт просит подождать, протух пароль),
а `failed` его бы похоронил — уникальный ключ `uq_dating_mailing_once` считает и
неудавшиеся, и повторно такое письмо уже не сгенерировать.

Ушло — `sent` и время. Это факт, и он окончателен.

## Чего здесь нет

**Круга отправки.** Письма уходят живым людям, и решение «шлём» принимает человек
кнопкой на карточке. Выключатель «отправка писем» у пула пока ничего не исполняет:
он про будущий круг, и о том, что круга нет, сказано прямо и в подсказке, и в
справке.

**Вложений.** Ручка сайта их умеет (`AttachmentName`, `AttachmentHash`,
`AttachmentFile`), но письма панели — текст, и посылать в этих полях пустоту честнее,
чем заводить то, чем никто не пользуется.
"""

import json

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_log import dating_log_write
from src.dating.dating_ready import dating_password_pick
from src.dating.dating_request import DATING_REQUEST_SEND
from src.dating.dating_site_api import DatingSiteApiError, dating_site_api_call

# Что зовём. Пути из документации сайта.
DATING_MAIL_SEND_CHECK_PATH = '/messages/check-send/{uid}'
DATING_MAIL_SEND_PATH = '/messages/send'

# Поля ответа сайта.
DATING_MAIL_SEND_CHECK_FIELD = 'CheckId'
DATING_MAIL_SEND_STATUS_FIELD = 'Status'
DATING_MAIL_SEND_STATUS_OK = 'OK'

# Сколько знаков причины храним: столько же, сколько принимает столбец `error`.
DATING_MAIL_SEND_ERROR_MAX = 500

# Куда можно писать исход. ⚠ **Белый список, а не доверие вызывающему**: имя таблицы
# в SQL не параметризуется, и строка «откуда придётся» была бы дырой.
DATING_MAIL_SEND_TABLES = ('mailing', 'mail_reply',
                           'favorite_mail')


class DatingMailSendError(RuntimeError):
    """Письмо не ушло. Текст годен для страницы и для столбца `error`."""


async def dating_mail_send_one(letter: dict, profile: dict, base: str,
                               password: str,
                               table: str = 'mailing') -> dict:
    """Отправляет одно письмо из пула. Пишет исход в его строку.

    Args:
        letter: строка пула: нужны `id`, `uid`, `body`, `status`. У ответа — ещё
            `reply_to`: номер письма на сайте, на которое отвечаем.
        profile: анкета-отправитель (`dating_profile`): `id`, `login`, `password`,
            `password_source`.
        base: корень API сайта из настройки `api_base`.
        password: общий пароль анкет сайта (настройка `api_password`). Свой пароль
            анкеты сильнее — решает `password_source`.
        table: куда писать исход — `dating_mailing` (рассылка) или
            `dating_mail_reply` (ответы).

            ⚠ **Пулов три, а отправка одна.** Разговор с сайтом у них одинаков до
            последнего поля, и вторая копия разошлась бы с первой ровно там, где это
            дороже всего, — в `CheckId` и его десяти секундах. Различаются они лишь
            тем, куда лечь исходу, и `ReplyForMessageId` в теле.

    Returns:
        dict: `{'sent': True}` — ушло. Иначе `DatingMailSendError`.

    Raises:
        DatingMailSendError: письмо уже ушло, писать нечем, сайт отказал.

    ⚠ **Проверки — до похода на сайт.** Пустое тело и уже отправленное письмо видно
    здесь и сразу; узнавать о них от чужого сервера значило бы платить запросом за
    то, что и так известно.
    """
    letter_id = int(letter.get('id') or 0)
    uid = str(letter.get('uid') or '').strip()
    body = str(letter.get('body') or '').strip()
    # ⚠ Номер письма, на которое отвечаем, — **у ответов**; у писем рассылки его нет,
    # и поле в строке отсутствует вовсе. Одна функция шлёт и то и другое: разговор с
    # сайтом у них одинаков до последнего поля, и вторая копия разошлась бы с первой
    # ровно там, где это дороже всего, — в `CheckId` и его десяти секундах.
    reply_to = letter.get('reply_to')

    if str(letter.get('status') or '') != 'draft':
        raise DatingMailSendError('письмо уже ушло — второй раз не отправляем')

    if not uid:
        raise DatingMailSendError('у письма нет адресата')

    if not body:
        raise DatingMailSendError('письмо пустое — отправлять нечего')

    if not str(base or '').strip():
        raise DatingMailSendError('у сайта не задан адрес API '
                                  '(настройка сайта «Адрес API сайта»)')

    # ⚠ Пароль выбираем **по настройке анкеты**, а не «свой, если задан»: у анкеты
    # есть явный `password_source`, и обойти его здесь значило бы отправить письмо
    # под паролем, которого человек для неё не выбирал.
    secret = dating_password_pick(profile, password)

    try:
        # ── Шаг 1: можно ли писать этому человеку ───────────────────────────
        check = await dating_site_api_call(
            profile, base, secret,
            DATING_MAIL_SEND_CHECK_PATH.format(uid=uid), method='GET',
            door=DATING_REQUEST_SEND)
    except DatingSiteApiError as e:
        # ⚠ Отказ приходит **и на этом шаге**: «можно ли писать этому человеку» —
        # ровно тот вопрос, на который сайт отвечает «он вас закрыл».
        await _deny_check(profile, letter, table, uid, str(e))

        raise DatingMailSendError(f'сайт не дал разрешения на отправку: {e}')

    check_id = str(check.get(DATING_MAIL_SEND_CHECK_FIELD) or '').strip()
    if not check_id:
        raise DatingMailSendError('сайт не вернул разрешения на отправку '
                                  f'({DATING_MAIL_SEND_CHECK_FIELD} пуст)')

    # ── Шаг 2: сама отправка ────────────────────────────────────────────────
    #
    # ⚠ Сразу за проверкой, без единого шага между: `CheckId` живёт десять секунд.
    try:
        answer = await dating_site_api_call(
            profile, base, secret, DATING_MAIL_SEND_PATH,
            payload=_payload(check_id, uid, body, reply_to), method='POST',
            door=DATING_REQUEST_SEND)
    except DatingSiteApiError as e:
        await _deny_check(profile, letter, table, uid, str(e))

        raise DatingMailSendError(f'сайт не принял письмо: {e}')

    # ⚠ Ответ сайта храним **как есть**, и у удачи тоже: «доставлено» — это наш
    # вывод из его слов, а не его слова. У инвайтов этот же разрыв уже стоил
    # времени: панель числила реплики ушедшими, а в чате у собеседника было пусто.
    said = json.dumps(answer, ensure_ascii=False)[:DATING_MAIL_SEND_ERROR_MAX]

    status = str(answer.get(DATING_MAIL_SEND_STATUS_FIELD) or '').strip()
    if status.upper() != DATING_MAIL_SEND_STATUS_OK:
        # ⚠ Код 200 с чужим `Status` — тоже неудача. Сайт вправе отвечать «принял
        # запрос, но не отправил», и считать это успехом значило бы объявить
        # ушедшим письмо, которого адресат не увидит.
        await dating_mail_send_mark(letter, sent=False, table=table, answer=said,
                                    error='')

        raise DatingMailSendError(
            f'сайт ответил не «{DATING_MAIL_SEND_STATUS_OK}»: {status or "пусто"}')

    await dating_mail_send_mark(letter, sent=True, table=table, answer=said)
    logger_info(f'[dating] письмо #{letter_id} ушло: анкета #{profile.get("id")} → {uid}')

    return {'sent': True}


async def dating_mail_send_mark(letter: dict, sent: bool, error: str = '',
                                table: str = 'mailing',
                                final: bool = False, answer: str = '') -> None:
    """Записывает исход отправки: в строку письма и в журнал анкеты.

    Args:
        letter: строка письма целиком — нужны `id`, `profile_id`, `uid`, `name`.
        sent: ушло или нет.
        error: причина, если не ушло.
        table: куда писать — `dating_mailing`, `dating_mail_reply` или
            `dating_favorite_mail`.

    ⚠ **Оба места — здесь, а не по разным.** Исход отправки записывается дважды:
    в само письмо (его читает страница) и в журнал анкеты (его читают, разбираясь,
    что вообще происходило). Разведи мы их — одна половина однажды осталась бы без
    другой, и «Доставка» показывала бы то, чего в журнале нет.

    ⚠ **Письмо принимаем целиком, а не одним номером.** Журналу нужны анкета и
    адресат, и добирать их вторым запросом значило бы платить походом в базу за то,
    что у вызывающего уже в руках.

    ⚠ **Неудача оставляет письмо в пуле** (`draft`), а причину кладёт в `error`.
    `failed` его бы похоронил: уникальный ключ `uq_dating_mailing_once` считает и
    неудавшиеся, и второй раз такое письмо уже не сгенерировать. Причина же обычно
    временная — сайт просит подождать, протух пароль, — и повтор после неё законен.

    ⚠ **`final` — ровно тот случай, когда хоронить правильно.** Сайт ответил «он
    вас закрыл»: второго письма этой паре не будет никогда, и ключ, который его не
    даст сгенерировать, здесь не помеха, а то же самое решение.

    ⚠ **И только так попытку видно в «Доставке».** Запрет выметает черновики пары
    из пулов (`dating_block.py`); останься письмо `draft`, его снесло бы следом — и
    отказ исчез бы бесследно, как будто отправки не было.
    """
    letter_id = int(letter.get('id') or 0)
    addressee = str(letter.get('name') or '').strip() or str(letter.get('uid') or '')

    # ⚠ Имя таблицы подставляем **из белого списка**, а не из строки вызывающего:
    # в SQL оно не параметризуется, и любое другое было бы дырой. Здесь их ровно
    # три, и все наши.
    if str(table) not in DATING_MAIL_SEND_TABLES:
        raise DatingMailSendError(f'неизвестный пул писем: {table}')

    # ⚠ Ответ сайта пишем, только когда он есть: пустая строка затёрла бы то, что
    # записала сама отправка, — исход отмечают дважды (здесь и у вызывающего).
    said = str(answer or '')[:DATING_MAIL_SEND_ERROR_MAX]
    tail = ', answer = %s' if said else ''

    if sent:
        sql = (f"UPDATE {table} SET status = 'sent', sent_at = NOW(), error = ''"
               f'{tail} WHERE id = %s')
        args = ((said, letter_id) if said else (letter_id,))
    elif final:
        sql = f"UPDATE {table} SET status = 'failed', error = %s{tail} WHERE id = %s"
        args = ((str(error or '')[:DATING_MAIL_SEND_ERROR_MAX], said, letter_id)
                if said else (str(error or '')[:DATING_MAIL_SEND_ERROR_MAX], letter_id))
    else:
        sql = f'UPDATE {table} SET error = %s{tail} WHERE id = %s'
        args = ((str(error or '')[:DATING_MAIL_SEND_ERROR_MAX], said, letter_id)
                if said else (str(error or '')[:DATING_MAIL_SEND_ERROR_MAX], letter_id))

    async with mysql_get_db_async() as db:
        await db.execute(sql, args)
        await db.connection.commit()

    # ⚠ Журнал — **после** записи в письмо, а не до: строка «письмо ушло» при
    # неудавшемся `UPDATE` была бы ложью, а обратный порядок такой лжи не даёт.
    # Сам `dating_log_write` не бросает: сбой журнала не должен ронять отправку,
    # которая уже случилась.
    await dating_log_write(
        int(letter.get('profile_id') or 0),
        'ok' if sent else 'fail',
        f'письмо ушло: {addressee}' if sent
        else f'письмо не ушло: {addressee} — {str(error or "причина неизвестна")}')


# ── Приватное ────────────────────────────────────────────────────────────────

async def _deny_check(profile: dict, letter: dict, table: str, uid: str,
                      said: str) -> None:
    """Не значит ли отказ сайта «он закрылся от этой девушки».

    ⚠ **Внутри отправки, а не у пяти вызывающих.** Письмо шлют кнопка рассылки,
    кнопка ответа, кнопка избранных и два круга — и каждый читает отказ по-своему.
    Расставь проверку по ним, первый забытый продолжал бы писать закрывшемуся.

    ⚠ **Разбор — общий с чатом** (`dating_block_deny`): закрылся человек от
    девушки, а не от способа доставки, и два перечня примет разошлись бы.

    Молчит при любой беде: это подсказка поверх отправки, и уронить ею письмо,
    которое сайт отказался принять по другой причине, было бы хуже отказа.
    """
    from src.dating.dating_block import dating_block_add, dating_block_deny

    site_id = int(profile.get('site_id') or 0)
    profile_id = int(profile.get('id') or 0)
    if not site_id or not profile_id:
        return

    try:
        if not await dating_block_deny(site_id, said):
            return

        # ⚠ Сперва хороним это письмо, потом заносим запрет: тот выметает из пулов
        # **черновики**, и `draft` снесло бы вместе с прочими — отказ пропал бы из
        # «Доставки», хотя ради него всё и затевалось.
        await dating_mail_send_mark(letter, sent=False, error=said, table=table,
                                    final=True)
        await dating_block_add(site_id, profile_id, uid,
                               str(letter.get('name') or ''), reason=said,
                               source='mail')
    except Exception as e:
        logger_info(f'[dating] отказ письма не разобрался: {e}')


def _payload(check_id: str, uid: str, body: str, reply_to=None) -> dict:
    """Тело запроса `POST /messages/send` — ровно по документации сайта.

    Args:
        reply_to: номер письма на сайте, на которое отвечаем. `None` — письмо
            первое, предыстории нет.

    ⚠ **Поля вложения шлём пустыми, а не опускаем.** Документация перечисляет их в
    теле, и сайт вправе требовать их наличия; пустое поле означает «вложения нет», а
    отсутствующее — «мы не знаем такого поля». Это разные утверждения, и второе
    неверно.
    """
    return {'CheckId': check_id,
            'RecipientAccountId': str(uid),
            'Body': str(body),
            # ⚠ У письма рассылки предыстории нет по определению — там `None`. У
            # ответа же он обязателен: без него сайт покажет ответ отдельным письмом,
            # и собеседник не поймёт, на что ему отвечают.
            'ReplyForMessageId': int(reply_to) if reply_to else None,
            'AttachmentName': None,
            'AttachmentHash': '',
            'AttachmentFile': None}
