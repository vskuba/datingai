"""Черновик ответа в чате: написан агентом, ждёт собеседника в сети.

## Зачем он есть

Ответ сочинялся и уходил одним движением, и пока собеседника нет в сети, не
существовал вовсе. Человек в панели видел пустоту — «ответ готовится» ничем не
отличалось от «ничего не происходит», — а собеседник, вернувшись, ждал ещё круг и
десяток секунд на LLM.

Теперь текст пишется заранее и лежит строкой. Появился человек — уходит сразу.

## ⚠⚠ Устроено как у писем, и нарочно

У `mail_reply` это работает давно: строка со `status = 'draft'`, показ последней
репликой разговора, отправка кругом. Второй способ хранить то же самое разошёлся
бы с первым на первой же правке, а объяснить, почему «ждущий ответ» у писем и у
чата ведёт себя по-разному, нечем.

## ⚠ Единица — реплика, а не человек

Черновик отвечает **конкретной реплике** (`message_id`), и уникальный ключ стоит
на ней. Отсюда даром получаются протухание (написал он снова — повод новый, и
черновик новый) и защита от дублей (круг ходит раз в минуту и запросто зайдёт
дважды).

⚠ Поводом бывает и **наша** реплика: у ведущей разговор анкеты второй повод
написать — тишина после собственных слов.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Сколько черновиков показываем в разговоре. Больше одного там быть не может — у
# пары один последний повод, — но выборка всё равно с пределом: строка в базе
# живёт дольше повода, и старые могли остаться при своих репликах.
CHAT_REPLY_SHOW = 1

# Сколько живёт ждущий черновик — **настройка сайта** и её умолчание.
#
# ⚠ Настройкой, а не константой: срок видно на вкладке «Настройки сайтов», и
# правит его тот, кто ведёт сайт. Зашитое в код число он бы не нашёл — а искать
# стал бы именно тогда, когда ответ ушёл не вовремя или не ушёл вовсе.
CHAT_REPLY_STALE_SETTING = 'chat_reply_stale_hours'
#
# ## ⚠⚠ Зачем срок вообще
#
# Черновик снимает обе сторожевые проверки отбора — выдержку повтора и порог
# свежести панельного (`answer._waiting_find`), — потому что платить за него уже
# не надо. Без своего срока это открыло бы дыру в обратную сторону: пара с
# написанным текстом бралась бы в работу **вечно**, и однажды ушёл бы ответ
# недельной давности.
#
# ## ⚠ Сутки, а не больше
#
# Текст сочинялся под разговор «сейчас». Человек, не заходивший сутки, вернётся к
# другому: ответ на позавчерашнюю реплику он прочтёт как обращение не по адресу.
# Дешевле сочинить заново — это один вызов LLM, а не испорченный разговор.
CHAT_REPLY_STALE_DEFAULT = 24


async def chat_reply_put(site_id: int, profile_id: int, message_id: int,
                         uid: str, name: str, body: str) -> int:
    """Кладёт черновик ответа. Возвращает номер строки, ноль — не легло.

    Args:
        site_id: сайт-закладка.
        profile_id: наша анкета.
        message_id: повод написать — реплика, на которую отвечаем.
        uid: собеседник на сайте.
        name: его имя на момент ответа.
        body: текст, который уйдёт.

    ⚠ **Повтор — не ошибка.** Ключ на `message_id` превращает второй заход круга
    на ту же реплику в `INSERT IGNORE`, который ничего не делает. Это и есть
    защита от двух ответов одному человеку, и шуметь тут не о чем.
    """
    if not (int(profile_id or 0) and int(message_id or 0) and str(body or '').strip()):
        return 0

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO chat_reply'
            '  (site_id, profile_id, message_id, uid, name, body)'
            ' VALUES (%s, %s, %s, %s, %s, %s)',
            (int(site_id or 0), int(profile_id), int(message_id),
             str(uid or ''), str(name or ''), str(body)))
        await db.connection.commit()

        return int(db.lastrowid or 0)


async def chat_reply_waiting(profile_id: int, message_id: int) -> dict:
    """Черновик, ждущий отправки по этому поводу. Пусто — его нет.

    ⚠ Спрашиваем **по поводу**, а не по паре: у пары в базе может лежать и старый
    черновик — при своей, уже неактуальной реплике. Отдать его значило бы
    отправить ответ на позавчерашнее.
    """
    if not (int(profile_id or 0) and int(message_id or 0)):
        return {}

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, body, error FROM chat_reply'
            " WHERE profile_id = %s AND message_id = %s AND status = 'draft'",
            (int(profile_id), int(message_id)))
        row = await db.fetchone()

    return dict(row) if row else {}


async def chat_reply_sent(reply_id: int) -> None:
    """Отмечает, что черновик ушёл."""
    if not int(reply_id or 0):
        return

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_reply SET status = 'sent', error = '', sent_at = NOW()"
            ' WHERE id = %s', (int(reply_id),))
        await db.connection.commit()


async def chat_reply_failed(reply_id: int, error: str) -> None:
    """Запоминает причину неудачи, **оставляя черновик в пуле**.

    ⚠ Не `failed`, а `draft` с причиной: повод написать никуда не делся, и
    следующий круг должен повторить. `failed` похоронил бы ответ на живую реплику,
    и единственным способом его оживить была бы правка базы руками.
    """
    if not int(reply_id or 0):
        return

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_reply SET status = 'draft', error = %s, sent_at = NULL"
            ' WHERE id = %s', (str(error or '')[:500], int(reply_id)))
        await db.connection.commit()


async def chat_reply_drop(profile_id: int, uid: str) -> int:
    """Убирает **ждущие** черновики пары. Возвращает, сколько убрал.

    Зовётся, когда ответ стал не нужен: человек ответил сам, пару закрыли, анкету
    отключили. Ушедшие не трогаем — они часть переписки.
    """
    if not int(profile_id or 0):
        return 0

    async with mysql_get_db_async() as db:
        await db.execute(
            "DELETE FROM chat_reply WHERE profile_id = %s AND uid = %s"
            " AND status = 'draft'", (int(profile_id), str(uid or '')))
        await db.connection.commit()

        return int(db.rowcount or 0)


async def chat_reply_pending(profile_id: int, uid: str) -> list[dict]:
    """Черновики пары для показа в разговоре — с их поводом.

    Returns:
        list: `{'id', 'body', 'error', 'created_at'}`, свежие первыми.

    ⚠ **Только те, чей повод всё ещё последний в разговоре.** Иначе в окне рядом с
    новой репликой висел бы ответ на предыдущую — человек прочитал бы его как наш
    следующий ход, хотя круг его уже не отправит.

    ⚠ Сбой чтения не роняет разговор: вернём пусто. Потерять переписку из-за
    черновика было бы худшим разменом — то же правило, что у лиц в нити писем.
    """
    if not int(profile_id or 0):
        return []

    try:
        async with mysql_get_db_async() as db:
            # Повод считается той же меркой, что «последняя реплика» везде в
            # проекте: по времени, а `id` — только внутри одного времени. Импорт
            # из панели кладёт недельную историю сегодняшними `id`, и `MAX(id)`
            # назвал бы последней реплику недельной давности.
            await db.execute(
                'SELECT r.id, r.body, r.error, r.created_at FROM chat_reply r'
                "  WHERE r.profile_id = %s AND r.uid = %s AND r.status = 'draft'"
                '    AND r.message_id = ('
                '      SELECT MAX(x.id) FROM message x'
                '       WHERE x.profile_id = r.profile_id AND x.sender_uid = r.uid'
                '         AND x.created_at = (SELECT MAX(y.created_at) FROM message y'
                '                              WHERE y.profile_id = r.profile_id'
                '                                AND y.sender_uid = r.uid))'
                '  ORDER BY r.id DESC LIMIT %s',
                (int(profile_id), str(uid or ''), CHAT_REPLY_SHOW))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] черновик ответа не прочитался: {e}')

        return []

    return [dict(row) for row in (rows or [])]


async def chat_reply_expire() -> int:
    """Снимает черновики, которых никто не дождался. Возвращает, сколько сняли.

    ## ⚠⚠ Пара к сторожевому послаблению

    Готовый черновик снимает у отбора выдержку повтора и порог свежести
    панельного: платить за него уже не надо (`answer._waiting_find`). Без этой
    уборки послабление работало бы вечно, и однажды ушёл бы ответ недельной
    давности.

    ⚠ **Ушедшие не трогаем** — они часть переписки. Снимаем только ждущие.

    ⚠ **Срок у каждого сайта свой** (`chat_reply_stale_hours`), поэтому идём
    посайтно: в `chat_reply` сайт есть своей колонкой, лишнего join не надо.
    """
    from src.uvicorn.routers.dating.site_ import site_seconds

    gone = 0
    try:
        async with mysql_get_db_async() as db:
            await db.execute("SELECT DISTINCT site_id FROM chat_reply"
                             " WHERE status = 'draft'")
            sites = [int(row['site_id']) for row in (await db.fetchall() or [])]
    except Exception as e:
        logger_info(f'[dating] сайты для уборки черновиков не спросились: {e}')

        return 0

    for site_id in sites:
        try:
            # ⚠ Без `or`: `site_seconds` сама подставляет умолчание реестра, а
            # `or` поверх неё превратил бы в умолчание законный ноль — «не
            # снимать» молча стало бы «снимать через сутки».
            hours = int(await site_seconds(site_id, CHAT_REPLY_STALE_SETTING))
        except Exception as e:
            logger_info(f'[dating] срок черновика сайта #{site_id}: {e}')

            continue

        # Ноль — черновик ждёт, пока адресат не вернётся.
        if hours <= 0:
            continue

        try:
            async with mysql_get_db_async() as db:
                await db.execute(
                    "DELETE FROM chat_reply WHERE site_id = %s AND status = 'draft'"
                    ' AND created_at < NOW() - INTERVAL %s HOUR',
                    (site_id, hours))
                gone += int(db.rowcount or 0)
                await db.connection.commit()
        except Exception as e:
            logger_info(f'[dating] черновики сайта #{site_id} не убрались: {e}')

    if gone:
        logger_info(f'[dating] черновики ответа: снято {gone} протухших')

    return gone
