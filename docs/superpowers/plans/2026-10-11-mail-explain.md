# Окно «почему этой паре не пишут письма» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На «Фаворитах → Диалоги» завести значок ⓘ, который объясняет, что уйдёт
этой паре следующим письмом, что мешает прямо сейчас и когда помеха снимется.

**Architecture:** Всё считает сервер — новый домен-модуль `src/dating/mail_explain.py`
теми же выражениями, что и круги; ручка отдаёт готовые вердикты, страница их
расставляет. Попутно чинится счётчик «следующий заход через N»: три копии одной
функции сводятся к одной в общем слое `py_modules/round_/round_slot.py`.

**Tech Stack:** Python 3.11, FastAPI, aiomysql, Redis, pytest (asyncio), Jinja2,
ванильный JS без сборки.

**Спека:** `docs/superpowers/specs/2026-10-11-mail-explain-design.md`

## Global Constraints

- Язык кода, докстрингов, комментариев и строк на экране — **русский**. Докстринг
  объясняет **почему**, а не пересказывает код. Правила — `.claude/docs-common/code_rules.md`.
- **Живого HTTP в тестах нет никогда.** Сосед — `tests/rhapsody_fake.py`, сайт —
  `tests/site_api_fake.py` (`.claude/docs/testing.md`, §1.2).
- Анкеты в тестах заводятся **только** фикстурами `girl` / `girl_make` из
  `tests/conftest.py`. Своих `INSERT INTO profile` и своих `DELETE` не писать:
  каскад уносит двенадцать таблиц сам, а `favorite` фикстура чистит отдельно.
- Сайт-стенд сюиты — `site_id = 999343` (константа `STAND_SITE` в тестах).
- Прогон группы: `pytest -m mail -q`. Полный прогон — только перед выкладкой.
- Перед `Write`/`Edit` нового кода может сработать застава `claude_gate_tool.py`:
  она **один раз** отменит запись и покажет кандидатов из общего слоя. Это не
  запрет — прочитать докстринг кандидата и повторить запись.
- `py_modules` — **отдельный репозиторий** (submodule). Коммит туда и коммит в
  проект — разные коммиты, и порядок важен: сперва `py_modules`, потом проект.
- Нельзя трогать файлы, которые правит вторая сессия: `src/dating/initiative.py`,
  `src/dating/chat_initiative.py`, `src/dating/initiative_round.py`,
  `src/dating/site_setting.py`, `src/uvicorn/routers/dating/chat.py`,
  `tests/test_initiative_*.py`, `tests/test_chat_initiative.py`, а в
  `data/uvicorn/template/admin/dating/profile.html` — зону `initiativeInfoCounts`
  (около строки 12694).

---

## Структура файлов

| Файл | Ответственность | Задача |
|---|---|---|
| `py_modules/round_/round_slot.py` | **создать** `round_slot_left` — сколько секунд до следующего захода | 1 |
| `src/dating/favorite_mail_round.py` | `favorite_mail_round_next` → один вызов слоя | 2 |
| `src/dating/invite_round.py` | `invite_round_next` → один вызов слоя | 2 |
| `src/dating/reply_round.py` | **создать** `reply_round_next` | 2 |
| `tests/test_round_next.py` | **создать** — регрессия на мёртвый счётчик | 2 |
| `src/dating/favorite_mail.py` | `_too_soon` → `favorite_mail_person_soon` | 3 |
| `src/dating/mail_explain.py` | **создать** — одна дверь `mail_explain` | 4–7 |
| `tests/test_mail_explain.py` | **создать** — помехи и сторож согласия | 4–8 |
| `src/uvicorn/routers/dating/favorite_mail.py` | ручка `GET /explain` | 9 |
| `data/.../profile/site/_base.html` | кнопка ⓘ в шапке разговора | 10 |
| `data/uvicorn/public/css/dating_page.css` | одна строка про край шапки | 10 |
| `data/.../admin/dating/profile.html` | показ значка и тело окна | 10 |
| `.claude/docs/api.md`, `CLAUDE.md` | документация | 11 |

---

### Task 1: `round_slot_left` в общем слое

**Files:**
- Modify: `py_modules/round_/round_slot.py` (дописать после `round_slot_due`, около строки 118)

**Interfaces:**
- Produces: `round_slot_left(key: str, unit=None, what: str = '') -> int` — секунды
  до следующего захода единицы. `0` — отметки нет, идти можно сейчас. `-1` —
  спросить не вышло.

**Почему в слой, а не в проект.** В проекте уже лежат **две дословные копии** этой
функции (`invite_round_next`, `favorite_mail_round_next`), и обе сломаны одинаково:
каждая подставляет ключ сама — `KEY.format(site_id=…)` на шаблоне с меткой `{id}`,
то есть `KeyError`, проглоченный `except`. Третью копию потребовал бы круг ответов.
Ключ обязан собирать `round_slot_key` — он здесь же, и метка у него одна на всех.

- [ ] **Step 1: Перевести подмодуль на ветку**

`py_modules` в проекте стоит отсоединённой головой (`HEAD detached`), и коммит в
таком состоянии никуда не уедет.

```bash
git -C py_modules checkout master
git -C py_modules pull --ff-only
git -C py_modules status --short     # ожидаем: пусто
```

- [ ] **Step 2: Написать проверку руками**

Своей сюиты у `py_modules` нет, поэтому проверка — скриптом. Положить в скрэтч, не
в репозиторий:

```bash
cat > /tmp/round_slot_left_check.py <<'PY'
import sys, time
sys.path.insert(0, 'py_modules')
from redis_.redis_ import redis_conn_get
from round_.round_slot import round_slot_left, round_slot_mark

KEY = 'probe:round:left:{id}'

r = redis_conn_get()
r.delete('probe:round:left:7')

print('нет отметки ->', round_slot_left(KEY, 7), '(ждём 0)')
round_slot_mark(KEY, 7, every=90, what='пробе')
got = round_slot_left(KEY, 7)
print('есть отметка ->', got, '(ждём около 90)')
assert got == 0 or 80 <= got <= 90, got
r.delete('probe:round:left:7')
print('OK')
PY
python3 /tmp/round_slot_left_check.py
```

- [ ] **Step 3: Запустить и убедиться, что падает**

Run: `python3 /tmp/round_slot_left_check.py`
Expected: FAIL — `ImportError: cannot import name 'round_slot_left'`

- [ ] **Step 4: Написать функцию**

В `py_modules/round_/round_slot.py`, сразу после `round_slot_due` (перед
`round_slot_mark`):

```python
def round_slot_left(key: str, unit=None, what: str = '') -> int:
    """Сколько секунд до следующего захода этой единицы. `0` — можно сейчас.

    Args:
        key: ключ или шаблон с `{id}`.
        unit: единица обхода; `None` — ключ готов.
        what: чей слот — словом, для журнала («инвайтам», «ответам»).

    Returns:
        int: секунды до того, как отметка погаснет. `0` — отметки нет, круг
        возьмётся за единицу на ближайшем тике. `-1` — спросить не вышло.

    ⚠ **Это не `round_slot_due` с другим ответом.** Тот отвечает «да или нет» и
    при сбое общей памяти молчит в сторону «не пора» — лишний заход дороже
    пропущенного. Здесь ответ — число секунд, и сторона ошибки своя: `-1`,
    «врать сроком нельзя». Два разных вопроса — две функции с именами, а не одна
    с флагом.

    ⚠⚠ **Заведена потому, что её писали копиями, и копии ломались.** Два круга
    соседнего проекта держали её у себя дословно, и обе подставляли единицу в
    ключ сами — `KEY.format(site_id=…)` на шаблоне с меткой `{id}`. `.format`
    бросал `KeyError`, его глотал `except`, и счётчик «следующий заход через N»
    молча отвечал «спросить не вышло» на живой отметке со сроком 1767 секунд.

    Лекарство не в том, чтобы поправить обе, а в том, чтобы подставлять ключ
    стало неоткуда: здесь это делает `round_slot_key`, и метка у него одна на
    весь слой.
    """
    try:
        left = redis_conn_get().ttl(round_slot_key(key, unit))
    except Exception as e:
        logger_info(f'[{round_prefix()}] срок {what} не спросился: {e}')

        return -1

    # `-2` — ключа нет, `-1` — ключ есть, но без срока. Первое значит «пора»,
    # второе мы завести не можем (ставим только `setex`), но отвечать на него
    # сроком всё равно нельзя.
    return 0 if int(left) == -2 else max(0, int(left))
```

- [ ] **Step 5: Запустить проверку — проходит**

Run: `python3 /tmp/round_slot_left_check.py`
Expected: три строки и `OK`; `есть отметка -> 90 (ждём около 90)`

- [ ] **Step 6: Коммит в подмодуль**

```bash
git -C py_modules add round_/round_slot.py
git -C py_modules commit -m "round_slot: сколько секунд до следующего захода — одной функцией

Копий было две, в соседнем проекте, и обе ломались одинаково: ключ
подставляли сами, KEY.format(site_id=…) на шаблоне с меткой {id}.
KeyError глотал except, и счётчик молча отвечал -1 на живой отметке.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git -C py_modules push origin master
```

⚠ `push` обязателен и **раньше** коммита проекта: прод тянет подмодуль по
хэшу, и ссылка на непушнутый коммит роняет выкладку `ImportError`-ом.

---

### Task 2: Три счётчика через слой + регрессия

**Files:**
- Modify: `src/dating/favorite_mail_round.py:105-128`
- Modify: `src/dating/invite_round.py:143-168`
- Modify: `src/dating/reply_round.py` (дописать после `reply_round_run`)
- Create: `tests/test_round_next.py`

**Interfaces:**
- Consumes: `round_slot_left(key, unit, what) -> int` из Task 1.
- Produces: `reply_round_next(site_id: int) -> int`; `favorite_mail_round_next` и
  `invite_round_next` сохраняют прежние имена и прежний контракт.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_round_next.py`:

```python
"""Счётчик «следующий заход через N»: отвечает срок живой отметки, а не `-1`.

## ⚠⚠ Зачем этот файл

Две такие функции молчали месяцами, и молчали **правдоподобно**: каждая
подставляла единицу в ключ сама — `KEY.format(site_id=…)` на шаблоне с меткой
`{id}`. `.format` бросал `KeyError`, его ловил `except Exception` тремя строками
ниже, писал в журнал и возвращал `-1` — «спросить не вышло».

Снято на проде 11.10.2026:

    favorite_mail_round_next(343) = -1
    invite_round_next(343)        = -1
    ttl ключа как есть            = 1767

То есть страница не показывала срок **нигде** — ни у приставания, ни у инвайтов,
— и выглядело это как «данных нет», а не как поломка. Тест ставит отметку своими
руками и требует, чтобы её увидели: иначе третья копия сломается так же.
"""

import pytest

from redis_.redis_ import redis_conn_get
from round_.round_slot import round_slot_mark

from src.dating.favorite_mail_round import (FAVORITE_MAIL_ROUND_KEY,
                                            favorite_mail_round_next)
from src.dating.invite_round import INVITE_ROUND_KEY, invite_round_next
from src.dating.reply_round import REPLY_ROUND_KEY, reply_round_next

pytestmark = pytest.mark.mail

# Свой номер сайта, которого нет ни у кого: тест трогает общую память, и чужая
# отметка, снятая им, остановила бы живой круг на целый срок.
PROBE_SITE = 999_901

# Срок отметки. Достаточно большой, чтобы не истечь между двумя строками теста.
PROBE_EVERY = 120


@pytest.fixture
def slots():
    """Ставит отметки трёх кругов пробному сайту и убирает их за собой."""
    keys = (FAVORITE_MAIL_ROUND_KEY, INVITE_ROUND_KEY, REPLY_ROUND_KEY)

    for key in keys:
        round_slot_mark(key, PROBE_SITE, every=PROBE_EVERY, what='пробе')

    yield

    conn = redis_conn_get()
    for key in keys:
        conn.delete(key.replace('{id}', str(PROBE_SITE)))


class TestNextSeesTheMark:
    """Отметка стоит — счётчик называет её срок, а не «не вышло»."""

    def test_favorite_mail(self, slots):
        got = favorite_mail_round_next(PROBE_SITE)

        assert got > 0, f'счётчик приставания не увидел отметку: {got}'
        assert got <= PROBE_EVERY

    def test_invite(self, slots):
        got = invite_round_next(PROBE_SITE)

        assert got > 0, f'счётчик инвайтов не увидел отметку: {got}'
        assert got <= PROBE_EVERY

    def test_reply(self, slots):
        got = reply_round_next(PROBE_SITE)

        assert got > 0, f'счётчик ответов не увидел отметку: {got}'
        assert got <= PROBE_EVERY


class TestNextWithoutMark:
    """Отметки нет — это «пора», то есть ноль, а не отказ."""

    def test_no_mark_means_now(self):
        # ⚠ Сайт, которому отметку не ставили вовсе: `-2` от Redis обязан
        # читаться как «идти можно», иначе страница напишет «не вышло» там, где
        # круг возьмётся на ближайшем тике.
        assert favorite_mail_round_next(999_902) == 0
        assert invite_round_next(999_902) == 0
        assert reply_round_next(999_902) == 0
```

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_round_next.py -q`
Expected: FAIL. `TestNextSeesTheMark::test_favorite_mail` и `test_invite` — на
`assert got > 0` со значением `-1`; `test_reply` — на
`ImportError: cannot import name 'reply_round_next'`.

- [ ] **Step 3: Переписать `favorite_mail_round_next`**

В `src/dating/favorite_mail_round.py` заменить тело целиком (строки 105-128).
Импорт `redis_conn_get` после этого может остаться без пользователей — проверить
и убрать, если так.

```python
def favorite_mail_round_next(site_id: int) -> int:
    """Через сколько секунд круг вернётся к этому сайту. `0` — готов идти сейчас.

    ⚠ **Отвечает `TTL` отметки, а не наш расчёт.** Отметку ставит сам круг, гасит
    её Redis; считай мы «последний заход плюс срок», ответ разошёлся бы с
    действительностью в тот же час, когда настройку поправят на ходу.

    ⚠⚠ Ключ собирает **слой** (`round_slot_left` → `round_slot_key`), а не эта
    функция. Прежде она подставляла единицу сама — `.format(site_id=…)` на
    шаблоне с меткой `{id}`, — и ответ был `-1` всегда: `KeyError` глотал
    `except`. Счётчик на странице молчал месяцами (`tests/test_round_next.py`).
    """
    return round_slot_left(FAVORITE_MAIL_ROUND_KEY, int(site_id),
                           'письмам избранным')
```

Импорт дописать к существующему списку из `src.dating.round`:

```python
from src.dating.round import (round_sites, round_sites_run, round_slot_due,
                              round_slot_left, round_slot_mark)
```

⚠ Импорт идёт **через `src.dating.round`**, а не из слоя напрямую: там задаётся
слово журнала `[dating]`, и прямой импорт увёл бы строку в безымянный `[round]`.

- [ ] **Step 4: Добавить ре-экспорт в дверь слоя**

`src/dating/round.py` ре-экспортирует слой списком. Дописать туда новое имя:

```python
from round_.round_slot import (round_slot_due, round_slot_left, round_slot_mark,
                               round_slot_release)
```

и в `__all__`:

```python
__all__ = ['round_loop', 'round_offset_loop', 'round_mem_due', 'round_mem_mark',
           'round_slot_due', 'round_slot_left', 'round_slot_mark',
           'round_slot_release', 'round_sent_day', 'round_sites',
           'round_sites_run']
```

- [ ] **Step 5: Переписать `invite_round_next`**

В `src/dating/invite_round.py` заменить тело (строки 143-168), сохранив первую
половину докстринга — она объясняет, зачем функция заведена:

```python
def invite_round_next(site_id: int) -> int:
    """Через сколько секунд круг вернётся к этому сайту. `0` — готов идти сейчас.

    ⚠ **Отвечает `TTL` отметки, а не наш расчёт.** Отметку ставит сам круг и
    гасит её Redis; считай мы «последний заход плюс срок», ответ разошёлся бы с
    действительностью в тот же час, когда настройку поправят на ходу.

    Заведено для страницы: в шапке пула висело «по расписанию» — обещание без
    срока. Пул набирается сам, и человек, включивший генерацию, не знал, ждать ему
    минуту или полчаса; выглядело это как «включил, а оно не работает».

    ⚠⚠ Ключ собирает слой, а не эта функция: своя подстановка здесь и была той
    ошибкой, из-за которой обещание без срока так и осталось без срока
    (`tests/test_round_next.py`).
    """
    return round_slot_left(INVITE_ROUND_KEY, int(site_id), 'инвайтам')
```

Импорт — тем же способом, через `src.dating.round`.

- [ ] **Step 6: Завести `reply_round_next`**

В `src/dating/reply_round.py`, сразу после `reply_round_run` (перед `_gate`):

```python
def reply_round_next(site_id: int) -> int:
    """Через сколько секунд круг ответов вернётся к этому сайту. `0` — сейчас.

    Returns:
        int: секунды до следующего захода. `0` — отметки нет, круг возьмётся на
        ближайшем тике. `-1` — спросить не вышло, и врать сроком нельзя.

    ⚠ **Заведена ради окна «почему паре не пишут».** Между приходом письма и
    черновиком ответа лежит этот срок, и он же — главная доля ожидания: снято на
    проде 11.10.2026, из шести минут до ответа пять заняло ожидание захода, а
    вызов модели — шестнадцать секунд. Человеку у экрана это ожидание ничем не
    объяснялось.
    """
    return round_slot_left(REPLY_ROUND_KEY, int(site_id), 'ответам')
```

Импорт дополнить:

```python
from src.dating.round import (round_sites, round_sites_run, round_slot_due,
                              round_slot_left, round_slot_mark)
```

- [ ] **Step 7: Запустить тест — проходит**

Run: `pytest tests/test_round_next.py -q`
Expected: `5 passed`

- [ ] **Step 8: Коммит**

```bash
git add py_modules tests/test_round_next.py src/dating/round.py \
        src/dating/favorite_mail_round.py src/dating/invite_round.py \
        src/dating/reply_round.py
git commit -m "Круги: счётчик «следующий заход» ожил — ключ собирает слой

.format(site_id=…) на шаблоне с меткой {id} бросал KeyError, его глотал
except, и обе функции отвечали -1 на живой отметке. Срок на странице не
показывался ни у приставания, ни у инвайтов.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

⚠ `git add py_modules` обязателен: он двигает ссылку проекта на новый коммит
подмодуля. Без него прод возьмёт старый `round_slot.py` и упадёт `ImportError`.

---

### Task 3: `_too_soon` → `favorite_mail_person_soon`

**Files:**
- Modify: `src/dating/favorite_mail.py:339, 707, 954`

**Interfaces:**
- Produces: `favorite_mail_person_soon(site_id: int, uid: str, per_day: int) -> str`
  — почему этому человеку сейчас нельзя писать. Пустая строка — можно.

**Почему переименование.** Подчёркивание означает «внутреннее дело модуля», а
правило теперь нужно двоим: набору пула и окну. Рядом уже стоит публичная
`favorite_mail_girl_soon` с тем же устройством и противоположным ключом
(отправительница против получателя) — имена обязаны читаться парой.

- [ ] **Step 1: Переименовать определение**

`src/dating/favorite_mail.py:954` — заменить строку сигнатуры:

```python
async def favorite_mail_person_soon(site_id: int, uid: str, per_day: int) -> str:
```

Докстринг оставить как есть и дописать первой строкой после `Returns`:

```python
    ⚠ **Пара к `favorite_mail_girl_soon`, и ключи у них разные намеренно**: тот
    считает отправительницу, этот — получателя. Имена обязаны читаться парой,
    иначе следующий читатель решит, что одна из двух лишняя.
```

- [ ] **Step 2: Переименовать оба вызова**

`src/dating/favorite_mail.py:339` (внутри `favorite_mail_make`):

```python
        why = await favorite_mail_person_soon(site_id, uid, per_day)
```

`src/dating/favorite_mail.py:707` (внутри `favorite_mail_why`):

```python
        why = (await favorite_mail_person_soon(site_id, str(pair['uid']), per_day)
               or soon[profile_id_])
```

- [ ] **Step 3: Убедиться, что старого имени не осталось**

```bash
grep -rn "_too_soon" --include=*.py src/ tests/
```

Expected: пусто.

- [ ] **Step 4: Прогнать соседние тесты**

Run: `pytest tests/test_favorite_mail.py -q`
Expected: все зелёные (переименование поведения не меняет)

- [ ] **Step 5: Коммит**

```bash
git add src/dating/favorite_mail.py
git commit -m "Письма избранным: дневной предел получателя стал публичным именем

Правило нужно и набору пула, и окну «почему паре не пишут». Рядом уже
стоит favorite_mail_girl_soon с противоположным ключом — имена читаются
парой.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `mail_explain` — каркас, известна ли пара, общие запреты

**Files:**
- Create: `src/dating/mail_explain.py`
- Create: `tests/test_mail_explain.py`

**Interfaces:**
- Consumes: `favorite_mail_person_soon` (Task 3), `reply_round_next` (Task 2).
- Produces: `mail_explain(profile_id: int, uid: str, offset_hours: int = 0) -> dict`
  — поля `site_id`, `known`, `last`, `block`, `cooling_sec`. Части `reply` и
  `favorite` появятся в задачах 5-7.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_mail_explain.py`:

```python
"""Окно «почему этой паре не пишут письма»: считает ли оно то же, что круги.

## ⚠⚠ Главное здесь — сторож согласия, а не проверка полей

Занятий два, правил у них шестнадцать, и почти все невидимы: человек видит
молчащую переписку и не знает, упёрлась ли пара в дневной предел получателя, не
вышла ли пауза отправительницы, не закрылся ли человек или просто не идёт смена.
Окно отвечает на это — но ровно до тех пор, пока отвечает **теми же правилами**,
что и круги.

Отбор кругов (`favorite_mail._quiet`, `mail_reply._to_answer`) идёт по всем парам
сайта одним запросом; окно спрашивает про одну пару и выражено иначе. Свести их в
один запрос не выходит, и значит остаётся риск: правка в одном месте, молчание в
другом — и окно объясняет занятие, которого нет.

Поэтому `TestTheyAgree` (задача 8) гоняет **обе дороги по одним данным**. Та же
грабля в проекте уже случалась дважды — с `initiative_why` и со значком потолка.

⚠ База настоящая, сайт — стенд сюиты, ручки зовутся в процессе: тому же правилу
следуют `test_initiative_explain.py` и `test_favorite_mail.py`.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_explain import mail_explain

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


async def _said_in(profile_id: int, uid: str, *, minutes_ago: int = 60,
                   body: str = 'his letter') -> int:
    """Его входящее письмо. Возвращает номер строки `mail_in`."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in (site_id, profile_id, uid, name, body,'
            ' message_id, date_post)'
            ' VALUES (%s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
            (STAND_SITE, int(profile_id), str(uid), 'He',
             body, f'm-{uuid.uuid4().hex[:8]}', int(minutes_ago)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _star(profile_id: int, uid: str, state: str = 'done') -> None:
    """Звёздочка: пара в избранных."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, name, state)'
            ' VALUES (%s, %s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE state = VALUES(state)',
            (STAND_SITE, int(profile_id), str(uid), 'He', str(state)))
        await db.connection.commit()


async def _block(profile_id: int, uid: str, *, channel: str = 'all',
                 reason: str = 'он закрылся от вас') -> None:
    """Запрет «не пишем» на пару."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO block (site_id, profile_id, uid, name, reason, source,'
            ' channel) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (STAND_SITE, int(profile_id), str(uid), 'He', reason, 'test',
             str(channel)))
        await db.connection.commit()


class TestKnown:
    """Знает ли окно эту пару вообще."""

    async def test_unknown_profile_is_not_an_error(self):
        """Анкеты нет — `known: false` со словами, а не исключение.

        ⚠ Человек открывает окно у разговора, которого в наших таблицах может не
        быть вовсе. Падение здесь нарисовалось бы красным там, где всё в порядке.
        """
        got = await mail_explain(0, 'nobody')

        assert got == {}

    async def test_pair_without_letters_is_known(self, girl):
        """Анкета есть, писем нет — пара известна, просто пустая.

        ⚠ Отсутствие переписки **не** делает пару неизвестной: приставание ей
        законно (звёздочка сама означает, что разговор был), и окно обязано это
        показать, а не отговориться незнанием.
        """
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['known'] is True
        assert got['site_id'] == STAND_SITE
        assert got['last']['at'] is None
        assert got['last']['side'] == ''


class TestCommon:
    """Общее для обоих занятий: запрет, остуда, последняя реплика."""

    async def test_last_word_is_his(self, girl):
        """Его письмо — последняя реплика, и сторона названа."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, minutes_ago=30)

        got = await mail_explain(girl, uid)

        assert got['last']['side'] == 'him'
        assert got['last']['at'] is not None

    async def test_block_is_seen_with_mark_and_channel(self, girl):
        """Запрет виден приметой и каналом — по каналу решается охват."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _block(girl, uid, channel='mail',
                     reason='вы не подходите ему по возрасту')

        got = await mail_explain(girl, uid)

        assert got['block']['is'] is True
        assert got['block']['channel'] == 'mail'
        assert 'возраст' in got['block']['mark']

    async def test_no_block_is_silent(self, girl):
        """Запрета нет — поле пустое, а не отсутствующее."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['block'] == {'is': False, 'mark': '', 'channel': ''}
```

⚠ Таблицы `block` и `favorite` каскадом за анкетой **не уходят** — `favorite`
чистит фикстура `girl_make`, а `block` надо убрать за собой. Проверить колонку
`profile_id` в `block` и, если каскада нет, дописать `block` в
`TEST_SWEEP_BY_PROFILE` (`tests/conftest.py`) отдельным шагом задачи.

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_mail_explain.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.dating.mail_explain'`

- [ ] **Step 3: Дописать `block` в уборку сюиты**

Проверено: у `dating_block` внешнего ключа на анкету нет вовсе
(`migrations/20260913100000_dating_block.sql:35-54` — только `UNIQUE KEY` и
индексы), то есть каскад удаления анкеты её строки **не уносит**. В
`TEST_SWEEP_BY_PROFILE` (`tests/conftest.py:221`) таблицы нет.

Заменить строку 221-222 на:

```python
TEST_SWEEP_BY_PROFILE = ('scenario_run', 'stat_day', 'favorite',
                         # ⚠ У `block` ключа на анкету нет вовсе, каскад её строки
                         # не уносит — та же беда, из-за которой `favorite`
                         # накопила 825 строк за две недели при зелёной сюите.
                         'block',
                         'companion_gallery_file', 'gallery_file')
```

- [ ] **Step 4: Написать каркас модуля**

Создать `src/dating/mail_explain.py`:

```python
"""Почему этой паре не пишут письма: что мешает сейчас и когда снимется.

На «Чат → Диалоги» такое окно уже есть — про инициативу
(`initiative.initiative_explain`). Здесь то же самое и по той же причине, но про
почту: занятий два — **ответ на его письмо** и **приставание**, — правил у них
шестнадцать, и почти все невидимы.

## ⚠⚠ Почему это нужно именно окном

Снято с прода 11.10.2026. Собеседник написал анкете #44 в 08:32:30, ответ ушёл в
08:38:36. Шесть минут, из которых на модель ушло шестнадцать секунд: остальное —
два срока опроса (`mail_check_every`, `reply_every`), и **ни один из них на
странице не был виден**. Молчащая переписка неотличима от сломанной, и человек у
экрана идёт искать поломку там, где всё работает ровно так, как настроено.

## ⚠ Считаем теми же выражениями, что и круги

Готовое берём как есть: `favorite_mail_girl_soon`, `favorite_mail_person_soon`,
`favorite_mail_shift_site`, `mail_wait_left`, `online_show`, сроки кругов.

Цена, которую платим осознанно: отбор кругов (`favorite_mail._quiet`,
`mail_reply._to_answer`) идёт по всем парам сайта одним запросом и выражен иначе
— для одной пары условия переписаны здесь. Ровно та же цена, что уже заплачена в
`initiative_explain`, и платится она тем же способом: сторожем согласия в
`tests/test_mail_explain.py`, а не внимательностью.

## ⚠ Чего здесь нет намеренно

**Минуты отправки.** Письма уносит общий круг отправки со своей паузой на весь
сайт, и очередь делится со всеми анкетами: назвать минуту значило бы соврать
точностью. Говорим «когда пара станет годной» — это считается честно.

**Возрастного окна.** `mail_age` спрашивается только у рассылки, у холодного
обращения. Избранному анкета писала первой, он отвечал, и критерий к этой паре не
применяется вовсе.

**`send_why` целиком.** Она отвечает про **сайт** («пул пуст», «все письма с
ошибкой»), а окно спрашивает про пару: подмешай мы её строку, человек получил бы
ответ про чужое письмо.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.mail_send import mail_sent_union
from src.dating.mail_wait import mail_wait_left


async def mail_explain(profile_id: int, uid: str,
                       offset_hours: int = 0) -> dict:
    """Всё о почте одной пары: что уйдёт следующим, что мешает и когда снимется.

    Args:
        profile_id: наша анкета.
        uid: собеседник на сайте.
        offset_hours: часовое смещение панели — смены заданы местным временем.

    Returns:
        dict: пустой, если пары не существует вовсе; иначе `known: True` и
        разбор по занятиям. Поля — в спеке
        `docs/superpowers/specs/2026-10-11-mail-explain-design.md`.

    ⚠ Пустой словарь, а не исключение: человек открывает окно у разговора,
    которого в наших таблицах может не быть. Ручка превращает это в
    `known: false` со словами, а не в 404 — 404 страница нарисовала бы красным.
    """
    profile_id, uid = int(profile_id or 0), str(uid or '')
    if not profile_id or not uid:
        return {}

    pair = await _pair(profile_id, uid)
    if not pair:
        return {}

    site_id = int(pair['site_id'])
    last = await _last(profile_id, uid)

    return {
        'site_id': site_id,
        'known': True,
        'last': last,
        'block': {'is': bool(pair.get('block_mark') is not None),
                  'mark': str(pair.get('block_mark') or ''),
                  'channel': str(pair.get('block_channel') or '')},
        # Остуда — **Redis, а не база**: сайт сам сказал, сколько ждать, и срок
        # гаснет сам. Ноль значит «можно писать», а не «не знаем».
        'cooling_sec': mail_wait_left(site_id, uid),
    }


# ── Приватное ────────────────────────────────────────────────────────────────

async def _pair(profile_id: int, uid: str) -> dict:
    """Анкета, звёздочка и запрет — одним запросом.

    ⚠ **Один запрос, а не три.** Окно открывают щелчком, и три похода в базу
    ради трёх полей — это три разных мгновения: запрет мог появиться между
    первым и третьим.

    ⚠ `block` приклеивается `LEFT JOIN`-ом без отбора по каналу: охват решает
    вызывающий, глядя на `channel`. Отсеки мы здесь — окно ответа молчало бы про
    запрет, который к ответам и правда не относится, но знать о котором человеку
    надо.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT p.id AS profile_id, p.site_id, p.enabled, p.status,'
                '       f.state AS star_state,'
                '       b.reason AS block_mark, b.channel AS block_channel'
                '  FROM profile p'
                '  LEFT JOIN favorite f'
                '    ON f.profile_id = p.id AND f.uid = %s'
                '  LEFT JOIN block b'
                '    ON b.profile_id = p.id AND b.uid = %s'
                ' WHERE p.id = %s LIMIT 1',
                (str(uid), str(uid), int(profile_id)))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] пара #{profile_id} → {uid} не спросилась: {e}')

        return {}

    return dict(row or {})


async def _last(profile_id: int, uid: str) -> dict:
    """Последняя реплика **по всей почте** пары: когда и чья.

    ⚠ **По всей почте, а не по одному источнику** — тем же перечислением, каким
    считает отбор круга (`favorite_mail._quiet`): его входящие плюс три наших
    пула ушедшего. Смотри мы только входящие, затихшим выглядел бы всякий, кому
    мы писали последними, включая того, кому письмо ушло пять минут назад.

    ⚠ `side` пустой — «реплик нет вовсе». Это законный случай: звёздочку ставят
    и тому, с кем разговор шёл на сайте задолго до панели.
    """
    sql = ("SELECT MAX(at) AS last_at,"
           "       SUBSTRING_INDEX(MAX(CONCAT(at, %s, side)), %s, -1) AS last_side"
           '  FROM ('
           "    SELECT date_post AS at, 'him' AS side FROM mail_in"
           '     WHERE profile_id = %s AND uid = %s'
           '    UNION ALL '
           + mail_sent_union("profile_id = %s AND uid = %s AND status = 'sent'",
                             select="sent_at AS at, 'her' AS side") +
           '  ) AS all_mail WHERE at IS NOT NULL')

    args = [' ', ' ', int(profile_id), str(uid)]
    # ⚠ Условие подставляется **в каждую** ветку союза, поэтому довода втрое
    # больше: так же считает их `_too_soon` и пауза отправительницы.
    args += [int(profile_id), str(uid)] * 3

    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, tuple(args))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] последняя реплика пары #{profile_id} '
                    f'не спросилась: {e}')

        return {'at': None, 'side': ''}

    return {'at': (row or {}).get('last_at'),
            'side': str((row or {}).get('last_side') or '')}
```

- [ ] **Step 5: Запустить — проходит**

Run: `pytest tests/test_mail_explain.py -q`
Expected: `5 passed`

- [ ] **Step 6: Коммит**

```bash
git add src/dating/mail_explain.py tests/test_mail_explain.py tests/conftest.py
git commit -m "Почта: каркас окна «почему этой паре не пишут»

Пара, последняя реплика по всей почте, запрет и остуда. Занятия —
следующими задачами.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Часть «Приставание» — одиннадцать помех

**Files:**
- Modify: `src/dating/mail_explain.py`
- Modify: `tests/test_mail_explain.py`

**Interfaces:**
- Consumes: `_pair`, `_last` (Task 4); `favorite_mail_girl_soon`,
  `favorite_mail_person_soon`, `favorite_mail_gap_sec`, `favorite_mail_shift_site`,
  `FAVORITE_MAIL_PER_DAY_SETTING`, `FAVORITE_MAIL_PER_DAY_DEFAULT`,
  `favorite_mail_round_next`, `FAVORITE_STATES`, `online_show`, `mail_wait_skip`.
- Produces: ключ `favorite` в ответе `mail_explain` — поля `make_on`, `send_on`,
  `star`, `star_state`, `shift_now`, `every_sec`, `next_sec`, `gap_sec`,
  `per_day`, `day_sent`, `him_online`, `him_known`, `draft`, `blockers`, `wait_sec`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_mail_explain.py`:

```python
async def _site_flag(name: str, value: int) -> None:
    """Выключатель занятия у сайта стенда."""
    async with mysql_get_db_async() as db:
        await db.execute(f'UPDATE site SET {name} = %s WHERE site_id = %s',
                         (int(value), STAND_SITE))
        await db.connection.commit()


@pytest.fixture
async def fav_on():
    """Приставание включено и смена идёт круглые сутки — убирается за собой.

    ⚠ Расписание ставится **целыми сутками**: без смены круг не наберёт ничего,
    и каждая проверка помехи ловила бы её вместо своей.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE site SET favorite_mail_make_on = 1, favorite_mail_send_on = 1,'
            ' favorite_mail_schedule = %s, mailing_schedule = %s'
            ' WHERE site_id = %s',
            ('[{"from": "00:00", "to": "23:59"}]',
             '[{"from": "00:00", "to": "23:59"}]', STAND_SITE))
        await db.connection.commit()

    yield

    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE site SET favorite_mail_make_on = 0, favorite_mail_send_on = 0,'
            " favorite_mail_schedule = NULL, mailing_schedule = NULL"
            ' WHERE site_id = %s', (STAND_SITE,))
        await db.connection.commit()


def _has(got: dict, part: str, needle: str) -> bool:
    """Есть ли среди помех занятия строка с этими словами."""
    return any(needle in one for one in got[part]['blockers'])


class TestFavoriteBlockers:
    """Одиннадцать помех приставания — в порядке, в каком их проверяет круг."""

    async def test_make_off(self, girl, fav_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _site_flag('favorite_mail_make_on', 0)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'набор писем избранным выключен')

    async def test_not_a_shift(self, girl, fav_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        async with mysql_get_db_async() as db:
            # Смена в прошлом: пять минут суток, которых сейчас точно нет.
            await db.execute(
                'UPDATE site SET favorite_mail_schedule = %s WHERE site_id = %s',
                ('[{"from": "00:00", "to": "00:01"}]', STAND_SITE))
            await db.connection.commit()

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'не смена')

    async def test_no_star(self, girl, fav_on):
        """Звёздочки нет — приставание не про эту пару."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['favorite']['star'] is False
        assert _has(got, 'favorite', 'нет в избранных')

    async def test_girl_offline(self, girl, fav_on):
        """Анкета не в сети: письмо уходит от её имени, и отправка её ждёт."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'анкета не в сети')

    async def test_he_spoke_last(self, girl_make, fav_on):
        """Последним говорил он — отвечать должен круг ответов, не этот."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _said_in(girl, uid, minutes_ago=10)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'последним говорил он')

    async def test_draft_already_waits(self, girl_make, fav_on):
        """У анкеты уже лежит черновик — второй был бы вторым письмом подряд.

        ⚠ Условие **по анкете**, а не по паре: черновик занимает ближайшую
        очередь девушки целиком.
        """
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _fav_draft(girl, uid)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'уже лежит черновик')

    async def test_block_stops_favorite(self, girl_make, fav_on):
        """Запрет «не пишем» гасит приставание и называет примету."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _block(girl, uid, channel='all')

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'запрещено')
        assert _has(got, 'favorite', 'он закрылся от вас')

    async def test_he_is_offline(self, girl_make, fav_on):
        """Его нет в сети — и оговорка, что ленту могли не снимать.

        ⚠ «Не знаем» здесь читается как «не сейчас», в отличие от чата: письмо
        стоит вызова модели и одного из двух писем в сутки.
        """
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)

        got = await mail_explain(girl, uid)

        assert got['favorite']['him_online'] is False
        assert _has(got, 'favorite', 'нет в сети')

    async def test_cooling(self, girl_make, fav_on, cooling):
        """Сайт сам просит подождать — предел входящих у получателя."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        cooling(uid, 1800)

        got = await mail_explain(girl, uid)

        assert got['cooling_sec'] > 0
        assert _has(got, 'favorite', 'предел входящих')

    async def test_person_day_cap(self, girl_make, fav_on):
        """Дневной предел считает **получатель**, а не пара.

        ⚠ Письмо шлёт другая анкета — и всё равно считается: пока ключом был
        `profile_id`, тринадцать девушек давали одному человеку до 26 писем.
        """
        girl = await girl_make(online=True)
        other = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        for _ in range(2):
            await _fav_sent(other, uid, minutes_ago=600)

        got = await mail_explain(girl, uid)

        assert got['favorite']['day_sent'] >= 2
        assert _has(got, 'favorite', 'за сутки')

    async def test_girl_gap(self, girl_make, fav_on):
        """Пауза считает **отправительницу**: кому она писала — неважно."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _fav_sent(girl, f'other-{uuid.uuid4().hex[:8]}', minutes_ago=5)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'пауза')


class TestFavoriteNotBlockers:
    """Чего в помехах быть не должно."""

    async def test_round_pass_is_not_a_blocker(self, girl_make, fav_on):
        """Срок круга — строка «вернётся через N», а не помеха.

        ⚠ Помеха — то, что мешает паре быть годной. Срок круга мешает не паре, а
        всем сразу, и назови мы его помехой — человек стал бы искать, что он
        нарушил.
        """
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)

        got = await mail_explain(girl, uid)

        assert not _has(got, 'favorite', 'вернётся через')
        assert isinstance(got['favorite']['next_sec'], int)

    async def test_batch_rules_are_absent(self, girl_make, fav_on):
        """Внутрисходовых правил в окне нет вовсе.

        ⚠⚠ «Ему уже пишет другая анкета этим заходом» и «эта анкета уже написала
        этим заходом» — про **заход**, а не про пару: к следующему заходу их нет.
        Назови их окно помехой, человек ждал бы снятия того, что уже снято.
        """
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)

        got = await mail_explain(girl, uid)

        assert not _has(got, 'favorite', 'этим заходом')


class TestOrder:
    """Помехи идут в том же порядке, в каком их проверяет круг.

    ⚠⚠ **Порядок — не украшение.** Человек читает первую строку и по ней решает,
    ждать ему или править настройку. Встань преходящая помеха («он не в сети»,
    пройдёт за минуту) выше решённой человеком («набор выключен»), и он пошёл бы
    ждать вместо того, чтобы поднять выключатель.
    """

    async def test_switch_stands_above_online(self, girl, fav_on):
        """Выключатель выше «его нет в сети»: первое решает человек, второе — время."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _site_flag('favorite_mail_make_on', 0)

        rows = (await mail_explain(girl, uid))['favorite']['blockers']
        where = {'выключен': -1, 'нет в сети': -1}
        for i, one in enumerate(rows):
            for word in where:
                if word in one and where[word] < 0:
                    where[word] = i

        assert where['выключен'] >= 0 and where['нет в сети'] >= 0
        assert where['выключен'] < where['нет в сети']

    async def test_star_stands_above_limits(self, girl_make, fav_on):
        """Звёздочка выше пределов: без неё занятие к паре не относится вовсе."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        # Звёздочки нет — и при этом выбран дневной предел получателя.
        for _ in range(2):
            await _fav_sent(girl, uid, minutes_ago=600)

        rows = (await mail_explain(girl, uid))['favorite']['blockers']
        star = next(i for i, one in enumerate(rows) if 'в избранных' in one)
        day = next(i for i, one in enumerate(rows) if 'за сутки' in one)

        assert star < day
```

Дописать туда же недостающие помощники и фикстуру остуды:

```python
async def _fav_draft(profile_id: int, uid: str) -> int:
    """Черновик приставания в пуле."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status) VALUES (%s, %s, %s, %s, %s, 'draft')",
            (STAND_SITE, int(profile_id), str(uid), 'He', 'draft body'))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _fav_sent(profile_id: int, uid: str, *, minutes_ago: int = 10) -> None:
    """Ушедшее приставание — им считаются оба предела."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status, sent_at)"
            " VALUES (%s, %s, %s, %s, %s, 'sent', NOW() - INTERVAL %s MINUTE)",
            (STAND_SITE, int(profile_id), str(uid), 'He', 'sent body',
             int(minutes_ago)))
        await db.connection.commit()


@pytest.fixture
def cooling():
    """Ставит остуду получателю и снимает её за собой.

    ⚠ Остуда живёт в Redis, а не в базе: каскад анкеты её не унесёт, и чужой
    прогон увидел бы её как настоящую.
    """
    from src.dating.mail_wait import MAIL_WAIT_KEY
    from redis_.redis_ import redis_conn_get
    import time

    marked = []

    def _set(uid: str, seconds: int) -> None:
        redis_conn_get().zadd(MAIL_WAIT_KEY.format(site_id=STAND_SITE),
                              {str(uid): time.time() + int(seconds)})
        marked.append(str(uid))

    yield _set

    if marked:
        redis_conn_get().zrem(MAIL_WAIT_KEY.format(site_id=STAND_SITE), *marked)
```

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_mail_explain.py -q -k Favorite`
Expected: FAIL, `KeyError: 'favorite'`

- [ ] **Step 3: Написать часть «приставание»**

В `src/dating/mail_explain.py` дописать импорты:

```python
from src.dating.favorite import FAVORITE_STATES
from src.dating.favorite_mail import (FAVORITE_MAIL_PER_DAY_DEFAULT,
                                      FAVORITE_MAIL_PER_DAY_SETTING,
                                      favorite_mail_gap_sec,
                                      favorite_mail_girl_soon,
                                      favorite_mail_person_soon,
                                      favorite_mail_shift_site)
from src.dating.favorite_mail_round import (FAVORITE_MAIL_ROUND_EVERY_DEFAULT,
                                            FAVORITE_MAIL_ROUND_EVERY_SETTING,
                                            favorite_mail_round_next)
from src.dating.mail_wait import mail_wait_left, mail_wait_skip
from src.dating.online import online_show
from src.dating.site_setting import site_seconds
```

и функцию:

```python
async def _favorite(site_id: int, profile_id: int, uid: str, pair: dict,
                    last: dict, flags: dict, cooling: int,
                    offset_hours: int) -> dict:
    """Что с приставанием у этой пары: помехи в порядке проверки круга.

    ⚠⚠ **Порядок — тот же, что у круга**, от решённого человеком к преходящему:
    выключатель → смена → звёздочка → анкета в сети → чьё последнее слово →
    черновик → запрет → он в сети → остуда → дневной предел получателя → пауза
    отправительницы. Переставь их — и окно назовёт главной ту помеху, что пройдёт
    сама через минуту.

    ⚠ Двух внутрисходовых правил здесь нет намеренно: «ему уже пишет другая
    анкета этим заходом» и «эта анкета уже написала этим заходом» — про заход, а
    не про пару.
    """
    gap = await favorite_mail_gap_sec(site_id)
    per_day = int(await site_seconds(site_id, FAVORITE_MAIL_PER_DAY_SETTING)
                  or FAVORITE_MAIL_PER_DAY_DEFAULT)
    online = online_show(site_id, uid)
    star_state = str(pair.get('star_state') or '')
    star = star_state in FAVORITE_STATES
    shift = await favorite_mail_shift_site(site_id, offset_hours)
    draft = await _draft(site_id, 'favorite_mail', profile_id, uid)
    # ⚠ Черновик пары и черновик **анкеты** — разные вопросы: очередь девушки
    # занимает любой её черновик, кому бы он ни был написан.
    girl_draft = await _girl_draft(profile_id)
    person = await favorite_mail_person_soon(site_id, uid, per_day)
    girl_soon = await favorite_mail_girl_soon(profile_id, gap)
    day_sent = await _day_sent(site_id, uid)

    blockers = []
    if not flags.get('favorite_mail_make_on'):
        blockers.append('набор писем избранным выключен у сайта')
    if not shift:
        blockers.append('сейчас не смена: письма сочиняются по часам, '
                        'заданным у сайта')
    if not star:
        blockers.append('его нет в избранных этой анкеты')
    if str(pair.get('status') or '') != 'online':
        blockers.append('анкета не в сети — письмо уходит от её имени')
    if str(last.get('side') or '') == 'him':
        blockers.append('последним говорил он — ответить должен круг ответов')
    if girl_draft:
        blockers.append('у этой анкеты уже лежит черновик приставания')
    if pair.get('block_mark') is not None:
        blockers.append(f'паре запрещено писать: {pair["block_mark"]}')
    if not online.get('online'):
        blockers.append('его нет в сети'
                        + ('' if online.get('known') else ' (ленту ещё не снимали)'))
    if cooling:
        blockers.append(mail_wait_skip(cooling))
    if person:
        blockers.append(person)
    if girl_soon:
        blockers.append(girl_soon)

    return {
        'make_on': bool(flags.get('favorite_mail_make_on')),
        'send_on': bool(flags.get('favorite_mail_send_on')),
        'star': star, 'star_state': star_state,
        'shift_now': bool(shift),
        'every_sec': int(await site_seconds(site_id,
                                            FAVORITE_MAIL_ROUND_EVERY_SETTING)
                         or FAVORITE_MAIL_ROUND_EVERY_DEFAULT),
        'next_sec': favorite_mail_round_next(site_id),
        'gap_sec': gap, 'per_day': per_day, 'day_sent': day_sent,
        'him_online': bool(online.get('online')),
        'him_known': bool(online.get('known')),
        'draft': draft,
        'blockers': blockers,
        # Отправка — своей частью: задача 7.
        'send_blockers': [], 'wait_sec': 0,
    }
```

плюс три помощника:

```python
async def _draft(site_id: int, table: str, profile_id: int, uid: str) -> dict | None:
    """Черновик или последняя строка пары в этом пуле. `None` — строки нет.

    ⚠ Имя таблицы уезжает в SQL текстом — параметром его не передать. Приходит
    оно из констант этого модуля, снаружи взяться неоткуда.
    """
    if table not in ('favorite_mail', 'mail_reply'):
        return None

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                f'SELECT id, status, error, created_at, sent_at FROM {table}'
                ' WHERE site_id = %s AND profile_id = %s AND uid = %s'
                ' ORDER BY id DESC LIMIT 1',
                (int(site_id), int(profile_id), str(uid)))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] черновик пары #{profile_id} не спросился: {e}')

        return None

    if not row:
        return None

    return {'id': int(row['id']), 'status': str(row['status'] or ''),
            'error': str(row['error'] or ''),
            'at': row['sent_at'] or row['created_at']}


async def _girl_draft(profile_id: int) -> bool:
    """Лежит ли у анкеты **любой** черновик приставания.

    ⚠ По анкете, а не по паре: пауза считает отправительницу, и лежащий черновик
    занимает её ближайшую очередь целиком — всё набранное сверх него пролежит до
    вечера и протухнет. То же условие, что в `favorite_mail._quiet`.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT 1 FROM favorite_mail'
                " WHERE profile_id = %s AND status = 'draft' LIMIT 1",
                (int(profile_id),))

            return bool(await db.fetchone())
    except Exception as e:
        logger_info(f'[dating] черновики анкеты #{profile_id} не спросились: {e}')

        return False


async def _day_sent(site_id: int, uid: str) -> int:
    """Сколько писем ушло этому человеку за скользящие сутки — по всему сайту.

    ⚠ Ключ — `site_id`, а не анкета: предел на человека. Тем же перечислением
    таблиц, каким считает его `favorite_mail_person_soon`.
    """
    from src.dating.mail_send import MAIL_SEND_TABLES

    sql = ('SELECT COUNT(*) AS n FROM ('
           + mail_sent_union("site_id = %s AND uid = %s AND status = 'sent'")
           + ') AS sent'
             ' WHERE sent_at IS NOT NULL AND sent_at > NOW() - INTERVAL 1 DAY')

    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, (int(site_id), str(uid)) * len(MAIL_SEND_TABLES))

            return int(((await db.fetchone()) or {}).get('n') or 0)
    except Exception as e:
        logger_info(f'[dating] счёт писем {uid} не спросился: {e}')

        return 0
```

и чтение выключателей сайта:

```python
async def _flags(site_id: int) -> dict:
    """Выключатели занятий и расписание отправки — одним запросом."""
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT favorite_mail_make_on, favorite_mail_send_on,'
                ' reply_make_on, reply_send_on, mailing_schedule'
                ' FROM site WHERE site_id = %s', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] выключатели сайта #{site_id} не спросились: {e}')

        return {}

    return dict(row or {})
```

Наконец, заменить `return` в `mail_explain` целиком:

```python
    site_id = int(pair['site_id'])
    last = await _last(profile_id, uid)
    flags = await _flags(site_id)
    # ⚠ Остуда спрашивается **один раз**: её читают обе части, и два чтения Redis
    # ради одного числа дали бы два разных мгновения.
    cooling = mail_wait_left(site_id, uid)

    return {
        'site_id': site_id,
        'known': True,
        'last': last,
        'block': {'is': bool(pair.get('block_mark') is not None),
                  'mark': str(pair.get('block_mark') or ''),
                  'channel': str(pair.get('block_channel') or '')},
        'cooling_sec': cooling,
        'favorite': await _favorite(site_id, profile_id, uid, pair, last,
                                    flags, cooling, offset_hours),
    }
```

- [ ] **Step 4: Запустить — проходит**

Run: `pytest tests/test_mail_explain.py -q`
Expected: `20 passed` (5 из задачи 4 + 15 новых)

- [ ] **Step 5: Коммит**

```bash
git add src/dating/mail_explain.py tests/test_mail_explain.py
git commit -m "Окно почты: часть «приставание» — одиннадцать помех в порядке круга

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Часть «Ответ на его письмо» — пять помех

**Files:**
- Modify: `src/dating/mail_explain.py`
- Modify: `tests/test_mail_explain.py`

**Interfaces:**
- Consumes: `_pair`, `_flags`, `_draft` (задачи 4-5); `reply_round_next`,
  `REPLY_EVERY_SETTING`, `REPLY_EVERY_DEFAULT`.
- Produces: ключ `reply` — поля `make_on`, `send_on`, `every_sec`, `next_sec`,
  `his_last`, `draft`, `blockers`, `send_blockers`, `wait_sec`.

- [ ] **Step 1: Написать падающие тесты**

Дописать в `tests/test_mail_explain.py`:

```python
@pytest.fixture
async def reply_on():
    """Генерация и отправка ответов включены — убирается за собой."""
    await _site_flag('reply_make_on', 1)
    await _site_flag('reply_send_on', 1)

    yield

    await _site_flag('reply_make_on', 0)
    await _site_flag('reply_send_on', 0)


async def _reply_draft(profile_id: int, uid: str, mail_in_id: int, *,
                       error: str = '') -> int:
    """Черновик ответа в пуле."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid, name,'
            " body, status, error) VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s)",
            (STAND_SITE, int(profile_id), int(mail_in_id), str(uid), 'He',
             'reply body', str(error)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


class TestReplyBlockers:
    """Пять помех ответа — в порядке `mail_reply._to_answer`."""

    async def test_make_off(self, girl, reply_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid)
        await _site_flag('reply_make_on', 0)

        got = await mail_explain(girl, uid)

        assert _has(got, 'reply', 'генерация ответов выключена')

    async def test_profile_disabled(self, girl, reply_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid)
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET enabled = 0 WHERE id = %s',
                             (girl,))
            await db.connection.commit()

        got = await mail_explain(girl, uid)

        assert _has(got, 'reply', 'анкета выключена')

    async def test_he_never_wrote(self, girl, reply_on):
        """Писем от него нет — отвечать не на что, и это не поломка."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['reply']['his_last'] is None
        assert _has(got, 'reply', 'не писал ни разу')

    async def test_answer_already_written(self, girl, reply_on):
        """На последнее письмо ответ уже есть — второй был бы двумя подряд."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)

        assert _has(got, 'reply', 'ответ уже написан')

    async def test_empty_body(self, girl, reply_on):
        """Тело письма ещё не забрали — отвечать нечем."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='')

        got = await mail_explain(girl, uid)

        assert got['reply']['his_last']['empty'] is True
        assert _has(got, 'reply', 'нет текста')

    async def test_ripe_pair_has_no_blockers(self, girl, reply_on):
        """Письмо есть, текст есть, ответа нет — помех быть не должно."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='his letter')

        got = await mail_explain(girl, uid)

        assert got['reply']['blockers'] == []
```

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_mail_explain.py -q -k Reply`
Expected: FAIL, `KeyError: 'reply'`

- [ ] **Step 3: Написать часть «ответ»**

Импорты:

```python
from src.dating.reply_round import (REPLY_EVERY_DEFAULT, REPLY_EVERY_SETTING,
                                    reply_round_next)
```

Функция:

⚠ Сигнатура сразу **конечная** — с `cooling` и `offset_hours`: задача 7 допишет
ей отправку, и менять сигнатуру задним числом значило бы править уже проверенный
вызов. Оба довода в этой задаче ещё не используются, и это нормально.

```python
async def _reply(site_id: int, profile_id: int, uid: str, pair: dict,
                 flags: dict, cooling: int, offset_hours: int) -> dict:
    """Что с ответом на его письмо: помехи в порядке `mail_reply._to_answer`.

    ⚠ **Срок круга помехой не считается.** Он мешает не паре, а всем сразу, и
    назови мы его помехой — человек стал бы искать, что эта пара нарушила.
    Срок идёт своим полем `next_sec`.
    """
    his = await _his_last(profile_id, uid)
    draft = await _draft(site_id, 'mail_reply', profile_id, uid)
    answered = await _answered(profile_id, uid, his)

    blockers = []
    if not flags.get('reply_make_on'):
        blockers.append('генерация ответов выключена у сайта')
    if not int(pair.get('enabled') or 0):
        blockers.append('анкета выключена')
    if not his:
        blockers.append('он не писал ни разу — отвечать не на что')
    elif answered:
        blockers.append('на его последнее письмо ответ уже написан')
    elif his.get('empty'):
        blockers.append('у письма нет текста — тело ещё не забрали с сайта')

    return {
        'make_on': bool(flags.get('reply_make_on')),
        'send_on': bool(flags.get('reply_send_on')),
        'every_sec': int(await site_seconds(site_id, REPLY_EVERY_SETTING)
                         or REPLY_EVERY_DEFAULT),
        'next_sec': reply_round_next(site_id),
        'his_last': his, 'draft': draft,
        'blockers': blockers,
        # Отправка — своей частью: задача 7.
        'send_blockers': [], 'wait_sec': 0,
    }


async def _his_last(profile_id: int, uid: str) -> dict | None:
    """Его последнее письмо: номер, время и есть ли у него текст.

    ⚠ **Последнее, а не каждое.** Он мог написать три подряд, не дождавшись
    ответа; отвечает круг на последнее, прежние идут в историю. Тот же порядок,
    что у `_to_answer`: по `date_post`, при равенстве — по номеру.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id, body, date_post FROM mail_in'
                ' WHERE profile_id = %s AND uid = %s'
                ' ORDER BY date_post DESC, id DESC LIMIT 1',
                (int(profile_id), str(uid)))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] письма пары #{profile_id} не спросились: {e}')

        return None

    if not row:
        return None

    return {'id': int(row['id']), 'at': row['date_post'],
            'empty': not str(row['body'] or '').strip()}


async def _answered(profile_id: int, uid: str, his: dict | None) -> bool:
    """Есть ли уже ответ на **это** письмо.

    ⚠ Ключ — `mail_in_id`, а не пара: ответ один на письмо, и уникальный ключ
    `mail_reply` стоит именно на нём. Спроси мы «есть ли ответ этой паре», старый
    ответ на позапрошлое письмо запретил бы отвечать на новое.
    """
    if not his:
        return False

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT 1 FROM mail_reply WHERE mail_in_id = %s'
                             ' LIMIT 1', (int(his['id']),))

            return bool(await db.fetchone())
    except Exception as e:
        logger_info(f'[dating] ответ на письмо #{his["id"]} не спросился: {e}')

        return False
```

Вшить в `mail_explain` рядом с `favorite`:

```python
        'reply': await _reply(site_id, profile_id, uid, pair, flags, cooling,
                              offset_hours),
```

- [ ] **Step 4: Запустить — проходит**

Run: `pytest tests/test_mail_explain.py -q`
Expected: `26 passed` (20 + 6 новых)

- [ ] **Step 5: Коммит**

```bash
git add src/dating/mail_explain.py tests/test_mail_explain.py
git commit -m "Окно почты: часть «ответ на его письмо» — пять помех

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Отправка и «что уйдёт следующим»

**Files:**
- Modify: `src/dating/mail_explain.py`
- Modify: `tests/test_mail_explain.py`

**Interfaces:**
- Consumes: `_favorite`, `_reply`, `_flags` (задачи 5-6); `schedule_now`,
  `json_column_list`, `datetime_day_minutes`.
- Produces: `send_blockers` и `wait_sec` у обоих занятий; верхние `next` и
  `wait_sec`.

- [ ] **Step 1: Написать падающие тесты**

```python
class TestSendBlockers:
    """Четыре помехи отправки — у каждого занятия своя часть."""

    async def test_send_off(self, girl, reply_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)
        await _site_flag('reply_send_on', 0)

        got = await mail_explain(girl, uid)

        assert any('отправка ответов выключена' in one
                   for one in got['reply']['send_blockers'])

    async def test_not_a_send_shift(self, girl, reply_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE site SET mailing_schedule = %s WHERE site_id = %s',
                ('[{"from": "00:00", "to": "00:01"}]', STAND_SITE))
            await db.connection.commit()

        got = await mail_explain(girl, uid)

        assert any('не смена' in one for one in got['reply']['send_blockers'])

    async def test_draft_with_error_is_not_taken(self, girl, reply_on, day_long):
        """Письмо с прежней ошибкой круг не берёт — его досылают кнопкой."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter, error='сайт отказал')

        got = await mail_explain(girl, uid)

        assert any('прежняя ошибка' in one
                   for one in got['reply']['send_blockers'])

    async def test_cooling_stops_sending(self, girl, reply_on, day_long, cooling):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)
        cooling(uid, 900)

        got = await mail_explain(girl, uid)

        assert any('предел входящих' in one
                   for one in got['reply']['send_blockers'])


class TestWhatIsNext:
    """Что уйдёт паре следующим — считает сервер, а не страница."""

    async def test_reply_draft_wins(self, girl, reply_on, day_long):
        """Черновик ответа лежит — следующим уйдёт он."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)

        assert got['next'] == 'reply'

    async def test_nothing_when_both_blocked(self, girl):
        """Оба занятия упёрлись — честное «нечего», а не выдуманное занятие."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['next'] == ''
```

Плюс фикстура расписания отправки, общая для этих проверок:

```python
@pytest.fixture
async def day_long():
    """Смена отправки — круглые сутки. Иначе её отсутствие ловится первой."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE site SET mailing_schedule = %s WHERE site_id = %s',
            ('[{"from": "00:00", "to": "23:59"}]', STAND_SITE))
        await db.connection.commit()

    yield

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET mailing_schedule = NULL'
                         ' WHERE site_id = %s', (STAND_SITE,))
        await db.connection.commit()
```

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_mail_explain.py -q -k "Send or Next"`
Expected: FAIL — `send_blockers` пуст, `KeyError: 'next'`

- [ ] **Step 3: Написать отправку и выбор следующего**

Импорты:

```python
from datetime_.datetime_ import datetime_day_minutes

from src.dating.json_column import json_column_list
from src.dating.schedule import schedule_now
```

```python
def _send_blockers(*, flag: bool, said: str, shift: bool, draft: dict | None,
                   cooling: int) -> list:
    """Почему готовое письмо сейчас не уходит. Четыре вопроса, порядок круга.

    Args:
        flag: поднят ли выключатель отправки этого занятия.
        said: как занятие зовётся в строке («ответов», «писем избранным»).
        shift: идёт ли смена отправки (`mailing_schedule` — одна на всю почту).
        draft: строка пула этой пары либо `None`.
        cooling: сколько секунд получателю ещё не пишем.

    ⚠ **`send_why` целиком не зовём**: она отвечает про сайт («пул пуст», «все
    письма с ошибкой»), а окно — про пару. Берём те же два предиката, из которых
    она складывает вопрос о смене.

    ⚠ Минуту отправки не называем ни при каких условиях: уносит письма общий круг
    со своей паузой на весь сайт, и очередь делится со всеми анкетами.
    """
    blockers = []
    if not flag:
        blockers.append(f'отправка {said} выключена')
    if not shift:
        blockers.append('сейчас не смена: письма уходят по часам, заданным у сайта')
    if draft and str(draft.get('status')) == 'draft' and draft.get('error'):
        blockers.append('у письма прежняя ошибка — круг такие не берёт, '
                        'досылают кнопкой')
    if cooling:
        blockers.append(mail_wait_skip(cooling))

    return blockers
```

В `_favorite` и `_reply` заменить заглушки:

```python
    # ⚠ Смена отправки **одна на всю почту сайта** (`mailing_schedule`): у
    # занятий свои часы сочинения, а уносит письма общий круг.
    shift_send = schedule_now(json_column_list(flags.get('mailing_schedule')),
                              datetime_day_minutes(int(offset_hours)))
```

и далее, соответственно:

```python
        'send_blockers': _send_blockers(
            flag=bool(flags.get('favorite_mail_send_on')), said='писем избранным',
            shift=shift_send, draft=draft, cooling=cooling),
```

```python
        'send_blockers': _send_blockers(
            flag=bool(flags.get('reply_send_on')), said='ответов',
            shift=shift_send, draft=draft, cooling=cooling),
```

⚠ `_reply` получает `offset_hours` и `cooling` доводами — дописать их в
сигнатуру и в вызов.

Выбор следующего — в `mail_explain`, после обеих частей:

```python
def _next(reply: dict, favorite: dict) -> str:
    """Что уйдёт этой паре следующим письмом. Пусто — нечего.

    ⚠⚠ **Считает сервер, а не страница.** Это сравнение двух занятий по их
    помехам, и своя арифметика в браузере разошлась бы с кругами на первой
    правке — ровно та грабля, что уже была дважды (`initiative_why`, значок
    потолка).

    ⚠ **Лежащий черновик старше годной пары.** Он уже сочинён, за него заплачено
    вызовом модели, и уносит его ближайший круг отправки; годная пара ещё только
    дождётся захода набора.

    ⚠ Ответ старше приставания при прочих равных: он отвечает на заданный
    вопрос, а приставание напоминает о себе молчащему. Круги и так не пересекутся
    — «последним говорил он» отнимает приставание, — но при рассинхроне данных
    правило должно быть названо, а не выведено из случая.
    """
    for name, part in (('reply', reply), ('favorite', favorite)):
        draft = part.get('draft') or {}
        if str(draft.get('status')) == 'draft' and not part['send_blockers']:
            return name

    for name, part in (('reply', reply), ('favorite', favorite)):
        if not part['blockers']:
            return name

    return ''
```

**`wait_sec` у занятия.** В `_favorite` и `_reply`, сразу после сборки
`blockers` и до `return`, добавить:

```python
    # ⚠ Ждать показываем, только когда ждут **времени**: у пары, упёршейся в
    # предел, ожидание бессмысленно — предел снимают настройкой или его ответом,
    # а не терпением. Ноль здесь значит «ждать нечего», а не «уйдёт сейчас».
    next_sec = favorite_mail_round_next(site_id)   # в `_reply`: reply_round_next
    wait = next_sec if not blockers and next_sec > 0 else 0
```

и подставить `'next_sec': next_sec, 'wait_sec': wait` вместо прежних значений
(вызов `*_round_next` в словаре при этом убрать — он теперь выше, и звать круг
дважды незачем).

**Верхние `next` и `wait_sec`.** В `mail_explain` обе части считаются
**локальными переменными до словаря** — `_next` нужны обе готовыми:

```python
    favorite = await _favorite(site_id, profile_id, uid, pair, last, flags,
                               cooling, offset_hours)
    reply = await _reply(site_id, profile_id, uid, pair, flags, cooling,
                         offset_hours)

    return {
        'site_id': site_id,
        'known': True,
        'last': last,
        'block': {'is': bool(pair.get('block_mark') is not None),
                  'mark': str(pair.get('block_mark') or ''),
                  'channel': str(pair.get('block_channel') or '')},
        'cooling_sec': cooling,
        'reply': reply,
        'favorite': favorite,
        'next': _next(reply, favorite),
        # ⚠ Меньший из двух ненулевых: ждать паре до **ближайшего** из занятий.
        # Ноль — ждать нечего, и что именно мешает, говорят `blockers`.
        'wait_sec': min([one for one in (reply['wait_sec'],
                                         favorite['wait_sec']) if one] or [0]),
    }
```

- [ ] **Step 4: Запустить — проходит**

Run: `pytest tests/test_mail_explain.py -q`
Expected: `32 passed` (26 + 6 новых)

- [ ] **Step 5: Коммит**

```bash
git add src/dating/mail_explain.py tests/test_mail_explain.py
git commit -m "Окно почты: отправка и «что уйдёт следующим»

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Сторож согласия

**Files:**
- Modify: `tests/test_mail_explain.py`

**Interfaces:**
- Consumes: `mail_explain`; `favorite_mail._quiet`; `mail_reply._to_answer`.

**Зачем.** Это главное в файле. Без сторожа окно разойдётся с кругами на первой
правке и начнёт объяснять занятие, которого нет. В проекте это случалось дважды.

- [ ] **Step 1: Написать сторож**

```python
from src.dating.favorite_mail import _quiet
from src.dating.mail_reply import _to_answer


# Помехи, которых круги на своём уровне не проверяют: выключатель и смену
# спрашивает круг **выше** отбора. Сверяя их, тест ловил бы разницу охвата, а не
# расхождение правил.
_ROUND_BLIND = ('выключен', 'выключена', 'не смена')


def _pair_blockers(got: dict, part: str) -> list:
    """Помехи занятия без тех, которых отбор круга не видит."""
    return [one for one in got[part]['blockers']
            if not any(word in one for word in _ROUND_BLIND)]


class TestTheyAgree:
    """⚠⚠ Окно и круги отвечают одинаково — на одних и тех же данных.

    Сверяется **вердикт**, а не слова: у окна они человеческие и меняются, у
    отбора их нет вовсе. Общее у них одно — «берём пару или нет», и ровно это
    обязано совпадать.
    """

    async def test_ripe_favorite_pair_is_seen_by_both(self, girl_make, fav_on,
                                                      online_feed):
        """Пара созрела для приставания: круг берёт, окно не находит помех."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        online_feed([uid])

        got = await mail_explain(girl, uid)
        mine = {one['uid'] for one in await _quiet(STAND_SITE, girl)}

        assert uid in mine, 'круг пару не взял — проверять согласие не на чем'
        assert _pair_blockers(got, 'favorite') == [], \
            f"круг взял, а окно нашло помехи: {_pair_blockers(got, 'favorite')}"

    async def test_blocked_favorite_pair_is_skipped_by_both(self, girl_make,
                                                            fav_on, online_feed):
        """Запрет «не пишем»: круг пару не берёт, окно говорит почему."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _block(girl, uid)
        online_feed([uid])

        got = await mail_explain(girl, uid)
        mine = {one['uid'] for one in await _quiet(STAND_SITE, girl)}

        assert uid not in mine, 'круг взял запрещённую пару — это другая беда'
        assert _pair_blockers(got, 'favorite'), \
            'круг пару не взял, а окно помех не нашло'

    async def test_he_spoke_last_is_skipped_by_both(self, girl_make, fav_on,
                                                    online_feed):
        """Последним говорил он: приставания нет ни у круга, ни у окна."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _said_in(girl, uid, minutes_ago=5)
        online_feed([uid])

        got = await mail_explain(girl, uid)
        mine = {one['uid'] for one in await _quiet(STAND_SITE, girl)}

        assert uid not in mine
        assert _pair_blockers(got, 'favorite')

    async def test_ripe_reply_is_seen_by_both(self, girl, reply_on):
        """Письмо без ответа: круг берёт его, окно не находит помех."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='his letter')

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _to_answer(STAND_SITE)}

        assert uid in mine, 'круг письмо не взял — проверять согласие не на чем'
        assert _pair_blockers(got, 'reply') == [], \
            f"круг взял, а окно нашло помехи: {_pair_blockers(got, 'reply')}"

    async def test_answered_reply_is_skipped_by_both(self, girl, reply_on):
        """Ответ уже написан: круг письмо не берёт, окно говорит почему."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid, body='his letter')
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _to_answer(STAND_SITE)}

        assert uid not in mine
        assert _pair_blockers(got, 'reply')
```

Плюс фикстура снимка ленты — без неё «он не в сети» ловится в каждой проверке:

```python
import json
import time

from redis_.redis_ import redis_conn_get

from src.dating.online import ONLINE_KEY


@pytest.fixture
def online_feed():
    """Кладёт людей в снимок «кто в сети» сайта стенда и убирает за собой.

    ⚠ Снимок живёт в Redis, и отбор приставания читает именно его: без этой
    фикстуры он законно не берёт ни одной пары, и сторож проверял бы «он не в
    сети» вместо согласия правил.

    ⚠ Форма значения — та, которую ждёт `online._snapshot`: словарь с ключами
    `at` (метка времени) и `people` (список записей с `uid`). Без `people`
    снимок считается негодным и молча читается как «не знаем».
    """
    def _set(uids: list) -> None:
        redis_conn_get().set(
            ONLINE_KEY.format(site_id=STAND_SITE),
            json.dumps({'at': time.time(),
                        'people': [{'uid': str(one)} for one in uids]}))

    yield _set

    redis_conn_get().delete(ONLINE_KEY.format(site_id=STAND_SITE))
```

Проверено по месту: `ONLINE_KEY = 'dating:people:site:{site_id}:online'`
(`src/dating/online.py:37`), метка `{site_id}` — `.format(site_id=…)` здесь верен;
`_snapshot` требует `isinstance(snapshot, dict) and 'people' in snapshot`
(`online.py:161`), а `_check` читает `snapshot['at']`.

Сигнатура отбора — `_quiet(site_id: int, profile_id: int = 0)`
(`favorite_mail.py:787`): сайт и, вторым доводом, сужение до анкеты.

- [ ] **Step 2: Запустить — проходит**

Run: `pytest tests/test_mail_explain.py -q`
Expected: `37 passed` (32 + 5 сторожей)

- [ ] **Step 3: Проверить, что сторож и правда сторожит**

Временно сломать порядок в `_favorite` — убрать проверку запрета, — и убедиться,
что `test_blocked_favorite_pair_is_skipped_by_both` краснеет. Вернуть как было.

Run: `pytest tests/test_mail_explain.py -q -k blocked`
Expected: сперва FAIL, после возврата PASS

- [ ] **Step 4: Коммит**

```bash
git add tests/test_mail_explain.py
git commit -m "Окно почты: сторож согласия — окно и круги на одних данных

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Ручка `/explain`

**Files:**
- Modify: `src/uvicorn/routers/dating/favorite_mail.py` (вставить после
  `favorite_mail_thread`, строка 568, **выше** `/{mail_id}`-путей)
- Modify: `tests/test_mail_explain.py`

**Interfaces:**
- Consumes: `mail_explain` (задачи 4-7), `setting_timezone_hours`.
- Produces: `GET /api/dating/favorite-mail/explain?profile_id&uid` →
  `{'result': {...}}`; 422 без доводов.

- [ ] **Step 1: Написать падающие тесты**

```python
from fastapi import HTTPException

from src.uvicorn.routers.dating.favorite_mail import favorite_mail_explain


class TestHandle:
    """Ручка окна: доводы обязательны, незнакомая пара — не ошибка."""

    async def test_needs_both_arguments(self):
        """Без пары отвечать нечем — 422, а не пустой словарь.

        ⚠ Умолчания нет намеренно: пара, взятая молча, была бы чужой.
        """
        with pytest.raises(HTTPException) as got:
            await favorite_mail_explain(profile_id=0, uid='', user=None)

        assert got.value.status_code == 422

    async def test_unknown_pair_is_known_false(self):
        """Пары нет — `known: false` со словами, а не 404."""
        got = await favorite_mail_explain(profile_id=999_999_999, uid='nobody',
                                          user=None)

        assert got['result']['known'] is False

    async def test_known_pair_answers(self, girl, reply_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='his letter')

        got = await favorite_mail_explain(profile_id=girl, uid=uid, user=None)

        assert got['result']['known'] is True
        assert 'reply' in got['result'] and 'favorite' in got['result']
```

- [ ] **Step 2: Запустить — падает**

Run: `pytest tests/test_mail_explain.py -q -k Handle`
Expected: FAIL, `ImportError: cannot import name 'favorite_mail_explain'`

- [ ] **Step 3: Написать ручку**

Импорт в шапку роутера:

```python
from src.dating.mail_explain import mail_explain
```

Сам обработчик — сразу после `favorite_mail_thread`:

```python
@router.get('/explain', include_in_schema=False)
async def favorite_mail_explain(profile_id: int = 0, uid: str = '',
                                user: AuthUser = Depends(auth_required)):
    """Что с почтой у этой пары: что уйдёт следующим и что мешает сейчас.

    ⚠ **Считает домен** (`mail_explain.mail_explain`) теми же выражениями, что и
    круги. Своя копия счёта в ручке разошлась бы с ними на первой правке — ровно
    та грабля, что уже была с `initiative_why` и со значком потолка.

    ⚠ Стоит **выше** путей с номером (`/{mail_id}/send`, `/{mail_id}`): соседний
    роутер уже ловил на этом — путь-слово, объявленный ниже пути-числа,
    достаётся преобразователю номера, и `explain` отвечал бы 422.

    ⚠ Незнакомая пара — не ошибка, а обычный исход: человек открывает окно у
    разговора, которого в наших таблицах ещё нет. Отвечаем `known: false` со
    словами, а не 404: 404 страница нарисовала бы красным.

    ⚠ Сдвиг нужен: первый вопрос обоих занятий — «идёт ли смена», а смены заданы
    местным временем панели, не UTC.
    """
    if not int(profile_id or 0) or not str(uid or ''):
        raise HTTPException(status_code=422, detail='нужны profile_id и uid')

    got = await mail_explain(int(profile_id), str(uid),
                             await setting_timezone_hours())

    return {'result': got or {'known': False}}
```

- [ ] **Step 4: Запустить — проходит**

Run: `pytest tests/test_mail_explain.py -q`
Expected: `40 passed` (37 + 3 на ручку)

- [ ] **Step 5: Прогнать группу целиком**

Run: `pytest -m mail -q`
Expected: всё зелёное. Красное в соседних файлах — сперва проверить, не идёт ли
чужой `pytest` параллельно (`pgrep -af pytest`): гонка двух прогонов даёт разные
падения в разных заходах.

- [ ] **Step 6: Коммит**

```bash
git add src/uvicorn/routers/dating/favorite_mail.py tests/test_mail_explain.py
git commit -m "Фавориты: ручка окна «почему этой паре не пишут»

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Страница — значок и окно

**Files:**
- Modify: `data/uvicorn/template/admin/dating/profile/site/_base.html:820-845`
- Modify: `data/uvicorn/public/css/dating_page.css` (после `.talk-acts`, строка 1040)
- Modify: `data/uvicorn/template/admin/dating/profile.html` — три зоны:
  строка 4960 (слушатель), 7415 (показ), около 12765 (тело окна, **после**
  `initiativeInfoLeft`, не трогая `initiativeInfoCounts` на 12682-12708)

**Interfaces:**
- Consumes: `GET /api/dating/favorite-mail/explain` (Task 9); `favTalkOpen`
  (строка `"profileId|uid"`), `favTalkRow`, `letterFact`, `modalOpen`, `getData`,
  `slot`, `showNotification`, `initiativeInfoLeft`.

- [ ] **Step 1: Кнопка в шапке разговора**

`_base.html`, между `fav-talk-who` (822) и `label.in-only` (839):

```html
                    {# ⚠ Значок «что с письмами у этой пары»: почему ей сейчас не
                       пишут и когда напишут. Считает **сервер** (`mail_explain`)
                       теми же выражениями, что и круги — своей арифметики на
                       странице нет намеренно.

                       ⚠⚠ Глиф — `ⓘ` (кружок), а **не** `ℹ`: последний в шрифте
                       панели рисуется голой буквой «i» без кружка и читается как
                       случайный символ. Видно только кадром — в DOM оба «значок
                       на месте».

                       ⚠⚠ Внутри обёртки `.talk-acts`, хотя значок один: шапка
                       переносит по словам, и в узкой колонке голая кнопка
                       встаёт столбиком под подписью. Обёртка не сжимается и
                       переносится целиком. #}
                    <span class="talk-acts">
                        <button type="button" class="talk-info"
                                data-slot="fav-mail-info" hidden
                                title="Письма: что с этой парой">ⓘ</button>
                    </span>
```

- [ ] **Step 2: Правило края в CSS**

`dating_page.css`, сразу после блока `.talk-acts` (строка 1040):

```css
/* ⚠⚠ В шапке «Фаворитов → Диалоги» за обёрткой стоит ещё галочка «с ответами», и
   правый край забирает она (`.in-only`, `margin-left: auto`). Второй `auto` рядом
   не прижимает, а **делит** свободное место: значок уехал бы на середину шапки.
   Поэтому обёртке, у которой справа есть сосед, край не отдаём. */
.talk-acts:not(:last-child) { margin-left: 0; }
```

- [ ] **Step 3: Показ значка по выбранной паре**

`profile.html`, в `favTalkPaint` (7408-7422), после `fillSlot('fav-talk-who'…)`:

```javascript
        // ⚠ Значок прячется, пока пара не выбрана: кнопка, ведущая в пустоту,
        // читается как поломка. Та же пара строк, что у значков инициативы.
        const mailInfo = slot('fav-mail-info');
        if (mailInfo) mailInfo.hidden = !favTalkOpen;
```

⚠ Условие — по `favTalkOpen`, а **не** по `picked`: пару открывают и карточкой
из пула ответов, а её строка может лежать на другой странице списка — в
`favTalks` её тогда нет вовсе (см. `favTalkRow`, строка 6954).

- [ ] **Step 4: Слушатель**

`profile.html`, рядом с `favTalksReload` (после строки 4960):

```javascript
        // Что с письмами у этой пары — окном. ⚠ Ничего не меняет, только
        // рассказывает.
        const favMailInfo = workEl.querySelector('[data-slot="fav-mail-info"]');
        if (favMailInfo) favMailInfo.addEventListener('click', mailInfoShow);
```

- [ ] **Step 5: Тело окна**

`profile.html`, после `initiativeInfoLeft` (после строки 12763):

```javascript
    // ── Окно «что с письмами у пары» ─────────────────────────────────────────
    //
    // ⚠⚠ **Считает всё сервер** (`mail_explain`), теми же выражениями, что и
    // круги. Здесь только показ: своя арифметика на странице разошлась бы с ними
    // на первой правке, и окно объясняло бы занятие, которого нет.
    //
    // ⚠ Поэтому ниже нет ни одного «если ушло больше предела»: вердикты приходят
    // готовыми (`blockers`, `send_blockers`, `next`), а страница их расставляет.

    async function mailInfoShow() {
        const [profileId, uid] = String(favTalkOpen || '').split('|');
        if (!uid) return;

        let got;
        try {
            got = (await getData(`${BASE}/api/dating/favorite-mail/explain`
                + `?profile_id=${profileId}&uid=${encodeURIComponent(uid)}`)).result
                || {};
        } catch (e) {
            showNotification('Не вышло спросить про письма: ' + e.message, 'error');

            return;
        }

        const found = favTalkRow || (favTalks || []).find(one =>
            String(one.uid) === String(uid)
            && String(one.profile_id) === String(profileId)) || {};

        modalOpen({
            title: `Письма: ${found.profile_name || '—'} → ${found.name || uid}`,
            body: mailInfoBody(got),
        });
    }

    function mailInfoBody(got) {
        if (!got.known) {
            return '<div class="letter-row"><span class="letter-val">'
                + 'Этой пары в наших таблицах нет — рассказывать не о чем.'
                + '</span></div>';
        }

        return mailInfoNow(got) + mailInfoReply(got) + mailInfoFavorite(got)
            + mailInfoRules(got);
    }

    // Что сейчас: что уйдёт следующим и что мешает.
    //
    // ⚠⚠ Помехи показываем **все**, а не первую. Человек, увидев «пауза не
    // вышла», подождёт четыре часа и вернётся к тому же молчанию, потому что под
    // паузой лежал дневной предел получателя. Круги их тоже проверяют все —
    // просто молча.
    function mailInfoNow(got) {
        const names = {reply: 'ответ на его письмо', favorite: 'приставание'};
        const all = [...(got.reply.blockers || []), ...(got.reply.send_blockers || []),
                     ...(got.favorite.blockers || []),
                     ...(got.favorite.send_blockers || [])];

        return '<div class="init-info-head">Сейчас</div>'
            + letterFact('Следующее письмо', got.next
                ? names[got.next]
                : 'нечего — оба занятия упёрлись', !got.next)
            + ([...new Set(all)]).map(one => letterFact('Мешает', one, true)).join('')
            + (got.wait_sec > 0
                ? letterFact('Ждать', initiativeInfoLeft(got.wait_sec)) : '')
            // ⚠⚠ Минуту отправки не называем: уносит письма общий круг со своей
            // паузой на весь сайт, и очередь делится со всеми анкетами.
            + '<div class="letter-row"><span class="letter-val init-info-note">'
            + 'Срок считается до годности пары. Когда именно уйдёт письмо, решает '
            + 'круг отправки: у него своя пауза на весь сайт.'
            + '</span></div>';
    }

    function mailInfoReply(got) {
        const one = got.reply;

        return '<div class="init-info-head">Ответ на его письмо</div>'
            + letterFact('Генерация', one.make_on ? 'включена' : 'выключена',
                         !one.make_on)
            + letterFact('Отправка', one.send_on ? 'включена' : 'выключена',
                         !one.send_on)
            + letterFact('Его последнее письмо', one.his_last
                ? `${timeShift(one.his_last.at)} · ${timeAgo(one.his_last.at)}`
                  + (one.his_last.empty ? ' · тело ещё не забрали' : '') : '')
            + letterFact('Черновик', mailInfoDraft(one.draft))
            + letterFact('Круг ответов', mailInfoEvery(one));
    }

    function mailInfoFavorite(got) {
        const one = got.favorite;

        return '<div class="init-info-head">Приставание</div>'
            + letterFact('Набор', one.make_on ? 'включён' : 'выключен', !one.make_on)
            + letterFact('Отправка', one.send_on ? 'включена' : 'выключена',
                         !one.send_on)
            + letterFact('В избранных', one.star
                ? `да${one.star_state ? ` · ${one.star_state}` : ''}` : 'нет',
                !one.star)
            + letterFact('Смена сочинения', one.shift_now ? 'идёт' : 'не идёт',
                         !one.shift_now)
            + letterFact('Он в сети', one.him_known
                ? (one.him_online ? 'да' : 'нет') : 'не знаем — ленту не снимали',
                !one.him_online)
            + letterFact('Писем ему за сутки',
                         `${one.day_sent} из ${one.per_day}`,
                         one.day_sent >= one.per_day)
            + letterFact('Черновик', mailInfoDraft(one.draft))
            + letterFact('Круг приставания', mailInfoEvery(one));
    }

    // По каким правилам: настройки сайта, которые человек и правит, увидев окно.
    function mailInfoRules(got) {
        return '<div class="init-info-head">Правила сайта</div>'
            + letterFact('Пауза между письмами анкеты',
                         got.favorite.gap_sec
                             ? initiativeInfoLeft(got.favorite.gap_sec)
                             : 'без паузы')
            + letterFact('Предел писем получателю', `${got.favorite.per_day} в сутки`)
            + letterFact('Остуда его ящика', got.cooling_sec
                ? initiativeInfoLeft(got.cooling_sec) : '')
            + letterFact('Запрет «не пишем»', got.block.is
                ? `${got.block.mark} · ${got.block.channel === 'mail'
                    ? 'только письма' : 'оба канала'}` : '', got.block.is);
    }

    // Черновик словами: его состояние важнее номера.
    function mailInfoDraft(draft) {
        if (!draft || draft.status !== 'draft') return 'нет';

        return draft.error ? `лежит с ошибкой: ${draft.error}` : 'лежит, ждёт отправки';
    }

    // Срок круга. ⚠ `-1` значит «спросить не вышло» — врать сроком нельзя.
    function mailInfoEvery(one) {
        const every = `раз в ${initiativeInfoLeft(one.every_sec)}`;

        return one.next_sec < 0
            ? `${every} · когда вернётся — не спросилось`
            : `${every} · вернётся через ${initiativeInfoLeft(one.next_sec)}`;
    }
```

- [ ] **Step 6: Снять кадр**

Поднять панель и перечитать шаблоны:

```bash
docker compose up -d                       # панель на http://localhost:8021
docker restart dating_uvicorn              # Jinja кэширует шаблон в памяти
```

⚠ `restart` обязателен: файл внутри контейнера новый сразу (каталог
смонтирован), а `uvicorn` держит прежний шаблон — «правка не видна» это оно
(`quickstart.md`, §«Изменения не подхватились»).

Открыть «Профили → Фавориты → Диалоги», выбрать пару, снять два кадра: шапку как
есть и шапку в **узкой** колонке (потянуть разделитель `.split-reply`). Кадр —
`py_modules/web_/web_drive_page.py` либо вкладка «Тесты → E2E». Проверить
глазами:

1. значок стоит слева от «с ответами», а не посередине шапки;
2. в узкой колонке он не уезжает столбиком под подпись;
3. глиф — кружок с «i», а не голая буква;
4. по щелчку открывается окно, помехи читаются, числа на месте.

⚠ Кадр обязателен: застава `claude_gate_design` не отпустит ход без него, и это
не формальность — обе грабли выше (глиф и столбик) в DOM не видны.

- [ ] **Step 7: Коммит**

```bash
git add data/uvicorn/template/admin/dating/profile/site/_base.html \
        data/uvicorn/public/css/dating_page.css \
        data/uvicorn/template/admin/dating/profile.html
git commit -m "Фавориты → Диалоги: значок ⓘ — что с письмами у этой пары

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Документация

**Files:**
- Modify: `.claude/docs/api.md`, §3.7.7
- Modify: `CLAUDE.md`, раздел Development Patterns

- [ ] **Step 1: Ручка в `api.md`**

В §3.7.7 («Письмо избранному, который замолчал») дописать подраздел:

```markdown
#### Почему этой паре не пишут — значок ⓘ в шапке разговора

`GET /api/dating/favorite-mail/explain?profile_id=<N>&uid=<строка>`

Окно на «Фаворитах → Диалоги», как значок инициативы на «Чате» (§3.8.2) и по той
же причине: занятий два, правил у них шестнадцать, и почти все невидимы.

⚠⚠ **Считает сервер** (`mail_explain`) теми же выражениями, что и круги. Своя
арифметика на странице разошлась бы с ними на первой правке — грабля уже
случалась дважды. За совпадением следит сторож `TestTheyAgree`
(`tests/test_mail_explain.py`): он гоняет окно и оба отбора по одним данным.

⚠ Помехи показываются **все**, а не первая: подождав «пауза не вышла», человек
вернулся бы к тому же молчанию из-за дневного предела получателя.

⚠ Минуты отправки окно не называет намеренно — её решает общий круг отправки со
своей паузой на весь сайт.

⚠ Возрастного окна в нём нет: `mail_age` спрашивается только у рассылки, у
холодного обращения.
```

- [ ] **Step 2: Строка в `CLAUDE.md`**

В Development Patterns, рядом со строкой «Почему этой паре не пишут» (она про
чат), дописать:

```markdown
- **Почему этой паре не пишут письма** → значок **ⓘ** в шапке разговора на
  «Фаворитах → Диалоги» (`.claude/docs/api.md`, §3.7.7; ⚠ считает **сервер**
  `mail_explain` теми же выражениями, что и круги, и за совпадением следит сторож
  `TestTheyAgree`. ⚠ Занятий **два** — ответ на его письмо и приставание, — и
  помехи у них свои; общие только запрет «не пишем» и остуда его ящика.
  ⚠⚠ Возрастного окна здесь нет: `mail_age` спрашивается только у рассылки.
  ⚠ Внутрисходовых правил тоже нет — они про заход, а не про пару)
```

- [ ] **Step 3: Коммит**

```bash
git add .claude/docs/api.md CLAUDE.md
git commit -m "Документация: окно «почему этой паре не пишут письма»

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

⚠ `CLAUDE.md` правит и вторая сессия — перед `git add` проверить `git diff
CLAUDE.md` и добавить **только свою** строку, не затирая чужую.

---

## Проверка перед выкладкой

- [ ] `pytest -m mail -q` — зелено
- [ ] `pytest -q` — полный прогон зелёный (одиночное красное в кругах ≠ регрессия;
      сперва проверить `pgrep -af pytest` на чужой прогон)
- [ ] `PYTHONPATH=py_modules python -m deploy_.deploy_precheck` — без «стоп»
- [ ] `git -C py_modules log origin/master..HEAD` — пусто (подмодуль запушен)
- [ ] кадр страницы приложен к задаче 10
