# Намерения разговора (`chat_intention`) — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Намерения, которые агент `dating_talk` присылает ключом `intentions` ответа `/chat/ask`, перестают выбрасываться и ложатся в таблицу `chat_intention` как очередь работы.

**Architecture:** Таблица с одной открытой строкой на «анкета + собеседник + метка» (ключ отпускают выполненные). Единственная дверь к ней — `src/dating/chat_intention.py`. Дверь к соседу `rhapsody_api_chat_talk` начинает отдавать третий ключ `intentions`, а пишет строки одна точка — `_agent_ask` в `src/dating/answer.py`, общая для чата, инициативы и обоих письменных каналов.

**Tech Stack:** Python 3.11, FastAPI, MySQL 8 (миграции через `yoyo`, накат на старте сервера), pytest + pytest-asyncio, docker compose.

Спека: `docs/superpowers/specs/2026-10-13-chat-intention-design.md`.

## Global Constraints

- **Живой HTTP в тестах запрещён** (`.claude/docs/testing.md`, §1.2). Подменяется приватная `_request` того же модуля либо сама функция-дверь.
- **Анкета для теста — только фикстуры `girl` / `girl_make`** из `tests/conftest.py`. Своих копий не заводить.
- Сайт стенда: `TEST_STAND_SITE = 999_343` (`tests/conftest.py:241`). Логин анкеты сюиты кончается на `@example.com` — это метка уборки.
- **У каждого файла тестов ровно один маркер.** Файл без маркера выпадает из всех пяти групп. Маркеры объявлены в `pyproject.toml`, ключ `markers`.
- **Класс тестов обязан начинаться с `Test`** — иначе pytest его молча не соберёт, и «зелено» будет означать «не проверено».
- `asyncio_mode = "auto"` (`pyproject.toml:2`): `async def` тесты и фикстуры пишутся **без** декораторов.
- **Запрос к базе руками — только `mysql_.mysql_query`** из общего слоя: `PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql '…'`. Пароль берётся из `.env` и не уезжает в `ps` и в историю оболочки.
- Миграция — **новый файл** в `migrations/`, имя `YYYYMMDDHHMMSS_<что делаем>.sql`. Применённые файлы не правят. Накат идёт `yoyo` на старте `dating_uvicorn`.
- Сюита ходит **по HTTP в поднятую панель**. Правка `.py` не видна сюите без `docker restart dating_uvicorn`.
- Предел длины метки — **100 знаков**, ровно тот, что у соседа (`CHAT_INTENTION_LENGTH_MAX`).
- Комментарии и докстринги — по-русски, в стиле соседних модулей раздела.

---

## File Structure

| Файл | Ответственность |
|---|---|
| `migrations/20261013150000_chat_intention.sql` (создать) | таблица `chat_intention` |
| `src/dating/chat_intention.py` (создать) | **единственная дверь** к таблице: запись, отбор открытых, отметка |
| `src/rhapsody/rhapsody_api.py` (править, 525–556) | `rhapsody_api_chat_talk` отдаёт третий ключ `intentions` |
| `src/dating/answer.py` (править, 44–50 и 617–620) | `_agent_ask` зовёт запись перед `return` |
| `tests/test_chat_intention.py` (создать) | дверь к таблице и точка записи, маркер `chat` |
| `tests/test_rhapsody_chat_client.py` (править) | разбор `intentions` в двери, маркер `core` |
| `.claude/docs/database.md`, `.claude/docs/dating.md`, `CLAUDE.md` (править) | документация |

---

### Task 1: Миграция — таблица `chat_intention`

**Files:**
- Create: `migrations/20261013150000_chat_intention.sql`

**Interfaces:**
- Consumes: ничего.
- Produces: таблица `chat_intention` со столбцами `id`, `site_id`, `profile_id`, `uid`, `intention`, `status`, `done_at`, `created_at`, `open_intention`; уникальный ключ `uq_chat_intention_open (site_id, uid, profile_id, open_intention)`; внешний ключ `fk_chat_intention_profile` на `profile(id)` с `ON DELETE CASCADE`.

- [ ] **Step 1: Написать файл миграции**

Создать `migrations/20261013150000_chat_intention.sql` ровно с этим содержимым:

```sql
-- Намерения разговора: что агент пообещал и исполнено ли это (2026-10-13)
--
-- ЗАЧЕМ. Агент `dating_talk` по ходу разговора принимает решения, которых нет в
-- тексте ответа: «она обещала прислать фото», «он просил не писать до вечера».
-- Сосед такое уже помечает — инструмент `chat_intention_add` кладёт метку словом
-- и отдаёт её списком в ключе `intentions` ответа `/chat/ask`.
--
-- Панель этот ключ выбрасывала: `rhapsody_api_chat_talk` читала только `result`
-- и `message_id`. Обещания девушки не жили нигде, и исполнить их было нечему.
--
-- ⚠ ЭТО НЕ КОПИЯ `companion_intention` У СОСЕДА. Та решает другую задачу:
-- отметкой времени режет историю надвое, чтобы одно обещание не приезжало на
-- каждом следующем ответе. Там дедупликация внутри разговора, здесь очередь
-- работы; подменить одно другим нельзя.
--
-- ⚠⚠ ТАБЛИЦА ЗАВОДИТСЯ РАНЬШЕ ИСПОЛНИТЕЛЯ, И ЭТО ОСОЗНАННО. Круга, который
-- берёт открытые намерения и делает дело, пока нет. Память нужна в тот же миг,
-- когда исполнение появится: заводить её потом значило бы иметь первые сотни
-- обещаний без следа. Тем же доводом живёт `companion_gallery_file`.
--
-- ⚠ КЛЮЧ ПАРЫ — `(site_id, uid)`, КАК ВЕЗДЕ В РАЗДЕЛЕ. Номер человека уникален
-- в пределах сайта, а не вообще: два сайта вправе выдать один и тот же.
--
-- ⚠⚠ КЛЮЧ ДЕРЖАТ ТОЛЬКО ОТКРЫТЫЕ. `open_intention` гаснет в `NULL` у
-- выполненной, а `NULL` в MySQL уникальности не нарушает. История обещаний
-- остаётся, повтор открытой строки не заводится. Простой ключ по
-- `(site_id, uid, profile_id, intention)` запретил бы ВТОРОЕ обещание фото
-- когда бы то ни было — а оно законно и ожидаемо: «oh no… I didn't get any
-- photo» → «i can send it again if you want».
--
-- ⚠⚠ `profile_id` ВХОДИТ В КЛЮЧ, НО НЕ В ВЫЧИСЛЕНИЕ, и это не стиль. MySQL
-- запрещает `ON DELETE CASCADE` у внешнего ключа, если от его столбца зависит
-- генерируемый (`Cannot add foreign key constraint`, 1215) — таблица не
-- создалась бы вовсе. Тем же приёмом живут `chat_initiative` и `invite`.
--
-- ⚠ ДВА СОСТОЯНИЯ, НЕ ТРИ. `failed` добавит миграцией тот, кто заведёт
-- исполнителя: сегодня падать нечему.
--
-- ⚠ РЕГИСТР МЕТКИ СРАВНИВАТЬ НЕ НУЖНО: при `utf8mb4_unicode_ci` ключ считает
-- `Promised_Photo` и `promised_photo` одним.
--
-- Идемпотентность: `CREATE TABLE IF NOT EXISTS`.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'chat_intention') AS таблица_есть;

-- -------------------------------------------- Намерения разговора
CREATE TABLE IF NOT EXISTS `chat_intention` (
    `id`         INT NOT NULL AUTO_INCREMENT,

    `site_id`    INT NOT NULL
        COMMENT 'Сайт собеседника: номер человека уникален в его пределах',
    `profile_id` INT NOT NULL
        COMMENT 'Чьё намерение: от чьего имени шёл разговор',
    `uid`  VARCHAR(32) NOT NULL
        COMMENT 'Кому обещано: номер собеседника на сайте',

    `intention` VARCHAR(100) NOT NULL
        COMMENT 'Метка словом: she_promised_send_photo',

    `status`  ENUM('open', 'done') NOT NULL DEFAULT 'open'
        COMMENT 'open — ждёт исполнения, done — исполнено',
    `done_at` DATETIME NULL DEFAULT NULL
        COMMENT 'Когда исполнили. NULL — ещё открыто',

    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        COMMENT 'Когда намерение приехало от соседа',

    -- ⚠ Приём `chat_initiative`: ключ держат только открытые строки.
    `open_intention` VARCHAR(100) GENERATED ALWAYS AS
        (IF(`status` = 'open', `intention`, NULL)) STORED
        COMMENT 'Метка, пока намерение открыто. NULL у выполненных',

    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_chat_intention_open`
        (`site_id`, `uid`, `profile_id`, `open_intention`),
    -- Под отбор исполнителя: открытые этого сайта, старые первыми.
    KEY `idx_chat_intention_open` (`status`, `site_id`, `id`),
    KEY `fk_chat_intention_profile` (`profile_id`),
    CONSTRAINT `fk_chat_intention_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `profile` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Намерения разговора: что агент пообещал и исполнено ли это';

-- ---------------------------------------------------------------- После
--
-- ⚠ Пусто, и это правильно: строки появятся на первом же прогоне `dating_talk`,
-- который вернёт непустой `intentions`.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'chat_intention') AS таблица_есть,
       (SELECT COUNT(*) FROM `chat_intention`) AS строк;
```

- [ ] **Step 2: Накатить миграцию перезапуском панели**

Run: `docker compose up -d && docker restart dating_uvicorn && sleep 15 && docker logs --tail 40 dating_uvicorn`

Expected: в логе видно применение миграции `20261013150000_chat_intention`, приложение стартовало без ошибок.

- [ ] **Step 3: Проверить, что таблица создалась ровно такой**

Run:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql \
  'SHOW CREATE TABLE chat_intention' --format json
```

⚠ Именно этим, а не `docker exec … mysql -p<пароль>`: учётные данные берутся из
`.env`, пароль не уезжает в `ps` и в историю оболочки.

Expected: в выводе есть `` `open_intention` varchar(100) GENERATED ALWAYS AS (if((`status` = 'open'),`intention`,NULL)) STORED ``, `UNIQUE KEY `uq_chat_intention_open` (`site_id`,`uid`,`profile_id`,`open_intention`)` и `CONSTRAINT `fk_chat_intention_profile` FOREIGN KEY (`profile_id`) REFERENCES `profile` (`id`) ON DELETE CASCADE`.

⚠ Если `CREATE TABLE` упал с `errno: 150` или `1215` — это значит, что `profile_id` попал в выражение генерируемого столбца. Сверься с текстом выше: в `open_intention` участвуют только `status` и `intention`.

- [ ] **Step 4: Убедиться, что сторож уборки по-прежнему зелёный**

Run: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_suite_sweep.py -q`

Expected: PASS. Сторож сверяется со схемой, и таблица с `ON DELETE CASCADE` в ручную чистку не просится — списков править не нужно. Если он покраснел, значит внешний ключ не создался: вернись к шагу 3.

- [ ] **Step 5: Коммит**

```bash
git add migrations/20261013150000_chat_intention.sql
git commit -m "Таблица chat_intention: намерения разговора"
```

---

### Task 2: `chat_intention_put` — запись намерений

**Files:**
- Create: `src/dating/chat_intention.py`
- Test: `tests/test_chat_intention.py` (создать)

**Interfaces:**
- Consumes: таблица `chat_intention` из Task 1.
- Produces:
  - `CHAT_INTENTION_LENGTH_MAX: int = 100`
  - `async def chat_intention_put(profile_id: int, site_id: int, uid: str, intentions: list) -> int` — сколько **новых** строк легло.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/test_chat_intention.py`:

```python
"""Намерения разговора: что агент пообещал и исполнено ли это.

Исполнителя ещё нет — круга, который берёт открытые намерения и делает дело.
Поэтому здесь проверяется **дверь**: ляжет ли метка, не ляжет ли вторая, пока
первая открыта, отпустит ли ключ выполненная, и не отметят ли одну строку
дважды два исполнителя.
"""
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.chat_intention import chat_intention_put
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.chat

PROMISE = 'she_promised_send_photo'


def _uid() -> str:
    """Свой собеседник каждому тесту: ключ таблицы — на пару, а не на анкету."""
    return f'ci-{uuid.uuid4().hex[:8]}'


async def _rows(profile_id: int) -> list:
    """Строки намерений этой анкеты, старые первыми."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, uid, intention, status, done_at'
            '  FROM chat_intention WHERE profile_id = %s ORDER BY id',
            (int(profile_id),))

        return [dict(row) for row in (await db.fetchall() or [])]


class TestThePut:
    """Запись намерения."""

    async def test_it_lands(self, girl):
        uid = _uid()

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        [PROMISE]) == 1

        rows = await _rows(girl)
        assert len(rows) == 1
        assert rows[0]['intention'] == PROMISE
        assert rows[0]['status'] == 'open'
        assert rows[0]['done_at'] is None

    async def test_twice_while_open_does_not_land(self, girl):
        """⚠ Одна открытая на пару и метку: повтор новой строки не заводит."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        [PROMISE]) == 0
        assert len(await _rows(girl)) == 1

    async def test_the_same_label_twice_in_one_batch_lands_once(self, girl):
        """Повторы внутри пачки схлопывает ключ базы, а не разбор."""
        uid = _uid()

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        [PROMISE, PROMISE]) == 1

    async def test_another_label_lands_beside_it(self, girl):
        """Ключ — на метку: другое обещание той же паре законно."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        ['he_asked_not_to_write']) == 1
        assert len(await _rows(girl)) == 2

    async def test_another_companion_lands_beside_it(self, girl):
        """Ключ — на пару: та же метка другому человеку законна."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        [PROMISE]) == 1
        assert len(await _rows(girl)) == 2


class TestWhatIsNotWritten:
    """Чего в таблицу не попадает."""

    async def test_a_long_label_is_dropped(self, girl):
        """⚠ Длиннее 100 знаков — пересказ разговора, а не метка."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        ['x' * 101]) == 0
        assert await _rows(girl) == []

    async def test_a_label_of_exactly_the_limit_lands(self, girl):
        """Граница включительная: сто знаков — ещё метка."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        ['x' * 100]) == 1

    async def test_blank_labels_are_dropped(self, girl):
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        ['', '   ']) == 0
        assert await _rows(girl) == []

    async def test_an_empty_list_writes_nothing(self, girl):
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(), []) == 0

    async def test_without_a_companion_writes_nothing(self, girl):
        """⚠ Пустой `uid` — намерению не к кому принадлежать."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, '',
                                        [PROMISE]) == 0
        assert await _rows(girl) == []

    async def test_without_a_profile_writes_nothing(self, girl):
        assert await chat_intention_put(0, TEST_STAND_SITE, _uid(),
                                        [PROMISE]) == 0
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_chat_intention.py -q`

Expected: collection error — `ModuleNotFoundError: No module named 'src.dating.chat_intention'`.

- [ ] **Step 3: Написать модуль**

Создать `src/dating/chat_intention.py`:

```python
"""Намерения разговора: что агент пообещал и исполнено ли это.

Агент `dating_talk` по ходу разговора принимает решения, которых нет в тексте
ответа: «она обещала прислать фото», «он просил не писать до вечера». Сосед
помечает их инструментом `chat_intention_add` и отдаёт списком в ключе
`intentions` ответа `/chat/ask`. Этот модуль — **единственная дверь** к таблице,
где такое обещание ждёт исполнения.

## ⚠ Это не копия `companion_intention` у соседа

Та решает другую задачу: отметкой времени режет историю надвое, чтобы одно
обещание не приезжало на каждом следующем ответе. Там дедупликация внутри
разговора, здесь очередь работы.

## ⚠⚠ Исполнителя пока нет, и дверь заведена раньше него

Круга, который берёт открытые намерения и делает дело, не существует. Память
нужна в тот же миг, когда исполнение появится: заводить её потом значило бы
иметь первые сотни обещаний без следа. Тем же доводом живёт
`companion_gallery.py`.

## ⚠ Пара — это «сайт и человек»

Номер собеседника уникален в пределах сайта, а не вообще, и ключ уникальности —
`(site_id, uid, profile_id, open_intention)`. Анкета в него входит: обещание
даёт она, и обещание другой девушки тому же человеку — своё.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

# Предел длины метки, знаков — ровно тот, что у соседа
# (`CHAT_INTENTION_LENGTH_MAX` в его `chat_intention.py`): длиннее он не
# принимает, значит приехать не может.
#
# ⚠ Проверка стоит здесь, а не в двери к соседу: столбец принадлежит этому
# модулю. Полагаться на чужой предел нельзя — он правится у соседа.
CHAT_INTENTION_LENGTH_MAX = 100


async def chat_intention_put(profile_id: int, site_id: int, uid: str,
                             intentions: list) -> int:
    """Заводит открытые намерения пары. Отдаёт, сколько новых легло.

    Args:
        profile_id: чьё намерение — анкета, от чьего имени шёл разговор.
        site_id: сайт собеседника.
        uid: номер собеседника на сайте.
        intentions: метки словом, как приехали от соседа.

    Returns:
        int: сколько строк вправду завелось. Ноль — всё уже лежало открытым
        либо писать было нечего, и это не ошибка.

    ## ⚠⚠ Повтор открытой строки — обычный исход, а не поломка

    Одно обещание держится в истории разговора много реплик, и агент вправе
    пометить его снова. Ключ базы такую вставку отвергает сам (`INSERT
    IGNORE`), и вызывающему довольно знать число новых. Выполненная строка ключ
    отпускает — следующее такое же обещание ляжет своей строкой.

    ⚠ Повторы внутри одной пачки тоже схлопывает ключ, а не разбор: одно
    правило вместо двух.

    ⚠ Пустой `uid` или пустая анкета — не пишем: намерению не к кому
    принадлежать.
    """
    said = [str(one or '').strip() for one in (intentions or [])]
    said = [one for one in said if one and len(one) <= CHAT_INTENTION_LENGTH_MAX]

    if not (int(profile_id or 0) and str(uid or '').strip() and said):
        return 0

    put = 0

    async with mysql_get_db_async() as db:
        for one in said:
            await db.execute(
                'INSERT IGNORE INTO chat_intention'
                '  (profile_id, site_id, uid, intention)'
                ' VALUES (%s, %s, %s, %s)',
                (int(profile_id), int(site_id or 0), str(uid).strip(), one))
            put += int(db.rowcount or 0)

        await db.connection.commit()

    if put:
        logger_info(f'[dating] намерений записано: {put} '
                    f'(анкета {int(profile_id)}, собеседник {str(uid).strip()})')

    return put
```

- [ ] **Step 4: Прогнать тесты**

Run: `docker restart dating_uvicorn && sleep 10 && ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_chat_intention.py -q`

Expected: PASS, 11 тестов.

- [ ] **Step 5: Коммит**

```bash
git add src/dating/chat_intention.py tests/test_chat_intention.py
git commit -m "Намерения разговора ложатся в базу: chat_intention_put"
```

---

### Task 3: `chat_intention_open` и `chat_intention_done` — отбор и отметка

**Files:**
- Modify: `src/dating/chat_intention.py`
- Test: `tests/test_chat_intention.py`

**Interfaces:**
- Consumes: `chat_intention_put` из Task 2.
- Produces:
  - `async def chat_intention_open(site_id: int, profile_id: int = 0, uid: str = '', limit: int = 0) -> list[dict]` — строки со столбцами `id`, `profile_id`, `site_id`, `uid`, `intention`, `created_at`.
  - `async def chat_intention_done(intention_id: int) -> bool` — легла ли **наша** отметка.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_chat_intention.py` — поправить импорт в шапке файла:

```python
from src.dating.chat_intention import (chat_intention_done,
                                       chat_intention_open, chat_intention_put)
```

и добавить в конец файла:

```python
class TestTheOpenOnes:
    """Отбор того, что ещё не исполнено."""

    async def test_it_gives_the_open_row(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        got = await chat_intention_open(TEST_STAND_SITE, profile_id=girl)

        assert [one['intention'] for one in got] == [PROMISE]
        assert got[0]['uid'] == uid

    async def test_the_done_one_is_gone_from_it(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]

        await chat_intention_done(one['id'])

        assert await chat_intention_open(TEST_STAND_SITE, profile_id=girl) == []

    async def test_it_filters_by_companion(self, girl):
        mine, other = _uid(), _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, mine, [PROMISE])
        await chat_intention_put(girl, TEST_STAND_SITE, other, [PROMISE])

        got = await chat_intention_open(TEST_STAND_SITE, profile_id=girl,
                                        uid=mine)

        assert [one['uid'] for one in got] == [mine]

    async def test_it_filters_by_profile(self, girl, girl_make):
        """Анкета — отбор, а не условие показа: у соседки свои намерения."""
        other = await girl_make()
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        await chat_intention_put(other, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await chat_intention_open(TEST_STAND_SITE, profile_id=other)

        assert [one['profile_id'] for one in got] == [other]

    async def test_the_limit_cuts_it(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 [PROMISE, 'he_asked_not_to_write'])

        got = await chat_intention_open(TEST_STAND_SITE, profile_id=girl,
                                        limit=1)

        assert len(got) == 1

    async def test_without_a_site_it_gives_nothing(self):
        """⚠ Отбор по сайту обязателен: круги ходят по сайтам поодиночке."""
        assert await chat_intention_open(0) == []


class TestTheDoneMark:
    """Отметка «исполнено»."""

    async def test_it_lands(self, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]

        assert await chat_intention_done(one['id']) is True

        rows = await _rows(girl)
        assert rows[0]['status'] == 'done'
        assert rows[0]['done_at'] is not None

    async def test_the_second_mark_is_not_ours(self, girl):
        """⚠ Условный UPDATE: два исполнителя не отметят одну строку дважды."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]
        await chat_intention_done(one['id'])

        assert await chat_intention_done(one['id']) is False

    async def test_a_done_one_releases_the_key(self, girl):
        """⚠⚠ Второе обещание фото законно — но только после первого."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])
        one = (await chat_intention_open(TEST_STAND_SITE, profile_id=girl))[0]
        await chat_intention_done(one['id'])

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        [PROMISE]) == 1
        assert [row['status'] for row in await _rows(girl)] == ['done', 'open']

    async def test_a_missing_row_is_not_ours(self):
        assert await chat_intention_done(0) is False
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_chat_intention.py -q`

Expected: collection error — `ImportError: cannot import name 'chat_intention_done'`.

- [ ] **Step 3: Дописать модуль**

Добавить в конец `src/dating/chat_intention.py`:

```python
async def chat_intention_open(site_id: int, profile_id: int = 0, uid: str = '',
                              limit: int = 0) -> list[dict]:
    """Намерения, которые ещё не исполнены. Пусто — законный исход.

    Args:
        site_id: сайт. Ноль — отдаём пусто (см. ⚠ ниже).
        profile_id: отбор по анкете. Ноль — все анкеты сайта.
        uid: отбор по собеседнику. Пусто — все.
        limit: сколько строк вернуть. Ноль — все.

    Returns:
        list[dict]: `id`, `profile_id`, `site_id`, `uid`, `intention`,
        `created_at`; старые первыми.

    ⚠ **Сайт обязателен.** Круги панели обходят сайты поодиночке
    (`round_sites`), и отбор «по всем сразу» здесь не нужен ни одному
    вызывающему. Ноль молча отдаёт пусто, а не всю таблицу: вторая форма
    однажды уехала бы в круг и унесла чужие строки.

    ⚠ Порядок — по номеру строки, то есть по времени: старое обещание исполняют
    раньше нового.
    """
    if not int(site_id or 0):
        return []

    sql = ('SELECT id, profile_id, site_id, uid, intention, created_at'
           '  FROM chat_intention'
           " WHERE status = 'open' AND site_id = %s")
    args = [int(site_id)]

    if int(profile_id or 0):
        sql += ' AND profile_id = %s'
        args.append(int(profile_id))

    if str(uid or '').strip():
        sql += ' AND uid = %s'
        args.append(str(uid).strip())

    sql += ' ORDER BY id'

    if int(limit or 0) > 0:
        sql += ' LIMIT %s'
        args.append(int(limit))

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))

        return [dict(row) for row in (await db.fetchall() or [])]


async def chat_intention_done(intention_id: int) -> bool:
    """Отмечает намерение исполненным.

    Args:
        intention_id: номер строки `chat_intention`.

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
    """
    if int(intention_id or 0) <= 0:
        return False

    async with mysql_get_db_async() as db:
        await db.execute(
            "UPDATE chat_intention SET status = 'done', done_at = NOW()"
            " WHERE id = %s AND status = 'open'",
            (int(intention_id),))
        done = int(db.rowcount or 0)
        await db.connection.commit()

    return bool(done)
```

- [ ] **Step 4: Прогнать тесты**

Run: `docker restart dating_uvicorn && sleep 10 && ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_chat_intention.py -q`

Expected: PASS, 21 тест.

- [ ] **Step 5: Коммит**

```bash
git add src/dating/chat_intention.py tests/test_chat_intention.py
git commit -m "Намерения: отбор открытых и отметка исполнения"
```

---

### Task 4: Дверь к соседу отдаёт `intentions`

**Files:**
- Modify: `src/rhapsody/rhapsody_api.py:525-556`
- Test: `tests/test_rhapsody_chat_client.py`

**Interfaces:**
- Consumes: ничего из прошлых задач.
- Produces: `rhapsody_api_chat_talk(...)` возвращает `dict` с тремя ключами — `text: str`, `message_id: int`, `intentions: list[str]`. Ключ `intentions` присутствует **всегда**.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_rhapsody_chat_client.py`, внутрь класса `TestChatTalk`:

```python
    async def test_intentions_come_through(self, requested):
        requested['answers'].append(
            {'result': 'ок', 'message_id': 1,
             'intentions': ['she_promised_send_photo']})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == ['she_promised_send_photo']

    async def test_no_key_is_an_empty_list(self, requested):
        """⚠ Ключ отдаётся всегда: «пусто» и «ключа нет» зовущий сольёт в одно,
        и пропажа стала бы неотличима от штатной тишины."""
        requested['answers'].append({'result': 'ок', 'message_id': 1})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == []

    async def test_a_strange_shape_is_an_empty_list(self, requested):
        requested['answers'].append(
            {'result': 'ок', 'message_id': 1, 'intentions': 'не список'})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == []

    async def test_blank_labels_are_dropped(self, requested):
        requested['answers'].append(
            {'result': 'ок', 'message_id': 1,
             'intentions': ['  promised_photo  ', '', '   ']})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == ['promised_photo']
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_rhapsody_chat_client.py -q`

Expected: FAIL — `KeyError: 'intentions'` в четырёх новых тестах.

- [ ] **Step 3: Править дверь**

В `src/rhapsody/rhapsody_api.py` **перед** `async def rhapsody_api_chat_talk` (строка 525) добавить:

```python
def _intentions(data: dict) -> list:
    """Намерения прогона списком строк. Что приехало не списком — пусто.

    ⚠ Разбор не доверяет чужой форме: ключ кладёт движок соседа по ответу
    инструмента `chat_intention_add`, и форма у него может смениться раньше,
    чем мы узнаем. Пустые метки выбрасываются здесь, длинные — у двери к
    таблице (`chat_intention.py`): предел принадлежит столбцу.

    ⚠ Повторы внутри пачки не схлопываются — это дело ключа базы.
    """
    got = (data or {}).get('intentions')

    if not isinstance(got, (list, tuple)):
        return []

    return [str(one).strip() for one in got if str(one or '').strip()]
```

Заменить последнюю строку `rhapsody_api_chat_talk` (строка 556):

```python
    return {'text': str(data.get('result') or ''), 'message_id': number}
```

на:

```python
    return {'text': str(data.get('result') or ''), 'message_id': number,
            'intentions': _intentions(data)}
```

В докстринге той же функции, в блоке `Returns:`, после строки про `message_id` добавить:

```
        `intentions` — метки намерений прогона (`she_promised_send_photo`).
        Пустой список законен и означает «агент ничего не пометил».
```

И следом за блоком «⚠⚠ Номер нужен, чтобы отметить доставку» добавить абзац:

```
    ## ⚠ Намерения отдаются всегда, пустым списком в том числе

    По той же причине, по какой их всегда отдаёт сосед: принимающая сторона
    сворачивает «пусто» и «ключа нет» в одно, и пропажа стала бы неотличима от
    штатной тишины. Ровно так поломка `message_id` прожила шесть дней
    незамеченной.
```

⚠ `rhapsody_api_chat_ask` не трогать: ей нужен только текст, и третий ключ её не касается.

- [ ] **Step 4: Прогнать тесты**

Run: `docker restart dating_uvicorn && sleep 10 && ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_rhapsody_chat_client.py -q`

Expected: PASS, все тесты файла.

- [ ] **Step 5: Коммит**

```bash
git add src/rhapsody/rhapsody_api.py tests/test_rhapsody_chat_client.py
git commit -m "Дверь к соседу отдаёт намерения прогона"
```

---

### Task 5: Точка записи — `_agent_ask`

**Files:**
- Modify: `src/dating/answer.py:44-50` (импорт), `src/dating/answer.py:617-620` (запись перед `return`)
- Test: `tests/test_chat_intention.py`

**Interfaces:**
- Consumes: `chat_intention_put` (Task 2), `chat_intention_open` (Task 3), ключ `intentions` двери (Task 4).
- Produces: ничего нового наружу — подпись `_agent_ask` остаётся `tuple[str, str, int]`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_chat_intention.py` — в шапку файла добавить импорты:

```python
from src.dating import answer as answer_module
from src.dating.answer import _agent_ask
```

и добавить в конец файла:

```python
async def _profile_row(profile_id: int) -> dict:
    """Настоящая строка `profile` — её и получает `_agent_ask` в бою."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s',
                         (int(profile_id),))

        return dict(await db.fetchone())


class TestTheWritingPoint:
    """`_agent_ask` — одна точка записи на чат, инициативу и оба письма."""

    async def test_it_writes_what_the_run_returned(self, girl, monkeypatch):
        uid = _uid()

        async def _talk(*args, **kwargs):
            return {'text': 'ок', 'message_id': 7, 'intentions': [PROMISE]}

        monkeypatch.setattr(answer_module, 'rhapsody_api_chat_talk', _talk)

        text, error, message_id = await _agent_ask(
            1, 'вопрос', 'en', await _profile_row(girl), uid=uid, name='Bob')

        assert (text, error, message_id) == ('ок', '', 7)
        assert [one['intention'] for one
                in await chat_intention_open(TEST_STAND_SITE,
                                             profile_id=girl)] == [PROMISE]

    async def test_an_empty_list_writes_nothing(self, girl, monkeypatch):
        async def _talk(*args, **kwargs):
            return {'text': 'ок', 'message_id': 7, 'intentions': []}

        monkeypatch.setattr(answer_module, 'rhapsody_api_chat_talk', _talk)

        await _agent_ask(1, 'вопрос', 'en', await _profile_row(girl),
                         uid=_uid(), name='Bob')

        assert await _rows(girl) == []

    async def test_a_broken_write_does_not_break_the_answer(self, girl,
                                                            monkeypatch):
        """⚠⚠ Реплика уже сочинена и сейчас уйдёт человеку: уронить её из-за
        ненаписавшейся отметки значит отменить дорогую работу ради дешёвой."""
        async def _talk(*args, **kwargs):
            return {'text': 'ок', 'message_id': 7, 'intentions': [PROMISE]}

        async def _boom(*args, **kwargs):
            raise RuntimeError('база легла')

        monkeypatch.setattr(answer_module, 'rhapsody_api_chat_talk', _talk)
        monkeypatch.setattr(answer_module, 'chat_intention_put', _boom)

        text, error, message_id = await _agent_ask(
            1, 'вопрос', 'en', await _profile_row(girl), uid=_uid(),
            name='Bob')

        assert (text, error, message_id) == ('ок', '', 7)
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_chat_intention.py -k TestTheWritingPoint -q`

Expected: FAIL — `test_it_writes_what_the_run_returned` падает на пустом списке открытых намерений, `test_a_broken_write_does_not_break_the_answer` падает на `AttributeError` (в `answer` нет имени `chat_intention_put`).

- [ ] **Step 3: Править `answer.py`**

В блоке импортов (после строки `from src.dating.chat_reply import (...)`, строка 52) добавить:

```python
from src.dating.chat_intention import chat_intention_put
```

⚠ Круговых импортов это не даёт: `chat_intention.py` знает только про `mysql_` и `logging_`.

Заменить строку 620:

```python
    return got['text'], '', int(got.get('message_id') or 0)
```

на:

```python
    # ⚠⚠ **Намерения прогона — одной точкой на все каналы.** Сюда приходят чат,
    # инициатива, письмо первым и ответ на письмо; отдай мы `intentions` наверх
    # четвёртым значением, правило «запиши намерения» встало бы тремя копиями —
    # ровно грабля `delivered_at`, где дорог оказалось пять и четвёртую забыли.
    #
    # ⚠ Строка заводится **по ответу прогона**, то есть до отправки: у писем
    # между сочинением и отправкой проходят часы. Размен назван в спеке —
    # лечится позже отметкой доставки по номеру обмена.
    #
    # ⚠⚠ Отказ записи ответ **не роняет**. Реплика к этому мигу сочинена и
    # сейчас уйдёт человеку; уронить её из-за ненаписавшейся отметки значит
    # отменить дорогую работу ради дешёвой. Тот же довод, по которому молчит
    # `rhapsody_api_chat_delivered`.
    try:
        await chat_intention_put(int(profile.get('id') or 0),
                                 int(profile.get('site_id') or 0),
                                 uid, got.get('intentions') or [])
    except Exception as e:
        logger_info(f'[dating] намерения прогона не записались: {e}')

    return got['text'], '', int(got.get('message_id') or 0)
```

- [ ] **Step 4: Прогнать тесты**

Run: `docker restart dating_uvicorn && sleep 10 && ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_chat_intention.py -q`

Expected: PASS, 24 теста.

- [ ] **Step 5: Прогнать задетые группы целиком**

Run: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest -m "chat or core" -q`

Expected: PASS. ⚠ Одиночное красное в большом прогоне — не обязательно регрессия: сперва проверь, не идёт ли параллельно чужой `pytest`, и перепрогони упавший файл отдельно.

- [ ] **Step 6: Коммит**

```bash
git add src/dating/answer.py tests/test_chat_intention.py
git commit -m "Намерения прогона записываются одной точкой в _agent_ask"
```

---

### Task 6: Документация

**Files:**
- Modify: `.claude/docs/database.md:17-45`, `.claude/docs/dating.md` (перед `## 6. Кнопка «Проверка»`, строка 1827), `.claude/docs/dating.md:2124-2132` (§7), `CLAUDE.md`

**Interfaces:**
- Consumes: всё из задач 1–5.
- Produces: ничего в коде.

- [ ] **Step 1: Строка в `.claude/docs/database.md`**

В таблицу §2 «Что в базе есть», после строки `` | `error` | … | ``, добавить:

```markdown
| `chat_intention` | намерения разговора: что агент пообещал и исполнено ли это (⚠ одна **открытая** строка на «анкета + собеседник + метка»; выполненная ключ отпускает, и второе такое же обещание законно) |
```

- [ ] **Step 2: Раздел в `.claude/docs/dating.md`**

Вставить перед строкой `## 6. Кнопка «Проверка»`:

```markdown
## 5.13 Намерения разговора: что агент пообещал

Агент `dating_talk` по ходу разговора принимает решения, которых нет в тексте
ответа: «она обещала прислать фото», «он просил не писать до вечера». Сосед
помечает их инструментом `chat_intention_add` — **меткой словом**
(`she_promised_send_photo`, не длиннее 100 знаков) — и отдаёт списком в ключе
`intentions` ответа `/chat/ask`.

Панель этот ключ выбрасывала: `rhapsody_api_chat_talk` читала только `result` и
`message_id`. Теперь намерения ложатся в `chat_intention`.

⚠ **Это не копия `companion_intention` у соседа.** Та решает другую задачу:
отметкой времени режет историю надвое, чтобы одно обещание не приезжало на
каждом следующем ответе. Там дедупликация внутри разговора, здесь очередь
работы.

⚠⚠ **Исполнителя пока нет.** Круга, который берёт открытые намерения и делает
дело (отправляет фото из галереи, молчит до вечера), не существует. Таблица
заведена раньше работы намеренно — тем же доводом, что `companion_gallery_file`:
память нужна в тот же миг, когда исполнение появится.

**Что значит «открыто».** Одна открытая строка на «анкета + собеседник +
метка»: повторную пометку того же обещания ключ отвергает. Выполненная строка
ключ **отпускает** (`open_intention` гаснет в `NULL`), и следующее такое же
обещание ложится своей строкой — «oh no… I didn't get any photo» → «i can send
it again if you want» это новое обещание, а не дубль.

**Где что лежит:**

| Что | Где |
|---|---|
| таблица и её правила | `src/dating/chat_intention.py` — единственная дверь |
| третий ключ ответа соседа | `rhapsody_api_chat_talk` в `src/rhapsody/rhapsody_api.py` |
| точка записи | `_agent_ask` в `src/dating/answer.py` — одна на чат, инициативу и оба письма |

⚠ **Строка заводится по ответу прогона**, то есть до отправки реплики. Значит
возможна открытая строка от обещания, которого собеседник не читал: агент
промолчал, отправка не удалась, а у писем между сочинением и отправкой проходят
часы. Принято сознательно: честный вариант требует отмечать намерение там же,
где ставится отметка доставки, — а это пять дорог сквозь пулы, и четвёртая
забывается. Лечится позже столбцом `message_id` в строке.

⚠ **Словаря меток нет ни на одной стороне.** Какие метки бывают, задаёт workflow
у соседа, а правки workflow живут только в его прод-базе. Метка хранится как
приехала.
```

- [ ] **Step 3: Строка в §7 «Таблицы» того же файла**

В таблицу §7, после строки `` | `log` | журнал по учётке: что делали и чем кончилось | ``, добавить:

```markdown
| `chat_intention` | намерения разговора: метка, `status` open/done, `done_at` |
```

- [ ] **Step 4: Две строки в `CLAUDE.md`**

В раздел «Development Patterns», после строки про `companion_about.py` («Описание собеседника с сайта»), добавить:

```markdown
- **Агент что-то пообещал собеседнику** → `src/dating/chat_intention.py` и `.claude/docs/dating.md`, §5.13 (⚠ метка словом приезжает ключом `intentions` ответа `/chat/ask`; пишет **одна точка** `_agent_ask` на все четыре канала, а не каждый канал своей копией. ⚠⚠ Одна **открытая** строка на «анкета + собеседник + метка»: выполненная ключ отпускает, и второе такое же обещание законно — «i can send it again if you want» не дубль. ⚠ Исполнителя пока нет: таблица заведена раньше работы, как `companion_gallery_file`. ⚠ Это **не** копия `companion_intention` у соседа — та отметкой времени режет историю, чтобы одно обещание не приезжало на каждом ответе)
```

В таблицу «Key Files», после строки про `src/dating/companion_about.py`, добавить:

```markdown
| `src/dating/chat_intention.py` | Намерения разговора: **единственная дверь** к `chat_intention` — запись пачкой, отбор открытых, отметка исполнения (⚠ отметка **условным `UPDATE`**: два исполнителя не закроют одну строку дважды. ⚠ Предел метки в 100 знаков проверяется здесь, а не в двери к соседу: столбец принадлежит модулю) |
```

- [ ] **Step 5: Коммит**

```bash
git add .claude/docs/database.md .claude/docs/dating.md CLAUDE.md
git commit -m "Документация: намерения разговора"
```

---

### Task 7: Полный прогон перед выкладкой

**Files:** ничего не правится.

**Interfaces:**
- Consumes: всё.
- Produces: подтверждение, что сюита зелёная.

- [ ] **Step 1: Перезапустить панель**

Run: `docker restart dating_uvicorn && sleep 15 && docker logs --tail 20 dating_uvicorn`

Expected: приложение поднялось, в логе нет traceback.

- [ ] **Step 2: Прогнать сюиту целиком**

Run: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/ -q`

Expected: PASS, около семи минут. Число тестов выросло на 28 (24 в `tests/test_chat_intention.py` + 4 в `tests/test_rhapsody_chat_client.py`).

⚠ Красное — сперва проверь, не идёт ли параллельно чужой `pytest` в том же каталоге: разные тесты падают в разных прогонах именно от этого.

- [ ] **Step 3: Убедиться, что сюита не оставила мусора**

Run:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql \
  'SELECT COUNT(*) AS осталось FROM chat_intention'
```

Expected: `0` — каскад по `profile_id` унёс строки вместе с анкетами сюиты. Не ноль — значит внешний ключ создался без `ON DELETE CASCADE`, вернись к Task 1, шагу 3.

---

## Self-Review

**Покрытие спеки.** Таблица — Task 1. `chat_intention_put` — Task 2. `chat_intention_open` и `chat_intention_done` — Task 3. Третий ключ двери — Task 4. Точка записи в `_agent_ask` и невозможность уронить ответ — Task 5. Три документа — Task 6. Каскад уборки и полный прогон — Task 7. Пункты спеки «исполнителя нет», «показа в панели нет», «словаря меток нет» задач не требуют — они про границы, и записаны в документации (Task 6, шаг 2).

**Согласованность имён.** `chat_intention_put(profile_id, site_id, uid, intentions)`, `chat_intention_open(site_id, profile_id=0, uid='', limit=0)`, `chat_intention_done(intention_id)`, `CHAT_INTENTION_LENGTH_MAX`, `_intentions(data)` — одинаковы во всех задачах и в тестах. Ключ ответа двери везде `intentions`, столбец состояния везде `status` со значениями `open`/`done`.
