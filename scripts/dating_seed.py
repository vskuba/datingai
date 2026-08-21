#!/usr/bin/env python3
"""Тестовые анкеты, собеседницы и переписка — для локальной разработки.

Зачем: на пустой базе не видно ни сортировки собеседниц, ни пагинации. Живая
анкета — это сотни диалогов и десятки тысяч реплик, и проверять на трёх строках
и порядок, и план запроса бессмысленно: любой порядок выглядит правильным.

**Только локально.** Скрипт ходит в базу напрямую, минуя API, и в автовыкладке не
участвует: на проде ему делать нечего.

Своё помечается `note = 'seed:contacts-sort'` — по этой пометке скрипт и сносит
данные (`--clear`). Чужие строки он не трогает никогда: ни одного `DELETE` без
этой пометки в файле нет. Удаление идёт по `dating_girl`, а `dating_message`,
`dating_contact` и `dating_log` уходят следом внешними ключами (`ON DELETE
CASCADE`) — руками их подчищать не нужно и нельзя, порядок за нас держит база.

Порядок вставки сообщений — по времени, а не по диалогам. Свежесть диалога панель
считает по `MAX(id)`, а `id` автоинкрементный: вставь мы диалог целиком, «свежим»
оказался бы тот, кого посеяли последним, и проверять сортировку было бы не на чем.

Запуск — внутри контейнера панели, репозиторий там смонтирован в `/app`:

    docker exec dating_uvicorn python scripts/dating_seed.py
    docker exec dating_uvicorn python scripts/dating_seed.py --clear

Именно внутри, а не с хоста: в `.env` база записана именем сервиса
(`dating_mysql`), и оно разрешается только в сети compose. С хоста пришлось бы
угадывать проброшенный порт — а на машине разработчика их два десятка от разных
проектов, и промах означает запись в чужую базу. Если очень нужно с хоста, порт
задаётся явно: `--host 127.0.0.1 --port 3306`.
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pymysql

# Пометка своих данных. Одна на весь скрипт: и ставится, и ищется по ней же —
# разойдись эти две строки, `--clear` перестал бы находить посеянное.
SEED_NOTE = 'seed:contacts-sort'

# Сайт тестовых анкет: Goldenbride, закладка живёт у RhapsodyAi. Номер зашит,
# потому что скрипт для местной разработки, а не для произвольной установки.
SITE_ID = 19

# Сколько анкет. Больше пяти страниц списка (по 8 на странице) — чтобы пагинация
# была не «две кнопки», а листалась по-настоящему.
GIRLS_N = 40

# У одной анкеты — сотни собеседниц: ради неё всё и затевалось. Восемь страниц по
# 50 штук.
BIG_CONTACTS_N = 400

# Реплик в диалоге. Нижняя граница выше десятка намеренно: карточка показывает
# последние десять, и диалог из трёх реплик не покажет, что список обрезан.
BIG_MESSAGES = (12, 100)

# Насколько давно мог писать самый молчаливый диалог. Три месяца — чтобы «старое
# неотвеченное» было заметно старым и в списке, и глазом по времени.
DAYS_BACK = 90

NAMES = ['Алина', 'Анна', 'Валерия', 'Вероника', 'Виктория', 'Дарья', 'Диана',
         'Евгения', 'Екатерина', 'Елена', 'Ирина', 'Кристина', 'Ксения', 'Лариса',
         'Людмила', 'Марина', 'Мария', 'Надежда', 'Наталья', 'Оксана', 'Ольга',
         'Полина', 'Светлана', 'София', 'Татьяна', 'Юлия', 'Яна', 'Ангелина']

CITIES = ['Киев', 'Одесса', 'Харьков', 'Львов', 'Днепр', 'Николаев', 'Полтава',
          'Винница', 'Черкассы', 'Запорожье']

# Реплики обеими сторонами. Текст нарочно разный по длине: в узкой строке карточки
# видно, как обрезается длинное, а в развёрнутом диалоге — как ложатся короткие.
TEXT_IN = ['Привет! Как твой день проходит?',
           'Ты давно на сайте? Я тут совсем недавно.',
           'Расскажи о себе, интересно очень',
           'Что любишь делать в выходные?',
           'У нас сегодня дождь весь день, а у тебя?',
           'Спасибо за фото, ты замечательно выглядишь',
           'Почему ты так долго не отвечал? Я скучала',
           'Хочу однажды увидеть океан. А ты был?',
           'Какую музыку слушаешь? Я люблю что-то спокойное',
           'Доброе утро! Хорошего тебе дня']

TEXT_OUT = ['Привет! Всё отлично, спасибо что написала',
            'Я тоже недавно, ещё разбираюсь как тут всё устроено',
            'Работаю, читаю, иногда путешествую. А ты?',
            'Выходные люблю проводить за городом',
            'У нас солнечно, но прохладно',
            'Спасибо, ты тоже очень красивая',
            'Прости, был занят на работе, теперь свободен',
            'Был однажды, впечатление на всю жизнь',
            'Слушаю джаз и старый рок',
            'Доброе утро, хорошего дня и тебе']


def env_read() -> dict:
    """Читает `.env` руками: скрипт запускают с хоста, а не внутри контейнера, и
    тянуть ради двух значений всю обвязку конфига незачем."""
    values = {}
    path = Path(__file__).resolve().parent.parent / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()

    return values


def db_connect(args) -> pymysql.connections.Connection:
    """Соединение с базой анкет — по тем же `.env`, что у панели.

    Адрес берётся из конфига, а не подбирается: промахнуться портом на машине с
    двумя десятками поднятых MySQL значит записать тестовые анкеты в чужую базу.
    Флаги оставлены для запуска с хоста, где имя сервиса не разрешается.
    """
    env = env_read()

    return pymysql.connect(
        host=args.host or env.get('MYSQL_HOST') or 'dating_mysql',
        port=int(args.port or env.get('MYSQL_PORT') or 3306),
        user=env.get('MYSQL_USER') or 'developer',
        password=env.get('MYSQL_PASSWORD') or 'password',
        database=env.get('MYSQL_DATABASE') or 'dating',
        charset='utf8mb4',
        autocommit=False)


def seed_clear(connection) -> int:
    """Сносит только посеянное — по пометке. Собеседницы, переписка и журнал
    уходят следом внешними ключами."""
    with connection.cursor() as cursor:
        cursor.execute('DELETE FROM dating_girl WHERE site_id = %s AND note = %s',
                       (SITE_ID, SEED_NOTE))
        removed = cursor.rowcount
    connection.commit()

    return removed


def girl_add(cursor, login: str, name: str, status: str, with_state: bool) -> int:
    """Заводит анкету. Пароль тестовый и наружу всё равно не отдаётся."""
    profile = {'name': name, 'photo': '', 'id': login,
               'about': f'Тестовая анкета {name}. Посеяна scripts/dating_seed.py.'}
    cursor.execute(
        'INSERT INTO dating_girl (site_id, login, password, note, role, auto_answer, '
        '                         storage_state, status, profile) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (SITE_ID, login, 'seed-password', SEED_NOTE, 'lady', 1,
         json.dumps({'cookies': []}) if with_state else None,
         status, json.dumps(profile, ensure_ascii=False)))

    return int(cursor.lastrowid)


def dialog_build(rnd: random.Random, girl_id: int, uid: str, name: str,
                 last_at: datetime, count: int, needs_answer: bool) -> list:
    """Собирает переписку одного диалога.

    Обе стороны намеренно: карточка разворачивает диалог как разговор, и список
    одних только входящих реплик проверять нечего.

    `needs_answer` — последнее слово осталось за собеседницей и ответа не было.
    Часть отвеченных диалогов тоже кончается входящей репликой: агент имеет право
    промолчать, и такой диалог закрыт, хотя написали последними не мы. Не будь их
    в данных, ошибку «считаем по направлению вместо пометки» тест бы не поймал.
    """
    # Реплики идут назад от последней: так последнее сообщение диалога попадает
    # ровно на `last_at`, а по нему и раскладывается свежесть в списке.
    step = timedelta(minutes=rnd.randint(3, 240))
    times = [last_at - step * i for i in range(count)][::-1]

    messages = []
    for index, moment in enumerate(times):
        is_last = index == count - 1
        if is_last:
            direction = 'in' if needs_answer else rnd.choice(['in', 'out'])
        else:
            direction = 'in' if index % 2 == 0 else 'out'

        # Отвечено всё входящее, кроме последней реплики незакрытого диалога.
        answered = 1
        if direction == 'in' and is_last and needs_answer:
            answered = 0

        text = rnd.choice(TEXT_IN if direction == 'in' else TEXT_OUT)
        messages.append({'girl_id': girl_id, 'uid': uid, 'name': name if direction == 'in' else '',
                         'text': text, 'direction': direction,
                         'answered': answered, 'at': moment})

    return messages


def contacts_seed(rnd: random.Random, cursor, girl_id: int, count: int,
                  messages_range: tuple, now: datetime) -> list:
    """Заводит собеседниц анкеты и возвращает их переписку одним списком.

    Свежесть и «нужен ответ» назначаются **независимо**: если бы неотвеченные
    заодно оказались самыми свежими, правильный порядок совпал бы с неправильным,
    и проверять было бы нечего — ошибка сортировки осталась бы невидимой.
    """
    # Каждой собеседнице — своя давность последней реплики, вразброс по трём
    # месяцам, а не «все сегодня».
    ages = [rnd.uniform(0, DAYS_BACK) for _ in range(count)]

    messages = []
    for index in range(count):
        uid = f'seed{girl_id}-{index:04d}'
        name = f'{rnd.choice(NAMES)} {index:03d}'
        last_at = now - timedelta(days=ages[index])
        # Каждая третья ждёт ответа. Признак от давности не зависит вовсе, поэтому
        # неотвеченные раскиданы и по свежим, и по самым старым диалогам.
        needs_answer = index % 3 == 0

        # Часть собеседниц оставляем без строки в `dating_contact`: сайт отдаёт
        # список «кто онлайн» не целиком, и диалог с тем, кого в нём нет, панель
        # обязана показывать по имени из переписки.
        if index % 7 != 6:
            cursor.execute(
                'INSERT INTO dating_contact (girl_id, uid, name, photo, age, city, is_online) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (girl_id, uid, name, '', rnd.randint(21, 45),
                 rnd.choice(CITIES), 1 if index % 5 == 0 else 0))

        messages += dialog_build(rnd, girl_id, uid, name, last_at,
                                 rnd.randint(*messages_range), needs_answer)

    return messages


def messages_write(connection, cursor, messages: list) -> None:
    """Пишет переписку в порядке времени.

    Порядок здесь — не мелочь: свежесть диалога считается по `MAX(id)`, а `id`
    выдаёт автоинкремент. Вставь мы диалоги подряд — «самым свежим» стал бы
    посеянный последним, и сортировка проверялась бы на выдуманном порядке.
    """
    messages.sort(key=lambda m: m['at'])

    # Непрочитанным считаем то, на что не ответили: бейдж «новых сообщений» в
    # списке анкет должен совпасть с тем, что видно в карточке, иначе цифры врут.
    rows = [(m['girl_id'], m['uid'], m['name'], m['text'],
             f"seed-{m['girl_id']}-{index}", m['direction'], m['answered'],
             0 if m['direction'] == 'in' and not m['answered'] else 1,
             m['at'])
            for index, m in enumerate(messages)]

    # Пачками: одним запросом на двадцать тысяч строк упирается `max_allowed_packet`.
    for start in range(0, len(rows), 1000):
        cursor.executemany(
            'INSERT INTO dating_message (girl_id, sender_uid, sender_name, text, '
            '                            message_uid, direction, is_answered, is_read, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)', rows[start:start + 1000])
        connection.commit()


def seed_run(connection, rnd: random.Random) -> dict:
    """Сеет всё: анкеты, собеседниц, переписку. Возвращает счётчики для отчёта."""
    now = datetime.now().replace(microsecond=0)
    total_messages = 0

    with connection.cursor() as cursor:
        # Первая анкета — та самая, ради которой всё: сотни собеседниц. Логин
        # говорящий, чтобы её было видно в списке без поиска.
        big_id = girl_add(cursor, 'seed-000-big@example.com', 'Анкета с сотнями диалогов',
                          'online', True)
        messages = contacts_seed(rnd, cursor, big_id, BIG_CONTACTS_N, BIG_MESSAGES, now)
        connection.commit()
        messages_write(connection, cursor, messages)
        total_messages += len(messages)

        # Остальные — фон: их дело показать, что листается и список анкет.
        for index in range(1, GIRLS_N):
            name = f'{rnd.choice(NAMES)} {index:03d}'
            girl_id = girl_add(cursor, f'seed-{index:03d}@example.com', name,
                               rnd.choice(['online', 'offline', 'error']), index % 2 == 0)
            messages = contacts_seed(rnd, cursor, girl_id, rnd.randint(3, 15), (4, 16), now)
            connection.commit()
            messages_write(connection, cursor, messages)
            total_messages += len(messages)

        cursor.execute('SELECT COUNT(*) FROM dating_girl WHERE note = %s', (SEED_NOTE,))
        girls = cursor.fetchone()[0]

    connection.commit()

    return {'girls': girls, 'big_id': big_id, 'contacts': BIG_CONTACTS_N,
            'messages': total_messages}


def main() -> int:
    parser = argparse.ArgumentParser(description='Тестовые данные DatingAi для локальной разработки')
    parser.add_argument('--clear', action='store_true',
                        help='снести посеянное и выйти')
    # Значения по умолчанию — из `.env`, то есть те, которыми ходит сама панель:
    # скрипт и рассчитан на запуск внутри контейнера. С хоста имя `dating_mysql`
    # не разрешается, а порт другой (`MYSQL_PORT_HOST`) — для этого случая флаги.
    parser.add_argument('--host', default='', help='хост MySQL (по умолчанию MYSQL_HOST из .env)')
    parser.add_argument('--port', default='', help='порт MySQL (по умолчанию MYSQL_PORT из .env; '
                                                   'с хоста нужен MYSQL_PORT_HOST)')
    parser.add_argument('--seed', type=int, default=20260819,
                        help='зерно случайности: один и тот же прогон даёт одни и те же данные')
    args = parser.parse_args()

    connection = db_connect(args)
    try:
        # Пересев всегда начинается со сноса: иначе второй прогон упрётся в
        # уникальный ключ `(site_id, login)` на середине и оставит половину данных.
        removed = seed_clear(connection)
        if removed:
            print(f'снесено посеянных анкет: {removed}')
        if args.clear:
            return 0

        stats = seed_run(connection, random.Random(args.seed))
    finally:
        connection.close()

    print(f"анкет: {stats['girls']}, из них одна с {stats['contacts']} собеседницами "
          f"(id {stats['big_id']}), сообщений: {stats['messages']}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
