# Вкладка «Намерения и действия» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Намерения разговора, которые уже копятся в `chat_intention`, становятся видны человеку: пятая подвкладка «Чата» с тремя колонками — пары, исполненное, пул открытых.

**Architecture:** Столбец `done_note` хранит, чем намерение исполнили. Домен получает две читающие функции для показа (`chat_intention_rows`, `chat_intention_pairs`) рядом с существующей дверью исполнителя `chat_intention_open`. Новый роутер отдаёт их двумя ручками, вкладка рисуется готовой раскладкой `in-cols-3` в `profile.html`.

**Tech Stack:** Python 3.11, FastAPI, MySQL 8 (миграции `yoyo`, накат на старте сервера), Jinja2, ванильный JS в шаблоне, pytest + pytest-asyncio, docker compose.

Спека: `docs/superpowers/specs/2026-10-13-chat-intention-tab-design.md`.

## ⚠ Две поправки к спеке, найденные при планировании

**1. `PROFILE_NAME_SQL` не нужен вовсе, и его не будет.** Спека велела завести
именованный источник для выражения `JSON_EXTRACT(p.profile, '$.name')`, чтобы не
плодить четвёртую копию. Планирование показало, что в списках панели имя анкеты
**не берут из SQL**: запрос выбирает `p.profile AS profile_card, p.login AS
profile_login`, а имя, лицо и номер выводит Python (`json_column_load` →
`profile_name`, `profile_photo`, `profile_uid`). Так устроены `block_rows`,
`mail_reply`, `mailing` (три запроса) и `chat.py` — пять мест. Новая выборка идёт
тем же путём, и вопрос о копии выражения отпадает: его там не будет.

Три SQL-копии, названные в спеке, живут для **сортировки и других страниц** — их
эта работа по-прежнему не трогает.

**2. Читающие функции остаются в домене, как в спеке, — и это проверено на
отсутствие цикла.** `src/dating/chat_intention.py` импортирует только `mysql_`,
`logging_` и (новое) `src.dating.json_column`; обратных ссылок нет. Домен →
роутер здесь не возникает, то есть грабля `site_setting.py` с 73 отложенными
импортами не повторяется.

## Global Constraints

- **Живой HTTP в тестах запрещён** (`.claude/docs/testing.md`, §1.2).
- **Анкета для теста — только фикстуры `girl` / `girl_make`** из `tests/conftest.py`.
- Сайт стенда: `TEST_STAND_SITE = 999_343` (`tests/conftest.py:241`).
- **У каждого файла тестов ровно один маркер**; маркеры в `pyproject.toml`, ключ `markers`.
- **Класс тестов обязан начинаться с `Test`** — иначе pytest его молча не соберёт.
- `asyncio_mode = "auto"` (`pyproject.toml:2`): `async def` тесты и фикстуры **без** декораторов.
- Миграция — **новый файл** `migrations/YYYYMMDDHHMMSS_<что делаем>.sql`; применённые не правят. Накат `yoyo` на старте `dating_uvicorn`.
- Сюита ходит **по HTTP в поднятую панель**; правка `.py` не видна без `docker restart dating_uvicorn`. Шаблоны подхватываются сразу, рестарта не требуют.
- **Запрос к базе руками — только** `PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql '…'`.
- Прогон сюиты: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest …`.
- **Перед прогоном убедиться, что чужого pytest нет:** `pgrep -af "bin/pytest|python -m pytest" | grep -v "until ! pgrep"`. В каталоге работает вторая сессия, и параллельные прогоны роняют оба.
- Комментарии и докстринги — по-русски, в стиле соседних модулей раздела.

---

## File Structure

| Файл | Ответственность |
|---|---|
| `migrations/20261013170000_chat_intention_done_note.sql` (создать) | столбец `done_note` |
| `src/dating/chat_intention.py` (править) | заметка в `chat_intention_done`; две читающие функции для показа |
| `src/uvicorn/routers/dating/chat_intention.py` (создать) | две ручки вкладки |
| `main_uvicorn.py` (править) | импорт и подключение роутера |
| `data/uvicorn/template/admin/dating/profile/site/_base.html` (править) | кнопка вкладки и панель с тремя колонками |
| `data/uvicorn/template/admin/dating/profile.html` (править) | `CHAT_TABS`, `chatSubShow`, загрузка и отрисовка трёх колонок |
| `tests/test_chat_intention.py` (править) | домен: заметка, строки, пары |
| `tests/test_chat_intention_http.py` (создать) | ручки, маркер `core` |
| `tests/test_chat_intention_tab.py` (создать) | вёрстка и привязка вкладки, маркер `page` |
| `.claude/docs/api.md`, `.claude/docs/dating.md`, `.claude/docs/database.md`, `CLAUDE.md` (править) | документация |

Глубоких ссылок на вкладку из ленты событий **не делаем** (`profile.html:14101`
разбирает `tab`/`sub` для чата и фаворитов). Это отдельная работа: событий
«намерение» в ленте пока нет, и адрес вёл бы в пустоту.

---

### Task 1: Столбец `done_note` и заметка при закрытии

**Files:**
- Create: `migrations/20261013170000_chat_intention_done_note.sql`
- Modify: `src/dating/chat_intention.py` (функция `chat_intention_done`)
- Test: `tests/test_chat_intention.py`

**Interfaces:**
- Consumes: таблица `chat_intention` и `chat_intention_put`, `chat_intention_open`, `chat_intention_done` из прошлой работы.
- Produces: `async def chat_intention_done(intention_id: int, note: str = '') -> bool`; столбец `chat_intention.done_note VARCHAR(500) NOT NULL DEFAULT ''`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/test_chat_intention.py`:

```python
class TestTheDoneNote:
    """Чем намерение исполнили — словами, в той же строке."""

    async def test_the_note_lands(self, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]

        assert await chat_intention_done(
            one['id'], note='отправлено фото public/a.png') is True

        async with mysql_get_db_async() as db:
            await db.execute('SELECT done_note FROM chat_intention WHERE id = %s',
                             (one['id'],))
            row = dict(await db.fetchone())

        assert row['done_note'] == 'отправлено фото public/a.png'

    async def test_without_a_note_the_old_contract_holds(self, girl):
        """⚠ Довод необязательный: прежние вызовы и их тесты целы."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]

        assert await chat_intention_done(one['id']) is True

        async with mysql_get_db_async() as db:
            await db.execute('SELECT done_note FROM chat_intention WHERE id = %s',
                             (one['id'],))
            row = dict(await db.fetchone())

        assert row['done_note'] == ''

    async def test_a_long_note_is_cut_to_the_column(self, girl):
        """⚠ Пятьсот знаков — предел столбца. Обрезаем **мы**, а не база: в
        строгом режиме MySQL такая вставка падает ошибкой, и закрытие намерения
        сорвалось бы из-за длинного текста исполнителя."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]

        assert await chat_intention_done(one['id'], note='x' * 600) is True

        async with mysql_get_db_async() as db:
            await db.execute('SELECT done_note FROM chat_intention WHERE id = %s',
                             (one['id'],))
            row = dict(await db.fetchone())

        assert len(row['done_note']) == 500

    async def test_the_second_mark_leaves_the_first_note(self, girl):
        """Закрыли дважды — вторая отметка не наша, и заметку она не трогает."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]
        await chat_intention_done(one['id'], note='первое')

        assert await chat_intention_done(one['id'], note='второе') is False

        async with mysql_get_db_async() as db:
            await db.execute('SELECT done_note FROM chat_intention WHERE id = %s',
                             (one['id'],))
            row = dict(await db.fetchone())

        assert row['done_note'] == 'первое'
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention.py -k TestTheDoneNote -q -p no:randomly --no-header`

Expected: FAIL — `Unknown column 'done_note' in 'field list'`.

- [ ] **Step 3: Написать миграцию**

Создать `migrations/20261013170000_chat_intention_done_note.sql`:

```sql
-- Чем намерение исполнили: `chat_intention.done_note` (2026-10-13)
--
-- ЗАЧЕМ. В строке есть «исполнено ли» (`status`) и «когда» (`done_at`), но нет
-- самого главного для человека — **что сделали**. Вкладка «Намерения и действия»
-- показывает исполненное отдельной колонкой, и без этого столбца ей нечего в неё
-- положить: она отвечала бы «обещание закрыто» и молчала о том, чем.
--
-- ⚠ ОДИН СТОЛБЕЦ, А НЕ ТАБЛИЦА ДЕЙСТВИЙ. Отдельная `chat_intention_action` дала
-- бы историю из нескольких действий на одно намерение. Но исполнителя ещё нет, и
-- формы его работы мы не знаем: заведённая сейчас таблица подогнала бы его под
-- догадку. Одно намерение — одна строка — один исход; понадобится история, её
-- заведут тогда, когда будет известно, из чего она состоит.
--
-- ⚠⚠ `NOT NULL DEFAULT ''` ЗДЕСЬ ЗАКОННО, в отличие от грабли с `updated_at` у
-- `favorite_mail`. Там `DEFAULT CURRENT_TIMESTAMP` приписал всем накопленным
-- строкам время наката, и письмо суточной давности показалось сегодняшним — то
-- есть умолчание **врало**. Пустая строка не врёт: «не записали» — правда для
-- всех старых строк, их никто не исполнял.
--
-- ⚠ Длина 500, как у `error` в пулах: это строка для глаза, а не метка.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'chat_intention'
           AND COLUMN_NAME = 'done_note') AS столбец_есть,
       (SELECT COUNT(*) FROM `chat_intention`) AS строк;

-- ------------------------------------------------- Чем исполнили
SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'chat_intention'
                  AND COLUMN_NAME = 'done_note') = 0,
              'ALTER TABLE `chat_intention`
                   ADD COLUMN `done_note` VARCHAR(500) NOT NULL DEFAULT ''''
                       COMMENT ''Что сделали по намерению: словами, для вкладки''
                       AFTER `done_at`',
              'SELECT ''столбец уже есть''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'chat_intention'
           AND COLUMN_NAME = 'done_note') AS столбец_есть;
```

- [ ] **Step 4: Править `chat_intention_done`**

В `src/dating/chat_intention.py` добавить рядом с `CHAT_INTENTION_LENGTH_MAX`:

```python
# Предел заметки об исполнении, знаков — длина столбца `done_note`.
#
# ⚠⚠ Режем **мы**, а не база. MySQL в строгом режиме отвергает длинную вставку
# ошибкой, и закрытие намерения сорвалось бы из-за того, что исполнитель написал
# в заметке лишнего. Дело уже сделано — терять отметку из-за текста нельзя.
CHAT_INTENTION_NOTE_MAX = 500
```

Заменить тело `chat_intention_done` целиком:

```python
async def chat_intention_done(intention_id: int, note: str = '') -> bool:
    """Отмечает намерение исполненным.

    Args:
        intention_id: номер строки `chat_intention`.
        note: чем исполнили, словами — «отправлено фото public/a.png». Пусто
            законно: исполнитель может не иметь что сказать.

    Returns:
        bool: `True` — отметка наша; `False` — строки нет либо её уже закрыли,
        и это не ошибка.

    ## ⚠⚠ Условный `UPDATE`, а не вопрос «ещё открыта?» отдельным запросом

    Исполнителей может быть несколько — круг и кнопка, два воркера, — и между
    вопросом и записью они разошлись бы: оба увидели бы открытую строку и оба
    сделали бы дело. Условие стоит **внутри** записи, а `rowcount` отвечает,
    чья это отметка. Тем же приёмом живёт `invite_send`.

    ⚠ Отметка **отпускает ключ**: `open_intention` гаснет в `NULL`, и следующее
    такое же обещание той же паре ложится своей строкой.

    ⚠ Заметка не нашей отметки никуда не пишется: условие одно на все столбцы, и
    второй исполнитель, опоздавший на миг, не затирает рассказ первого.
    """
    if int(intention_id or 0) <= 0:
        return False

    said = str(note or '').strip()[:CHAT_INTENTION_NOTE_MAX]

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_intention"
            "   SET status = 'done', done_at = NOW(), done_note = %s"
            " WHERE id = %s AND status = 'open'",
            (said, int(intention_id)))
        done = int(db.rowcount or 0)
        await db.connection.commit()

    return bool(done)
```

- [ ] **Step 5: Накатить и прогнать**

Run: `docker restart dating_uvicorn && sleep 15 && ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention.py -q -p no:randomly --no-header`

Expected: PASS, 28 тестов (24 прежних + 4 новых).

- [ ] **Step 6: Проверить схему**

Run:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql \
  "SHOW COLUMNS FROM chat_intention LIKE 'done_note'"
```

Expected: одна строка, `varchar(500)`, `NO` в колонке Null, умолчание — пустая строка.

- [ ] **Step 7: Коммит**

```bash
git add migrations/20261013170000_chat_intention_done_note.sql \
        src/dating/chat_intention.py tests/test_chat_intention.py
git commit -m "Намерения: чем исполнили — столбец done_note"
```

---

### Task 2: `chat_intention_rows` — строки для показа

**Files:**
- Modify: `src/dating/chat_intention.py`
- Test: `tests/test_chat_intention.py`

**Interfaces:**
- Consumes: `chat_intention_put`, `chat_intention_open`, `chat_intention_done(id, note='')` (Task 1).
- Produces: `async def chat_intention_rows(site_id: int, profile_id: int = 0, uid: str = '', status: str = '', page: int = 1, per: int = 0) -> dict` — `{'rows': [...], 'page': int, 'pages': int, 'total': int}`. В строке: `id`, `profile_id`, `site_id`, `uid`, `intention`, `status`, `created_at`, `done_at`, `done_note`, `name`, `photo`, `profile_name`, `profile_photo`, `profile_uid`. Все даты — строками.

- [ ] **Step 1: Написать падающие тесты**

В шапке `tests/test_chat_intention.py` расширить импорт:

```python
from src.dating.chat_intention import (chat_intention_done,
                                       chat_intention_open, chat_intention_pairs,
                                       chat_intention_put, chat_intention_rows)
```

⚠ `chat_intention_pairs` появится только в Task 3 — до него этот импорт падает,
и так и задумано: обе читающие функции живут в одном модуле и в одном файле
тестов.

Добавить в конец файла:

```python
class TestTheRows:
    """Строки для показа: с именами, страницами и счётом."""

    async def test_it_gives_the_row_with_names(self, girl):
        uid = _uid()
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO companion (site_id, uid, name, photo)'
                ' VALUES (%s, %s, %s, %s)',
                (TEST_STAND_SITE, uid, 'Bruce', 'http://x/b.jpg'))
            await db.connection.commit()

        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl)

        assert got['total'] == 1
        assert got['rows'][0]['name'] == 'Bruce'
        assert got['rows'][0]['photo'] == 'http://x/b.jpg'
        assert got['rows'][0]['intention'] == PROMISE

    async def test_a_companion_without_a_card_is_not_empty(self, girl):
        """⚠ Карточка заводится по требованию — её может не быть. Тогда в строке
        остаётся номер, а не пустое место: по пустому собеседника не узнать."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl)

        assert got['rows'][0]['name'] == uid

    async def test_it_filters_by_status(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 [PROMISE, 'he_asked_not_to_write'])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]
        await chat_intention_done(one['id'], note='сделано')

        done = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                         status='done')
        openi = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                          status='open')

        assert [row['done_note'] for row in done['rows']] == ['сделано']
        assert done['total'] == 1 and openi['total'] == 1

    async def test_it_filters_by_companion(self, girl):
        mine = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, mine, [PROMISE])
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                        uid=mine)

        assert got['total'] == 1
        assert got['rows'][0]['uid'] == mine

    async def test_a_profile_of_zero_means_all_of_them(self, girl, girl_make):
        """⚠ Анкета — фильтр, а не условие показа. Общее правило раздела."""
        other = await girl_make()
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        await chat_intention_put(other, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_rows(TEST_STAND_SITE)

        assert {row['profile_id'] for row in got['rows']} >= {girl, other}

    async def test_pages_are_counted_by_the_server(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 ['a_one', 'a_two', 'a_three'])

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl, per=2)

        assert got['total'] == 3
        assert got['pages'] == 2
        assert len(got['rows']) == 2

    async def test_a_page_past_the_end_is_fixed_not_refused(self, girl):
        """⚠ Строку могли закрыть с последней страницы: отказ вместо предыдущей
        читался бы как поломка. То же правило, что у журнала доставки."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                        page=99)

        assert got['page'] == 1
        assert len(got['rows']) == 1

    async def test_without_a_site_it_gives_nothing(self):
        got = await chat_intention_rows(0)

        assert got == {'rows': [], 'page': 1, 'pages': 1, 'total': 0}

    async def test_dates_come_as_strings(self, girl):
        """Страница сдвигает время сама (`time.js`), и ей нужна строка, а не
        объект, который JSON не переживёт."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl)

        assert isinstance(got['rows'][0]['created_at'], str)
        assert got['rows'][0]['created_at']
        assert got['rows'][0]['done_at'] == ''
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention.py -q -p no:randomly --no-header`

Expected: collection error — `ImportError: cannot import name 'chat_intention_pairs'`.

Чтобы увидеть именно красноту `TestTheRows`, временно убрать `chat_intention_pairs`
из импорта, прогнать, вернуть обратно. Ожидание без него: FAIL —
`cannot import name 'chat_intention_rows'`.

- [ ] **Step 3: Написать функцию**

Добавить в `src/dating/chat_intention.py` — импорт в шапке модуля:

```python
from src.dating.json_column import json_column_load
```

и функцию в конец файла:

```python
# Строк на странице по умолчанию. Столько же, сколько у чёрного списка: списки
# соседние по виду, и разная высота читалась бы как разная важность.
CHAT_INTENTION_PAGE = 50


def _intention_row(row: dict) -> dict:
    """Строка базы → строка для страницы: имена, лица, даты строками.

    ## ⚠ Имя анкеты выводится здесь, а не выражением в SQL

    В списках панели так заведено везде (`block_rows`, `mail_reply`, `mailing`,
    `chat.py`): запрос берёт `p.profile` целиком, а имя, лицо и номер на сайте
    достаёт Python. Выражение `JSON_EXTRACT` в запросе стояло бы пятой копией, и
    ради него пришлось бы ещё и присоединять карточку второй раз.

    ⚠ Собеседник без карточки в `companion` отдаётся **номером**, а не пустотой:
    карточка заводится по требованию, и по пустому месту человека не узнать.
    """
    got = dict(row)
    card = json_column_load(got.pop('profile_card', None)) or {}

    got['profile_name'] = str(card.get('name')
                              or got.pop('profile_login', '') or '')
    got.pop('profile_login', None)
    got['profile_photo'] = str(card.get('photo') or '')
    got['profile_uid'] = str(card.get('id') or '')

    got['name'] = str(got.get('name') or '') or str(got.get('uid') or '')
    got['photo'] = str(got.get('photo') or '')
    got['done_note'] = str(got.get('done_note') or '')

    for key in ('created_at', 'done_at'):
        got[key] = str(got[key]) if got.get(key) else ''

    return got


async def chat_intention_rows(site_id: int, profile_id: int = 0, uid: str = '',
                              status: str = '', page: int = 1,
                              per: int = 0) -> dict:
    """Намерения для показа: страницей, с именами обеих сторон.

    Args:
        site_id: сайт. Ноль — пусто, как у `chat_intention_open`.
        profile_id: сузить до анкеты. Ноль — все анкеты сайта.
        uid: сузить до собеседника. Пусто — все.
        status: `open` или `done`. Пусто — и те, и другие.
        page: страница, с единицы. За концом — поправится сама.
        per: строк на странице. Ноль — `CHAT_INTENTION_PAGE`.

    Returns:
        dict: `rows`, `page`, `pages`, `total`.

    ## ⚠ Это не дубль `chat_intention_open`, у них разные договоры

    `chat_intention_open` — дверь **для исполнителя**: минимум полей, без имён и
    страниц, зато с `limit`. Здесь показ: имена, лица, счёт и страницы. Слей их,
    и исполнитель платил бы двумя присоединениями за то, чего не читает.

    ⚠ **Страницы считает сервер.** Список копится сам и не убывает — намерение не
    истекает. Отданный целиком, он кончается молча, и конец прокрутки читается как
    конец списка.

    ⚠ Порядок зависит от отбора: исполненные — свежие сверху (человек смотрит,
    что сделано последним), открытые — старые сверху (старое обещание исполняют
    раньше нового).
    """
    site_id = int(site_id or 0)
    empty = {'rows': [], 'page': 1, 'pages': 1, 'total': 0}

    if not site_id:
        return empty

    where = (' WHERE i.site_id = %s'
             '   AND (%s = 0 OR i.profile_id = %s)'
             "   AND (%s = '' OR i.uid = %s)"
             "   AND (%s = '' OR i.status = %s)")
    pid = int(profile_id or 0)
    who = str(uid or '').strip()
    what = str(status or '').strip()
    args = (site_id, pid, pid, who, who, what, what)

    order = (' ORDER BY i.done_at DESC, i.id DESC' if what == 'done'
             else ' ORDER BY i.id')
    per = max(1, int(per or 0) or CHAT_INTENTION_PAGE)

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM chat_intention i' + where,
                             args)
            total = int(((await db.fetchone()) or {}).get('n') or 0)

            pages = max(1, -(-total // per))
            page = min(max(int(page or 1), 1), pages)

            await db.execute(
                'SELECT i.id, i.profile_id, i.site_id, i.uid, i.intention,'
                '       i.status, i.created_at, i.done_at, i.done_note,'
                '       comp.name, comp.photo,'
                '       p.profile AS profile_card, p.login AS profile_login'
                '  FROM chat_intention i'
                '  JOIN profile p ON p.id = i.profile_id'
                '  LEFT JOIN companion comp'
                '    ON comp.site_id = i.site_id AND comp.uid = i.uid'
                + where + order + ' LIMIT %s OFFSET %s',
                (*args, per, (page - 1) * per))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] намерения сайта #{site_id} не прочитались: {e}')

        return empty

    return {'rows': [_intention_row(row) for row in (rows or [])],
            'page': page, 'pages': pages, 'total': total}
```

- [ ] **Step 4: Прогнать**

Run: `docker restart dating_uvicorn && sleep 12 && ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention.py -k TestTheRows -q -p no:randomly --no-header`

Expected: PASS, 9 тестов.

- [ ] **Step 5: Коммит**

```bash
git add src/dating/chat_intention.py tests/test_chat_intention.py
git commit -m "Намерения: строки для показа с именами и страницами"
```

---

### Task 3: `chat_intention_pairs` — колонка пар

**Files:**
- Modify: `src/dating/chat_intention.py`
- Test: `tests/test_chat_intention.py`

**Interfaces:**
- Consumes: `_intention_row`, `CHAT_INTENTION_PAGE` (Task 2).
- Produces: `async def chat_intention_pairs(site_id: int, profile_id: int = 0, page: int = 1, per: int = 0) -> dict` — `{'rows', 'page', 'pages', 'total'}`. В строке: `profile_id`, `site_id`, `uid`, `name`, `photo`, `profile_name`, `profile_photo`, `profile_uid`, `open_count`, `done_count`, `last_at` (строкой).

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_chat_intention.py`:

```python
class TestThePairs:
    """Колонка пар: по строке на пару, со счётчиками."""

    async def test_one_row_per_pair_with_counts(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 [PROMISE, 'he_asked_not_to_write'])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]
        await chat_intention_done(one['id'], note='сделано')

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert got['total'] == 1
        assert got['rows'][0]['uid'] == uid
        assert got['rows'][0]['open_count'] == 1
        assert got['rows'][0]['done_count'] == 1

    async def test_counts_are_numbers_not_decimals(self, girl):
        """⚠ `SUM()` в MySQL отдаёт `Decimal`, а JSON его не переживёт: ручка
        упала бы на сериализации, а не на запросе."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert isinstance(got['rows'][0]['open_count'], int)
        assert isinstance(got['rows'][0]['done_count'], int)

    async def test_two_companions_are_two_rows(self, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert got['total'] == 2

    async def test_a_pair_without_intentions_is_not_listed(self, girl):
        """Вкладка отвечает на «что агент наобещал». Пары без намерений — шум."""
        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert got['rows'] == []
        assert got['total'] == 0

    async def test_a_profile_of_zero_means_all_of_them(self, girl, girl_make):
        other = await girl_make()
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        await chat_intention_put(other, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_pairs(TEST_STAND_SITE)

        assert {row['profile_id'] for row in got['rows']} >= {girl, other}

    async def test_the_companion_name_comes_through(self, girl):
        uid = _uid()
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO companion (site_id, uid, name) VALUES (%s, %s, %s)',
                (TEST_STAND_SITE, uid, 'Mike'))
            await db.connection.commit()

        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert got['rows'][0]['name'] == 'Mike'

    async def test_pages_are_counted_by_the_server(self, girl):
        for _ in range(3):
            await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl, per=2)

        assert got['total'] == 3
        assert got['pages'] == 2
        assert len(got['rows']) == 2

    async def test_without_a_site_it_gives_nothing(self):
        got = await chat_intention_pairs(0)

        assert got == {'rows': [], 'page': 1, 'pages': 1, 'total': 0}
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention.py -q -p no:randomly --no-header`

Expected: collection error — `ImportError: cannot import name 'chat_intention_pairs'`.

- [ ] **Step 3: Написать функцию**

Добавить в конец `src/dating/chat_intention.py`:

```python
async def chat_intention_pairs(site_id: int, profile_id: int = 0, page: int = 1,
                               per: int = 0) -> dict:
    """Пары, у которых есть намерения: по строке на пару, свежие сверху.

    Args:
        site_id: сайт. Ноль — пусто.
        profile_id: сузить до анкеты. Ноль — все анкеты сайта.
        page: страница, с единицы. За концом — поправится сама.
        per: строк на странице. Ноль — `CHAT_INTENTION_PAGE`.

    Returns:
        dict: `rows`, `page`, `pages`, `total`. В строке — обе стороны,
        `open_count`, `done_count` и `last_at` (время последнего намерения).

    ⚠ **Пары без намерений сюда не попадают по построению**: группировка идёт по
    самой таблице намерений. Вкладка отвечает на вопрос «что агент наобещал», и
    пары, где он не обещал ничего, — шум; их на живом сайте сотни.

    ⚠⚠ Счётчики приводятся к `int` **здесь**: `SUM()` в MySQL отдаёт `Decimal`,
    и ручка упала бы не на запросе, а на сериализации ответа в JSON — то есть
    жаловалась бы не на то.
    """
    site_id = int(site_id or 0)
    empty = {'rows': [], 'page': 1, 'pages': 1, 'total': 0}

    if not site_id:
        return empty

    where = (' WHERE i.site_id = %s AND (%s = 0 OR i.profile_id = %s)')
    pid = int(profile_id or 0)
    args = (site_id, pid, pid)
    per = max(1, int(per or 0) or CHAT_INTENTION_PAGE)

    try:
        async with mysql_get_db_async() as db:
            # ⚠ Счёт **пар**, а не строк: `COUNT(DISTINCT …)` по двум столбцам
            # считает именно их, и подзапрос ради этого не нужен.
            await db.execute(
                'SELECT COUNT(DISTINCT i.profile_id, i.uid) AS n'
                '  FROM chat_intention i' + where, args)
            total = int(((await db.fetchone()) or {}).get('n') or 0)

            pages = max(1, -(-total // per))
            page = min(max(int(page or 1), 1), pages)

            await db.execute(
                'SELECT i.profile_id, i.site_id, i.uid,'
                "       SUM(i.status = 'open') AS open_count,"
                "       SUM(i.status = 'done') AS done_count,"
                '       MAX(i.created_at) AS last_at,'
                '       comp.name, comp.photo,'
                '       p.profile AS profile_card, p.login AS profile_login'
                '  FROM chat_intention i'
                '  JOIN profile p ON p.id = i.profile_id'
                '  LEFT JOIN companion comp'
                '    ON comp.site_id = i.site_id AND comp.uid = i.uid'
                + where +
                ' GROUP BY i.profile_id, i.site_id, i.uid,'
                '          comp.name, comp.photo, p.profile, p.login'
                ' ORDER BY last_at DESC, i.profile_id, i.uid'
                ' LIMIT %s OFFSET %s',
                (*args, per, (page - 1) * per))
            rows = await db.fetchall()
    except Exception as e:
        logger_info(f'[dating] пары намерений сайта #{site_id} не прочитались: {e}')

        return empty

    out = []
    for row in rows or []:
        got = _intention_row(row)
        got['open_count'] = int(got.get('open_count') or 0)
        got['done_count'] = int(got.get('done_count') or 0)
        got['last_at'] = str(got['last_at']) if got.get('last_at') else ''
        out.append(got)

    return {'rows': out, 'page': page, 'pages': pages, 'total': total}
```

- [ ] **Step 4: Прогнать**

Run: `docker restart dating_uvicorn && sleep 12 && ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention.py -q -p no:randomly --no-header`

Expected: PASS, 45 тестов.

- [ ] **Step 5: Коммит**

```bash
git add src/dating/chat_intention.py tests/test_chat_intention.py
git commit -m "Намерения: колонка пар со счётчиками"
```

---

### Task 4: Ручки вкладки

**Files:**
- Create: `src/uvicorn/routers/dating/chat_intention.py`
- Modify: `main_uvicorn.py` (импорт рядом со строкой 65, подключение рядом со строкой 322)
- Test: `tests/test_chat_intention_http.py` (создать)

**Interfaces:**
- Consumes: `chat_intention_rows`, `chat_intention_pairs` (Tasks 2–3).
- Produces: `GET /api/dating/chat-intention/pairs` и `GET /api/dating/chat-intention`, обе отдают `{'result': [...], 'page': int, 'pages': int, 'total': int}`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_chat_intention_http.py`:

```python
"""Ручки вкладки «Намерения и действия».

Две ручки на три колонки: пары отдельно, строки — одной ручкой с доводом
`status`. Своя дверь, а не хвост чата: вкладка живёт в «Чате», но намерения
приезжают и из писем.
"""
import uuid

import pytest

from src.dating.chat_intention import chat_intention_put
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.core

PROMISE = 'she_promised_send_photo'
PAIRS = '/api/dating/chat-intention/pairs'
ROWS = '/api/dating/chat-intention'


def _uid() -> str:
    return f'cih-{uuid.uuid4().hex[:8]}'


class TestThePairsHandle:
    """`GET /api/dating/chat-intention/pairs`."""

    async def test_it_answers_with_the_pager_shape(self, auth_client, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await auth_client.get(
            f'{PAIRS}?site_id={TEST_STAND_SITE}&profile_id={girl}')

        assert got.status_code == 200
        body = got.json()
        assert body['total'] == 1
        assert body['page'] == 1 and body['pages'] == 1
        assert body['result'][0]['open_count'] == 1

    async def test_without_a_site_it_is_empty_not_an_error(self, auth_client):
        """⚠ Ноль — «сайт не выбран», а не поломка: селект пуст, пока едет его
        список, и красная ошибка на пустом месте пугала бы зря."""
        got = await auth_client.get(PAIRS)

        assert got.status_code == 200
        assert got.json()['result'] == []


class TestTheRowsHandle:
    """`GET /api/dating/chat-intention`."""

    async def test_status_open_gives_the_pool(self, auth_client, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        got = await auth_client.get(
            f'{ROWS}?site_id={TEST_STAND_SITE}&profile_id={girl}&status=open')

        assert got.status_code == 200
        body = got.json()
        assert [row['intention'] for row in body['result']] == [PROMISE]
        assert body['result'][0]['uid'] == uid

    async def test_status_done_is_empty_while_nobody_executes(self, auth_client,
                                                              girl):
        """Исполнителя нет — исполненных не бывает. Вкладка об этом и говорит."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await auth_client.get(
            f'{ROWS}?site_id={TEST_STAND_SITE}&profile_id={girl}&status=done')

        assert got.json()['result'] == []

    async def test_it_narrows_to_one_companion(self, auth_client, girl):
        mine = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, mine, [PROMISE])
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await auth_client.get(
            f'{ROWS}?site_id={TEST_STAND_SITE}&profile_id={girl}&uid={mine}')

        assert [row['uid'] for row in got.json()['result']] == [mine]

    async def test_a_stranger_is_turned_away(self, client, girl):
        """Без входа — отказ. Права как у пулов: всем **вошедшим**."""
        got = await client.get(f'{ROWS}?site_id={TEST_STAND_SITE}')

        assert got.status_code in (401, 403)
```

Фикстура `client` (без входа) есть в `tests/conftest.py:466` — проверено.

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention_http.py -q -p no:randomly --no-header`

Expected: FAIL — ответы `404`, маршрута ещё нет.

- [ ] **Step 3: Написать роутер**

Создать `src/uvicorn/routers/dating/chat_intention.py`:

```python
"""Намерения разговора: две ручки вкладки «Намерения и действия».

Правила самих намерений — в `src/dating/chat_intention.py`; здесь только дверь.

⚠ **Своя дверь, а не хвост чата.** Вкладка живёт в полосе «Чата», но намерения
приезжают и из писем — их пишет одна точка `_agent_ask` на все четыре канала.
Повесь мы ручку на `/api/dating/chat`, адрес обещал бы только чат.

⚠ **Права — как у пулов, всем вошедшим.** Это работа оператора: он смотрит, что
агент наобещал и что из этого сделано. Админского здесь нет ничего — ни адресов,
ни паролей.
"""

from fastapi import APIRouter, Depends

from src.auth.auth import AuthUser, auth_required
from src.dating.chat_intention import chat_intention_pairs, chat_intention_rows

router = APIRouter(prefix='/api/dating/chat-intention', tags=['dating'])


@router.get('/pairs', include_in_schema=False)
async def chat_intention_pairs_list(site_id: int = 0, profile_id: int = 0,
                                    page: int = 1, per: int = 0,
                                    user: AuthUser = Depends(auth_required)):
    """Пары с намерениями — левая колонка вкладки.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не пары всех сайтов.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.
        page: страница, с единицы. За концом — поправится сама.
        per: строк на странице. Ноль — предел по умолчанию.

    Returns:
        dict: `result` — пары страницы, рядом `page`, `pages`, `total`.
    """
    got = await chat_intention_pairs(int(site_id or 0), int(profile_id or 0),
                                     int(page or 1), int(per or 0))

    return {'result': got['rows'], 'page': got['page'], 'pages': got['pages'],
            'total': got['total']}


@router.get('', include_in_schema=False)
async def chat_intention_list(site_id: int = 0, profile_id: int = 0,
                              uid: str = '', status: str = '',
                              page: int = 1, per: int = 0,
                              user: AuthUser = Depends(auth_required)):
    """Намерения страницей — средняя и правая колонки вкладки.

    Args:
        site_id: сайт-закладка. Ноль — пустой список.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.
        uid: сузить до собеседника. Пусто — все.
        status: `done` — средняя колонка (что сделано), `open` — правая (пул).
            Пусто — и те, и другие.
        page: страница, с единицы.
        per: строк на странице. Ноль — предел по умолчанию.

    Returns:
        dict: `result` — записи страницы, рядом `page`, `pages`, `total`.

    ⚠ **Одна ручка на две колонки.** Они спрашивают одно и то же разными
    доводами; вторая ручка держала бы две копии отбора и разошлась бы с первой.
    """
    got = await chat_intention_rows(int(site_id or 0), int(profile_id or 0),
                                    str(uid or ''), str(status or ''),
                                    int(page or 1), int(per or 0))

    return {'result': got['rows'], 'page': got['page'], 'pages': got['pages'],
            'total': got['total']}
```

- [ ] **Step 4: Подключить роутер**

В `main_uvicorn.py` после строки 65 (`from src.uvicorn.routers.dating import block as dating_block_router`) добавить:

```python
from src.uvicorn.routers.dating import chat_intention as dating_chat_intention_router
```

И после строки 322 (`app.include_router(dating_block_router.router)`):

```python
app.include_router(dating_chat_intention_router.router)
```

- [ ] **Step 5: Прогнать**

Run: `docker restart dating_uvicorn && sleep 15 && ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention_http.py -q -p no:randomly --no-header`

Expected: PASS, 6 тестов.

- [ ] **Step 6: Коммит**

```bash
git add src/uvicorn/routers/dating/chat_intention.py main_uvicorn.py \
        tests/test_chat_intention_http.py
git commit -m "Намерения: ручки вкладки"
```

---

### Task 5: Сама вкладка

**Files:**
- Modify: `data/uvicorn/template/admin/dating/profile/site/_base.html` (кнопка после строки 130, панель после строки 500)
- Modify: `data/uvicorn/template/admin/dating/profile.html` (`CHAT_TABS` — строка 5657; `chatSubShow` — строка ~5696; привязки — рядом со строкой 4905; код колонок — в конце блока «Не пишем», около строки 6480)
- Test: `tests/test_chat_intention_tab.py` (создать)

**Interfaces:**
- Consumes: `GET /api/dating/chat-intention/pairs` и `GET /api/dating/chat-intention` (Task 4).
- Produces: подвкладка `intention` в `CHAT_TABS`, панель `data-chat-pane="intention"`, функция `intentionShow()`.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_chat_intention_tab.py`:

```python
"""Вкладка «Намерения и действия»: вёрстка и привязка.

⚠ Сюита ходит по HTTP и браузера не имеет — щёлкнуть по вкладке она не может.
Поэтому сторожим то, что видно в ответе: порядок узлов и наличие привязки.
Нарисованная, но ни к чему не привязанная вкладка — тот случай, который тише
сломанного теста.
"""
import pytest

pytestmark = pytest.mark.page
PAGE = '/admin/dating/profile'


class TestTheTabButton:
    """Кнопка в полосе «Чата»."""

    async def test_the_button_is_there(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert 'data-chat-tab="intention"' in page, 'кнопки вкладки нет'
        assert 'Намерения и действия' in page, 'подписи вкладки нет'

    async def test_it_stands_after_the_block_tab(self, auth_client):
        """⚠ Порядок — конец пути «Чата»: позвали → ушло ли → поговорили →
        этим не пишем → а вот что наобещали."""
        page = (await auth_client.get(PAGE)).text

        block = page.find('data-chat-tab="block"')
        mine = page.find('data-chat-tab="intention"')

        assert block != -1 and mine != -1, 'вкладок нет — разметка сломана'
        assert block < mine, 'вкладка стоит раньше «Не пишем»'

    async def test_the_name_is_known_to_the_script(self, auth_client):
        """⚠⚠ Главный сторож: кнопка, которой нет в `CHAT_TABS`, нарисована и
        мертва — щелчок по ней не покажет ничего, и тест на наличие узла это
        пропустит."""
        page = (await auth_client.get(PAGE)).text

        assert "const CHAT_TABS = ['invite', 'sent', 'talk', 'block', 'intention']" \
            in page, 'имя вкладки не попало в CHAT_TABS'


class TestThePane:
    """Панель с тремя колонками."""

    async def test_the_pane_is_there(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert 'data-chat-pane="intention"' in page, 'панели вкладки нет'

    async def test_it_has_three_columns_and_two_splits(self, auth_client):
        """Раскладка общая с «Диалогами» (`in-cols-3`): вкладки об одном, и две
        разные сетки на одну работу разошлись бы с первой правки."""
        page = (await auth_client.get(PAGE)).text
        start = page.find('data-chat-pane="intention"')
        end = page.find('data-chat-pane', start + 10)
        pane = page[start:end if end != -1 else len(page)]

        assert 'in-cols in-cols-3' in pane, 'раскладка не трёхколонная'
        assert pane.count('data-slot="intention-pairs-list"') == 1
        assert pane.count('data-slot="intention-done-list"') == 1
        assert pane.count('data-slot="intention-pool-list"') == 1
        assert pane.count('split-reply') == 1 and pane.count('split-initiative') == 1

    async def test_every_column_has_a_pager(self, auth_client):
        """⚠ Список, отданный целиком, кончается молча: конец прокрутки читается
        как конец списка. Эта грабля уже стоила трёхсот невидимых переписок."""
        page = (await auth_client.get(PAGE)).text

        for name in ('intention-pairs-pager', 'intention-done-pager',
                     'intention-pool-pager'):
            assert f'data-slot="{name}"' in page, f'у колонки нет пейджера: {name}'

    async def test_the_loader_is_wired(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert "if (chatTab === 'intention') intentionShow();" in page, \
            'подвкладка не наполняется — ветки в chatSubShow нет'
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention_tab.py -q -p no:randomly --no-header`

Expected: FAIL, все 7 тестов.

- [ ] **Step 3: Кнопка вкладки**

В `data/uvicorn/template/admin/dating/profile/site/_base.html` после строки 130
(`<button type="button" class="page-tab" data-chat-tab="block">Не пишем</button>`)
добавить:

```html
        {# ⚠ «Намерения и действия» — за «Не пишем», и это продолжение того же
           пути: позвали → посмотрели, ушло ли → поговорили → с этими не
           разговариваем → а вот что в разговорах наобещано.

           ⚠ Намерения рождаются и в чате, и в письмах — их пишет одна точка
           `_agent_ask`. Вкладка стоит здесь, а не в «Фаворитах», потому что
           намерение про **разговор**: среди «Приставания» и «Доставки» оно
           читалось бы как «намерение по письмам». Второго места показа нет
           намеренно — отбор был бы тот же самый, то есть два одинаковых экрана. #}
        <button type="button" class="page-tab"
                data-chat-tab="intention">Намерения и действия</button>
```

- [ ] **Step 4: Панель с тремя колонками**

В том же файле после закрытия панели «Не пишем» (`{% endblock %}` и `</div>`
блока `data-chat-pane="block"`, около строки 500) добавить:

```html
    {# ⚠ Колонок три, и порядок в них — порядок работы: кому обещали → что
       сделано → что ещё ждёт. Раскладка общая с «Диалогами» (`in-cols-3`), и
       разделители те же классы (`split-reply`, `split-initiative`): их
       перетаскивание уже привязано обходом по всей панели работы, и своего кода
       новой вкладке не нужно. #}
    <div class="chat-pane off" data-chat-pane="intention">
        {% block work_chat_intention %}
        <div class="in-cols in-cols-3">
            <div class="in-col">
                <div class="in-head">
                    <span class="in-count" data-slot="intention-pairs-count"></span>
                    <button type="button" class="people-reload"
                            data-slot="intention-pairs-reload"
                            title="Перечитать пары">⟳</button>
                </div>
                <div class="in-list talk-cards"
                     data-slot="intention-pairs-list"></div>
                <div class="pane-foot" data-slot="intention-pairs-pager"></div>
            </div>

            <div class="split split-reply"
                 title="Потяните, чтобы изменить ширину"></div>

            <div class="in-col">
                <div class="in-head">
                    <span>Сделано</span>
                    <span class="in-count" data-slot="intention-done-who"></span>
                </div>
                <div class="in-list" data-slot="intention-done-list"></div>
                <div class="pane-foot" data-slot="intention-done-pager"></div>
            </div>

            <div class="split split-initiative"
                 title="Потяните, чтобы изменить ширину"></div>

            <div class="in-col in-col-pool">
                <div class="in-head">
                    <span>В пуле</span>
                    <span class="in-count" data-slot="intention-pool-count"></span>
                    <button type="button" class="people-reload"
                            data-slot="intention-pool-reload"
                            title="Перечитать пул">⟳</button>
                </div>
                <div class="in-list" data-slot="intention-pool-list"></div>
                <div class="pane-foot" data-slot="intention-pool-pager"></div>
            </div>
        </div>
        {% endblock %}
    </div>
```

- [ ] **Step 5: Имя вкладки в `CHAT_TABS` и ветка в `chatSubShow`**

В `data/uvicorn/template/admin/dating/profile.html` заменить строку 5657:

```javascript
    const CHAT_TABS = ['invite', 'sent', 'talk', 'block'];
```

на:

```javascript
    const CHAT_TABS = ['invite', 'sent', 'talk', 'block', 'intention'];
```

И в `chatSubShow` после строки `if (chatTab === 'block') inviteBlockShow();`
добавить:

```javascript
        if (chatTab === 'intention') intentionShow();
```

- [ ] **Step 6: Код колонок**

В том же файле, сразу после блока «Не пишем» (после `inviteBlockPagerPaint` и
его соседей, около строки 6480 — перед следующим разделом-комментарием),
добавить:

```javascript
    // ── Намерения и действия: что агент наобещал ────────────────────────────
    //
    // ⚠ Три колонки, и связаны они по-разному. Выбор **анкеты** в левом списке
    // страницы сужает все три — общее правило раздела. Выбор **пары** внутри
    // вкладки сужает только среднюю: правая отвечает на вопрос «что вообще ждёт
    // работы», и сузь мы её щелчком по паре, вкладка лишилась бы единственного
    // места, где этот вопрос задаётся.
    let intentionPairs = [];
    let intentionPairsPage = 0, intentionPairsPages = 1, intentionPairsTotal = 0;
    // Открытая пара строкой «анкета:номер»: номер у пары один на двоих, и по
    // нему её не найти. Тот же приём, что у `favTalkOpen`.
    let intentionOpen = '';
    let intentionDone = [];
    let intentionDonePage = 0, intentionDonePages = 1, intentionDoneTotal = 0;
    let intentionPool = [];
    let intentionPoolPage = 0, intentionPoolPages = 1, intentionPoolTotal = 0;

    const intentionKey = (row) => `${row.profile_id}:${row.uid}`;

    function intentionShow() {
        intentionPairsLoad();
        intentionPoolLoad();
        intentionDonePaint();
    }

    async function intentionPairsLoad() {
        const site = siteId();
        if (!site) {
            intentionPairs = [];
            intentionPairsPaint();

            return;
        }

        try {
            const got = (await getData(
                `${BASE}/api/dating/chat-intention/pairs?site_id=${site}`
                + `&profile_id=${openId || 0}&page=${intentionPairsPage + 1}`)) || {};

            intentionPairs = got.result || [];
            intentionPairsPages = Math.max(1, Number(got.pages) || 1);
            intentionPairsTotal = Number(got.total) || 0;
            intentionPairsPage = Math.max(0, (Number(got.page) || 1) - 1);
        } catch (e) {
            intentionPairs = [];
            showNotification('Пары намерений не пришли: ' + e.message, 'error');
        }

        intentionPairsPaint();
    }

    function intentionPairsPaint() {
        const box = slot('intention-pairs-list');
        if (!box) return;

        fillSlot('intention-pairs-count',
                 intentionPairsTotal ? `пар: ${intentionPairsTotal}` : '');

        if (!intentionPairs.length) {
            box.innerHTML = '<div class="in-empty">Намерений пока нет.<br>'
                + 'Их помечает агент, когда в разговоре что-то обещано — '
                + 'например, прислать фото.</div>';
            intentionPagerPaint('intention-pairs-pager', intentionPairsPages,
                                intentionPairsPage,
                                (pick) => { intentionPairsPage = pick;
                                            intentionPairsLoad(); },
                                intentionPairsTotal ? `пар: ${intentionPairsTotal}` : '');

            return;
        }

        // ⚠ Класс карточки — `contact`, а выбранная метится `open`: так устроен
        // общий вид `talk-cards`, которым рисуют списки пар и в чате, и у
        // фаворитов. Свой класс остался бы без стилей, и список вышел бы голым
        // столбцом текста.
        box.innerHTML = intentionPairs.map(row =>
            `<div class="contact${intentionKey(row) === intentionOpen ? ' open' : ''}"`
            + ` data-pair="${esc(intentionKey(row))}">`
            + '<span class="mail-sent-who talk">'
            + talkSide(row.profile_name, row.profile_photo, row.profile_uid,
                       'анкета, которая обещала')
            + '<span class="talk-swap">→</span>'
            + talkSide(row.name, row.photo, row.uid, 'кому обещано')
            + '</span>'
            // ⚠ Два числа и подписи к ним: голые «1 · 0» читались бы как номер.
            + `<span class="in-count">открыто ${Number(row.open_count) || 0}`
            + ` · сделано ${Number(row.done_count) || 0}</span>`
            + '</div>').join('');

        // ⚠ Обработчик вешаем после отрисовки: строки переписываются целиком на
        // каждое чтение, и повешенный до неё не пережил бы первого обновления.
        box.querySelectorAll('.contact').forEach(card =>
            card.addEventListener('click', () => {
                intentionOpen = card.dataset.pair;
                intentionDonePage = 0;
                intentionPairsPaint();
                intentionDoneLoad();
            }));

        intentionPagerPaint('intention-pairs-pager', intentionPairsPages,
                            intentionPairsPage,
                            (pick) => { intentionPairsPage = pick;
                                        intentionPairsLoad(); },
                            `пар: ${intentionPairsTotal}`);
    }

    async function intentionDoneLoad() {
        const site = siteId();
        const [pid, uid] = (intentionOpen || '').split(':');

        if (!site || !uid) {
            intentionDone = [];
            intentionDonePaint();

            return;
        }

        try {
            const got = (await getData(
                `${BASE}/api/dating/chat-intention?site_id=${site}`
                + `&profile_id=${pid}&uid=${encodeURIComponent(uid)}`
                + `&status=done&page=${intentionDonePage + 1}`)) || {};

            intentionDone = got.result || [];
            intentionDonePages = Math.max(1, Number(got.pages) || 1);
            intentionDoneTotal = Number(got.total) || 0;
            intentionDonePage = Math.max(0, (Number(got.page) || 1) - 1);
        } catch (e) {
            intentionDone = [];
            showNotification('Исполненные намерения не пришли: ' + e.message,
                             'error');
        }

        intentionDonePaint();
    }

    function intentionDonePaint() {
        const box = slot('intention-done-list');
        if (!box) return;

        const picked = intentionPairs.find(
            row => intentionKey(row) === intentionOpen);

        fillSlot('intention-done-who',
                 picked ? `${picked.profile_name} → ${picked.name}` : '');

        if (!intentionOpen) {
            box.innerHTML = '<div class="in-empty">Выберите пару слева.</div>';
            intentionPagerPaint('intention-done-pager', 1, 0, () => {}, '');

            return;
        }

        if (!intentionDone.length) {
            // ⚠⚠ Пока нет исполнителя, колонка пуста ВСЕГДА — закрывать строки
            // некому. Сказать это надо прямо, иначе человек решит, что вкладка
            // сломана, и будет искать поломку там, где её нет.
            box.innerHTML = '<div class="in-empty">Исполненных намерений нет: '
                + 'занятия, которое их исполняет, пока не существует — '
                + 'все намерения ждут в пуле справа.</div>';
            intentionPagerPaint('intention-done-pager', 1, 0, () => {}, '');

            return;
        }

        box.innerHTML = intentionDone.map(row =>
            '<div class="mail-sent-row plain">'
            + `<span class="mail-sent-when first">`
            + `${esc(row.done_at ? timeShift(row.done_at) : '')}</span>`
            + `<span class="mail-sent-state">${esc(row.intention)}</span>`
            // ⚠ Заметка исполнителя — тем же весом, что метка: ради неё колонку
            // и открывают. Пусто — говорим словами, а не пустым местом.
            + `<span class="mail-sent-text">`
            + `${esc(row.done_note || 'чем исполнили — не записано')}</span>`
            + '</div>').join('');

        intentionPagerPaint('intention-done-pager', intentionDonePages,
                            intentionDonePage,
                            (pick) => { intentionDonePage = pick;
                                        intentionDoneLoad(); },
                            `сделано: ${intentionDoneTotal}`);
    }

    async function intentionPoolLoad() {
        const site = siteId();
        if (!site) {
            intentionPool = [];
            intentionPoolPaint();

            return;
        }

        try {
            const got = (await getData(
                `${BASE}/api/dating/chat-intention?site_id=${site}`
                + `&profile_id=${openId || 0}&status=open`
                + `&page=${intentionPoolPage + 1}`)) || {};

            intentionPool = got.result || [];
            intentionPoolPages = Math.max(1, Number(got.pages) || 1);
            intentionPoolTotal = Number(got.total) || 0;
            intentionPoolPage = Math.max(0, (Number(got.page) || 1) - 1);
        } catch (e) {
            intentionPool = [];
            showNotification('Пул намерений не пришёл: ' + e.message, 'error');
        }

        intentionPoolPaint();
    }

    function intentionPoolPaint() {
        const box = slot('intention-pool-list');
        if (!box) return;

        fillSlot('intention-pool-count',
                 intentionPoolTotal ? `намерений: ${intentionPoolTotal}` : '');

        if (!intentionPool.length) {
            box.innerHTML = '<div class="in-empty">Пул пуст.<br>'
                + 'Намерения кладёт сюда прогон агента — на каждом ответе в '
                + 'чате и в письмах.</div>';
            intentionPagerPaint('intention-pool-pager', 1, 0, () => {}, '');

            return;
        }

        box.innerHTML = intentionPool.map(row =>
            '<div class="mail-sent-row plain">'
            + `<span class="mail-sent-when first">`
            + `${esc(row.created_at ? timeShift(row.created_at) : '')}</span>`
            + '<span class="mail-sent-who talk">'
            + talkSide(row.profile_name, row.profile_photo, row.profile_uid,
                       'анкета, которая обещала')
            + '<span class="talk-swap">→</span>'
            + talkSide(row.name, row.photo, row.uid, 'кому обещано')
            + '</span>'
            + `<span class="mail-sent-state">${esc(row.intention)}</span>`
            + '</div>').join('');

        intentionPagerPaint('intention-pool-pager', intentionPoolPages,
                            intentionPoolPage,
                            (pick) => { intentionPoolPage = pick;
                                        intentionPoolLoad(); },
                            `намерений: ${intentionPoolTotal}`);
    }

    // ⚠ Одна обёртка на три пейджера: разница между ними — имя узла и что делать
    // при выборе страницы, и три копии `pagerRender` разошлись бы на первой правке.
    function intentionPagerPaint(name, pages, current, pick, count) {
        const foot = slot(name);
        if (!foot) return;

        pagerRender(foot, pages, current, pick, count);
    }
```

- [ ] **Step 7: Привязать кнопки ⟳**

В `data/uvicorn/template/admin/dating/profile.html` рядом со строкой 4922
(`if (initiativeReload) initiativeReload.addEventListener('click', initiativeLoad);`)
добавить:

```javascript
        // ⚠ Колонки не перечитываются сами, а строки в них кладёт прогон агента:
        // человек вернётся на вкладку, ничего не нажимая, и увидит прежнее.
        const intentionPairsReload = workEl.querySelector(
            '[data-slot="intention-pairs-reload"]');
        if (intentionPairsReload)
            intentionPairsReload.addEventListener('click', intentionPairsLoad);

        const intentionPoolReload = workEl.querySelector(
            '[data-slot="intention-pool-reload"]');
        if (intentionPoolReload)
            intentionPoolReload.addEventListener('click', intentionPoolLoad);
```

- [ ] **Step 8: Прогнать тесты вкладки**

Шаблоны подхватываются без рестарта.

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention_tab.py -q -p no:randomly --no-header`

Expected: PASS, 7 тестов.

- [ ] **Step 9: Посмотреть вкладку глазами**

Открыть `http://localhost:8000/admin/dating/profile` (порт — в `.claude/docs/quickstart.md`), выбрать сайт, перейти «Чат → Намерения и действия».

Проверить глазами: три колонки на месте, оба разделителя тянутся, в правой —
накопленные намерения либо слова «Пул пуст», в средней — слова про отсутствие
исполнителя, щелчок по паре слева меняет заголовок средней колонки.

⚠ Если колонка пар пуста на всех сайтах — это ожидаемо на локальной базе:
намерения пишет живой прогон агента. Проверить колонки на данных можно,
положив строку руками:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query --write sql \
  "INSERT INTO chat_intention (profile_id, site_id, uid, intention)
   SELECT id, site_id, 'demo-1', 'she_promised_send_photo' FROM profile LIMIT 1"
```

После проверки строку убрать:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query --write sql \
  "DELETE FROM chat_intention WHERE uid = 'demo-1'"
```

- [ ] **Step 10: Прогнать задетые группы**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest -m "chat or core or page" -q -p no:randomly --no-header`

Expected: PASS.

- [ ] **Step 11: Коммит**

```bash
git add data/uvicorn/template/admin/dating/profile/site/_base.html \
        data/uvicorn/template/admin/dating/profile.html \
        tests/test_chat_intention_tab.py
git commit -m "Вкладка «Намерения и действия»: три колонки в полосе чата"
```

---

### Task 6: Документация

**Files:**
- Modify: `.claude/docs/api.md`, `.claude/docs/dating.md` (§5.13), `.claude/docs/database.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: всё из задач 1–5.
- Produces: ничего в коде.

- [ ] **Step 1: Ручки в `.claude/docs/api.md`**

Найти конец §3.12 (`grep -n '^## 3.1' .claude/docs/api.md`) и добавить следом
новый параграф. Номер взять следующий свободный — посмотреть по выводу `grep`.

```markdown
## 3.15 Намерения разговора: вкладка «Намерения и действия»

Агент `dating_talk` помечает решения разговора меткой словом
(`she_promised_send_photo`), и они копятся в `chat_intention` — пишет их одна
точка `_agent_ask` на все четыре канала. Вкладка показывает накопленное.

| Ручка | Что отдаёт |
|---|---|
| `GET /api/dating/chat-intention/pairs` | пары с намерениями: обе стороны, `open_count`, `done_count`, `last_at` |
| `GET /api/dating/chat-intention` | намерения страницей; довод `status` — `open` (пул) или `done` (сделано) |

Обе отдают `result`, `page`, `pages`, `total`. Права — всем вошедшим, как у
пулов: это работа оператора.

⚠ **Своя дверь, а не хвост чата.** Вкладка живёт в полосе «Чата», но намерения
приезжают и из писем: повесь мы ручку на `/api/dating/chat`, адрес обещал бы
только чат.

⚠ **Одна ручка на две колонки.** «Сделано» и «в пуле» спрашивают одно и то же
разными доводами; вторая ручка держала бы две копии отбора и разошлась бы с
первой.

⚠⚠ **Колонка «сделано» пуста, пока нет исполнителя.** Занятия, которое берёт
открытое намерение и делает дело, не существует — закрывать строки некому.
Пустое место говорит это словами: иначе вкладка читается как сломанная.

⚠ Выбор **анкеты** в левом списке страницы сужает все три колонки; выбор **пары**
внутри вкладки — только среднюю. Правая отвечает на вопрос «что вообще ждёт
работы», и сужать её щелчком по паре значило бы лишить вкладку единственного
места, где этот вопрос задаётся.
```

- [ ] **Step 2: Абзац в `.claude/docs/dating.md`**

В конец §5.13 (перед `## 6. Кнопка «Проверка»`) добавить:

```markdown
### Где это видно

«Профили» → «Чат» → **«Намерения и действия»**, пятая подвкладка. Три колонки:
пары с намерениями, исполненное у выбранной пары, пул открытых по всему сайту.
Ручки — `api.md`, §3.15.

⚠ Средняя колонка пуста, пока нет исполнителя, и говорит об этом словами.

⚠ Чем исполнили — столбец `done_note`: его пишет `chat_intention_done(id, note=…)`.
Режем по 500 знаков **мы**, а не база: в строгом режиме MySQL длинная вставка
падает ошибкой, и закрытие намерения сорвалось бы из-за текста исполнителя — при
том, что дело уже сделано.
```

- [ ] **Step 3: Строка в `.claude/docs/database.md`**

Заменить строку таблицы `chat_intention` на:

```markdown
| `chat_intention` | намерения разговора: что агент пообещал, исполнено ли это и чем (`done_note`) (⚠ одна **открытая** строка на «анкета + собеседник + метка»; выполненная ключ отпускает, и второе такое же обещание законно) |
```

- [ ] **Step 4: Строки в `CLAUDE.md`**

В строке «Development Patterns» про `chat_intention.py` в конец скобки добавить:

```
. ⚠ Видно это на «Чат → Намерения и действия» (`api.md`, §3.15): три колонки — пары, исполненное, пул. Средняя пуста, пока нет исполнителя, и говорит об этом словами — иначе вкладка читается как сломанная
```

В таблицу «Key Files» после строки `src/dating/chat_intention.py` добавить:

```markdown
| `src/uvicorn/routers/dating/chat_intention.py` | Ручки вкладки «Намерения и действия»: пары и строки (⚠ **одна** ручка на две колонки — «сделано» и «в пуле» различаются доводом `status`; вторая держала бы две копии отбора) |
```

- [ ] **Step 5: Коммит**

```bash
git add .claude/docs/api.md .claude/docs/dating.md .claude/docs/database.md CLAUDE.md
git commit -m "Документация: вкладка «Намерения и действия»"
```

---

### Task 7: Полный прогон

**Files:** ничего не правится.

- [ ] **Step 1: Убедиться, что чужого прогона нет**

Run: `pgrep -af "bin/pytest|python -m pytest" | grep -v "until ! pgrep"`

Expected: пусто. Не пусто — подождать: в каталоге работает вторая сессия, и
параллельные прогоны роняют оба.

- [ ] **Step 2: Перезапустить панель и прогнать сюиту**

Run: `docker restart dating_uvicorn && sleep 15 && ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/ -q -p no:randomly --no-header`

Expected: PASS, около девяти минут. Число тестов вырастет на 34 (4 + 9 + 8 в
`test_chat_intention.py`, 6 в `test_chat_intention_http.py`, 7 в
`test_chat_intention_tab.py`).

- [ ] **Step 3: Убедиться, что сюита не оставила мусора**

Run:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql \
  'SELECT COUNT(*) AS осталось FROM chat_intention'
```

Expected: `0` — каскад по `profile_id` унёс строки вместе с анкетами сюиты.

⚠ Карточки `companion`, заведённые тестами Task 2 и 3 вручную, каскадом **не
уходят**: у таблицы ключа на анкету нет. Проверить и, если накопилось, добавить
`companion` в разбор уборки отдельной задачей:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql \
  "SELECT COUNT(*) AS осталось FROM companion WHERE uid LIKE 'ci%'"
```

Expected: `0`. Не ноль — третий проход уборки (`TEST_SWEEP_BY_SITE`) уже знает
`companion` по стендовому сайту; проверить, что тесты заводили карточки именно с
`TEST_STAND_SITE`.

---

## Self-Review

**Покрытие спеки.** Столбец `done_note` и заметка — Task 1. `chat_intention_rows`
— Task 2. `chat_intention_pairs` — Task 3. Две ручки и их права — Task 4. Вкладка,
три колонки, пейджеры, кнопки ⟳, три пустых состояния — Task 5. Четыре документа
— Task 6. Полный прогон и чистота базы — Task 7.

Два пункта спеки задач не требуют и записаны в документацию: «исполнителя нет» и
«ручной отметки нет». Третий — `PROFILE_NAME_SQL` — **снят**, и причина
объяснена в начале плана: в списках панели имя анкеты выводит Python, а не SQL.

**Согласованность имён.** `chat_intention_done(intention_id, note='')`,
`chat_intention_rows(site_id, profile_id, uid, status, page, per)`,
`chat_intention_pairs(site_id, profile_id, page, per)`, `_intention_row`,
`CHAT_INTENTION_PAGE`, `CHAT_INTENTION_NOTE_MAX` — одинаковы во всех задачах и
тестах. Имена слотов (`intention-pairs-*`, `intention-done-*`, `intention-pool-*`)
совпадают между разметкой Task 5, кодом Task 5 и тестами Task 5. Ключ подвкладки
— `intention` в разметке, в `CHAT_TABS` и в `chatSubShow`.
