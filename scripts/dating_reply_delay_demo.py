#!/usr/bin/env python3
"""Три ответа разной длины в пул — посмотреть таймер задержки глазами.

Зачем: задержка ответа по длине текста (`src/dating/reply_delay.py`,
`.claude/docs/api.md` §3.7.4) видна на карточке тикающим счётчиком — а чтобы
карточка появилась, нужны входящее письмо и сочинённый на него ответ. Ждать этого
от живой переписки можно сутками: письма приходят редко, и совпасть с окном, пока
ответ ещё лежит, почти не выходит.

Скрипт кладёт три ответа — на 400, 120 и 15 слов, — и при коэффициенте по
умолчанию (30 секунд на десять слов) их сроки расходятся от двадцати минут до
сорока пяти секунд. Этого хватает, чтобы увидеть и формат строки, и ход счётчика.

⚠⚠ **Только локально.** Строки кладутся в базу напрямую, мимо круга и агента; на
проде такой «ответ» уйдёт живому человеку от имени девушки. Поэтому сайт берётся
из `DEMO_SITE_ID` (по умолчанию `343` — Ladadate локальной базы), а в автовыкладке
скрипт не участвует.

Своё помечается `uid LIKE 'demo-ui-%'` — по этой же примете и сносится (`--drop`).
Чужих строк скрипт не трогает: ни одного `DELETE` без неё в файле нет.

⚠ Повторный запуск **освежает время входящих**, а не плодит строки: таймер идёт от
письма собеседника, и у вчерашнего черновика он давно истёк.

Запуск — внутри контейнера панели, репозиторий там смонтирован:

    docker exec dating_uvicorn python scripts/dating_reply_delay_demo.py
    docker exec dating_uvicorn python scripts/dating_reply_delay_demo.py --drop

Именно внутри, а не с хоста: в `.env` база записана именем сервиса
(`dating_mysql`), и оно разрешается только в сети compose — тот же довод, что у
`dating_seed.py`.

Смотреть на «Профили»:

* «Фавориты» → «Диалоги» → колонка **«Ответы в пуле»** — три карточки со сроками;
* «Письма» → «Диалоги» → форма **«Ответ на это письмо»** — срок рядом с кнопкой.
"""

import os
import sys

from mysql_.mysql_query import mysql_query_run

# Примета своих строк. Одна на весь скрипт: и ставится, и ищется по ней же —
# разойдись эти две строки, `--drop` перестал бы находить положенное.
DEMO_UID = 'demo-ui-'

# Сайт локальной базы. ⚠ Из переменной, а не числом в коде: у другой установки
# номер свой, и промах означал бы ответы в пуле чужого сайта.
DEMO_SITE_ID = int(os.getenv('DEMO_SITE_ID', '343'))

# Сколько слов в каждом ответе и от кого письмо. ⚠ Длины разные намеренно: один
# срок на три карточки не показал бы, что он считается по тексту.
DEMO_LETTERS = (
    ('1', 'Benoit', 400,
     'Tell me about your day, I have been thinking about our last letter.'),
    ('2', 'Mark', 120,
     'Good morning! How is the weather in Kyiv today?'),
    ('3', 'Henry', 15,
     'Just a quick note before work.'),
)


def main() -> int:
    """Положить или убрать. Код возврата — для оболочки, а не для красоты."""
    if '--drop' in sys.argv[1:]:
        return _drop()

    girls = _girls()
    if not girls:
        print(f'у сайта #{DEMO_SITE_ID} нет анкет — положить ответы некуда',
              file=sys.stderr)

        return 1

    # ⚠ Освежаем **до** вставки: у уже лежащих строк ответ тот же, а срок идёт от
    # письма — без этого повторный запуск показал бы три истёкших таймера.
    mysql_query_run(
        "UPDATE mail_in SET date_post = NOW() WHERE uid LIKE %s",
        (f'{DEMO_UID}%',))

    for (mark, name, words, text), profile_id in zip(DEMO_LETTERS, girls):
        _letter(f'{DEMO_UID}{mark}', name, text, int(profile_id))
        _reply(f'{DEMO_UID}{mark}', name, words, int(profile_id))

    _show()

    print('\nОткрыть: http://localhost:8021/admin/dating/profile')
    print('  «Фавориты» → «Диалоги» → колонка «Ответы в пуле»')
    print('  «Письма»   → «Диалоги» → форма ответа под разговором')
    print('Убрать: docker exec dating_uvicorn python '
          'scripts/dating_reply_delay_demo.py --drop')

    return 0


def _girls() -> list:
    """Три анкеты сайта — живые, а не выдуманные.

    ⚠ Карточка подписана именем и лицом девушки, и выдуманный номер оставил бы её
    пустой: смотреть на вёрстку стало бы не на что.

    ⚠ Их может оказаться меньше трёх — `zip` просто положит меньше ответов, и это
    верный исход: одна карточка показывает всё то же самое.
    """
    got = mysql_query_run(
        'SELECT id FROM profile WHERE site_id = %s ORDER BY id LIMIT 3',
        (DEMO_SITE_ID,))

    return [row['id'] for row in got['rows']]


def _letter(uid: str, name: str, text: str, profile_id: int) -> None:
    """Входящее письмо — то, от которого считается срок. Уже есть — не трогаем."""
    got = mysql_query_run('SELECT id FROM mail_in WHERE uid = %s', (uid,))
    if got['rows']:
        return

    mysql_query_run(
        'INSERT INTO mail_in (site_id, profile_id, message_id, uid, name, body,'
        ' date_post) VALUES (%s, %s, %s, %s, %s, %s, NOW())',
        (DEMO_SITE_ID, profile_id, 900_000_000 + profile_id, uid, name, text))


def _reply(uid: str, name: str, words: int, profile_id: int) -> None:
    """Черновик ответа заданной длины. Уже лежит — оставляем как есть."""
    got = mysql_query_run('SELECT id FROM mail_reply WHERE uid = %s', (uid,))
    if got['rows']:
        return

    letter = mysql_query_run('SELECT id FROM mail_in WHERE uid = %s', (uid,))
    if not letter['rows']:
        return

    mysql_query_run(
        'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid, name,'
        " body, status, error) VALUES (%s, %s, %s, %s, %s, %s, 'draft', '')",
        (DEMO_SITE_ID, profile_id, letter['rows'][0]['id'], uid, name,
         ' '.join(['word'] * int(words))))


def _show() -> None:
    """Что положено и какой у этого срок — числом, а не на глаз.

    ⚠⚠ **Коэффициент спрашивается у сайта, а не берётся умолчанием.** Поймано на
    первом же запуске: у локальной Ladadate стоит 15, скрипт печатал 30, и
    напечатанные сроки вдвое расходились с тем, что показывала карточка. Числа
    рядом, спорящие друг с другом, — худший исход для вещи, которую завели,
    чтобы **посмотреть**, работает ли оно.
    """
    import asyncio

    from src.dating.reply_delay import reply_delay_per10, reply_delay_sec

    per10 = asyncio.run(reply_delay_per10(DEMO_SITE_ID))

    got = mysql_query_run(
        'SELECT uid, body FROM mail_reply WHERE uid LIKE %s ORDER BY id',
        (f'{DEMO_UID}%',))

    if not per10:
        print(f'⚠ у сайта #{DEMO_SITE_ID} коэффициент задержки нулевой — '
              'таймера на карточках не будет, ответы уйдут ближайшей очередью')

    for row in got['rows']:
        words = len(str(row['body']).split())
        term = reply_delay_sec(row['body'], per10)
        print(f'{row["uid"]}: {words} слов → {term // 60} мин {term % 60} с '
              f'при {per10} с на 10 слов')


def _drop() -> int:
    """Убрать своё. ⚠ Ответы раньше писем: у них внешний ключ на `mail_in`."""
    mysql_query_run('DELETE FROM mail_reply WHERE uid LIKE %s', (f'{DEMO_UID}%',))
    mysql_query_run('DELETE FROM mail_in WHERE uid LIKE %s', (f'{DEMO_UID}%',))
    print('демо-ответы убраны')

    return 0


if __name__ == '__main__':
    sys.exit(main())
