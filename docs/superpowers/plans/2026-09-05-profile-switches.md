# Две галочки в блоке «Настройки анкеты» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В блок «Настройки анкеты» на `/admin/dating` добавляются два выключателя — «Чат автоответ» (существующий флаг `auto_answer`) и «Письма рассылка» (новый флаг-задел `mailing_on`).

**Architecture:** «Чат автоответ» ничего не заводит: колонка, ручка `PUT /{id}/auto` и чтение воркером уже есть, добавляется только показ на странице. «Письма рассылка» заводит колонку `dating_profile.mailing_on`, ручку `PUT /{id}/mailing` и поле в карточке; исполнителя у флага нет и в этой задаче не появляется. Разметку трёх строк собирает общая функция `setRow`, обвязку трёх обработчиков — общая `setBind`.

**Tech Stack:** FastAPI + Pydantic, MySQL + `yoyo`, Jinja2-шаблон со скриптом внутри, `pytest` + `httpx`.

**Спецификация:** `docs/superpowers/specs/2026-09-05-profile-switches-design.md`

## Global Constraints

- Комментарии и docstring — **по-русски**, в тоне и плотности соседнего кода: он объясняет «почему», а не «что».
- Тесты **наружу не ходят** — ни к RhapsodyAi, ни на сайт знакомств. Сторож сети в `tests/conftest.py` это проверяет; заглушка соседа — фикстура `rhapsody` из `tests/rhapsody_fake.py`.
- Тестовые анкеты **одноразовые** и удаляются в `finally`. Живые трогать нельзя: щёлкая их флагами, сюита меняла бы работу настоящих учёток.
- `SITE = 343` — сайт для тестовых анкет, как в `tests/test_profile_enabled.py`.
- Ручки в тестах зовутся **в процессе**, а не по HTTP: заведение анкеты сверяет сайт у соседа, и по HTTP это дало бы два запроса к RhapsodyAi на каждую тестовую учётку — то есть сюиту, падающую от его перезапуска. Исключение — проверки, решающиеся до входа в обработчик (401): они по HTTP.
- Прогон сюиты: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/ -q` (`.claude/docs/testing.md`, §1). Панель при этом должна работать.
- Миграции накатывает `yoyo` при старте: `docker compose up -d --force-recreate dating_uvicorn`. Применённые миграции **не правят и не переименовывают** — переименовать значит применить заново (`.claude/docs/migration.md`, §2.1).
- Имя файла миграции: `20260905120000_dating_profile_mailing.sql`.

---

## File Structure

**Создаются:**

| Файл | За что отвечает |
|------|-----------------|
| `migrations/20260905120000_dating_profile_mailing.sql` | Колонка `dating_profile.mailing_on` |
| `tests/test_profile_mailing.py` | Хранение флага, независимость от соседних флагов, доезд до карточки, коды отказов, наличие обеих галочек в разметке |

**Меняются:**

| Файл | Что меняется |
|------|--------------|
| `src/uvicorn/routers/dating/dating_profile_action.py` | Модель `DatingProfileMailing` и ручка `PUT /{profile_id}/mailing` — после обработчика `/enabled` (строка 260), перед `@router.post('/{profile_id}/login')` |
| `src/uvicorn/routers/dating/dating_profile_view.py:287` | Поле `mailing_on` в ответе `dating_profile_view_card` |
| `data/uvicorn/template/admin/dating.html:1204-1281` | `settingsBlock`, новые `setRow`, `setBind`, `enabledSet`, `autoSet`, `mailingSet`, переписанный `settingsBind` |
| `.claude/docs/api.md:83` | Строка ручки `/mailing` в таблице |
| `.claude/docs/database.md:124` | Абзац про колонку `mailing_on` |
| `.claude/docs/dashboard_frontend.md:887` | Три галочки вместо одной и почему у рассылки нет приглушения |

**Не меняются:** `src/uvicorn/routers/dating/dating_profile.py` (списку флаг не нужен — блок читает карточку), `src/uvicorn/uvicorn_validation.py` (`schedule_on` там тоже нет: род ошибки у булева один), `src/dating/dating_auto.py` (круг рассылки не пишется), `.claude/docs/dating.md` (предметной механики у рассылки нет).

---

### Task 1: Колонка `mailing_on`, ручка `PUT /{id}/mailing`, поле в карточке

**Files:**
- Create: `migrations/20260905120000_dating_profile_mailing.sql`
- Create: `tests/test_profile_mailing.py`
- Modify: `src/uvicorn/routers/dating/dating_profile_action.py` (после строки 260)
- Modify: `src/uvicorn/routers/dating/dating_profile_view.py:287`

**Interfaces:**
- Consumes: `dating_profile_row(profile_id) -> dict` (404, если анкеты нет) и `dating_log_write(profile_id, level, text)` — оба уже импортированы в `dating_profile_action.py` (строки 48 и ниже); `mysql_get_db_async`; `DatingProfileRepository.find` делает `SELECT *`, поэтому новая колонка попадает в `dating_profile_row` сама.
- Produces:
  - `class DatingProfileMailing(BaseModel)` с полем `mailing_on: bool`
  - `async dating_profile_action_mailing(profile_id: int, data: DatingProfileMailing, user=...) -> dict` — отвечает `{'result': {'mailing_on': bool}}`
  - `dating_profile_view_card(...)['result']['mailing_on']: bool`
  - Ручка `PUT /api/dating/profile/{id}/mailing`, тело `{"mailing_on": true|false}`

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_profile_mailing.py`:

```python
"""⚠ Рассылка писем: флаг есть, исполнителя нет.

Третий выключатель анкеты, и он про своё. `enabled` — «ведём ли анкету вовсе»,
`auto_answer` — «отвечает ли за неё агент в чате», `mailing_on` — «шлём ли от неё
рассылку писем». Случаи не совпадают: анкета отвечает в чате, но не рассылает;
анкету ведут руками, а рассылка стоит.

⚠ **Круга рассылки пока не существует.** Флаг только хранит решение человека —
исполнителя напишет отдельная задача. Поэтому здесь проверяется хранение и
независимость от соседних флагов, а не поведение: сторожить отсутствие поведения
нечем.

База настоящая, сосед поддельный. Ручки зовутся в процессе, а не по HTTP:
заведение анкеты сверяет сайт у соседа, и по HTTP это дало бы два запроса к
RhapsodyAi на каждую тестовую учётку — то есть сюиту, падающую от его перезапуска.
То же правило, что в `test_profile_crud.py` и `test_profile_enabled.py`.
"""

import uuid

import pytest
from fastapi import HTTPException

from src.uvicorn.routers.dating.dating_profile import (DatingProfileSave,
                                                       dating_profile_create,
                                                       dating_profile_delete,
                                                       dating_profile_row)
from src.uvicorn.routers.dating.dating_profile_action import (
    DatingProfileAuto, DatingProfileEnabled, DatingProfileMailing,
    dating_profile_action_auto, dating_profile_action_enabled,
    dating_profile_action_mailing)
from src.uvicorn.routers.dating.dating_profile_view import dating_profile_view_card

SITE = 343


@pytest.fixture
async def profile(rhapsody):
    """Одноразовая анкета в живой базе.

    ⚠ Чужую трогать нельзя: щёлкая её флагами, сюита меняла бы работу настоящей
    учётки — и человек искал бы поломку там, где её нет.
    """
    answer = await dating_profile_create(
        DatingProfileSave(site_id=SITE, login=f'mailing-{uuid.uuid4().hex[:10]}',
                          password='secret', note='тест рассылки'),
        user=None)
    profile_id = int(answer['result'])

    yield profile_id

    try:
        await dating_profile_delete(profile_id, user=None)
    except HTTPException:
        pass


class TestMailingEndpoint:
    """Ручка рассылки: хранение и независимость от соседних флагов."""

    async def test_new_profile_has_mailing_off(self, profile):
        """⚠ Умолчание — «не рассылаем», в отличие от `enabled` («ведём»).

        Показать включённой галочку у функции, которой нет, значит соврать:
        человек решит, что рассылка идёт.
        """
        assert await _mailing(profile) is False

    async def test_toggle_is_stored(self, profile):
        await _set(profile, True)

        assert await _mailing(profile) is True

        await _set(profile, False)

        assert await _mailing(profile) is False

    async def test_mailing_is_independent(self, profile):
        """⚠ Три флага про разное, и ни один не задевает соседей.

        Состояния «ведём руками, рассылка идёт» и «отвечаем в чате, не рассылаем»
        законны. Задень один флаг другим — и они стали бы недостижимы.
        """
        await dating_profile_action_auto(
            profile, DatingProfileAuto(auto_answer=False), request=None, user=None)
        await _set(profile, True)
        await dating_profile_action_enabled(
            profile, DatingProfileEnabled(enabled=False), user=None)

        row = await dating_profile_row(profile)

        assert bool(row['mailing_on']) is True, 'выключение анкеты сбросило рассылку'
        assert bool(row['auto_answer']) is False, 'рассылка вернула автоответ'
        assert bool(row['enabled']) is False

    async def test_mailing_reaches_the_card(self, rhapsody, profile):
        """Галочку рисует карточка — значит флаг должен в ней быть.

        ⚠ Без него после перезагрузки страницы галочка показывала бы снятую там,
        где стоит: человек поставил бы её второй раз и не понял, почему она не
        держится.
        """
        await _set(profile, True)
        card = (await dating_profile_view_card(profile, user=None))['result']

        assert card['mailing_on'] is True

    async def test_unknown_profile_is_404(self):
        """404, а не молчаливое ничего: правка несуществующей анкеты — ошибка
        вызывающего, и знать о ней он должен."""
        with pytest.raises(HTTPException) as refusal:
            await _set(99_999_999, True)

        assert refusal.value.status_code == 404

    async def test_closed_without_login(self, client):
        """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""
        answer = await client.put('/api/dating/profile/1/mailing',
                                  json={'mailing_on': True})

        assert answer.status_code == 401


async def _set(profile_id: int, mailing_on: bool) -> None:
    await dating_profile_action_mailing(
        profile_id, DatingProfileMailing(mailing_on=mailing_on), user=None)


async def _mailing(profile_id: int) -> bool:
    return bool((await dating_profile_row(profile_id))['mailing_on'])
```

- [ ] **Шаг 2: Прогнать тест и убедиться, что он падает**

Выполнить: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_profile_mailing.py -q`

Ожидается: сбор падает целиком — `ImportError: cannot import name 'DatingProfileMailing'`. Это верное падение: ни модели, ни ручки ещё нет.

- [ ] **Шаг 3: Завести миграцию**

Создать `migrations/20260905120000_dating_profile_mailing.sql`:

```sql
-- Флаг рассылки писем (2026-09-05)
--
-- ЗАЧЕМ: в блоке «Настройки анкеты» появляется галочка «Письма рассылка», и её
-- состоянию негде храниться. На «Профилях» вкладка с таким именем уже есть, но
-- её тело — заглушка: механики рассылки в проекте нет.
--
-- ⚠ ИСПОЛНИТЕЛЯ У ФЛАГА НЕТ, И ЭТО НЕ НЕДОДЕЛКА. Круга рассылки не существует —
-- колонка хранит решение человека, чтобы оно не пропадало до того дня, когда
-- рассылку напишут. Никто её не читает: ни воркер, ни отправка.
--
-- ⚠ ПОЧЕМУ УМОЛЧАНИЕ 0, А НЕ 1 КАК У `auto_answer`. Показать включённой галочку у
-- функции, которой нет, значит соврать: человек решит, что рассылка идёт, и не
-- поймёт, почему писем никто не получил.
--
-- ⚠ ПОЧЕМУ БЕЗ ИНДЕКСА. Отбирать по колонке некому — круга нет. Индекс, который
-- никто не читает, платится на каждой записи. Заведёт его та задача, которая
-- напишет круг, — как `idx_dating_profile_sched` завела задача расписания.
--
-- Идемпотентность: `yoyo` её не требует (`.claude/docs/migration.md`, §2), но
-- соседние файлы её держат, и цена нулевая — зато файл можно прогнать руками по
-- прод-туннелю, не боясь второго накатывания.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME = 'mailing_on') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_profile`) AS анкет;

-- ------------------------------------------- Колонка
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'mailing_on') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `mailing_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Рассылка писем: флаг задела, исполнителя пока нет''
            AFTER `auto_answer_only`',
    'SELECT ''колонка mailing_on уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `mailing_on` = 1) AS рассылают,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `mailing_on` = 0) AS не_рассылают;
```

- [ ] **Шаг 4: Накатить миграцию и убедиться, что она применилась**

Выполнить: `docker compose up -d --force-recreate dating_uvicorn`

Затем: `docker compose logs --tail 80 dating_uvicorn`

Ожидается: в журнале старта есть строка «Будет применено миграций: 1» и имя `20260905120000_dating_profile_mailing`.

- [ ] **Шаг 5: Добавить модель и ручку**

В `src/uvicorn/routers/dating/dating_profile_action.py`, **сразу после** обработчика `dating_profile_action_enabled` (он заканчивается около строки 260) и **перед** `@router.post('/{profile_id}/login', include_in_schema=False)`:

```python
class DatingProfileMailing(BaseModel):
    """Рассылка писем: флаг задела, исполнителя пока нет."""

    mailing_on: bool = Field(..., description='Слать ли от анкеты рассылку писем')


@router.put('/{profile_id}/mailing', include_in_schema=False)
async def dating_profile_action_mailing(profile_id: int, data: DatingProfileMailing,
                                        user=Depends(auth_required)):
    """Включает или выключает рассылку писем.

    ⚠ **Исполнителя у флага нет, и это не забытый баг.** Круга рассылки в проекте
    не существует: ручка хранит решение человека, чтобы оно не пропадало до того
    дня, когда рассылку напишут. Колонку никто не читает — ни воркер, ни отправка.

    ⚠ **Третий выключатель анкеты, и он про своё.** `enabled` — «ведём ли анкету
    вовсе», `auto_answer` — «отвечает ли за неё агент в чате», здесь — «шлём ли от
    неё рассылку писем». Состояния «отвечаем в чате, но не рассылаем» и «ведём
    руками, а рассылка идёт» законны, и каждое достижимо только своим флагом.

    Побочных действий нет — в отличие от `/auto`, где включение сразу разбирает
    накопившееся. Разбирать здесь нечего.
    """
    await dating_profile_row(profile_id)

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET mailing_on = %s WHERE id = %s',
                         (1 if data.mailing_on else 0, profile_id))
        await db.connection.commit()

    await dating_log_write(profile_id, 'info',
                           'Рассылка писем включена' if data.mailing_on
                           else 'Рассылка писем выключена')

    return {'result': {'mailing_on': data.mailing_on}}
```

- [ ] **Шаг 6: Отдать флаг в карточку**

В `src/uvicorn/routers/dating/dating_profile_view.py`, строка 287 — после `'auto_answer': bool(profile.get('auto_answer')),` добавить:

```python
                       # Рассылка писем — третья галочка блока «Настройки анкеты».
                       # ⚠ Исполнителя у флага пока нет: карточка отдаёт его,
                       # чтобы галочка после перезагрузки страницы показывала то,
                       # что человек поставил, а не снятую.
                       'mailing_on': bool(profile.get('mailing_on')),
```

- [ ] **Шаг 7: Прогнать тест и убедиться, что он проходит**

Выполнить: `docker compose up -d --force-recreate dating_uvicorn && ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_profile_mailing.py -q`

Ожидается: `6 passed` (класс `TestMailingEndpoint` целиком).

- [ ] **Шаг 8: Прогнать всю сюиту — соседние флаги не задеты**

Выполнить: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/ -q`

Ожидается: столько же прошедших, сколько до правки, плюс шесть новых. Особое внимание — `tests/test_profile_enabled.py`: он держит те же две оси, и его падение означало бы, что новая колонка задела старые.

- [ ] **Шаг 9: Коммит**

```bash
git add migrations/20260905120000_dating_profile_mailing.sql \
        tests/test_profile_mailing.py \
        src/uvicorn/routers/dating/dating_profile_action.py \
        src/uvicorn/routers/dating/dating_profile_view.py
git commit -m "Флаг рассылки писем: колонка, ручка и поле в карточке анкеты"
```

---

### Task 2: Три галочки в блоке «Настройки анкеты»

**Files:**
- Modify: `data/uvicorn/template/admin/dating.html:1204-1281` (функции `settingsBlock` и `settingsBind`)
- Modify: `tests/test_profile_mailing.py` (добавляется класс `TestPageCarriesTheBoxes`)

**Interfaces:**
- Consumes: `data.enabled`, `data.auto_answer`, `data.mailing_on` из ответа `GET /api/dating/profile/{id}/card` (Task 1); ручки `PUT /{id}/enabled`, `PUT /{id}/auto` (существующая), `PUT /{id}/mailing` (Task 1). Готовое на странице: `putData(url, data)` возвращает разобранный JSON и бросает при отказе (`data/uvicorn/public/js/api.js:41`), `say(text, isError)`, `profileLoad()`, `profilesLoad()`, `passwordBlock(data)`, `scheduleBlock(row)`, `passwordBind()`, `scheduleBind(profileEl, row)`.
- Produces: `setRow(id, on, title, note, dim) -> string` и `setBind(id, save)`; идентификаторы `enabledBox`, `autoBox`, `mailingBox` в разметке страницы.

⚠ **CSS не меняется.** `.prof-set-row`, `.prof-set-row.off` и `.prof-set-row i` уже описаны (`dating.html:317-337`); у рассылки просто не ставится класс `off`.

- [ ] **Шаг 1: Написать падающий тест**

Добавить в конец `tests/test_profile_mailing.py`, **перед** функциями `_set` и `_mailing`:

```python
class TestPageCarriesTheBoxes:
    """Страница отдаёт разметку обеих новых галочек.

    Блок собирается скриптом уже в браузере, но сам скрипт лежит в шаблоне —
    значит идентификаторы видны прямо в ответе. Сторожит от того, что галочки
    молча выпадут при следующей правке блока: молчаливая пропажа настройки хуже
    сломанной, её никто не заметит.
    """

    async def test_both_boxes_are_in_the_page(self, auth_client):
        page = (await auth_client.get('/admin/dating')).text

        # ⚠ Ищем **вызов**, а не голое имя: `id="autoBox"` в ответе не встретится
        # вовсе (разметку собирает скрипт, в шаблоне лежит `id="${id}"`), а голое
        # `autoBox` совпало бы и внутри комментария — тест пережил бы удаление
        # самой строки.
        assert "setRow('autoBox'" in page, 'галочка «Чат автоответ» пропала со страницы'
        assert "setRow('mailingBox'" in page, 'галочка «Письма рассылка» пропала со страницы'
```

- [ ] **Шаг 2: Прогнать тест и убедиться, что он падает**

Выполнить: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_profile_mailing.py::TestPageCarriesTheBoxes -q`

Ожидается: FAIL с `AssertionError: галочка «Чат автоответ» пропала со страницы` — галочек в шаблоне ещё нет.

- [ ] **Шаг 3: Переписать `settingsBlock` и добавить `setRow`**

В `data/uvicorn/template/admin/dating.html` заменить строки 1204-1240 (комментарий и функцию `settingsBlock` целиком) на:

```javascript
    // ── Настройки анкеты ─────────────────────────────────────────────────────
    //
    // Три выключателя подряд, и все три про одно — как эту анкету ведут. Порядок
    // от общего к частному: сама анкета, чат, письма. Стоят до блока пароля
    // потому, что пароль и расписание — про «чем и когда», а эти — про «ведём ли».
    //
    // ⚠ Первый — **ведём ли** мы анкету вовсе. Не то же, что «ai-ответ»: тот про
    // то, **кто** отвечает. Выключенную воркер не трогает ничем: ни автоответ, ни
    // отправка. Входящие при этом принимаются, и это сказано прямо — иначе
    // выключатель читается как «отключить от сайта».
    //
    // ⚠ Второй — **тот же самый флаг**, что галочка «ai-ответ» на «Профилях» и в
    // окне переписки. Не копия: одна колонка, одна ручка, и щелчок здесь виден
    // там же.
    //
    // ⚠ Третий пока ничего не запускает: круга рассылки в проекте нет. Галочка
    // хранит решение, и пояснение говорит это прямо — молчащая настройка без
    // объяснения читается как поломка.
    //
    // Умолчание каждой галочки повторяет умолчание её колонки: `enabled` и
    // `auto_answer` заведены с единицей, `mailing_on` — с нулём. Разойдись они, и
    // анкета без ответа сервера выглядела бы не так, как она работает.
    function settingsBlock(data) {
        const on = data.enabled !== false;
        // Строка списка: в ней логин, пароль, роль и пометка — то, что правят
        // формы ниже. В `data` их нет: карточка отдаёт анкету с сайта, а не учётку.
        const row = profiles.find(g => g.id === currentId) || {};

        return '<div class="prof-set">'
            + '<div class="prof-set-head">Настройки анкеты</div>'
            + setRow('enabledBox', on, 'Анкета включена',
                     'Выключенную анкету воркер не ведёт: не отвечает и ничего не '
                     + 'отправляет. Входящие сообщения при этом принимаются.', true)
            + setRow('autoBox', data.auto_answer !== false, 'Чат автоответ',
                     'Отвечает агент, а не человек. Включение сразу разбирает '
                     + 'накопившееся — ответы могут уйти прямо сейчас.', true)
            // ⚠ Без приглушения, в отличие от двух выше: умолчание здесь снято, и
            // жёлтая строка горела бы у каждой анкеты и всегда. Жёлтый везде —
            // жёлтый нигде, и тогда он перестанет работать у двух первых.
            + setRow('mailingBox', data.mailing_on === true, 'Письма рассылка',
                     'Рассылка ещё не работает: галочка только запоминает решение.',
                     false)
            // ⚠ Расписание переехало сюда с «Профилей»: там оно стояло во вкладке
            // «Настройки» рядом с чтением переписки, хотя говорит о том же, о чём
            // выключатель, — как эту анкету ведут.
            + passwordBlock(data)
            + scheduleBlock(row)
            // Удаление — внизу и отдельной полосой: это единственное здесь
            // необратимое действие, и стоять рядом с галочками ему нельзя.
            + '<div class="prof-set-danger">'
            + '<button type="button" class="dating-btn danger" id="deleteBtn">'
            + 'Удалить анкету</button>'
            + '<i>Уходит вся переписка, собеседники и журнал: они привязаны к '
            + 'анкете. Учётка на сайте остаётся — удаляется только то, что о ней '
            + 'знает панель.</i>'
            + '</div>'
            + '</div>';
    }

    // Строка-выключатель. Три штуки отличаются только идентификатором, подписью и
    // пояснением — собираем одной функцией: три копии разметки разошлись бы в
    // мелочах (класс, отступы, вложенность), а мелочи в этом блоке и есть смысл.
    //
    // `dim` — приглушать ли снятую строку жёлтым. У рассылки не приглушаем
    // (почему — выше).
    function setRow(id, on, title, note, dim) {
        return `<label class="prof-set-row${!on && dim ? ' off' : ''}">`
            + `<input type="checkbox" id="${id}"${on ? ' checked' : ''}>`
            + `<span><b>${title}</b><i>${note}</i></span></label>`;
    }
```

- [ ] **Шаг 4: Переписать `settingsBind` и добавить обработчики**

В том же файле заменить функцию `settingsBind` (была на строках 1242-1281) на код ниже.

⚠ **Ранний выход `if (!box) return` исчезает — так и надо, но с оговоркой.** Раньше он закрывал сразу всё: нет `enabledBox` — не вешаем ничего. Теперь каждый выключатель закрыт своим `if (!box) return` внутри `setBind`, `passwordBind` и `scheduleBind` закрыты своими `if (!form) return` (`dating.html:1172` и `dating.html:781`), а вот кнопка удаления оставалась бы без защиты — поэтому у неё появился явный `if (deleteBtn)`. Без него блок, отрисованный не целиком, ронял бы всю привязку на `null.addEventListener`.

```javascript
    function settingsBind() {
        // Та же строка, что рисовала блок: кнопка входа-выхода читает из неё
        // состояние анкеты.
        const row = profiles.find(g => g.id === currentId) || {id: currentId};

        setBind('enabledBox', enabledSet);
        setBind('autoBox', autoSet);
        setBind('mailingBox', mailingSet);

        const deleteBtn = document.getElementById('deleteBtn');
        if (deleteBtn) deleteBtn.addEventListener('click', () => profileDelete());

        // Обработчики расписания: блок перерисовывается вместе с настройками,
        // поэтому вешаются каждый раз — узлы новые.
        passwordBind();
        scheduleBind(profileEl, row);
    }

    // Общая обвязка выключателя: запереть, сходить, отпустить, при отказе вернуть
    // галочку и сказать причину.
    //
    // ⚠ Запираем на время запроса потому, что круг перерисовки идёт каждые 15
    // секунд, и второе нажатие успело бы уехать раньше, чем вернулся ответ на
    // первое, — состояние в базе разошлось бы с галочкой.
    function setBind(id, save) {
        const box = document.getElementById(id);
        if (!box) return;

        box.addEventListener('change', async () => {
            const on = box.checked;
            box.disabled = true;
            try {
                await save(on, box);
            } catch (e) {
                box.checked = !on;
                say('не переключилось: ' + e.message, true);
            } finally {
                box.disabled = false;
            }
        });
    }

    async function enabledSet(on, box) {
        await putData(`${BASE}/api/dating/profile/${currentId}/enabled`, {enabled: on});
        box.closest('.prof-set-row').classList.toggle('off', !on);
        say(on ? 'анкета включена' : 'анкета выключена');
        // Список слева рисует значок выключенной — обновляем и его.
        await profilesLoad();
        // ⚠ И карточку: рядом стоит расписание, а оно у выключенной не действует.
        // Без перечитывания оно осталось бы в прежнем виде — человек выключил
        // анкету, а расписание говорит, что работает.
        await profileLoad();
    }

    // ⚠ Тот же флаг, что «ai-ответ» на «Профилях», — и то же побочное действие:
    // включение сразу разбирает накопившееся, и ответы могут уйти в эту же
    // секунду. Поэтому говорим, сколько ушло: молчаливое «включено» скрыло бы
    // отправку, о которой человек не просил вслух.
    async function autoSet(on, box) {
        const answer = await putData(`${BASE}/api/dating/profile/${currentId}/auto`,
                                     {auto_answer: on});
        box.closest('.prof-set-row').classList.toggle('off', !on);

        // `answered` приходит пустым, когда агента у анкеты ещё нет: разбирать
        // было некем. Отсюда чтение через умолчание, а не напрямую.
        const sent = (answer.result && answer.result.answered
                      && answer.result.answered.sent) || 0;
        if (!on) say('автоответ выключен');
        else say(sent ? `автоответ включён, отправлено ${sent}` : 'автоответ включён');

        // Ответы ушли — собеседницы в карточке изменились. Список слева про
        // автоответ ничего не рисует (значок там только у выключенной), его не
        // трогаем.
        if (sent) await profileLoad();
    }

    async function mailingSet(on) {
        await putData(`${BASE}/api/dating/profile/${currentId}/mailing`,
                      {mailing_on: on});
        // ⚠ Класс `off` не трогаем: у этой строки приглушения нет вовсе.
        // Перечитывать тоже нечего — флаг пока никто не исполняет.
        say(on ? 'рассылка писем включена' : 'рассылка писем выключена');
    }
```

- [ ] **Шаг 5: Прогнать тест страницы**

Выполнить: `docker compose up -d --force-recreate dating_uvicorn && ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/test_profile_mailing.py -q`

Ожидается: `7 passed`.

- [ ] **Шаг 6: Проверить руками в браузере**

Открыть `http://localhost:8021/admin/dating`, выбрать анкету слева. Проверить:

1. В блоке «Настройки анкеты» три галочки в порядке: «Анкета включена», «Чат автоответ», «Письма рассылка».
2. «Письма рассылка» снята и **не жёлтая**; под подписью — «Рассылка ещё не работает: галочка только запоминает решение».
3. Щелчок по «Письма рассылка» → подпись внизу «рассылка писем включена»; после `F5` галочка стоит.
4. Щелчок по «Чат автоответ» → строка гаснет/загорается жёлтым, подпись говорит «автоответ включён» (или «…, отправлено N»).
5. Снять «Чат автоответ» здесь → открыть `/admin/dating/profile`, найти ту же анкету: галочка «ai-ответ» тоже снята. Это один флаг, а не два.

⚠ Если правки не видно — `Ctrl+Shift+R`: у статики есть кэш (`.claude/docs/dashboard_frontend.md`, §4).

- [ ] **Шаг 7: Прогнать всю сюиту**

Выполнить: `ADMIN_PASSWORD=<пароль> .venv/bin/pytest tests/ -q`

Ожидается: всё зелёное. Особое внимание — `tests/test_timezone.py` и `tests/test_role.py`: они читают `/admin/dating` целиком и упали бы на сломанном шаблоне.

- [ ] **Шаг 8: Коммит**

```bash
git add data/uvicorn/template/admin/dating.html tests/test_profile_mailing.py
git commit -m "Настройки анкеты: галочки «Чат автоответ» и «Письма рассылка»"
```

---

### Task 3: Документация

**Files:**
- Modify: `.claude/docs/api.md:83`
- Modify: `.claude/docs/database.md:124`
- Modify: `.claude/docs/dashboard_frontend.md:887`

**Interfaces:**
- Consumes: ручку и колонку из Task 1, вид блока из Task 2. Ничего не производит для других задач.

⚠ **`.claude/docs-common/` не трогать** — это симлинк на `py_modules/docs`, общий для всех проектов; правка через него уходит в чужой репозиторий (`CLAUDE.md`).

- [ ] **Шаг 1: Строка в таблицу ручек**

В `.claude/docs/api.md` после строки 83 (`| PUT | /{id}/enabled | …`) добавить:

```markdown
| `PUT` | `/{id}/mailing` | Слать ли от анкеты рассылку писем. ⚠ **Флаг задела: исполнителя нет**, круга рассылки в проекте не существует — ручка хранит решение человека |
```

- [ ] **Шаг 2: Абзац про колонку**

В `.claude/docs/database.md` после абзаца про `dating_profile.can_work` (заканчивается на строке 126) добавить:

```markdown
**`dating_profile.mailing_on`** — слать ли от анкеты рассылку писем. ⚠ **Читать
эту колонку пока некому:** круга рассылки в проекте нет, и `mailing_on` хранит
только решение человека, поставленное галочкой в блоке «Настройки анкеты». Не
ищите исполнителя — его напишет отдельная задача, она же заведёт индекс.

⚠ Умолчание `0`, в отличие от `auto_answer` (`1`): показать включённой галочку у
функции, которой нет, значит соврать. Третий флаг ведения анкеты рядом с `enabled`
(ведём ли) и `auto_answer` (отвечает ли агент в чате) — все три про разное.
```

- [ ] **Шаг 3: Правка описания правой колонки**

В `.claude/docs/dashboard_frontend.md`, в разделе «Правая колонка «Анкет»: настройки, а не собеседницы», заменить абзац (строки 887-889):

```markdown
Первая — выключатель анкеты (`dating.md`, §5.10). Пояснение под подписью, а не
подсказкой: выключатель редкий, последствия у него широкие — их читают один раз и
запоминают, а наводить мышь ради этого никто не станет.
```

на:

```markdown
Сверху — **три выключателя подряд**, и все про одно: как эту анкету ведут.
«Анкета включена» (`dating.md`, §5.10) — ведём ли вовсе; «Чат автоответ» —
отвечает ли за неё агент; «Письма рассылка» — шлём ли от неё письма. Пояснение под
каждой подписью, а не подсказкой: выключатели редкие, последствия у них широкие —
их читают один раз и запоминают, а наводить мышь ради этого никто не станет.

⚠ **«Чат автоответ» — тот же флаг, что «ai-ответ» на «Профилях»** и в окне
переписки, а не его копия: одна колонка `auto_answer`, одна ручка `PUT /{id}/auto`,
и щелчок здесь виден там. У этой ручки есть побочное действие — включение сразу
разбирает накопившееся, — поэтому подпись внизу говорит, сколько ответов ушло:
молчаливое «включено» скрыло бы отправку.

⚠ **«Письма рассылка» не приглушается жёлтым**, в отличие от двух соседних.
Умолчание у колонки снято, и жёлтая строка горела бы у каждой анкеты и всегда —
а жёлтый везде значит жёлтый нигде, и тогда он перестанет работать у двух первых.
Что рассылка ещё не работает, сказано словами в пояснении: молчащая настройка без
объяснения читается как поломка.

Разметку всех трёх строк собирает одна функция `setRow`, обвязку обработчиков —
`setBind`: три копии разошлись бы в мелочах, а мелочи в этом блоке и есть смысл.
```

- [ ] **Шаг 4: Убедиться, что ссылки не разъехались**

Выполнить: `grep -ln "mailing_on\|/mailing" .claude/docs/*.md`

Ожидается **два** файла — `api.md` и `database.md`. ⚠ `dashboard_frontend.md` в этот вывод не попадает и попасть не должен: он описывает страницу, а страница говорит названиями галочек («Письма рассылка»), а не именем колонки. Добавлять туда `mailing_on` ради совпадения с `grep` — значит подгонять текст под проверку.

Затем: `grep -ln "Письма рассылка" .claude/docs/*.md` — ожидается `dashboard_frontend.md`.

В обоих выводах `dating.md` быть не должно: предметной механики у рассылки нет, и раздел она получит тогда, когда появится.

- [ ] **Шаг 5: Коммит**

```bash
git add .claude/docs/api.md .claude/docs/database.md .claude/docs/dashboard_frontend.md
git commit -m "Документация: флаг рассылки писем и три галочки в настройках анкеты"
```

---

## Чего в этой работе нет

- **Сама рассылка писем** — круг воркера, кому пишем, чем отправляем (API сайта или сценарий у соседа), как часто. Отдельная задача; она же заведёт индекс по `mailing_on`.
- **Перестройка вкладок «Профилей»** — вкладки поднимаются над колонками, у каждой свой список профилей. Следующая задача после этой.
