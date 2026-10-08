# Правка персональных данных анкеты: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** на вкладке «Общая информация» страницы «Анкеты» иконка ✎ открывает окно,
которым человек правит почту, номер анкеты на сайте, имя, возраст, город и описание
— и правленое руками больше не затирается ни импортом, ни чтением анкеты со страницы.

**Architecture:** шесть полей лежат в трёх разных местах (колонка `login`, колонка
`profile_id` плюс ключ `id` карточки, четыре ключа карточки `profile`). Их сводит
одна доменная дверь `src/dating/profile_card.py`; она же ведёт новую колонку
`profile_edited` — список полей, правленых человеком. Это правило читают **оба**
писателя карточки со стороны сайта, импорт и чтение страницы кабинета, одной и той
же функцией. Наружу — ручка `PUT /api/dating/profile/{id}/card` и снятие отметки
`DELETE /api/dating/profile/{id}/card/{field}`.

**Tech Stack:** Python 3 / FastAPI / Pydantic v2, MySQL (миграции `yoyo`), pytest
(`pytest-asyncio`, метки `profile` / `page`), серверные шаблоны Jinja2 и ES-модули
без сборщика.

## Global Constraints

- **Спека:** `docs/superpowers/specs/2026-10-08-profile-card-edit-design.md` — при
  расхождении плана со спекой верна спека; скажи о расхождении, а не выбирай сам.
- **Живого HTTP наружу в тестах нет никогда.** Сосед — только заглушка `rhapsody`
  (`tests/rhapsody_fake.py`), сайт — `site_api_fake.py` (`.claude/docs/testing.md`, §1.2).
- **Анкету для теста берут фикстурой** `girl` / `girl_make` из `tests/conftest.py`.
  Своей не заводить. Стендовый сайт — `TEST_STAND_SITE = 999343`.
- **Логин тестовой анкеты обязан кончаться на `@example.com`** — это метка сюиты,
  по ней уборка находит мусор. Правка логина в тесте тоже кладёт `@example.com`.
- **Ручки зовутся в процессе**, а не по HTTP, когда тест трогает заведение анкеты:
  `profile_create` сверяет сайт, и по HTTP это дало бы запрос к соседу на каждую
  тестовую учётку. Образец — `tests/test_profile_mail_photo.py`.
- **Применённые миграции не правят** — только новый файл рядом
  (`.claude/docs/migration.md`).
- **Границы длин берутся из постоянных импорта**, а не выдумываются рядом:
  `IMPORT_LOGIN_MAX = 190`, `IMPORT_NAME_MAX = 190`, `IMPORT_CITY_MAX = 190`,
  `IMPORT_ABOUT_MAX = 2000` (`src/dating/import_.py:44-49`). Номер — `32` (ширина
  колонки `profile.profile_id`).
- **Возраст 0 — законное «не знаем»**, а не ошибка. Пустая почта — отказ.
- **Номер анкеты — строка, а не число:** у Goldenbride идентификаторы не только цифры.
- **Пусто в номере ложится `NULL`**, а не `''`: уникальный ключ считает пустые
  строки одинаковыми, а `NULL`-ы — разными.
- **Текст в разметку только литералами**; пришедшее с сервера — через `esc()`. На
  этой странице уже была настоящая инъекция через неэкранированный `title`.
- **Проверка после правок** — `pytest -m profile` и `pytest -m page` (полминуты
  против трёх минут на всё). Полный прогон — перед выкладкой.
- **Коммит каждой задачей**, сообщение по-русски, как в `git log`.

---

### Task 1: Колонка `profile_edited`

**Files:**
- Create: `migrations/20261008120000_dating_profile_card_edited.sql`
- Create: `tests/test_profile_card_edit.py`

**Interfaces:**
- Consumes: ничего.
- Produces: колонка `profile.profile_edited` типа `JSON NULL DEFAULT NULL`, стоит
  `AFTER profile`. Читают её задачи 2, 3, 4, 5.

- [ ] **Step 1: Write the failing test**

Создай `tests/test_profile_card_edit.py`:

```python
"""Правка персональных данных анкеты: шесть полей, три места, одна дверь.

Спека — `docs/superpowers/specs/2026-10-08-profile-card-edit-design.md`.

⚠⚠ Главное, что здесь сторожится, — **не «поле сохранилось»**, а две вещи,
которые ломаются молча:

1. Номер анкеты на сайте записан дважды — колонкой `profile.profile_id` и ключом
   `id` в карточке, — и читают их разные места: ключ фактов агента берёт колонку,
   сведение переписки из панели и ссылка на сайт берут карточку. Разойдись они, и
   половина кода увидит номер, а половина пустоту; ни одна проверка не упадёт.
2. Правленое руками обязано переживать импорт и чтение анкеты со страницы. Иначе
   человек увидит затёртую правку и перестанет править вовсе.

База настоящая, сосед поддельный. Ручки зовутся в процессе, а не по HTTP: тот же
довод, что в `test_profile_mail_photo.py`.
"""

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.profile


async def test_column_is_there_and_empty(girl):
    """⚠ Пусто — `NULL`, а не `'[]'`: «отметок нет» должно иметь одно написание."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile_edited FROM profile WHERE id = %s', (girl,))
        row = await db.fetchone() or {}

    assert 'profile_edited' in row, 'колонки profile_edited нет'
    assert row['profile_edited'] is None, 'у свежей анкеты отметок быть не должно'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: FAIL — `Unknown column 'profile_edited' in 'field list'`.

- [ ] **Step 3: Write the migration**

Создай `migrations/20261008120000_dating_profile_card_edited.sql`:

```sql
-- Поля карточки анкеты, правленые человеком (2026-10-08)
--
-- ЗАЧЕМ: на вкладке «Общая информация» появляется окно правки персональных
-- данных — почта, номер на сайте, имя, возраст, город, описание. Четыре из этих
-- полей пишет ещё и сайт: импорт анкет (`IMPORT_CARD_FIELDS`) и чтение анкеты со
-- страницы кабинета (`profile_page`). Без отметки «это правил человек» первый же
-- импорт затёр бы его работу — молча, и человек перестал бы править вовсе.
--
-- ⚠⚠ ПОЧЕМУ КОЛОНКОЙ, А НЕ КЛЮЧОМ ВНУТРИ КАРТОЧКИ. Чтение анкеты со страницы
-- делает `UPDATE profile SET profile = %s` — заменяет JSON **целиком**, значением,
-- собранным из `extract` сценария. Отметка, положенная внутрь, исчезла бы вместе
-- с тем, что она защищает.
--
-- ⚠ ПОЧЕМУ `NULL`, А НЕ `'[]'`. «Отметок нет» и «отметки — пустой список» это один
-- и тот же факт, и два его написания развели бы читателей: одни проверяли бы
-- `IS NULL`, другие длину массива.
--
-- ⚠ ПОЧЕМУ БЕЗ ИНДЕКСА. Отбирать по колонке некому: её читают по одной анкете —
-- импорт по известному номеру и чтение карточки. Индекс, который никто не читает,
-- платится на каждой записи.
--
-- ⚠ ЗАПОЛНЯТЬ НЕЧЕГО. Правленых руками полей до этой правки не существовало:
-- править было нечем.
--
-- Идемпотентность: `yoyo` её не требует (`.claude/docs/migration.md`, §2), но
-- соседние файлы её держат, и цена нулевая — зато файл можно прогнать руками по
-- прод-туннелю, не боясь второго накатывания.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'profile'
           AND COLUMN_NAME = 'profile_edited') AS колонка_есть,
       (SELECT COUNT(*) FROM `profile`) AS анкет;

-- ------------------------------------------- Колонка
--
-- ⚠ `AFTER profile`: отметка про эту самую карточку, и читается рядом с ней, а не
-- в хвосте таблицы между временами создания и правки.
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'profile'
        AND COLUMN_NAME = 'profile_edited') = 0,
    'ALTER TABLE `profile`
        ADD COLUMN `profile_edited` JSON NULL DEFAULT NULL
            COMMENT ''Поля карточки, правленые человеком: сайт их не перезаписывает''
            AFTER `profile`',
    'SELECT ''колонка profile_edited уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `profile`) AS анкет,
       (SELECT COUNT(*) FROM `profile` WHERE `profile_edited` IS NOT NULL) AS с_отметками;
```

- [ ] **Step 4: Apply the migration**

Run:
```bash
PYTHONPATH=py_modules python -m mysql_.migration.mysql_migration_pending
```
Expected: в списке непримененных значится `20261008120000_dating_profile_card_edited`.

Накат:
```bash
PYTHONPATH=py_modules python -c "from mysql_.migration.mysql_migration_up import mysql_migration_up; mysql_migration_up()"
```
Expected: процесс завершается без исключения. ⚠ Репетиция (`mysql_rehearse`) накат
не заменяет: она режет файл иначе, чем `yoyo`, и зелёная репетиция не обещает, что
миграция применится.

Проверка глазами:
```bash
PYTHONPATH=py_modules python py_modules/mysql_/mysql_query.py sql "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='profile' AND COLUMN_NAME='profile_edited'"
```
Expected:
```
COLUMN_NAME     COLUMN_TYPE  IS_NULLABLE  COLUMN_DEFAULT
profile_edited  json         YES
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add migrations/20261008120000_dating_profile_card_edited.sql tests/test_profile_card_edit.py
git commit -m "Анкета: колонка profile_edited — какие поля карточки правил человек

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Правило «правленое руками не берём» — чистой функцией

**Files:**
- Create: `src/dating/profile_card.py`
- Test: `tests/test_profile_card_edit.py` (дописать)

**Interfaces:**
- Consumes: колонка `profile_edited` из задачи 1.
- Produces:
  - `PROFILE_CARD_FIELDS: tuple[str, ...]` — `('id', 'name', 'age', 'city', 'about')`
  - `PROFILE_CARD_EMPTY: dict[str, str | int]` — чем пусто у каждого поля
  - `profile_card_edited(value) -> list[str]` — отметки из колонки, только известные имена
  - `profile_card_keep(fresh: dict, edited) -> dict` — пришедшее с сайта без правленых полей

- [ ] **Step 1: Write the failing test**

Допиши в `tests/test_profile_card_edit.py`:

```python
from src.dating.profile_card import (PROFILE_CARD_FIELDS, profile_card_edited,
                                     profile_card_keep)


class TestKeepRule:
    """Правило слияния: что сайт имеет право положить поверх.

    ⚠ Чистая функция и тесты без базы: правило читают **два** писателя карточки —
    импорт и чтение страницы кабинета, — и своя копия в каждом разошлась бы на
    первой правке. Сторож стоит на самом правиле, а не на его применении.
    """

    def test_untouched_card_passes_whole(self):
        fresh = {'id': '77', 'name': 'Olga', 'age': 31, 'city': 'Kyiv', 'about': 'текст'}

        assert profile_card_keep(fresh, None) == fresh

    def test_edited_field_is_dropped(self):
        """⚠ Выбрасываем из пришедшего, а не перекрываем прежним: класть поверх
        нечего — у вызывающего и так остаётся то, что лежит."""
        fresh = {'name': 'Olga', 'about': 'с сайта'}

        assert profile_card_keep(fresh, ['about']) == {'name': 'Olga'}

    def test_edited_takes_a_json_string_too(self):
        """Столбец приезжает то списком, то строкой — зависит от драйвера."""
        assert profile_card_keep({'about': 'с сайта'}, '["about"]') == {}

    def test_unknown_names_are_ignored(self):
        """⚠ Чужое имя в отметках ничего не защищает и ничего не ломает.

        Иначе строка `["password"]`, попавшая в колонку как угодно, отняла бы у
        сайта поле, которого в карточке нет вовсе.
        """
        assert profile_card_edited('["about", "password", "login"]') == ['about']

    def test_broken_json_is_no_marks(self):
        """Испорченное значение — «отметок нет», а не исключение: карточку пишет
        вход, и падать там из-за мусора в колонке нельзя."""
        assert profile_card_edited('{не json') == []

    def test_every_field_has_its_empty(self):
        """⚠ У каждого правимого поля объявлено, чем у него «пусто».

        Без этого «возраст 0 там, где ключа нет вовсе» считалось бы правкой, и
        открытое окно, сохранённое без изменений, пометило бы возраст правленым.
        """
        from src.dating.profile_card import PROFILE_CARD_EMPTY

        assert set(PROFILE_CARD_EMPTY) == set(PROFILE_CARD_FIELDS)
        assert PROFILE_CARD_EMPTY['age'] == 0
        assert PROFILE_CARD_EMPTY['about'] == ''
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dating.profile_card'`.

- [ ] **Step 3: Write minimal implementation**

Создай `src/dating/profile_card.py`:

```python
"""Карточка анкеты: что в ней правит человек и что это защищает от перезаписи.

Карточка (`profile.profile`, JSON) — **кэш того, что отдал сайт**, и писателей у
неё двое: импорт анкет (`import_.py`) и чтение страницы кабинета
(`profile_page.py`). Человек стал третьим: окно правки на вкладке «Общая
информация» даёт вписать то, чего сайт не отдал или отдал неверно.

## ⚠⚠ Правило одно, а мест применения два

Правленое руками сайт перезаписывать не вправе — таково правило проекта («не
трогаем то, что завёл человек», `import_.py`). Проверять это приходится в **двух**
писателях, и своя копия правила в каждом разошлась бы на первой правке: одна
половина системы уважала бы ручную правку, другая затирала. Поэтому правило живёт
здесь одной функцией `profile_card_keep`, а оба писателя её зовут.

## ⚠ Почему отметка выбрасывает поле, а не перекрывает его

`profile_card_keep` возвращает пришедшее с сайта **без** правленых ключей, а не
подменяет их прежними значениями. Так проще и так вернее: у обоих вызывающих
прежняя карточка под рукой (`{**before, **fresh}`), и класть её значения второй раз
означало бы два источника правды в одном выражении.

## ⚠ Номер анкеты — поле карточки, хотя живёт ещё и колонкой

Ключ `id` в списке правимых полей есть, и это не описка: номер записан дважды —
колонкой `profile.profile_id` и ключом карточки, — потому что читают их разные
места (ключ фактов агента берёт колонку, сведение переписки из панели и ссылка на
сайт берут карточку). Отметка защищает обе записи сразу: пока она стоит, импорт не
трогает ключ, а колонку он не пишет вовсе.

⚠ Почты (`login`) среди полей нет намеренно. Это колонка учётки, и импорт у
существующей анкеты её не трогает (`_profile_update` пишет только `profile`).
Отметка защищает от перезаписи; защищать почту не от кого.
"""

from src.dating.json_column import json_column_list

# Поля карточки, которые правят руками. ⚠ Имена — ключи самой карточки, а не
# подписи окна: по ним же ложится отметка, и второе написание развело бы их.
PROFILE_CARD_FIELDS = ('id', 'name', 'age', 'city', 'about')

# Чем «пусто» у каждого поля. ⚠ Нужно потому, что карточку собирают разные
# писатели и ключа может не быть вовсе: без этого «возраст 0 там, где ключа нет»
# читалось бы как правка, и окно, открытое поглядеть и сохранённое без изменений,
# заморозило бы возраст навсегда.
PROFILE_CARD_EMPTY = {'id': '', 'name': '', 'age': 0, 'city': '', 'about': ''}


def profile_card_edited(value) -> list[str]:
    """Отметки из колонки `profile_edited` — списком известных имён.

    Args:
        value: значение колонки: список, строка JSON или `None`.

    Returns:
        list: имена правленых полей в порядке объявления `PROFILE_CARD_FIELDS`.
        Незнакомые имена и испорченный JSON дают пустой список.

    ⚠ Чужие имена отсеиваются, а не переносятся как есть: строка, попавшая в
    колонку мимо нашей двери, иначе отняла бы у сайта поле, которого в карточке
    нет вовсе.

    ⚠ Порядок — наш, а не как лежало: он уезжает в ответ ручки и в окно, и
    «то же самое, но в другом порядке» выглядело бы изменением.
    """
    said = {str(one) for one in json_column_list(value)}

    return [name for name in PROFILE_CARD_FIELDS if name in said]


def profile_card_keep(fresh: dict, edited) -> dict:
    """Пришедшее с сайта без полей, которые правил человек.

    Args:
        fresh: карточка или её часть, как её отдал сайт.
        edited: значение колонки `profile_edited` — список, строка JSON или `None`.

    Returns:
        dict: копия `fresh` без правленых ключей. Отметок нет — копия целиком.
    """
    skip = set(profile_card_edited(edited))

    return {name: value for name, value in (fresh or {}).items() if name not in skip}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dating/profile_card.py tests/test_profile_card_edit.py
git commit -m "Анкета: правило «правленое руками сайт не перезаписывает» — одной функцией

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Запись правки — единственная дверь

**Files:**
- Modify: `src/dating/profile_card.py` (дописать)
- Test: `tests/test_profile_card_edit.py` (дописать)

**Interfaces:**
- Consumes: `PROFILE_CARD_FIELDS`, `PROFILE_CARD_EMPTY`, `profile_card_edited` (задача 2).
- Produces:
  - `async profile_card_save(profile_id: int, values: dict) -> dict` — отдаёт
    `{'login': str, 'card': dict, 'edited': list[str]}`; `LookupError` — анкеты нет,
    `ValueError` с человеческими словами — почта или номер уже заняты на этом сайте.
  - `async profile_card_forget(profile_id: int, field: str) -> list[str]` — отдаёт
    оставшиеся отметки; `ValueError` — поля нет среди правимых, `LookupError` — анкеты нет.
  - `profile_card_why(error: Exception, login: str, uid: str) -> str` — причина словами.

- [ ] **Step 1: Write the failing test**

Допиши в `tests/test_profile_card_edit.py`:

```python
import uuid

from src.dating.profile_card import (profile_card_forget, profile_card_save,
                                     profile_card_why)

STAND_SITE = 999_343


def _login() -> str:
    """Логин тестовой анкеты. ⚠ Домен `@example.com` — метка сюиты, по ней
    уборка находит мусор, если тест оборвётся на полуслове."""
    return f'card-{uuid.uuid4().hex[:10]}@example.com'


async def _row(profile_id: int) -> dict:
    """Строка анкеты как она лежит: нужны обе записи номера и отметки."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT login, profile_id, profile, profile_edited '
                         '  FROM profile WHERE id = %s', (profile_id,))

        return dict(await db.fetchone() or {})


class TestSave:
    """Запись правки: шесть полей, две колонки, одна карточка."""

    async def test_all_six_fields_are_stored(self, girl):
        login = _login()
        got = await profile_card_save(girl, {
            'login': login, 'id': '643694', 'name': 'Diana', 'age': 29,
            'city': 'Kyiv', 'about': 'люблю шахматы'})

        assert got['login'] == login
        assert got['card']['name'] == 'Diana'
        assert got['card']['age'] == 29
        assert got['card']['city'] == 'Kyiv'
        assert got['card']['about'] == 'люблю шахматы'

    async def test_uid_reaches_both_writings(self, girl):
        """⚠⚠ Главный сторож задачи: номер уезжает **и в колонку, и в ключ**.

        Читают их разные места — ключ фактов агента берёт колонку
        (`agent_site_uid`), сведение переписки из панели и ссылка на сайт берут
        ключ карточки. Разойдись они, половина кода увидит номер, а половина
        пустоту, и ни одна проверка не упадёт.
        """
        from src.dating.json_column import json_column_load

        await profile_card_save(girl, {'login': _login(), 'id': '643694'})
        row = await _row(girl)

        assert row['profile_id'] == '643694', 'номер не лёг в колонку'
        assert json_column_load(row['profile']).get('id') == '643694', \
            'номер не лёг в ключ карточки'

    async def test_empty_uid_is_null_not_empty_string(self, girl):
        """⚠ Уникальный ключ `(site_id, profile_id)` считает пустые строки
        одинаковыми, а `NULL`-ы — разными: с `''` вторая анкета без номера на том
        же сайте не сохранилась бы вовсе."""
        await profile_card_save(girl, {'login': _login(), 'id': ''})

        assert (await _row(girl))['profile_id'] is None

    async def test_two_profiles_may_both_have_no_uid(self, girl_make):
        """Та же мысль, но проверенная тем, ради чего правило и стоит."""
        one, two = await girl_make(), await girl_make()

        await profile_card_save(one, {'login': _login(), 'id': ''})
        await profile_card_save(two, {'login': _login(), 'id': ''})

        assert (await _row(one))['profile_id'] is None
        assert (await _row(two))['profile_id'] is None

    async def test_changed_fields_are_marked(self, girl):
        await profile_card_save(girl, {'login': _login(), 'name': 'Diana',
                                       'about': 'текст'})

        assert set((await _row(girl))['profile_edited'] or []) == {'name', 'about'}

    async def test_unchanged_save_marks_nothing(self, girl):
        """⚠⚠ Метится изменённое, а не присланное.

        Иначе человек, открывший окно поглядеть и нажавший «Сохранить»,
        заморозил бы все пять полей разом — и сайт больше не обновил бы ни одного,
        без единого признака того, что произошло.
        """
        await profile_card_save(girl, {'login': _login(), 'name': 'Diana'})
        before = await _row(girl)

        # Второй раз — ровно то же, что лежит: так выглядит «открыл и сохранил».
        await profile_card_save(girl, {'login': before['login'], 'name': 'Diana',
                                       'age': 0, 'city': '', 'about': '', 'id': ''})

        assert profile_card_edited((await _row(girl))['profile_edited']) == ['name'], \
            'сохранение без изменений пометило лишние поля'

    async def test_clearing_is_a_change(self, girl):
        """⚠ Стирание — тоже правка: вписанное пустым значит «неправда, не знаем».

        Без отметки импорт вернул бы то, что человек только что убрал.
        """
        login = _login()
        await profile_card_save(girl, {'login': login, 'about': 'текст'})
        await profile_card_save(girl, {'login': login, 'about': ''})

        row = await _row(girl)
        assert 'about' in profile_card_edited(row['profile_edited'])

    async def test_duplicate_login_says_why(self, girl_make):
        one, two = await girl_make(), await girl_make()
        taken = _login()
        await profile_card_save(one, {'login': taken})

        with pytest.raises(ValueError) as refusal:
            await profile_card_save(two, {'login': taken})

        assert 'уже заведена' in str(refusal.value), str(refusal.value)

    async def test_duplicate_uid_says_which(self, girl_make):
        """⚠ Два уникальных ключа, и путать их нельзя: «почта занята» вместо
        «номер занят» отправляет человека править не то поле."""
        one, two = await girl_make(), await girl_make()
        await profile_card_save(one, {'login': _login(), 'id': '643694'})

        with pytest.raises(ValueError) as refusal:
            await profile_card_save(two, {'login': _login(), 'id': '643694'})

        assert '643694' in str(refusal.value)
        assert 'другой анкеты' in str(refusal.value), str(refusal.value)

    async def test_unknown_profile_is_lookup_error(self):
        with pytest.raises(LookupError):
            await profile_card_save(99_999_999, {'login': _login()})


class TestForget:
    """«Вернуть сайту»: отметка снимается, значение остаётся."""

    async def test_mark_goes_value_stays(self, girl):
        """⚠ Стереть значение значило бы выбросить то, что человек видел в поле.
        Обновит его следующий импорт, когда сайт отдаст своё."""
        from src.dating.json_column import json_column_load

        await profile_card_save(girl, {'login': _login(), 'about': 'мой текст'})

        left = await profile_card_forget(girl, 'about')

        row = await _row(girl)
        assert left == []
        assert row['profile_edited'] is None, 'пустые отметки обязаны лечь NULL'
        assert json_column_load(row['profile']).get('about') == 'мой текст'

    async def test_other_marks_survive(self, girl):
        await profile_card_save(girl, {'login': _login(), 'about': 'текст',
                                       'name': 'Diana'})

        assert await profile_card_forget(girl, 'about') == ['name']

    async def test_unknown_field_is_refused(self, girl):
        with pytest.raises(ValueError):
            await profile_card_forget(girl, 'password')


class TestWhy:
    """Причина словами, а не текстом драйвера."""

    def test_uid_key_is_recognised_before_the_generic_1062(self):
        """⚠ Порядок проверок важен: общий признак дубля (`1062`) стоит последним,
        иначе занятый номер доложился бы как занятая почта."""
        error = Exception("1062 (23000): Duplicate entry '343-643694' for key "
                          "'uk_dating_profile_site_uid'")

        assert 'другой анкеты' in profile_card_why(error, 'a@example.com', '643694')

    def test_login_key_is_recognised(self):
        error = Exception("1062 (23000): Duplicate entry '343-a@example.com' for key "
                          "'uk_dating_profile_site_login'")

        assert 'уже заведена' in profile_card_why(error, 'a@example.com', '')

    def test_anything_else_keeps_the_text(self):
        """Незнакомую беду не прячем: иначе причина пропадёт и в журнале."""
        assert 'что-то своё' in profile_card_why(Exception('что-то своё'), 'a', '')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: FAIL — `ImportError: cannot import name 'profile_card_save'`.

- [ ] **Step 3: Write minimal implementation**

Допиши в `src/dating/profile_card.py` (после существующих функций, импорты — к
верхним):

```python
import json

from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load

# Имена уникальных ключей таблицы. ⚠ Нужны **оба и порознь**: по ним причина
# отказа узнаётся точно, а «просто дубль» отправил бы человека править не то поле.
PROFILE_CARD_KEY_LOGIN = 'uk_dating_profile_site_login'
PROFILE_CARD_KEY_UID = 'uk_dating_profile_site_uid'


async def profile_card_save(profile_id: int, values: dict) -> dict:
    """Записать правку руками: карточка, две колонки и отметки одним заходом.

    Args:
        profile_id: наш номер анкеты.
        values: что пришло из окна. `login` — почта; остальные ключи — имена из
            `PROFILE_CARD_FIELDS`. Отсутствующий ключ значит «не трогать».

    Returns:
        dict: `login`, `card` и `edited` — что в итоге легло.

    Raises:
        LookupError: анкеты с таким номером нет.
        ValueError: почта или номер на сайте уже заняты — причина словами.

    ## ⚠⚠ Номер уезжает в оба написания

    Колонка `profile_id` и ключ `id` карточки держат одну и ту же величину, и
    читают их разные места: ключ фактов агента — колонку (`agent_site_uid`),
    сведение переписки из панели и ссылка на сайт — карточку. Пишем их **одним
    `UPDATE`**: разойдясь, они не уронят ни одной проверки — половина кода просто
    увидит номер, а половина пустоту.

    ## ⚠⚠ Метится изменённое, а не присланное

    Поле попадает в отметки, только если значение разошлось с тем, что лежит.
    Иначе человек, открывший окно поглядеть и нажавший «Сохранить», заморозил бы
    все пять полей разом — и сайт больше не обновил бы ни одного.

    ⚠ Стирание при этом — правка: вписанное пустым значит «неправда, не знаем», и
    без отметки импорт вернул бы убранное.

    ⚠ Совпало с тем, что лежит, — отметка **не ставится и не снимается**. Снять её
    можно только «вернуть сайту» (`profile_card_forget`): это решение человека, и
    угадывать его по совпадению значений нельзя — девушку могли переименовать в то
    же самое.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT login, profile, profile_edited FROM profile '
                         ' WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise LookupError(f'анкеты #{int(profile_id)} нет')

    before = json_column_load(row['profile'])
    card = dict(before)
    edited = set(profile_card_edited(row['profile_edited']))

    for field in PROFILE_CARD_FIELDS:
        if field not in values:
            continue

        if _same(field, before.get(field), values[field]):
            continue

        card[field] = values[field]
        edited.add(field)

    login = str(values.get('login') or '').strip() or str(row['login'] or '')
    # ⚠ Пусто ложится `NULL`, а не `''`: уникальный ключ считает пустые строки
    # одинаковыми, а `NULL`-ы — разными, и второй анкеты без номера не было бы.
    uid = str(card.get('id') or '').strip() or None
    marks = profile_card_edited(sorted(edited))

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE profile SET login = %s, profile_id = %s, profile = %s, '
                '       profile_edited = %s WHERE id = %s',
                (login, uid, json.dumps(card, ensure_ascii=False),
                 json.dumps(marks) if marks else None, int(profile_id)))
            await db.connection.commit()
    except Exception as error:
        raise ValueError(profile_card_why(error, login, str(uid or ''))) from error

    return {'login': login, 'card': card, 'edited': marks}


async def profile_card_forget(profile_id: int, field: str) -> list[str]:
    """Снять отметку с поля — «вернуть сайту».

    Args:
        profile_id: наш номер анкеты.
        field: имя поля карточки из `PROFILE_CARD_FIELDS`.

    Returns:
        list: отметки, которые остались.

    Raises:
        ValueError: такого поля среди правимых нет.
        LookupError: анкеты нет.

    ⚠ **Значение остаётся на месте.** Стереть его значило бы выбросить то, что
    человек видел в поле; обновит его следующий импорт, когда сайт отдаст своё.

    ⚠ Отметка нужна человеку именно снимаемой: без этого правило «сайт правленое
    не трогает» становится ловушкой — поправивший имя однажды отрезал бы его от
    сайта навсегда и узнал об этом только тем, что имя больше не обновляется.
    """
    if field not in PROFILE_CARD_FIELDS:
        raise ValueError(f'поля «{field}» в карточке анкеты нет')

    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile_edited FROM profile WHERE id = %s',
                         (int(profile_id),))
        row = await db.fetchone()
        if not row:
            raise LookupError(f'анкеты #{int(profile_id)} нет')

        left = [one for one in profile_card_edited(row['profile_edited'])
                if one != field]
        # ⚠ Пустые отметки ложатся `NULL`, а не `'[]'`: «отметок нет» должно иметь
        # одно написание — то же правило, что в миграции колонки.
        await db.execute('UPDATE profile SET profile_edited = %s WHERE id = %s',
                         (json.dumps(left) if left else None, int(profile_id)))
        await db.connection.commit()

    return left


def profile_card_why(error: Exception, login: str, uid: str) -> str:
    """Причина отказа словами вместо текста драйвера.

    Args:
        error: исключение драйвера.
        login: почта, которую пытались записать.
        uid: номер на сайте, который пытались записать.

    Returns:
        str: предложение для человека.

    ⚠ **Ключи различаются порознь, и общий признак дубля стоит последним.** У
    таблицы два уникальных ключа, и «почта занята» вместо «номер занят» отправило
    бы человека править не то поле.
    """
    text = str(error)

    if PROFILE_CARD_KEY_UID in text:
        return f'номер {uid} на этом сайте уже у другой анкеты'

    if PROFILE_CARD_KEY_LOGIN in text or '1062' in text or 'Duplicate entry' in text:
        return f'учётка «{login}» на этом сайте уже заведена'

    return f'не удалось сохранить анкету: {text}'


def _same(field: str, before, after) -> bool:
    """Совпало ли значение с тем, что лежит.

    ⚠ Отсутствующий ключ приравнивается к «пусто» своего вида
    (`PROFILE_CARD_EMPTY`): без этого присланный возраст `0` там, где ключа нет
    вовсе, читался бы как правка.
    """
    empty = PROFILE_CARD_EMPTY[field]
    was = empty if before is None else before
    now = empty if after is None else after

    return str(was) == str(now)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: PASS (все, 23 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dating/profile_card.py tests/test_profile_card_edit.py
git commit -m "Анкета: единственная дверь записи правки — карточка, две колонки и отметки

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Ручки правки и снятия отметки

**Files:**
- Modify: `src/uvicorn/routers/dating/profile_.py` (модель и две ручки)
- Modify: `src/uvicorn/routers/dating/profile_view.py:313-358` (в ответ карточки — `edited`)
- Modify: `.claude/docs/api.md` (новый §3.14 после §3.13, перед `## 4. Сайты`)
- Test: `tests/test_profile_card_edit.py` (дописать)

**Interfaces:**
- Consumes: `profile_card_save`, `profile_card_forget` (задача 3); `profile_row` из
  `src/uvicorn/routers/dating/profile_.py`.
- Produces:
  - `DatingProfileCard` — pydantic-модель тела правки.
  - `async profile_card_put(profile_id: int, data: DatingProfileCard, user=None)` —
    отдаёт `{'result': {'login': str, 'card': dict, 'edited': list[str]}}`.
  - `async profile_card_drop(profile_id: int, field: str, user=None)` — отдаёт
    `{'result': {'edited': list[str]}}`.
  - `GET /api/dating/profile/{id}/card` в ответе получает ключ `edited: list[str]`.

- [ ] **Step 1: Write the failing test**

Допиши в `tests/test_profile_card_edit.py`:

```python
from fastapi import HTTPException

from src.uvicorn.routers.dating.profile_ import (DatingProfileCard,
                                                 profile_card_drop,
                                                 profile_card_put)
from src.uvicorn.routers.dating.profile_view import profile_view_card


class TestEndpoint:
    """Ручка правки: что принимает, чем отказывает, что отдаёт."""

    async def test_it_stores_and_answers(self, girl):
        login = _login()
        answer = await profile_card_put(girl, DatingProfileCard(
            login=login, profile_id='643694', name='Diana', age=29,
            city='Kyiv', about='люблю шахматы'), user=None)
        got = answer['result']

        assert got['login'] == login
        assert got['card']['id'] == '643694'
        assert set(got['edited']) == {'id', 'name', 'age', 'city', 'about'}

    async def test_empty_login_is_refused(self):
        """⚠ Пустая почта — отказ, а не «не трогать»: колонка `NOT NULL`, и она же
        половина ключа входа на сайт. Пустыми законно бывают пять полей карточки,
        почта — нет."""
        with pytest.raises(Exception):
            DatingProfileCard(login='   ')

    async def test_long_about_is_refused(self):
        """Границы — те же постоянные, что у импорта: `IMPORT_ABOUT_MAX = 2000`."""
        with pytest.raises(Exception):
            DatingProfileCard(login=_login(), about='я' * 2001)

    async def test_age_out_of_range_is_refused(self):
        with pytest.raises(Exception):
            DatingProfileCard(login=_login(), age=121)

    async def test_zero_age_is_fine(self):
        """⚠ Ноль — законное «не знаем», а не ошибка: карточка показывает возраст
        только непустым."""
        assert DatingProfileCard(login=_login(), age=0).age == 0

    async def test_unknown_profile_is_404(self):
        with pytest.raises(HTTPException) as refusal:
            await profile_card_put(99_999_999, DatingProfileCard(login=_login()),
                                   user=None)

        assert refusal.value.status_code == 404

    async def test_taken_login_is_409(self, girl_make):
        one, two = await girl_make(), await girl_make()
        taken = _login()
        await profile_card_put(one, DatingProfileCard(login=taken), user=None)

        with pytest.raises(HTTPException) as refusal:
            await profile_card_put(two, DatingProfileCard(login=taken), user=None)

        assert refusal.value.status_code == 409
        assert 'уже заведена' in refusal.value.detail

    async def test_taken_uid_is_409(self, girl_make):
        one, two = await girl_make(), await girl_make()
        await profile_card_put(one, DatingProfileCard(login=_login(),
                                                      profile_id='643694'), user=None)

        with pytest.raises(HTTPException) as refusal:
            await profile_card_put(two, DatingProfileCard(login=_login(),
                                                          profile_id='643694'),
                                   user=None)

        assert refusal.value.status_code == 409
        assert 'другой анкеты' in refusal.value.detail

    async def test_everything_comes_back_through_the_card(self, rhapsody, girl):
        """Круг замкнут: что записали ручкой, то и отдаёт карточка странице.

        ⚠ Сторож именно на `GET /card`, а не на ответ самой правки: карточку
        страница читает при каждом открытии анкеты, и поле, потерянное по дороге
        (`response_model` молча выбрасывает всё, чего нет в схеме), всплыло бы
        `undefined` только у человека. Так уже выходило с `age` и `proxy_ip`.
        """
        login = _login()
        await profile_card_put(girl, DatingProfileCard(
            login=login, profile_id='643694', name='Diana', age=29,
            city='Kyiv', about='люблю шахматы'), user=None)

        card = (await profile_view_card(girl, user=None))['result']

        assert card['login'] == login
        assert card['profile']['id'] == '643694'
        assert card['profile']['name'] == 'Diana'
        assert card['profile']['age'] == 29
        assert card['profile']['city'] == 'Kyiv'
        assert card['profile']['about'] == 'люблю шахматы'

    async def test_card_handle_gives_edited(self, rhapsody, girl):
        """⚠ Без `edited` в ответе карточки окно не нарисовало бы отметку
        «правлено руками · вернуть сайту» — то есть человек не узнал бы, какие
        поля сайт больше не обновляет."""
        await profile_card_put(girl, DatingProfileCard(login=_login(), about='текст'),
                               user=None)
        card = (await profile_view_card(girl, user=None))['result']

        assert card['edited'] == ['about']

    async def test_fresh_card_gives_empty_edited(self, rhapsody, girl):
        """Поле есть всегда: `undefined` на странице читался бы как «нет отметок»
        случайно, а не по правилу."""
        card = (await profile_view_card(girl, user=None))['result']

        assert card['edited'] == []

    async def test_drop_handle_removes_the_mark(self, girl):
        await profile_card_put(girl, DatingProfileCard(login=_login(), about='текст',
                                                       name='Diana'), user=None)

        answer = await profile_card_drop(girl, 'about', user=None)

        assert answer['result']['edited'] == ['name']

    async def test_drop_unknown_field_is_422(self, girl):
        with pytest.raises(HTTPException) as refusal:
            await profile_card_drop(girl, 'password', user=None)

        assert refusal.value.status_code == 422

    async def test_drop_unknown_profile_is_404(self):
        with pytest.raises(HTTPException) as refusal:
            await profile_card_drop(99_999_999, 'about', user=None)

        assert refusal.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: FAIL — `ImportError: cannot import name 'DatingProfileCard'`.

- [ ] **Step 3: Add the model and the two handles**

В `src/uvicorn/routers/dating/profile_.py` допиши импорт к верхним:

```python
from src.dating.import_ import (IMPORT_ABOUT_MAX, IMPORT_CITY_MAX,
                                IMPORT_LOGIN_MAX, IMPORT_NAME_MAX)
from src.dating.profile_card import (PROFILE_CARD_FIELDS, profile_card_forget,
                                     profile_card_save)
```

Модель — рядом с `DatingProfileSave`:

```python
class DatingProfileCard(BaseModel):
    """Тело правки персональных данных анкеты.

    ⚠ Границы длин — **постоянные импорта**, а не числа рядом: те же колонки и те
    же поля карточки, и два набора границ разошлись бы на первой правке схемы.

    ⚠ Номер анкеты приезжает под именем `profile_id` (так зовётся колонка), а в
    карточку ложится ключом `id`. Перевод делает ручка одной строкой: два имени
    одной величины внутри домена развели бы её по смыслу.
    """

    login: str = Field(..., max_length=IMPORT_LOGIN_MAX,
                       description='Почта, которой анкета входит на сайт')
    profile_id: str = Field('', max_length=32,
                            description='Номер анкеты на сайте; пусто — не знаем')
    name: str = Field('', max_length=IMPORT_NAME_MAX)
    age: int = Field(0, ge=0, le=120, description='0 — «не знаем», а не ошибка')
    city: str = Field('', max_length=IMPORT_CITY_MAX)
    about: str = Field('', max_length=IMPORT_ABOUT_MAX)

    @field_validator('profile_id', 'name', 'city', 'about')
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()

    @field_validator('login')
    @classmethod
    def _login_needed(cls, value: str) -> str:
        """⚠ Своим проверяющим, а не `min_length=1`: тот считает пробелы знаками, и
        почта из трёх пробелов прошла бы границу, а потом легла пустой."""
        if not value.strip():
            raise ValueError('почта у анкеты обязательна: ею она входит на сайт')

        return value.strip()
```

Ручки — после `profile_update`, до `profile_delete`:

```python
@router.put('/{profile_id}/card', include_in_schema=False)
async def profile_card_put(profile_id: int, data: DatingProfileCard,
                           user=Depends(auth_required)):
    """Правка персональных данных анкеты: шесть полей одним нажатием.

    Симметрична `GET /{profile_id}/card`, которая эту же карточку странице и
    отдаёт.

    ⚠ **Одной ручкой на шесть полей, а не шестью по одному.** Выключатели анкеты
    устроены наоборот (`/enabled`, `/mail-photo`, `/password`), и правильно: там
    щелчок и есть всё действие. Здесь человек правит шесть полей и нажимает
    «Сохранить» один раз, и шесть запросов на одно нажатие оставили бы
    половинчатое состояние при отказе второго.

    ⚠ Отказ дубля — `409`, как у заведения: это не ошибка запроса, а занятое имя.

    Raises:
        HTTPException: 404 — анкеты нет; 409 — почта или номер заняты на этом
            сайте; 422 — границы полей (их считает сама модель).
    """
    await profile_row(profile_id)

    try:
        got = await profile_card_save(profile_id, {
            'login': data.login, 'id': data.profile_id, 'name': data.name,
            'age': data.age, 'city': data.city, 'about': data.about})
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err

    return {'result': got}


@router.delete('/{profile_id}/card/{field}', include_in_schema=False)
async def profile_card_drop(profile_id: int, field: str,
                            user=Depends(auth_required)):
    """«Вернуть сайту»: снять отметку «правлено руками» с одного поля.

    Значение остаётся прежним — обновит его следующий импорт.

    ⚠ Имя поля сверяется со списком правимых, а не принимается на веру: оно
    уезжает в колонку, и чужое имя лежало бы там, ничего не защищая.

    Raises:
        HTTPException: 404 — анкеты нет; 422 — такого поля среди правимых нет.
    """
    await profile_row(profile_id)

    if field not in PROFILE_CARD_FIELDS:
        raise HTTPException(status_code=422,
                            detail=f'поля «{field}» в карточке анкеты нет')

    try:
        left = await profile_card_forget(profile_id, field)
    except LookupError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err

    return {'result': {'edited': left}}
```

- [ ] **Step 4: Add `edited` to the card answer**

В `src/uvicorn/routers/dating/profile_view.py` допиши импорт к верхним:

```python
from src.dating.profile_card import profile_card_edited
```

В `profile_view_card`, рядом со сборкой `card` (строка ~307), добавь:

```python
    card = json_column_load(profile.get('profile'))
    # Какие поля человек правил руками — окну правки: у них стоит отметка
    # «правлено руками · вернуть сайту».
    #
    # ⚠ Поле отдаётся **всегда**, пустым списком в обычном случае: `undefined` на
    # странице читался бы как «отметок нет» случайно, а не по правилу. Та же
    # грабля, что уже была с `age` и `proxy_ip` — `response_model` выбрасывал их
    # молча, и страница получала `undefined`.
    edited = profile_card_edited(profile.get('profile_edited'))
```

и в возвращаемый словарь, сразу после `'profile': card`:

```python
                       'edited': edited,
```

⚠ Запроса править не надо: `profile_view_card` берёт строку через `profile_row` →
`DatingProfileRepository().find` → `SELECT *` (`py_modules/mysql_/repository/abstract_repository.py:15`),
и новая колонка приезжает сама. Это **не** общее свойство: список анкет
(`profile_list`) и шесть запросов, собирающих словарь анкеты для кругов,
перечисляют колонки поимённо — вот почему отметки в задаче 5 читаются своим
`SELECT` по месту, а не приезжают доводом.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: PASS (все, 37 passed).

Run: `pytest -m profile -q`
Expected: PASS — ничего из соседнего не покраснело.

- [ ] **Step 6: Document the handle in `api.md`**

В `.claude/docs/api.md` вставь новый раздел **перед** строкой `## 4. Сайты — `/api/site`**
(она была строкой 5363 до правок):

```markdown
## 3.14 Персональные данные анкеты — `PUT /api/dating/profile/{id}/card`

Окно по иконке ✎ в шапке карточки на вкладке «Общая информация». Шесть полей:
почта, номер анкеты на сайте, имя, возраст, город, описание.

| Ручка | Что делает |
|-------|------------|
| `PUT /api/dating/profile/{id}/card` | записывает шесть полей; отдаёт `{login, card, edited}` |
| `DELETE /api/dating/profile/{id}/card/{field}` | снимает отметку «правлено руками» с поля |

### ⚠⚠ Шесть полей лежат в трёх местах

| Поле | Где лежит |
|------|-----------|
| почта | колонка `login`, уникальна парой с сайтом |
| номер на сайте | колонка `profile_id` **и** ключ `id` карточки |
| имя, возраст, город, описание | ключи карточки `profile` (JSON) |

Номер записан дважды потому, что читают его разные места: ключ фактов агента
берёт колонку (`agent_site_uid`), сведение переписки из панели и ссылка на анкету
— ключ карточки. Ручка пишет **оба одним `UPDATE`**: разойдясь, они не уронят ни
одной проверки — половина кода просто увидит номер, а половина пустоту.

⚠ Пусто в номере ложится `NULL`, а не `''`: уникальный ключ считает пустые строки
одинаковыми, а `NULL`-ы — разными, и второй анкеты без номера не завелось бы.

### ⚠⚠ Правленое руками сайт больше не перезаписывает

Четыре поля карточки пишет ещё и сайт — импорт анкет (`IMPORT_CARD_FIELDS`) и
чтение страницы кабинета (`profile_page`). Правленое человеком они обходят по
колонке `profile_edited`, и правило это — **одна функция** `profile_card_keep`
(`src/dating/profile_card.py`) на оба писателя: своя копия в каждом разошлась бы
на первой правке, и одна половина системы уважала бы ручную правку, другая
затирала.

⚠ **Метится изменённое, а не присланное.** Иначе человек, открывший окно
поглядеть и нажавший «Сохранить», заморозил бы все пять полей разом — и сайт
больше не обновил бы ни одного, без единого признака того, что произошло.
Стирание при этом правка: вписанное пустым значит «неправда, не знаем».

⚠ Отметка **снимается** — ссылкой «вернуть сайту» у поля. Без этого правило
становится ловушкой: поправивший имя однажды отрезал бы его от сайта навсегда.
Значение при снятии остаётся прежним; обновит его следующий импорт.

### ⚠ Номер и имя — две половины ключа фактов, и окно об этом говорит

`agent_vars` собирает ключ как `%girl_id%_%girl_name%`, где `girl_id` — номер на
сайте, а `girl_name` — имя латиницей строчными. Сменив **любую** из половин,
человек делает девушку для агента незнакомой: прежние факты остаются за старым
ключом. Поэтому под обоими полями стоит постоянное пояснение, а при изменённом
значении оно называет переход («643694 → 999111»).

### Отказы

| Код | Когда |
|-----|-------|
| 404 | анкеты с таким номером нет |
| 409 | почта занята на этом сайте, либо номер уже у другой анкеты — причина словами и порознь |
| 422 | границы полей; пустая почта; незнакомое имя поля при снятии отметки |

⚠ Два уникальных ключа различаются **порознь** (`profile_card_why`): «почта
занята» вместо «номер занят» отправило бы человека править не то поле.

Границы — те же постоянные, что у импорта: `IMPORT_LOGIN_MAX` 190,
`IMPORT_NAME_MAX` 190, `IMPORT_CITY_MAX` 190, `IMPORT_ABOUT_MAX` 2000; номер — 32
(ширина колонки). Возраст 0…120, где **0 — законное «не знаем»**.

⚠ Почта обязательна: колонка `NOT NULL`, и она же половина ключа входа на сайт.
```

- [ ] **Step 7: Commit**

```bash
git add src/uvicorn/routers/dating/profile_.py src/uvicorn/routers/dating/profile_view.py .claude/docs/api.md tests/test_profile_card_edit.py
git commit -m "Анкета: ручки правки персональных данных и снятия отметки

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Оба писателя карточки уважают ручную правку

**Files:**
- Modify: `src/dating/import_.py:137-182` (`import_plan`)
- Modify: `src/uvicorn/routers/dating/import_.py:260-272` (`_profiles_known` — отдать отметки)
- Modify: `src/dating/profile_page.py:40-131` (`_profile_save` сливает, а не заменяет)
- Test: `tests/test_profile_card_edit.py` (дописать)

**Interfaces:**
- Consumes: `profile_card_keep`, `profile_card_edited` (задача 2); колонка
  `profile_edited` (задача 1).
- Produces: `import_plan(row, found, base)` принимает в `found` ключ `edited`
  (значение колонки `profile_edited`, разобранное или строкой); `_profile_save`
  возвращает `dict` — карточку, как она легла.

- [ ] **Step 1: Write the failing test**

Допиши в `tests/test_profile_card_edit.py`:

```python
from src.dating.import_ import import_plan


class TestImportRespectsHand:
    """⚠ Импорт — первый из двух писателей со стороны сайта."""

    def test_edited_field_is_not_overwritten(self):
        """Правленое описание переживает импорт, остальное обновляется."""
        found = {'id': 1, 'login': 'a@example.com',
                 'card': {'id': '77', 'name': 'Olga', 'about': 'мой текст'},
                 'edited': ['about']}
        row = {'site_uid': '77', 'name': 'Olga Petrova', 'about': 'с сайта',
               'city': 'Kyiv'}

        plan = import_plan(row, found, 'https://site.example')

        assert plan['card']['about'] == 'мой текст', 'импорт затёр ручную правку'
        assert plan['card']['name'] == 'Olga Petrova', 'неправленое обновляться должно'

    def test_edited_field_is_not_reported_as_changed(self):
        """⚠ Иначе импорт докладывал бы «Обновлена Olga: about», ничего не обновив."""
        found = {'id': 1, 'login': 'a@example.com',
                 'card': {'id': '77', 'name': 'Olga', 'about': 'мой текст'},
                 'edited': ['about']}
        row = {'site_uid': '77', 'name': 'Olga', 'about': 'с сайта'}

        plan = import_plan(row, found, 'https://site.example')

        assert 'about' not in plan['changed']
        assert plan['action'] == 'same', 'единственная «перемена» была правленой'

    def test_edited_uid_survives_too(self):
        """⚠ Номер в `IMPORT_CARD_FIELDS` не входит и в `changed` не попадал
        никогда — а в карточку импорт его всё равно кладёт. Отметка защищает и его."""
        found = {'id': 1, 'login': 'a@example.com',
                 'card': {'id': '643694', 'name': 'Diana'}, 'edited': ['id']}
        row = {'site_uid': '999111', 'name': 'Diana'}

        plan = import_plan(row, found, 'https://site.example')

        assert plan['card']['id'] == '643694'

    def test_no_marks_changes_nothing(self):
        """Анкета без отметок обновляется ровно как прежде."""
        found = {'id': 1, 'login': 'a@example.com',
                 'card': {'id': '77', 'about': 'старое'}, 'edited': None}
        row = {'site_uid': '77', 'about': 'с сайта'}

        plan = import_plan(row, found, 'https://site.example')

        assert plan['card']['about'] == 'с сайта'
        assert 'about' in plan['changed']


class TestCardReadRespectsHand:
    """⚠⚠ Второй писатель, и без него защита неполная.

    Чтение анкеты со страницы кабинета заходит только при пустой карточке, а
    признак пустоты — **имя** (`login._card_empty`). То есть у анкеты, где человек
    впишет описание, но не впишет имя, следующий вход затёр бы описание молча.
    Такая анкета в базе есть — заведённая импортом без карточки, логин равен
    номеру.
    """

    async def test_stored_card_keeps_edited_field(self, girl):
        from src.dating.json_column import json_column_load
        from src.dating.profile_page import _profile_save

        await profile_card_save(girl, {'login': _login(), 'about': 'мой текст'})

        stored = await _profile_save(girl, {'id': '77', 'name': 'Olga',
                                            'about': 'со страницы', 'city': 'Kyiv',
                                            'age': 30, 'photo': ''})

        row = await _row(girl)
        assert json_column_load(row['profile'])['about'] == 'мой текст', \
            'чтение страницы затёрло ручную правку'
        assert json_column_load(row['profile'])['name'] == 'Olga'
        assert stored['about'] == 'мой текст', \
            'вернулась карточка сценария, а не та, что легла'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_card_edit.py -v -k "Import or CardRead"`
Expected: FAIL — `AssertionError: импорт затёр ручную правку` и
`AssertionError: чтение страницы затёрло ручную правку`.

- [ ] **Step 3: Teach `import_plan` the marks**

В `src/dating/import_.py` допиши импорт к верхним:

```python
from src.dating.profile_card import profile_card_edited, profile_card_keep
```

Замени в `import_plan` блок от `before = dict(...)` до `return` на:

```python
    # Сравниваем с тем, что уже лежит в кэше анкеты, а не с колонками учётки:
    # именно `profile` импорт и переписывает.
    before = dict(found.get('card') or {})
    # ⚠⚠ Правленое руками импорт не трогает — ни в карточке, ни в отчёте. Правило
    # одно на два писателя (`profile_card.py`): второй — чтение анкеты со
    # страницы кабинета. Своя копия здесь разошлась бы с ним на первой правке.
    edited = set(profile_card_edited(found.get('edited')))
    changed = [name for name in IMPORT_CARD_FIELDS
               if name not in edited and _changed(before.get(name), card.get(name))]

    # **Поверх кладём только непустое.** Иначе импорт без карточек стирал бы город
    # и описание, дочитанные импортом с карточками: список их не отдаёт вовсе, и
    # в `card` они приходят пустыми. Заметно это не сразу — пустое не считается
    # изменением, и `same` такую анкету не тронет; но стоит смениться имени, и тот
    # же прогон унёс бы вместе с ним всё, чего не было в списке.
    fresh = {name: value for name, value in card.items() if value not in (None, '', 0)}
    # ⚠ Номер (`id`) в `IMPORT_CARD_FIELDS` не входит и в `changed` не попадал
    # никогда — а в карточку импорт его всё равно кладёт. Отметка защищает и его:
    # отсев идёт по `fresh`, а не по `changed`.
    fresh = profile_card_keep(fresh, sorted(edited))

    return {'action': 'update' if changed else 'same',
            'login': str(found.get('login') or login),
            'card': {**before, **fresh},
            'changed': changed}
```

Допиши в докстринг `import_plan`, в `Args`, строку про новый ключ:

```
        found: наша анкета, если нашлась: `id`, `login`, `card` и `edited` —
            значение колонки `profile_edited`. ⚠ Без `edited` правленое руками
            было бы затёрто: ключ обязателен у всякого, кто зовёт.
```

- [ ] **Step 4: Teach the import router to pass the marks**

В `src/uvicorn/routers/dating/import_.py`, в функции `_profiles_known` (строка ~260):
добавь `profile_edited` в список колонок запроса и в собираемую запись:

```python
        card = json_column_load(row.get('profile'))
        item = {'id': int(row['id']), 'login': str(row.get('login') or ''),
                'card': card,
                # ⚠ Отметки «правлено руками» — `import_plan`, иначе он затрёт
                # правку человека. Колонка, а не ключ карточки: карточку
                # перезаписывают целиком.
                'edited': row.get('profile_edited')}
```

- [ ] **Step 5: Teach the card reader to merge**

В `src/dating/profile_page.py` допиши импорт к верхним:

```python
from src.dating.json_column import json_column_load
from src.dating.profile_card import profile_card_keep
```

Замени `_profile_save` целиком:

```python
async def _profile_save(profile_id: int, profile: dict) -> dict:
    """Кладёт анкету, не затирая правленого руками. Не роняет вход.

    Args:
        profile_id: наш номер анкеты.
        profile: карточка, собранная из `extract` сценария.

    Returns:
        dict: карточка, как она **легла** — она же идёт в журнал и наверх. Пустой
        словарь значит «записать не удалось»; вход из-за этого не падает: сессия
        уже открыта, и терять её незачем.

    ## ⚠⚠ Почему слияние, а не замена

    Прежде здесь стоял один `UPDATE profile SET profile = %s` — то есть замена
    карточки целиком. С появлением правки руками это стало затиранием: человек
    вписывает описание анкете, у которой сайт его не отдал, и следующий вход
    уносит вписанное.

    ⚠ Случай не редкий, а ровно тот, ради которого правка и заводилась: чтение
    карточки заходит **только при пустой карточке**, а признак пустоты — **имя**
    (`login._card_empty`). Анкета, где впишут описание, но не впишут имя, попадает
    сюда при каждом входе.

    ## ⚠ Отметки читаются здесь, а не приезжают доводом

    Словарь анкеты собирают шесть разных запросов, и половина перечисляет колонки
    поимённо (`agent_site_uid` объясняет, чем это уже стоило). Спроси мы отметки у
    вызывающего — забытая колонка в одном из запросов тихо вернула бы затирание.
    Лишний `SELECT` случается не чаще раза на вход.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT profile, profile_edited FROM profile '
                             ' WHERE id = %s', (int(profile_id),))
            row = dict(await db.fetchone() or {})
            before = json_column_load(row.get('profile'))
            card = {**before, **profile_card_keep(profile, row.get('profile_edited'))}

            await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                             (json.dumps(card, ensure_ascii=False), int(profile_id)))
            await db.connection.commit()

        return card
    except Exception as e:
        logger_info(f'[dating] анкета не записалась: {e}')

        return {}
```

И в `profile_page_read` замени строку записи на использование итога:

```python
    # ⚠ Наверх уходит карточка, **как она легла**, а не как её собрал сценарий:
    # правленые руками поля в ней остались прежними, и журнал обязан назвать то,
    # что теперь лежит в базе, а не то, что отдал сайт.
    stored = await _profile_save(int(profile['id']), card)

    return {'ok': True, 'profile': stored or card, 'error': ''}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_profile_card_edit.py -v`
Expected: PASS (все, 42 passed).

Run: `pytest -m profile -q && pytest -m chat -q`
Expected: PASS — импорт и вход никого не задели.

- [ ] **Step 7: Commit**

```bash
git add src/dating/import_.py src/uvicorn/routers/dating/import_.py src/dating/profile_page.py tests/test_profile_card_edit.py
git commit -m "Анкета: импорт и чтение карточки обходят правленое руками

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Окно на странице

**Files:**
- Modify: `data/uvicorn/public/css/dating_page.css:849-882` (подложка и каркас окна — общими именами)
- Modify: `data/uvicorn/template/admin/dating/index.html` (иконка в шапке карточки,
  окно, привязка; `galleryModal` — на общие классы)
- Modify: `tests/test_gallery_page.py:93` (список классов в CSS)
- Modify: `CLAUDE.md` (строка в Development Patterns)
- Test: `tests/test_profile_card_page.py` (создать)

**Interfaces:**
- Consumes: `PUT /api/dating/profile/{id}/card`, `DELETE /api/dating/profile/{id}/card/{field}`,
  ключ `edited` в ответе `GET /{id}/card` (задача 4).
- Produces: `.modal-back`, `.modal-box`, `.modal-head`, `.modal-foot`, `.modal-field`,
  `.modal-label`, `.modal-hint`, `.modal-edited`, `.card-edit` в `dating_page.css`;
  функция страницы `cardEditModal()`.

- [ ] **Step 1: Write the failing test**

Создай `tests/test_profile_card_page.py`:

```python
"""Окно правки персональных данных: иконка, поля, предупреждения, привязка.

## ⚠⚠ Читается файл шаблона, а не ответ панели

Тот же довод, что в `test_gallery_page.py`: поднятая панель бежит ту ветку, из
которой её запустили, и по HTTP этот тест краснел бы у всякого, кто проверяет
ветку, не перезапустив панель. Проверять надо то, что **ветка поставляет**, а это
файл на диске. Ошибки рендера ловит `test_pages_open`.

## ⚠ Нарисованное поле без привязки тише сломанного теста

Иконка, ни к чему не привязанная, выглядит рабочей и молчит. Поэтому проверяется и
разметка, и то, что окно зовёт ручку, и что после удачи перечитываются **оба**
списка — строка списка и карточка.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.page

PAGE_FILE = Path('data/uvicorn/template/admin/dating/index.html')
CSS_FILE = Path('data/uvicorn/public/css/dating_page.css')


@pytest.fixture(scope='module')
def page() -> str:
    assert PAGE_FILE.exists(), f'шаблона нет: {PAGE_FILE}'

    return PAGE_FILE.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def css() -> str:
    return CSS_FILE.read_text(encoding='utf-8')


def test_card_head_has_the_edit_icon(page):
    """Иконка стоит в шапке карточки — там, где показано правимое."""
    assert 'id="cardEditBtn"' in page, 'иконки правки в шапке карточки нет'
    assert 'cardEditModal()' in page, 'иконка ни к чему не привязана'


def test_window_has_all_six_fields(page):
    """Шесть полей, и ни одним меньше: окно — единственный вход к ним."""
    for name in ('cardLogin', 'cardUid', 'cardName', 'cardAge', 'cardCity',
                 'cardAbout'):
        assert f'id="{name}"' in page, f'поля {name} в окне нет'


def test_window_calls_the_handle(page):
    """Ручка та, и тело несёт имена, которые она принимает."""
    assert '/card`, send)' in page or "/card`, send)" in page, \
        'окно не зовёт PUT …/card'
    for key in ('login:', 'profile_id:', 'name:', 'age:', 'city:', 'about:'):
        assert key in page, f'в теле запроса нет {key}'


def test_success_reloads_both_lists(page):
    """⚠ Перечитываются **оба**: правка меняет строку списка (почта, имя,
    возраст, номер) не меньше, чем карточку, а список приходит своей ручкой."""
    keep = page[page.index('cardEditKeep'):]
    assert 'profilesLoad()' in keep, 'список не перечитывается — строка устареет'
    assert 'profileLoad()' in keep, 'карточка не перечитывается'


def test_key_warning_names_both_halves(page):
    """⚠⚠ Предупреждение про ключ фактов стоит под **двумя** полями.

    `girl_name` — имя латиницей строчными, вторая половина того же ключа
    `%girl_id%_%girl_name%`. Предупреди мы только про номер, человек поправил бы
    регистр имени и получил то же последствие, не узнав о нём.
    """
    assert 'id="cardUidWhy"' in page, 'под номером пояснения нет'
    assert 'id="cardNameWhy"' in page, 'под именем пояснения нет'
    assert 'незнакомой' in page, 'пояснение не говорит, что произойдёт'


def test_forget_link_is_wired(page):
    """Отметка снимаемая: ссылка есть и зовёт свою ручку."""
    assert 'data-forget=' in page, 'ссылки «вернуть сайту» нет'
    assert 'deleteData(' in page and '/card/' in page, 'ссылка ни к чему не ведёт'


def test_backdrop_is_shared_not_copied(css, page):
    """⚠ Подложка одна на все окна раздела, и зовётся общим именем.

    Класс `gallery-view` у окна с почтой и описанием читался бы как ошибка, а
    вторая такая же подложка рядом разъехалась бы с первой на первой правке фона.
    """
    assert '.modal-back {' in css, 'общей подложки в CSS нет'
    assert '.modal-box {' in css, 'общего каркаса окна в CSS нет'
    assert '.gallery-view' not in css, 'прежнее имя подложки осталось'
    assert '.gallery-view' not in page, 'страница держится за прежнее имя подложки'


def test_gallery_window_uses_the_shared_shell(page):
    """Окно галереи переехало на общий каркас, а не осталось своим."""
    assert "className = 'modal-back gallery-modal'" in page \
        or "'modal-back gallery-modal'" in page, 'окно галереи не на общей подложке'
    assert 'modal-box gallery-card' in page, 'окно галереи не на общем каркасе'


def test_about_height_is_in_css_not_rows(css):
    """⚠ Общий слой ставит `textarea { height: 60px }`, и оно бьёт любой `rows`:
    описание в девять строк выходило в одну. Та же грабля уже была у окна снимка."""
    assert '#cardAbout' in css, 'высота описания не задана в CSS'


def test_styles_live_in_the_shared_sheet(css, page):
    """Размеры — в общей таблице стилей раздела, а не в разметке."""
    for name in ('.modal-back', '.modal-box', '.modal-field', '.modal-hint',
                 '.modal-edited', '.card-edit'):
        assert name in css, f'{name} нет в общей таблице стилей'
        assert f'{name} {{' not in page, f'{name} описан в разметке, а не в CSS'


def test_script_has_no_syntax_error(page):
    """Скрипт страницы цел как браузерный модуль.

    ⚠ Без этого правка разметки роняет страницу целиком, а тест разметки остаётся
    зелёным: он читает текст, а не исполняет его.
    """
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден в PATH — проверка синтаксиса пропущена')

    body = re.search(r'<script type="module">(.*?)</script>', page, re.S)
    assert body, 'модульного скрипта на странице нет'

    # ⚠ Выражения Jinja подменяются заглушкой: `node` их не понимает, а на
    # синтаксис JS они не влияют. Расширение `.mjs`, иначе `node --check` читает
    # файл как обычный скрипт и спотыкается на `import`.
    source = re.sub(r"\{\{.*?\}\}", "''", body.group(1))
    source = re.sub(r"\{#.*?#\}", '', source, flags=re.S)
    source = re.sub(r"\{%.*?%\}", '', source, flags=re.S)

    with tempfile.NamedTemporaryFile('w', suffix='.mjs', delete=False,
                                     encoding='utf-8') as tmp:
        tmp.write(source)
        path = tmp.name

    got = subprocess.run([node, '--check', path], capture_output=True, text=True)
    Path(path).unlink(missing_ok=True)

    assert got.returncode == 0, f'скрипт не проходит `node --check`: {got.stderr}'
```

⚠ Пропуск по `shutil.which('node')` — тот же, что в `tests/test_gallery_page.py:105`:
проверка синтаксиса полезна, но её отсутствие не повод красить сюиту у того, у
кого `node` не поставлен. В этом окружении `node` есть
(`/home/vasyl/.nvm/versions/node/v22.22.0/bin/node`), и тест обязан пройти, а не
пропуститься.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_card_page.py -v`
Expected: FAIL — `AssertionError: иконки правки в шапке карточки нет` и прочие.

- [ ] **Step 3: Rename the backdrop and the window shell in CSS**

В `data/uvicorn/public/css/dating_page.css` замени блок строк 849-882 на:

```css
/* ── Окна поверх страницы ────────────────────────────────────────────────────
   Подложка и каркас — **одни на все окна раздела**: кадр галереи, правка снимка,
   персональные данные анкеты.

   ⚠ Имя общее намеренно. Класс `gallery-view` у окна с почтой и описанием
   читался бы как ошибка, а вторая такая же подложка рядом разъехалась бы с
   первой на первой правке фона или `z-index`. */
.modal-back {
    position: fixed; inset: 0; z-index: 50; display: flex;
    align-items: center; justify-content: center; background: rgba(15, 23, 42, .82);
}
.modal-back img { max-width: 92vw; max-height: 92vh; object-fit: contain; }
/* ⚠ `zoom-out` — только у карусели: щелчок по подложке там и правда уводит из
   просмотра кадра. У окон правки курсор обычный, и прежний `.gallery-modal
   { cursor: default }` был оговоркой к общему правилу, которого больше нет. */
.gallery-carousel { cursor: zoom-out; }

.modal-box {
    width: min(560px, 94vw); max-height: 92vh; overflow: auto; padding: 20px;
    background: #fff; border-radius: 12px; display: flex; flex-direction: column; gap: 6px;
}
.modal-head { font-size: 14px; font-weight: 700; color: #0f172a; }
.modal-box label { font-size: 11px; color: #64748b; margin-top: 6px; }
.modal-box textarea, .modal-box input { width: 100%; box-sizing: border-box; }
.modal-foot {
    display: flex; align-items: center; justify-content: flex-end;
    gap: 8px; margin-top: 10px;
}
/* Причина отказа — в подвале окна, а не подсказкой в шапке страницы: окно стоит
   поверх, и подсказку под ним человек не увидит. */
.modal-why { margin-right: auto; font-size: 12px; color: #b91c1c; }

/* Поле окна: подпись, отметка, поле ввода, пояснение — одной колонкой.
   ⚠ Цвета и размеры поля ввода заданы прямо, а не унаследованы от подписи:
   подпись серая и мелкая, и наследование красило бы ввод её цветом. */
.modal-field { display: flex; flex-direction: column; gap: 3px; margin-top: 8px; }
.modal-label { font-size: 11px; color: #64748b; }
.modal-field input, .modal-field textarea { font-size: 13px; color: #0f172a; }
.modal-hint { font-size: 11px; color: #94a3b8; line-height: 1.45; font-style: normal; }
/* ⚠ Жёлтым метится **изменённое поле ключа**, и только пока оно изменено:
   постоянно горящее предупреждение перестают читать. */
.modal-hint-warn { color: #92400e; }
.modal-hint-warn b { color: #b45309; }
.modal-edited { font-style: normal; color: #b45309; }
.modal-edited a { color: inherit; }

/* ⚠⚠ Высота — **здесь**, а не атрибутом `rows`. В общем слое
   (`dashboard/css/common.css`) стоит `textarea { height: 60px }`, и оно бьёт
   любой `rows`: описание в девять строк выходило в одну. Править общий файл
   нельзя — он submodule, и правка уехала бы в соседние проекты.

   ⚠ `resize: vertical` возвращает то, что общий слой запретил целиком
   (`resize: none`): здесь правят абзац, и длина у него всякий раз своя. */
.modal-box #cardAbout { height: 150px; resize: vertical; }

/* ⚠ Окно описания снимка **крупнее соседних**, и это не вкусовщина: в нём правят
   абзац текста и строку тегов. В 560 пикселях описание шло в шесть строк с
   прокруткой, а теги не помещались в поле целиком — человек правил вслепую,
   прокручивая однострочный input. Ширина остаётся его собственной. */
.gallery-card { width: min(860px, 94vw); }
.gallery-card #galleryText { height: 170px; resize: vertical; }
.gallery-card #galleryTags { height: 52px; resize: vertical; }

/* Иконка правки в шапке карточки анкеты. ⚠ Прижата вправо и к верху: шапка —
   флексовая строка с фотографией и тремя строками текста, и по центру иконка
   встала бы напротив описания, а не напротив имени. */
.card-edit {
    margin-left: auto; align-self: flex-start; flex: 0 0 auto;
    border: 0; background: none; cursor: pointer; padding: 2px 4px;
    font-size: 14px; color: #94a3b8; line-height: 1;
}
.card-edit:hover { color: #4338ca; }
.card-edit:focus-visible { outline: 2px solid #a5b4fc; outline-offset: 2px; }
```

- [ ] **Step 4: Move the gallery window onto the shared shell**

В `data/uvicorn/template/admin/dating/index.html`:

- строка ~1242: `back.className = 'gallery-view gallery-carousel';`
  → `back.className = 'modal-back gallery-carousel';`
- строка ~1352: `back.className = 'gallery-view gallery-modal';`
  → `back.className = 'modal-back';`
- строка ~1353: `'<div class="gallery-card" onclick=…'`
  → `'<div class="modal-box gallery-card" onclick=…'`
- строка ~1354: `class="gallery-card-head"` → `class="modal-head"`
- строка ~1369: `class="gallery-card-foot"` → `class="modal-foot"`

⚠ `.gallery-modal` больше не нужен: он нёс одну строку `cursor: default`, которая
стала умолчанием подложки.

- [ ] **Step 5: Add the icon to the card head**

В `index.html`, в сборке `head` (строка ~910), допиши третьим ребёнком `.card`:

```js
        const head = `<div class="card">
            <img src="${esc(p.photo || '')}" alt="" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'">
            <div>
                <div class="card-name">${esc(p.name || data.login || '')}</div>
                <div class="card-meta">${ident}
                    ${p.age ? ' · ' + p.age : ''}${p.city ? ' · ' + esc(p.city) : ''}</div>
                <div class="card-meta">${esc(p.about || 'описание профиля сайт не отдал')}</div>
            </div>
            <button type="button" class="card-edit" id="cardEditBtn"
                    title="Править персональные данные: почту, номер на сайте, имя, возраст, город, описание">✎</button>
            </div>`;
```

В `settingsBind()` допиши привязку рядом с `deleteBtn`:

```js
        // ⚠ Вешается каждый раз: карточка перерисовывается фоновым опросом раз в
        // пятнадцать секунд, и узел всякий раз новый.
        const edit = document.getElementById('cardEditBtn');
        if (edit) edit.addEventListener('click', () => cardEditModal());
```

- [ ] **Step 6: Write the window**

В `index.html`, сразу после `settingsBind()`, допиши:

```js
    // ── Окно правки персональных данных ──────────────────────────────────────
    //
    // ⚠⚠ Шесть полей лежат в **трёх** разных местах: почта — колонкой `login`,
    // номер на сайте — колонкой `profile_id` и ключом `id` карточки, остальные
    // четыре — ключами карточки. Сводит их ручка; страница этого разделения не
    // знает и знать не должна.
    //
    // ⚠⚠ Пояснения под номером и именем — **постоянные**, а не всплывающие по
    // изменению: это две половины ключа, которым агент помнит эту девушку
    // (`%girl_id%_%girl_name%`), и прочитать об этом человек должен **до** того,
    // как начнёт править, а не узнать после. Переход («643694 → 999111») при этом
    // называется только когда он есть: горящее всегда предупреждение не читают.
    //
    // ⚠ Отказ показывается **в подвале окна**, а не `say()` в шапке страницы:
    // окно стоит поверх, и подсказку под ним человек не увидит. Набранное при
    // этом остаётся в полях — ему ещё править.
    function cardEditModal() {
        const data = cardData || {};
        const p = data.profile || {};
        const row = profiles.find(g => g.id === currentId) || {};
        const edited = new Set(data.edited || []);
        const wasUid = String(p.id || '');
        const wasName = String(p.name || '');

        // Подпись поля плюс отметка «правлено руками».
        // ⚠ Подписи здесь только литералами: пришедшее с сервера идёт через
        // `esc()` — на этой странице уже была настоящая инъекция через
        // неэкранированный атрибут.
        const label = (key, text) => `<span class="modal-label">${text}`
            + (edited.has(key)
                ? ' <i class="modal-edited">· правлено руками · '
                  + `<a href="#" data-forget="${key}">вернуть сайту</a></i>`
                : '')
            + '</span>';

        const back = document.createElement('div');
        back.className = 'modal-back';
        back.innerHTML = '<div class="modal-box" onclick="event.stopPropagation()">'
            + '<div class="modal-head">Персональные данные анкеты</div>'

            + '<label class="modal-field">' + label('login', 'Почта')
            + '<input type="text" id="cardLogin" maxlength="190" spellcheck="false"'
            + ` autocomplete="off" value="${esc(String(row.login || ''))}">`
            + '<i class="modal-hint">Ею анкета входит на сайт — пустой быть не может.'
            + (data.gallery_folder ? ''
                : ' ⚠ Папка галереи у этой анкеты не выбрана, а значит ищется по'
                  + ' логину — вместе с почтой уедет и она.')
            + '</i></label>'

            + '<label class="modal-field">' + label('id', 'ID на сайте')
            + '<input type="text" id="cardUid" maxlength="32" spellcheck="false"'
            + ` autocomplete="off" value="${esc(wasUid)}">`
            + '<i class="modal-hint" id="cardUidWhy"><b data-move></b>Номер анкеты на'
            + ' сайте. Вместе с именем он — ключ, которым агент помнит эту девушку:'
            + ' сменив его, вы делаете её незнакомой, и прежние факты останутся за'
            + ' старым номером. Пусто — «не знаем», это законно.</i></label>'

            + '<label class="modal-field">' + label('name', 'Имя')
            + `<input type="text" id="cardName" maxlength="190" value="${esc(wasName)}">`
            + '<i class="modal-hint" id="cardNameWhy"><b data-move></b>Как её зовут в'
            + ' анкете. Вторая половина того же ключа фактов — она же уходит в'
            + ' обращение, которым агент зовётся.</i></label>'

            + '<label class="modal-field">' + label('age', 'Возраст')
            + `<input type="number" id="cardAge" min="0" max="120" value="${esc(String(p.age || 0))}">`
            + '<i class="modal-hint">0 — «не знаем»: карточка тогда возраст не'
            + ' показывает вовсе.</i></label>'

            + '<label class="modal-field">' + label('city', 'Город')
            + `<input type="text" id="cardCity" maxlength="190" value="${esc(String(p.city || ''))}">`
            + '</label>'

            + '<label class="modal-field">' + label('about', 'Описание')
            + `<textarea id="cardAbout" maxlength="2000">${esc(String(p.about || ''))}</textarea>`
            + '<i class="modal-hint">Его читает агент и письма — «О себе».</i></label>'

            + '<div class="modal-foot">'
            + '<span class="modal-why" id="cardEditWhy"></span>'
            + '<button type="button" class="dating-btn" id="cardEditOff">Отмена</button>'
            + '<button type="button" class="dating-btn primary" id="cardEditKeep">Сохранить</button>'
            + '</div></div>';

        back.addEventListener('click', () => back.remove());
        document.body.appendChild(back);

        const why = document.getElementById('cardEditWhy');

        // Переход называем словами и только когда он есть.
        // ⚠ `textContent`, а не склейка разметки: значения набирает человек.
        const watch = (input, box, before) => {
            const move = box.querySelector('[data-move]');
            const paint = () => {
                const now = input.value.trim();
                const other = now !== before;
                box.classList.toggle('modal-hint-warn', other);
                move.textContent = other
                    ? `${before || 'пусто'} → ${now || 'пусто'}. ` : '';
            };
            input.addEventListener('input', paint);
        };
        watch(document.getElementById('cardUid'),
              document.getElementById('cardUidWhy'), wasUid);
        watch(document.getElementById('cardName'),
              document.getElementById('cardNameWhy'), wasName);

        back.querySelectorAll('[data-forget]').forEach(link =>
            link.addEventListener('click', async event => {
                event.preventDefault();
                const was = currentId;
                try {
                    await deleteData(
                        `${BASE}/api/dating/profile/${was}/card/${link.dataset.forget}`);
                } catch (err) {
                    why.textContent = 'отметка не снялась: ' + err.message;

                    return;
                }
                back.remove();
                say('поле вернулось сайту — обновит его следующий импорт');
                if (currentId === was) await profileLoad();
            }));

        document.getElementById('cardEditOff')
                .addEventListener('click', () => back.remove());

        document.getElementById('cardEditKeep').addEventListener('click', async () => {
            const was = currentId;
            const send = {
                login: document.getElementById('cardLogin').value.trim(),
                profile_id: document.getElementById('cardUid').value.trim(),
                name: document.getElementById('cardName').value.trim(),
                age: Number(document.getElementById('cardAge').value || 0),
                city: document.getElementById('cardCity').value.trim(),
                about: document.getElementById('cardAbout').value,
            };

            try {
                await putData(`${BASE}/api/dating/profile/${was}/card`, send);
            } catch (err) {
                why.textContent = 'не сохранилось: ' + err.message;

                return;
            }

            back.remove();
            say('персональные данные сохранены');
            // ⚠ Перечитываем **и список, и карточку**: правка меняет строку списка
            // (почта, имя, возраст, номер) не меньше, чем карточку, а список
            // приходит своей ручкой. Ответ одной покрыл бы одну из двух.
            await profilesLoad();
            if (currentId === was) await profileLoad();
        });
    }
```

- [ ] **Step 7: Fix the gallery page test**

В `tests/test_gallery_page.py:93` замени список классов:

```python
    for name in ('.gallery-grid', '.gallery-thumb', '.gallery-group',
                 '.modal-back', '.modal-box', '.gallery-card', '.gallery-why'):
```

и допиши рядом пояснение:

```python
    # ⚠ Подложка и каркас окна зовутся общими именами: их делят кадр галереи,
    # правка снимка и окно персональных данных анкеты. Своё у галереи осталось
    # только то, что и правда её — ширина окна и высоты полей.
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_profile_card_page.py tests/test_gallery_page.py -v`
Expected: PASS.

Run: `pytest -m page -q`
Expected: PASS — соседние страницы целы.

- [ ] **Step 9: Check it in the real app**

Подними панель и открой `/admin/dating`, выбери анкету, нажми ✎.

Проверь глазами:
1. Окно открывается, в нём шесть полей с нынешними значениями.
2. Изменение номера зажигает под ним жёлтое «643694 → 999111».
3. «Сохранить» закрывает окно, строка списка и карточка обновляются.
4. Открыть окно снова — у правленых полей стоит «правлено руками · вернуть сайту».
5. «Вернуть сайту» отметку снимает, значение в поле остаётся.
6. Пустая почта даёт отказ в подвале окна, набранное не теряется.

- [ ] **Step 10: Add the pointer to `CLAUDE.md`**

В `CLAUDE.md`, в раздел **Development Patterns**, после строки про ссылку на анкету
девушки (`§3.5.1`), вставь:

```markdown
- **Правка персональных данных анкеты руками** → `.claude/docs/api.md`, §3.14
  (⚠ окно по иконке ✎ в шапке карточки; шесть полей лежат в **трёх** местах, и
  номер на сайте — **в двух сразу**: колонка `profile_id` и ключ `id` карточки,
  читают их разные места. ⚠⚠ Правленое руками сайт больше не перезаписывает —
  правило одной функцией `profile_card_keep` на **обоих** писателей, импорт и
  чтение страницы кабинета; своя копия в каждом разошлась бы на первой правке.
  ⚠ Метится **изменённое**, а не присланное: иначе «открыл поглядеть и сохранил»
  заморозило бы все пять полей разом. ⚠ Отметка снимаемая — ссылка «вернуть
  сайту» у поля, иначе правило становится ловушкой)
```

И в таблицу **Key Files**, после строки `src/dating/profile_link.py`:

```markdown
| `src/dating/profile_card.py` | Карточка анкеты: правка руками и что она защищает от перезаписи (⚠ **единственная дверь** записи правки: карточка, колонки `login`/`profile_id` и отметки одним заходом. ⚠⚠ Номер уезжает в **оба** написания — колонку и ключ карточки; читают их разные места, и разойдясь, они не уронят ни одной проверки) |
```

- [ ] **Step 11: Full suite and commit**

Run: `pytest -q`
Expected: PASS. ⚠ Одиночное красное в полном прогоне — не обязательно регрессия:
сперва проверь, не идёт ли чужой `pytest` в том же каталоге, и перегони упавший
тест в одиночку.

```bash
git add data/uvicorn/public/css/dating_page.css data/uvicorn/template/admin/dating/index.html tests/test_profile_card_page.py tests/test_gallery_page.py CLAUDE.md
git commit -m "Анкета: окно правки персональных данных по иконке в шапке карточки

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
