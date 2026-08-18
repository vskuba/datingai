# Автовосстановление входа анкет — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** смена держателя сокетов сама замечает протухший вход анкеты и восстанавливает его браузером, а пока не выходит — на карточке мигает предупреждение.

**Architecture:** новый цикл `_revive_loop` внутри смены (`dating_chat_hold_run`) раз в минуту делает полный опрос присутствия по анкетам с `keep_online = 1`; неудача начинает череду попыток входа по лестнице 1/5/15/45/90 минут → каждые 2 часа → сдача через сутки. Состояние череды лежит в `dating_girl`, потому что смена конечна (6 часов), а череда тянется до суток. Вход выносится из ручки роутера в `src/dating/dating_login.py`, чтобы у автомата и у кнопки была одна реализация.

**Tech Stack:** Python 3.11, FastAPI, aiomysql, rq, httpx; MySQL 8; тесты — pytest + httpx по HTTP в запущенную панель.

## Global Constraints

- Схема правится **только** в `migrations/0001.sql`; на живых базах она догоняется идемпотентным файлом в `data/migration_manual/` (`.claude/docs/migration.md`).
- Обращения к RhapsodyAi — **только** через `src/rhapsody/rhapsody_api.py`.
- Журналы не роняют работу: любая запись в журнал гасит исключение.
- Русские комментарии и docstring'и, как во всём проекте; комментарий объясняет **почему**, а не пересказывает код.
- Тесты сюиты ходят по HTTP в запущенную панель: перед прогоном `docker compose up -d`, команда — `ADMIN_PASSWORD=<пароль> uv run --with pytest --with pytest-asyncio --with httpx pytest tests/ -q`.
- Лестница пауз: **1, 5, 15, 45, 90 минут, дальше каждые 2 часа**; сдача через **24 часа** от начала череды.
- Профилактический вход — при возрасте входа больше **20 часов**.

---

### Task 1: Поля состояния в базе

**Files:**
- Modify: `migrations/0001.sql` (таблица `dating_girl`)
- Create: `data/migration_manual/2026-08-18_dating_relogin.sql`
- Modify: `.claude/docs/migration.md` (новый раздел про ручной догон)

**Interfaces:**
- Consumes: ничего.
- Produces: колонки `dating_girl.keep_online` (TINYINT(1), 0), `logged_in_at` (DATETIME NULL), `relogin_since` (DATETIME NULL), `relogin_tries` (INT, 0), `relogin_next_at` (DATETIME NULL).

- [ ] **Step 1: Добавить колонки в схему**

В `migrations/0001.sql`, в `CREATE TABLE dating_girl`, сразу после строки `last_error`:

```sql
    -- Анкета должна быть в сети: ставит успешный вход, снимает «Отключить». По
    -- этой пометке смена держателя восстанавливает вход сама. Отдельным полем, а
    -- не `storage_state IS NOT NULL`: неудачная проверка входа состояние стирает,
    -- и анкета выпадала бы из-под присмотра ровно тогда, когда её надо чинить.
    `keep_online` TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Держать анкету в сети автоматически',
    -- Когда вход сняли последний раз. Нужен профилактике: сессия сайта живёт
    -- около суток, и обновлять её лучше заранее, а не в середине разговора.
    `logged_in_at` DATETIME NULL COMMENT 'Когда последний раз снят storage_state',
    -- Череда неудачных попыток входа. Пусто — всё хорошо. Живёт в базе, а не в
    -- памяти смены: смена длится шесть часов, а череда тянется до суток.
    `relogin_since` DATETIME NULL COMMENT 'Начало череды неудачных входов',
    `relogin_tries` INT NOT NULL DEFAULT 0 COMMENT 'Сколько попыток сделано в череде',
    `relogin_next_at` DATETIME NULL COMMENT 'Когда следующая попытка входа',
```

- [ ] **Step 2: Написать файл ручного догона**

Создать `data/migration_manual/2026-08-18_dating_relogin.sql`:

```sql
-- Поля автовосстановления входа: dating_girl (выпуск 2026-08-18)
--
-- Зачем вручную: `migrations/0001.sql` — схема для чистой установки, и yoyo
-- применяет её один раз. На живой базе (локальной и прод) новых колонок от правки
-- файла не появится, а без них смена держателя не поднимется: она их читает.
--
-- ЧТО МЕНЯЕТСЯ: пять колонок в `dating_girl`. Данные не переписываются.
--
-- ОДНО ИСКЛЮЧЕНИЕ — `keep_online` у тех, кто уже в сети: их вход снят человеком
-- раньше, и без этой строки автомат про них не узнал бы до следующего ручного
-- входа. Ставится там, где состояние входа есть.
--
-- Идемпотентность: каждая колонка добавляется по сверке с `information_schema`.
-- Файл можно гонять сколько угодно раз.
--
-- ПЕРЕД ПРОГОНОМ: дамп базы (`.claude/docs/migration.md`, раздел 3).

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_girl'
           AND COLUMN_NAME = 'keep_online') AS колонки_есть,
       (SELECT COUNT(*) FROM dating_girl) AS всего_анкет;

-- ------------------------------------------- 1. Колонки
SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_girl'
        AND COLUMN_NAME = 'keep_online') = 0,
    'ALTER TABLE `dating_girl` ADD COLUMN `keep_online` TINYINT(1) NOT NULL DEFAULT 0
       COMMENT ''Держать анкету в сети автоматически'' AFTER `last_error`',
    'SELECT ''keep_online уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_girl'
        AND COLUMN_NAME = 'logged_in_at') = 0,
    'ALTER TABLE `dating_girl` ADD COLUMN `logged_in_at` DATETIME NULL
       COMMENT ''Когда последний раз снят storage_state'' AFTER `keep_online`',
    'SELECT ''logged_in_at уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_girl'
        AND COLUMN_NAME = 'relogin_since') = 0,
    'ALTER TABLE `dating_girl` ADD COLUMN `relogin_since` DATETIME NULL
       COMMENT ''Начало череды неудачных входов'' AFTER `logged_in_at`',
    'SELECT ''relogin_since уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_girl'
        AND COLUMN_NAME = 'relogin_tries') = 0,
    'ALTER TABLE `dating_girl` ADD COLUMN `relogin_tries` INT NOT NULL DEFAULT 0
       COMMENT ''Сколько попыток сделано в череде'' AFTER `relogin_since`',
    'SELECT ''relogin_tries уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_girl'
        AND COLUMN_NAME = 'relogin_next_at') = 0,
    'ALTER TABLE `dating_girl` ADD COLUMN `relogin_next_at` DATETIME NULL
       COMMENT ''Когда следующая попытка входа'' AFTER `relogin_tries`',
    'SELECT ''relogin_next_at уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

-- ------------------------------------------- 2. Кто уже в сети — под присмотр
UPDATE dating_girl SET keep_online = 1, logged_in_at = COALESCE(logged_in_at, last_seen_at)
 WHERE storage_state IS NOT NULL AND keep_online = 0;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, id, login, keep_online, logged_in_at, relogin_since FROM dating_girl;
```

- [ ] **Step 3: Прогнать на локальной базе дважды**

```bash
docker exec -i dating_mysql mysql -udeveloper -ppassword --default-character-set=utf8mb4 \
  dating < data/migration_manual/2026-08-18_dating_relogin.sql
docker exec -i dating_mysql mysql -udeveloper -ppassword --default-character-set=utf8mb4 \
  dating < data/migration_manual/2026-08-18_dating_relogin.sql
```

Пароль базы взять из `.env` (`MYSQL_USER`, `MYSQL_PASSWORD`).
Ожидается: первый прогон добавляет колонки, второй печатает «уже на месте» пять раз и ничего не меняет.

- [ ] **Step 4: Описать приём в документации**

В `.claude/docs/migration.md` после раздела «2. Как менять» добавить:

```markdown
## 2.1 Живая база: ручной догон

`yoyo` применяет `0001.sql` **один раз** и запоминает это в `_yoyo_migration`.
Правка файла доезжает только до чистой установки: на локальной базе с данными и на
проде новых колонок от неё не появится, а код их уже читает — и падает.

Поэтому изменение существующей таблицы делается **дважды**: правка `0001.sql`
(источник схемы) и одноразовый файл `data/migration_manual/YYYY-MM-DD_<что>.sql`,
который догоняет живую базу. Автоматически такие файлы не применяются — их гонят
руками, локально и на проде.

Файл обязан быть идемпотентным: каждый шаг сверяется с `information_schema` и на
повторном прогоне ничего не делает. В шапке — что меняется и что теряется.
```

- [ ] **Step 5: Commit**

```bash
git add migrations/0001.sql data/migration_manual/2026-08-18_dating_relogin.sql .claude/docs/migration.md
git commit -m "Поля автовосстановления входа в dating_girl"
```

---

### Task 2: Лестница попыток — чистая логика

**Files:**
- Create: `src/dating/dating_revive.py`
- Test: `tests/test_revive_schedule.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `DATING_REVIVE_PERIOD_SEC = 60`, `DATING_REVIVE_AGE_SEC = 20 * 3600`, `DATING_REVIVE_GIVE_UP_SEC = 24 * 3600`
  - `dating_revive_delay(tries: int) -> int` — секунды до попытки номер `tries` (0 — первая)
  - `dating_revive_state(relogin_since: datetime | None, now: datetime) -> str` — `'ok' | 'failing' | 'given_up'`
  - `dating_revive_due(relogin_next_at: datetime | None, now: datetime) -> bool`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_revive_schedule.py`:

```python
"""Лестница попыток автовхода — чистая арифметика, без сети и базы.

Проверяется тестами, потому что цена ошибки несимметрична: слишком частые попытки
долбят сайт настоящим входом через браузер, слишком редкие оставляют анкету
молчащей на часы.
"""

from datetime import datetime, timedelta

from src.dating.dating_revive import (DATING_REVIVE_GIVE_UP_SEC, dating_revive_delay,
                                      dating_revive_due, dating_revive_state)

NOW = datetime(2026, 8, 18, 12, 0, 0)


class TestReviveDelay:

    def test_ladder_is_exact(self):
        """1, 5, 15, 45, 90 минут — ровно в таком порядке."""
        assert [dating_revive_delay(n) // 60 for n in range(5)] == [1, 5, 15, 45, 90]

    def test_tail_is_two_hours(self):
        """После лестницы — каждые два часа, сколько бы попыток ни прошло."""
        assert dating_revive_delay(5) // 60 == 120
        assert dating_revive_delay(12) // 60 == 120

    def test_negative_tries_is_first_step(self):
        """Битое значение из базы не должно давать отрицательную паузу."""
        assert dating_revive_delay(-3) // 60 == 1


class TestReviveState:

    def test_no_chain_is_ok(self):
        assert dating_revive_state(None, NOW) == 'ok'

    def test_fresh_chain_is_failing(self):
        assert dating_revive_state(NOW - timedelta(hours=3), NOW) == 'failing'

    def test_day_old_chain_is_given_up(self):
        """Сутки — рубеж: дальше автомат не пробует, и карточка перестаёт мигать."""
        old = NOW - timedelta(seconds=DATING_REVIVE_GIVE_UP_SEC + 1)
        assert dating_revive_state(old, NOW) == 'given_up'

    def test_exactly_a_day_is_given_up(self):
        old = NOW - timedelta(seconds=DATING_REVIVE_GIVE_UP_SEC)
        assert dating_revive_state(old, NOW) == 'given_up'


class TestReviveDue:

    def test_no_time_set_is_not_due(self):
        assert dating_revive_due(None, NOW) is False

    def test_past_time_is_due(self):
        assert dating_revive_due(NOW - timedelta(seconds=1), NOW) is True

    def test_future_time_is_not_due(self):
        assert dating_revive_due(NOW + timedelta(minutes=5), NOW) is False
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run --with pytest --with pytest-asyncio --with httpx pytest tests/test_revive_schedule.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dating.dating_revive'`

- [ ] **Step 3: Написать модуль**

Создать `src/dating/dating_revive.py`:

```python
"""Восстановление входа анкеты: когда пробовать и когда сдаться.

Вход на сайт живёт около суток. Когда он протухает, анкета уходит из сети молча:
сообщения перестают приходить, а сайт не сообщает об этом ничем, кроме отказа в
опросе. Здесь — правила, по которым смена держателя решает, пора ли входить заново.

Лестница пауз, а не постоянный интервал: половина причин временные (сайт лёг,
сосед недоступен), и они рассасываются за минуты; вторая половина требует человека,
и долбить сайт настоящим входом через браузер раз в минуту ради них нельзя.

Сутки — рубеж, за которым автомат перестаёт пробовать. Дальше это уже не сбой, а
поломка: пароль сменили, анкету закрыли, сайт просит подтверждение.
"""

from datetime import datetime

# Как часто смена сверяет вход анкет с сайтом.
DATING_REVIVE_PERIOD_SEC = 60

# Возраст входа, после которого он обновляется профилактически. Сессия сайта живёт
# около суток, и ловить её протухание в середине разговора незачем.
DATING_REVIVE_AGE_SEC = 20 * 3600

# Сколько терпим череду неудач, прежде чем перестать пробовать.
DATING_REVIVE_GIVE_UP_SEC = 24 * 3600

# Лестница пауз в минутах и хвост после неё.
DATING_REVIVE_STEPS_MIN = (1, 5, 15, 45, 90)
DATING_REVIVE_TAIL_MIN = 120


def dating_revive_delay(tries: int) -> int:
    """Сколько ждать до попытки номер `tries` (0 — первая), в секундах."""
    step = max(0, int(tries))
    minutes = (DATING_REVIVE_STEPS_MIN[step] if step < len(DATING_REVIVE_STEPS_MIN)
               else DATING_REVIVE_TAIL_MIN)

    return minutes * 60


def dating_revive_state(relogin_since: datetime | None, now: datetime) -> str:
    """Состояние анкеты: `ok`, `failing` (череда идёт) или `given_up` (сдались).

    Считается по времени, а не по наличию назначенной попытки: так ответ один и
    тот же у страницы, у журнала и у самой смены, чем бы ни кончился её прошлый круг.
    """
    if not relogin_since:
        return 'ok'

    lasted = (now - relogin_since).total_seconds()

    return 'given_up' if lasted >= DATING_REVIVE_GIVE_UP_SEC else 'failing'


def dating_revive_due(relogin_next_at: datetime | None, now: datetime) -> bool:
    """Настал ли срок очередной попытки."""
    return bool(relogin_next_at) and relogin_next_at <= now
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run --with pytest --with pytest-asyncio --with httpx pytest tests/test_revive_schedule.py -q`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/dating/dating_revive.py tests/test_revive_schedule.py
git commit -m "Лестница попыток автовхода"
```

---

### Task 3: Вход как функция, а не тело ручки

**Files:**
- Create: `src/dating/dating_login.py`
- Modify: `src/uvicorn/routers/dating/dating_girl.py` (ручка `dating_girl_login`, строки 204-322; вспомогательные функции `_profile_page`, `_ws_capture`, `_state_save`, `_state_clear`, `_session_close_old`, `_session_all`, `_session_close`, `_session_open`, `_scenario_login`, `_site_check` — строки 758-930)
- Test: `tests/test_girl_crud.py` (проверка, что ручка входа осталась на месте и закрыта без сессии)

**Interfaces:**
- Consumes: `_girl_row`, `_json_load` остаются в роутере.
- Produces: `dating_login_run(girl_id: int, scenario_id: int = 0, session_id: str = '') -> dict` — возвращает `{'status': 'success'|'error', 'session_id': str, 'scenario': str, 'error': str, 'profile': dict}`; `DatingLoginError(RuntimeError)` — «войти нечем» (нет пароля, нет сценария, нет сайта).

- [ ] **Step 1: Написать модуль входа**

Создать `src/dating/dating_login.py`. Тело — то же, что сейчас в ручке (строки 216-322 файла `dating_girl.py`), с тремя отличиями:

1. вместо `HTTPException` бросается `DatingLoginError` — модуль зовут и из воркера, где HTTP нет;
2. при успехе пишутся `keep_online = 1`, `logged_in_at = NOW()`;
3. вспомогательные функции переносятся сюда из `dating_girl.py:758-930` **телом дословно**, но те, которыми пользуются другие ручки, получают публичные имена — роутер будет звать их по импорту:

| Было в роутере | Стало в `dating_login.py` |
|---|---|
| `_profile_page` | `dating_login_profile_page` |
| `_ws_capture` | `dating_login_ws_capture` |
| `_state_clear` | `dating_login_state_clear` |
| `_session_open` | `dating_login_session_open` |
| `_session_close` | `dating_login_session_close` |
| `_session_all` | `dating_login_session_all` |
| `_state_save`, `_session_close_old`, `_scenario_login`, `_site_check`, `_json_load` | остаются приватными (`_имя`): наружу их никто не звал |

`_site_check` в модуле входа бросает `DatingLoginError`, а не `HTTPException`, — модуль обязан работать без FastAPI. Роутерная версия с тем же именем остаётся на месте для прочих ручек.

Шапка модуля:

```python
"""Вход под анкетой: прогон сценария в браузере и всё, что делает его настоящим.

Отдельным модулем, а не телом ручки, потому что входить умеет не только человек:
смена держателя сокетов входит сама, когда сессия сайта протухла
(`dating_revive`). Две реализации входа разошлись бы на первой же правке — одна
половина системы обновляла бы состояние, другая нет.

Вход — не «нажать кнопку»: сценарий отчитывается успехом и на неверном пароле,
потому что его последние шаги (ожидание кабинета) помечены необязательными — у
мужской и женской анкеты кабинет разный. Поэтому после сценария идёт проверка
опросом, и только подтверждённый вход считается входом.

Ошибки наружу — `DatingLoginError` с текстом для журнала анкеты: HTTP-кода здесь
нет, его ставит ручка.
"""
```

Публичная функция:

```python
async def dating_login_run(girl_id: int, scenario_id: int = 0, session_id: str = '') -> dict:
    """Входит под учёткой: сценарий, состояние, проверка опросом, адрес сокета.

    Args:
        girl_id: номер учётки.
        scenario_id: сценарий входа; 0 — найти по подстановкам сайта.
        session_id: готовая вкладка; пусто — завести свою и закрыть после.

    Returns:
        dict: `status` — `success` или `error`, `session_id`, `scenario`, `error`,
        `profile`.

    Raises:
        DatingLoginError: входить нечем — нет пароля, сайта или сценария входа.
    """
```

Внутри — тело нынешней ручки. Замены построчно:

```python
    # было: girl = await _girl_row(girl_id)
    girl = await _girl_row(girl_id)          # локальная копия, см. Step 2
    site = await _site_check(int(girl['site_id']))
    if not str(girl.get('password') or ''):
        raise DatingLoginError('у учётки не задан пароль')

    scenario = await _scenario_login(int(girl['site_id']), scenario_id)
    if not scenario:
        raise DatingLoginError('для сайта нет сценария входа: заведите его на вкладке '
                               '«Сценарии» с подстановками логина и пароля')
```

Дальше — без изменений до места, где вход подтверждён опросом. Там, сразу после
строки журнала «Вход признан сайтом», добавляется:

```python
        # Анкета под присмотром автомата: раз человек её ввёл, она должна быть в
        # сети и дальше. Здесь же гасим череду неудач — вход только что удался.
        await _keep_online_set(girl_id)
```

И новая приватная функция в конце модуля:

```python
async def _keep_online_set(girl_id: int) -> None:
    """Успешный вход: анкета под присмотром, череда неудач обнулена."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_girl SET keep_online = 1, logged_in_at = NOW(), '
                             '       relogin_since = NULL, relogin_tries = 0, '
                             '       relogin_next_at = NULL WHERE id = %s', (int(girl_id),))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] пометка keep_online не поставилась: {e}')


async def _girl_row(girl_id: int) -> dict:
    """Учётка из базы. Нет такой — `DatingLoginError`: входить не под кем."""
    row = await DatingGirlRepository().find(girl_id)
    if not row:
        raise DatingLoginError('учётка не найдена')

    return dict(row)
```

Класс ошибки — сразу после импортов:

```python
class DatingLoginError(RuntimeError):
    """Входить нечем. Текст пригоден и для журнала анкеты, и для ответа ручки."""
```

- [ ] **Step 2: Сделать ручку обёрткой**

В `src/uvicorn/routers/dating/dating_girl.py` заменить тело `dating_girl_login` (строки 216-322) на:

```python
    try:
        result = await dating_login_run(girl_id, int(data.scenario_id or 0),
                                        str(data.session_id or ''))
    except DatingLoginError as e:
        # «Войти нечем» — это ошибка запроса, а не сбой: нет пароля, нет сценария,
        # нет сайта. 422 отличает её от 502 «сосед не отвечает».
        raise HTTPException(status_code=422, detail=str(e))

    return {'result': result}
```

Импорт добавить к остальным `src.dating`:

```python
from src.dating.dating_login import DatingLoginError, dating_login_run
```

Перенесённые вспомогательные функции удалить из роутера, оставив `_girl_row`,
`_json_load` и `_site_check` (последний нужен ручкам `dating_girl_probe`,
`dating_girl_check`, `dating_girl_disconnect` и бросает `HTTPException`).

Прочие ручки, звавшие перенесённые функции, переключить на импорт из нового модуля
по таблице имён из Step 1:

```python
from src.dating.dating_login import (dating_login_profile_page, dating_login_session_all,
                                     dating_login_session_close, dating_login_session_open,
                                     dating_login_state_clear, dating_login_ws_capture)
```

Сигнатуры при переносе не меняются: `dating_login_ws_capture(girl, session_id='')`,
`dating_login_state_clear(girl_id)`, `dating_login_session_open(site, name)`,
`dating_login_session_close(session_id)`, `dating_login_session_all()`,
`dating_login_profile_page(girl, presence, session_id)`.

- [ ] **Step 3: Проверить, что панель поднялась и вход на месте**

```bash
docker compose up -d --force-recreate dating_uvicorn
sleep 8
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8021/health
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8021/api/dating/girl/1/login
```

Expected: `200`, затем `401` (ручка закрыта без сессии — значит роутер собрался и путь на месте).

Что `keep_online` действительно ставится, автотестом не проверить: для этого нужен
настоящий вход через браузер соседа на живой сайт. Проверяется руками — нажать
«Войти» у рабочей анкеты в панели и посмотреть базу:

```bash
docker exec dating_mysql mysql -udeveloper -ppassword --default-character-set=utf8mb4 dating \
  -e "SELECT id, login, keep_online, logged_in_at FROM dating_girl;"
```

Ожидается `keep_online = 1` и свежий `logged_in_at` у той анкеты, под которой вошли.

- [ ] **Step 4: Прогнать сюиту**

Run: `ADMIN_PASSWORD=<из .env> uv run --with pytest --with pytest-asyncio --with httpx pytest tests/ -q`
Expected: PASS, все прежние тесты зелёные (24 + 11 из Task 2).

- [ ] **Step 5: Commit**

```bash
git add src/dating/dating_login.py src/uvicorn/routers/dating/dating_girl.py
git commit -m "Вход вынесен из ручки в отдельный модуль"
```

---

### Task 4: Круг восстановления в смене держателя

**Files:**
- Modify: `src/dating/dating_revive.py` (добавить `dating_revive_round`)
- Modify: `src/dating/dating_chat_hold.py` (новый цикл `_revive_loop`, запуск в `dating_chat_hold_run`)
- Test: `tests/test_revive_round.py`

**Interfaces:**
- Consumes: `dating_revive_delay`, `dating_revive_state`, `dating_revive_due` (Task 2); `dating_login_run` (Task 3); `dating_presence_probe`, `dating_presence_ready` (есть); `rhapsody_api_site_get` (есть).
- Produces:
  - `dating_revive_plan(girl: dict, online: bool, now: datetime) -> dict` — решение по одной анкете: `action` (`none`/`clear`/`wait`/`login`/`give_up`) плюс новые `relogin_since`, `relogin_tries`, `relogin_next_at`
  - `dating_revive_round(login_run=None) -> dict` — круг по всем анкетам под присмотром; `login_run=None` означает настоящий `dating_login_run`, в тестах передают заглушку. Ключи ответа: `checked`, `online`, `tried`, `revived`, `given_up`

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_revive_round.py`:

```python
"""Круг восстановления: кого трогаем, кого нет, и что пишем в базу.

Настоящий вход сюда не заходит — он водит браузер соседа и стучится на живой сайт.
Поэтому вход подменяется заглушкой, а проверяется решение: пора входить или рано.
"""

from datetime import datetime, timedelta

from src.dating.dating_revive import dating_revive_plan

NOW = datetime(2026, 8, 18, 12, 0, 0)


def _girl(**kw) -> dict:
    row = {'id': 1, 'login': 'test', 'keep_online': 1, 'logged_in_at': NOW,
           'relogin_since': None, 'relogin_tries': 0, 'relogin_next_at': None}
    row.update(kw)

    return row


class TestRevivePlan:

    def test_online_and_fresh_needs_nothing(self):
        """Вход жив и свеж — трогать нечего."""
        assert dating_revive_plan(_girl(), online=True, now=NOW)['action'] == 'none'

    def test_online_but_old_is_refreshed(self):
        """Профилактика: входу больше двадцати часов — обновляем заранее."""
        girl = _girl(logged_in_at=NOW - timedelta(hours=21))
        assert dating_revive_plan(girl, online=True, now=NOW)['action'] == 'login'

    def test_offline_starts_chain(self):
        """Первый отказ начинает череду: попытка через минуту, а не сейчас."""
        plan = dating_revive_plan(_girl(), online=False, now=NOW)

        assert plan['action'] == 'wait'
        assert plan['relogin_since'] == NOW
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=1)

    def test_offline_waits_until_due(self):
        """Срок не настал — молчим, попытку не двигаем."""
        girl = _girl(relogin_since=NOW - timedelta(minutes=1),
                     relogin_next_at=NOW + timedelta(minutes=4), relogin_tries=1)

        assert dating_revive_plan(girl, online=False, now=NOW)['action'] == 'none'

    def test_offline_logs_in_when_due(self):
        """Срок настал — входим, и следующая попытка назначается по лестнице."""
        girl = _girl(relogin_since=NOW - timedelta(minutes=6),
                     relogin_next_at=NOW - timedelta(seconds=1), relogin_tries=1)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_tries'] == 2
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=15)

    def test_gives_up_after_a_day(self):
        """Сутки прошли — попытки прекращаются, срок следующей стирается."""
        girl = _girl(relogin_since=NOW - timedelta(hours=25),
                     relogin_next_at=NOW - timedelta(minutes=1), relogin_tries=9)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'give_up'
        assert plan['relogin_next_at'] is None

    def test_recovery_clears_chain(self):
        """Вход снова живой — череда обнуляется целиком."""
        girl = _girl(relogin_since=NOW - timedelta(hours=2), relogin_tries=4,
                     relogin_next_at=NOW + timedelta(minutes=90))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'clear'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 0
        assert plan['relogin_next_at'] is None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `uv run --with pytest --with pytest-asyncio --with httpx pytest tests/test_revive_round.py -q`
Expected: FAIL — `ImportError: cannot import name 'dating_revive_plan'`

- [ ] **Step 3: Написать решение круга**

Добавить в `src/dating/dating_revive.py`:

```python
def dating_revive_plan(girl: dict, online: bool, now: datetime) -> dict:
    """Что делать с анкетой по итогам опроса — одним решением, без побочных действий.

    Отдельно от самого круга, потому что здесь всё интересное: лестница, сдача,
    профилактика. Круг вокруг — это база и вход, их в тестах не прогонишь.

    Returns:
        dict: `action` — `none` (ничего), `clear` (череда кончилась), `wait`
        (череда началась, входить ещё рано), `login` (входить сейчас), `give_up`
        (сутки вышли); плюс новые значения `relogin_since`, `relogin_tries`,
        `relogin_next_at` для полей учётки.
    """
    since = girl.get('relogin_since')
    tries = int(girl.get('relogin_tries') or 0)

    if online:
        if since:
            # Вход вернулся сам (сайт полежал и встал) — череду закрываем.
            return {'action': 'clear', 'relogin_since': None, 'relogin_tries': 0,
                    'relogin_next_at': None}

        logged_in_at = girl.get('logged_in_at')
        old = not logged_in_at or (now - logged_in_at).total_seconds() >= DATING_REVIVE_AGE_SEC
        if old:
            # Профилактика. Череду не начинаем: вход жив, и если обновление не
            # выйдет, это заметит следующий круг — уже как настоящий отказ.
            return {'action': 'login', 'relogin_since': None, 'relogin_tries': 0,
                    'relogin_next_at': None}

        return {'action': 'none', 'relogin_since': None, 'relogin_tries': 0,
                'relogin_next_at': None}

    if not since:
        # Первый отказ. Входить сразу нельзя: сайт мог моргнуть, и минута ожидания
        # дешевле лишнего прогона браузера.
        return {'action': 'wait', 'relogin_since': now, 'relogin_tries': 0,
                'relogin_next_at': now + timedelta(seconds=dating_revive_delay(0))}

    if dating_revive_state(since, now) == 'given_up':
        return {'action': 'give_up', 'relogin_since': since, 'relogin_tries': tries,
                'relogin_next_at': None}

    if not dating_revive_due(girl.get('relogin_next_at'), now):
        return {'action': 'none', 'relogin_since': since, 'relogin_tries': tries,
                'relogin_next_at': girl.get('relogin_next_at')}

    return {'action': 'login', 'relogin_since': since, 'relogin_tries': tries + 1,
            'relogin_next_at': now + timedelta(seconds=dating_revive_delay(tries + 1))}
```

Импорт в шапке модуля дополнить: `from datetime import datetime, timedelta`.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `uv run --with pytest --with pytest-asyncio --with httpx pytest tests/test_revive_round.py -q`
Expected: PASS, 7 passed

- [ ] **Step 5: Написать сам круг**

Добавить в `src/dating/dating_revive.py`:

```python
async def dating_revive_round(login_run=None) -> dict:
    """Один круг: опросить анкеты под присмотром и починить те, что выпали.

    Вход передаётся параметром, а не импортируется намертво: так круг проверяется
    тестами без браузера, а модуль не тянет за собой половину проекта.

    Returns:
        dict: `checked`, `online`, `tried` (сколько раз входили), `revived`,
        `given_up`.
    """
    login_run = login_run or dating_login_run
    counts = {'checked': 0, 'online': 0, 'tried': 0, 'revived': 0, 'given_up': 0}
    now = datetime.now()

    for girl in await _girls_kept():
        counts['checked'] += 1
        presence = await _presence_of(int(girl['site_id']))
        if not presence:
            continue

        probe = await dating_presence_probe(girl, presence)
        online = probe['status'] == 'online'
        counts['online'] += int(online)
        await _probe_save(int(girl['id']), probe)

        plan = dating_revive_plan(girl, online, now)
        await _plan_save(int(girl['id']), plan)

        if plan['action'] == 'wait':
            await dating_log_write(int(girl['id']), 'fail',
                                   f"Вход не признан сайтом: {probe['error'] or probe['status']}")
            await _error_write(girl, probe['error'] or probe['status'])
        elif plan['action'] == 'give_up':
            await dating_log_write(int(girl['id']), 'fail',
                                   'Сутки не выходит войти — попытки прекращены, нужен вход вручную')
            await _error_write(girl, 'сутки не выходит войти, автовход остановлен')
        elif plan['action'] == 'login':
            counts['tried'] += 1
            if await _login_try(int(girl['id']), login_run, plan):
                counts['revived'] += 1

    return counts


async def _login_try(girl_id: int, login_run, plan: dict) -> bool:
    """Одна попытка входа. Успех сам обнуляет череду — это делает `dating_login_run`."""
    await dating_log_write(girl_id, 'info',
                           f"Вхожу заново (попытка {plan['relogin_tries']})"
                           if plan['relogin_since'] else 'Обновляю вход заранее')
    try:
        result = await login_run(girl_id)
    except Exception as e:
        await dating_log_write(girl_id, 'fail', f'Войти не вышло: {str(e)[:300]}')
        return False

    ok = str(result.get('status')) == 'success' and not result.get('error')
    await dating_log_write(girl_id, 'ok' if ok else 'fail',
                           'Вход восстановлен' if ok
                           else f"Войти не вышло: {str(result.get('error') or '')[:300]}")

    return ok
```

Плюс приватные помощники в том же файле:

```python
async def _girls_kept() -> list[dict]:
    """Анкеты под присмотром автомата — те, кого хотя бы раз ввели успешно."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, login, site_id, storage_state, keep_online, logged_in_at, '
            '       relogin_since, relogin_tries, relogin_next_at '
            '  FROM dating_girl WHERE keep_online = 1 ORDER BY id')
        rows = await db.fetchall()

    return [dict(row) for row in (rows or [])]


async def _presence_of(site_id: int) -> dict:
    """Настройка опроса сайта. Сосед не ответил — пустой словарь: круг повторится."""
    try:
        site = await rhapsody_api_site_get(int(site_id))
    except RhapsodyError as e:
        logger_info(f'[dating] сайт #{site_id} не спросился: {e}')
        return {}

    presence = _json_load(site.get('presence'))

    return presence if dating_presence_ready(presence) else {}


async def _probe_save(girl_id: int, probe: dict) -> None:
    """Итог опроса в карточку. Раньше это делала только открытая страница."""
    seen = 'NOW()' if probe['status'] == 'online' else 'last_seen_at'
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'UPDATE dating_girl SET status = %s, unread = %s, last_error = %s, '
                f'       last_seen_at = {seen} WHERE id = %s',
                (probe['status'], probe['unread'] if probe['unread'] >= 0 else 0,
                 probe['error'], int(girl_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] итог опроса не записался: {e}')


async def _plan_save(girl_id: int, plan: dict) -> None:
    """Новое состояние череды. Успешный вход перепишет его сам — и это правильно."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_girl SET relogin_since = %s, relogin_tries = %s, '
                             '       relogin_next_at = %s WHERE id = %s',
                             (plan['relogin_since'], int(plan['relogin_tries']),
                              plan['relogin_next_at'], int(girl_id)))
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] череда попыток не записалась: {e}')


async def _error_write(girl: dict, why: str) -> None:
    """Переход состояния — во вкладку «Ошибки» соседа. Каждая неудача туда не идёт:
    за сутки набежало бы два десятка одинаковых строк."""
    await rhapsody_api_error_write('dating', f"анкета «{girl['login']}» не входит: {why}",
                                   context={'girl_id': girl['id'], 'site_id': girl['site_id'],
                                            'where': 'автовосстановление входа'})
```

Импорты модуля:

```python
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.dating_log import dating_log_write
from src.dating.dating_login import dating_login_run
from src.dating.dating_presence import dating_presence_probe, dating_presence_ready
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_error_write, rhapsody_api_site_get
```

`_json_load` скопировать из `dating_chat_hold.py:298-305` (JSON-столбец приезжает то структурой, то строкой).

- [ ] **Step 6: Подключить цикл к смене**

В `src/dating/dating_chat_hold.py` добавить импорт:

```python
from src.dating.dating_revive import DATING_REVIVE_PERIOD_SEC, dating_revive_round
```

и цикл рядом с `_answer_loop`:

```python
async def _revive_loop(deadline: float) -> None:
    """Держит вход анкет живым, пока идёт смена.

    Вход на сайт живёт около суток, и его протухание раньше не замечал никто:
    полный опрос делала только открытая страница, а держатель шлёт голый heartbeat,
    который на мёртвых cookie молча возвращает False.

    Входы идут по одному: круг обходит анкеты последовательно, и каждый вход
    дожидается предыдущего. Отдельного семафора не нужно — каждый вход открывает
    вкладку Chromium у соседа, и десяток разом уронил бы пул, а последовательный
    обход этого просто не допускает. Смена в установке одна (single-flight), так
    что второго круга параллельно не бывает.
    """
    while time.monotonic() < deadline:
        await asyncio.sleep(min(DATING_REVIVE_PERIOD_SEC,
                                max(1.0, deadline - time.monotonic())))
        try:
            await dating_revive_round()
        except Exception as e:
            logger_info(f'[dating] круг восстановления: {e}')
```

Найти в `dating_chat_hold_run` место, где запускаются `_presence_loop` и
`_answer_loop`, и добавить третьей задачей `_revive_loop(deadline)` тем же способом
(создание задачи и отмена в `finally` — как у соседей).

- [ ] **Step 7: Проверить на живом стенде**

```bash
docker compose up -d --force-recreate dating_uvicorn dating_worker
sleep 15
docker logs --since 3m dating_worker 2>&1 | grep -i "восстановлен\|автовход\|revive" | head
```

Expected: круг проходит без исключений; при живом входе в логе тихо, в журнале анкеты новых строк нет.

- [ ] **Step 8: Проверить восстановление подделкой протухания**

```bash
docker exec dating_mysql mysql -udeveloper -ppassword --default-character-set=utf8mb4 dating \
  -e "UPDATE dating_girl SET storage_state = JSON_OBJECT('cookies', JSON_ARRAY()) WHERE keep_online = 1 LIMIT 1;"
sleep 90
docker exec dating_mysql mysql -udeveloper -ppassword --default-character-set=utf8mb4 dating \
  -e "SELECT id, status, relogin_since, relogin_tries, relogin_next_at FROM dating_girl WHERE keep_online = 1;"
```

Expected: `status = error`, `relogin_since` заполнено, `relogin_next_at` — через минуту от него.

- [ ] **Step 9: Commit**

```bash
git add src/dating/dating_revive.py src/dating/dating_chat_hold.py tests/test_revive_round.py
git commit -m "Смена сама восстанавливает вход анкеты"
```

---

### Task 5: Состояние наружу и снятие присмотра

**Files:**
- Modify: `src/uvicorn/routers/dating/dating_girl.py` (`dating_girl_list`, строки 123-153)
- Modify: `src/dating/dating_disconnect.py` (`_state_clear`, строка 157)
- Test: `tests/test_girl_crud.py`

**Interfaces:**
- Consumes: `dating_revive_state` (Task 2).
- Produces: в ответе `GET /api/dating/girl` — поля `revive_state` (`ok`/`failing`/`given_up`), `relogin_next_at` (ISO или `null`), `keep_online` (bool).

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_girl_crud.py` в класс `TestGirlCrud`:

```python
    async def test_new_girl_is_not_watched(self, auth_client, girl):
        """Только что заведённая анкета под присмотр не берётся.

        Присмотр ставит успешный вход: лезть в браузер под учёткой, которую ещё ни
        разу не проверял человек, система не должна.
        """
        response = await auth_client.get(f'/api/dating/girl?site_id={SITE_ID}')
        row = next(r for r in response.json()['result'] if r['login'] == girl['login'])

        assert row['keep_online'] is False
        assert row['revive_state'] == 'ok'
        assert row['relogin_next_at'] is None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `ADMIN_PASSWORD=<из .env> uv run --with pytest --with pytest-asyncio --with httpx pytest tests/test_girl_crud.py -q -k watched`
Expected: FAIL — `KeyError: 'keep_online'`

- [ ] **Step 3: Отдать поля наружу**

В `dating_girl_list` дополнить выборку (строки 133-136):

```python
            'SELECT g.id, g.site_id, g.login, g.note, g.status, g.unread, g.last_seen_at, '
            '       g.last_error, g.profile, g.auto_answer, g.auto_answer_only, '
            '       g.keep_online, g.relogin_since, g.relogin_next_at, '
```

и в цикле разбора строк (после `row['auto_answer'] = bool(...)`):

```python
        row['keep_online'] = bool(row.get('keep_online'))
        # Состояние считает сервер: страница иначе повторяла бы арифметику суток,
        # и «мигает или горит ровно» решалось бы в двух местах по-разному.
        row['revive_state'] = dating_revive_state(row.pop('relogin_since', None), datetime.now())
```

Импорт: `from src.dating.dating_revive import dating_revive_state`.

- [ ] **Step 4: Снимать присмотр при отключении**

В `src/dating/dating_disconnect.py`, функция `_state_clear` (строка 157), дополнить UPDATE:

```python
            await db.execute("UPDATE dating_girl SET storage_state = NULL, ws_url = '', "
                             "       status = %s, last_error = %s, keep_online = 0, "
                             "       relogin_since = NULL, relogin_tries = 0, "
                             "       relogin_next_at = NULL WHERE id = %s",
                             ('offline', '', girl_id))
```

Комментарий над строкой:

```python
            # Снимаем и присмотр автомата: «Отключить» — это «не веди её», а не
            # «выйди и войди снова». Иначе смена вернула бы анкету в сеть через минуту.
```

- [ ] **Step 5: Убедиться, что тесты проходят**

```bash
docker compose up -d --force-recreate dating_uvicorn
sleep 8
ADMIN_PASSWORD=<из .env> uv run --with pytest --with pytest-asyncio --with httpx pytest tests/ -q
```

Expected: PASS, все тесты зелёные.

- [ ] **Step 6: Commit**

```bash
git add src/uvicorn/routers/dating/dating_girl.py src/dating/dating_disconnect.py tests/test_girl_crud.py
git commit -m "Состояние автовхода в списке анкет; «Отключить» снимает присмотр"
```

---

### Task 6: Предупреждение на карточке

**Files:**
- Modify: `data/uvicorn/template/admin/dating.html` (стили — рядом с `.girl-badges`, строка 153; разметка карточки — строки 449-478)

**Interfaces:**
- Consumes: поля `revive_state`, `relogin_next_at` из `GET /api/dating/girl` (Task 5).
- Produces: ничего для кода — только вид страницы.

- [ ] **Step 1: Добавить стили**

Рядом с `.girl-badges` (строка 153):

```css
        /* Предупреждение о том, что анкета не входит. Мигает, пока автомат ещё
           пробует: это то, ради чего человек смотрит в панель. После сдачи горит
           ровно — вечно мигающее перестаёт читаться как тревога. */
        .girl-warn { font-size: 12px; line-height: 1; cursor: help; }
        .girl-warn.failing { animation: girl-warn-blink 1.2s steps(1, end) infinite; }
        .girl-warn.given_up { color: #b91c1c; }

        @keyframes girl-warn-blink {
            0%, 49% { opacity: 1; }
            50%, 100% { opacity: .15; }
        }
```

- [ ] **Step 2: Показать значок в карточке**

В `girlsRender`, сразу после точки статуса (строка 456), добавить:

```javascript
                + warnBadge(row)
```

И рядом с `statusTitle` — саму функцию:

```javascript
    // Значок «не входит». Текст подсказки собираем здесь, а состояние считает
    // сервер: арифметика суток в двух местах разошлась бы.
    function warnBadge(row) {
        if (!row.revive_state || row.revive_state === 'ok') return '';

        const when = row.relogin_next_at
            ? String(row.relogin_next_at).slice(11, 16) : '';
        const title = row.revive_state === 'given_up'
            ? `не удаётся войти: ${row.last_error || 'причина неизвестна'} — попытки прекращены, нужен вход вручную`
            : `не удаётся войти: ${row.last_error || 'причина неизвестна'}`
              + (when ? `; следующая попытка в ${when}` : '');

        return `<span class="girl-warn ${esc(row.revive_state)}" title="${esc(title)}">⚠</span>`;
    }
```

- [ ] **Step 3: Проверить глазами**

```bash
docker exec dating_mysql mysql -udeveloper -ppassword --default-character-set=utf8mb4 dating \
  -e "UPDATE dating_girl SET relogin_since = NOW() - INTERVAL 2 HOUR, relogin_next_at = NOW() + INTERVAL 15 MINUTE, last_error = 'сайт не признал вход' WHERE keep_online = 1 LIMIT 1;"
```

Открыть `http://localhost:8021/`: у анкеты мигает ⚠, подсказка показывает причину и время следующей попытки. Затем:

```bash
docker exec dating_mysql mysql -udeveloper -ppassword --default-character-set=utf8mb4 dating \
  -e "UPDATE dating_girl SET relogin_since = NOW() - INTERVAL 25 HOUR, relogin_next_at = NULL WHERE keep_online = 1 LIMIT 1;"
```

Значок перестаёт мигать и горит красным, подсказка — «попытки прекращены».

Вернуть как было:

```bash
docker exec dating_mysql mysql -udeveloper -ppassword --default-character-set=utf8mb4 dating \
  -e "UPDATE dating_girl SET relogin_since = NULL, relogin_tries = 0, relogin_next_at = NULL, last_error = '' WHERE keep_online = 1;"
```

- [ ] **Step 4: Commit**

```bash
git add data/uvicorn/template/admin/dating.html
git commit -m "Мигающее предупреждение «не удаётся войти» на карточке"
```

---

### Task 7: Документация

**Files:**
- Modify: `.claude/docs/dating.md` (раздел про присутствие и держателя; таблица таблиц)
- Modify: `.claude/docs/worker.md` (циклы смены)
- Modify: `.claude/docs/database.md` (колонки `dating_girl`)
- Modify: `.claude/docs/dashboard_frontend.md` (значок на карточке)
- Modify: `.claude/docs/testing.md` (что проверяется)
- Modify: `CLAUDE.md` (таблица ключевых файлов)

**Interfaces:**
- Consumes: всё поведение из Task 1-6.
- Produces: ничего.

- [ ] **Step 1: Описать механику в `dating.md`**

Добавить раздел после описания держателя сокетов:

```markdown
## 5.4 Автовосстановление входа

Вход на сайт живёт около суток. Раньше его протухание не замечал никто: полный
опрос делала только открытая страница, а держатель шлёт голый heartbeat, который на
мёртвых cookie молча возвращает `False`. Анкета уходила из сети, сообщения ложились
в «Missed chat» безвозвратно, и узнавал об этом человек — когда открывал панель.

Теперь смена держателя раз в минуту опрашивает анкеты с пометкой `keep_online` и
сама чинит то, что выпало (`src/dating/dating_revive.py`):

| Что увидели | Что делаем |
|---|---|
| вход жив, снят меньше 20 часов назад | ничего |
| вход жив, снят больше 20 часов назад | обновляем заранее, не дожидаясь протухания |
| вход не признан, череды нет | начинаем череду: первая попытка через минуту |
| вход не признан, срок попытки настал | входим браузером, назначаем следующую |
| череде больше суток | попытки прекращаем, ждём человека |

Лестница пауз: **1, 5, 15, 45, 90 минут, дальше каждые два часа**. Успешный вход
обнуляет череду.

`keep_online` ставит успешный вход, снимает «Отключить». Отдельным полем, а не
`storage_state IS NOT NULL`: неудачная проверка входа состояние стирает, и анкета
выпадала бы из-под присмотра ровно тогда, когда её надо чинить.

Входы идут по одному: каждый открывает вкладку Chromium у соседа. Смена в установке
одна, поэтому семафора в процессе достаточно.

Побочно чинится давний перекос: `status`, `unread` и `last_error` больше не зависят
от того, открыл ли кто-то страницу.
```

- [ ] **Step 2: Дополнить таблицу колонок в `database.md`**

Дописать в описание `dating_girl`: `keep_online`, `logged_in_at`, `relogin_since`,
`relogin_tries`, `relogin_next_at` — состояние автовосстановления входа.

- [ ] **Step 3: Дополнить `worker.md`**

В описание смены добавить третий цикл: `_revive_loop` — следит за входом анкет,
раз в минуту, подробности в `dating.md`, раздел 5.4.

- [ ] **Step 4: Дополнить `dashboard_frontend.md`**

```markdown
На карточке анкеты может мигать ⚠ — автомат не может войти под ней. Мигает, пока
попытки идут; после суток безуспешных попыток горит ровно красным («нужен вход
вручную»). Состояние (`revive_state`) считает сервер: арифметика суток в двух
местах разошлась бы.
```

- [ ] **Step 5: Дополнить `testing.md`**

В таблицу файлов добавить: `test_revive_schedule.py` — лестница пауз и рубеж суток;
`test_revive_round.py` — решение «входить или ждать». Оговорить, что настоящий вход
сюита не трогает: он водит браузер и живой сайт.

- [ ] **Step 6: Дополнить `CLAUDE.md`**

В таблицу «Key Files» добавить строки:

```markdown
| `src/dating/dating_login.py` | Вход под анкетой: сценарий, состояние, проверка |
| `src/dating/dating_revive.py` | Автовосстановление входа: лестница попыток |
```

- [ ] **Step 7: Commit**

```bash
git add .claude/docs CLAUDE.md
git commit -m "Документация автовосстановления входа"
```

---

## Выкладка

После Task 7:

1. Прогнать всю сюиту локально — ожидается 24 прежних + 11 (Task 2) + 7 (Task 4) + 1 (Task 5) = 43 теста.
2. Снять дамп прод-базы (`.claude/docs/migration.md`, раздел 3).
3. Прогнать `data/migration_manual/2026-08-18_dating_relogin.sql` на прод-базе **до** выкладки кода: новый код читает колонки, которых там нет.
4. Запушить в `master` — дальше выложит GitHub Actions.
5. Проверить: `curl -s https://92.5.17.195.sslip.io/dating/health`, затем через две минуты — `relogin_*` в базе и журнал анкет.
