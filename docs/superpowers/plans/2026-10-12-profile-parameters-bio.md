# Параметры анкеты девушки и полное био — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Карточка анкеты девушки получает 21 параметр с постоянным каркасом,
снимаемый кнопкой со страницы админ-панели агентства, правимый руками во второй
колонке окна и целиком уезжающий в `girl_bio` агенту чата.

**Architecture:** Один реестр параметров (`profile_param.py`) задаёт ключи, подписи
окна, подписи био, порядок и вид поля — его читают и страница, и сборка био, и
сторож согласия. Чтение формы панели общее (`panel_fetch_form`, семантика HTML),
карта «имя поля формы → наш ключ» и нормализация — в драйвере сайта, четвёртой
таблицей `site_registry`. Запись в карточку идёт двумя названными дверями:
`profile_card_save` (человек, ставит отметки) и новая `profile_card_merge` (сайт,
уважает отметки) — последняя собирает в себя двух нынешних близнецов.

**Tech Stack:** Python 3.11, FastAPI, aiomysql, `html.parser` из стандартной
библиотеки, pytest (маркеры `profile`/`chat`/`page`), ES-модули без сборки.

**Спека:** `docs/superpowers/specs/2026-10-12-profile-parameters-bio-design.md`

## Global Constraints

- **Живого HTTP в тестах не бывает.** Панель подменяется фикстурой `panel` из
  `tests/panel_fake.py` (она подменяет `http_heartbeat_client` **внутри модуля**
  `panel_fetch`, а не сам `panel_fetch_html`). Сторож `http_mock_guard` стоит на
  весь прогон и падает на любом походе наружу.
- **Анкета для теста — только фикстуры `girl` / `girl_make`** из `tests/conftest.py`.
  Своих `DELETE` не писать: каскад уносит двенадцать таблиц сам.
  `girl_make(prefix='t', *, site_id=TEST_STAND_SITE, online=False) -> int`.
- **Номера сайтов в тестах — выдуманные**, из диапазона `999_xxx` (живые настройки
  задевать нельзя).
- **`py_modules` — отдельный репозиторий и submodule.** Коммит туда делается и
  пушится **раньше**, чем коммит в `DatingAi`, иначе прод падает `ImportError`.
  Своей сюиты у `py_modules` нет: его функции проверяются тестами проектов.
- **Комментарии и докстринги — по-русски**, в стиле соседних файлов: докстринг
  отвечает «зачем так, а не иначе», `⚠` помечает грабли.
- **Прогон группы:** `pytest -m profile -q` (~полминуты). Полный прогон
  `pytest -q` — только перед выкладкой.
- **Длины:** `PROFILE_PARAM_TEXT_MAX = 190`, `PROFILE_PARAM_AREA_MAX = 2000`
  (`IMPORT_ABOUT_MAX`), `PROFILE_PARAM_BIO_MAX = 3000`, `COMPANION_BIO_MAX = 1200`.
- **Ровно 21 ключ параметров**, порядок — порядок реестра:
  `birthday, country, occupation, education, english, marital_state, children,
  religion, ethnicity, body, height, weight, hair, hair_style, eyes, smoke, drink,
  age_from, age_to, hobby, about_partner`.

---

## Файлы

| Файл | Ответственность | Задача |
|---|---|---|
| `py_modules/text_/text_trim_lines.py` | обрезка текста по границе строки | 1 |
| `src/dating/companion_bio.py` | переходит на общую обрезку | 1 |
| `src/dating/profile_param.py` | **реестр** параметров: ключи, подписи, каркас, строки био | 2 |
| `src/dating/agent.py` | `agent_bio` дописывает блок параметров | 3 |
| `src/dating/panel_fetch.py` | `panel_fetch_form` — значения формы | 4 |
| `tests/frames/ladadate_lady_edit.html` | образец разметки панели | 4 |
| `src/dating/site/site_panel.py` | **драйвер**: путь, карта имён, нормализация | 5 |
| `src/dating/site/site_registry.py` | 4-я таблица драйверов + `site_registry_panel` | 5 |
| `src/dating/profile_card.py` | составные отметки, `profile_card_merge` | 6, 7 |
| `src/dating/profile_page.py`, `src/dating/profile_cache.py` | зовут `profile_card_merge` | 7 |
| `src/dating/profile_panel.py` | снять параметры с панели и записать | 8 |
| `src/uvicorn/routers/dating/profile_view.py` | `GET /card` отдаёт параметры и реестр | 9 |
| `src/uvicorn/routers/dating/profile_.py` | `PUT /card` принимает параметры | 9 |
| `src/uvicorn/routers/dating/profile_action.py` | `POST /card/parameters` | 9 |
| `data/uvicorn/template/admin/dating/index.html` | вторая колонка окна | 10 |
| `data/uvicorn/public/css/dating_page.css` | сетка окна в две колонки | 10 |
| `CLAUDE.md`, `.claude/docs/api.md`, `.claude/docs/dating.md` | документация | 11 |

---

### Task 1: Обрезка текста по границе строки — в общий слой

**Files:**
- Create: `py_modules/text_/text_trim_lines.py`
- Modify: `src/dating/companion_bio.py:124-137`
- Test: `tests/test_text_trim.py`

**Interfaces:**
- Produces: `text_trim_lines(text: str, limit: int) -> str` — импортируется как
  `from text_.text_trim_lines import text_trim_lines`.

**Зачем:** правило «резать по границе строки, а не по знаку» живёт в
`companion_bio.py` своим куском кода, и сейчас появляется второй случай
(`agent_bio`, задача 3). Две копии разошлись бы так же, как разошлись три копии
процентов (`percent.py`): две клампили сами, третья соблюдала границы реестра.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_text_trim.py`:

```python
"""Обрезка текста по границе строки: общая на био девушки и био собеседника.

⚠ Функция живёт в общем слое (`py_modules/text_/text_trim_lines.py`), а проверяется
отсюда: своей сюиты у общего слоя нет — его функции проверяют тесты проектов. Тот же
приём, что у `test_text_emoji.py`.
"""

import pytest

from text_.text_trim_lines import text_trim_lines

pytestmark = pytest.mark.profile


class TestItCutsOnLineBoundary:
    """⚠ Оборванная на полуслове строка выглядит как сбой и провоцирует модель
    её «досочинить» — поэтому режем целыми строками."""

    def test_short_text_goes_whole(self):
        assert text_trim_lines('раз\nдва', 100) == 'раз\nдва'

    def test_long_text_loses_whole_lines(self):
        # Строки по 10 знаков с переводом: в 25 знаков влезают две.
        text = '\n'.join(['0123456789'] * 5)

        assert text_trim_lines(text, 25) == '0123456789\n0123456789'

    def test_a_line_is_never_cut_in_half(self):
        text = 'раз\nдвадцать-два-знака-ровно'

        assert text_trim_lines(text, 10) == 'раз'

    def test_first_line_longer_than_limit_gives_nothing(self):
        """⚠ Так было и так остаётся: половина первой строки хуже пустоты.

        Вызывающий обязан это учитывать — `agent_bio` потому и режет **только блок
        параметров**, оставляя прозу нетронутой."""
        assert text_trim_lines('очень длинная первая строка', 5) == ''

    def test_edges_are_trimmed(self):
        assert text_trim_lines('  раз  \n', 100) == 'раз'

    def test_empty_input_is_legal(self):
        assert text_trim_lines('', 100) == ''
        assert text_trim_lines(None, 100) == ''
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `pytest tests/test_text_trim.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'text_.text_trim_lines'`

- [ ] **Step 3: Написать функцию в общем слое**

Создать `py_modules/text_/text_trim_lines.py`:

```python
"""Обрезка текста до предела — целыми строками, а не по знаку.

Текст, который уезжает языковой модели, приходится ограничивать: не ради экономии
токенов, а ради её внимания — полторы тысячи знаков это уже «простыня», в которой
тонет сам вопрос.

## ⚠ Почему по границе строки

Оборванная на полуслове строка выглядит как сбой, и модель начинает её
«досочинять» — то есть выдумывать то, чего в источнике не было. Целая строка
меньше, но честнее.

## ⚠⚠ Первая строка длиннее предела даёт пустоту

Это осознанное свойство, а не недосмотр: половина первой строки — то самое
«оборвано на полуслове», от которого функция и защищает. Вызывающий обязан это
учитывать: кладите в неё **список строк**, а не один абзац, либо режьте только ту
часть текста, которой не жалко.
"""


def text_trim_lines(text, limit: int) -> str:
    """Текст не длиннее предела: целые строки с начала, хвост отброшен.

    Args:
        text: исходный текст; `None` и пустое — законный ввод.
        limit: предел в знаках, считая переводы строк между оставленными.

    Returns:
        str: обрезанный текст без пробелов по краям. Пустая строка, когда не
        поместилась даже первая строка.
    """
    said = str(text or '').strip()
    if len(said) <= int(limit):
        return said

    kept, left = [], int(limit)
    for line in said.split('\n'):
        # +1 — перевод строки, которым строка будет приклеена к предыдущей.
        if len(line) + 1 > left:
            break

        kept.append(line)
        left -= len(line) + 1

    return '\n'.join(kept).strip()
```

- [ ] **Step 4: Прогнать тест**

Run: `pytest tests/test_text_trim.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Перевести `companion_bio` на общую функцию**

В `src/dating/companion_bio.py` добавить импорт после строки 48:

```python
from text_.text_trim_lines import text_trim_lines
```

Заменить блок обрезки в конце `companion_bio_build` (строки 124-137):

```python
    # ⚠ Режем **по границе строки**, а не по знаку: оборванная на полуслове
    # анкета выглядит как сбой и провоцирует модель её «досочинить».
    if len(text) > COMPANION_BIO_MAX:
        kept = []
        left = COMPANION_BIO_MAX
        for line in text.split('\n'):
            if len(line) + 1 > left:
                break

            kept.append(line)
            left -= len(line) + 1

        text = '\n'.join(kept).strip()

    return text
```

на:

```python
    # ⚠ Режем **по границе строки**, а не по знаку: оборванная на полуслове
    # анкета выглядит как сбой и провоцирует модель её «досочинить». Правило общее
    # с био девушки (`agent_bio`) и потому живёт в общем слое: две копии разошлись
    # бы ровно там, где это не видно.
    return text_trim_lines(text, COMPANION_BIO_MAX)
```

- [ ] **Step 6: Прогнать соседние тесты — поведение не изменилось**

Run: `pytest tests/test_companion_bio.py tests/test_text_trim.py -q`
Expected: PASS, все зелёные

Файла `tests/test_companion_bio.py` может не быть — тогда прогнать
`pytest -m mail -q -k companion` и убедиться, что зелено.

- [ ] **Step 7: Коммит в общий слой (первым!)**

```bash
cd py_modules
git add text_/text_trim_lines.py
git commit -m "Текст: обрезка по границе строки одной функцией

Правило «резать целыми строками, а не по знаку» стояло куском кода в
био собеседника, и появился второй случай — био девушки. Две копии
разошлись бы так же, как разошлись три копии процентов.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git push
cd ..
```

- [ ] **Step 8: Коммит в проект**

```bash
git add py_modules tests/test_text_trim.py src/dating/companion_bio.py
git commit -m "Био собеседника: обрезка общей функцией слоя

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Реестр параметров

**Files:**
- Create: `src/dating/profile_param.py`
- Test: `tests/test_profile_param.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `PROFILE_PARAMS: tuple[tuple[str, str, str, str], ...]` — `(ключ, подпись окна, подпись био, вид)`, вид `'text'` либо `'area'`;
  - `PROFILE_PARAM_KEYS: tuple[str, ...]` — 21 ключ в порядке реестра;
  - `PROFILE_PARAM_PREFIX = 'parameters.'`;
  - `PROFILE_PARAM_TEXT_MAX = 190`, `PROFILE_PARAM_AREA_MAX = 2000`, `PROFILE_PARAM_BIO_MAX = 3000`;
  - `profile_param_frame(values) -> dict` — каркас из 21 ключа;
  - `profile_param_fields() -> list[dict]` — `[{'key', 'title', 'kind'}, …]`;
  - `profile_param_bio(params) -> list[str]` — строки `Label: value`, пустые опущены;
  - `profile_param_max(key: str) -> int` — предел длины значения по виду поля.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_profile_param.py`:

```python
"""Реестр параметров анкеты: один каркас на окно, панель и био.

## ⚠⚠ Зачем каркас постоянный

Все 21 ключ лежат в карточке **всегда**, неизвестное — пустой строкой. Иначе
«ключа нет» и «ключ пуст» стали бы двумя разными состояниями, которые пришлось бы
различать в четырёх местах: в показе, в сборке био, в сравнении при правке и в
отсеве пустых.
"""

import pytest

from src.dating.profile_param import (PROFILE_PARAM_AREA_MAX,
                                      PROFILE_PARAM_KEYS,
                                      PROFILE_PARAM_PREFIX,
                                      PROFILE_PARAM_TEXT_MAX,
                                      PROFILE_PARAMS, profile_param_bio,
                                      profile_param_fields,
                                      profile_param_frame, profile_param_max)

pytestmark = pytest.mark.profile


class TestTheFrameIsAlwaysTheSame:
    """⚠ «Каркас ключей одинаковый для всех профилей» — проверяемо, а не на словах."""

    def test_there_are_twenty_one_keys(self):
        assert len(PROFILE_PARAM_KEYS) == 21
        assert len(set(PROFILE_PARAM_KEYS)) == 21, 'ключ повторился'

    def test_empty_values_give_the_whole_frame(self):
        got = profile_param_frame({})

        assert list(got) == list(PROFILE_PARAM_KEYS)
        assert set(got.values()) == {''}

    def test_none_gives_the_whole_frame_too(self):
        assert list(profile_param_frame(None)) == list(PROFILE_PARAM_KEYS)

    def test_strangers_are_dropped(self):
        got = profile_param_frame({'height': '172 cm', 'шпион': 'да'})

        assert got['height'] == '172 cm'
        assert 'шпион' not in got

    def test_order_is_the_registry_order(self):
        got = profile_param_frame({'hobby': 'x', 'birthday': '1990-01-21'})

        assert list(got)[0] == 'birthday', 'порядок не реестра'

    def test_values_are_trimmed(self):
        assert profile_param_frame({'eyes': '  Hazel \n'})['eyes'] == 'Hazel'


class TestTheBioLines:
    def test_empty_fields_are_not_named(self):
        """⚠ «Hair: —» модель читает как факт и может об этом заговорить."""
        got = profile_param_bio({'eyes': 'Hazel', 'hair': ''})

        assert got == ['Eyes: Hazel']

    def test_labels_come_from_the_registry(self):
        got = profile_param_bio({'marital_state': 'Never been Married'})

        assert got == ['Marital status: Never been Married']

    def test_lines_follow_the_registry_order(self):
        got = profile_param_bio({'hobby': 'swimming', 'birthday': '1990-01-21'})

        assert got == ['Birthday: 1990-01-21', 'Hobby: swimming']

    def test_nothing_known_gives_no_lines(self):
        assert profile_param_bio({}) == []
        assert profile_param_bio(None) == []


class TestTheFieldsForThePage:
    """⚠ Подписи и вид поля страница берёт **с сервера**: своя копия списка на
    странице разошлась бы с кодом молча — грабля словаря тегов галереи."""

    def test_every_key_has_a_title_and_a_kind(self):
        got = profile_param_fields()

        assert len(got) == 21
        for one in got:
            assert one['title'], f'у {one["key"]} нет подписи окна'
            assert one['kind'] in ('text', 'area')

    def test_two_fields_are_areas(self):
        areas = [one['key'] for one in profile_param_fields()
                 if one['kind'] == 'area']

        assert areas == ['hobby', 'about_partner']


class TestTheLengthLimits:
    def test_text_field_limit(self):
        assert profile_param_max('eyes') == PROFILE_PARAM_TEXT_MAX

    def test_area_field_limit(self):
        assert profile_param_max('hobby') == PROFILE_PARAM_AREA_MAX

    def test_stranger_has_no_limit(self):
        """Ноль — «такого поля нет»; вызывающий решает, что с этим делать."""
        assert profile_param_max('шпион') == 0


class TestThePrefix:
    def test_the_mark_name_is_built_from_the_prefix(self):
        assert PROFILE_PARAM_PREFIX + 'height' == 'parameters.height'

    def test_the_registry_rows_have_four_parts(self):
        for one in PROFILE_PARAMS:
            assert len(one) == 4, f'строка реестра не из четырёх частей: {one}'
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `pytest tests/test_profile_param.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dating.profile_param'`

- [ ] **Step 3: Написать реестр**

Создать `src/dating/profile_param.py`:

```python
"""Параметры анкеты девушки: один реестр на окно, панель и био.

Агент, который говорит за девушку, знал о ней четыре вещи: имя, возраст, город и
рассказ «о себе». Собеседник спрашивает о другом — рост, волосы, дети, работа,
увлечения, — и ответов у агента не было вовсе, значит он их придумывал.

Ответы лежат в админ-панели агентства. Здесь — **реестр** того, что мы оттуда
берём: какие ключи бывают, как они подписаны в окне правки, как подписаны в био и
каким полем правятся.

## ⚠⚠ Каркас постоянный: 21 ключ всегда

`profile_param_frame` отдаёт **все** ключи, неизвестное — пустой строкой. Иначе
«ключа нет» и «ключ пуст» были бы двумя разными состояниями, и различать их
пришлось бы в четырёх местах: в показе, в сборке био, в сравнении при правке
руками и в отсеве пустых. Одно состояние дешевле четырёх проверок.

## ⚠ Реестр один, а читателей трое

Подписи окна уезжают **на страницу** ответом ручки карточки, подписи био уезжают
агенту, ключи — в отметки правки (`parameters.height`) и в карту драйвера панели.
Своя копия списка у любого из троих разошлась бы с остальными молча: ровно это уже
случилось со словарём тегов галереи, и закрыто там тем же способом — список отдаёт
сервер.

## ⚠ Чего здесь нет

**Нормализации значений.** `5'8" - 172 cm` → `172 cm` делает драйвер сайта
(`site/site_panel.py`): это знание о том, как пишет конкретная панель, и в общем
коде оно стало бы миной для второго сайта.

**Отметки о чтении.** `parameters_at` лежит в карточке рядом с `parameters`, а не
внутри — иначе каркас перестал бы быть ровно из 21 ключа.
"""

# Ключ, подпись окна, подпись био (по-английски), вид поля.
#
# ⚠ Порядок — порядок показа в окне **и** порядок строк био разом: два порядка на
# один список разошлись бы на первой вставке.
#
# ⚠ Подписи био по-английски, потому что на нём ведётся переписка и объявлен узел
# Dating у соседа; подписи окна по-русски — его читает человек.
PROFILE_PARAMS = (
    ('birthday',      'Дата рождения',      'Birthday',         'text'),
    ('country',       'Страна',             'Country',          'text'),
    ('occupation',    'Занятие',            'Occupation',       'text'),
    ('education',     'Образование',        'Education',        'text'),
    ('english',       'Английский',         'English',          'text'),
    ('marital_state', 'Семейное положение', 'Marital status',   'text'),
    ('children',      'Дети',               'Children',         'text'),
    ('religion',      'Религия',            'Religion',         'text'),
    ('ethnicity',     'Происхождение',      'Ethnicity',        'text'),
    ('body',          'Телосложение',       'Body',             'text'),
    ('height',        'Рост',               'Height',           'text'),
    ('weight',        'Вес',                'Weight',           'text'),
    ('hair',          'Волосы',             'Hair',             'text'),
    ('hair_style',    'Причёска',           'Hair style',       'text'),
    ('eyes',          'Глаза',              'Eyes',             'text'),
    ('smoke',         'Курит',              'Smoke',            'text'),
    ('drink',         'Алкоголь',           'Drink',            'text'),
    ('age_from',      'Ищет: возраст от',   'Partner age from', 'text'),
    ('age_to',        'Ищет: возраст до',   'Partner age to',   'text'),
    ('hobby',         'Увлечения',          'Hobby',            'area'),
    ('about_partner', 'О партнёре',         'Looking for',      'area'),
)

# Ключи отдельно — их спрашивают чаще всего остального.
PROFILE_PARAM_KEYS = tuple(one[0] for one in PROFILE_PARAMS)

# Приставка имени отметки «правлено руками»: `parameters.height`.
#
# ⚠ Отметки у параметров **составные**, а не свои колонкой: механизм
# (`profile_edited`) уже есть, и второй рядом отвечал бы на тот же вопрос дважды.
PROFILE_PARAM_PREFIX = 'parameters.'

# Пределы длины значения по виду поля — те же, что у соседних полей карточки.
PROFILE_PARAM_TEXT_MAX = 190
PROFILE_PARAM_AREA_MAX = 2000

# Сколько знаков параметров отдаём агенту.
#
# ⚠⚠ Режется **только блок параметров**, а не всё био: проза идёт одной строкой, и
# обрезка по границе строки унесла бы её целиком, оставив агента без имени. Первая
# строка блока — `Birthday: …`, то есть короткая при любой анкете.
PROFILE_PARAM_BIO_MAX = 3000


def profile_param_frame(values) -> dict:
    """Каркас: все 21 ключ в порядке реестра, неизвестное — пустой строкой.

    Args:
        values: что известно; `None` и чужие ключи — законный ввод.

    Returns:
        dict: ровно `PROFILE_PARAM_KEYS` ключей, значения строками без краёв.

    ⚠ Чужие ключи **выбрасываются**, а не переносятся: попавшее в карточку мимо
    нашей двери иначе показалось бы в окне строкой без подписи.
    """
    got = values if isinstance(values, dict) else {}

    return {key: str(got.get(key) or '').strip() for key in PROFILE_PARAM_KEYS}


def profile_param_fields() -> list[dict]:
    """Реестр для страницы: ключ, подпись окна, вид поля.

    ⚠ Подписи отдаёт **сервер**: своя копия списка на странице разошлась бы с
    кодом молча. То же правило, что у словаря тегов галереи.
    """
    return [{'key': key, 'title': title, 'kind': kind}
            for key, title, _label, kind in PROFILE_PARAMS]


def profile_param_bio(params) -> list[str]:
    """Строки био: `Label: value` по непустым полям, в порядке реестра.

    ⚠ **Пустое не называется вовсе.** Строка «Hair: —» читается моделью как факт
    («цвет волос неизвестен»), и она может об этом заговорить. Молчание честнее:
    чего нет, о том не сказано. То же правило у био собеседника.
    """
    got = params if isinstance(params, dict) else {}

    lines = []
    for key, _title, label, _kind in PROFILE_PARAMS:
        value = str(got.get(key) or '').strip()
        if value:
            lines.append(f'{label}: {value}')

    return lines


def profile_param_max(key: str) -> int:
    """Предел длины значения по виду поля. Ноль — такого поля в реестре нет."""
    for one, _title, _label, kind in PROFILE_PARAMS:
        if one == key:
            return PROFILE_PARAM_AREA_MAX if kind == 'area' else PROFILE_PARAM_TEXT_MAX

    return 0
```

- [ ] **Step 4: Прогнать тест**

Run: `pytest tests/test_profile_param.py -q`
Expected: PASS, 17 passed

- [ ] **Step 5: Коммит**

```bash
git add src/dating/profile_param.py tests/test_profile_param.py
git commit -m "Параметры анкеты: реестр из 21 ключа с постоянным каркасом

Один список на троих читателей — окно, драйвер панели и сборку био.
Каркас отдаётся целиком всегда: «ключа нет» и «ключ пуст» иначе стали
бы двумя состояниями, которые пришлось бы различать в четырёх местах.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Параметры в био агента

**Files:**
- Modify: `src/dating/agent.py:368-404` (функция `agent_bio`)
- Test: `tests/test_agent_bio_params.py`

**Interfaces:**
- Consumes: `profile_param_bio`, `PROFILE_PARAM_BIO_MAX` (задача 2);
  `text_trim_lines` (задача 1).
- Produces: `agent_bio(card: dict) -> str` — та же подпись, другой состав.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_agent_bio_params.py`:

```python
"""Био девушки: проза плюс блок параметров.

`girl_bio` уезжает двумя дорогами — при заведении агента (`dating_girl_create`
добывает из неё факты) и на **каждом** ответе в чате (`answer.py`). Поэтому здесь
проверяется и состав, и предел длины.
"""

import pytest

from src.dating.agent import agent_bio

pytestmark = pytest.mark.profile

CARD = {'name': 'Olga', 'age': 36, 'city': 'Lviv', 'about': 'Red-haired woman.',
        'parameters': {'birthday': '1990-01-21', 'eyes': 'Hazel',
                       'height': '172 cm', 'hobby': 'swimming', 'hair': ''}}


class TestTheProseIsUntouched:
    """⚠ Первый абзац — то, что уезжало всегда; параметры его не меняют."""

    def test_the_prose_still_comes_first(self):
        got = agent_bio(CARD)

        assert got.startswith('My name is Olga. I am 36 years old. I live in Lviv.')

    def test_a_card_without_parameters_works_as_before(self):
        got = agent_bio({'name': 'Olga', 'age': 36, 'city': 'Lviv',
                         'about': 'Red-haired woman.'})

        assert got == 'My name is Olga. I am 36 years old. I live in Lviv. Red-haired woman.'

    def test_no_bio_rule_is_not_touched(self):
        """⚠ Параметры в условие «биографии нет» не входят: оно про анкету с сайта."""
        assert agent_bio({'name': 'Olga', 'parameters': {'eyes': 'Hazel'}}) == ''


class TestTheParameterBlock:
    def test_known_parameters_become_lines(self):
        got = agent_bio(CARD)

        assert 'Birthday: 1990-01-21' in got
        assert 'Eyes: Hazel' in got
        assert 'Height: 172 cm' in got

    def test_empty_parameters_are_not_named(self):
        assert 'Hair:' not in agent_bio(CARD)

    def test_the_block_is_separated_by_a_blank_line(self):
        """Чтобы модель видела границу между рассказом и справкой."""
        assert '\n\nBirthday: 1990-01-21' in agent_bio(CARD)

    def test_the_block_keeps_the_registry_order(self):
        got = agent_bio(CARD)

        assert got.index('Birthday:') < got.index('Eyes:') < got.index('Hobby:')


class TestTheLimit:
    """⚠⚠ Режется **только блок параметров**: проза идёт одной строкой, и обрезка
    по границе строки унесла бы её целиком, оставив агента без имени."""

    def test_a_huge_hobby_does_not_eat_the_prose(self):
        card = dict(CARD, parameters=dict(CARD['parameters'], hobby='x' * 5000))

        got = agent_bio(card)

        assert got.startswith('My name is Olga.')
        assert 'Birthday: 1990-01-21' in got, 'короткие строки должны уцелеть'
        assert 'x' * 5000 not in got, 'строка сверх предела должна отпасть'
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `pytest tests/test_agent_bio_params.py -q`
Expected: FAIL — `assert 'Birthday: 1990-01-21' in got` (параметры в био не попадают)

- [ ] **Step 3: Правка `agent_bio`**

В `src/dating/agent.py` добавить импорты после строки 56 (`import re`):

```python
from text_.text_trim_lines import text_trim_lines

from src.dating.profile_param import PROFILE_PARAM_BIO_MAX, profile_param_bio
```

Заменить хвост `agent_bio` (строки 395-404 — от `if about:` до `return`):

```python
    if about:
        parts.append(about)

    # Одно имя — ещё не биография: фактов из него не добыть, и агент вышел бы
    # пустым. Пусть лучше журнал скажет «сначала прочитайте анкету».
    if not about and not city and not age:
        return ''

    return ' '.join(parts)
```

на:

```python
    if about:
        parts.append(about)

    # Одно имя — ещё не биография: фактов из него не добыть, и агент вышел бы
    # пустым. Пусть лучше журнал скажет «сначала прочитайте анкету».
    #
    # ⚠ Параметры в это условие **не входят**: оно про анкету, прочитанную с
    # сайта, а параметры снимаются отдельной кнопкой и бывают у анкеты, которую
    # ещё ни разу не читали.
    if not about and not city and not age:
        return ''

    prose = ' '.join(parts)

    # Справка по полям анкеты — вторым абзацем, пустой строкой от прозы: модель
    # должна видеть границу между рассказом от первого лица и перечнем.
    #
    # ⚠⚠ Предел применяется **только к блоку**, а не ко всему тексту. Проза
    # собрана в одну строку, и обрезка по границе строки унесла бы её целиком —
    # агент остался бы без имени. Первая строка блока короткая при любой анкете.
    block = text_trim_lines('\n'.join(profile_param_bio(card.get('parameters'))),
                            PROFILE_PARAM_BIO_MAX)

    return f'{prose}\n\n{block}' if block else prose
```

Дописать в докстринг `agent_bio` после абзаца про язык:

```python
    ⚠ Параметры анкеты (рост, волосы, увлечения — реестр `profile_param.py`) идут
    **вторым абзацем**, строками «Label: value», и только непустые: «Hair: —»
    модель читает как факт и может об этом заговорить.
```

- [ ] **Step 4: Прогнать тесты**

Run: `pytest tests/test_agent_bio_params.py -q`
Expected: PASS, 9 passed

- [ ] **Step 5: Прогнать соседей — заведение агента не сломалось**

Run: `pytest -m profile -q -k "agent"`
Expected: PASS, все зелёные

- [ ] **Step 6: Коммит**

```bash
git add src/dating/agent.py tests/test_agent_bio_params.py
git commit -m "Био девушки: параметры анкеты вторым абзацем

Предел режет только блок параметров: проза собрана в одну строку, и
обрезка по границе строки унесла бы её целиком — агент остался бы без
имени.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Чтение формы панели

**Files:**
- Modify: `src/dating/panel_fetch.py` (добавить `panel_fetch_form` и класс `_Form`)
- Create: `tests/frames/ladadate_lady_edit.html`
- Test: `tests/test_panel_fetch_form.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `panel_fetch_form(html: str) -> dict` — `{имя поля: значение}`.

- [ ] **Step 1: Положить образец разметки**

Создать `tests/frames/ladadate_lady_edit.html` — **точное** содержимое в
приложении А в конце этого плана. Это форма со страницы
`/partner/agency/userprofile/edit/{uid}` живой панели Ladadate, снятая 12.10.2026:
разметка и имена полей настоящие, списки `option` укорочены до трёх с сохранением
выбранного, значения обезличены (правило папки — её `readme.md`).

Дописать в `tests/frames/readme.md` в конец:

```markdown
## Страница анкеты в админ-панели

`ladadate_lady_edit.html` — форма со страницы `/partner/agency/userprofile/edit/{ID}`,
снятая с живой панели 12.10.2026 читающим GET по удерживаемой сессии. Разметка и
**имена полей настоящие** — в них весь смысл образца: разбор идёт по ним, а не по
подписям. Списки `option` укорочены до трёх с сохранением выбранного, значения
обезличены по правилу этой папки.

⚠ Сущности здесь не случайны: `5&#39;8&quot; - 172 cm` — то, как панель пишет рост,
и разбор обязан их разворачивать (`convert_charrefs=True`).
```

- [ ] **Step 2: Написать падающий тест**

Создать `tests/test_panel_fetch_form.py`:

```python
"""Чтение формы админ-панели: input, выбранный option, textarea.

⚠ Это **семантика HTML**, а не свойство сайта: значение `input` лежит в `value`,
значение `select` — в тексте `option` с `selected`, значение `textarea` — в его
теле. Поэтому разбор общий, рядом с разбором таблицы, а карта имён — у драйвера.

Проверяется на **настоящем образце** (`tests/frames/ladadate_lady_edit.html`):
рукописная заглушка расходится с живой страницей молча.
"""

from pathlib import Path

import pytest

from src.dating.panel_fetch import panel_fetch_form

pytestmark = pytest.mark.profile

SAMPLE = (Path(__file__).parent / 'frames' / 'ladadate_lady_edit.html'
          ).read_text(encoding='utf-8')


class TestTheSample:
    """Настоящая страница — настоящие имена полей."""

    def test_input_value_is_read(self):
        assert panel_fetch_form(SAMPLE)['Name'] == 'Olga'

    def test_hidden_input_is_read_too(self):
        assert panel_fetch_form(SAMPLE)['AccountId'] == '100001'

    def test_number_input_is_read(self):
        assert panel_fetch_form(SAMPLE)['AgeFrom'] == '35'

    def test_select_gives_the_selected_option_text(self):
        """⚠ Текст, а не `value`: `value="208"` — внутренний номер сайта, он не
        говорит ни человеку, ни модели ничего."""
        assert panel_fetch_form(SAMPLE)['CountryId'] == 'Ukraine'

    def test_entities_are_unescaped(self):
        """⚠ Рост панель пишет сущностями: `5&#39;8&quot; - 172 cm`."""
        assert panel_fetch_form(SAMPLE)['HeightId'] == '5\'8" - 172 cm'

    def test_textarea_body_is_read(self):
        assert panel_fetch_form(SAMPLE)['Interest'] == 'swimming and dancing'

    def test_all_three_birthday_parts_are_read(self):
        got = panel_fetch_form(SAMPLE)

        assert (got['BirthdayDay'], got['BirthdayMonth'],
                got['BirthdayYear']) == ('21', 'Jan', '1990')


class TestTheEdges:
    def test_a_control_without_a_name_is_skipped(self):
        got = panel_fetch_form('<input type="text" value="x">')

        assert got == {}

    def test_a_select_without_a_selected_option_is_empty(self):
        got = panel_fetch_form(
            '<select name="A"><option value="1">one</option></select>')

        assert got['A'] == ''

    def test_an_empty_input_is_an_empty_string_not_absent(self):
        """⚠ Разница существенна: «поля нет» и «поле пусто» решают разное —
        первое значит «разметка сменилась»."""
        got = panel_fetch_form('<input name="A" type="text">')

        assert got == {'A': ''}

    def test_broken_markup_gives_what_it_managed_to_read(self):
        got = panel_fetch_form('<input name="A" value="1"><select name="B">')

        assert got['A'] == '1'

    def test_empty_input_is_legal(self):
        assert panel_fetch_form('') == {}
        assert panel_fetch_form(None) == {}
```

- [ ] **Step 3: Прогнать и убедиться, что падает**

Run: `pytest tests/test_panel_fetch_form.py -q`
Expected: FAIL — `ImportError: cannot import name 'panel_fetch_form'`

- [ ] **Step 4: Написать разбор формы**

В `src/dating/panel_fetch.py` добавить функцию сразу после `panel_fetch_table`
(то есть перед строкой `# ── Приватное ───`):

```python
def panel_fetch_form(html: str) -> dict:
    """Значения формы панели: имя поля → то, что в нём стоит.

    Returns:
        dict: `{имя: значение}` строками. Поля без `name` пропускаются; поле без
        значения даёт пустую строку, а не отсутствует вовсе.

    ⚠ **Пусто и «нет вовсе» — разные вещи, и обе нужны.** Пустое поле значит «у
    анкеты это не заполнено», отсутствие имени — «разметка сменилась», и второе
    обязано отличаться от первого: на нём стоит отказ читателя параметров.

    ⚠ У `select` берётся **текст** выбранного `option`, а не его `value`:
    `value="208"` — внутренний номер сайта, человеку и модели он не говорит
    ничего, а текст «Ukraine» говорит.

    ⚠ Разбор свой, а не библиотекой: в образе нет ни `bs4`, ни `lxml`, и тянуть их
    ради одной формы незачем. Та же причина, что у `panel_fetch_table`.
    """
    parser = _Form()
    try:
        parser.feed(str(html or ''))
    except Exception as e:
        logger_info(f'[dating] форма панели не разобралась: {e}')

    return parser.fields
```

В конец раздела «Приватное» (после класса `_Table`, перед `async def _session`)
добавить класс:

```python
class _Form(HTMLParser):
    """Разбор формы страницы: значения `input`, `select` и `textarea`.

    ⚠ Состояние — два указателя: в каком поле мы сейчас и собираем ли текст.
    Полный разбор DOM без библиотеки стоил бы вчетверо больше кода ради
    возможностей, которых здесь не спрашивают.

    ⚠⚠ `convert_charrefs=True` обязателен: панель пишет рост сущностями
    (`5&#39;8&quot; - 172 cm`), и без разворота значение уехало бы в био как есть.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}
        # Имя поля, текст которого собираем: `textarea` либо выбранный `option`.
        self._name = ''
        self._take = False
        # Имя текущего `select` и выбран ли у него уже вариант.
        self._select = ''

    def handle_starttag(self, tag: str, attrs: list) -> None:
        got = dict(attrs)
        name = str(got.get('name') or '').strip()

        if tag == 'input':
            if name:
                self.fields[name] = str(got.get('value') or '')
        elif tag == 'select':
            self._select = name
            if name:
                # ⚠ Заводим пустым сразу: `select` без выбранного варианта обязан
                # дать пустую строку, а не исчезнуть из ответа.
                self.fields[name] = ''
        elif tag == 'option':
            if self._select and 'selected' in got:
                self._name, self._take = self._select, True
                self.fields[self._name] = ''
        elif tag == 'textarea':
            if name:
                self._name, self._take = name, True
                self.fields[name] = ''

    def handle_endtag(self, tag: str) -> None:
        if tag in ('option', 'textarea'):
            self._take = False
            self._name = ''
        elif tag == 'select':
            self._select = ''

    def handle_data(self, data: str) -> None:
        if not self._take or not self._name:
            return

        self.fields[self._name] += data
```

⚠ Значения не обрезаются по краям здесь: это делает `profile_param_frame`, и
двойная обрезка в двух местах разошлась бы на первой правке.

- [ ] **Step 5: Прогнать тест**

Run: `pytest tests/test_panel_fetch_form.py -q`
Expected: PASS, 12 passed

- [ ] **Step 6: Коммит**

```bash
git add src/dating/panel_fetch.py tests/test_panel_fetch_form.py \
        tests/frames/ladadate_lady_edit.html tests/frames/readme.md
git commit -m "Панель: чтение значений формы рядом с чтением таблицы

Семантика HTML, а не свойство сайта: input из value, select из текста
выбранного option, textarea из тела. Проверяется на настоящем образце
страницы — рукописная заглушка расходится с живой молча.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Драйвер панели сайта

**Files:**
- Create: `src/dating/site/site_panel.py`
- Modify: `src/dating/site/site_registry.py`
- Test: `tests/test_site_panel.py`

**Interfaces:**
- Consumes: `PROFILE_PARAM_KEYS`, `profile_param_frame` (задача 2).
- Produces:
  - `DatingSitePanel` с атрибутами `name`, `path`, `FIELDS`, `BUILT`, `SOURCES` и
    методами `params(form: dict) -> dict`, `known(form: dict) -> int`;
  - `DatingSitePanelLadadate`;
  - `site_registry_panel(presence: dict | None) -> DatingSitePanel`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_site_panel.py`:

```python
"""Драйвер панели: карта имён и нормализация значений.

## ⚠ Почему нормализация здесь, а не в сборке био

`5'8" - 172 cm` — то, как пишет рост **Ladadate**. В общем коде это знание стало бы
миной для второго сайта: его панель напишет иначе, и чужая обрезка молча испортила
бы значение. Та же причина, по которой заведён `site_about.py`.
"""

from pathlib import Path

import pytest

from src.dating.panel_fetch import panel_fetch_form
from src.dating.profile_param import PROFILE_PARAM_KEYS
from src.dating.site.site_panel import DatingSitePanel, DatingSitePanelLadadate
from src.dating.site.site_registry import site_registry_panel

pytestmark = pytest.mark.profile

SAMPLE = (Path(__file__).parent / 'frames' / 'ladadate_lady_edit.html'
          ).read_text(encoding='utf-8')


@pytest.fixture
def ladadate():
    return DatingSitePanelLadadate({'driver': 'ladadate'})


class TestTheyAgree:
    """⚠⚠ Сторож согласия: карта драйвера и реестр — про одно и то же.

    Два списка про одно и то же расходятся **молча**: добавил ключ в реестр, забыл
    в карте — окно показывает пустую строку вечно; наоборот — драйвер кладёт ключ,
    которого не знает ни показ, ни био.
    """

    def test_every_registry_key_is_covered(self, ladadate):
        covered = set(ladadate.FIELDS.values()) | set(ladadate.BUILT)
        missed = set(PROFILE_PARAM_KEYS) - covered

        assert not missed, f'драйвер не знает ключей реестра: {sorted(missed)}'

    def test_the_driver_invents_nothing(self, ladadate):
        covered = set(ladadate.FIELDS.values()) | set(ladadate.BUILT)
        extra = covered - set(PROFILE_PARAM_KEYS)

        assert not extra, f'драйвер кладёт ключи вне реестра: {sorted(extra)}'

    def test_sources_feed_the_built_keys(self, ladadate):
        """⚠ `SOURCES` — поля формы, из которых собрано `BUILT`; на них же стоит
        проверка «разметка сменилась»."""
        assert ladadate.SOURCES == ('BirthdayDay', 'BirthdayMonth', 'BirthdayYear')


class TestTheSampleIsParsed:
    def test_simple_fields_land_as_is(self, ladadate):
        got = ladadate.params(panel_fetch_form(SAMPLE))

        assert got['country'] == 'Ukraine'
        assert got['occupation'] == 'teacher'
        assert got['eyes'] == 'Hazel'
        assert got['hobby'] == 'swimming and dancing'
        assert got['about_partner'] == 'kind and honest man'
        assert got['age_from'] == '35'

    def test_the_frame_is_whole(self, ladadate):
        got = ladadate.params(panel_fetch_form(SAMPLE))

        assert list(got) == list(PROFILE_PARAM_KEYS)


class TestTheNormalisation:
    def test_height_keeps_the_metric_half(self, ladadate):
        assert ladadate.params({'HeightId': '5\'8" - 172 cm'})['height'] == '172 cm'

    def test_weight_keeps_the_metric_half(self, ladadate):
        assert ladadate.params({'WeightId': '112 lb - 51 kg'})['weight'] == '51 kg'

    def test_a_value_without_a_dash_is_kept_whole(self, ladadate):
        """Сайт вправе писать иначе — тогда берём как есть, а не теряем."""
        assert ladadate.params({'HeightId': '172 cm'})['height'] == '172 cm'

    def test_birthday_is_built_from_three_fields(self, ladadate):
        got = ladadate.params({'BirthdayDay': '21', 'BirthdayMonth': 'Jan',
                               'BirthdayYear': '1990'})

        assert got['birthday'] == '1990-01-21'

    def test_a_single_digit_day_is_padded(self, ladadate):
        got = ladadate.params({'BirthdayDay': '5', 'BirthdayMonth': 'Mar',
                               'BirthdayYear': '1991'})

        assert got['birthday'] == '1991-03-05'

    def test_an_unknown_month_gives_nothing(self, ladadate):
        """⚠ Половина даты хуже её отсутствия: «1990-00-21» уехало бы в био."""
        got = ladadate.params({'BirthdayDay': '21', 'BirthdayMonth': '',
                               'BirthdayYear': '1990'})

        assert got['birthday'] == ''

    def test_an_incomplete_triple_gives_nothing(self, ladadate):
        got = ladadate.params({'BirthdayDay': '21', 'BirthdayMonth': 'Jan'})

        assert got['birthday'] == ''


class TestTheMarkupGuard:
    """⚠⚠ Без этого счётчика смена разметки обнулила бы параметры у всех анкет."""

    def test_the_sample_is_recognised(self, ladadate):
        assert ladadate.known(panel_fetch_form(SAMPLE)) >= 20

    def test_a_stranger_page_is_recognised_as_nothing(self, ladadate):
        assert ladadate.known({'Login': '', 'Password': ''}) == 0


class TestTheRegistry:
    def test_ladadate_gets_its_driver(self):
        assert isinstance(site_registry_panel({'driver': 'ladadate'}),
                          DatingSitePanelLadadate)

    def test_an_unknown_site_gets_the_base_one(self):
        got = site_registry_panel({'driver': 'goldenbride'})

        assert type(got) is DatingSitePanel

    def test_the_base_driver_has_no_path(self):
        """⚠ Пустой путь и значит «панель этого сайта читать нечем»."""
        assert site_registry_panel(None).path == ''

    def test_the_base_driver_gives_an_empty_frame(self):
        got = site_registry_panel(None).params({'HeightId': '172 cm'})

        assert set(got.values()) == {''}
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `pytest tests/test_site_panel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dating.site.site_panel'`

- [ ] **Step 3: Написать драйвер**

Создать `src/dating/site/site_panel.py`:

```python
"""Разбор страницы анкеты в админ-панели агентства: имена полей и нормализация.

Четвёртая половина драйвера сайта. Три прежних отвечают на свои вопросы: чат — про
кадры сокета, анкета девушки — про имена полей JSON, разбор страницы собеседника —
про классы в вёрстке. Эта — про **форму админ-панели**: как зовутся её поля и в
каком виде она пишет значения.

## ⚠ Почему по именам полей, а не по подписям

У собеседника-мужчины (`companion_about.py`) выбора не было: сценарий снимал весь
текст блока одной строкой, и подписи («Occupation», «Marital Status») были
единственными ориентирами. Здесь ориентир лучше: `name="HeightId"` — машинное имя,
оно переживёт перевёрстку, а подпись «HEIGHT» нарисована рядом и станет другой при
первом же переводе интерфейса.

## ⚠ Что делает нормализация и почему она здесь

Панель Ladadate пишет рост `5'8" - 172 cm`, вес `112 lb - 51 kg`, а дату рождения
кладёт в **три** поля. В био нужны `172 cm`, `51 kg` и `1990-01-21`. Это знание о
том, как пишет конкретная панель: в общем коде оно стало бы миной для второго
сайта, чья панель напишет иначе.

## ⚠⚠ `known` — не удобство, а предохранитель

Смена разметки у сайта означала бы, что форма разобралась, а знакомых имён в ней
нет. Без счётчика узнанных имён читатель параметров записал бы пустоту **всем
анкетам сразу**, по одному нажатию кнопки. Поэтому `known` считает, сколько наших
имён в форме нашлось, а решение «отказ» принимает `profile_panel.py`.
"""

from src.dating.profile_param import profile_param_frame

# Месяцы, как их пишет панель в `BirthdayMonth`. ⚠ Трёхбуквенные и по-английски —
# так на странице; русские названия сюда не попадут никогда.
SITE_PANEL_MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                     'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}

# Чем панель разделяет две системы мер в росте и весе: `5'8" - 172 cm`.
SITE_PANEL_UNIT_SPLIT = ' - '


class DatingSitePanel:
    """Панель, которой у сайта нет: путь пуст, параметров не бывает."""

    name = 'base'

    # Путь страницы анкеты от корня панели; `{uid}` — номер анкеты на сайте.
    # ⚠ Пусто значит «читать нечем», и это единственный выключатель занятия.
    path = ''

    # Имя поля формы → наш ключ, один к одному.
    FIELDS: dict = {}

    # Ключи, которые драйвер собирает сам из нескольких полей формы.
    BUILT: tuple = ()

    # Поля формы, из которых собрано `BUILT`. ⚠ Нужны `known`: без них страница с
    # одной только датой рождения считалась бы незнакомой.
    SOURCES: tuple = ()

    def __init__(self, presence: dict | None = None):
        self.presence = presence or {}

    def params(self, form: dict) -> dict:
        """Параметры анкеты из значений формы — полным каркасом.

        Args:
            form: то, что вернул `panel_fetch_form`.

        Returns:
            dict: ровно ключи реестра; неизвестное — пустой строкой.
        """
        got = form or {}
        values = {our: got.get(theirs, '') for theirs, our in self.FIELDS.items()}

        return profile_param_frame(values)

    def known(self, form: dict) -> int:
        """Сколько наших имён нашлось в форме. Ноль — разметка сменилась."""
        got = form or {}
        names = tuple(self.FIELDS) + tuple(self.SOURCES)

        return sum(1 for name in names if name in got)


class DatingSitePanelLadadate(DatingSitePanel):
    """Панель Ladadate: `/partner/agency/userprofile/edit/{ID}`."""

    name = 'ladadate'

    path = 'userprofile/edit/{uid}'

    FIELDS = {
        'CountryId': 'country', 'Occupation': 'occupation',
        'EducationId': 'education', 'EnglishId': 'english',
        'MaritalStateId': 'marital_state', 'ChildrenId': 'children',
        'ReligionId': 'religion', 'EthnicityId': 'ethnicity',
        'BodyTypeId': 'body', 'HeightId': 'height', 'WeightId': 'weight',
        'HairColorId': 'hair', 'HairStyleId': 'hair_style',
        'EyesColorId': 'eyes', 'SmokeId': 'smoke', 'DrinkId': 'drink',
        'AgeFrom': 'age_from', 'AgeTo': 'age_to',
        'Interest': 'hobby', 'AboutPartner': 'about_partner',
    }

    BUILT = ('birthday',)

    SOURCES = ('BirthdayDay', 'BirthdayMonth', 'BirthdayYear')

    def params(self, form: dict) -> dict:
        """То же, что у базового, плюс нормализация мер и сборка даты."""
        got = form or {}
        values = {our: got.get(theirs, '') for theirs, our in self.FIELDS.items()}

        values['height'] = _metric(values.get('height'))
        values['weight'] = _metric(values.get('weight'))
        values['birthday'] = _birthday(got.get('BirthdayDay'),
                                       got.get('BirthdayMonth'),
                                       got.get('BirthdayYear'))

        return profile_param_frame(values)


def _metric(value) -> str:
    """Метрическая половина меры: `5'8" - 172 cm` → `172 cm`.

    ⚠ Разделителя нет — возвращаем как есть, а не пустоту: сайт вправе писать
    иначе, и терять значение из-за этого незачем.
    """
    said = str(value or '').strip()

    return said.split(SITE_PANEL_UNIT_SPLIT)[-1].strip() if SITE_PANEL_UNIT_SPLIT in said else said


def _birthday(day, month, year) -> str:
    """Дата рождения из трёх полей панели: `21`, `Jan`, `1990` → `1990-01-21`.

    ⚠ **Неполная тройка и незнакомый месяц дают пустоту.** Половина даты хуже её
    отсутствия: `1990-00-21` уехало бы в био настоящей датой.
    """
    at = SITE_PANEL_MONTHS.get(str(month or '').strip().lower()[:3], 0)

    try:
        one, four = int(str(day or '').strip()), int(str(year or '').strip())
    except (TypeError, ValueError):
        return ''

    if not at or not one or four < 1900:
        return ''

    return f'{four:04d}-{at:02d}-{one:02d}'
```

- [ ] **Step 4: Подключить четвёртую таблицу в реестр**

В `src/dating/site/site_registry.py` добавить импорт после импорта `site_goldenbride`:

```python
from src.dating.site.site_panel import (DatingSitePanel,
                                        DatingSitePanelLadadate)
```

После словаря `DATING_SITE_ABOUT_DRIVERS` добавить:

```python
# Драйверы админ-панели агентства — по тем же именам, четвёртая таблица.
#
# ⚠ **Своя таблица, а не поле в прежних.** Панель отвечает на четвёртый вопрос: чат
# — про кадры сокета, анкета девушки — про имена полей JSON, разбор страницы
# собеседника — про классы вёрстки, эта — про имена полей формы панели. Сайт может
# совпасть с соседом в трёх и разойтись в четвёртом.
DATING_SITE_PANEL_DRIVERS = {
    DatingSitePanelLadadate.name: DatingSitePanelLadadate,
    DatingSitePanel.name: DatingSitePanel,
}
```

После функции `site_registry_about` добавить:

```python
def site_registry_panel(presence: dict | None = None) -> DatingSitePanel:
    """Чем читать страницу анкеты в админ-панели агентства этого сайта.

    ⚠ **Умолчание — базовый драйвер с пустым путём**, то есть «панели у этого сайта
    читать нечем», и ручка говорит это словами. Подставлять сюда чужой разбор
    нельзя: имена полей у панелей свои, и чужая карта дала бы пустые параметры,
    неотличимые от «анкета не заполнена».
    """
    if not isinstance(presence, dict):
        return DatingSitePanel()

    name = str(presence.get('driver') or '').strip()

    return DATING_SITE_PANEL_DRIVERS.get(name, DatingSitePanel)(presence)
```

- [ ] **Step 5: Прогнать тест**

Run: `pytest tests/test_site_panel.py -q`
Expected: PASS, 18 passed

- [ ] **Step 6: Коммит**

```bash
git add src/dating/site/site_panel.py src/dating/site/site_registry.py \
        tests/test_site_panel.py
git commit -m "Драйвер панели: карта имён и нормализация, четвёртой таблицей

Разбор по машинным именам полей, а не по подписям: имя переживёт
перевёрстку, подпись станет другой при первом переводе интерфейса.
Сторож согласия гоняет карту драйвера против реестра в обе стороны.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Составные отметки правки руками

**Files:**
- Modify: `src/dating/profile_card.py:57-65` (списки полей), `:90-102`
  (`profile_card_keep`), `:339-392` (`_card_apply`)
- Test: `tests/test_profile_card_params.py`

**Interfaces:**
- Consumes: `PROFILE_PARAM_KEYS`, `PROFILE_PARAM_PREFIX`, `profile_param_frame`
  (задача 2).
- Produces:
  - `PROFILE_CARD_OWN: tuple[str, ...]` — пять прежних полей;
  - `PROFILE_CARD_FIELDS` — те же пять плюс 21 имя `parameters.*`;
  - `profile_card_keep(fresh, edited)` — понимает вложенный `parameters`;
  - `profile_card_save(profile_id, values)` — принимает `values['parameters']`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_profile_card_params.py`:

```python
"""Отметки «правлено руками» у параметров: имена составные.

    profile_edited = ["about", "parameters.height", "parameters.hobby"]

Механизм тот же, что у пяти прежних полей: отметка защищает поле от перезаписи
сайтом, снимается ссылкой «вернуть сайту». Новое — только составное имя.
"""

import json

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load
from src.dating.profile_card import (PROFILE_CARD_FIELDS, PROFILE_CARD_OWN,
                                     profile_card_edited, profile_card_forget,
                                     profile_card_keep, profile_card_save)
from src.dating.profile_param import PROFILE_PARAM_KEYS

pytestmark = pytest.mark.profile


async def _card(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile FROM profile WHERE id = %s',
                         (int(profile_id),))

        return json_column_load((await db.fetchone() or {}).get('profile'))


async def _marks(profile_id: int) -> list:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile_edited FROM profile WHERE id = %s',
                         (int(profile_id),))

        return profile_card_edited((await db.fetchone() or {}).get('profile_edited'))


class TestTheFieldList:
    def test_every_parameter_is_editable(self):
        for key in PROFILE_PARAM_KEYS:
            assert f'parameters.{key}' in PROFILE_CARD_FIELDS

    def test_the_five_old_fields_are_still_there(self):
        assert PROFILE_CARD_OWN == ('id', 'name', 'age', 'city', 'about')

    def test_there_are_twenty_six_in_all(self):
        assert len(PROFILE_CARD_FIELDS) == 26


class TestKeepDropsNestedKeys:
    def test_a_marked_parameter_is_dropped(self):
        got = profile_card_keep({'parameters': {'height': '180 cm', 'eyes': 'Blue'}},
                                ['parameters.height'])

        assert got['parameters'] == {'eyes': 'Blue'}

    def test_unmarked_parameters_survive(self):
        got = profile_card_keep({'parameters': {'eyes': 'Blue'}}, ['about'])

        assert got['parameters'] == {'eyes': 'Blue'}

    def test_the_key_stays_even_when_everything_is_marked(self):
        """⚠⚠ Ключ остаётся **пустым словарём**, а не исчезает: на этом стоит
        глубокое слияние в `profile_card_merge` (задача 7)."""
        got = profile_card_keep({'parameters': {'height': '180 cm'}},
                                ['parameters.height'])

        assert got['parameters'] == {}

    def test_a_card_without_parameters_is_untouched(self):
        """⚠ Три прежних писателя ключа `parameters` не присылают вовсе."""
        got = profile_card_keep({'name': 'Olga'}, ['parameters.height'])

        assert got == {'name': 'Olga'}

    def test_parameters_at_passes_through(self):
        got = profile_card_keep({'parameters_at': '2026-10-12 16:32:00',
                                 'parameters': {}}, [])

        assert got['parameters_at'] == '2026-10-12 16:32:00'


class TestEditedKnowsCompositeNames:
    def test_a_parameter_mark_survives_the_filter(self):
        assert profile_card_edited(['parameters.height']) == ['parameters.height']

    def test_a_stranger_name_is_dropped(self):
        assert profile_card_edited(['parameters.шпион']) == []


class TestSavingParameters:
    async def test_a_changed_parameter_is_marked(self, girl):
        await profile_card_save(girl, {'login': f'kept-{girl}@example.com',
                                       'parameters': {'eyes': 'Hazel'}})

        assert 'parameters.eyes' in await _marks(girl)
        assert (await _card(girl))['parameters']['eyes'] == 'Hazel'

    async def test_an_unchanged_parameter_is_not_marked(self, girl):
        """⚠ Метится изменённое, а не присланное: «открыл поглядеть и сохранил»
        не должно заморозить все 21 поле разом."""
        login = f'kept-{girl}@example.com'
        await profile_card_save(girl, {'login': login,
                                       'parameters': {'eyes': 'Hazel'}})
        await profile_card_save(girl, {'login': login,
                                       'parameters': {'eyes': 'Hazel',
                                                      'hair': 'Red'}})

        marks = await _marks(girl)

        assert 'parameters.hair' in marks
        assert marks.count('parameters.eyes') == 1, 'отметка задвоилась'

    async def test_the_frame_is_whole_after_saving(self, girl):
        await profile_card_save(girl, {'login': f'kept-{girl}@example.com',
                                       'parameters': {'eyes': 'Hazel'}})

        assert list((await _card(girl))['parameters']) == list(PROFILE_PARAM_KEYS)

    async def test_strangers_are_not_saved(self, girl):
        await profile_card_save(girl, {'login': f'kept-{girl}@example.com',
                                       'parameters': {'шпион': 'да'}})

        assert 'шпион' not in (await _card(girl))['parameters']

    async def test_no_parameters_key_means_do_not_touch(self, girl):
        login = f'kept-{girl}@example.com'
        await profile_card_save(girl, {'login': login,
                                       'parameters': {'eyes': 'Hazel'}})
        await profile_card_save(girl, {'login': login, 'name': 'Olga'})

        assert (await _card(girl))['parameters']['eyes'] == 'Hazel'

    async def test_the_mark_can_be_given_back_to_the_site(self, girl):
        await profile_card_save(girl, {'login': f'kept-{girl}@example.com',
                                       'parameters': {'eyes': 'Hazel'}})

        left = await profile_card_forget(girl, 'parameters.eyes')

        assert 'parameters.eyes' not in left
        assert (await _card(girl))['parameters']['eyes'] == 'Hazel', \
            'значение должно остаться — обновит его следующее чтение панели'
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `pytest tests/test_profile_card_params.py -q`
Expected: FAIL — `ImportError: cannot import name 'PROFILE_CARD_OWN'`

- [ ] **Step 3: Расширить списки полей**

В `src/dating/profile_card.py` добавить импорт после строки 55:

```python
from src.dating.profile_param import (PROFILE_PARAM_KEYS, PROFILE_PARAM_PREFIX,
                                      profile_param_frame)
```

Заменить блок полей (строки 57-65):

```python
# Поля карточки, которые правят руками. ⚠ Имена — ключи самой карточки, а не
# подписи окна: по ним же ложится отметка, и второе написание развело бы их.
PROFILE_CARD_FIELDS = ('id', 'name', 'age', 'city', 'about')

# Чем «пусто» у каждого поля. ⚠ Нужно потому, что карточку собирают разные
# писатели и ключа может не быть вовсе: без этого «возраст 0 там, где ключа нет»
# читалось бы как правка, и окно, открытое поглядеть и сохранённое без изменений,
# заморозило бы возраст навсегда.
PROFILE_CARD_EMPTY = {'id': '', 'name': '', 'age': 0, 'city': '', 'about': ''}
```

на:

```python
# Поля карточки верхнего уровня, которые правят руками. ⚠ Имена — ключи самой
# карточки, а не подписи окна: по ним же ложится отметка, и второе написание
# развело бы их.
PROFILE_CARD_OWN = ('id', 'name', 'age', 'city', 'about')

# Всё правимое разом: свои поля плюс параметры составными именами.
#
# ⚠⚠ Составное имя (`parameters.height`), а не своя колонка отметок: механизм
# `profile_edited` уже есть, и вторая колонка рядом отвечала бы на тот же вопрос
# дважды. На этом списке стоят три проверки — отсев чужих имён в
# `profile_card_edited`, сверка в `profile_card_forget` и сверка в ручке.
PROFILE_CARD_FIELDS = PROFILE_CARD_OWN + tuple(
    PROFILE_PARAM_PREFIX + key for key in PROFILE_PARAM_KEYS)

# Чем «пусто» у каждого **своего** поля. ⚠ Нужно потому, что карточку собирают
# разные писатели и ключа может не быть вовсе: без этого «возраст 0 там, где ключа
# нет» читалось бы как правка, и окно, открытое поглядеть и сохранённое без
# изменений, заморозило бы возраст навсегда.
#
# ⚠ Параметров здесь нет: пусто у них у всех одно — пустая строка.
PROFILE_CARD_EMPTY = {'id': '', 'name': '', 'age': 0, 'city': '', 'about': ''}
```

- [ ] **Step 4: Научить `profile_card_keep` вложенному ключу**

Заменить тело `profile_card_keep` (строки 100-102):

```python
    skip = set(profile_card_edited(edited))

    return {name: value for name, value in (fresh or {}).items() if name not in skip}
```

на:

```python
    skip = set(profile_card_edited(edited))
    got = fresh or {}

    out = {name: value for name, value in got.items()
           if name not in skip and name != 'parameters'}

    # ⚠⚠ Ключ `parameters` остаётся **даже когда из него выброшено всё**: пустой
    # словарь говорит «параметры пришли, но все правлены руками», и на этом стоит
    # глубокое слияние в `profile_card_merge`. Исчезни он — слияние не узнало бы,
    # что параметры вообще приходили.
    if 'parameters' in got:
        inner = dict(got.get('parameters') or {})
        out['parameters'] = {key: value for key, value in inner.items()
                             if PROFILE_PARAM_PREFIX + key not in skip}

    return out
```

Дописать в докстринг `profile_card_keep` перед `Returns`:

```python
    ⚠ Вложенный `parameters` разбирается **поключево**: отметка `parameters.height`
    роняет только его, соседние остаются. Сам ключ при этом не исчезает никогда.
```

- [ ] **Step 5: Научить `_card_apply` параметрам**

В `_card_apply` заменить цикл по полям (строки 378-386):

```python
    for field in PROFILE_CARD_FIELDS:
        if field not in values:
            continue

        if _same(field, before.get(field), values[field]):
            continue

        card[field] = values[field]
        edited.add(field)
```

на:

```python
    for field in PROFILE_CARD_OWN:
        if field not in values:
            continue

        if _same(field, before.get(field), values[field]):
            continue

        card[field] = values[field]
        edited.add(field)

    # ⚠ Параметры — своим проходом, потому что лежат вложенным словарём, а правило
    # то же: метится **изменённое**, а не присланное. Ключа `parameters` в
    # присланном нет вовсе — не трогаем ничего, как и у прочих полей.
    if 'parameters' in values:
        was = dict(before.get('parameters') or {})
        now = dict(was)
        for key, value in (values.get('parameters') or {}).items():
            if key not in PROFILE_PARAM_KEYS:
                continue

            if str(was.get(key) or '').strip() == str(value or '').strip():
                continue

            now[key] = value
            edited.add(PROFILE_PARAM_PREFIX + key)

        # ⚠ Каркас восстанавливается на **каждой** записи руками: анкета, у которой
        # параметров не было вовсе, получает их здесь — и окно с био дальше видят
        # одно и то же у всех.
        card['parameters'] = profile_param_frame(now)
```

- [ ] **Step 6: Прогнать тесты**

Run: `pytest tests/test_profile_card_params.py -q`
Expected: PASS, 15 passed

- [ ] **Step 7: Прогнать прежние тесты карточки**

Run: `pytest -m profile -q -k "card"`
Expected: PASS, прежние проверки правки руками зелёные

- [ ] **Step 8: Коммит**

```bash
git add src/dating/profile_card.py tests/test_profile_card_params.py
git commit -m "Карточка: отметки правки у параметров составными именами

Механизм тот же, что у пяти прежних полей; новое — вложенный ключ.
Он не исчезает даже когда из него выброшено всё: пустой словарь значит
«параметры пришли, но все правлены руками».

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `profile_card_merge` — одна дверь вместо двух близнецов

**Files:**
- Modify: `src/dating/profile_card.py` (добавить `profile_card_merge`)
- Modify: `src/dating/profile_page.py:129-184` (`_profile_save`)
- Modify: `src/dating/profile_cache.py:209-250` (`_profile_save_card`)
- Test: `tests/test_profile_card_merge.py`

**Interfaces:**
- Consumes: `profile_card_keep` (задача 6).
- Produces: `profile_card_merge(profile_id: int, fresh: dict, note: str) -> dict` —
  кладёт пришедшее с сайта, уважая отметки; пустой словарь — записать не удалось.

**Зачем:** путь «сайт принёс карточку» написан **дважды, слово в слово**, и в
докстринге сказано, что близнецов «правят парой». Панель стала бы третьей копией —
ровно та грабля, что уже стоила пропущенного третьего писателя карточки.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_profile_card_merge.py`:

```python
"""Дверь «сайт принёс карточку»: слияние, уважающее правку руками.

## ⚠⚠ Почему одна функция, а не три копии

Путь был написан дважды и слово в слово — `profile_page._profile_save` и
`profile_cache._profile_save_card` отличались только фразой в журнале, и в
докстринге стояло «правятся парой». Чтение панели стало бы третьей копией.
"""

import json

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load
from src.dating.profile_card import profile_card_merge
from src.dating.profile_param import PROFILE_PARAM_KEYS

pytestmark = pytest.mark.profile


async def _put(profile_id: int, card: dict, marks=None) -> None:
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE profile SET profile = %s, profile_edited = %s WHERE id = %s',
            (json.dumps(card, ensure_ascii=False),
             json.dumps(marks) if marks else None, int(profile_id)))
        await db.connection.commit()


async def _card(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile FROM profile WHERE id = %s',
                         (int(profile_id),))

        return json_column_load((await db.fetchone() or {}).get('profile'))


class TestItRespectsTheMarks:
    async def test_a_marked_field_is_not_overwritten(self, girl):
        await _put(girl, {'name': 'Olga', 'city': 'Lviv'}, ['name'])

        await profile_card_merge(girl, {'name': 'Другая', 'city': 'Kyiv'}, 'тест')

        got = await _card(girl)

        assert got['name'] == 'Olga', 'правленое руками затёрто'
        assert got['city'] == 'Kyiv'

    async def test_a_marked_parameter_is_not_overwritten(self, girl):
        await _put(girl, {'parameters': {'height': '180 cm', 'eyes': 'Blue'}},
                   ['parameters.height'])

        await profile_card_merge(
            girl, {'parameters': {'height': '172 cm', 'eyes': 'Hazel'}}, 'тест')

        got = (await _card(girl))['parameters']

        assert got['height'] == '180 cm', 'правленый руками рост затёрт'
        assert got['eyes'] == 'Hazel'


class TestTheDeepMerge:
    """⚠⚠ Плоское слияние стирало бы правленое руками целиком."""

    async def test_everything_marked_survives(self, girl):
        """Все 21 поля правлены руками: пришедшее даёт пустой `parameters`, и
        плоское `{**before, **kept}` положило бы его поверх полного."""
        full = {key: f'{key}-ручной' for key in PROFILE_PARAM_KEYS}
        marks = [f'parameters.{key}' for key in PROFILE_PARAM_KEYS]
        await _put(girl, {'parameters': full}, marks)

        await profile_card_merge(
            girl, {'parameters': {key: 'с-сайта' for key in PROFILE_PARAM_KEYS}},
            'тест')

        got = (await _card(girl))['parameters']

        assert got == full, 'правленое руками стёрто пустым словарём'

    async def test_old_parameters_survive_a_partial_fresh(self, girl):
        await _put(girl, {'parameters': {'eyes': 'Hazel', 'hair': 'Red'}})

        await profile_card_merge(girl, {'parameters': {'eyes': 'Blue'}}, 'тест')

        got = (await _card(girl))['parameters']

        assert got == {'eyes': 'Blue', 'hair': 'Red'}


class TestOtherWritersDoNotLoseParameters:
    """⚠ Страница кабинета и кэш анкеты ключа `parameters` не присылают."""

    async def test_a_card_without_parameters_leaves_them_alone(self, girl):
        await _put(girl, {'name': 'Olga', 'parameters': {'eyes': 'Hazel'}})

        await profile_card_merge(girl, {'name': 'Другая'}, 'тест')

        got = await _card(girl)

        assert got['name'] == 'Другая'
        assert got['parameters'] == {'eyes': 'Hazel'}


class TestImportKeepsParametersItsOwnWay:
    """⚠⚠ Импорт через `profile_card_merge` **не** ходит, и это не упущение.

    У него решение и запись разведены намеренно: `import_plan` — чистая функция,
    она только планирует карточку (`{**before, **fresh}`), а `UPDATE` делает роутер
    импорта. Параметры переживают его потому, что `before` — прежняя карточка
    целиком. Проверяем именно это: своего ключа импорт не присылает, прежний обязан
    уцелеть.
    """

    def test_the_planned_card_keeps_the_old_parameters(self):
        from src.dating.import_ import import_plan

        found = {'id': 1, 'login': 'olga@example.com',
                 'card': {'name': 'Olga', 'parameters': {'eyes': 'Hazel'}},
                 'edited': None}

        got = import_plan({'login': 'olga@example.com', 'name': 'Другая'},
                          found, 'https://site.test')

        assert got['card']['parameters'] == {'eyes': 'Hazel'}, \
            'импорт потерял параметры — он кладёт карточку поверх прежней'


class TestItNeverRaises:
    async def test_a_missing_profile_gives_an_empty_dict(self):
        """⚠ Не бросает: вход и «Проверка» зовут её последним шагом, и терять
        открытую сессию из-за неудачной записи незачем."""
        assert await profile_card_merge(10_000_000, {'name': 'x'}, 'тест') == {}
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `pytest tests/test_profile_card_merge.py -q`
Expected: FAIL — `ImportError: cannot import name 'profile_card_merge'`

- [ ] **Step 3: Написать `profile_card_merge`**

В `src/dating/profile_card.py` добавить импорт после строки 53:

```python
from logging_.logging_ import logger_info
```

Добавить функцию после `profile_card_forget` (перед `profile_card_why`):

```python
async def profile_card_merge(profile_id: int, fresh: dict, note: str) -> dict:
    """Положить пришедшее с сайта, не затирая правленого руками.

    Args:
        profile_id: наш номер анкеты.
        fresh: карточка или её часть, как её отдал сайт. Ключа нет — поле не
            трогается вовсе.
        note: чем назвать неудачу в журнале — у каждого вызывающего своя фраза.

    Returns:
        dict: карточка, как она **легла**. Пустой словарь значит «записать не
        удалось»; вызывающие на это не падают.

    ## ⚠⚠ Одна дверь вместо двух близнецов

    Этот путь был написан дважды и слово в слово — `profile_page._profile_save` и
    `profile_cache._profile_save_card` отличались только фразой в журнале, и в
    докстринге обоих стояло «правятся парой». Чтение панели стало бы третьей
    копией, то есть ровно той граблёй, что уже стоила проекту пропущенного
    третьего писателя карточки.

    ## ⚠⚠ Слияние `parameters` — глубокое, и это не придирка

    `profile_card_keep` выбрасывает из пришедшего помеченные подключи. У анкеты,
    где человек правил **все** 21 поле, она вернёт `{'parameters': {}}` — и
    плоское `{**before, **kept}` положило бы пустой словарь поверх полного,
    стерев правленое руками целиком. У той самой анкеты, которую заполняли
    дольше всех.

    ⚠ Это **единственное** место с глубоким слиянием, и держится оно на том, что
    вложенный ключ в карточке один. Появится второй — правило придётся обобщать,
    а не копировать строкой ниже.

    ## ⚠ Сверки снимка здесь нет

    Её не было и у обоих близнецов. Безусловный `UPDATE` уносит то, что записали в
    щель между чтением и записью; у писателей со стороны сайта это маловероятно
    (круги по одной анкете не ходят парами), а у правки руками сверка есть
    (`_card_claim`). Признак завести её и здесь: второй круг, пишущий карточку.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT profile, profile_edited FROM profile '
                             ' WHERE id = %s', (int(profile_id),))
            row = dict(await db.fetchone() or {})
            if not row:
                raise LookupError(f'анкеты #{int(profile_id)} нет')

            before = json_column_load(row.get('profile'))
            kept = profile_card_keep(fresh, row.get('profile_edited'))
            card = {**before, **kept}

            if 'parameters' in kept:
                card['parameters'] = {**(before.get('parameters') or {}),
                                      **kept['parameters']}

            await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                             (json.dumps(card, ensure_ascii=False),
                              int(profile_id)))
            await db.connection.commit()

        return card
    except Exception as e:
        logger_info(f'[dating] {note}: {e}')

        return {}
```

- [ ] **Step 4: Прогнать тест**

Run: `pytest tests/test_profile_card_merge.py -q`
Expected: PASS, 7 passed

- [ ] **Step 5: Перевести первого близнеца**

В `src/dating/profile_page.py` заменить тело `_profile_save` (строки 168-184, от
`try:` до конца функции):

```python
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

на:

```python
    return await profile_card_merge(profile_id, profile, 'анкета не записалась')
```

Заменить в докстринге `_profile_save` раздел «⚠⚠ У этой функции есть близнец»
целиком на:

```python
    ## ⚠ Слияние живёт не здесь

    Правило «положить пришедшее, не затирая правленого руками» написано один раз —
    `profile_card.profile_card_merge`. Прежде оно стояло здесь и в
    `profile_cache._profile_save_card` двумя копиями слово в слово, и в докстринге
    обоих было написано «правятся парой»; третьим копию потребовало чтение панели,
    и копии не стало.
```

Заменить импорт в `src/dating/profile_page.py`:

```python
from src.dating.profile_card import profile_card_keep
```

на:

```python
from src.dating.profile_card import profile_card_merge
```

Проверить, какие импорты осиротели, и убрать **только** их:

```bash
grep -n "json\.dumps\|json\.loads\|logger_info\|mysql_get_db_async\|json_column_load" \
     src/dating/profile_page.py
```

Кандидаты — `import json`, `logger_info`, `mysql_get_db_async`, `json_column_load`:
после переезда их использовал только `_profile_save`. Оставить те, что встречаются
в выводе `grep` в других функциях (`_profile_build` зовёт `json_column_load`).

- [ ] **Step 6: Перевести второго близнеца**

В `src/dating/profile_cache.py` заменить тело `_profile_save_card` (от `try:` до
конца функции) на:

```python
    return await profile_card_merge(profile_id, card, 'кэш анкеты не записался')
```

Заменить в его докстринге раздел «⚠⚠ Слияние, а не замена» на:

```python
    ## ⚠ Слияние живёт не здесь

    Прежде здесь стоял `UPDATE profile SET profile = %s` — замена целиком, и с
    появлением правки руками это стало затиранием. Чинилось это слиянием, но
    слияние было написано **дважды**: здесь и в `profile_page._profile_save`.
    Теперь оно одно — `profile_card.profile_card_merge`, и объяснение правила
    лежит там же.
```

Заменить импорт `profile_card_keep` на `profile_card_merge` и убрать осиротевшие
импорты тем же способом.

- [ ] **Step 7: Прогнать тесты обоих писателей**

Run: `pytest -m profile -q -k "profile_page or profile_cache or card or merge"`
Expected: PASS, все зелёные

- [ ] **Step 8: Прогнать группу целиком**

Run: `pytest -m profile -q`
Expected: PASS

- [ ] **Step 9: Коммит**

```bash
git add src/dating/profile_card.py src/dating/profile_page.py \
        src/dating/profile_cache.py tests/test_profile_card_merge.py
git commit -m "Карточка: «сайт принёс» одной дверью вместо двух близнецов

Путь был написан дважды и слово в слово, с пометкой «правятся парой».
Чтение панели стало бы третьей копией. Слияние parameters глубокое:
плоское стирало бы правленое руками у анкеты, где правили всё.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Снятие параметров с панели

**Files:**
- Create: `src/dating/profile_panel.py`
- Test: `tests/test_profile_panel.py`

**Interfaces:**
- Consumes: `panel_fetch_html`, `panel_fetch_form` (задача 4);
  `site_registry_panel` (задача 5); `profile_card_merge` (задача 7).
- Produces:
  - `DatingProfilePanelError(RuntimeError)`;
  - `profile_panel_read(profile: dict, presence: dict) -> dict` — возвращает
    `{'parameters': dict, 'parameters_at': str}`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_profile_panel.py`:

```python
"""Снятие параметров анкеты со страницы админ-панели.

## ⚠⚠ Главный сторож здесь — отказ при сменившейся разметке

Форма разобралась, а знакомых имён в ней нет — значит сайт перевёрстан. Записать в
этот миг пустоту значило бы обнулить параметры у **всех** анкет сразу, по одному
нажатию кнопки. Пустой разбор — это отказ, а не пустой результат.
"""

import json
from pathlib import Path

import httpx
import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_load
from src.dating.profile_panel import (DatingProfilePanelError,
                                      profile_panel_read)
from src.dating import panel_fetch as panel_fetch_module
from tests.panel_fake import PANEL_FAKE_STATE, PANEL_FAKE_URL

pytestmark = pytest.mark.profile

SITE = 999_783
SAMPLE = (Path(__file__).parent / 'frames' / 'ladadate_lady_edit.html'
          ).read_text(encoding='utf-8')
PRESENCE = {'driver': 'ladadate'}


@pytest.fixture
async def site(auth_client):
    """Сайт с заданной панелью и живой сессией. Что завела, то и убрала."""
    before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
              ['result']['settings'])

    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {'admin_url': PANEL_FAKE_URL,
                                             'admin_mark': 'Ladies profiles'}})

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET panel_state = %s WHERE site_id = %s',
                         (PANEL_FAKE_STATE, SITE))
        await db.connection.commit()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET panel_state = NULL WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()


@pytest.fixture
def page(monkeypatch):
    """Панель, отдающая страницу анкеты. Поле `html` — что она вернёт."""

    class Page:
        def __init__(self):
            self.html = SAMPLE
            self.paths = []

        def answer(self, request: httpx.Request) -> httpx.Response:
            self.paths.append(request.url.path)

            return httpx.Response(200, text=self.html)

    fake = Page()

    def client(proxy: str = ''):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.answer),
                                 follow_redirects=True)

    monkeypatch.setattr(panel_fetch_module, 'http_heartbeat_client', client)

    return fake


async def _profile(profile_id: int, card: dict) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                         (json.dumps(card, ensure_ascii=False), int(profile_id)))
        await db.connection.commit()
        await db.execute('SELECT id, site_id, profile, profile_id FROM profile'
                         ' WHERE id = %s', (int(profile_id),))

        return dict(await db.fetchone())


async def _card(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile FROM profile WHERE id = %s',
                         (int(profile_id),))

        return json_column_load((await db.fetchone() or {}).get('profile'))


class TestItReads:
    async def test_parameters_land_in_the_card(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001', 'name': 'Olga'})

        await profile_panel_read(row, PRESENCE)

        got = (await _card(one))['parameters']

        assert got['country'] == 'Ukraine'
        assert got['height'] == '172 cm'
        assert got['birthday'] == '1990-01-21'

    async def test_the_path_carries_the_site_number(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001'})

        await profile_panel_read(row, PRESENCE)

        assert page.paths[-1].endswith('/userprofile/edit/100001')

    async def test_the_read_mark_is_set(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001'})

        got = await profile_panel_read(row, PRESENCE)

        assert len(got['parameters_at']) == 19, 'отметка не вида YYYY-MM-DD HH:MM:SS'
        assert (await _card(one))['parameters_at'] == got['parameters_at']

    async def test_the_other_card_fields_survive(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001', 'name': 'Olga',
                                   'about': 'мой текст'})

        await profile_panel_read(row, PRESENCE)

        got = await _card(one)

        assert got['name'] == 'Olga'
        assert got['about'] == 'мой текст'


class TestTheMarkupGuard:
    """⚠⚠ Пустой разбор — отказ, а не пустой результат."""

    async def test_a_changed_markup_is_refused(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001',
                                   'parameters': {'eyes': 'Hazel'}})
        page.html = '<form><input name="СовсемДругое" value="1"></form>'

        with pytest.raises(DatingProfilePanelError) as got:
            await profile_panel_read(row, PRESENCE)

        assert 'разметка' in str(got.value)

    async def test_nothing_is_written_when_refused(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001',
                                   'parameters': {'eyes': 'Hazel'}})
        page.html = '<form><input name="СовсемДругое" value="1"></form>'

        with pytest.raises(DatingProfilePanelError):
            await profile_panel_read(row, PRESENCE)

        assert (await _card(one))['parameters'] == {'eyes': 'Hazel'}, \
            'прежние параметры обнулены сменившейся разметкой'


class TestTheRefusals:
    async def test_no_number_on_the_site(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'name': 'Olga'})
        row['profile_id'] = None

        with pytest.raises(DatingProfilePanelError) as got:
            await profile_panel_read(row, PRESENCE)

        assert 'номера на сайте' in str(got.value)

    async def test_a_site_without_a_panel_driver(self, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        row = await _profile(one, {'id': '100001'})

        with pytest.raises(DatingProfilePanelError) as got:
            await profile_panel_read(row, {'driver': 'goldenbride'})

        assert 'читать нечем' in str(got.value)
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `pytest tests/test_profile_panel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.dating.profile_panel'`

- [ ] **Step 3: Написать читатель**

Создать `src/dating/profile_panel.py`:

```python
"""Параметры анкеты со страницы админ-панели агентства.

Страница `…/partner/agency/userprofile/edit/{ID}` — обычная серверная форма, и
сессия панели у нас уже есть: её держит круг (`panel_round.py`). Значит браузер не
нужен вовсе — один GET по cookie, доли секунды.

## ⚠ Кто зовёт и когда

Только кнопка в окне правки карточки. Круга, который обходил бы анкеты сам, нет
намеренно: анкета меняется раз в месяцы, и сторож, будящийся ради «ничего нового»,
— сторож без предмета. Признак пересмотреть: параметры у половины анкет не сняты ни
разу.

## ⚠⚠ Сменившаяся разметка — отказ, а не пустой результат

Форма разобралась, а знакомых имён в ней нет — это перевёрстка у сайта. Запиши мы в
этот миг пустоту, и параметры обнулились бы у **всех** анкет сразу, по одному
нажатию кнопки. Порог — «ни одного знакомого имени», а не «меньше половины»: сайт
вправе убрать одно поле, и половинчатый порог отказывал бы на законных изменениях.
"""

from datetime_.datetime_ import datetime_utc_now

from src.dating.json_column import json_column_load
from src.dating.panel_fetch import panel_fetch_form, panel_fetch_html
from src.dating.profile_card import profile_card_merge
from src.dating.site.site_registry import site_registry_panel


class DatingProfilePanelError(RuntimeError):
    """Параметры не снялись. Текст годен и для журнала, и для ответа ручки."""


# Вид отметки о чтении. ⚠ Тот же, что понимает `timeShift` на странице: она ждёт
# ровно `YYYY-MM-DD HH:MM:SS` и короткую строку возвращает как есть.
PROFILE_PANEL_AT_FORMAT = '%Y-%m-%d %H:%M:%S'


async def profile_panel_read(profile: dict, presence: dict) -> dict:
    """Снять параметры анкеты с панели и положить в карточку.

    Args:
        profile: строка `profile` — нужны `id`, `site_id`, `profile` и
            `profile_id`.
        presence: настройка сайта (`browser_site.presence`) — по ней выбирается
            драйвер панели.

    Returns:
        dict: `parameters` — что легло, `parameters_at` — когда снято.

    Raises:
        DatingProfilePanelError: у анкеты нет номера на сайте; у сайта нет
            драйвера панели; панель не ответила или увела на вход; разметка
            сменилась. Причина словами — её показывает человеку ручка.
    """
    card = json_column_load(profile.get('profile'))
    uid = str(card.get('id') or profile.get('profile_id') or '').strip()
    if not uid:
        raise DatingProfilePanelError('у анкеты нет номера на сайте')

    driver = site_registry_panel(presence)
    if not driver.path:
        raise DatingProfilePanelError('панель этого сайта читать нечем')

    # ⚠ Отказ панели (`DatingPanelFetchError`) наверх идёт как есть: он уже
    # разобран словами — «панель увела на вход», «панель ответила 500».
    html = await panel_fetch_html(int(profile['site_id']),
                                  driver.path.format(uid=uid))
    form = panel_fetch_form(html)

    if not driver.known(form):
        raise DatingProfilePanelError(
            'на странице панели не нашлось ни одного поля — '
            'похоже, разметка сменилась')

    at = datetime_utc_now().strftime(PROFILE_PANEL_AT_FORMAT)
    # ⚠ Отметка уезжает **тем же** `UPDATE`, что и значения: разойтись им нечем.
    stored = await profile_card_merge(
        int(profile['id']), {'parameters': driver.params(form),
                             'parameters_at': at},
        'параметры анкеты не записались')

    return {'parameters': stored.get('parameters') or {},
            'parameters_at': str(stored.get('parameters_at') or '')}
```

- [ ] **Step 4: Прогнать тест**

Run: `pytest tests/test_profile_panel.py -q`
Expected: PASS, 8 passed

- [ ] **Step 5: Коммит**

```bash
git add src/dating/profile_panel.py tests/test_profile_panel.py
git commit -m "Параметры анкеты: снятие со страницы панели

Один GET по удерживаемой сессии вместо браузера. Сменившаяся разметка —
отказ, а не пустой результат: запись пустоты обнулила бы параметры у
всех анкет сразу, по одному нажатию кнопки.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Ручки

**Files:**
- Modify: `src/uvicorn/routers/dating/profile_view.py:331-346` (`GET /card`)
- Modify: `src/uvicorn/routers/dating/profile_.py:76-110` (модель),
  `:320-355` (`PUT /card`)
- Modify: `src/uvicorn/routers/dating/profile_action.py` (новая ручка)
- Test: `tests/test_profile_card_api.py`

**Interfaces:**
- Consumes: `profile_param_fields`, `profile_param_frame`, `profile_param_max`,
  `PROFILE_PARAM_KEYS` (задача 2); `profile_card_save` (задача 6);
  `profile_panel_read`, `DatingProfilePanelError` (задача 8).
- Produces:
  - `GET /api/dating/profile/{id}/card` → в `result` добавлены `parameters`,
    `parameters_at`, `parameter_fields`;
  - `PUT /api/dating/profile/{id}/card` принимает `parameters: dict[str, str]`;
  - `POST /api/dating/profile/{id}/card/parameters` → `{'result': {...}}`.

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_profile_card_api.py`:

```python
"""Ручки карточки: параметры наружу, внутрь и с панели."""

import json
from pathlib import Path

import httpx
import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import panel_fetch as panel_fetch_module
from src.dating.profile_param import PROFILE_PARAM_KEYS
from tests.panel_fake import PANEL_FAKE_STATE, PANEL_FAKE_URL

pytestmark = pytest.mark.profile

SITE = 999_784
SAMPLE = (Path(__file__).parent / 'frames' / 'ladadate_lady_edit.html'
          ).read_text(encoding='utf-8')


@pytest.fixture
async def site(auth_client):
    before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
              ['result']['settings'])

    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {'admin_url': PANEL_FAKE_URL,
                                             'admin_mark': 'Ladies profiles'}})

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET panel_state = %s WHERE site_id = %s',
                         (PANEL_FAKE_STATE, SITE))
        await db.connection.commit()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET panel_state = NULL WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()


@pytest.fixture
def page(monkeypatch):
    class Page:
        def __init__(self):
            self.html = SAMPLE

        def answer(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=self.html)

    fake = Page()
    monkeypatch.setattr(
        panel_fetch_module, 'http_heartbeat_client',
        lambda proxy='': httpx.AsyncClient(
            transport=httpx.MockTransport(fake.answer), follow_redirects=True))

    return fake


async def _uid(profile_id: int, uid: str) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                         (json.dumps({'id': uid}), int(profile_id)))
        await db.connection.commit()


class TestTheCardGives:
    async def test_the_frame_always_comes(self, auth_client, girl):
        got = (await auth_client.get(f'/api/dating/profile/{girl}/card')).json()

        assert list(got['result']['parameters']) == list(PROFILE_PARAM_KEYS)

    async def test_the_registry_comes_with_it(self, auth_client, girl):
        """⚠ Подписи отдаёт сервер: своя копия на странице разошлась бы молча."""
        got = (await auth_client.get(f'/api/dating/profile/{girl}/card')).json()
        fields = got['result']['parameter_fields']

        assert len(fields) == 21
        assert fields[0]['key'] == 'birthday'
        assert fields[0]['title'] == 'Дата рождения'

    async def test_the_read_mark_is_a_string_even_when_empty(self, auth_client, girl):
        """⚠ Поле отдаётся всегда: `undefined` на странице читался бы как
        «не снимали» случайно, а не по правилу."""
        got = (await auth_client.get(f'/api/dating/profile/{girl}/card')).json()

        assert got['result']['parameters_at'] == ''


class TestTheCardTakes:
    async def test_a_parameter_is_saved_and_marked(self, auth_client, girl):
        await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'eyes': 'Hazel'}})

        got = (await auth_client.get(f'/api/dating/profile/{girl}/card')).json()

        assert got['result']['parameters']['eyes'] == 'Hazel'
        assert 'parameters.eyes' in got['result']['edited']

    async def test_a_stranger_key_is_refused(self, auth_client, girl):
        """⚠ 422, а не молчаливый отсев: чужой ключ — ошибка вызывающего."""
        got = await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'шпион': 'да'}})

        assert got.status_code == 422

    async def test_a_too_long_area_is_refused(self, auth_client, girl):
        got = await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'hobby': 'x' * 2001}})

        assert got.status_code == 422

    async def test_a_too_long_text_is_refused(self, auth_client, girl):
        got = await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'eyes': 'x' * 191}})

        assert got.status_code == 422

    async def test_the_mark_can_be_given_back(self, auth_client, girl):
        await auth_client.put(f'/api/dating/profile/{girl}/card', json={
            'login': f'kept-{girl}@example.com',
            'parameters': {'eyes': 'Hazel'}})

        got = await auth_client.delete(
            f'/api/dating/profile/{girl}/card/parameters.eyes')

        assert got.status_code == 200
        assert 'parameters.eyes' not in got.json()['result']['edited']


class TestTheRefreshButton:
    async def test_it_reads_the_panel(self, auth_client, site, page, girl_make):
        one = await girl_make(site_id=SITE)
        await _uid(one, '100001')

        got = await auth_client.post(
            f'/api/dating/profile/{one}/card/parameters')

        assert got.status_code == 200
        assert got.json()['result']['parameters']['country'] == 'Ukraine'

    async def test_a_changed_markup_answers_502(self, auth_client, site, page,
                                                girl_make):
        one = await girl_make(site_id=SITE)
        await _uid(one, '100001')
        page.html = '<form><input name="СовсемДругое" value="1"></form>'

        got = await auth_client.post(
            f'/api/dating/profile/{one}/card/parameters')

        assert got.status_code == 502
        assert 'разметка' in got.json()['detail']

    async def test_no_number_answers_422(self, auth_client, site, page, girl_make):
        one = await girl_make(site_id=SITE)

        got = await auth_client.post(
            f'/api/dating/profile/{one}/card/parameters')

        assert got.status_code == 422
        assert 'номера на сайте' in got.json()['detail']

    async def test_an_unknown_profile_answers_404(self, auth_client):
        got = await auth_client.post(
            '/api/dating/profile/10000000/card/parameters')

        assert got.status_code == 404
```

- [ ] **Step 2: Прогнать и убедиться, что падает**

Run: `pytest tests/test_profile_card_api.py -q`
Expected: FAIL — `KeyError: 'parameters'` в первом же тесте

- [ ] **Step 3: `GET /card` отдаёт параметры и реестр**

В `src/uvicorn/routers/dating/profile_view.py` добавить импорт рядом с
`profile_card_edited` (строка 26):

```python
from src.dating.profile_param import profile_param_fields, profile_param_frame
```

В `return` ручки `profile_view_card` (строка 346) добавить три ключа:

```python
    return {'result': {'profile': card, 'edited': edited, 'site_url': site_url,
                       # ⚠ Каркас отдаётся **всегда и целиком**: окно рисует одни
                       # и те же строки у любой анкеты, а `undefined` на странице
                       # читался бы как «параметров нет» случайно, а не по правилу.
                       'parameters': profile_param_frame(card.get('parameters')),
                       'parameters_at': str(card.get('parameters_at') or ''),
                       # ⚠ Подписи и вид поля — **с сервера**: своя копия списка на
                       # странице разошлась бы с кодом молча. То же правило, что у
                       # словаря тегов галереи.
                       'parameter_fields': profile_param_fields(),
```

(остальные ключи `login`, `contacts`, `enabled` и далее остаются как есть)

- [ ] **Step 4: `PUT /card` принимает параметры**

В `src/uvicorn/routers/dating/profile_.py` добавить импорт рядом со строкой 45:

```python
from src.dating.profile_param import profile_param_max
```

В класс `DatingProfileCard` добавить поле после `about` (строка 94) и проверяющий:

```python
    parameters: dict[str, str] = Field(
        default_factory=dict,
        description='Параметры анкеты: ключи реестра `profile_param.py`. '
                    'Ключа нет — поле не трогается')

    @field_validator('parameters')
    @classmethod
    def _parameters_known(cls, value: dict) -> dict:
        """⚠ Чужой ключ — отказ, а не молчаливый отсев.

        Молчаливый отсев прошёл бы незамеченным у страницы, которая разошлась с
        реестром: человек правит поле, панель отвечает «сохранено», значение не
        ложится. Пусть лучше 422 скажет, что страница устарела.

        ⚠ Предел длины берётся из реестра по виду поля: свой набор границ здесь
        разошёлся бы с ним на первой правке.
        """
        for key, one in (value or {}).items():
            limit = profile_param_max(key)
            if not limit:
                raise ValueError(f'параметра «{key}» в реестре анкеты нет')

            if len(str(one or '')) > limit:
                raise ValueError(
                    f'параметр «{key}» длиннее {limit} знаков')

        return {key: str(one or '').strip() for key, one in (value or {}).items()}
```

В докстринг класса дописать:

```python
    ⚠ `parameters` приезжает **словарём целиком**, а не полем на каждый ключ: их 21,
    и двадцать одно поле модели повторяло бы реестр вторым написанием.
```

В ручке `profile_card_put` дополнить вызов (строка 345):

```python
        got = await profile_card_save(profile_id, {
            'login': data.login, 'id': data.profile_id, 'name': data.name,
            'age': data.age, 'city': data.city, 'about': data.about,
            'parameters': data.parameters})
```

⚠ Ключ `parameters` присылается **всегда** (у поля есть умолчание `{}`), и пустой
словарь значит «параметров не правили»: `_card_apply` обходит присланные ключи, и
пустой словарь не меняет ничего.

- [ ] **Step 5: Новая ручка обновления**

В `src/uvicorn/routers/dating/profile_action.py` добавить импорты:

```python
from src.dating.panel_fetch import DatingPanelFetchError
from src.dating.profile_panel import (DatingProfilePanelError,
                                      profile_panel_read)
```

Добавить ручку в конец файла:

```python
@router.post('/{profile_id}/card/parameters', include_in_schema=False)
async def profile_action_parameters(profile_id: int,
                                    user=Depends(auth_required)):
    """Снять параметры анкеты с админ-панели агентства.

    Кнопка «Обновить с сайта» во второй колонке окна правки карточки.

    ⚠ **Наружу не пишет ничего.** Страница панели только читается; правленое
    руками при записи не трогается (`profile_card_merge`).

    ⚠ Своего круга у занятия нет намеренно: анкета меняется раз в месяцы.

    Raises:
        HTTPException: 404 — анкеты нет; 422 — у анкеты нет номера на сайте либо
            у сайта нет панели; 502 — панель не ответила, увела на вход или
            сменила разметку.
    """
    profile = await profile_row(profile_id)
    site = await profile_site(int(profile['site_id']))

    try:
        result = await profile_panel_read(
            profile, json_column_load(site.get('presence')))
    except DatingPanelFetchError as e:
        # Отказ самой панели: не ответила, увела на вход, ответила кодом ошибки.
        await log_write(profile_id, 'fail', f'Параметры не снялись: {e}')
        raise HTTPException(status_code=502, detail=str(e))
    except DatingProfilePanelError as e:
        # ⚠ Два исхода под одним исключением, и коды у них разные: «нет номера» и
        # «нет панели» — ошибка запроса (422), «разметка сменилась» — отказ чужой
        # стороны (502). Различаем по тексту, потому что это один отказ домена, а
        # заводить два исключения ради кода HTTP значило бы учить домен про HTTP.
        code = 502 if 'разметка' in str(e) else 422
        await log_write(profile_id, 'fail', f'Параметры не снялись: {e}')
        raise HTTPException(status_code=code, detail=str(e))

    said = sum(1 for one in (result.get('parameters') or {}).values() if one)
    await log_write(profile_id, 'ok',
                    f'Параметры сняты с панели: заполнено {said} из 21')

    return {'result': result}
```

- [ ] **Step 6: Прогнать тест**

Run: `pytest tests/test_profile_card_api.py -q`
Expected: PASS, 12 passed

- [ ] **Step 7: Прогнать группу**

Run: `pytest -m profile -q`
Expected: PASS

- [ ] **Step 8: Коммит**

```bash
git add src/uvicorn/routers/dating/profile_view.py \
        src/uvicorn/routers/dating/profile_.py \
        src/uvicorn/routers/dating/profile_action.py \
        tests/test_profile_card_api.py
git commit -m "Ручки карточки: параметры наружу, внутрь и с панели

Каркас и подписи отдаёт сервер — своя копия реестра на странице
разошлась бы молча. Чужой ключ в правке — 422, а не молчаливый отсев:
иначе устаревшая страница получала бы «сохранено» на несохранённое.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Вторая колонка окна

**Files:**
- Modify: `data/uvicorn/template/admin/dating/index.html:2276-2440`
  (функция `cardEditModal`)
- Modify: `data/uvicorn/public/css/dating_page.css` (после строки 920)

**Interfaces:**
- Consumes: `GET /card` отдаёт `parameters`, `parameters_at`, `parameter_fields`;
  `PUT /card` принимает `parameters`; `POST /card/parameters` (задача 9).
- Produces: окно в две колонки; никаких новых экспортов.

- [ ] **Step 1: Сетка в две колонки**

В `data/uvicorn/public/css/dating_page.css` добавить после строки 920
(`.dating-modal-box #cardAbout { … }`):

```css
/* Окно персональных данных — **шире соседних и в две колонки**: слева шесть полей
   учётки, справа 21 параметр с сайта. Ширина своим классом, а не правкой
   `.dating-modal-box`: её делят трое. Так же сделано у окна описания снимка. */
.card-edit { width: min(980px, 94vw); }
.card-edit-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 0 24px; align-items: start;
}
/* ⚠ На узком экране колонки встают друг под друга: 980 пикселей не помещаются в
   телефон, а окно правки открывают и с него. */
@media (max-width: 760px) {
    .card-edit { width: 94vw; }
    .card-edit-grid { grid-template-columns: 1fr; }
}
.card-edit-head {
    font-size: 12px; font-weight: 700; color: #0f172a; margin-top: 8px;
}
/* Отметка о чтении и кнопка обновления — подвалом правой колонки. */
.card-edit-when { font-size: 11px; color: #94a3b8; margin-top: 8px; }
.card-edit-side { display: flex; flex-direction: column; }
.card-edit-side textarea { height: 70px; resize: vertical; }
```

- [ ] **Step 2: Разделить разметку окна на две колонки**

В `data/uvicorn/template/admin/dating/index.html`, в функции `cardEditModal`,
заменить первую строку сборки разметки (строка 2297):

```javascript
        back.innerHTML = '<div class="dating-modal-box" onclick="event.stopPropagation()">'
            + '<div class="dating-modal-head">Персональные данные анкеты</div>'

            + '<label class="dating-modal-field">' + label('login', 'Почта')
```

на:

```javascript
        back.innerHTML = '<div class="dating-modal-box card-edit" onclick="event.stopPropagation()">'
            + '<div class="dating-modal-head">Персональные данные анкеты</div>'
            + '<div class="card-edit-grid"><div>'

            + '<label class="dating-modal-field">' + label('login', 'Почта')
```

Заменить строки перед подвалом (строка 2335, блок `about` и начало подвала):

```javascript
            + '<label class="dating-modal-field">' + label('about', 'Описание')
            + `<textarea id="cardAbout" maxlength="2000">${esc(String(p.about || ''))}</textarea>`
            + '<i class="dating-modal-hint">Его читает агент и письма — «О себе».</i></label>'

            + '<div class="dating-modal-foot">'
```

на:

```javascript
            + '<label class="dating-modal-field">' + label('about', 'Описание')
            + `<textarea id="cardAbout" maxlength="2000">${esc(String(p.about || ''))}</textarea>`
            + '<i class="dating-modal-hint">Его читает агент и письма — «О себе».</i></label>'

            + '</div><div class="card-edit-side">'
            + '<div class="card-edit-head">Параметры с сайта</div>'
            + '<i class="dating-modal-hint">Их читает агент в чате — всё, что он'
            + ' знает о девушке, кроме описания. ⚠ Правка здесь меняет <b>нашу</b>'
            + ' карточку: на сайт ничего не пишется.</i>'
            + paramRows()
            + '<button type="button" class="dating-btn" id="cardParamsGet">'
            + 'Обновить с сайта</button>'
            + '<div class="card-edit-when" id="cardParamsWhen">'
            + (data.parameters_at ? `снято ${esc(timeShift(data.parameters_at))}`
                                  : 'с сайта ещё не снимали')
            + '</div>'
            + '</div></div>'

            + '<div class="dating-modal-foot">'
```

- [ ] **Step 3: Нарисовать строки параметров**

В `cardEditModal`, сразу после объявления `label` (строка ~2295), добавить:

```javascript
        // Строки параметров — **по реестру с сервера**, а не по своему списку:
        // своя копия подписей и порядка разошлась бы с кодом молча. Та же грабля
        // уже закрыта у словаря тегов галереи.
        //
        // ⚠ Отметка «правлено руками» у каждой строки — тем же `label`, что у
        // шести полей слева: имя поля составное (`parameters.height`), и ссылка
        // «вернуть сайту» работает без единой правки.
        const params = data.parameters || {};
        const paramRows = () => (data.parameter_fields || []).map(one => {
            const key = String(one.key || '');
            const value = String(params[key] || '');
            const field = one.kind === 'area'
                ? `<textarea id="cardParam-${esc(key)}" data-param="${esc(key)}"`
                  + ` maxlength="2000">${esc(value)}</textarea>`
                : `<input type="text" id="cardParam-${esc(key)}"`
                  + ` data-param="${esc(key)}" maxlength="190" value="${esc(value)}">`;

            return '<label class="dating-modal-field">'
                + label('parameters.' + key, String(one.title || key))
                + field + '</label>';
        }).join('');
```

- [ ] **Step 4: Собрать параметры при сохранении**

В обработчике `cardEditKeep` (строка ~2405) дополнить объект `send`:

```javascript
            const send = {
                login: document.getElementById('cardLogin').value.trim(),
                profile_id: document.getElementById('cardUid').value.trim(),
                name: document.getElementById('cardName').value.trim(),
                age: Number(document.getElementById('cardAge').value || 0),
                city: document.getElementById('cardCity').value.trim(),
                about: document.getElementById('cardAbout').value,
                // ⚠ Собираем **из разметки**, а не из `data.parameters`: в поле
                // лежит то, что человек набрал, и второй источник правды здесь
                // означал бы молча потерянную правку.
                parameters: Object.fromEntries(
                    [...back.querySelectorAll('[data-param]')]
                        .map(el => [el.dataset.param, el.value.trim()])),
            };
```

- [ ] **Step 5: Повесить кнопку обновления**

После обработчика `cardEditOff` (строка ~2402) добавить:

```javascript
        // ⚠ Кнопка **только читает** панель. Набранное в полях она перезаписывает
        // тем, что отдал сайт, — и это ожидаемо: человек нажал «обновить с сайта».
        // Правленое руками сайт не трогает, об этом говорит отметка у строки.
        document.getElementById('cardParamsGet').addEventListener('click', async () => {
            const button = document.getElementById('cardParamsGet');
            const was = button.textContent;
            button.disabled = true;
            button.textContent = 'читаю панель…';
            why.textContent = '';

            try {
                const got = await postData(
                    `${BASE}/api/dating/profile/${currentId}/card/parameters`);
                const fresh = (got && got.result && got.result.parameters) || {};
                back.querySelectorAll('[data-param]').forEach(el => {
                    el.value = String(fresh[el.dataset.param] || '');
                });
                const at = (got && got.result && got.result.parameters_at) || '';
                document.getElementById('cardParamsWhen').textContent =
                    at ? `снято ${timeShift(at)}` : 'с сайта ещё не снимали';
            } catch (err) {
                why.textContent = 'с сайта не снялось: ' + err.message;
            } finally {
                button.disabled = false;
                button.textContent = was;
            }
        });
```

`postData(url, data = {})` уже импортирован в этом файле из `js/api.js` (строка 577)
и тела не требует — второй довод у него с умолчанием.

- [ ] **Step 6: Поднять панель и посмотреть глазами**

```bash
docker compose up -d
docker restart dating_uvicorn
```

⚠ Контейнер монтирует каталог проекта в `/app`: файлы внутри обновляются мгновенно,
а работающий код — нет. «Правка на месте, а поведение старое» — это оно.

Открыть «Анкеты» → выбрать девушку → иконка ✎ в шапке карточки.

Проверить глазами:
- окно в две колонки, слева шесть прежних полей, справа 21 строка;
- `Увлечения` и `О партнёре` — текстовые области, остальные однострочные;
- кнопка «Обновить с сайта» и строка «с сайта ещё не снимали»;
- сузить окно браузера до 700 px — колонки встают друг под друга.

- [ ] **Step 7: Прогнать проверку вёрстки**

Run: `pytest -m page -q`
Expected: PASS

⚠ Если в сюите есть сторож «классы шаблона и CSS не разъехались»
(`html_scan_pairs`), он должен остаться зелёным: новые классы `card-edit*`
объявлены и в разметке, и в стиле.

- [ ] **Step 8: Коммит**

```bash
git add data/uvicorn/template/admin/dating/index.html \
        data/uvicorn/public/css/dating_page.css
git commit -m "Окно анкеты: вторая колонка с параметрами и кнопкой обновления

Строки рисуются по реестру с сервера, а не по своему списку: копия
подписей на странице разошлась бы с кодом молча. Отметка «правлено
руками» работает у них тем же кодом — имя поля составное.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Документация

**Files:**
- Modify: `CLAUDE.md` (Development Patterns и Key Files)
- Modify: `.claude/docs/api.md` (§3.14)
- Modify: `.claude/docs/dating.md` (раздел «Что видит агент»)

- [ ] **Step 1: Строка в Development Patterns**

В `CLAUDE.md` добавить после строки про «Правка персональных данных анкеты руками»:

```markdown
- **Параметры анкеты девушки (рост, волосы, увлечения…)** → `src/dating/profile_param.py`
  (⚠ **реестр из 21 ключа** — один на троих читателей: окно берёт из него подписи
  **ручкой**, био берёт подписи английские, драйвер панели — ключи. Каркас отдаётся
  **целиком всегда**, неизвестное пустой строкой: «ключа нет» и «ключ пуст» иначе
  стали бы двумя состояниями на четыре места. ⚠⚠ Снимаются **кнопкой** со страницы
  админ-панели агентства (`profile_panel.py`), круга нет намеренно — анкета меняется
  раз в месяцы. ⚠⚠ Разбор идёт **по именам полей формы**, а не по подписям: имя
  переживёт перевёрстку. Карта имён и нормализация (`5'8" - 172 cm` → `172 cm`, три
  селекта → `1990-01-21`) — у драйвера сайта `site/site_panel.py`, за совпадением
  карты с реестром следит сторож `TestTheyAgree`. ⚠⚠⚠ Форма без единого знакомого
  имени — **отказ**, а не пустой результат: запись пустоты обнулила бы параметры у
  всех сорока анкет по одному нажатию. ⚠ Отметки правки руками **составные**
  (`parameters.height`), механизм тот же, что у пяти прежних полей)
```

- [ ] **Step 2: Строки в Key Files**

Добавить в таблицу `Key Files`:

```markdown
| `src/dating/profile_param.py` | Реестр параметров анкеты: 21 ключ, подписи окна и био, каркас, строки био (⚠ чистый, без базы; читателей трое — окно, драйвер панели и сборка био) |
| `src/dating/site/site_panel.py` | Драйвер админ-панели: путь страницы анкеты, карта имён формы, нормализация мер и даты (⚠ четвёртая таблица `site_registry`; `known` — предохранитель от сменившейся разметки) |
| `src/dating/profile_panel.py` | Снять параметры со страницы панели и записать (⚠ один GET по удерживаемой сессии; пустой разбор — отказ, а не пустой результат) |
```

Дописать в строку `src/dating/profile_card.py`:

```markdown
Там же `profile_card_merge` — дверь «сайт принёс карточку»: стояла двумя копиями слово в слово (страница кабинета и кэш анкеты), третьей её сделало бы чтение панели. ⚠ Импорт сюда не входит намеренно: у него решение (`import_plan`) и запись (роутер) разведены, и отметки он уважает своим путём. ⚠⚠ Слияние `parameters` **глубокое**: плоское стирало бы правленое руками у анкеты, где правили все поля
```

- [ ] **Step 3: Раздел в `api.md`**

В `.claude/docs/api.md`, §3.14, дописать подраздел:

```markdown
### 3.14.1 Параметры анкеты: вторая колонка окна

Окно правки делится на две колонки. Слева шесть полей учётки (почта, ID, имя,
возраст, город, описание), справа — 21 параметр с админ-панели агентства.

| Ручка | Что делает |
|---|---|
| `GET /api/dating/profile/{id}/card` | в `result` — `parameters` (каркас), `parameters_at` (когда снято, UTC) и `parameter_fields` (реестр: `key`, `title`, `kind`) |
| `PUT /api/dating/profile/{id}/card` | принимает `parameters: {ключ: значение}`; чужой ключ или перебор длины — `422` |
| `POST /api/dating/profile/{id}/card/parameters` | снять с панели; `422` — нет номера или панели, `502` — панель не ответила либо сменила разметку |
| `DELETE /api/dating/profile/{id}/card/parameters.{ключ}` | «вернуть сайту»: снять отметку правки руками |

⚠ **Ключи и подписи — с сервера.** Страница не держит своего списка: он разошёлся
бы с реестром молча, и человек правил бы поле, которого код не знает.

⚠⚠ **Форма без единого знакомого имени — отказ.** Смена разметки у сайта иначе
обнулила бы параметры у всех анкет сразу, по одному нажатию кнопки.

⚠ **Наружу не пишется ничего.** Кнопка только читает панель; правка в окне меняет
нашу карточку.
```

- [ ] **Step 4: Раздел в `dating.md`**

В `.claude/docs/dating.md`, раздел «Что видит агент», дописать:

```markdown
### Из чего состоит `girl_bio`

Два абзаца, разделённые пустой строкой:

1. **проза от первого лица** — имя, возраст, город и рассказ «о себе» из карточки
   (`agent_bio`, было всегда);
2. **справка по параметрам** — строки `Label: value` по непустым полям реестра
   (`profile_param_bio`), в порядке реестра.

⚠ Пустые поля не называются вовсе: «Hair: —» модель читает как факт и может об этом
заговорить. То же правило у био собеседника.

⚠⚠ Предел в 3000 знаков применяется **только ко второму абзацу**. Проза собрана в
одну строку, и обрезка по границе строки (`text_trim_lines`) унесла бы её целиком —
агент остался бы без имени.

Уезжает `girl_bio` двумя дорогами и обе не менялись: при заведении агента
(`dating_girl_create` добывает из неё факты) и на каждом ответе в чате
(`answer.py`). Правок у соседа нет.
```

- [ ] **Step 5: Полный прогон перед коммитом документации**

Run: `pytest -q`
Expected: PASS

⚠ Одиночное красное в полном прогоне у круговых тестов — не обязательно регрессия:
сперва проверить, не идёт ли параллельно чужой `pytest`.

- [ ] **Step 6: Коммит**

```bash
git add CLAUDE.md .claude/docs/api.md .claude/docs/dating.md
git commit -m "Документация: параметры анкеты и состав girl_bio

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Приложение А: `tests/frames/ladadate_lady_edit.html`

Форма со страницы `/partner/agency/userprofile/edit/{ID}` панели Ladadate, снятая
12.10.2026. Имена полей, классы, сущности и структура — настоящие; значения
обезличены, списки `option` укорочены до трёх с сохранением выбранного.

```html
<form action="/partner/agency/userprofile/edit" method="post">
  <input type="hidden" value="100001" name="AccountId" />
  <input class="form-control" id="Name" maxlength="50" name="Name" required="required" type="text" value="Olga" />
  <input class="form-control" id="City" maxlength="50" name="City" required="required" type="text" value="Lviv" />
  <select class="form-control" id="CountryId" name="CountryId" required="required">
    <option value="0"></option>
    <option selected="selected" value="208">Ukraine</option>
    <option value="1">Afghanistan</option>
  </select>
  <input class="form-control" id="Occupation" maxlength="100" name="Occupation" required="required" type="text" value="teacher" />
  <select class="form-control" id="EducationId" name="EducationId" required="required">
    <option value="0"></option>
    <option selected="selected" value="4">University</option>
    <option value="3">College</option>
  </select>
  <select class="form-control" id="EnglishId" name="EnglishId" required="required">
    <option value="0"></option>
    <option selected="selected" value="4">Good</option>
    <option value="3">Basic</option>
  </select>
  <select class="form-control" id="EyesColorId" name="EyesColorId" required="required">
    <option value="0"></option>
    <option selected="selected" value="6">Hazel</option>
    <option value="1">Black</option>
  </select>
  <select class="form-control" id="HairColorId" name="HairColorId" required="required">
    <option value="0"></option>
    <option selected="selected" value="6">Red</option>
    <option value="1">Auburn</option>
  </select>
  <select class="form-control" id="HairStyleId" name="HairStyleId" required="required">
    <option value="0"></option>
    <option selected="selected" value="2">Medium</option>
    <option value="3">Long</option>
  </select>
  <select class="form-control col" id="BirthdayDay" name="BirthdayDay" required="required">
    <option value="0"></option>
    <option selected="selected" value="21">21</option>
    <option value="1">1</option>
  </select>
  <select class="form-control col-3" id="BirthdayMonth" name="BirthdayMonth" required="required">
    <option value="0"></option>
    <option selected="selected" value="1">Jan</option>
    <option value="2">Feb</option>
  </select>
  <select class="form-control col-3" id="BirthdayYear" name="BirthdayYear" required="required">
    <option value="0"></option>
    <option selected="selected" value="1990">1990</option>
    <option value="1957">1957</option>
  </select>
  <select class="form-control" id="MaritalStateId" name="MaritalStateId" required="required">
    <option value="0"></option>
    <option selected="selected" value="7">Never been Married</option>
    <option value="5">Betrothed</option>
  </select>
  <select class="form-control" id="ReligionId" name="ReligionId" required="required">
    <option value="0"></option>
    <option selected="selected" value="3">Christian</option>
    <option value="1">Buddhist</option>
  </select>
  <select class="form-control" id="EthnicityId" name="EthnicityId" required="required">
    <option value="0"></option>
    <option selected="selected" value="9">White/European</option>
    <option value="1">Afr. American</option>
  </select>
  <select class="form-control" id="BodyTypeId" name="BodyTypeId" required="required">
    <option value="0"></option>
    <option selected="selected" value="5">Slim</option>
    <option value="1">Athletic</option>
  </select>
  <select class="form-control" id="DrinkId" name="DrinkId" required="required">
    <option value="0"></option>
    <option selected="selected" value="3">Rarely</option>
    <option value="1">No</option>
  </select>
  <select class="form-control" id="SmokeId" name="SmokeId" required="required">
    <option value="0"></option>
    <option selected="selected" value="1">No</option>
    <option value="5">Often</option>
  </select>
  <select class="form-control" id="HeightId" name="HeightId" required="required">
    <option value="0"></option>
    <option selected="selected" value="172">5&#39;8&quot; - 172 cm</option>
    <option value="120">3&#39;11&quot; - 120 cm</option>
  </select>
  <select class="form-control" id="WeightId" name="WeightId" required="required">
    <option value="0"></option>
    <option selected="selected" value="51">112 lb - 51 kg</option>
    <option value="40">88 lb - 40 kg</option>
  </select>
  <select class="form-control" id="ChildrenId" name="ChildrenId" required="required">
    <option value="0"></option>
    <option selected="selected" value="1">None</option>
    <option value="2">One</option>
  </select>
  <input class="form-control" id="AgeFrom" max="80" min="18" name="AgeFrom" required="required" type="number" value="35" />
  <input class="form-control" id="AgeTo" max="80" maxlength="2" min="18" name="AgeTo" required="required" type="number" value="65" />
  <textarea class="form-control" cols="20" id="AboutMyself" name="AboutMyself" required="required" rows="6">about myself text</textarea>
  <textarea class="form-control" cols="20" id="Interest" name="Interest" required="required" rows="6">swimming and dancing</textarea>
  <textarea class="form-control" cols="20" id="AboutPartner" name="AboutPartner" required="required" rows="6">kind and honest man</textarea>
</form>
```
