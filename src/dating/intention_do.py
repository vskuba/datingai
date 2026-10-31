"""Исполнение намерений: обещанное уходит письмом.

Агент в разговоре помечает решения меткой словом, а подпоток соседа собирает к
ней контекст — текст письма и то, чем его исполнять (`chat_intention`,
`api.md`, §3.15.1). До сих пор этим никто не распоряжался: пул рос, колонка
«Сделано» была пуста всегда. Здесь — то занятие, которое берёт обещание и
делает дело.

## Что происходит

1. Берём открытое намерение, которому есть что исполнить.
2. Смотрим в карту `_DOERS`: чем эта метка исполняется.
3. Кладём черновик письма в `intention_mail`.
4. Отправка уносит его позже (`mail_send.py`).

## ⚠ Исполнителей двое, и разнятся они ровно вложением

`she_promised_send_photo` выбирает по приметам контекста снимок, которого
**этот** человек не видел (`companion_gallery_pick`), и кладёт его номер в
строку пула. `she_promise_tell_a_story` кладёт то же письмо **без снимка**:
обещанная история целиком приехала текстом, выбирать нечего.

Общего у них — текст и запись, и оно вынесено (`_body`, `_draft_save`): иначе
«письмо без вложения» стало бы вторым описанием строки пула, и первая же
правка колонок развела бы их молча.

## ⚠⚠ Сочинение и отправка разведены, как у всех писем

Между «собрали письмо» и «оно ушло» проходят минуты: отправку держат пауза
отправительницы, остуда ящика получателя, расписание. Сделай мы это одним
шагом — письмо уходило бы ровно в тот миг, когда круг до него добрался, то есть
залпом со всеми прочими.

Отсюда же `gallery_file_id` в строке: отбор считается заново на каждый вопрос
(снимок мог уйти этому человеку другой дорогой), но человек, глядя на карточку,
должен видеть **тот** снимок, который уйдёт.

## ⚠⚠ Отметок две, и ставятся они в разные моменты

`companion_gallery_sent` — когда письмо **ушло**, а не когда выбрали: иначе
первая же неудача сожгла бы снимок, и повторная попытка взяла бы другой.
`chat_intention_done` — там же: обещание исполнено отправкой, а не намерением
отправить.

⚠ Снимок к письму готовится **байтами заранее**: между `check-send` и `send`
нельзя ходить в сеть, а Диск отвечает секунды. Готовит их `intention_mail_attach`
до входа в отправку.

## ⚠ Чего здесь нет

**Сочинения.** Текст письма приезжает вместе с меткой — его пишет тот же
прогон, что нашёл обещание, пока история разговора у него под рукой. Спроси мы
модель здесь второй раз, письмо разошлось бы с тем, что агент уже сказал
собеседнику.

⚠ Метку присылает workflow у соседа, и исполнитель у каждой свой: выбор лежит
в `_DOERS`, а неизвестная метка молча **ждёт в пуле**, а не считается
сделанной. Так и задумано — новое обещание агента не должно закрываться
неисполненным только оттого, что панель про него ещё не знает.
"""

import os

from ai.vision.ai_vision import ai_vision_normalize
from google_drive_.google_drive_bytes import google_drive_bytes
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.chat_intention import chat_intention_done, chat_intention_rows
from src.dating.companion_gallery import (companion_gallery_pick,
                                          companion_gallery_sent)

# Какие метки чем исполняются. ⚠ Карта, а не `if`: метки приезжают **от
# соседа**, из workflow, и каждая новая иначе требовала бы правки ветвления.
#
# ⚠ Слова здесь — **те самые**, что в указаниях тем судьи у соседа
# (`agent_workflow_topic.instructions`, группа 2) и в развилке подпотока
# `intention_context`. Третьего списка нет и быть не должно: метка приезжает
# строкой, и опечатка здесь означала бы вечно ждущее обещание без единой
# ошибки.
INTENTION_DO_PHOTO = 'she_promised_send_photo'
INTENTION_DO_STORY = 'she_promise_tell_a_story'

# Снимок режем до этой стороны перед отправкой. ⚠⚠ 1600 — самое крупное превью
# Диска (`GOOGLE_DRIVE_THUMB_SIZES`), и оригинал вместо него был бы ошибкой:
# на Диске лежат PNG по 8 МБ, а base64 добавляет треть — такое письмо сайт
# отвергнет, и узнаем мы об этом уже потратив поход.
INTENTION_DO_WIDTH = 1600

# Сколько черновиков собираем за один заход. ⚠ Предел на **сайт**: заход стоит
# похода на Диск за каждым снимком, и десяток разом занял бы круг надолго.
INTENTION_DO_BATCH = 5

# Во сколько раз больше кандидатов берём, чем черновиков собираем. Запас идёт
# на обещания, которые исполнить нечем, — они остаются открытыми и на следующем
# заходе снова первые.
INTENTION_DO_SPARE = 4

# Сколько намерений читаем за страницу, пока ищем несобранные.
INTENTION_DO_PAGE = 50

# Сколько страниц пролистываем, прежде чем сдаться. ⚠ Потолок нужен: при полном
# пуле и отсутствии новых обещаний отбор иначе обошёл бы всю таблицу на каждом
# заходе круга.
INTENTION_DO_PAGES = 10


class DatingIntentionError(Exception):
    """Намерение исполнить не вышло. Текст — то, что увидит человек."""


async def intention_mail_make(site_id: int, limit: int = 0,
                              profile_id: int = 0) -> dict:
    """Собрать черновики писем по открытым намерениям сайта.

    Args:
        site_id: сайт. Ноль — ничего не делаем.
        limit: сколько черновиков за заход. Ноль — `INTENTION_DO_BATCH`.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.

    Returns:
        dict: `{'made': сколько собрано, 'skipped': сколько пропущено,
        'why': [причины словами]}`.

    ⚠ **Ничего не отправляет.** Черновики уносит круг отправки, со своей паузой
    и своей нормой; это занятие только готовит.

    ⚠ Намерение, которому собрать нечем, **остаётся открытым** и причины в себе
    не носит: причина эта на каждом заходе своя (снимок мог появиться), и
    записанная она пережила бы свой повод. Видна она на карточке — её считает
    та же `companion_gallery_pick`, которую зовёт ручка превью.
    """
    site_id = int(site_id or 0)
    if not site_id:
        return {'made': 0, 'skipped': 0, 'why': []}

    want = int(limit or 0) or INTENTION_DO_BATCH
    made, skipped, why = 0, 0, []

    # ⚠⚠ Кандидатов берём **с запасом**, а не ровно `want`. Обещание, которое
    # исполнить нечем (у анкеты нет галереи, приметы ни к чему не подошли),
    # остаётся открытым и на следующем заходе снова окажется первым — порядок
    # у открытых по возрасту. Бери мы ровно пятерых, пять таких обещаний
    # заняли бы заход навсегда, и годные за ними не собирались бы никогда.
    for row in await _todo(site_id, profile_id, want * INTENTION_DO_SPARE):
        if made >= want:
            break

        try:
            await intention_mail_draft(row)
            made += 1
        except DatingIntentionError as e:
            skipped += 1
            # ⚠ Причины копим **с именем пары**: «снимок не выбрался» без неё
            # не говорит ничего — намерений в пуле десятки.
            why.append(f'#{row["id"]} {row.get("name") or row["uid"]}: {e}')

    if made or skipped:
        logger_info(f'[dating] намерения сайта #{site_id}: собрано {made}, '
                    f'пропущено {skipped}')

    return {'made': made, 'skipped': skipped, 'why': why}


async def intention_mail_draft(row: dict) -> int:
    """Черновик письма по одному намерению. Возвращает номер строки пула.

    Args:
        row: намерение в форме `chat_intention_rows` — с разобранным
            `context`.

    Returns:
        int: номер строки `intention_mail`.

    Raises:
        DatingIntentionError: исполнять нечем — словами, почему.

    ⚠ Повтор ключ отвергает сам (`uq_intention_mail_once`), и это **не ошибка**:
    круг просыпается часто, а черновик ждёт человека. Второй заход отвечает
    номером уже лежащей строки.
    """
    doer = _DOERS.get(str(row.get('intention') or ''))
    if not doer:
        raise DatingIntentionError(
            f'намерение «{row.get("intention")}» исполнять нечем: '
            'занятия под эту метку нет')

    return await doer(row)


async def intention_mail_attach(file_id: str, name: str) -> dict:
    """Снимок с Диска → вложение письма: имя и байты готового JPEG.

    Args:
        file_id: номер файла на Диске (`gallery_file.file_id`).
        name: имя файла из индекса — оно и уедет в `AttachmentName`.

    Returns:
        dict: `{'name': str, 'data': bytes}` — то, что принимает `mail_send_one`.

    Raises:
        DatingIntentionError: Диск не отдал кадр либо отдал не изображение.

    ## ⚠⚠ Приводим к JPEG, а не шлём как есть

    На Диске лежат PNG, и крупный весит мегабайты; base64 добавляет треть.
    Письмо с таким вложением сайт отвергнет — и узнаем мы это, уже потратив
    поход и сожжённое разрешение на отправку.

    ⚠ Зовём `ai_vision_normalize`, хотя зрение тут ни при чём: операция у неё
    общая — «любой формат → RGB-JPEG с ограниченной стороной», — и в общем слое
    она уже служит не только моделям (`adb_/adb_.py` снимает ею экран
    устройства). Второй такой функции рядом заводить незачем.

    ⚠ Имя приводим к `.jpg`: содержимое после приведения — JPEG, и `photo.png`
    в `AttachmentName` соврал бы почтовому клиенту собеседника.
    """
    if not str(file_id or '').strip():
        raise DatingIntentionError('у снимка нет номера файла на Диске')

    try:
        raw, _ = await google_drive_bytes(str(file_id), INTENTION_DO_WIDTH)
    except (RuntimeError, OSError) as e:
        raise DatingIntentionError(f'Диск не отдал снимок: {e}') from e

    try:
        frame = ai_vision_normalize(raw)
    except Exception as e:
        raise DatingIntentionError(f'снимок не читается как изображение: {e}') from e

    return {'name': _jpeg_name(name), 'data': frame}


async def intention_mail_ready(letter: dict) -> dict:
    """Строка пула → письмо, готовое к отправке: тот же словарь плюс снимок.

    Args:
        letter: строка `intention_mail` — нужны `gallery_file_id` и `body`.

    Returns:
        dict: копия строки с ключом `attach` (`{'name', 'data'}`). Без снимка
        (`gallery_file_id = 0`) — та же строка без `attach`, и письмо уйдёт
        текстом.

    Raises:
        DatingIntentionError: снимок есть, а взять его нечем.

    ## ⚠⚠ Одна дверь на обе дороги

    Готовить вложение надо и кругу отправки, и кнопке человека. Своя подготовка
    у каждого разошлась бы ровно там, где это дороже всего: один слал бы снимок,
    другой — то же письмо текстом, и собеседник получил бы «вот моё фото» без
    фото. Та же причина, по которой `reply_to` однажды свели в одно место.

    ⚠ Зовётся **до** `mail_send_one`, а не внутри: между `check-send` и `send`
    нельзя ходить в сеть, а Диск отвечает секунды.
    """
    file_id = int(letter.get('gallery_file_id') or 0)
    if not file_id:
        return dict(letter)

    async with mysql_get_db_async() as db:
        await db.execute('SELECT file_id, name FROM gallery_file WHERE id = %s',
                         (file_id,))
        file = await db.fetchone()

    if not file:
        raise DatingIntentionError('снимка больше нет в индексе галереи')

    attach = await intention_mail_attach(str(file['file_id']),
                                         str(file['name'] or ''))

    return {**letter, 'attach': attach}


async def intention_mail_done(letter: dict) -> None:
    """Письмо ушло: запомнить снимок и закрыть обещание.

    Args:
        letter: строка `intention_mail` целиком — нужны `intention_id`,
            `profile_id`, `site_id`, `uid`, `gallery_file_id`.

    ## ⚠⚠ Обе отметки — здесь, и ставятся они **после** отправки

    Память о снимке (`companion_gallery_file`) и закрытие намерения
    (`chat_intention.status`) отвечают на один вопрос — «сделано» — и
    разъехавшись, дали бы худшее из состояний: обещание закрыто, а снимок
    числится непоказанным, и следующее обещание отправит его второй раз.

    ⚠ Ставить их **до** отправки нельзя: первая же неудача сожгла бы снимок, и
    повторная попытка взяла бы другой — у человека копилась бы галерея вместо
    обещанного кадра.

    ⚠ Своя неудача эту дверь не роняет: письмо **уже ушло**, и исключение
    отсюда заставило бы вызывающего считать отправку несостоявшейся. Молча,
    впрочем, не проходит — строка в журнале остаётся.
    """
    file_id = int(letter.get('gallery_file_id') or 0)

    try:
        if file_id:
            await companion_gallery_sent(int(letter.get('profile_id') or 0),
                                         int(letter.get('site_id') or 0),
                                         str(letter.get('uid') or ''), file_id)

        await chat_intention_done(int(letter.get('intention_id') or 0),
                                  _done_note(letter))
    except Exception as e:
        logger_info(f'[dating] письмо-исполнение #{letter.get("id")} ушло, '
                    f'а отметки не легли: {e}')


# ── Приватное ────────────────────────────────────────────────────────────────


async def _photo_doer(row: dict) -> int:
    """Исполнитель метки `she_promised_send_photo`: письмо со снимком.

    ⚠ Снимок выбирается **здесь**, а не при показе карточки: карточка
    спрашивает отбор заново и ничего не запоминает, а в пуле должен лежать тот
    кадр, который уйдёт.
    """
    context = row.get('context') or {}
    body = _body(context)

    pick = await companion_gallery_pick(int(row['profile_id']),
                                        int(row['site_id']), str(row['uid']),
                                        context.get('tags'))
    file = pick.get('file') or {}

    if not file.get('file_id'):
        raise DatingIntentionError(pick.get('why') or 'снимок не выбрался')

    return await _draft_save(row, body, file)


async def _story_doer(row: dict) -> int:
    """Исполнитель метки `she_promise_tell_a_story`: письмо без вложения.

    ## ⚠ Вся работа — в тексте, и он уже приехал

    Обещанная история сочиняется **там же, где помечено обещание**: подпоток
    соседа бросает жребий (о чём, какой длины, чем кончается) и пишет письмо,
    пока история разговора у него под рукой. Нам остаётся положить это в пул —
    выбирать, досочинять или спрашивать модель второй раз здесь нечего.

    ⚠ Снимка нет **намеренно**, а не по недоделке: `gallery_file_id = 0` —
    законное состояние строки пула (`intention_mail`), отправка такое письмо
    уносит текстом, а `_done_note` так и пишет — «отправлено письмо».
    """
    return await _draft_save(row, _body(row.get('context') or {}), None)


def _body(context: dict) -> str:
    """Текст письма из контекста. Пусто — отказ словами, а не пустое письмо.

    ⚠ Тело не режем: колонка `TEXT`, и соседние пулы его тоже не режут. Свой
    предел здесь был бы правилом, которого больше никто не держит.

    ⚠ Метки абзацев разворачивать не надо: контекст приезжает **уже
    развёрнутым** — это делает общая дверь чтения `_intention_row`
    (`chat_intention.intention_letter_text`), чтобы оператор и собеседник
    видели один и тот же текст.
    """
    body = str(context.get('letter') or '').strip()

    if not body:
        raise DatingIntentionError('в контексте нет текста письма')

    return body


async def _draft_save(row: dict, body: str, file: dict | None) -> int:
    """Положить черновик в пул. Повтор — не ошибка, а уже лежащая строка.

    Args:
        row: намерение в форме `chat_intention_rows`.
        body: текст письма.
        file: строка `gallery_file`, которая уедет вложением. `None` — письмо
            уходит текстом.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO intention_mail'
            '  (site_id, profile_id, intention_id, uid, name, photo,'
            '   gallery_file_id, body)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
            (int(row['site_id']), int(row['profile_id']), int(row['id']),
             str(row['uid']), str(row.get('name') or '')[:190],
             str(row.get('photo') or '')[:500],
             int((file or {}).get('id') or 0), body))
        made = int(db.lastrowid or 0) if db.rowcount else 0
        await db.connection.commit()

        if made:
            return made

        # ⚠ Ключ отверг вставку — черновик уже лежит. Отвечаем его номером, а
        # не нулём: вызывающему нужен результат, а не признак «что-то было».
        await db.execute('SELECT id FROM intention_mail WHERE intention_id = %s',
                         (int(row['id']),))

        return int(((await db.fetchone()) or {}).get('id') or 0)


async def _todo(site_id: int, profile_id: int, want: int) -> list:
    """Открытые намерения сайта, которым ещё не собран черновик.

    Args:
        site_id: сайт.
        profile_id: сузить до анкеты. Ноль — все анкеты сайта.
        want: сколько годных нужно. Набрав столько, перестаём листать.

    ⚠ Читаем **одной дверью** `chat_intention_rows`, а не своим запросом: она
    уже отдаёт разобранный контекст и имена обеих сторон, и второй отбор
    разошёлся бы с тем, что человек видит в пуле.

    ⚠ Собранные отсеиваются **после** чтения, а не условием в запросе: условие
    стояло бы внутри чужой двери, и ради него пришлось бы учить её про пул
    писем — то есть сделать чтение намерений зависимым от занятия.

    ## ⚠⚠ Отсюда листание, и без него занятие глохнет молча

    Отсев идёт после страницы, а `LIMIT` применяется до него — значит страница
    целиком из уже собранных даёт пустой ответ при живой работе дальше. Случай
    не выдуман: черновики ждут человека, и пока он не нажимает «Отправить»,
    они копятся. Полсотни неотправленных — и новые обещания перестают
    собираться вовсе, без единой ошибки. Та же грабля, что уже была у
    инициативы, где негодные пары съедали окно в двадцать строк.

    ⚠ Потолок страниц есть: отбор не должен превратиться в обход всей таблицы,
    когда пул забит, а новых обещаний нет.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT intention_id FROM intention_mail WHERE site_id = %s',
            (int(site_id),))
        has = {int(one['intention_id']) for one in (await db.fetchall() or [])}

    fresh, page = [], 1

    while page <= INTENTION_DO_PAGES:
        got = await chat_intention_rows(site_id, profile_id=profile_id,
                                        status='open', page=page,
                                        per=INTENTION_DO_PAGE)
        rows = got.get('rows') or []
        fresh += [row for row in rows if int(row['id']) not in has]

        if len(fresh) >= want or page >= int(got.get('pages') or 1):
            break

        page += 1

    return fresh[:want]


def _jpeg_name(name: str) -> str:
    """Имя файла с расширением `.jpg` — содержимое после приведения именно оно."""
    stem = os.path.splitext(str(name or '').strip())[0].strip()

    return f'{stem or "photo"}.jpg'


def _done_note(letter: dict) -> str:
    """Чем исполнили — словами, для колонки «Сделано»."""
    who = str(letter.get('name') or '') or str(letter.get('uid') or '')
    file_id = int(letter.get('gallery_file_id') or 0)

    return (f'отправлено фото письмом: {who}' if file_id
            else f'отправлено письмо: {who}')


# Какая метка чем исполняется.
#
# ⚠ Приватная и стоит **в самом низу**: значения в ней — ссылки на функции, и
# выше она падала бы на неопределённом имени, а публичной нарушала бы порядок
# «сперва публичное» (`code_rules.md`, §3). Наружу отвечает
# `intention_mail_draft`, а «какие метки умеем» видно по константам сверху.
_DOERS = {INTENTION_DO_PHOTO: _photo_doer,
          INTENTION_DO_STORY: _story_doer}
