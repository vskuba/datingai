# Вкладки «Профилей» над колонками — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Вкладки страницы `/admin/dating/profile` поднимаются над обеими колонками; у каждой вкладки свой отбор анкет в списке слева и своя выбранная анкета.

**Architecture:** Каркас страницы остаётся один, вкладка переключает состояние. Полоса вкладок переезжает из шапки правой панели в свою строку сетки во всю ширину. Отбор вкладки встаёт первым звеном перед уже существующим поиском. Выбранная анкета хранится словарём `openByTab`, а переменная `openId` остаётся и синхронизируется при переходе — так сорок мест, читающих `openId`, править не приходится.

**Tech Stack:** Jinja2-шаблон со скриптом внутри, CSS Grid, FastAPI + MySQL на серверной стороне, `pytest` + `httpx`.

**Спецификация:** `docs/superpowers/specs/2026-09-05-profiles-tabs-design.md`

## Global Constraints

- Комментарии и docstring — **по-русски**, в тоне и плотности соседнего кода: он объясняет «почему», а не «что».
- Тесты **наружу не ходят** — ни к RhapsodyAi, ни на сайт знакомств. Сторож сети в `tests/conftest.py` это проверяет; заглушка соседа — фикстура `rhapsody` из `tests/rhapsody_fake.py`.
- Тестовые анкеты **одноразовые** и удаляются в `finally`. Живые трогать нельзя.
- `SITE = 343` — сайт для тестовых анкет, как в `tests/test_profile_enabled.py`.
- Ручки в тестах зовутся **в процессе**, а не по HTTP: заведение анкеты сверяет сайт у соседа. Исключение — проверки, решающиеся до входа в обработчик (401), и чтение страниц: они по HTTP клиентом `auth_client`.
- Прогон сюиты: `ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/ -q`. Панель при этом должна работать.
- **Базовая линия — 721 прошедший тест.** После Задачи 1 — 722 (+1), после Задачи 2 — 725 (+3), после Задачи 3 — 727 (+2). Задача 4 тестов не добавляет.
- Работаем прямо в `master` — решение заказчика, ветку не заводить.
- Правки шаблона контейнера не требуют: Jinja перечитывает файл. Но у статики есть кэш — проверяя руками, `Ctrl+Shift+R`.
- ⚠ `.claude/docs-common/` — симлинк на `py_modules/docs`, общий репозиторий на все проекты. Правка через него уедет соседям. Писать только в `.claude/docs/`.

---

## File Structure

**Меняются:**

| Файл | Что меняется |
|------|--------------|
| `src/uvicorn/routers/dating/dating_profile.py:126` и `:142` | `mailing_on` в `SELECT` списка и приведение к `bool` |
| `data/uvicorn/template/admin/dating_profile.html:63-75` | CSS: `.work-tabs`/`.work-tab` → `.page-tabs`/`.page-tab`, полоса во всю ширину |
| `data/uvicorn/template/admin/dating_profile.html:33-40` | CSS: третья строка сетки под полосу вкладок |
| `data/uvicorn/template/admin/dating_profile.html:586-666` | Разметка: полоса вкладок переезжает из `.work-pane` в свою строку |
| `data/uvicorn/template/admin/dating_profile.html` (скрипт) | `openByTab`, `tabRows()`, отбор в `shownRows()`, счётчик, три «пусто», сброс страницы |
| `.claude/docs/dashboard_frontend.md` §3.5 | Раскладка, отбор, своя анкета у вкладки, три «пусто» |
| `.claude/docs/api.md` | `GET /api/dating/profile` отдаёт `mailing_on` |
| `.claude/docs/database.md` | ⚠ «Читать эту колонку пока некому» перестало быть правдой |

**Создаётся:**

| Файл | За что отвечает |
|------|-----------------|
| `tests/test_profiles_tabs.py` | Раскладка страницы: полоса над колонками, четыре вкладки, привязка состояния |

**Не меняются:** `data/uvicorn/template/admin/profile/site/*.html` (шаблоны сайта отдают тело вкладки, а не её отбор), `src/dating/*` (механики рассылки в этой работе нет), `.claude/docs/dating.md`.

---

### Task 1: `mailing_on` доезжает до списка анкет

Отбор вкладки «Письма рассылка» идёт по этому флагу, а в списке его сейчас нет.

⚠ **Это отмена решения предыдущей задачи** (`2026-09-05-profile-switches-design.md`): там флаг намеренно не клали в список — «блок читает карточку, а списку он не нужен». Тогда верно, теперь нет. В комментарии к коду это писать не надо: колонка просто едет в списке, как `enabled` и `auto_answer`.

**Files:**
- Modify: `src/uvicorn/routers/dating/dating_profile.py:126` (строка `SELECT`) и `:142` (приведение к `bool`)
- Test: `tests/test_profile_mailing.py` (новый тест в существующий класс `TestMailingEndpoint`)

**Interfaces:**
- Consumes: ручка `dating_profile_action_mailing` и модель `DatingProfileMailing` (уже есть); фикстура `profile` и функция `_set(profile_id, mailing_on)` из того же файла.
- Produces: `dating_profile_list(site_id, user)['result'][i]['mailing_on']: bool`.

- [ ] **Шаг 1: Написать падающий тест**

В `tests/test_profile_mailing.py` в класс `TestMailingEndpoint`, сразу после `test_mailing_reaches_the_card`, добавить:

```python
    async def test_mailing_reaches_the_list(self, rhapsody, profile):
        """Список анкет отдаёт флаг — по нему идёт отбор вкладки «Письма рассылка».

        ⚠ Без него отбор делать нечем, и вкладка была бы пуста всегда — при любом
        числе анкет с включённой рассылкой. Зеркало `test_flag_reaches_the_list`
        из `test_profile_enabled.py`: тот же урок, тот же способ проверки.
        """
        await _set(profile, True)
        rows = (await dating_profile_list(site_id=SITE, user=None))['result']
        found = next(row for row in rows if int(row['id']) == profile)

        assert found['mailing_on'] is True
```

В шапке файла к существующему импорту из `dating_profile` добавить `dating_profile_list`:

```python
from src.uvicorn.routers.dating.dating_profile import (DatingProfileSave,
                                                       dating_profile_create,
                                                       dating_profile_delete,
                                                       dating_profile_list,
                                                       dating_profile_row)
```

- [ ] **Шаг 2: Прогнать тест и убедиться, что он падает**

Выполнить:

```bash
ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) \
  .venv/bin/pytest tests/test_profile_mailing.py::TestMailingEndpoint::test_mailing_reaches_the_list -q
```

Ожидается: FAIL с `KeyError: 'mailing_on'` — колонки нет в `SELECT`, значит нет и в строке ответа.

- [ ] **Шаг 3: Добавить колонку в запрос**

В `src/uvicorn/routers/dating/dating_profile.py` строку 126 заменить:

```python
            '       g.last_error, g.profile, g.auto_answer, g.auto_answer_only, g.enabled, '
```

на:

```python
            '       g.last_error, g.profile, g.auto_answer, g.auto_answer_only, g.enabled, '
            '       g.mailing_on, '
```

- [ ] **Шаг 4: Привести к `bool`**

В том же файле, после строки `row['auto_answer'] = bool(row.get('auto_answer'))` (строка 142), добавить:

```python
        # Рассылка писем: по этому флагу «Профили» отбирают анкеты вкладки
        # «Письма рассылка». Без приведения строка отдавала бы 0 и 1, а страница
        # сравнивает с `true`.
        row['mailing_on'] = bool(row.get('mailing_on'))
```

- [ ] **Шаг 5: Прогнать тест и убедиться, что он проходит**

Выполнить ту же команду, что в шаге 2.
Ожидается: `1 passed`.

- [ ] **Шаг 6: Прогнать всю сюиту**

Выполнить: `ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/ -q`

Ожидается: `722 passed` (было 721). ⚠ Внимание на `tests/test_profile_crud.py` — он читает тот же список.

- [ ] **Шаг 7: Коммит**

```bash
git add src/uvicorn/routers/dating/dating_profile.py tests/test_profile_mailing.py
git commit -m "Список анкет отдаёт флаг рассылки: по нему пойдёт отбор вкладки"
```

---

### Task 2: полоса вкладок переезжает над колонками

Разметка и CSS. Поведение вкладок при этом **не меняется** — переключение работает как раньше, только кнопки стоят в другом месте. Отбор списка и своя анкета у вкладки — Задача 3.

**Files:**
- Modify: `data/uvicorn/template/admin/dating_profile.html` — CSS сетки (строки 33-40), CSS вкладок (63-75), разметка (586-666), три места в скрипте
- Create: `tests/test_profiles_tabs.py`

**Interfaces:**
- Consumes: `mailing_on` в списке из Task 1 (в этой задаче не используется, но уже едет).
- Produces: узел `#pageTabs` с классом `.page-tabs` **до** `.prof-pane`; кнопки `.page-tab` с `data-tab` из `chat|mail|mail_in|event`; переменная скрипта `pageTabsEl`.

⚠ **Почему переименование `.work-tab` → `.page-tab`, а не «оставим как было».** Полоса больше не принадлежит панели работы: имя `work-tabs` вне `.work-pane` — ложь, а следующий читатель будет искать её внутри правой колонки. Правок ровно шесть: два CSS-правила, один узел разметки, три обращения в скрипте.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_profiles_tabs.py`:

```python
"""⚠ Вкладки «Профилей» стоят над обеими колонками, а не внутри правой.

Каждая вкладка включает в себя обе колонки: слева список анкет по своему отбору,
справа работа с выбранной. Раньше вкладки жили в шапке правой панели, и список
слева был один на все четыре занятия — а список, нужный чату, не тот же, что нужен
рассылке писем.

⚠ **Разметку собирает шаблон, поведение — скрипт в нём же.** Сюита ходит по HTTP и
браузера не имеет: щёлкнуть по вкладке она не может. Поэтому сторожим то, что видно
в ответе, — порядок узлов и наличие привязки. Нарисованная, но ни к чему не
привязанная вкладка — тот случай, который тише сломанного теста.
"""

PAGE = '/admin/dating/profile'


class TestTabsAboveColumns:
    """Полоса вкладок — своя строка страницы, до колонок."""

    async def test_tabs_are_above_columns(self, auth_client):
        """⚠ Проверяем **порядок**, а не наличие: «подняты на уровень выше» и есть
        порядок. Полоса, оставшаяся внутри правой панели, тест проходила бы по
        наличию узла и провалила бы задачу.
        """
        page = (await auth_client.get(PAGE)).text
        tabs = page.find('id="pageTabs"')
        profiles = page.find('class="pane prof-pane"')
        work = page.find('class="pane work-pane"')

        assert tabs != -1, 'полосы вкладок нет на странице'
        assert profiles != -1 and work != -1, 'колонки не нашлись — разметка сломана'
        assert tabs < profiles, 'полоса вкладок стоит после списка анкет'
        assert tabs < work, 'полоса вкладок стоит внутри правой панели или после неё'

    async def test_old_tabs_are_gone(self, auth_client):
        """Прежнего имени не осталось: `work-tabs` вне панели работы — ложь, и
        следующий читатель искал бы полосу внутри правой колонки."""
        page = (await auth_client.get(PAGE)).text

        assert 'work-tabs' not in page, 'осталось прежнее имя полосы вкладок'

    async def test_all_four_tabs_present(self, auth_client):
        """Четыре вкладки на месте. Потерять одну при переносе легко, и пропажу
        никто не заметит: страница откроется на «Чате» и будет выглядеть целой."""
        page = (await auth_client.get(PAGE)).text

        for tab in ('chat', 'mail', 'mail_in', 'event'):
            assert f'data-tab="{tab}"' in page, f'вкладка {tab} пропала'
```

- [ ] **Шаг 2: Прогнать тест и убедиться, что он падает**

Выполнить:

```bash
ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) \
  .venv/bin/pytest tests/test_profiles_tabs.py -q
```

Ожидается: два падения (`test_tabs_are_above_columns` — `полосы вкладок нет на странице`; `test_old_tabs_are_gone` — `осталось прежнее имя полосы вкладок`) и одно прохождение (`test_all_four_tabs_present`: четыре вкладки уже есть, просто не там).

- [ ] **Шаг 3: Завести строку сетки под полосу**

В `data/uvicorn/template/admin/dating_profile.html` заменить строки 35-40:

```css
            grid-template-columns: var(--col) 6px minmax(0, 1fr);
            grid-template-rows: auto minmax(0, 1fr);
        }
        .dating-head { grid-column: 1 / -1; }
        .prof-pane { grid-column: 1; grid-row: 2; }
        .work-pane { grid-column: 3; grid-row: 2; }
```

на:

```css
            grid-template-columns: var(--col) 6px minmax(0, 1fr);
            /* Три строки: шапка сайта, полоса вкладок, колонки. ⚠ Вкладки — своя
               строка во всю ширину, а не часть правой панели: каждая включает в
               себя обе колонки, и стоять внутри одной из них она не может. */
            grid-template-rows: auto auto minmax(0, 1fr);
        }
        .dating-head { grid-column: 1 / -1; grid-row: 1; }
        .page-tabs { grid-column: 1 / -1; grid-row: 2; }
        .prof-pane { grid-column: 1; grid-row: 3; }
        .work-pane { grid-column: 3; grid-row: 3; }
```

- [ ] **Шаг 4: Поправить CSS разделителя ширины**

Там же найти правило `.split-col` (около строки 51) и заменить:

```css
        .split-col { grid-column: 2; grid-row: 2; cursor: col-resize; }
```

на:

```css
        .split-col { grid-column: 2; grid-row: 3; cursor: col-resize; }
```

⚠ Без этого разделитель уедет в строку вкладок, а между колонками останется дыра.

- [ ] **Шаг 5: Переписать CSS вкладок**

Заменить строки 63-75 (блок «Вкладки правой колонки» вместе с `#workWho`) на:

```css
        /* ---------- Полоса вкладок страницы ---------- */

        /* ⚠ Вкладки стоят **над обеими колонками**, а не в шапке правой панели.
           Каждая включает в себя обе: слева список анкет по своему отбору, справа
           работа с выбранной. Пока полоса жила в шапке панели, список слева был
           один на все четыре занятия — а он у них разный. */
        .page-tabs {
            display: flex; gap: 2px; align-items: center;
            padding: 0 0 8px; margin: 0;
        }
        .page-tab {
            padding: 3px 10px; border: none; border-radius: 6px; background: none;
            font-family: inherit; font-size: 12px; font-weight: 700; color: #94a3b8;
            text-transform: uppercase; letter-spacing: .04em; cursor: pointer;
        }
        .page-tab:hover { color: #4338ca; }
        .page-tab.on { background: #eef2ff; color: #4338ca; }

        /* Кого сейчас правим — в шапке правой панели, где стоит работа с анкетой.
           ⚠ Осталось там, хотя вкладки ушли: подпись про **анкету**, а не про
           вкладку, и место ей рядом с тем, что она подписывает. */
        #workWho { min-width: 0; color: #94a3b8; font-weight: 400;
                   text-transform: none; letter-spacing: normal;
                   overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Шаг 6: Перенести полосу в разметке**

В том же файле, после закрывающего `</div>` блока `.dating-head` (строка 613) и **до** `<div class="pane prof-pane">`, вставить:

```html
    {# Полоса вкладок — во всю ширину, над обеими колонками. Каждая вкладка
       включает в себя обе: слева список анкет по своему отбору, справа работа с
       выбранной. #}
    <div class="page-tabs" id="pageTabs">
        <button type="button" class="page-tab on" data-tab="chat">Чат</button>
        {# ⚠ Две вкладки писем, а не одна: рассылка и входящие — разные вещи.
           Рассылка это то, что анкета шлёт сама (одно письмо многим), входящие —
           то, что пишут ей. Одной вкладкой они слипались бы в «почту вообще», где
           непонятно, чьё письмо. #}
        <button type="button" class="page-tab" data-tab="mail">Письма рассылка</button>
        <button type="button" class="page-tab" data-tab="mail_in">Письма входящие</button>
        <button type="button" class="page-tab" data-tab="event">Ивенты</button>
    </div>
```

Затем в шапке правой панели заменить:

```html
        <div class="pane-head">
            <div class="work-tabs" id="workTabs">
                <button type="button" class="work-tab on" data-tab="chat">Чат</button>
                {# ⚠ Две вкладки писем, а не одна: рассылка и входящие — разные
                   вещи. Рассылка это то, что анкета шлёт сама (одно письмо
                   многим), входящие — то, что пишут ей. Одной вкладкой они
                   слипались бы в «почту вообще», где непонятно, чьё письмо. #}
                <button type="button" class="work-tab" data-tab="mail">Письма рассылка</button>
                <button type="button" class="work-tab" data-tab="mail_in">Письма входящие</button>
                <button type="button" class="work-tab" data-tab="event">Ивенты</button>
            </div>
            <span id="workWho"></span>
        </div>
```

на:

```html
        <div class="pane-head">
            <span id="workWho"></span>
        </div>
```

- [ ] **Шаг 7: Поправить три обращения в скрипте**

В том же файле, строка 725, заменить:

```javascript
    const workTabsEl = document.getElementById('workTabs');
```

на:

```javascript
    const pageTabsEl = document.getElementById('pageTabs');
```

Строки 1091-1092 заменить:

```javascript
        workTabsEl.querySelectorAll('.work-tab').forEach(btn =>
            btn.classList.toggle('on', btn.dataset.tab === workTab));
```

на:

```javascript
        pageTabsEl.querySelectorAll('.page-tab').forEach(btn =>
            btn.classList.toggle('on', btn.dataset.tab === workTab));
```

Строки 2528-2530 заменить:

```javascript
    workTabsEl.addEventListener('click', (e) => {
        const tab = e.target.closest('.work-tab');
        if (!tab || tab.dataset.tab === workTab) return;
```

на:

```javascript
    pageTabsEl.addEventListener('click', (e) => {
        const tab = e.target.closest('.page-tab');
        if (!tab || tab.dataset.tab === workTab) return;
```

⚠ Комментарий над этим обработчиком говорит «Нажатие по кнопке, а не по всей шапке: рядом с вкладками стоит имя анкеты». Имя анкеты рядом больше не стоит — оно осталось в шапке правой панели. Заменить комментарий на:

```javascript
    // Переключение вкладок. Нажатие по кнопке, а не по всей полосе: полоса тянется
    // во всю ширину страницы, и промах мимо кнопки не должен ничего переключать.
```

- [ ] **Шаг 8: Убедиться, что в файле не осталось прежнего имени**

Выполнить: `grep -n "work-tab\|workTabs" data/uvicorn/template/admin/dating_profile.html`

Ожидается: пусто. Найдётся что-то — значит шаг 6 или 7 сделан не до конца.

- [ ] **Шаг 9: Прогнать тест раскладки**

Выполнить:

```bash
ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) \
  .venv/bin/pytest tests/test_profiles_tabs.py -q
```

Ожидается: `3 passed`.

- [ ] **Шаг 10: Прогнать всю сюиту**

Выполнить: `ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/ -q`

Ожидается: `725 passed` (722 + три новых). ⚠ Внимание на `tests/test_timezone.py`, `tests/test_role.py` и `tests/test_base_path.py` — все трое читают эту страницу и упадут на сломанной разметке.

- [ ] **Шаг 11: Проверить руками в браузере**

Открыть `http://localhost:8021/admin/dating/profile` (при необходимости `Ctrl+Shift+R`):

1. Полоса вкладок — отдельной строкой под шапкой сайта, во всю ширину.
2. В шапке правой панели вкладок нет; при выбранной анкете там её имя.
3. Щелчок по вкладке подсвечивает её и меняет правую колонку, как раньше.
4. Разделитель между колонками на месте и тянется.

Нет браузера — скажи об этом в отчёте прямо, не выдавая непроверенное за проверенное.

- [ ] **Шаг 12: Коммит**

```bash
git add data/uvicorn/template/admin/dating_profile.html tests/test_profiles_tabs.py
git commit -m "Вкладки «Профилей» встали над обеими колонками"
```

---

### Task 3: свой отбор и своя анкета у каждой вкладки

**Files:**
- Modify: `data/uvicorn/template/admin/dating_profile.html` (скрипт: состояние, `tabRows`, `shownRows`, `tablePaint`, `openSet`, обработчик вкладок, хвост `profilesLoad`)
- Modify: `tests/test_profiles_tabs.py` (новый тест привязки)

**Interfaces:**
- Consumes: `mailing_on` в строке списка (Task 1); `pageTabsEl` и класс `.page-tab` (Task 2). Готовое на странице: `profiles` — массив всех анкет сайта; `findEl` — поле поиска; `countEl` — счётчик `#profilesN`; `empty(text)` — рисует строку в пустом списке и чистит счётчик; `pagerRender(el, page, pages, onPick)`; `openSet(id)`; `workPaint()`; `tablePaint(tries)`.
- Produces: `TAB_ROWS` (карта отборов), `tabRows()`, `openByTab`.

- [ ] **Шаг 1: Написать падающий тест**

В `tests/test_profiles_tabs.py` добавить в конец:

```python
class TestTabStateIsWired:
    """Состояние вкладок привязано, а не только нарисовано.

    ⚠ Урок предыдущей работы: разметка без привязки — вкладка нарисованная и
    мёртвая. Щёлкнуть по ней сюита не может (браузера нет), поэтому сторожим
    наличие самого механизма: карты отборов и словаря выбранных анкет.
    """

    async def test_tab_filter_exists(self, auth_client):
        """Отбор вкладки: без него список слева один на все четыре занятия."""
        page = (await auth_client.get(PAGE)).text

        assert 'TAB_ROWS' in page, 'карты отборов нет — списки вкладок одинаковы'
        assert 'mailing_on' in page, 'отбор рассылки не смотрит на флаг'

    async def test_open_by_tab_exists(self, auth_client):
        """Своя выбранная анкета у каждой вкладки."""
        page = (await auth_client.get(PAGE)).text

        assert 'openByTab' in page, 'выбранная анкета общая на все вкладки'
```

- [ ] **Шаг 2: Прогнать тест и убедиться, что он падает**

Выполнить:

```bash
ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) \
  .venv/bin/pytest tests/test_profiles_tabs.py::TestTabStateIsWired -q
```

Ожидается: два падения — `карты отборов нет` и `выбранная анкета общая на все вкладки`.

- [ ] **Шаг 3: Завести карту отборов и словарь выбранных**

В `data/uvicorn/template/admin/dating_profile.html` после строки `let openId = 0;` (строка 734) и её комментария вставить:

```javascript
    // ⚠ Выбранная анкета — **своя у каждой вкладки**. Списки у вкладок разные, и
    // общий выбор означал бы правую колонку с анкетой, которой слева не видно:
    // перешёл с «Чата» на «Письма рассылка», а у выбранной анкеты рассылка снята.
    //
    // `openId` при этом остаётся и держит выбор активной вкладки: его читают в
    // сорока местах, и заменять их обращением к словарю значило бы править сорок
    // мест ради одного и того же числа. Синхронизирует `openSet`.
    const openByTab = {chat: 0, mail: 0, mail_in: 0, event: 0};

    // Отбор анкет по вкладке. ⚠ Разный, и это вся суть перестройки: список,
    // нужный чату, не тот же, что нужен рассылке.
    //
    // «Чат» не отбирает ничего намеренно: это основное занятие страницы, и скрыв
    // часть анкет, мы отняли бы единственное место, где видны все.
    //
    // ⚠ У «Писем входящих» и «Ивентов» отбора нет за отсутствием данных: механики
    // у них пока никакой, и правило без источника было бы выдуманным. Своё они
    // получат вместе со своими данными.
    const TAB_ROWS = {
        chat: rows => rows,
        mail: rows => rows.filter(row => row.mailing_on === true),
        mail_in: rows => rows,
        event: rows => rows,
    };

    // Анкеты активной вкладки — первое сито, до поиска и до страниц.
    const tabRows = () => (TAB_ROWS[workTab] || TAB_ROWS.chat)(profiles);
```

- [ ] **Шаг 4: Пропустить поиск через отбор вкладки**

Заменить строки 809-811:

```javascript
    function shownRows() {
        const needle = findEl.value.trim().toLowerCase();
        if (!needle) return profiles;
```

на:

```javascript
    // ⚠ Поиск ищет **внутри вкладки**, а не по сайту: порядок сит — вкладка,
    // потом поиск, потом страница. От него зависит, какое из трёх «пусто»
    // показать (см. `tablePaint`).
    function shownRows() {
        const rows = tabRows();
        const needle = findEl.value.trim().toLowerCase();
        if (!needle) return rows;
```

Ниже в той же функции заменить `return profiles.filter(row => ...)` на `return rows.filter(row => ...)` — то есть строку:

```javascript
        return profiles.filter(row => [row.name, row.login, row.site_uid]
```

на:

```javascript
        return rows.filter(row => [row.name, row.login, row.site_uid]
```

- [ ] **Шаг 5: Счётчик и три «пусто»**

Заменить начало `tablePaint` (строки 850-866):

```javascript
    function tablePaint(tries = 3) {
        const found = shownRows();
        countEl.textContent = found.length === profiles.length
            ? profiles.length : `${found.length} из ${profiles.length}`;

        if (!profiles.length) {
            empty('На этом сайте учёток пока нет. Заводят их на странице «Анкеты».');
            pagerRender(pagerEl, 0, 0, () => {});
            return;
        }
        if (!found.length) {
            tableEl.innerHTML = '<div class="prof-empty">Ничего не нашлось.</div>';
            pagerRender(pagerEl, 0, 0, () => {});
            return;
        }
```

на:

```javascript
    function tablePaint(tries = 3) {
        const rows = tabRows();
        const found = shownRows();
        // ⚠ Считаем **внутри вкладки**: знаменатель — её список, а не весь сайт.
        // Иначе на рассылке при семи анкетах с флагом и 64 на сайте счётчик
        // сказал бы «7 из 64» — то есть выдал бы отбор за поиск.
        countEl.textContent = found.length === rows.length
            ? rows.length : `${found.length} из ${rows.length}`;

        // ⚠ Три разных «пусто», и путать их нельзя: причины разные, и общая
        // строка отправила бы человека не туда. Порядок — от самой общей причины
        // к самой частной, иначе человек с набранным поиском на пустой вкладке
        // узнал бы про поиск, а не про отбор.
        if (!profiles.length) {
            empty('На этом сайте учёток пока нет. Заводят их на странице «Анкеты».');
            pagerRender(pagerEl, 0, 0, () => {});
            return;
        }
        if (!rows.length) {
            // Отбор вкладки не пропустил никого. Сейчас так бывает только у
            // рассылки — у остальных вкладок отбор пропускает всех, и сюда они
            // не доходят.
            empty('Ни у одной анкеты сайта не включена рассылка. Включают галочкой '
                  + '«Письма рассылка» в настройках анкеты на «Анкетах».');
            pagerRender(pagerEl, 0, 0, () => {});
            return;
        }
        if (!found.length) {
            tableEl.innerHTML = '<div class="prof-empty">Ничего не нашлось.</div>';
            pagerRender(pagerEl, 0, 0, () => {});
            return;
        }
```

- [ ] **Шаг 6: Хранить выбор по вкладке**

Заменить тело `openSet` (строки 2161-2170):

```javascript
    function openSet(id) {
        openId = Number(id || 0);
```

на:

```javascript
    function openSet(id) {
        openId = Number(id || 0);
        // Выбор запоминается за вкладкой: вернувшись на неё, человек застаёт то,
        // что оставил.
        openByTab[workTab] = openId;
```

- [ ] **Шаг 7: Переключать выбор вместе со вкладкой**

Заменить тело обработчика вкладок (строки 2532-2533):

```javascript
        workTab = tab.dataset.tab;
        workPaint();
```

на:

```javascript
        workTab = tab.dataset.tab;
        // Выбор — свой у вкладки. ⚠ Не через `openSet`: он **пишет** выбор в
        // словарь, а здесь надо прочитать уже запомненный.
        openId = openByTab[workTab] || 0;
        // Номер страницы — на первую: третья страница другого списка ничего не
        // значит. ⚠ Не то же, что зажим в `tablePaint`: тот правит номер, когда
        // страниц стало меньше, но не обнуляет его.
        page = 0;
        dialogsStateSet();
        contactsPage = 0;
        tablePaint();
        workPaint();
```

- [ ] **Шаг 8: Сбрасывать выбор, если анкета выпала из отбора**

Заменить строку 804 вместе с комментарием:

```javascript
        // Раскрытой анкеты могло не стать: её удалили здесь же или она ушла вместе
        // со сменой сайта. Держать открытой строку, которой в списке нет, нельзя —
        // журнал справа показывал бы чужое.
        if (openId && !openRow()) openSet(0);
```

на:

```javascript
        // Раскрытой анкеты могло не стать: её удалили здесь же, она ушла вместе со
        // сменой сайта или **выпала из отбора вкладки** — рассылку сняли в другом
        // окне. Держать открытой строку, которой в списке нет, нельзя: журнал
        // справа показывал бы чужое.
        //
        // ⚠ Ищем среди отобранных вкладкой, а не среди всех анкет сайта: анкета
        // без рассылки на вкладке «Письма рассылка» есть у сайта, но не в списке.
        if (openId && !tabRows().some(row => row.id === openId)) openSet(0);
```

- [ ] **Шаг 9: Прогнать тесты раскладки**

Выполнить:

```bash
ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) \
  .venv/bin/pytest tests/test_profiles_tabs.py -q
```

Ожидается: `5 passed`.

- [ ] **Шаг 10: Прогнать всю сюиту**

Выполнить: `ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-) .venv/bin/pytest tests/ -q`

Ожидается: `727 passed` (725 + два новых).

- [ ] **Шаг 11: Проверить руками в браузере**

Открыть `http://localhost:8021/admin/dating/profile`:

1. Вкладка «Чат»: список слева полный, счётчик показывает общее число анкет сайта.
2. Вкладка «Письма рассылка»: список пуст, в нём строка «Ни у одной анкеты сайта не включена рассылка…». ⚠ Это ожидаемо: флаг снят у всех 64 анкет.
3. Включить рассылку одной анкете на `/admin/dating` → вернуться → на «Письма рассылка» она одна, счётчик «1».
4. Выбрать анкету на «Чате», перейти на «Письма рассылка» и обратно — на «Чате» выбор сохранился.
5. Выбрать анкету на «Чате», у которой рассылка снята, перейти на «Письма рассылка» — справа «Выберите анкету в списке», выделения нет.
6. Набрать в поиске имя, переключить вкладку — набранное осталось.

Нет браузера — скажи об этом в отчёте прямо.

- [ ] **Шаг 12: Коммит**

```bash
git add data/uvicorn/template/admin/dating_profile.html tests/test_profiles_tabs.py
git commit -m "У каждой вкладки «Профилей» свой отбор анкет и своя выбранная"
```

---

### Task 4: документация

**Files:**
- Modify: `.claude/docs/dashboard_frontend.md` (§3.5 «Страница «Профили»», раздел про каркас)
- Modify: `.claude/docs/api.md` (строка `GET /api/dating/profile`)
- Modify: `.claude/docs/database.md` (абзац про `mailing_on`)

**Interfaces:**
- Consumes: всё сделанное задачами 1-3. Ничего не производит.

- [ ] **Шаг 1: Раскладка страницы**

В `.claude/docs/dashboard_frontend.md`, в разделе §3.5 «Страница «Профили»» (начинается около строки 291), после абзаца про каркас страницы добавить:

```markdown
### ⚠ Вкладки стоят над обеими колонками, а не в шапке правой панели

Каждая вкладка включает в себя обе колонки: слева список анкет **по своему
отбору**, справа работа с выбранной. Пока полоса вкладок жила в шапке правой
панели, список слева был один на все четыре занятия — а он у них разный: чату
нужны все анкеты сайта, рассылке писем — только те, у кого она включена.

| Вкладка | Отбор |
|---------|-------|
| Чат | все анкеты сайта |
| Письма рассылка | только `mailing_on = 1` |
| Письма входящие | все анкеты сайта |
| Ивенты | все анкеты сайта |

«Чат» не отбирает ничего намеренно: это основное занятие страницы, и скрыв часть
анкет, мы отняли бы единственное место, где видны все. У «Писем входящих» и
«Ивентов» отбора нет за отсутствием данных — правило без источника было бы
выдуманным.

**Отбор делает страница, а не сервер.** `GET /api/dating/profile` отдаёт все анкеты
сайта разом, без `LIMIT`; порядок сит — вкладка, потом поиск, потом страница.

⚠ **Своя выбранная анкета у каждой вкладки** (`openByTab`). Общий выбор означал бы
правую колонку с анкетой, которой слева не видно: перешёл с «Чата» на «Письма
рассылка», а у выбранной рассылка снята. Выбор запоминается за вкладкой —
вернувшись, человек застаёт то, что оставил.

⚠ **Выпавшая из отбора анкета снимается с выбора.** Рассылку сняли в другом окне,
анкету удалили — перерисовка списка проверяет, есть ли выбранная среди отобранных,
и если нет, показывает «Выберите анкету в списке». Молча оставленное выделение на
строке, которой нет, — это работа с анкетой, невидимой в списке.

**Ширины разделителей общие, не по вкладкам.** `--col`, `--log`, `--fav`,
`--online-h` — свойство экрана, а не занятия: своя ширина у каждой вкладки означала
бы раскладку, прыгающую на каждый щелчок.

**Поиск переживает переход, номер страницы — нет.** Поиск про человека («ищу
Марину»), а третья страница другого списка ничего не значит.

#### Три разных «пусто», и путать их нельзя

| Когда | Что показано |
|-------|--------------|
| На сайте нет анкет вовсе | «На этом сайте учёток пока нет. Заводят их на странице «Анкеты».» |
| Отбор вкладки не пропустил никого | «Ни у одной анкеты сайта не включена рассылка. Включают галочкой «Письма рассылка» в настройках анкеты на «Анкетах».» |
| Поиск ничего не нашёл | «Ничего не нашлось.» |

Проверяются в этом порядке — от общей причины к частной. Иначе человек с набранным
поиском на пустой вкладке узнал бы про поиск, а не про отбор.

⚠ Среднее состояние — **не исключение, а первый день**: `mailing_on` снят у всех
живых анкет, и без объясняющей строки вкладка выглядела бы сломанной.
```

- [ ] **Шаг 2: Строка списка в api.md**

В `.claude/docs/api.md`, в таблице ручек анкет (около строки 73), заменить строку:

```markdown
| `GET` | `` | Список учёток. Пароля в ответе нет — только `has_password`; `site_uid` — номер анкеты на сайте, по нему ищет поиск страницы |
```

на:

```markdown
| `GET` | `` | Список учёток. Пароля в ответе нет — только `has_password`; `site_uid` — номер анкеты на сайте, по нему ищет поиск страницы; `mailing_on` — по нему «Профили» отбирают анкеты вкладки «Письма рассылка» |
```

- [ ] **Шаг 3: Починить абзац про `mailing_on` в database.md**

В `.claude/docs/database.md` (строки 128-131) заменить:

```markdown
**`dating_profile.mailing_on`** — слать ли от анкеты рассылку писем. ⚠ **Читать
эту колонку пока некому:** круга рассылки в проекте нет, и `mailing_on` хранит
только решение человека, поставленное галочкой в блоке «Настройки анкеты». Не
ищите исполнителя — его напишет отдельная задача, она же заведёт индекс.
```

на:

```markdown
**`dating_profile.mailing_on`** — слать ли от анкеты рассылку писем. Колонку читает
**отбор списка** на «Профилях»: вкладка «Письма рассылка» показывает только анкеты
с этим флагом. ⚠ **А вот исполнителя у него нет:** круга рассылки в проекте не
существует, и поставленная галочка пока никому не поручает писать письма. Не ищите
его — его напишет отдельная задача, она же заведёт индекс.
```

⚠ Разница тонкая, но существенная: «никто не читает» и «никто не исполняет» — не
одно и то же, и первое перестало быть правдой. Остальная часть абзаца (умолчание
`0` в отличие от `auto_answer`, три флага про разное) остаётся как есть.

- [ ] **Шаг 4: Проверить, что документация сошлась с кодом**

Выполнить: `grep -ln "mailing_on" .claude/docs/*.md`

Ожидается три файла: `api.md`, `database.md`, `dashboard_frontend.md`.

Затем: `grep -n "Читать эту колонку пока некому" .claude/docs/database.md`

Ожидается: пусто — утверждение исправлено.

- [ ] **Шаг 5: Коммит**

```bash
git add .claude/docs/dashboard_frontend.md .claude/docs/api.md .claude/docs/database.md
git commit -m "Документация: вкладки «Профилей» над колонками и свой отбор у каждой"
```

---

## Чего в этой работе нет

- **Сама рассылка писем** — круг воркера, кому пишем, чем отправляем, как часто.
- **Наполнение вкладок «Письма входящие» и «Ивенты»** — остаются заглушками «появится здесь».
- **Свои столбцы у списков разных вкладок** — заказчик выбрал разный отбор, а не разные колонки. Столбцы у всех четырёх одни.
- **Сохранение вкладки и выбора между перезагрузками** — страница по-прежнему открывается на «Чате» без выбранной анкеты.
