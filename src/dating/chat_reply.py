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

from src.dating.chat_initiative import CHAT_INITIATIVE_INVITED

# Сколько черновиков показываем в разговоре. Больше одного там быть не может — у
# пары один последний повод, — но выборка всё равно с пределом: строка в базе
# живёт дольше повода, и старые могли остаться при своих репликах.
CHAT_REPLY_SHOW = 1

# Состояние «ушло приглашением, ждёт согласия».
#
# ⚠⚠ Берётся **у инициативы**, а не объявляется своим словом: состояние у них
# одно и то же — текст лежит у собеседника в списке запросов, — и два разных
# слова на него заставили бы читателя гадать, чем они разнятся. Сторож стоит на
# их совпадении (`test_answer_invite.py`).
CHAT_REPLY_INVITED = CHAT_INITIATIVE_INVITED

async def chat_reply_put(site_id: int, profile_id: int, message_id: int,
                         uid: str, name: str, body: str,
                         talk_message_id: int = 0) -> int:
    """Кладёт черновик ответа. Возвращает номер строки, ноль — не легло.

    Args:
        site_id: сайт-закладка.
        profile_id: наша анкета.
        message_id: повод написать — реплика в нашей `message`, на которую
            отвечаем. На нём стоит `UNIQUE`-ключ.
        uid: собеседник на сайте.
        name: его имя на момент ответа.
        body: текст, который уйдёт.
        talk_message_id: номер обмена разговора **у соседа** — тот, которым
            отмечается доставка (`rhapsody_api_chat_delivered`). Не повод и не
            ключ: это чужой счётчик, пришедший с ответом агента. Ноль значит
            «агент промолчал о номере» и ложится `NULL`, а не самим нулём —
            отметка на нуле ушла бы в никуда.

    ⚠ **Повтор — не ошибка.** Ключ на `message_id` превращает второй заход круга
    на ту же реплику в `INSERT IGNORE`, который ничего не делает. Это и есть
    защита от двух ответов одному человеку, и шуметь тут не о чем.
    """
    if not (int(profile_id or 0) and int(message_id or 0) and str(body or '').strip()):
        return 0

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO chat_reply'
            '  (site_id, profile_id, message_id, uid, name, body, talk_message_id)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (int(site_id or 0), int(profile_id), int(message_id),
             str(uid or ''), str(name or ''), str(body),
             int(talk_message_id or 0) or None))
        await db.connection.commit()

        return int(db.lastrowid or 0)


async def chat_reply_waiting(profile_id: int, message_id: int) -> dict:
    """Черновик, ждущий отправки по этому поводу. Пусто — его нет.

    ⚠ Спрашиваем **по поводу**, а не по паре: у пары в базе может лежать и старый
    черновик — при своей, уже неактуальной реплике. Отдать его значило бы
    отправить ответ на позавчерашнее.

    ⚠ Отдаём и `talk_message_id`: ветка готового черновика в `answer_run` берёт
    номер отметки доставки отсюда — агента во втором заходе уже не спрашивают, и
    другого источника у номера нет.
    """
    if not (int(profile_id or 0) and int(message_id or 0)):
        return {}

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, body, error, talk_message_id FROM chat_reply'
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


async def chat_reply_invited(reply_id: int) -> None:
    """Отмечает, что ответ ушёл **приглашением** и ждёт согласия.

    Args:
        reply_id: номер строки пула.

    ## ⚠⚠⚠ Третье состояние, и ни одно из двух прежних сюда не годится

    У сайта с приглашениями (Step2Love) писать можно только в открытый чат, а
    закрывший окно собеседник оставляет его пустым. Тогда реплика уходит
    приглашением: текст лежит у него в списке запросов и ждёт галочки.

    | Чем пометить | Что сломается |
    |--------------|---------------|
    | `sent` | агент сочтёт реплику сказанной и продолжит от непрочитанного |
    | `draft` | круг разошлёт второе приглашение через минуту, третье через две |

    Отсюда `invited` — «позвали и ждём». Судьбу строки разбирает круг ответов
    (`answer._invited_check`): примут — доставка, истечёт срок — отказ.

    ⚠ Время ставим: по нему и считается срок ожидания. Без него строка висела
    бы вечно — разобрать её было бы нечем.
    """
    if not int(reply_id or 0):
        return

    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE chat_reply SET status = %s, error = %s, sent_at = NOW()'
            ' WHERE id = %s',
            (CHAT_REPLY_INVITED, 'приглашение ушло, ждёт согласия',
             int(reply_id)))
        await db.connection.commit()


async def chat_reply_invited_rows(profile_id: int, older_sec: int) -> list[dict]:
    """Приглашения этой анкеты, по которым пора принять решение.

    Args:
        profile_id: анкета.
        older_sec: сколько секунд должно пройти с отправки. ⚠ Раньше срока
            решать нечего — собеседник ещё может нажать галочку.

    Returns:
        list: строки пула в состоянии `invited`, старые первыми.
    """
    if not int(profile_id or 0):
        return []

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT * FROM chat_reply'
                ' WHERE profile_id = %s AND status = %s'
                '   AND sent_at IS NOT NULL'
                '   AND sent_at < NOW() - INTERVAL %s SECOND'
                ' ORDER BY sent_at',
                (int(profile_id), CHAT_REPLY_INVITED, int(older_sec)))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] ждущие приглашения ответа не спросились: {e}')

        return []

    return [dict(row) for row in rows or []]


async def chat_reply_hanging(profile_id: int, uid: str) -> list[dict]:
    """Ответы пары, ушедшие приглашением и ждущие согласия. Для показа человеку.

    Returns:
        list: строки пула в состоянии `invited`, от старых к свежим.

    ⚠⚠ Отдельно от `chat_reply_invited_rows`: та отбирает **по сроку** — её
    спрашивает круг, чтобы решить судьбу. Эта отвечает «что сейчас висит у
    этой пары», и срок ей не нужен: человеку показывают и то, что ушло минуту
    назад. То же разделение, что у инициативы.
    """
    if not (int(profile_id or 0) and str(uid or '').strip()):
        return []

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, body, sent_at FROM chat_reply'
                ' WHERE profile_id = %s AND uid = %s AND status = %s'
                ' ORDER BY sent_at, id',
                (int(profile_id), str(uid), CHAT_REPLY_INVITED))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] ждущие приглашения ответа не прочитались: {e}')

        return []

    return [dict(row) for row in rows or []]


async def chat_reply_expired(reply_id: int) -> None:
    """Приглашение не приняли: реплику не прочитал никто.

    ⚠⚠ Строка метится **настоящим отказом** (`failed`), а не возвращается в
    пул. Повод остаётся неотвеченным, и это правда: мы позвали, человек не
    открыл. Верни мы её черновиком — круг послал бы второе приглашение, третье,
    и так, пока он не вернётся; у закрывшейся анкеты — вечно.

    ⚠ Второго приглашения по тому же поводу не будет и даром: ключ пула стоит
    на `message_id`, то есть **одна строка на реплику**. Напишет снова — будет
    новый повод и новый ответ, это законно.

    ⚠ Отличие от инициативы, где такая строка **удаляется**: там пара обязана
    снова стать свободной — круг зовёт её по своему сроку. Здесь повод один и
    неповторим, и след о попытке полезнее пустоты: он виден в разговоре.
    """
    if not int(reply_id or 0):
        return

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_reply SET status = 'failed', error = %s"
            ' WHERE id = %s',
            ('приглашение не приняли — реплику не прочитали', int(reply_id)))
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
