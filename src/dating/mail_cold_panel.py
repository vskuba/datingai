"""Холодное письмо **через админ-панель агентства**, а не через API сайта.

Дорога ответа (`mail_reply_panel.py`) умеет только **отвечать**: форма панели
открывается номером входящего письма, а у рассылки входящего нет — письмо уходит
незнакомому человеку первым. Для сайтов **без API-ручки** (Step2Love: ни ручки, ни
сокета) это означало тупик: отправка спотыкалась о проверку `api_base` и писала в
письмо «у сайта не задан адрес API», хотя никакая настройка тут не помогла бы —
ручки у сайта нет вовсе и быть не может. Найдено 08.11.2026: за месяц рассылки на
таком сайте не ушло **ни одного** письма, 78 черновиков стояли с этой причиной.

## Как устроена отправка

Панель ведает переписку страницей `mailbox.php` — на ней оператор агентства руками
пишет письма мужчинам. Сочинение письма — три шага, снятые с её же JavaScript
(`mailbox.js`, версии `4.00.183`):

```
POST /mailbox.php?com=draftcreate    ← завести черновик: to_id, reply_id=0, from_id
POST /mailbox.php?com=fileupload     ← кадр письма: id=draftID, type=1, файл `upload`
POST /mailbox.php?com=messagesend    ← отправить: id, to_id, type=1, subj, text
```

Каждый шаг отвечает **JSON-ом**: `{result: 'ok', id: N}` на удачу и
`{result: 'error', text: '…'}` на отказ. Код `200` бывает и там и там, поэтому
удачу решает `result`, а не транспорт.

## ⚠⚠ Отправитель — анкета, а не агентство

`from_id` в `draftcreate` — **номер девушки на сайте**: письмо мужчина увидит от неё,
а не от «агентства». У ответа этот номер панель берёт из письма, на которое
отвечают; у холодного письма взять его неоткуда, поэтому передаём сами —
`profile.profile_id` (тот же номер, что в `login` анкет Step2Love).

## ⚠ Порядок шагов — часть контракта

`fileupload` крепится к **черновику** (`id`), значит черновик обязан существовать
до кадра; `messagesend` уносит черновик в отправленные, значит после него кадр не
приложить. Перепутать шаги — значит отправить письмо без снимка, а владелец
(08.11.2026) требует снимок **каждому** письму рассылки. Поэтому порядок здесь
линейный, а на нём — сторож.

## ⚠ Чего этот модуль не решает

**Кому слать панелью.** Условие — «у сайта нет API-ручки, и драйвер сайта умеет
сочинять письмо» — живёт в `mail_cold_panel_want`, а развилка — в `mail_send.py`,
одной на круг и кнопку.

**Куда лечь исходу.** Пишет общий `mail_send_mark` — тот же, что у ручки API.
"""

import json

from logging_.logging_ import logger_info

from mysql_.mysql_ import mysql_get_db_async

from src.dating.panel_fetch import (DatingPanelFetchError, panel_fetch_post)

# Драйверы сайтов, чья панель умеет **сочинять** письмо. Не «любая панель»:
# панель Ladadate знает только форму ответа (`usermessage`), сочинения холодного
# письма у неё нет — туда letters ходят ручкой API, и не надо их совать сюда.
MAIL_COLD_PANEL_DRIVERS = ('step2love',)

# Пулы холодных писем: у всех адресат не писал нам первым. `mail_reply` —
# исключение: у него входящее письмо есть, и ему дорога `mail_reply_panel.py`.
MAIL_COLD_PANEL_TABLES = ('mailing', 'favorite_mail', 'intention_mail')

# Страница сочинения и её команды — имена сняты с `mailbox.js` живые. Команда
# едет **в строке запроса** (`?com=`), поля — телом: так делает сама страница.
MAIL_COLD_PANEL_PATH = 'mailbox.php'
MAIL_COLD_PANEL_DRAFT = f'{MAIL_COLD_PANEL_PATH}?com=draftcreate'
MAIL_COLD_PANEL_UPLOAD = f'{MAIL_COLD_PANEL_PATH}?com=fileupload'
MAIL_COLD_PANEL_SEND = f'{MAIL_COLD_PANEL_PATH}?com=messagesend'

# Тип письма: 1 — «Letter», 3 — «Video message». Нам нужен обычный текст с кадром.
MAIL_COLD_PANEL_TYPE = '1'

# Тема. Панель держит поле `subj`, но холодное письмо не о чём, кроме текста: оператор
# набирает тему руками по вкусу, а у нас её брать не откуда — в пуле темы нет.
# Пустая тема — то же, что у писем сайтов с ручкой: там её рождает сайт.
MAIL_COLD_PANEL_SUBJ = ''


class DatingPanelColdError(RuntimeError):
    """Письмо не ушло панелью. Текст годен для страницы и для столбца `error`."""


async def mail_cold_panel_want(site_id: int, table: str) -> bool:
    """Слать ли это письмо через панель.

    Args:
        site_id: сайт-закладка.
        table: пул — `mailing`, `favorite_mail`, `intention_mail`.

    Returns:
        bool: `True` — панель сочиняет такие письма, `False` — решать другой дорогой.

    ⚠ **Три условия, и все молча решают «нет» при любой беде:** пул из холодных,
    драйвер сайта умеет сочинение, настройки прочитались. Сомнение — за прежнюю
    дорогу: рассылка не должна встать из-за того, что мы не разобрались.

    ⚠ Про **отсутствие API** здесь ничего нет — этим делит вызывающий
    (`mail_send.py`): у сайта с рабочей ручкой панель не спрашивается никогда,
    как бы хорош ни был её драйвер.
    """
    if str(table) not in MAIL_COLD_PANEL_TABLES:
        return False

    from src.dating.site_presence import site_presence

    try:
        presence = await site_presence(int(site_id))
    except Exception as e:
        logger_info(f'[dating] панель для писем сайта #{site_id} не спросилась: {e}')

        return False

    driver = str((presence or {}).get('driver') or '').strip()

    return driver in MAIL_COLD_PANEL_DRIVERS


async def mail_cold_panel_send(letter: dict, profile: dict) -> dict:
    """Отправляет холодное письмо панелью. Ничего не пишет в базу — дело зовущего.

    Args:
        letter: строка пула: `site_id`, `uid`, `body`; у письма со снимком —
            `attach` готовыми байтами (`{'name', 'data'}`).
        profile: анкета-отправительница: `id` — наша строка, по ней берём её
            номер на сайте.

    Returns:
        dict: `{'sent': True, 'answer': <что сказала панель>}`.

    Raises:
        DatingPanelColdError: панель не приняла черновик, кадр или письмо; сессия
            не признана; у анкеты нет номера на сайте.

    ## ⚠⚠ Три шага, и отказ любого — отказ письма

    Черновик без письма — безопасно: панель чистит пустые черновики сама
    (`draftdeleteempty`). Письмо без кадра — запрещено правилом владельца, поэтому
    неудача загрузки возвращает ошибку, а не «отправим текстом».
    """
    site_id = int(letter.get('site_id') or 0)
    to_id = str(letter.get('uid') or '').strip()
    body = str(letter.get('body') or '').strip()

    if not to_id or not to_id.isdigit():
        raise DatingPanelColdError(
            f'адресат письма не похож на номер в панели: {to_id or "пусто"}')

    if not body:
        raise DatingPanelColdError('письмо пустое — отправлять нечего')

    sender = await _girl_site_id(int(profile.get('id') or 0))

    # ── Шаг 1: черновик ──────────────────────────────────────────────────────
    draft = await _ask(site_id, MAIL_COLD_PANEL_DRAFT,
                       {'to_id': to_id, 'reply_id': '0', 'from_id': str(sender)},
                       'панель не завела черновик')

    # Номер черновика обязателен: на нём держатся и кадр, и отправка, а сама
    # страница сочинения проверяет ровно это (`answerData.id > 0`) и при нуле
    # не продолжает. Пройти дальше с нулём — значит увести письмо в никуда.
    draft_id = int(draft.get('id') or 0)
    if draft_id <= 0:
        raise DatingPanelColdError(
            f'панель завела черновик без номера (id {draft.get("id")!r})')

    # ── Шаг 2: кадр письма ───────────────────────────────────────────────────
    attach = letter.get('attach') or {}
    raw = attach.get('data') or b''
    if raw:
        await _ask(
            site_id, MAIL_COLD_PANEL_UPLOAD,
            {'id': str(draft_id), 'type': MAIL_COLD_PANEL_TYPE},
            'панель не приняла кадр письма',
            files={'upload': (str(attach.get('name') or 'photo.jpg'), raw,
                              'image/jpeg')})

    # ── Шаг 3: отправка ──────────────────────────────────────────────────────
    sent = await _ask(
        site_id, MAIL_COLD_PANEL_SEND,
        {'id': str(draft_id), 'to_id': to_id,
         'type': MAIL_COLD_PANEL_TYPE, 'subj': MAIL_COLD_PANEL_SUBJ,
         'text': body},
        'панель не приняла письмо')

    said = json.dumps(sent, ensure_ascii=False)[:500]
    logger_info(f"[dating] письмо ушло панелью: сайт #{site_id}, "
                f"{profile.get('id')} → {to_id}")

    return {'sent': True, 'answer': said}


# ── Приватное ────────────────────────────────────────────────────────────────

async def _ask(site_id: int, path: str, fields: dict, why: str,
               files: dict = None) -> dict:
    """Один шаг сочинения: форма панели → разобранный JSON.

    ⚠ **Удача — `result == 'ok'`, и только она.** Код `200` панель отдаёт и на
    отказ, а `{result: 'error', text: …}` несёт **её** причину человеческими
    словами — её и показываем, а не выдумываем свою.
    """
    try:
        said = await panel_fetch_post(site_id, path, fields, files=files)
    except DatingPanelFetchError as e:
        raise DatingPanelColdError(f'{why}: {e}')

    try:
        data = json.loads(said)
    except Exception:
        raise DatingPanelColdError(
            f'{why}: панель ответила не JSON ({str(said)[:80]})')

    if not isinstance(data, dict):
        raise DatingPanelColdError(f'{why}: непонятный ответ панели')

    if str(data.get('result') or '') != 'ok':
        raise DatingPanelColdError(f'{why}: '
                                   f'{str(data.get("text") or "без объяснения")}')

    return data


async def _girl_site_id(profile_id: int) -> int:
    """Номер девушки **на сайте** — тот, что панель зовёт `from_id`.

    ⚠ Берём из базы, а не из `login`: у Step2Love номер и есть логин, а у прочих
    панельных сайтов логины бывает почта; `profile_id` — единственное поле, где
    номер у всех сайтов одинаково.

    ⚠ Нуль — отказ, а не отправка «как-нибудь»: черновик без отправителя панель
    либо не заведёт, либо заведёт не от той девушки.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT profile_id FROM profile WHERE id = %s',
                             (int(profile_id),))
            row = await db.fetchone()
    except Exception as e:
        raise DatingPanelColdError(f'номер анкеты на сайте не спросился: {e}')

    got = int((row or {}).get('profile_id') or 0)
    if not got:
        raise DatingPanelColdError(
            f'у анкеты #{profile_id} нет номера на сайте — панель не узнает, '
            'от кого письмо')

    return got
