# `companion_session.delivered_at` — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Агент перестаёт видеть в истории разговора свои реплики, которых собеседник не получал.

**Architecture:** В `companion_session` (RhapsodyAi) появляется `delivered_at`. Шаг workflow пишет обмен как раньше — доставку отмечает DatingAi отдельным вызовом, когда реплика реально ушла. Лента для агента показывает половину «ответ» только у отмеченных строк. Номер строки едет в DatingAi ответом `/chat/ask` и, для писем, хранится в строке пула до момента отправки.

**Tech Stack:** Python 3.11, FastAPI, MySQL 8 + yoyo-миграции, pytest (asyncio), httpx.

## Global Constraints

- **Два репозитория.** `~/PycharmProjects/RhapsodyAi` (сосед) и `~/PycharmProjects/DatingAi` (панель). Коммиты раздельные, пути в задачах даны от корня своего репозитория.
- **Порядок выкладки — три шага:** задачи 1–3 (сосед) → задачи 4–8 (панель) → задача 9 (сосед, включение отбора). Нарушение порядка оставляет агента без истории по всем новым репликам.
- **Миграции не правят применённые.** Новый файл в `migrations/`, имя `<YYYYMMDDHHMMSS>_<что делаем>.sql`.
- **Живой HTTP в тестах запрещён.** В обоих проектах стоит сторож на транспорте httpx (`py_modules/http_/http_mock.py`), только заглушки.
- **Докстринг обязателен** у каждой новой функции; префикс функции = namespace файла (`py_modules/docs/code_rules.md`).
- **`delivered_at` — `DATETIME NULL DEFAULT NULL`.** Никогда `NOT NULL DEFAULT CURRENT_TIMESTAMP`: `ALTER` проставил бы время миграции всем накопленным строкам.
- Локальный MySQL соседа: `docker exec rhapsodyai-mysql-1 mysql -uroot -ppassword project`. Локальный MySQL панели: `docker exec dating_mysql mysql -uroot -ppassword dating`.
- Миграции накатываются перезапуском uvicorn своего проекта: `docker restart uvicorn` (сосед), `docker compose restart dating_uvicorn` (панель).

---

## Файлы

### RhapsodyAi

| Файл | Ответственность |
|---|---|
| `migrations/20261002100000_companion_session_delivered.sql` | колонка + бэкфилл накопленного |
| `migrations/20261002100100_wf_talk_message_id_out.sql` | `outputs_map` шагу 743 |
| `src/tool/companion/companion_session.py` | `companion_session_delivered` (новая), отбор в `companion_session_rows` |
| `src/uvicorn/routers/chat/chat.py` | `message_id` в ответе `/chat/ask`, ручка `/chat/message/delivered` |
| `tests/test_companion_session_delivered.py` | тесты инструмента и ручки |

### DatingAi

| Файл | Ответственность |
|---|---|
| `migrations/20261002110000_mail_pool_message_id.sql` | `message_id` двум пулам |
| `src/rhapsody/rhapsody_api.py` | `rhapsody_api_chat_talk`, `rhapsody_api_chat_delivered` |
| `src/dating/answer.py` | `_agent_ask` отдаёт `message_id`; чат зовёт `delivered` |
| `src/dating/mail_reply.py` | номер в строку пула ответов |
| `src/dating/favorite_mail.py` | номер в строку пула писем избранным |
| `src/dating/mail_send.py` | `mail_send_mark` зовёт `delivered` при удачной отправке |
| `tests/test_delivered_mark.py` | тесты всех трёх дорог |

---

## Задача 1: Колонка и бэкфилл (RhapsodyAi)

**Files:**
- Create: `migrations/20261002100000_companion_session_delivered.sql`

**Interfaces:**
- Consumes: ничего
- Produces: столбец `companion_session.delivered_at DATETIME NULL`

- [ ] **Шаг 1: Снять «до» с локальной базы**

```bash
cd ~/PycharmProjects/RhapsodyAi && docker exec rhapsodyai-mysql-1 mysql -uroot -ppassword -N -B project -e "
SELECT COUNT(*) FROM companion_session;
SELECT COUNT(*) FROM companion_session WHERE COALESCE(answer,'') <> '';"
```

Ожидается два числа. Второе — сколько строк получит бэкфилл. Запишите их: шаг 4 сверяется с ними.

- [ ] **Шаг 2: Написать миграцию**

```sql
-- Доставка реплики как факт строки: `companion_session.delivered_at` (2026-10-02)
--
-- ## ⚠⚠ ЗАЧЕМ
--
-- Шаг `dating_talk` «Разговор: записать ответ» пишет обмен **внутри прогона** —
-- до того, как ответ вернётся в DatingAi и панель попробует его отправить. У
-- панели строка в переписке появляется только при удачной отправке, у нас —
-- всегда. Неудача отправки расходит две истории: в переписке реплики нет, в
-- памяти агента есть, и следующий ответ строится так, будто сказанное сказано.
--
-- Отметку ставит DatingAi отдельным вызовом, когда реплика реально ушла.
--
-- ## ⚠⚠ `NULL` и БЕЗ умолчания
--
-- `NOT NULL DEFAULT CURRENT_TIMESTAMP` получил бы от `ALTER` время миграции
-- **всем накопленным строкам** — и недоставленное выглядело бы доставленным сию
-- секунду. Эта грабля уже стоила ложной картины в «Доставке» у DatingAi
-- (`favorite_mail.updated_at`). Неизвестно — значит `NULL`.
--
-- ## Бэкфилл накопленного: решение принято явно
--
-- Без него агент в день выкладки разом забывает переписку со всеми
-- собеседниками и начинает разговоры с нуля. Цена известна и принята: среди
-- отмеченных есть неизвестное число реально не ушедших реплик — это ровно то
-- допущение, на котором система жила до сих пор.
--
-- ⚠ Время берём `answer_at`, а не `NOW()`: это момент, когда реплика была
-- сказана. `NOW()` сделал бы всю прежнюю переписку «доставленной только что».
--
-- Идемпотентность: колонка заводится по условию, бэкфилл не трогает уже
-- проставленное (`delivered_at IS NULL`).

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'companion_session'
           AND COLUMN_NAME = 'delivered_at') AS колонка_есть,
       (SELECT COUNT(*) FROM `companion_session`) AS строк,
       (SELECT COUNT(*) FROM `companion_session`
         WHERE COALESCE(`answer`, '') <> '') AS с_ответом;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'companion_session'
        AND COLUMN_NAME = 'delivered_at') = 0,
    'ALTER TABLE `companion_session`
        ADD COLUMN `delivered_at` DATETIME NULL DEFAULT NULL
            COMMENT ''Когда наш ответ дошёл до собеседника; NULL — не дошёл или ещё не ушёл''',
    'SELECT ''колонка уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE `companion_session`
   SET `delivered_at` = `answer_at`
 WHERE `delivered_at` IS NULL
   AND `answer_at` IS NOT NULL
   AND COALESCE(`answer`, '') <> '';

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `companion_session`
         WHERE `delivered_at` IS NOT NULL) AS отмечено,
       (SELECT COUNT(*) FROM `companion_session`
         WHERE `delivered_at` IS NULL) AS без_отметки;
```

- [ ] **Шаг 3: Накатить**

```bash
cd ~/PycharmProjects/RhapsodyAi && docker restart uvicorn && sleep 25 && \
  docker logs uvicorn --since 1m 2>&1 | grep -E "companion_session_delivered|Все миграции|ERROR" | head -5
```

Ожидается: `Applying 20261002100000_companion_session_delivered` и `Все миграции успешно применены`.

- [ ] **Шаг 4: Проверить результат**

```bash
docker exec rhapsodyai-mysql-1 mysql -uroot -ppassword -N -B project -e "
SELECT COUNT(*) FROM companion_session WHERE delivered_at IS NOT NULL;
SELECT COUNT(*) FROM companion_session WHERE delivered_at IS NULL AND COALESCE(answer,'') <> '';"
```

Ожидается: первое число равно «с ответом» из шага 1; второе — **ноль**.

- [ ] **Шаг 5: Коммит**

```bash
cd ~/PycharmProjects/RhapsodyAi && git add migrations/20261002100000_companion_session_delivered.sql && \
git commit -m "Доставка реплики: колонка companion_session.delivered_at и бэкфилл накопленного"
```

---

## Задача 2: Отметка доставки — инструмент и ручка (RhapsodyAi)

**Files:**
- Modify: `src/tool/companion/companion_session.py`
- Modify: `src/uvicorn/routers/chat/chat.py`
- Create: `tests/test_companion_session_delivered.py`

**Interfaces:**
- Consumes: столбец `delivered_at` из задачи 1
- Produces:
  - `async def companion_session_delivered(message_id: int) -> dict` — возвращает `{'marked': 0|1, 'why': str}`
  - `POST /chat/message/delivered` с телом `{"message_id": int}` → `{"result": {"marked": 0|1, "why": ""}}`

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_companion_session_delivered.py`:

```python
"""Отметка доставки: строка помечается один раз, повтор время не двигает.

⚠⚠ Что здесь стережётся. `companion_session` пишется **внутри прогона
workflow** — до того, как DatingAi попробует отправить реплику. Отметка
`delivered_at` отделяет «мы это сочинили» от «собеседник это получил», и лента
для агента показывает только второе.

⚠ Тесты зовут инструмент напрямую, а не через прогон workflow: прогон стоит
вызова модели, общей с продом, а проверяется здесь запись в таблицу.

⚠ `asyncio_mode = "auto"` в `pyproject.toml` — своей метки `pytest.mark.asyncio`
тестам не нужно.

⚠ В базу ходим драйвером, а не `docker exec`: образец —
`tests/test_workflow_variables_node.py`, оттуда же фикстура `db_pool_release`.
"""
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async, mysql_pool_close

from src.tool.companion.companion_session import (companion_session_add,
                                                  companion_session_delivered)

pytestmark = pytest.mark.core


@pytest.fixture(autouse=True)
async def db_pool_release():
    """Закрыть пул MySQL после теста: он привязан к циклу, а цикл у каждого свой.

    ⚠ Тот же приём, что у `test_workflow_variables_node.py`. Без него второй
    ходящий в базу тест падает «Future attached to a different loop».
    """
    yield
    await mysql_pool_close()


async def _delivered_at(row_id: int):
    """Отметка доставки у строки — `None`, если её нет."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT `delivered_at` FROM `companion_session`'
                         ' WHERE `id` = %s', (int(row_id),))
        row = await db.fetchone()

    return (row or {}).get('delivered_at')


@pytest.fixture
async def pair():
    """Пара ключей, по которой заводится разговор. Убирается за собой."""
    mark = uuid.uuid4().hex[:8]
    keys = (f'comp_own_{mark}', f'comp_him_{mark}')

    yield keys

    async with mysql_get_db_async() as db:
        await db.execute(
            'DELETE s FROM `companion_session` s'
            ' JOIN `companion` c ON c.`id` = s.`owner_id`'
            ' WHERE c.`unique_id` = %s', (keys[0],))
        await db.execute('DELETE FROM `companion` WHERE `unique_id` IN (%s, %s)',
                         keys)
        await db.connection.commit()


class TestDeliveredMark:
    async def test_new_row_is_not_delivered(self, pair):
        """Записанный обмен доставленным не считается: отправки ещё не было."""
        made = await companion_session_add(pair[0], pair[1],
                                           question='hi', answer='hello')

        assert await _delivered_at(made['id']) is None

    async def test_mark_sets_time(self, pair):
        """Отметка проставляет время."""
        made = await companion_session_add(pair[0], pair[1],
                                           question='hi', answer='hello')

        got = await companion_session_delivered(int(made['id']))

        assert got['marked'] == 1
        assert await _delivered_at(made['id']) is not None

    async def test_second_mark_does_not_move_time(self, pair):
        """⚠ Повтор времени не двигает: «когда дошло» обязано остаться первым."""
        made = await companion_session_add(pair[0], pair[1],
                                           question='hi', answer='hello')
        await companion_session_delivered(int(made['id']))
        first = await _delivered_at(made['id'])

        got = await companion_session_delivered(int(made['id']))

        assert got['marked'] == 0, 'повтор не должен считаться отметкой'
        assert await _delivered_at(made['id']) == first

    async def test_unknown_id_is_not_an_error(self):
        """⚠ Неизвестный номер — не отказ сервера: панели нечего повторять."""
        got = await companion_session_delivered(999999999)

        assert got['marked'] == 0
        assert got['why']
```

⚠ Проверить имя функции закрытия пула: `grep -n "mysql_pool_close" tests/test_workflow_variables_node.py`. Если там иначе — взять оттуда.

- [ ] **Шаг 2: Убедиться, что тест падает**

```bash
cd ~/PycharmProjects/RhapsodyAi && python -m pytest tests/test_companion_session_delivered.py -v 2>&1 | tail -15
```

Ожидается: `ImportError: cannot import name 'companion_session_delivered'`.

- [ ] **Шаг 3: Написать инструмент**

В `src/tool/companion/companion_session.py`, сразу после `companion_session_add`:

```python
async def companion_session_delivered(message_id: int) -> dict:
    """Отметить обмен доставленным: наш ответ дошёл до собеседника.

    Args:
        message_id: номер строки `companion_session` — его отдаёт
            `companion_session_add` и возвращает `/chat/ask` полем `message_id`.

    Returns:
        dict: `marked` — 1, если отметка легла, 0 иначе; `why` — почему не легла.

    ## ⚠⚠ Повтор времени не двигает

    Условие `delivered_at IS NULL`. Без него повторная отправка сдвигала бы
    отметку вперёд, и «когда это дошло» перестало бы отвечать на свой вопрос.

    ## ⚠ Неизвестный номер — не отказ

    Строки нет — отвечаем `marked: 0` и причиной. Исключение здесь заставило бы
    панель повторять вызов, которому повторяться незачем: строка не появится.
    """
    number = int(message_id or 0)

    if number <= 0:
        return {'marked': 0, 'why': 'номер обмена не задан'}

    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE `companion_session` SET `delivered_at` = %s'
            ' WHERE `id` = %s AND `delivered_at` IS NULL',
            (datetime.now(), number))
        marked = int(db.rowcount or 0)
        await db.connection.commit()

    if marked:
        return {'marked': 1, 'why': ''}

    return {'marked': 0, 'why': f'обмен {number} не найден либо уже отмечен'}
```

- [ ] **Шаг 4: Прогнать тесты инструмента**

```bash
cd ~/PycharmProjects/RhapsodyAi && python -m pytest tests/test_companion_session_delivered.py -v 2>&1 | tail -15
```

Ожидается: 4 passed.

- [ ] **Шаг 5: Добавить ручку**

В `src/uvicorn/routers/chat/chat.py` — рядом с `ChatAskJsonRequest`:

```python
class ChatMessageDeliveredRequest(BaseModel):
    message_id: int
```

и ручка после `chat_ask_ai`:

```python
@router.post("/chat/message/delivered")
async def chat_message_delivered(
        json_request: ChatMessageDeliveredRequest,
        user: AuthSession = Depends(AuthChecker([]))
) -> dict:
    """Отметить обмен разговора доставленным.

    Зовёт DatingAi в момент, когда реплика реально ушла собеседнику. До этого
    строка в `companion_session` лежит без отметки и в ленту для агента не идёт:
    сочинённое — ещё не сказанное.

    ⚠ Идемпотентна: повтор отвечает `marked: 0` и времени не двигает.
    """
    from src.tool.companion.companion_session import companion_session_delivered

    return {"result": await companion_session_delivered(json_request.message_id)}
```

- [ ] **Шаг 6: Проверить ручку живым запросом**

```bash
cd ~/PycharmProjects/RhapsodyAi && docker restart uvicorn && sleep 25 && \
docker exec rhapsodyai-mysql-1 mysql -uroot -ppassword -N -B project -e \
  "SELECT id FROM companion_session WHERE delivered_at IS NOT NULL ORDER BY id DESC LIMIT 1"
```

Взять номер из вывода и позвать ручку — ожидается `{"result":{"marked":0,...}}`, потому что строка уже отмечена:

```bash
curl -s -X POST http://localhost:8011/chat/message/delivered \
  -H 'Content-Type: application/json' -b /tmp/rh.cookie \
  -d '{"message_id": <номер из предыдущей команды>}'
```

(Кука: `curl -s -c /tmp/rh.cookie -X POST http://localhost:8011/auth/login -H 'Content-Type: application/json' -d "$(python -c "import json,os;print(json.dumps({'username':os.getenv('TEST_USER','test'),'password':os.getenv('TEST_PASS','test')}))")"` — учётка сюиты из `tests/constants.py`.)

- [ ] **Шаг 7: Коммит**

```bash
cd ~/PycharmProjects/RhapsodyAi && git add src/tool/companion/companion_session.py \
  src/uvicorn/routers/chat/chat.py tests/test_companion_session_delivered.py && \
git commit -m "Доставка реплики: инструмент companion_session_delivered и ручка /chat/message/delivered"
```

---

## Задача 3: Номер обмена в ответе `/chat/ask` (RhapsodyAi)

**Files:**
- Create: `migrations/20261002100100_wf_talk_message_id_out.sql`
- Modify: `src/uvicorn/routers/chat/chat.py`

**Interfaces:**
- Consumes: ручку из задачи 2
- Produces: `POST /chat/ask` отвечает `{"result": str, "message_id": int}` (`0` — агент промолчал)

- [ ] **Шаг 1: Написать миграцию `outputs_map`**

```sql
-- Шаг записи ответа отдаёт номер обмена наружу (2026-10-02)
--
-- ## ⚠⚠ ЗАЧЕМ
--
-- DatingAi должен сказать «эта реплика дошла», а сказать может только про
-- конкретную строку `companion_session`. Номер у неё есть — его возвращает
-- `companion_session_add`, — но наружу он не выходил.
--
-- Движок и так раскладывает результат инструмента по переменным
-- (`%companion_session_add_743_result_id%`); `outputs_map` даёт ему имя, под
-- которым его найдёт ручка `/chat/ask`.
--
-- ## ⚠ Шаг «тишины» (744) не трогаем
--
-- У него нет половины «ответ» — агент промолчал, доставлять нечего. Пустой
-- `message_id` в ответе `/chat/ask` это и означает, и отказом не является.
--
-- Идемпотентность: правка по условию — второй прогон видит `talk_message_id` на
-- месте и ничего не делает.

SET @step = (SELECT `id` FROM `agent_workflow_step`
              WHERE `workflow_id` = (SELECT `id` FROM `agent_workflow`
                                      WHERE `name` = 'dating_talk')
                AND `metadata` LIKE '%companion_session_add%'
                AND `title` LIKE '%ответ%' LIMIT 1);

SELECT 'ДО' AS этап, @step AS шаг,
       (SELECT `metadata` LIKE '%talk_message_id%'
          FROM `agent_workflow_step` WHERE `id` = @step) AS уже_отдаёт;

UPDATE `agent_workflow_step`
   SET `metadata` = JSON_SET(`metadata`, '$.tool_invoke.outputs_map.id',
                             'talk_message_id')
 WHERE `id` = @step
   AND `metadata` NOT LIKE '%talk_message_id%';

SELECT 'ПОСЛЕ' AS этап,
       (SELECT JSON_EXTRACT(`metadata`, '$.tool_invoke.outputs_map')
          FROM `agent_workflow_step` WHERE `id` = @step) AS outputs_map;
```

- [ ] **Шаг 2: Накатить и проверить**

```bash
cd ~/PycharmProjects/RhapsodyAi && docker restart uvicorn && sleep 25 && \
docker exec rhapsodyai-mysql-1 mysql -uroot -ppassword -N -B project -e \
  "SELECT JSON_EXTRACT(metadata, '\$.tool_invoke.outputs_map') FROM agent_workflow_step WHERE title LIKE '%записать ответ%'"
```

Ожидается: `{"id": "talk_message_id"}`.

- [ ] **Шаг 3: Отдать номер из ручки**

В `src/uvicorn/routers/chat/chat.py`, в `chat_ask_ai`, заменить

```python
        result_text = await asyncio.wait_for(future, timeout=timeout)
        return {"result": result_text}
```

на

```python
        result_text = await asyncio.wait_for(future, timeout=timeout)

        # ⚠⚠ Номер обмена разговора — тому, кто будет отправлять реплику.
        # `workflow_variables` движок мутирует на месте, поэтому после прогона в
        # нём лежат финальные значения, в том числе положенное шагом записи.
        #
        # ⚠ Ноль — законный исход, а не отказ: агент промолчал, прогон ушёл в
        # «Тишину», и доставлять нечего.
        try:
            message_id = int(framework_model.workflow_variables
                             .get('talk_message_id') or 0)
        except (TypeError, ValueError):
            message_id = 0

        return {"result": result_text, "message_id": message_id}
```

- [ ] **Шаг 4: Проверить на живом прогоне**

```bash
cd ~/PycharmProjects/RhapsodyAi && docker restart uvicorn && sleep 25 && \
docker logs uvicorn --since 2m 2>&1 | grep -E "talk_message_id" | tail -3
```

Ожидается строка `🏷️ Переменная '%talk_message_id%'…` либо `🔗 Алиас '%talk_message_id%'…` после ближайшего прогона `dating_talk`. Если прогонов нет — проверка переносится на задачу 6, где её делает панель.

- [ ] **Шаг 5: Коммит**

```bash
cd ~/PycharmProjects/RhapsodyAi && git add migrations/20261002100100_wf_talk_message_id_out.sql \
  src/uvicorn/routers/chat/chat.py && \
git commit -m "Доставка реплики: /chat/ask отдаёт message_id обмена разговора"
```

**⛔ Стоп-точка выкладки.** Сосед выложен, лента ещё не отбирает. Дальше правится панель.

---

## Задача 4: Колонка `message_id` двум пулам (DatingAi)

**Files:**
- Create: `migrations/20261002110000_mail_pool_message_id.sql`

**Interfaces:**
- Consumes: ничего
- Produces: `mail_reply.message_id`, `favorite_mail.message_id` — `INT NULL`

- [ ] **Шаг 1: Написать миграцию**

```sql
-- Пулы писем помнят номер обмена разговора (2026-10-02)
--
-- ## ⚠⚠ ЗАЧЕМ
--
-- Чат отправляет ответ сразу, и номер обмена живёт в памяти вызова. Письма
-- ложатся в пул и ждут круга отправки, иногда часами, — между сочинением и
-- отправкой номер теряется, и сказать соседу «эта реплика дошла» нечем.
--
-- Пулов два, потому что через `dating_talk` ходят оба: ответ на письмо
-- (`mail_reply`) и письмо избранному (`favorite_mail`). Рассылка (`mailing`) и
-- инвайты идут своими workflow и в `companion_session` не пишут вовсе —
-- колонки им не нужно.
--
-- ## ⚠ `NULL` значит «не знаем»
--
-- Строка могла родиться до этой правки либо агент промолчал. Круг отправки на
-- таком не спотыкается — просто не зовёт отметку.
--
-- Идемпотентность: обе колонки заводятся по условию.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
           AND COLUMN_NAME = 'message_id') AS у_ответов,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
           AND COLUMN_NAME = 'message_id') AS у_избранных;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
        AND COLUMN_NAME = 'message_id') = 0,
    'ALTER TABLE `mail_reply`
        ADD COLUMN `message_id` INT NULL DEFAULT NULL
            COMMENT ''Номер обмена разговора у соседа; им помечаем доставку''',
    'SELECT ''у mail_reply колонка уже есть'' AS пропуск');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
        AND COLUMN_NAME = 'message_id') = 0,
    'ALTER TABLE `favorite_mail`
        ADD COLUMN `message_id` INT NULL DEFAULT NULL
            COMMENT ''Номер обмена разговора у соседа; им помечаем доставку''',
    'SELECT ''у favorite_mail колонка уже есть'' AS пропуск');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
           AND COLUMN_NAME = 'message_id') AS у_ответов,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
           AND COLUMN_NAME = 'message_id') AS у_избранных;
```

- [ ] **Шаг 2: Накатить и проверить**

```bash
cd ~/PycharmProjects/DatingAi && docker compose restart dating_uvicorn && sleep 20 && \
PYTHONPATH=py_modules python -m mysql_.mysql_query sql \
"SELECT table_name, column_name FROM information_schema.columns
  WHERE table_schema='dating' AND column_name='message_id'"
```

Ожидается две строки: `mail_reply` и `favorite_mail`.

- [ ] **Шаг 3: Коммит**

```bash
cd ~/PycharmProjects/DatingAi && git add migrations/20261002110000_mail_pool_message_id.sql && \
git commit -m "Доставка реплики: пулы писем помнят номер обмена разговора"
```

---

## Задача 5: Клиент соседа (DatingAi)

**Files:**
- Modify: `src/rhapsody/rhapsody_api.py:525-538`

**Interfaces:**
- Consumes: `/chat/ask` с `message_id` (задача 3), `/chat/message/delivered` (задача 2)
- Produces:
  - `async def rhapsody_api_chat_talk(agent_id: int, question: str, variables: dict = None) -> dict` → `{'text': str, 'message_id': int}`
  - `async def rhapsody_api_chat_delivered(message_id: int) -> bool`
  - `rhapsody_api_chat_ask(...) -> str` остаётся как есть

- [ ] **Шаг 1: Заменить тело `rhapsody_api_chat_ask` на две функции**

В `src/rhapsody/rhapsody_api.py` заменить существующую `rhapsody_api_chat_ask` целиком на:

```python
async def rhapsody_api_chat_talk(agent_id: int, question: str,
                                 variables: dict = None) -> dict:
    """Спрашивает агента и отдаёт ответ вместе с номером обмена разговора.

    Returns:
        dict: `text` — ответ агента (пустой законен: он может промолчать),
        `message_id` — номер строки `companion_session` у соседа. Ноль значит
        «обмена нет»: агент смолчал, прогон ушёл в «Тишину».

    ## ⚠⚠ Номер нужен, чтобы отметить доставку

    Сосед пишет обмен **внутри прогона** — до того, как мы попробуем отправить
    реплику. Отметку ставит `rhapsody_api_chat_delivered`, когда реплика реально
    ушла; без неё лента для агента эту строку не покажет.

    ⚠ Зовут это только дороги `dating_talk` — чат и два пула писем. Галерея и
    почтовые агенты номера не получают: их workflow в `companion_session` не
    пишут, и спрашивать его там было бы враньём.
    """
    payload = {'question': str(question), 'agent_id': int(agent_id),
               'variables': {'chat_timeout': RHAPSODY_API_CHAT_TIMEOUT, **(variables or {})}}

    data = await _request('POST', '/chat/ask', payload, timeout=RHAPSODY_API_TIMEOUT_AGENT)

    try:
        number = int(data.get('message_id') or 0)
    except (TypeError, ValueError):
        number = 0

    return {'text': str(data.get('result') or ''), 'message_id': number}


async def rhapsody_api_chat_ask(agent_id: int, question: str,
                                variables: dict = None) -> str:
    """Спрашивает агента и отдаёт его ответ строкой.

    Пустая строка — законный ответ: агент может намеренно промолчать, и это
    решение, а не сбой.

    ⚠ Тонкая обёртка над `rhapsody_api_chat_talk` для тех, кому номер обмена не
    нужен: галерея и почтовые агенты (`mail_agent.py`) идут другими workflow, и
    `companion_session` там не пишется вовсе.
    """
    return (await rhapsody_api_chat_talk(agent_id, question, variables))['text']


async def rhapsody_api_chat_delivered(message_id: int) -> bool:
    """Говорит соседу, что наша реплика дошла до собеседника.

    Args:
        message_id: номер обмена из `rhapsody_api_chat_talk`. Ноль — звать не
            надо, и функция молча отвечает `False`.

    Returns:
        bool: легла ли отметка. `False` у уже отмеченной строки — обычный исход,
        а не беда: ручка у соседа идемпотентна.

    ## ⚠⚠ Отказ не роняет отправку

    Реплика к этому моменту **уже ушла живому человеку**, и бросить исключение
    значило бы отменить то, что отменить нельзя. Неудача метки — строка в
    журнале: агент один раз увидит в истории лишнюю свою реплику, и это
    несравнимо дешевле.
    """
    number = int(message_id or 0)

    if number <= 0:
        return False

    try:
        data = await _request('POST', '/chat/message/delivered',
                              {'message_id': number})
    except RhapsodyError as e:
        logger_info(f'[rhapsody] отметка доставки {number} не легла: {e}')

        return False

    return bool((data.get('result') or {}).get('marked'))
```

- [ ] **Шаг 2: Проверить, что прежние зовущие не сломались**

```bash
cd ~/PycharmProjects/DatingAi && grep -rn "rhapsody_api_chat_ask" --include=*.py src/ | grep -v "rhapsody_api.py"
```

Ожидается ровно две строки импорта и два вызова — `src/dating/mail_agent.py` и `src/dating/gallery_vision.py`. Обе зовут строковую обёртку, правки не нужны.

- [ ] **Шаг 3: Прогнать группу тестов чата**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest -m chat -q 2>&1 | tail -10
```

Ожидается: прежнее число passed, новых падений нет.

- [ ] **Шаг 4: Коммит**

```bash
cd ~/PycharmProjects/DatingAi && git add src/rhapsody/rhapsody_api.py && \
git commit -m "Доставка реплики: клиент соседа отдаёт номер обмена и умеет отмечать доставку"
```

---

## Задача 6: Чат отмечает доставку (DatingAi)

**Files:**
- Modify: `src/dating/answer.py:438-571` (`_agent_ask`), `src/dating/answer.py:175-250` (`answer_run`)
- Create: `tests/test_delivered_mark.py`

**Interfaces:**
- Consumes: `rhapsody_api_chat_talk`, `rhapsody_api_chat_delivered` (задача 5)
- Produces: `_agent_ask(...) -> tuple[str, str, int]` — текст, ошибка, номер обмена

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_delivered_mark.py`. ⚠ Образец — `tests/test_chat_answer_anyway.py`: своя анкета с логином на `@example.com` (за ним каскадом идёт уборка), входящая реплика в `message`, подменённый агент.

```python
"""Доставка отмечается только у реплики, которая вправду ушла.

## ⚠⚠ Зачем

Сосед пишет обмен в `companion_session` **внутри прогона** — до того, как мы
постучимся к сайту. Не отметь мы доставку отдельно, агент увидел бы в истории
свою реплику, которой собеседник не получал, и построил бы следующий ответ как
продолжение несказанного.

⚠ **Наружу сюита не ходит** (`testing.md`, §1.2): и агент, и отправка —
заглушки, проверяется то, что мы им передали.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import answer as answer_mod

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    """Анкета сюиты. Логин на `@example.com` — метка, по ней идёт уборка."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'deliver-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM chat_reply WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _said(profile_id: int, uid: str = 'u-1') -> None:
    """Входящая реплика: без неё отвечать нечего и агент не зовётся."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            "  message_uid, direction) VALUES (%s, %s, %s, %s, %s, 'in')",
            (int(profile_id), uid, 'Brian', 'привет',
             f'dl-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()


async def _profile(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s', (profile_id,))

        return dict(await db.fetchone())


@pytest.fixture
def marked(monkeypatch):
    """Номера, которые панель отметила доставленными."""
    got = []

    async def _delivered(message_id):
        got.append(int(message_id))

        return True

    monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_delivered', _delivered)

    return got


def _agent(monkeypatch, text: str, message_id: int):
    async def _talk(agent_id, question, variables=None):
        return {'text': text, 'message_id': message_id}

    monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _talk)


def _sending(monkeypatch, ok: bool):
    async def _send(profile, presence, uid, text, **kwargs):
        return {'ok': ok, 'error': '' if ok else 'сайт отказал',
                'offline': False, 'response': '',
                'message_uid': 'u1' if ok else ''}

    monkeypatch.setattr(answer_mod, 'send_message', _send)


class TestChatMarksDelivery:
    async def test_sent_reply_is_marked(self, monkeypatch, marked, girl):
        """Ушедшая реплика помечается доставленной."""
        await _said(girl)
        _agent(monkeypatch, 'hello', 777)
        _sending(monkeypatch, ok=True)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [777], 'ушедшая реплика обязана быть отмечена'

    async def test_failed_send_is_not_marked(self, monkeypatch, marked, girl):
        """⚠⚠ Неудачная отправка отметки не ставит — ради этого всё и затеяно."""
        await _said(girl)
        _agent(monkeypatch, 'hello', 778)
        _sending(monkeypatch, ok=False)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [], 'не ушедшую реплику отмечать нельзя'

    async def test_silence_marks_nothing(self, monkeypatch, marked, girl):
        """Агент промолчал — номера нет, звать нечего."""
        await _said(girl)
        _agent(monkeypatch, '', 0)
        _sending(monkeypatch, ok=True)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == []
```

⚠ Если `answer_run` не доходит до отправки (анкета не готова, собеседника нет в снимке «онлайн»), посмотреть, чем это обходит `test_chat_answer_anyway.py` для своей анкеты, и повторить тот же приём — он уже решён там.

- [ ] **Шаг 2: Убедиться, что тест падает**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest tests/test_delivered_mark.py -v 2>&1 | tail -15
```

Ожидается: `AttributeError: … has no attribute 'rhapsody_api_chat_talk'`.

- [ ] **Шаг 3: Научить `_agent_ask` отдавать номер**

В `src/dating/answer.py` заменить строку импорта:

```python
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_chat_delivered,
                                       rhapsody_api_chat_talk)
```

В сигнатуре `_agent_ask` заменить тип возврата на `tuple[str, str, int]` и добавить в докстринг:

```python
    Returns:
        tuple: ответ, ошибка и **номер обмена разговора** у соседа. Номер нужен,
        чтобы отметить доставку, когда реплика вправду уйдёт: сосед пишет обмен
        внутри прогона, то есть до отправки. Ноль — агент промолчал.
```

Заменить вызов и возврат в конце `_agent_ask`:

```python
        got = await rhapsody_api_chat_talk(
            agent_id, question,
            variables={...})          # ← блок variables не меняется
    except RhapsodyError as e:
        return '', str(e), 0

    return got['text'], '', int(got.get('message_id') or 0)
```

- [ ] **Шаг 4: Поправить три места, читающих результат `_agent_ask`**

`answer_run` (около строки 175):

```python
            answer, error, message_id = await _agent_ask(
                agent_id, question, language, profile, ...)   # ← доводы те же
```

и после отправки (около строки 244):

```python
        result = await send_message(profile, presence, uid, answer)

        # ⚠⚠ Доставку отмечаем **только у ушедшего**, и это вся суть правки.
        # Сосед записал обмен внутри прогона — до того, как мы постучались к
        # сайту. Не отметь мы её отдельно, агент увидел бы в истории реплику,
        # которой собеседник не получал.
        if result['ok']:
            await rhapsody_api_chat_delivered(message_id)
```

`answer_mail_write` (строка ~350) и `answer_mail_reply` (строка ~395) — обе кончаются `return await _agent_ask(...)`, и обе теперь отдают тройку. Поменять в их докстрингах `Returns:` на:

```python
    Returns:
        tuple: текст, ошибка и номер обмена разговора. Номер кладётся в строку
        пула и доживает до отправки: письмо уходит кругом через часы после
        сочинения.
```

- [ ] **Шаг 5: Прогнать тесты**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest tests/test_delivered_mark.py -v 2>&1 | tail -12
```

Ожидается: 3 passed.

- [ ] **Шаг 6: Прогнать группу чата целиком**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest -m chat -q 2>&1 | tail -10
```

Ожидается: без новых падений. ⚠ Одиночное красное в круговых тестах регрессией не является (`testing.md`) — перепроверить его отдельным запуском.

- [ ] **Шаг 7: Коммит**

```bash
cd ~/PycharmProjects/DatingAi && git add src/dating/answer.py tests/test_delivered_mark.py && \
git commit -m "Доставка реплики: чат отмечает ушедшее, неудачную отправку не отмечает"
```

---

## Задача 7: Пулы писем хранят номер (DatingAi)

**Files:**
- Modify: `src/dating/mail_reply.py:117-119`, `src/dating/mail_reply.py:551-565`
- Modify: `src/dating/favorite_mail.py:379-381`, `src/dating/favorite_mail.py:1144-1160`

**Interfaces:**
- Consumes: тройку из `_agent_ask` (задача 6), колонки из задачи 4
- Produces: `mail_reply.message_id` и `favorite_mail.message_id` заполнены у новых черновиков

- [ ] **Шаг 1: Пул ответов — принять номер**

`src/dating/mail_reply.py`, около строки 117:

```python
        body, error, message_id = await answer_mail_reply(
            agent_id, profile, str(letter['uid']), language,
            str(letter.get('name') or ''))
```

Там же, где вызывается `_put` (ниже по тому же циклу), добавить номер доводом: `await _put(site_id, letter, body, message_id)`.

- [ ] **Шаг 2: Пул ответов — положить номер в строку**

`src/dating/mail_reply.py`, `_put`:

```python
async def _put(site_id: int, letter: dict, body: str, message_id: int = 0) -> None:
    """Кладёт ответ в пул. `INSERT IGNORE` ловит гонку двух заходов разом.

    ⚠ Уникальный ключ по `mail_in_id`: один ответ на письмо. Два ответа подряд
    одному человеку выглядят поломкой — ею и являются.

    ⚠⚠ `message_id` — номер обмена разговора у соседа. Доживает здесь до
    отправки: письмо уносит круг через часы после сочинения, а отметить доставку
    можно только зная номер. Ноль ложится `NULL` — «не знаем».
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT IGNORE INTO mail_reply'
            ' (site_id, profile_id, mail_in_id, uid, name, body, status, message_id)'
            " VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s)",
            (int(site_id), int(letter['profile_id']), int(letter['id']),
             str(letter['uid']), str(letter['name'] or ''), body,
             int(message_id or 0) or None))
        await db.connection.commit()
```

- [ ] **Шаг 3: Пул избранных — принять номер**

`src/dating/favorite_mail.py`, около строки 379:

```python
        try:
            body, error, message_id = await answer_mail_write(
                agent_id, pair['profile'], uid, language,
                name=str(pair.get('name') or ''))
        except DatingMailAgentError as e:
            error, body, message_id = str(e), '', 0
```

Там же, где зовётся `_put`, добавить номер доводом.

- [ ] **Шаг 4: Пул избранных — положить номер в строку**

`src/dating/favorite_mail.py`, `_put`:

```python
async def _put(site_id: int, profile_id: int, pair: dict, body: str,
               message_id: int = 0) -> None:
    """Кладёт письмо в пул.

    ⚠ **Без `INSERT IGNORE`, в отличие от прочих пулов**: уникального ключа на
    пару здесь нет — одному человеку пишут много раз, в том и смысл занятия. От
    двух черновиков разом защищает отбор (`_quiet` не берёт тех, у кого черновик
    уже лежит), а гонка двух заходов стоила бы одного лишнего письма в пуле, где
    его прочтёт человек.

    ⚠⚠ `message_id` — номер обмена разговора у соседа; им отмечается доставка в
    момент отправки. Ноль ложится `NULL`: агент промолчал либо строка старая.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite_mail'
            ' (site_id, profile_id, uid, name, photo, body, status, message_id)'
            " VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s)",
            (int(site_id), int(profile_id), str(pair['uid']),
             str(pair.get('name') or ''), str(pair.get('photo') or ''), body,
             int(message_id or 0) or None))
        await db.connection.commit()
```

- [ ] **Шаг 5: Прогнать группу писем**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest -m mail -q 2>&1 | tail -10
```

Ожидается: без новых падений. Если метки `mail` нет — `python -m pytest tests/test_mail_reply.py tests/test_favorite_mail.py -q`.

- [ ] **Шаг 6: Коммит**

```bash
cd ~/PycharmProjects/DatingAi && git add src/dating/mail_reply.py src/dating/favorite_mail.py && \
git commit -m "Доставка реплики: пулы писем хранят номер обмена до отправки"
```

---

## Задача 7а: Черновик чата хранит номер (DatingAi)

⚠ **Задача добавлена после задачи 6** — её нашёл исполнитель, а план пропустил. Решение пользователя: закрыть сейчас, а не выносить в отдельную работу.

**Files:**
- Create: `migrations/20261002120000_chat_reply_talk_message_id.sql`
- Modify: `src/dating/chat_reply.py` (`chat_reply_put`, `chat_reply_waiting`)
- Modify: `src/dating/answer.py` (ветка готового черновика и вызов `chat_reply_put`)
- Modify: `tests/test_delivered_mark.py`

**Interfaces:**
- Consumes: `rhapsody_api_chat_delivered` (задача 5), тройку из `_agent_ask` (задача 6)
- Produces: `chat_reply.talk_message_id` заполнен у новых черновиков; `chat_reply_waiting` отдаёт его вместе с телом

### Зачем

Ответ в чате уходит сразу **не всегда**. Собеседника нет в сети — текст ложится черновиком в `chat_reply` и уходит **следующим кругом** (`answer.py`, ветка `ready`). Это штатный путь, ради которого пул и заведён: «текст готовится заранее — ради этого всё и делалось».

Номер обмена живёт в памяти вызова и до следующего круга не доживает. Задача 6 оставила там честный холостой ход — `rhapsody_api_chat_delivered(0)`, без похода к соседу, — но это значит, что **ушедшая** реплика остаётся у соседа неотмеченной, и агент не увидит собственных сказанных слов. Ошибка обратная той, что мы чиним, и не менее вредная.

⚠⚠ **Столбец нельзя назвать `message_id`.** В `chat_reply` это имя занято и значит совсем другое — номер реплики в нашей таблице `message`, на которую отвечаем, и на нём стоит `UNIQUE`-ключ (он же даёт протухание черновика и защиту от дублей). Отсюда имя `talk_message_id` — то же, под которым номер живёт переменной у соседа.

- [ ] **Шаг 1: Написать падающий тест**

Дописать в `tests/test_delivered_mark.py`:

```python
class TestDraftKeepsTheNumber:
    async def test_draft_carries_the_number_to_the_next_round(self, monkeypatch, marked, girl):
        """⚠⚠ Черновик, ушедший следующим кругом, помечается своим номером.

        Собеседника не было в сети — текст лёг в пул; круг спустя он вернулся, и
        реплика ушла. Номер обмена к этому времени живёт только в строке пула:
        агента во втором заходе не спрашивали.
        """
        await _said(girl)
        _agent(monkeypatch, 'hello', 991)
        _sending(monkeypatch, ok=True)

        # Первый заход: собеседника нет в сети — черновик лёг, отправки не было.
        monkeypatch.setattr(answer_mod, 'online_check',
                            lambda site_id, uid: {'online': False})
        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [], 'неотправленное отмечать нельзя'

        # Второй заход: он в сети, агента не спрашиваем — текст уже лежит.
        async def _no_agent(*args, **kwargs):
            raise AssertionError('агента во втором заходе спрашивать не должны')

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _no_agent)
        monkeypatch.setattr(answer_mod, 'online_check',
                            lambda site_id, uid: {'online': True})
        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [991], 'номер обязан дожить до отправки в строке пула'
```

⚠ Имя и форма подмены `online_check` — сверь с тем, как это место зовётся в `answer.py` после задачи 6, и с образцом `tests/test_online_guard.py`. Фикстуры `girl`, `marked`, `_said`, `_agent`, `_sending`, `_profile` уже есть в файле от задачи 6.

- [ ] **Шаг 2: Убедиться, что тест падает**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest tests/test_delivered_mark.py::TestDraftKeepsTheNumber -v 2>&1 | tail -12
```

Ожидается: `assert [] == [991]` — номер до второго круга не доехал.

- [ ] **Шаг 3: Миграция**

```sql
-- Черновик чата помнит номер обмена разговора (2026-10-02)
--
-- ## ⚠⚠ ЗАЧЕМ
--
-- Ответ в чате уходит сразу не всегда: собеседника нет в сети — текст ложится
-- черновиком и уходит следующим кругом. Номер обмена живёт в памяти вызова и до
-- следующего круга не доживает, а значит ушедшая реплика остаётся у соседа
-- неотмеченной — и агент не видит собственных сказанных слов.
--
-- ## ⚠⚠ Почему не `message_id`
--
-- Это имя в `chat_reply` **занято и значит другое**: номер реплики в нашей
-- таблице `message`, на которую отвечаем. На нём стоит `UNIQUE`-ключ, которым
-- даром получаются протухание черновика и защита от дублей круга. Второе
-- значение у того же имени развело бы два смысла в одном столбце.
--
-- `talk_message_id` — то же имя, под которым номер живёт переменной у соседа
-- (`talk_message_id` в workflow `dating_talk`).
--
-- ## ⚠ `NULL` значит «не знаем»
--
-- Строка могла родиться до этой правки либо агент промолчал. Отправка на таком
-- не спотыкается — просто не зовёт отметку.
--
-- Идемпотентность: колонка заводится по условию.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_reply'
           AND COLUMN_NAME = 'talk_message_id') AS колонка_есть;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_reply'
        AND COLUMN_NAME = 'talk_message_id') = 0,
    'ALTER TABLE `chat_reply`
        ADD COLUMN `talk_message_id` INT NULL DEFAULT NULL
            COMMENT ''Номер обмена разговора у соседа; им помечаем доставку''',
    'SELECT ''колонка уже есть'' AS пропуск');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_reply'
           AND COLUMN_NAME = 'talk_message_id') AS колонка_есть;
```

- [ ] **Шаг 4: Класть номер и отдавать его обратно**

`src/dating/chat_reply.py`:

- `chat_reply_put` принимает `talk_message_id: int = 0` последним доводом и кладёт его в строку (`int(talk_message_id or 0) or None` — ноль значит «не знаем», то есть `NULL`). В докстринге объяснить, чем он отличается от `message_id`, который уже есть в сигнатуре: тот — повод написать, этот — номер обмена у соседа.
- `chat_reply_waiting` добавляет `talk_message_id` в `SELECT`: ветка готового черновика берёт номер оттуда.

- [ ] **Шаг 5: Провести номер через `answer_run`**

В `src/dating/answer.py`:
- при `chat_reply_put(...)` передать номер, полученный от `_agent_ask`;
- в ветке готового черновика взять номер из строки: `message_id = int(ready.get('talk_message_id') or 0)`.

⚠ Умолчание `message_id = 0` перед `if not ready:` **оставить**: оно защищает от того, что переменная останется от прошлой строки цикла. Ветка готового черновика теперь перезаписывает его своим значением — это и есть правка.

- [ ] **Шаг 6: Прогнать тесты**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest tests/test_delivered_mark.py -v 2>&1 | tail -12
cd ~/PycharmProjects/DatingAi && python -m pytest -m chat -q 2>&1 | tail -8
```

Ожидается: 4 passed в файле; в группе `chat` — 684, то есть 683 от задачи 6 плюс новый.

- [ ] **Шаг 7: Коммит**

```bash
cd ~/PycharmProjects/DatingAi && git add migrations/20261002120000_chat_reply_talk_message_id.sql \
  src/dating/chat_reply.py src/dating/answer.py tests/test_delivered_mark.py && \
git commit -m "Доставка реплики: черновик чата хранит номер обмена до отправки"
```

---

## Задача 7б: Инициатива хранит номер и отмечает доставку (DatingAi)

⚠ **Задача добавлена после задачи 7** — её нашёл исполнитель, а план и два ревью пропустили. Здесь не только дыра, но и **живая поломка**: `src/dating/initiative.py:139` распаковывает две величины из `_agent_ask`, которая после задачи 6 отдаёт три.

**Files:**
- Create: `migrations/20261002130000_chat_initiative_talk_message_id.sql`
- Modify: `src/dating/initiative.py` (вызов `_agent_ask`, вызов `chat_initiative_put`)
- Modify: `src/dating/chat_initiative.py` (`chat_initiative_put`, `chat_initiative_ready`)
- Modify: `src/dating/initiative_round.py` (`_send`)
- Modify: `tests/test_delivered_mark.py`

**Interfaces:**
- Consumes: `rhapsody_api_chat_delivered` (задача 5), тройку из `_agent_ask` (задача 6)
- Produces: `chat_initiative.talk_message_id` заполнен у новых карточек; круг отправки зовёт отметку

### Зачем

Инициатива — «заговорить первой с тем, кто затих». Она ходит тем же workflow `dating_talk`, значит сосед пишет ей обмен в `companion_session` ровно так же. И это **четвёртый отложенный пул**: `initiative.py` сочиняет карточку и кладёт в `chat_initiative`, а уносит её другой круг — `initiative_round._send`, когда собеседник окажется в сети.

Два следствия:
- номер обмена до отправки не доживает — ушедшая инициатива остаётся у соседа неотмеченной, и агент не увидит собственных слов;
- прямо сейчас строка 139 падает `ValueError: too many values to unpack` на первом же заходе круга инициативы.

⚠ Имя столбца — `talk_message_id`, как у `chat_reply` (задача 7а). В `chat_initiative` имя `message_id` не занято, но столбец значит **номер обмена у соседа**, а не наш, и одинаковое имя в двух пулах чата важнее экономии букв.

- [ ] **Шаг 1: Написать падающий тест**

Дописать в `tests/test_delivered_mark.py`:

```python
class TestInitiativeMarksDelivery:
    """⚠⚠ Карточку сочиняет один круг, уносит другой: номер к моменту отправки
    живёт только в строке пула."""

    @pytest.fixture
    def card_marked(self, monkeypatch):
        """Номера, отмеченные доставленными при отправке карточки."""
        import src.dating.initiative_round as initiative_round_mod

        got = []

        async def _delivered(message_id):
            got.append(int(message_id))

            return True

        monkeypatch.setattr(initiative_round_mod, 'rhapsody_api_chat_delivered',
                            _delivered)

        return got

    async def test_sent_card_is_marked(self, monkeypatch, card_marked, girl_online):
        """Ушедшая инициатива помечается своим номером обмена."""
        import src.dating.initiative_round as initiative_round_mod

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO chat_initiative (site_id, profile_id, uid, name,'
                "  body, try_no, talk_message_id)"
                " VALUES (%s, %s, 'u-1', 'Brian', 'привет', 1, 771)",
                (STAND_SITE, girl_online))
            await db.connection.commit()

        async def _send_ok(profile, presence, uid, text, **kwargs):
            return {'ok': True, 'error': '', 'offline': False,
                    'response': '', 'message_uid': 'u1'}

        monkeypatch.setattr(initiative_round_mod, 'send_message', _send_ok)
        monkeypatch.setattr(initiative_round_mod, 'online_check',
                            lambda site_id, uid: {'online': True})

        await initiative_round_mod._send(STAND_SITE)

        assert card_marked == [771], 'номер обязан дожить до отправки в строке пула'

    async def test_failed_card_is_not_marked(self, monkeypatch, card_marked,
                                             girl_online):
        """⚠ Не ушедшую карточку отмечать нельзя."""
        import src.dating.initiative_round as initiative_round_mod

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO chat_initiative (site_id, profile_id, uid, name,'
                "  body, try_no, talk_message_id)"
                " VALUES (%s, %s, 'u-2', 'Brian', 'привет', 1, 772)",
                (STAND_SITE, girl_online))
            await db.connection.commit()

        async def _send_fail(profile, presence, uid, text, **kwargs):
            return {'ok': False, 'error': 'сайт отказал', 'offline': False,
                    'response': '', 'message_uid': ''}

        monkeypatch.setattr(initiative_round_mod, 'send_message', _send_fail)
        monkeypatch.setattr(initiative_round_mod, 'online_check',
                            lambda site_id, uid: {'online': True})

        await initiative_round_mod._send(STAND_SITE)

        assert card_marked == []
```

⚠⚠ **Анкете нужен `status = 'online'`**: `chat_initiative_ready` берёт карточки только у анкет в сети, и на обычной фикстуре `girl` из `test_delivered_mark.py` круг не увидит ничего, а тест позеленеет впустую. Заведи фикстуру `girl_online` — такую же, как `girl`, плюс `UPDATE profile SET status = 'online'` и уборка `chat_initiative` в конце. Готовый образец — фикстура `girl` в `tests/test_initiative_round.py`, строки 33–53: оттуда же бери вид вставки карточки и `STAND_SITE`.

⚠ Имена `send_message` и `online_check` сверь с тем, как они импортированы в `initiative_round.py` — подменять надо имя **в этом модуле**, а не там, откуда оно пришло.

- [ ] **Шаг 2: Убедиться, что тест падает**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest tests/test_delivered_mark.py::TestInitiativeMarksDelivery -v 2>&1 | tail -12
```

- [ ] **Шаг 3: Миграция**

По образцу `migrations/20261002120000_chat_reply_talk_message_id.sql` (задача 7а), но для таблицы `chat_initiative`. Шапка объясняет: инициатива ходит тем же `dating_talk`, пул отложенный, номер обязан дожить до отправки. `INT NULL DEFAULT NULL`, заводится по условию.

- [ ] **Шаг 4: Починить распаковку и провести номер**

`src/dating/initiative.py`:

```python
    text, error, message_id = await _agent_ask(agent, '', language, profile,
                                               uid=uid, name=name,
                                               anyway=True, initiative=True)
```

и ниже, при укладке карточки, передать номер в `chat_initiative_put`.

`src/dating/chat_initiative.py`:
- `chat_initiative_put` принимает `talk_message_id: int = 0` последним доводом, кладёт `int(talk_message_id or 0) or None`;
- `chat_initiative_ready` добавляет `i.talk_message_id` в `SELECT` — круг отправки берёт номер оттуда.

- [ ] **Шаг 5: Отметить доставку в круге отправки**

`src/dating/initiative_round.py`, в `_send`, в ветке `if result['ok']:` — рядом с `chat_initiative_sent`:

```python
        if result['ok']:
            # ⚠⚠ Доставку отмечаем только у ушедшего: сосед записал обмен внутри
            # прогона, то есть до того, как мы постучались к сайту. Номер дожил
            # до сюда в строке пула — карточку сочинял другой круг.
            await rhapsody_api_chat_delivered(int(card.get('talk_message_id') or 0))
```

⚠ Импорт — из `src.rhapsody.rhapsody_api`, единственной двери к соседу. Своей обработки отказа не добавляй: она уже внутри.

- [ ] **Шаг 6: Прогнать тесты**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest tests/test_delivered_mark.py tests/test_initiative_round.py tests/test_chat_initiative.py -v 2>&1 | tail -15
cd ~/PycharmProjects/DatingAi && python -m pytest -m chat -q 2>&1 | tail -8
```

- [ ] **Шаг 7: Коммит**

```bash
cd ~/PycharmProjects/DatingAi && git add migrations/20261002130000_chat_initiative_talk_message_id.sql \
  src/dating/initiative.py src/dating/chat_initiative.py src/dating/initiative_round.py \
  tests/test_delivered_mark.py && \
git commit -m "Доставка реплики: инициатива хранит номер обмена и отмечает доставку"
```

---

## Задача 8: Отправка письма отмечает доставку (DatingAi)

**Files:**
- Modify: `src/dating/mail_send.py:241-320` (`mail_send_mark`)
- Modify: `tests/test_delivered_mark.py`

**Interfaces:**
- Consumes: `rhapsody_api_chat_delivered` (задача 5), `message_id` в строке пула (задача 7)
- Produces: ничего нового наружу

- [ ] **Шаг 1: Дописать тест в `tests/test_delivered_mark.py`**

⚠ `mail_send_mark` пишет строку пула **прямым SQL** и зовёт `log_write` — приватной функции-обёртки там нет, подменять нечего. Поэтому письмо заводится настоящей строкой, как это делает остальная сюита.

Дописать в `tests/test_delivered_mark.py` (импорты `mail_send_mod` — к верхним):

```python
from src.dating import mail_send as mail_send_mod


@pytest.fixture
async def letter(girl):
    """Строка пула ответов с номером обмена. Убирается за собой."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_reply'
            ' (site_id, profile_id, mail_in_id, uid, name, body, status, message_id)'
            " VALUES (%s, %s, %s, 'u-1', 'Brian', 'текст', 'draft', 555)",
            (STAND_SITE, int(girl), int(uuid.uuid4().int % 1_000_000_000)))
        row_id = int(db.lastrowid)
        await db.connection.commit()

    yield {'id': row_id, 'profile_id': int(girl), 'uid': 'u-1',
           'name': 'Brian', 'message_id': 555}

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM mail_reply WHERE id = %s', (row_id,))
        await db.connection.commit()


@pytest.fixture
def mail_marked(monkeypatch):
    """Номера, отмеченные доставленными при отправке письма."""
    got = []

    async def _delivered(message_id):
        got.append(int(message_id))

        return True

    monkeypatch.setattr(mail_send_mod, 'rhapsody_api_chat_delivered', _delivered)

    return got


class TestMailMarksDelivery:
    async def test_sent_letter_is_marked(self, mail_marked, letter):
        """Ушедшее письмо из пула ответов помечается доставленным."""
        await mail_send_mod.mail_send_mark(letter, sent=True, table='mail_reply')

        assert mail_marked == [555]

    async def test_failed_letter_is_not_marked(self, mail_marked, letter):
        """⚠ Неудачная отправка отметки не ставит."""
        await mail_send_mod.mail_send_mark(letter, sent=False,
                                           error='сайт отказал', table='mail_reply')

        assert mail_marked == []

    async def test_mailing_is_never_marked(self, mail_marked, letter):
        """⚠⚠ Рассылка идёт своим workflow и в `companion_session` не пишет.

        Номер в строке есть, таблица другая — отметка уйти не должна: у рассылки
        обмена разговора нет, и совпавший номер указал бы на чужую строку.
        """
        await mail_send_mod.mail_send_mark({**letter, 'id': 0}, sent=True,
                                           table='mailing')

        assert mail_marked == []
```

- [ ] **Шаг 2: Убедиться, что тест падает**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest tests/test_delivered_mark.py::TestMailMarksDelivery -v 2>&1 | tail -12
```

Ожидается: `AttributeError: … has no attribute 'rhapsody_api_chat_delivered'`.

- [ ] **Шаг 3: Отметить доставку в `mail_send_mark`**

В `src/dating/mail_send.py` добавить импорт:

```python
from src.rhapsody.rhapsody_api import rhapsody_api_chat_delivered
```

Константу рядом с прочими:

```python
# Пулы, письма которых сочиняет `dating_talk`, — только их обмены лежат у соседа
# в `companion_session`.
#
# ⚠⚠ `mailing` сюда не входит и входить не должен: рассылка идёт своим workflow
# (`mail_agent.py`), обмена разговора у неё нет, и отметка ушла бы в пустоту либо,
# хуже, в чужую строку с совпавшим номером.
MAIL_SEND_TALK_TABLES = ('mail_reply', 'favorite_mail')
```

В конец `mail_send_mark`, после записи строки и журнала:

```python
    # ⚠⚠ **Доставка отмечается здесь, а не в круге отправки.** Через эту точку
    # проходят обе дороги письма — круг и путь через панель
    # (`mail_reply_panel.py`), — и она уже знает таблицу доводом `table`. Две
    # точки вместо одной разошлись бы ровно так же, как разошлись бы записи
    # исхода, которые тут и сведены в одно место.
    #
    # ⚠ Отметка **после** записи исхода: сосед может не ответить, а исход письма
    # обязан лечь в любом случае.
    if sent and table in MAIL_SEND_TALK_TABLES:
        await rhapsody_api_chat_delivered(int(letter.get('message_id') or 0))
```

- [ ] **Шаг 4: Прогнать тесты**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest tests/test_delivered_mark.py -v 2>&1 | tail -12
```

Ожидается: 6 passed.

- [ ] **Шаг 5: Прогнать сюиту целиком — перед выкладкой**

```bash
cd ~/PycharmProjects/DatingAi && python -m pytest -q 2>&1 | tail -15
```

Ожидается: без новых падений против прогона до начала работы.

- [ ] **Шаг 6: Коммит**

```bash
cd ~/PycharmProjects/DatingAi && git add src/dating/mail_send.py tests/test_delivered_mark.py && \
git commit -m "Доставка реплики: отправка письма отмечает обмен разговора доставленным"
```

**⛔ Стоп-точка выкладки.** Панель выложена и ставит отметки. Только теперь включается отбор.

---

## Задача 9: Лента прячет недоставленное (RhapsodyAi)

**Files:**
- Modify: `src/tool/companion/companion_session.py:165-215` (`companion_session_rows`)
- Modify: `tests/test_companion_session_delivered.py`

**Interfaces:**
- Consumes: `delivered_at` (задача 1), отметки от панели (задачи 6 и 8)
- Produces: лента без неподтверждённых реплик

- [ ] **Шаг 1: Дописать тест**

В `tests/test_companion_session_delivered.py`:

```python
from src.tool.companion.companion_session import companion_session_history


class TestHistoryHidesUndelivered:
    async def test_undelivered_answer_is_hidden(self, pair):
        """⚠⚠ Неотмеченный ответ в ленту не идёт — ради этого всё и затеяно."""
        await companion_session_add(pair[0], pair[1],
                                    question='are you there?', answer='yes!')

        got = await companion_session_history(pair[0], pair[1])

        assert 'are you there?' in got, 'его слова доставлены самим приходом'
        assert 'yes!' not in got, 'наш неотмеченный ответ показывать нельзя'

    async def test_delivered_answer_is_shown(self, pair):
        """Отмеченный — показывается."""
        made = await companion_session_add(pair[0], pair[1],
                                           question='are you there?', answer='yes!')
        await companion_session_delivered(int(made['id']))

        got = await companion_session_history(pair[0], pair[1])

        assert 'yes!' in got

    async def test_row_without_visible_half_is_skipped(self, pair):
        """Строка, где нечего показать, в ленту не идёт вовсе."""
        await companion_session_add(pair[0], pair[1], question='', answer='ghost')

        got = await companion_session_history(pair[0], pair[1])

        assert got == ''
```

- [ ] **Шаг 2: Убедиться, что первый и третий падают**

```bash
cd ~/PycharmProjects/RhapsodyAi && python -m pytest tests/test_companion_session_delivered.py::TestHistoryHidesUndelivered -v 2>&1 | tail -12
```

Ожидается: `test_undelivered_answer_is_hidden` и `test_row_without_visible_half_is_skipped` — FAIL; `test_delivered_answer_is_shown` — PASS.

- [ ] **Шаг 3: Включить отбор**

В `companion_session_rows` заменить `SELECT` на:

```python
            await db.execute(
                'SELECT s.`question`, s.`question_at`,'
                # ⚠⚠ **Отбор половиной, а не строкой.** Доставка бывает только у
                # нашей реплики: его слова доставлены самим фактом прихода.
                # Отбери мы строку целиком — вместе с неподтверждённым ответом
                # пропали бы и его слова, в том числе у «тишины», где нашего
                # ответа нет вовсе.
                "       IF(s.`delivered_at` IS NULL, '', s.`answer`) AS `answer`,"
                '       s.`answer_at`'
                '  FROM `companion_session` s'
                '  JOIN `companion` o ON o.`id` = s.`owner_id`'
                '  JOIN `companion` c ON c.`id` = s.`companion_id`'
                ' WHERE o.`unique_id` = %s AND c.`unique_id` = %s'
                '   AND s.`kind` = %s'
                # ⚠⚠ **Строка, которой нечего показать, отсекается здесь — до
                # `LIMIT`.** Отсеки её после выборки, и «последние двадцать»
                # считали бы двадцать строк, из которых видимых три: лента
                # выходила бы короче предела тем сильнее, чем хуже доходили
                # ответы, — то есть ровно тогда, когда история нужнее всего.
                "   AND (s.`question` <> '' OR s.`delivered_at` IS NOT NULL)"
                # ⚠ По времени, а не по `id`: перенесённые обмены ложатся в базу
                # позже, чем стоят в разговоре.
                ' ORDER BY COALESCE(s.`question_at`, s.`answer_at`) DESC,'
                '          s.`id` DESC'
                ' LIMIT %s',
                (owner_key, companion_key, channel, take))
```

Дописать в докстринг `companion_session_rows`:

```python
    ## ⚠⚠ Неподтверждённый ответ не показывается

    `delivered_at IS NULL` значит «мы это сочинили, но собеседник не получил»:
    обмен пишется внутри прогона, а отправляет его DatingAi после. Показать
    такое агенту — заставить его продолжать несказанное.
```

- [ ] **Шаг 4: Прогнать тесты**

```bash
cd ~/PycharmProjects/RhapsodyAi && python -m pytest tests/test_companion_session_delivered.py -v 2>&1 | tail -15
```

Ожидается: 7 passed.

- [ ] **Шаг 5: Проверить на живых данных**

```bash
docker exec rhapsodyai-mysql-1 mysql -uroot -ppassword -N -B project -e "
SELECT COUNT(*) AS видимых FROM companion_session
 WHERE question <> '' OR delivered_at IS NOT NULL;
SELECT COUNT(*) AS скрытых FROM companion_session
 WHERE question = '' AND delivered_at IS NULL;"
```

Ожидается: «скрытых» — небольшое число свежих строк, у которых отправка не удалась либо ещё идёт. Если скрытых сотни — бэкфилл задачи 1 не отработал, вернуться к ней.

- [ ] **Шаг 6: Коммит**

```bash
cd ~/PycharmProjects/RhapsodyAi && git add src/tool/companion/companion_session.py \
  tests/test_companion_session_delivered.py && \
git commit -m "Доставка реплики: лента агента показывает только подтверждённые ответы"
```

---

## Задача 10: Документация (DatingAi)

**Files:**
- Modify: `.claude/docs/dating.md` (раздел «Что видит агент, когда отвечает в чате»)
- Modify: `CLAUDE.md` (строка Development Patterns)

**Interfaces:**
- Consumes: всё сделанное
- Produces: ничего в коде

- [ ] **Шаг 1: Дописать раздел в `.claude/docs/dating.md`**

В подраздел «⚠⚠ Ленту собираем не мы — её собирает сосед», после абзаца про расхождение двух историй, заменить этот абзац на:

```markdown
⚠⚠ **Расхождение двух историй чинится отметкой доставки.** Запись обмена стоит
**внутри прогона** — до того, как ответ вернётся к нам и мы попробуем его
отправить. Поэтому у строки `companion_session` есть `delivered_at`: сосед
кладёт её пустой, панель отмечает её, когда реплика вправду ушла
(`rhapsody_api_chat_delivered`), а лента для агента показывает половину «ответ»
только у отмеченных. Его слова показываются всегда — они доставлены самим фактом
прихода.

⚠ У писем номер обмена доживает до отправки **в строке пула**
(`mail_reply.message_id`, `favorite_mail.message_id`): письмо уносит круг через
часы после сочинения. Отмечает его одна точка на оба пула — `mail_send_mark`.
```

- [ ] **Шаг 2: Добавить строку в `CLAUDE.md`**

В раздел «Development Patterns», после строки про «Описание собеседника с сайта»:

```markdown
- **Реплика в истории агента, которой собеседник не получал** → `.claude/docs/dating.md`, «Что видит агент» (⚠⚠ обмен пишется **внутри прогона** у соседа, то есть до нашей отправки; чинит это `companion_session.delivered_at` — сосед кладёт строку пустой, панель отмечает её после удачной отправки. ⚠ Отбор идёт **половиной**, а не строкой: его слова доставлены самим приходом. ⚠⚠ У писем номер живёт в строке пула — письмо уходит кругом через часы после сочинения, и отмечает его `mail_send_mark`, одна точка на оба пула)
```

- [ ] **Шаг 3: Коммит**

```bash
cd ~/PycharmProjects/DatingAi && git add .claude/docs/dating.md CLAUDE.md && \
git commit -m "Доставка реплики: описание в документации проекта"
```

---

## Порядок выкладки

⚠⚠ **Правилось после финального ревью. Прежний порядок был неверен** — ниже сказано, почему.

**Сперва панель целиком, потом сосед целиком.**

1. **DatingAi, весь `master`.** Колонки четырёх пулов, клиент, пять дорог отметки.
   ⚠ Сперва `py_modules`, если он менялся, — иначе прод падает `ImportError`.
2. **RhapsodyAi, весь `master`.** Колонка, бэкфилл, ручка, `message_id` в ответе и
   отбор в ленте — одним пушем.

### ⚠⚠ Почему именно так, а не «сосед → панель → сосед»

Прежний план требовал трёх шагов с соседом по краям. **Так выложить нельзя:**
`RhapsodyAi/.github/workflows/deploy.yml` триггерится на `push` в `master` и
делает на сервере `git reset --hard origin/master`. Коммиты «первого шага» и
«третьего» лежат на одном `master` — один пуш везёт отбор вместе с колонкой, и
разделить их обычной выкладкой нечем.

Выложи соседа первым — и каждый обмен, записанный **между** двумя выкладками,
останется `delivered_at = NULL` навсегда: старая панель отметку звать не умеет, а
задним числом её никто не проставит. Агент перестанет видеть по этим строкам
собственные слова — ровно обратная поломка той, от которой лечимся.

### Почему обратный порядок безопасен

**Панель совместима со старым соседом.** Тот не отдаёт `message_id` — панель
получает ноль, а `rhapsody_api_chat_delivered(0)` возвращает `False`, **не ходя
наружу**: ни отметок, ни 404 в журнале.

**Окно между выкладками закрывает бэкфилл.** Миграция соседа проставляет
`delivered_at = answer_at` всему, что уже в таблице, — включая строки, записанные
в это окно. Отбор включается ровно тогда, когда панель уже шлёт настоящие номера.

⚠ Остаточная щель — секунды перекрытия blue-green, пока Caddy держит старый слот.
