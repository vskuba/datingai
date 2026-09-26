"""Ответ на письмо **через админ-панель агентства**, а не через API сайта.

Обычный путь ответа — ручка сайта под самой девушкой (`mail_send.py`): два шага,
`check-send` за разрешением и `send` с телом. Здесь второй путь, для избранных: тот
же ответ уходит формой панели агентства, под сессией, которую круг панели держит
живой круглые сутки (`panel.py`).

## ⚠⚠ Зачем второй путь, если первый работает

Панель **не считает наши ответы ответами**. Снято с прода 26.09.2026: два письма от
одного мужчины, на оба ответ ушёл ручкой API 25.09 в 21:20 и 21:37, — а в панели у
них по-прежнему `Answered = No` и форма ответа открыта. То есть агентство видит
письма неотвеченными, чем бы мы ни отвечали.

⚠ Причина не доказана, и рядом лежит вторая находка того же дня: номер письма, на
которое отвечаем, уезжал в `ReplyForMessageId` **только с кнопки человека** — запрос
круга отправки поля не выбирал вовсе, и ответы круга шли отдельными письмами. Это
починено, но путь через панель всё равно надёжнее по существу: панель — то самое
место, где агентство считает письмо отвеченным.

## Как устроен разговор с панелью

```
GET  /partner/agency/usermessage/{MessageId}     ← форма ответа этого письма
     └─ нет `buttonReply` → отвечать нельзя: уже отвечено или срок вышел
POST /partner/agency/usermessage                 ← multipart: MessageId, ToBody, кнопка
GET  /partner/agency/usermessage/{MessageId}     ← форма закрылась? значит ушло
```

⚠ **Списка писем в этой цепочке нет.** Человек ходит через
`/partner/agency/usermessages` (выбрать сутки, найти строку, нажать «Reply»), но
номер письма у нас уже есть: `mail_in.message_id` — **тот самый** номер, что у
панели, сверено на живых данных. Листать список ради того, что известно, значит
платить запросом за чужую разметку и зависеть от её порядка.

## ⚠⚠⚠ Самое опасное место: вторая кнопка в той же форме

Форма панели несёт **два** сабмита:

    <button name="buttonReply"  value="Reply">Reply</button>
    <input  name="buttonIgnore" value="Ignore Message">

Промах в имени кнопки — это не «не отправилось», а **«письмо проигнорировано»**:
панель закроет его без ответа, и вернуть это нельзя. Поэтому имя кнопки здесь —
константа, и на то, что `buttonIgnore` не уходит никогда, стоит отдельный сторож.

## ⚠ Чего этот модуль не решает

**Кому отвечать панелью.** Условие — «пара есть в избранных» плюс настройка сайта, и
живёт оно в `mail_reply_panel_due`, но правило самой пары берётся из общего
`mail_reply_delivery_scope`: им же разделены журналы доставки, и вторая копия
означала бы, что ответ уходит не тем путём, каким показан.

**Куда лечь исходу.** Исход пишет общий `mail_send_mark` — тот же, что у пути через
API: пул один, и две записи исхода разошлись бы в словах отказа.
"""

from logging_.logging_ import logger_info

from src.dating.panel_fetch import (DatingPanelFetchError, panel_fetch_html,
                                    panel_fetch_post)

# Страница письма в панели. ⚠ Путь **относительный корню панели**
# (`/partner/agency`), как и у прочих её страниц: корень собирает `panel_fetch`.
MAIL_REPLY_PANEL_PATH = 'usermessage'

# Поля формы ответа — имена сняты с живой страницы.
MAIL_REPLY_PANEL_ID_FIELD = 'MessageId'
MAIL_REPLY_PANEL_BODY_FIELD = 'ToBody'

# ⚠⚠⚠ Кнопка. В форме их **две**, и вторая — `buttonIgnore` со значением «Ignore
# Message»: ею панель закрывает письмо **без ответа**, необратимо. Имя и значение
# лежат здесь одной парой, чтобы промахнуться было нечем.
MAIL_REPLY_PANEL_BUTTON = 'buttonReply'
MAIL_REPLY_PANEL_BUTTON_VALUE = 'Reply'

# Настройка сайта: чем отправлять ответы избранным.
MAIL_REPLY_PANEL_SOURCE_SETTING = 'reply_send_source'
MAIL_REPLY_PANEL_SOURCE_PANEL = 'panel'
MAIL_REPLY_PANEL_SOURCE_API = 'api'

# Настройка сайта: что делать, когда панель не может.
MAIL_REPLY_PANEL_FALLBACK_SETTING = 'reply_panel_fallback'


class DatingPanelReplyError(RuntimeError):
    """Ответ не ушёл панелью. Текст годен для страницы и для столбца `error`."""


async def mail_reply_panel_due(site_id: int, profile_id: int, uid: str) -> bool:
    """Отвечать ли этому человеку **через панель**.

    Args:
        site_id: сайт-закладка.
        profile_id: наша анкета — отправительница ответа.
        uid: собеседник на сайте.

    Returns:
        bool: `True` — идём панелью, `False` — обычной ручкой API.

    ## ⚠⚠ Два условия, и оба обязательны

    Настройка сайта `reply_send_source` должна стоять в `panel`, **и** пара
    «анкета — собеседник» должна быть в избранных. Панельный путь заведён под
    переписку с избранными: у неё ответ — продолжение разговора, который агентство
    считает по своим письмам.

    ⚠ Условие пары спрашивается **у общего правила** (`favorite` без оглядки на
    `state`, как в `mail_reply_delivery_scope`): им разделены журналы доставки, и
    вторая копия означала бы, что ответ уходит не тем путём, каким показан.

    ⚠ **Сомнение — в сторону API.** Не спросилась настройка, не спросилась база —
    отвечаем прежним путём: он рабочий, а молчание вместо ответа хуже, чем ответ не
    тем каналом.
    """
    from src.uvicorn.routers.dating.site_ import site_setting

    try:
        picked = str(await site_setting(int(site_id),
                                        MAIL_REPLY_PANEL_SOURCE_SETTING)).strip()
    except Exception as e:
        logger_info(f'[dating] чем отвечать на сайте #{site_id} — не спросилось: {e}')

        return False

    if picked != MAIL_REPLY_PANEL_SOURCE_PANEL:
        return False

    return await _starred(int(site_id), int(profile_id), str(uid))


async def mail_reply_panel_fallback(site_id: int) -> bool:
    """Отправлять ли через API, когда панель не смогла.

    ⚠ Настройкой, а не решением кода: случаи разные по смыслу. «Форма закрыта»
    значит, что панель считает письмо отвеченным, и досылать его ручкой — второе
    письмо человеку; «сессия протухла» значит, что путь временно закрыт, и досылка
    ручкой как раз спасает ответ. Выбор между этим — человеческий.

    ⚠ Умолчание — досылать: так ведёт себя прежний код, и включение панели не должно
    само по себе останавливать ответы.
    """
    from src.uvicorn.routers.dating.site_ import site_setting

    try:
        picked = str(await site_setting(int(site_id),
                                        MAIL_REPLY_PANEL_FALLBACK_SETTING)).strip()
    except Exception as e:
        logger_info(f'[dating] откат отправки на сайте #{site_id} — не спросился: {e}')

        return True

    return picked != '0'


async def mail_reply_panel_open(site_id: int, message_id: int) -> str:
    """Страница письма в панели, если ответить на него ещё можно.

    Args:
        site_id: сайт-закладка.
        message_id: номер письма в панели — он же `mail_in.message_id`.

    Returns:
        str: разметка страницы ответа.

    Raises:
        DatingPanelReplyError: номера нет, панель недоступна, форма ответа закрыта.

    ## ⚠⚠ Зачем читать страницу перед отправкой

    Форма ответа **пропадает**, когда панель считает письмо отвеченным. Проверено на
    живых номерах: у писем от 25.09 кнопка «Reply» есть, у писем от 23.09 и 09.09 —
    ни кнопки, ни поля. Отправь мы форму слепо, панель ответила бы `200` со страницей
    без формы, и мы записали бы «ушло».

    ⚠ Здесь же сверяется, что на странице стоит **наш** номер: путь принимает любое
    число, и опечатка увела бы ответ в чужую переписку.
    """
    if not int(message_id or 0):
        raise DatingPanelReplyError('у письма нет номера в панели '
                                    '(входящее пришло не из письма)')

    try:
        page = await panel_fetch_html(
            int(site_id), f'{MAIL_REPLY_PANEL_PATH}/{int(message_id)}')
    except DatingPanelFetchError as e:
        raise DatingPanelReplyError(f'панель не открыла письмо: {e}')

    if not mail_reply_panel_can(page, int(message_id)):
        raise DatingPanelReplyError(
            'в панели нет формы ответа на это письмо: она считает его отвеченным '
            'или срок ответа вышел')

    return page


def mail_reply_panel_can(page: str, message_id: int) -> bool:
    """Открыта ли на этой странице форма ответа **на это** письмо.

    ⚠ Проверок две, и вторая не лишняя: кнопка говорит «ответить можно», скрытый
    номер — «ответ уйдёт на то письмо, которое мы открывали». Панель кладёт номер в
    форму сама, и расхождение значило бы, что мы читаем не ту страницу.
    """
    text = str(page or '')

    if f'name="{MAIL_REPLY_PANEL_BUTTON}"' not in text:
        return False

    if f'name="{MAIL_REPLY_PANEL_BODY_FIELD}"' not in text:
        return False

    return f'value="{int(message_id)}"' in text


async def mail_reply_panel_send(site_id: int, message_id: int, body: str) -> dict:
    """Отправляет ответ формой панели. Ничего не пишет в базу — это дело зовущего.

    Args:
        site_id: сайт-закладка.
        message_id: номер письма в панели, на которое отвечаем.
        body: текст ответа.

    Returns:
        dict: `{'sent': True, 'answer': <что сказала панель>}`.

    Raises:
        DatingPanelReplyError: форма закрыта, панель недоступна, ответ не подтвердился.

    ## ⚠⚠ Удачу решает перечитывание, а не код ответа

    Панель отвечает `200` и на удачу, и на отказ, а на удачную форму умеет ответить
    `302` на список. Ни то, ни другое не годится в признак. Поэтому после отправки
    страница письма читается снова: **закрылась форма — ответ ушёл**.

    ⚠ Ровно этим ошиблись у инвайтов: «сайт принял кадр» приняли за «реплика дошла»,
    и панель числила ушедшим то, чего у собеседника не было.

    ⚠ Перечитывание идёт **строгим GET**: протухни сессия между отправкой и
    проверкой, он скажет «панель увела на вход» — правильными словами, а не
    «панель по-прежнему ждёт ответа».
    """
    text = str(body or '').strip()
    if not text:
        raise DatingPanelReplyError('ответ пустой — отправлять нечего')

    await mail_reply_panel_open(int(site_id), int(message_id))

    # ⚠⚠ Кнопка — **`buttonReply`**, и только она. Вторая кнопка той же формы
    # (`buttonIgnore`) закрывает письмо без ответа, необратимо.
    form = {MAIL_REPLY_PANEL_ID_FIELD: str(int(message_id)),
            MAIL_REPLY_PANEL_BODY_FIELD: text,
            MAIL_REPLY_PANEL_BUTTON: MAIL_REPLY_PANEL_BUTTON_VALUE}

    try:
        said = await panel_fetch_post(int(site_id), MAIL_REPLY_PANEL_PATH, form)
    except DatingPanelFetchError as e:
        raise DatingPanelReplyError(f'панель не приняла форму: {e}')

    # ── Подтверждение: форма закрылась? ─────────────────────────────────────
    try:
        again = await panel_fetch_html(
            int(site_id), f'{MAIL_REPLY_PANEL_PATH}/{int(message_id)}')
    except DatingPanelFetchError as e:
        raise DatingPanelReplyError(
            f'ответ отправлен, но подтвердить не вышло: {e}')

    if mail_reply_panel_can(again, int(message_id)):
        raise DatingPanelReplyError(
            'панель по-прежнему ждёт ответа на это письмо — форма не закрылась')

    logger_info(f'[dating] ответ ушёл панелью: сайт #{site_id}, '
                f'письмо {message_id}')

    return {'sent': True, 'answer': _said(said, again)}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _starred(site_id: int, profile_id: int, uid: str) -> bool:
    """Есть ли пара в избранных. Сомнение — «нет»: путь по умолчанию прежний.

    ⚠ Без оглядки на `favorite.state`, как и везде: звезда со `state = 'fail'` — это
    «нажать не вышло», а не «человек не избранный».
    """
    from mysql_.mysql_ import mysql_get_db_async

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT 1 FROM favorite'
                ' WHERE site_id = %s AND profile_id = %s AND uid = %s LIMIT 1',
                (int(site_id), int(profile_id), str(uid)))

            return bool(await db.fetchone())
    except Exception as e:
        logger_info(f'[dating] избранные сайта #{site_id} не спросились: {e}')

        return False


def _said(sent_page: str, after_page: str) -> str:
    """Что сказала панель — короткой строкой для столбца `answer`.

    ⚠ Не вся разметка: страница панели это 12 килобайт, а столбец — 500 знаков, и
    обрезанная разметка в журнале не говорит ничего. Пишем то, что человек прочитает:
    подтверждение и заголовок письма, если он нашёлся.
    """
    import re

    head = re.search(r'Message to\s*([^<]{0,80})', str(after_page or ''))
    where = f'; {head.group(1).strip()}' if head else ''

    return f'панель приняла форму ({len(str(sent_page or ""))} знаков){where}'[:500]
