# Выключатель «выполнять действия» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** У сайта появляется выключатель «выполнять действия» — разрешение панели исполнять накопленные намерения, готовое к инструменту, которого ещё нет.

**Architecture:** Столбец `site.chat_intention_do_on` рядом с тремя собратьями, имя в белом списке `SITE_FLAGS`, пара ручек по образцу `chat-answer`, галочка в полосе вкладки «Чата» с `data-for="intention"`. Домен получает одну дверь `chat_intention_do_allowed(site_id)` — её спросит будущий исполнитель.

**Tech Stack:** Python 3.11, FastAPI, MySQL 8 (миграции `yoyo`, накат на старте сервера), Jinja2, ванильный JS в шаблоне, pytest + pytest-asyncio, docker compose.

Спека: `docs/superpowers/specs/2026-10-13-chat-intention-do-design.md`.

## Global Constraints

- **Живой HTTP в тестах запрещён** (`.claude/docs/testing.md`, §1.2).
- **Анкета для теста — только фикстуры `girl` / `girl_make`** из `tests/conftest.py`.
- Сайт стенда: `TEST_STAND_SITE = 999_343` (`tests/conftest.py:241`).
- **У каждого файла тестов ровно один маркер**; маркеры в `pyproject.toml`, ключ `markers`.
- **Класс тестов обязан начинаться с `Test`** — иначе pytest его молча не соберёт.
- `asyncio_mode = "auto"` (`pyproject.toml:2`): `async def` тесты и фикстуры **без** декораторов.
- **Приватные функции — ниже всех публичных**, константы — вверху файла (`py_modules/docs/code_rules.md`, §2–3).
- Миграция — **новый файл** `migrations/YYYYMMDDHHMMSS_<что делаем>.sql`; применённые не правят.
- Сюита ходит **по HTTP в поднятую панель**; правка `.py` не видна без `docker restart dating_uvicorn`. Шаблоны подхватываются сразу.
- **Запрос к базе руками — только** `PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql '…'`.
- Прогон: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest …`.
- **Перед прогоном проверить, что чужого pytest нет:** `pgrep -af "bin/pytest|python -m pytest" | grep -v "until ! pgrep"`. В каталоге работает вторая сессия.
- **Правка вёрстки без снятого кадра не принимается** (`py_modules/docs/frontend_check_rules.md`). Кадр снимается **CLI-вызовом** `web_.web_drive`, не своим скриптом.
- Комментарии и докстринги — по-русски, в стиле соседних модулей.

---

## File Structure

| Файл | Ответственность |
|---|---|
| `migrations/20261013190000_site_chat_intention_do_on.sql` (создать) | столбец-выключатель |
| `src/dating/chat_intention.py` (править) | `chat_intention_do_allowed` — дверь для исполнителя |
| `src/uvicorn/routers/dating/site_switch.py` (править) | имя в `SITE_FLAGS`, модель, пара ручек |
| `data/uvicorn/template/admin/dating/profile/site/_base.html` (править) | галочка в полосе вкладки |
| `data/uvicorn/template/admin/dating/profile.html` (править) | загрузка, отрисовка, привязка |
| `tests/test_chat_intention.py` (править) | дверь домена, маркер `chat` |
| `tests/test_chat_intention_http.py` (править) | ручки, маркер `core` |
| `tests/test_chat_intention_tab.py` (править) | галочка в полосе, маркер `page` |
| `.claude/docs/api.md`, `.claude/docs/dating.md`, `.claude/docs/database.md`, `CLAUDE.md` (править) | документация + поправка про словарь |

⚠ Тесты домена идут в `tests/test_chat_intention.py` (маркер `chat`), а не в файл
про сайты: функция про намерения и живёт в их модуле. Тесты ручек — в
`test_chat_intention_http.py` (`core`), хотя сами ручки лежат в `site_switch.py`:
файл тестов называет **предмет**, а не модуль, и «выключатель намерений» ищут там
же, где остальные намерения.

---

### Task 1: Столбец-выключатель

**Files:**
- Create: `migrations/20261013190000_site_chat_intention_do_on.sql`

**Interfaces:**
- Consumes: таблица `site` со столбцом `chat_initiative_send_on` (он в `AFTER`).
- Produces: столбец `site.chat_intention_do_on TINYINT(1) NOT NULL DEFAULT 0`.

- [ ] **Step 1: Написать миграцию**

Создать `migrations/20261013190000_site_chat_intention_do_on.sql`:

```sql
-- Выключатель «выполнять действия»: `site.chat_intention_do_on` (2026-10-13)
--
-- ЗАЧЕМ. Намерения разговора копятся в `chat_intention` и видны на вкладке
-- «Чат → Намерения и действия». Исполнять их некому: занятия, которое берёт
-- открытое намерение и делает дело, не существует. Столбец — разрешение панели
-- действовать, заведённое заранее: инструмент воткнётся в готовое место.
--
-- ⚠⚠ УМОЛЧАНИЕ 0 — ЭТО ЧЕСТНОСТЬ, А НЕ ОСТОРОЖНОСТЬ. Поднятая галочка у работы,
-- которой нет, — ложь: человек решит, что обещания исполняются, и не поймёт,
-- почему никто ничего не получил. Тот же довод стоит в миграции
-- `20261006120000_profile_mail_photo.sql`, заведённой в точно такой же
-- ситуации «флаг есть, исполнителя нет».
--
-- ⚠ ПРО САЙТ, А НЕ ПРО АНКЕТУ. Решение «исполняем ли намерения» — про занятие,
-- а не про конкретную девушку. Разложенный по учёткам флаг означал бы сорок
-- включений, а забытая анкета молчала бы, и причину искали бы в поломке. Тем же
-- путём прошли автоответ, генерация ответов и отправка.
--
-- ⚠ ИНДЕКСА НЕТ. Отбирать по колонке некому — круга не существует. Индекс,
-- который никто не читает, платится на каждой записи. Заведёт его та работа,
-- которая напишет исполнителя.
--
-- ⚠ МЕСТО — ЗА `chat_initiative_send_on`: там стоят три собрата того же вида
-- (`chat_answer_on`, `chat_initiative_on`, `chat_initiative_send_on`), и
-- выключателю чата место среди выключателей чата.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'site'
           AND COLUMN_NAME = 'chat_intention_do_on') AS столбец_есть,
       (SELECT COUNT(*) FROM `site`) AS сайтов;

-- ------------------------------------------- Разрешение исполнять
SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'site'
               AND COLUMN_NAME = 'chat_intention_do_on');

SET @sql = IF(@add,
    "ALTER TABLE `site`
        ADD COLUMN `chat_intention_do_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT 'Исполнять ли намерения разговора'
            AFTER `chat_initiative_send_on`",
    'SELECT ''столбец уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- ---------------------------------------------------------------- После
--
-- ⚠ Поднятых нулей — и это правильно: исполнителя нет, разрешать нечего.
-- Поднимет галочку человек, когда занятие появится.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'site'
           AND COLUMN_NAME = 'chat_intention_do_on') AS столбец_есть,
       (SELECT COUNT(*) FROM `site` WHERE `chat_intention_do_on` = 1) AS поднятых;
```

- [ ] **Step 2: Накатить перезапуском панели**

Run: `docker restart dating_uvicorn && sleep 15 && docker logs --tail 25 dating_uvicorn`

Expected: в логе видно применение `20261013190000_site_chat_intention_do_on`, приложение поднялось без traceback.

- [ ] **Step 3: Проверить столбец**

Run:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql \
  "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
     FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'site'
      AND COLUMN_NAME LIKE 'chat%' ORDER BY ORDINAL_POSITION"
```

Expected: четыре строки, последняя — `chat_intention_do_on | tinyint(1) | NO | 0`. Порядок подтверждает, что `AFTER` сработал.

- [ ] **Step 4: Коммит**

```bash
git add migrations/20261013190000_site_chat_intention_do_on.sql
git commit -m "Выключатель «выполнять действия»: столбец сайта"
```

---

### Task 2: `chat_intention_do_allowed` — дверь для исполнителя

**Files:**
- Modify: `src/dating/chat_intention.py`
- Test: `tests/test_chat_intention.py`

**Interfaces:**
- Consumes: столбец из Task 1.
- Produces: `async def chat_intention_do_allowed(site_id: int) -> bool`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/test_chat_intention.py` (импорт в шапке расширить именем `chat_intention_do_allowed`):

```python
async def _do_flag(site_id: int, on: bool) -> None:
    """Поднять или снять выключатель сайта напрямую — минуя ручку."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO site (site_id, chat_intention_do_on) VALUES (%s, %s)'
            ' ON DUPLICATE KEY UPDATE chat_intention_do_on = VALUES(chat_intention_do_on)',
            (int(site_id), 1 if on else 0))
        await db.connection.commit()


class TestTheDoFlag:
    """Разрешение исполнять намерения — дверь для будущего исполнителя."""

    async def test_it_is_off_by_default(self):
        """⚠ Умолчание снято: поднятая галочка у работы, которой нет, — ложь."""
        await _do_flag(TEST_STAND_SITE, False)

        assert await chat_intention_do_allowed(TEST_STAND_SITE) is False

    async def test_it_is_on_when_raised(self):
        await _do_flag(TEST_STAND_SITE, True)
        try:
            assert await chat_intention_do_allowed(TEST_STAND_SITE) is True
        finally:
            await _do_flag(TEST_STAND_SITE, False)

    async def test_a_site_without_a_row_is_not_allowed(self):
        """Строки сайта может не быть вовсе — это обычное состояние, не поломка."""
        assert await chat_intention_do_allowed(999_111) is False

    async def test_a_zero_site_is_not_allowed(self):
        assert await chat_intention_do_allowed(0) is False

    async def test_a_broken_read_is_not_allowed(self, monkeypatch):
        """⚠⚠ Сбой чтения даёт `False`, а не исключение и не `True`.

        Исполнение уходит живому человеку, и «на всякий случай сделал» отменить
        нельзя. Тем же правилом живёт `chat_answer_on` в `auto.py`.
        """
        def _boom(*args, **kwargs):
            raise RuntimeError('база легла')

        monkeypatch.setattr(intention_module, 'mysql_get_db_async', _boom)

        assert await chat_intention_do_allowed(TEST_STAND_SITE) is False
```

И в шапке файла добавить импорт модуля целиком (для подмены):

```python
from src.dating import chat_intention as intention_module
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention.py -k TestTheDoFlag -q -p no:randomly --no-header`

Expected: collection error — `ImportError: cannot import name 'chat_intention_do_allowed'`.

- [ ] **Step 3: Написать функцию**

Добавить в `src/dating/chat_intention.py` **после** `chat_intention_pairs` и **до** строки `# ── детали реализации ──` (правило: публичные выше приватных):

```python
async def chat_intention_do_allowed(site_id: int) -> bool:
    """Разрешено ли панели исполнять намерения этого сайта.

    Args:
        site_id: сайт-закладка.

    Returns:
        bool: `True` — человек поднял галочку «выполнять действия»; `False` —
        снята, строки сайта нет, или прочитать не вышло.

    ## ⚠⚠ Спрашивают это ПЕРЕД работой, а не вместо работы

    Галочка не трогает набор пула: намерения пишет прогон агента, и запрет
    исполнять не означает запрет замечать. Снятая галочка откладывает работу, а
    не стирает историю обещаний.

    ## ⚠⚠ Сбой чтения — это `False`

    Исполнение уходит живому человеку, и «на всякий случай сделал» отменить
    нельзя. Не смогли узнать — не делаем. Тем же правилом живёт `chat_answer_on`
    в `auto.py`; у настроек-сроков правило обратное (умолчание), и путать их
    дорого: там цена ошибки — лишняя пауза, здесь — необратимое действие.

    ⚠ Исполнителя пока нет вовсе: функция заведена заранее, вместе с галочкой.
    """
    if int(site_id or 0) <= 0:
        return False

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT chat_intention_do_on FROM site WHERE site_id = %s',
                (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] разрешение исполнять намерения сайта #{int(site_id)} '
                    f'не прочиталось: {e}')

        return False

    return bool((row or {}).get('chat_intention_do_on'))
```

- [ ] **Step 4: Прогнать**

Run: `docker restart dating_uvicorn && sleep 12 && ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention.py -q -p no:randomly --no-header`

Expected: PASS, 50 тестов (45 прежних + 5 новых).

- [ ] **Step 5: Коммит**

```bash
git add src/dating/chat_intention.py tests/test_chat_intention.py
git commit -m "Намерения: дверь «разрешено ли исполнять»"
```

---

### Task 3: Пара ручек

**Files:**
- Modify: `src/uvicorn/routers/dating/site_switch.py` (`SITE_FLAGS` — строка 48; модель рядом с `DatingSiteChatAnswerOn`, строка 182; ручки после `chat-answer-on`, строка 491)
- Test: `tests/test_chat_intention_http.py`

**Interfaces:**
- Consumes: столбец (Task 1), `site_flag_put(site_id, column, value)` из того же файла.
- Produces: `GET /api/dating/site/{id}/chat-intention-do` → `{'result': {'site_id': int, 'chat_intention_do_on': bool}}`; `PUT /api/dating/site/{id}/chat-intention-do-on` с телом `{'chat_intention_do_on': bool}`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/test_chat_intention_http.py`:

```python
DO_GET = f'/api/dating/site/{TEST_STAND_SITE}/chat-intention-do'
DO_PUT = f'/api/dating/site/{TEST_STAND_SITE}/chat-intention-do-on'


class TestTheDoHandles:
    """Выключатель «выполнять действия»: прочитать и переключить."""

    async def test_it_reads_the_flag(self, auth_client):
        await auth_client.put(DO_PUT, json={'chat_intention_do_on': False})

        got = await auth_client.get(DO_GET)

        assert got.status_code == 200
        assert got.json()['result'] == {'site_id': TEST_STAND_SITE,
                                        'chat_intention_do_on': False}

    async def test_it_raises_and_lowers(self, auth_client):
        try:
            put = await auth_client.put(DO_PUT, json={'chat_intention_do_on': True})

            assert put.status_code == 200
            assert (await auth_client.get(DO_GET)).json()['result'][
                'chat_intention_do_on'] is True
        finally:
            await auth_client.put(DO_PUT, json={'chat_intention_do_on': False})

        assert (await auth_client.get(DO_GET)).json()['result'][
            'chat_intention_do_on'] is False

    async def test_a_site_without_a_row_is_off_not_404(self, auth_client):
        """Нет строки сайта — выключено, а не «такого сайта нет»."""
        got = await auth_client.get('/api/dating/site/999111/chat-intention-do')

        assert got.status_code == 200
        assert got.json()['result']['chat_intention_do_on'] is False

    async def test_the_column_is_in_the_white_list(self):
        """⚠⚠ Имя столбца уезжает в SQL текстом, и `site_flag_put` проверяет его
        утверждением. Забудь мы имя в списке — ручка падала бы 500-й, а тест на
        ответ 200 этого бы не поймал: он падал бы по другой причине."""
        from src.uvicorn.routers.dating.site_switch import SITE_FLAGS

        assert 'chat_intention_do_on' in SITE_FLAGS

    async def test_a_stranger_is_turned_away(self, client):
        got = await client.get(DO_GET)

        assert got.status_code in (401, 403)
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention_http.py -k TestTheDoHandles -q -p no:randomly --no-header`

Expected: FAIL — ответы `404` (маршрута нет) и `assert 'chat_intention_do_on' in SITE_FLAGS`.

- [ ] **Step 3: Имя в белый список**

В `src/uvicorn/routers/dating/site_switch.py` заменить строку 54:

```python
    'chat_answer_on', 'chat_initiative_on', 'chat_initiative_send_on',
```

на:

```python
    'chat_answer_on', 'chat_initiative_on', 'chat_initiative_send_on',
    'chat_intention_do_on',
```

- [ ] **Step 4: Модель запроса**

После класса `DatingSiteChatAnswerOn` (строка 186) добавить:

```python
class DatingSiteChatIntentionDoOn(BaseModel):
    """Исполнять ли намерения разговора за анкеты сайта."""

    chat_intention_do_on: bool = Field(..., description='Разрешение исполнять '
                                                        'намерения: действие уходит '
                                                        'живому человеку')
```

- [ ] **Step 5: Пара ручек**

После `site_chat_answer_on` (строка 491, сразу за `return await site_flag_put(site_id, 'chat_answer_on', payload.chat_answer_on)`) добавить:

```python
@router.get('/{site_id}/chat-intention-do', include_in_schema=False)
async def site_chat_intention_do_get(site_id: int,
                                     user: AuthUser = Depends(auth_required)):
    """Исполняются ли намерения этого сайта. Нет строки — выключено, а не 404."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT chat_intention_do_on FROM site WHERE site_id = %s',
                         (int(site_id),))
        row = await db.fetchone()

    return {'result': {'site_id': int(site_id),
                       'chat_intention_do_on':
                           bool((row or {}).get('chat_intention_do_on'))}}


@router.put('/{site_id}/chat-intention-do-on', include_in_schema=False)
async def site_chat_intention_do_on(site_id: int,
                                    payload: DatingSiteChatIntentionDoOn,
                                    user: AuthUser = Depends(auth_required)):
    """Разрешает и запрещает исполнять намерения разговора.

    ⚠⚠ **Занятия, которое их исполняет, пока не существует.** Галочка заведена
    заранее — вместе со столбцом, — чтобы инструмент воткнулся в готовое место.
    Поднятая сейчас, она ничего не запускает, и подсказка у неё об этом говорит.

    ⚠ **Набор пула галочка не трогает.** Намерения пишет прогон агента, и запрет
    исполнять не означает запрет замечать: снятая галочка откладывает работу, а
    не стирает историю обещаний.

    ⚠ **Умолчание — снято.** Действие по намерению уходит живому человеку (сейчас
    это обещанная фотография), и накат, разрешивший такое сам, был бы худшим из
    сюрпризов.
    """
    return await site_flag_put(site_id, 'chat_intention_do_on',
                               payload.chat_intention_do_on)
```

- [ ] **Step 6: Прогнать**

Run: `docker restart dating_uvicorn && sleep 15 && ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention_http.py -q -p no:randomly --no-header`

Expected: PASS, 11 тестов (6 прежних + 5 новых).

- [ ] **Step 7: Коммит**

```bash
git add src/uvicorn/routers/dating/site_switch.py tests/test_chat_intention_http.py
git commit -m "Выключатель «выполнять действия»: пара ручек"
```

---

### Task 4: Галочка в полосе вкладки

**Files:**
- Modify: `data/uvicorn/template/admin/dating/profile/site/_base.html` (блок настроек полосы «Чата», после `data-for="talk"`)
- Modify: `data/uvicorn/template/admin/dating/profile.html` (привязка рядом со строкой 5104; код рядом с `chatAnswerLoad`, строка 5747)
- Test: `tests/test_chat_intention_tab.py`

**Interfaces:**
- Consumes: ручки из Task 3.
- Produces: галочка `data-slot="chat-intention-do-on"` в блоке `data-for="intention"`; функции `chatIntentionDoLoad()`, `chatIntentionDoPaint()`, `chatIntentionDoSet(box)`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/test_chat_intention_tab.py`:

```python
class TestTheDoCheckbox:
    """Галочка «выполнять действия» в полосе «Чата»."""

    async def test_it_is_in_the_strip(self, work):
        assert 'data-slot="chat-intention-do-on"' in work, 'галочки нет'
        assert 'выполнять действия' in work, 'подписи нет'

    async def test_it_shows_only_on_its_own_subtab(self, work):
        """⚠ `data-for="intention"` — настройки видны на своей подвкладке, как у
        соседей: на «Инвайтах» эта галочка не нужна и путала бы."""
        start = work.find('data-slot="chat-intention-do-on"')
        box = work.rfind('data-for="intention"', 0, start)
        other = work.rfind('data-for="talk"', 0, start)

        assert box != -1, 'галочка стоит вне блока data-for="intention"'
        assert box > other, 'галочка попала в блок соседней подвкладки'

    async def test_the_hint_says_nobody_executes_yet(self, work):
        """⚠⚠ Подсказка здесь важнее обычного: у трёх соседних галочек снятие
        что-то останавливает, а здесь поднятие пока не запускает ничего. Не
        сказать об этом — повторить ту ложь, от которой защищает умолчание 0."""
        start = work.find('data-slot="chat-intention-do-on"')
        head = work.rfind('<label', 0, start)
        label = work[head:start]

        assert 'не существует' in label, 'подсказка не говорит про отсутствие занятия'

    async def test_the_page_wires_it(self, auth_client):
        page = (await auth_client.get(PAGE)).text

        assert "slot('chat-intention-do-on')" in page, 'страница её не рисует'
        assert 'chatIntentionDoSet' in page, 'переключение не привязано'
        assert "path: 'chat-intention-do-on'" in page, 'адрес ручки не тот'
```

- [ ] **Step 2: Прогнать и убедиться, что тесты падают**

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention_tab.py -k TestTheDoCheckbox -q -p no:randomly --no-header`

Expected: FAIL, все 5 тестов.

- [ ] **Step 3: Галочка в разметке**

В `data/uvicorn/template/admin/dating/profile/site/_base.html` найти закрытие блока `<span class="mail-tabs-set" data-for="talk">` в полосе «Чата» (он кончается перед `</div>` полосы) и **после** него добавить:

```html
        {# ⚠⚠ **Своя подвкладка, свой выключатель.** Галочка стоит в блоке
           `data-for="intention"` и видна только на «Намерениях», как настройки
           у соседей: на «Инвайтах» она не нужна и путала бы.

           ⚠⚠ **Подсказка здесь важнее обычного.** У трёх соседних галочек
           снятие что-то останавливает, а здесь поднятие пока не запускает
           ничего: занятия, которое исполняет намерения, не существует вовсе.
           Не сказать об этом — повторить ровно ту ложь, от которой защищает
           снятое умолчание столбца. #}
        <span class="mail-tabs-set" data-for="intention">
            <label class="mail-send-on" title="Пока снята, намерения копятся в
                   пуле и никто их не исполняет. ⚠ Занятия, которое исполняет
                   намерения, сейчас не существует вовсе — галочка готова к нему
                   заранее">
                <input type="checkbox" data-slot="chat-intention-do-on">
                выполнять действия</label>
        </span>
```

- [ ] **Step 4: Код страницы**

В `data/uvicorn/template/admin/dating/profile.html` после функции `chatAnswerSet` (строка 5861, перед комментарием `// ── Доставка инвайтов`) добавить:

```javascript
    // ── Намерения: разрешено ли их исполнять ────────────────────────────────
    //
    // ⚠⚠ Занятия, которое исполняет намерения, пока нет вовсе: галочка заведена
    // заранее, чтобы инструмент воткнулся в готовое место. Об этом говорит её
    // подсказка — иначе поднятая галочка обещала бы работу, которой не будет.
    //
    // ⚠ По сайтам, как прочие такие флаги: переключение сайта не должно
    // показывать чужое значение, пока едет ответ.
    const chatIntentionDoOn = new Map();

    async function chatIntentionDoLoad() {
        const site = siteId();
        if (!site || chatIntentionDoOn.has(site)) {
            chatIntentionDoPaint();

            return;
        }

        let on = false;
        try {
            on = !!(await getData(`${BASE}/api/dating/site/${site}/chat-intention-do`))
                .result.chat_intention_do_on;
        } catch (e) {
            // Молча: выключатель — не то, ради чего открыли вкладку, и всплывающее
            // сообщение на каждый заход было бы шумом. Снятая галочка здесь —
            // верная сторона ошибки: действие уходит живому человеку.
            on = false;
        }

        chatIntentionDoOn.set(site, on);
        chatIntentionDoPaint();
    }

    function chatIntentionDoPaint() {
        const box = slot('chat-intention-do-on');
        if (!box) return;

        box.checked = !!chatIntentionDoOn.get(siteId());
        // ⚠ Только админу: ручка отвечает ему 403, и галочка, возвращающаяся на
        // место, хуже запертой.
        box.disabled = !CAN_EDIT_SITE;
    }

    async function chatIntentionDoSet(box) {
        await siteFlagToggle(box, {
            path: 'chat-intention-do-on', flag: 'chat_intention_do_on',
            words: ['Намерения будут исполняться, когда занятие появится',
                    'Намерения не исполняются — пул копится'],
            save: (on, site) => chatIntentionDoOn.set(site, on),
        });
    }
```

⚠ Слова уведомления названы честно: поднятая галочка **не** означает «сейчас
начнётся», и сообщение «Намерения будут исполняться» без оговорки врало бы ровно
как и подсказка без неё.

- [ ] **Step 5: Привязка и загрузка**

В том же файле после привязки `chatAnswer` (строка 5107, за `}`) добавить:

```javascript
        const chatIntentionDo = workEl.querySelector(
            '[data-slot="chat-intention-do-on"]');
        if (chatIntentionDo && CAN_EDIT_SITE) {
            chatIntentionDo.addEventListener('change',
                                             () => chatIntentionDoSet(chatIntentionDo));
        }
```

И в `chatSubShow` заменить строку:

```javascript
        if (chatTab === 'intention') intentionShow();
```

на:

```javascript
        if (chatTab === 'intention') {
            intentionShow();
            // ⚠ Состояние галочки грузим здесь же: она видна только на этой
            // подвкладке, и спрашивать её при каждом заходе в чат значило бы
            // платить лишним запросом за то, чего человек не смотрит.
            chatIntentionDoLoad();
        }
```

- [ ] **Step 6: Прогнать тесты**

Шаблоны подхватываются без рестарта.

Run: `ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/test_chat_intention_tab.py -q -p no:randomly --no-header`

Expected: PASS, 13 тестов (8 прежних + 5 новых).

- [ ] **Step 7: Проверить синтаксис JS**

Сюита смотрит на HTML текстом и синтаксической ошибки не видит: страница сломается целиком, а тесты останутся зелёными.

```bash
SCRATCH=$(ls -d /tmp/claude-*/*/*/scratchpad 2>/dev/null | head -1)
ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) \
PYTHONPATH=py_modules:. .venv/bin/python - "$SCRATCH" <<'PY'
import asyncio, os, re, sys, pathlib
from tests_.tests_panel import tests_panel_as

async def main():
    user = os.getenv('ADMIN_USERNAME', 'admin')
    async with await tests_panel_as(user, os.environ['ADMIN_PASSWORD'],
                                    'http://localhost:8021') as http:
        return (await http.get('/admin/dating/profile')).text

page = asyncio.run(main())
big = max(re.findall(r'<script[^>]*>(.*?)</script>', page, re.S), key=len)
pathlib.Path(sys.argv[1], 'rendered.mjs').write_text(big, encoding='utf-8')
print('строк в скрипте:', big.count('\n'))
PY
node --input-type=module --check < "$SCRATCH/rendered.mjs" && echo "СИНТАКСИС ЧИСТ"
```

Expected: «СИНТАКСИС ЧИСТ».

- [ ] **Step 8: Снять кадр и посмотреть в него**

⚠ Правка вёрстки без кадра не принимается. Кадр снимается **CLI-вызовом**.

Положить в `$SCRATCH/probe_do.js`:

```javascript
const wait = async (fn, ms = 15000) => { const t0 = Date.now();
    while (Date.now() - t0 < ms) { const v = fn && fn(); if (v) return v;
        await new Promise(z => setTimeout(z, 150)); } return null; };
const l = await fetch('/auth/login', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: 'ЛОГИН', password: 'ПАРОЛЬ'}),
    credentials: 'same-origin'});
if (!l.ok) return {err: 'вход', status: l.status};
localStorage.setItem('dating_site', '343');
localStorage.setItem('dating.profile.tab', 'chat');
localStorage.setItem('dating.profile.chattab', 'intention');
document.body.style.margin = '0';
const f = document.createElement('iframe');
f.style.cssText = 'width:100vw;height:100vh;border:0;display:block';
f.src = '/admin/dating/profile';
document.body.innerHTML = ''; document.body.appendChild(f);
await new Promise(r => f.addEventListener('load', r, {once: true}));
const d = f.contentDocument;
await wait(() => d.querySelector('[data-chat-pane="intention"]:not(.off)'));
await new Promise(r => setTimeout(r, 1500));
const box = d.querySelector('[data-slot="chat-intention-do-on"]');
const label = box && box.closest('label');
const r = (el) => { const b = el.getBoundingClientRect();
    return {x: Math.round(b.x), y: Math.round(b.y),
            w: Math.round(b.width), h: Math.round(b.height)}; };
const strip = d.querySelector('[data-slot="chat-tabs"]');
return {exists: !!box, visible: label ? r(label).w > 0 : false,
        label: label ? label.textContent.replace(/\s+/g, ' ').trim() : null,
        rect: label ? r(label) : null, stripRect: r(strip),
        checked: box ? box.checked : null,
        insideStrip: label ? (r(label).y >= r(strip).y
                              && r(label).y + r(label).h <= r(strip).y + r(strip).h) : false};
```

⚠ `ЛОГИН`/`ПАРОЛЬ` в плане не записаны намеренно — учётным данным в репозитории
не место. Подставить их из `.env` одной командой, не правя файл руками:

```bash
sed -i "s/'ЛОГИН'/'$(grep -m1 '^ADMIN_USERNAME=' .env | cut -d= -f2-)'/; \
        s/'ПАРОЛЬ'/'$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-)'/" \
    "$SCRATCH/probe_do.js"
```

⚠ Файл лежит в `$SCRATCH` (каталог сессии вне репозитория), и в git не попадает.

Run:

```bash
pkill -f "user-data-dir=/tmp/web_drive_" 2>/dev/null; sleep 2
PYTHONPATH=py_modules .venv/bin/python -m web_.web_drive \
  http://localhost:8021/auth/me --js-file "$SCRATCH/probe_do.js" \
  --shot "$SCRATCH/do_flag.png" --size 1700,1150 --wait 40000 --json
```

Expected: `exists: true`, `visible: true`, `checked: false`, `insideStrip: true`, подпись содержит «выполнять действия». Затем **открыть PNG и посмотреть глазами**: галочка стоит в полосе справа, не разрывает её на две строки, подпись не обрезана.

⚠ Если `insideStrip: false` — галочка вывалилась из полосы: проверить, что блок `<span class="mail-tabs-set">` закрыт и стоит внутри `data-slot="chat-tabs"`.

- [ ] **Step 9: Коммит**

```bash
git add data/uvicorn/template/admin/dating/profile/site/_base.html \
        data/uvicorn/template/admin/dating/profile.html \
        tests/test_chat_intention_tab.py
git commit -m "Выключатель «выполнять действия»: галочка в полосе вкладки"
```

---

### Task 5: Документация и поправка про словарь

**Files:**
- Modify: `.claude/docs/api.md` (§3.15), `.claude/docs/dating.md` (§5.13), `.claude/docs/database.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: всё из задач 1–4.
- Produces: ничего в коде.

- [ ] **Step 1: Ручки в `.claude/docs/api.md`**

В §3.15, после таблицы ручек namespace `chat-intention`, добавить:

```markdown
**Выключатель «выполнять действия»** — у сайта, не у анкеты:

| Метод | Путь | Что делает |
|-------|------|------------|
| GET | `/api/dating/site/{id}/chat-intention-do` | исполняются ли намерения сайта; нет строки — выключено, а не 404 |
| PUT | `/api/dating/site/{id}/chat-intention-do-on` | переключает (`site.chat_intention_do_on`) |

⚠⚠ **Занятия, которое исполняет намерения, пока не существует.** Галочка и
столбец заведены заранее, чтобы инструмент воткнулся в готовое место; поднятая
сейчас, она ничего не запускает, и её подсказка об этом говорит. Умолчание —
снято: поднятая галочка у работы, которой нет, читается как «обещания
исполняются», и человек не поймёт, почему никто ничего не получил.

⚠ **Набор пула галочка не трогает.** Намерения пишет прогон агента, и запрет
исполнять не означает запрет замечать: снятая галочка откладывает работу, а не
стирает историю обещаний.

⚠ Код спрашивает разрешение **одной дверью** `chat_intention_do_allowed(site_id)`
(`src/dating/chat_intention.py`), и сбой чтения она отдаёт как `False`: действие
уходит живому человеку, и «на всякий случай сделал» отменить нельзя.
```

- [ ] **Step 2: Абзац в `.claude/docs/dating.md`, §5.13**

Заменить в разделе «Где это видно» строку

```markdown
⚠ Средняя колонка пуста, пока нет исполнителя, и говорит об этом словами.
```

на:

```markdown
⚠ Средняя колонка пуста, пока нет исполнителя, и говорит об этом словами.

⚠ В правом краю полосы — галочка **«выполнять действия»** (`site.chat_intention_do_on`,
`api.md` §3.15). Она разрешает панели исполнять накопленное; набор пула не
трогает. Занятия-исполнителя пока нет вовсе, и подсказка галочки об этом
говорит: поднятая, она сегодня ничего не запускает. Код спрашивает её
`chat_intention_do_allowed(site_id)`.
```

- [ ] **Step 3: Поправка про словарь — там же**

Заменить абзац:

```markdown
⚠ **Словаря меток нет ни на одной стороне.** Какие метки бывают, задаёт workflow
у соседа, а правки workflow живут только в его прод-базе. Метка хранится как
приехала.
```

на:

```markdown
⚠⚠ **Словарь меток есть, и он у соседа — темами.** Это группа `namereniya`
(`agent_workflow_topic_group`): каждая тема описывает одно намерение, её поле
`instructions` и есть метка, а вопросы темы — то, что спрашивает судья. Сегодня
в группе одна тема `photo_send` «Отправка фото»: метка `she_promised_send_photo`,
вопрос «Has she agreed or promised to send a photo?», приметы `photo, pic,
selfie, send, show…`, порог — 2 приметы и 1 «да». Кладёт её шаг подпотока
`dating_talk_intention` инструментом `chat_intention_add`.

⚠ **Своего списка меток у панели нет и заводить не надо.** Темы правятся у соседа
и живут только в его прод-базе; вторая копия разошлась бы молча. Метка хранится
как приехала, а предел её длины (100 знаков) — про столбец, а не про словарь.
```

- [ ] **Step 4: Строка в `.claude/docs/database.md`**

Отдельного перечня столбцов `site` в файле нет (в §2 таблица описана одной
строкой — проверено), поэтому столбец идёт в **§4 «Поля, смысл которых
неочевиден»** — там же, где живут `profile_.storage_state` и `profile_.ws_url`.
Добавить в конец §4:

```markdown
**`site.chat_intention_do_on`** — разрешение исполнять намерения разговора.
⚠ Занятия-исполнителя пока нет: столбец заведён заранее, умолчание снято. Набора
пула не касается — намерения пишет прогон агента независимо от него.
```

- [ ] **Step 5: Строка в `CLAUDE.md`**

В строке «Development Patterns» про `chat_intention.py` в конец скобки добавить:

```
. ⚠ Разрешение исполнять — галочка «выполнять действия» у сайта (`chat_intention_do_on`), код спрашивает её `chat_intention_do_allowed`; набор пула она не трогает, и занятия-исполнителя пока нет вовсе
```

- [ ] **Step 6: Коммит**

```bash
git add .claude/docs/api.md .claude/docs/dating.md .claude/docs/database.md CLAUDE.md
git commit -m "Документация: выключатель «выполнять действия» и словарь намерений"
```

---

### Task 6: Полный прогон

**Files:** ничего не правится.

- [ ] **Step 1: Убедиться, что чужого прогона нет**

Run: `pgrep -af "bin/pytest|python -m pytest" | grep -v "until ! pgrep"`

Expected: пусто. Не пусто — подождать: параллельные прогоны роняют оба.

- [ ] **Step 2: Перезапустить панель и прогнать сюиту**

Run: `docker restart dating_uvicorn && sleep 15 && ADMIN_PASSWORD=$(grep -m1 '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/ -q -p no:randomly --no-header`

Expected: PASS, около девяти минут. Число тестов вырастет на 15 (5 в `test_chat_intention.py`, 5 в `test_chat_intention_http.py`, 5 в `test_chat_intention_tab.py`).

- [ ] **Step 3: Убедиться, что сюита не оставила поднятого флага**

Run:

```bash
PYTHONPATH=py_modules .venv/bin/python -m mysql_.mysql_query sql \
  "SELECT site_id, chat_intention_do_on FROM site WHERE chat_intention_do_on = 1"
```

Expected: 0 строк. Тесты поднимают флаг и опускают его в `finally`; поднятый после прогона означает оборванный тест, и на живом сайте это было бы разрешением действовать, которого никто не давал.

---

## Self-Review

**Покрытие спеки.** Столбец — Task 1. `chat_intention_do_allowed` с правилом «сбой
= False» — Task 2. Белый список, модель, пара ручек — Task 3. Галочка, подсказка,
загрузка по подвкладке — Task 4. Четыре документа и поправка про словарь — Task 5.
Полный прогон и проверка, что флаг не остался поднятым, — Task 6.

Пункты спеки «круга нет», «реестра нет», «доставки нет» задач не требуют — это
границы, и они записаны в документации (Task 5) и в докстринге ручки (Task 3).

**Согласованность имён.** `chat_intention_do_on` — столбец, поле тела запроса и
ключ ответа; `chat-intention-do` — адрес чтения, `chat-intention-do-on` — адрес
записи и имя слота; `chat_intention_do_allowed` — дверь домена;
`chatIntentionDoLoad` / `chatIntentionDoPaint` / `chatIntentionDoSet` — код
страницы; `DatingSiteChatIntentionDoOn` — модель. Проверено по всем задачам и
тестам.

⚠ Адрес чтения и адрес записи **намеренно разные** (`chat-intention-do` против
`chat-intention-do-on`): так же устроена пара `chat-answer` / `chat-answer-on`,
и ломать соответствие ради красоты значило бы завести пятую вкладку, устроенную
иначе.
