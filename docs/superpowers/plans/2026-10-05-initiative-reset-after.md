# Повторный заход инициативы: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Девушка возвращается к молчащему собеседнику по своему, суточному сроку, а упёршуюся в потолок пару человек отпускает щелчком по иконке в шапке разговора.

**Architecture:** Новая настройка сайта `chat_initiative_reset_after` (часы) задаёт срок тишины для **повторных** заходов; первый остаётся на минутном сроке с жребием, потолком заходов остаётся `chat_initiative_repeat`. Счёт заходов переезжает из запроса-на-строку в основной запрос отбора, и его выражение становится единственным на два места — отбор круга и строку диалога. Сброс руками — отметка `chat_initiative.reset_at` на последней ушедшей карточке пары, сдвигающая нижнюю границу счёта.

**Tech Stack:** Python 3.11, FastAPI, aiomysql, MySQL 8.0.46, Jinja2, ванильный JS, pytest (`pytest -m chat`).

**Спека:** `docs/superpowers/specs/2026-10-05-initiative-reset-after-design.md` — там доводы, здесь шаги.

## Global Constraints

- Ветка работы: `initiative-reset-after` (создана, спека уже в ней коммитом `23dcc80`).
- Прогон после каждой задачи: `pytest -m chat` (полминуты). Полная сюита — перед выкладкой.
- Живого HTTP в тестах **нет никогда** (`.claude/docs/testing.md`, §1.2): сторож `py_modules/http_/http_mock.py` стоит на транспорте httpx на весь прогон.
- Анкета для теста — только фикстуры `girl` / `girl_make` из `tests/conftest.py`; своих `INSERT`/`DELETE` по анкете не писать.
- Номер стендового сайта в тестах — `STAND_SITE = 999_343`, он уже объявлен в обоих файлах тестов.
- ⚠ Флаги `chat_initiative_on` / `chat_initiative_send_on` у стендового сайта в базе **не поднимать**: ту же базу читает живой воркер в соседнем контейнере, и он пошёл бы писать настоящим людям. Подменять только `initiative_round._sites` (готовый помощник `_awake(monkeypatch)` в `tests/test_chat_initiative.py`).
- Миграции: новый файл в `migrations/`, применённые не правятся (`.claude/docs/migration.md`). Имя — `ГГГГММДДччммсс_<о чём>.sql`.
- Умолчание новой настройки — `'0'`: выкладка не меняет поведения ни одному существующему сайту.
- Названия и подписи настроек — ровно те, что в спеке; подпись новой настройки: `Инициатива, сброс через часов`.
- Значения на LadaDate (`chat_initiative_reset_after = 24`, `chat_initiative_repeat = 4`) ставятся **руками на странице** и в код не попадают.

---

## Карта файлов

| Файл | За что отвечает | Что с ним |
|------|-----------------|-----------|
| `src/dating/site_setting.py` | реестр настроек сайта и чтение значений | добавить настройку, уточнить две подписи |
| `src/dating/initiative.py` | кого и чем звать: отбор затихших пар | чтение срока, выражение счёта, проверка срока повторного захода |
| `src/dating/chat_initiative.py` | работа с таблицей пула | новая `chat_initiative_reset` |
| `migrations/20261005*_chat_initiative_reset_at.sql` | столбец отметки сброса | создать |
| `src/uvicorn/routers/dating/chat.py` | ручки диалогов | ручка сброса, поле `initiative_blocked` в строке |
| `data/uvicorn/template/admin/dating/profile/site/_base.html` | разметка вкладок работы с анкетой | иконка в шапке блока `work_talk` |
| `data/uvicorn/template/admin/dating/profile.html` | страница «Профили»: вся её логика | отрисовка и щелчок по иконке |
| `data/uvicorn/public/css/dating_page.css` | оформление страниц раздела | состояние иконки |
| `tests/test_chat_initiative.py` | пул и его настройки | шестёрка настроек, ручка сброса, поле строки |
| `tests/test_initiative_round.py` | круг и отбор | срок повторного захода, окно отбора, сброс |
| `.claude/docs/api.md`, `CLAUDE.md` | документация | таблица настроек, два порога, иконка и ручка |

---

## Task 1: Настройка в реестре и её чтение

**Files:**
- Modify: `src/dating/site_setting.py:505-525` (между `chat_initiative_after_max` и `chat_initiative_repeat`)
- Modify: `src/dating/initiative.py:84-97` (константы), `src/dating/initiative.py:470-503` (читалки)
- Test: `tests/test_chat_initiative.py:289-293` (`INITIATIVE_SETTINGS`), `:297-317` (`test_all_five_are_registered`)

**Interfaces:**
- Consumes: `site_seconds(site_id, key)` из `src/dating/site_setting.py` — число настройки с проверкой границ.
- Produces: `INITIATIVE_RESET_AFTER_SETTING: str`, `INITIATIVE_RESET_AFTER_DEFAULT: int = 0`, `async _reset_after(site_id: int) -> int` — срок в **секундах** (настройка в часах). Их берут задачи 2 и 6.

- [ ] **Step 1: Write the failing test**

В `tests/test_chat_initiative.py` заменить список настроек и переименовать тест:

```python
# Настройки занятия: их читают вместе, и стоять им рядом — одним подразделом.
INITIATIVE_SETTINGS = ('chat_initiative_after', 'chat_initiative_after_max',
                       'chat_initiative_reset_after',
                       'chat_initiative_repeat', 'chat_initiative_max_day',
                       'chat_initiative_send_every')


class TestTheSettings:
    """Настройки инициативы — шестёрка, их читают вместе."""

    def test_all_six_are_registered(self):
        after = SITE_KNOWN['chat_initiative_after']
        upto = SITE_KNOWN['chat_initiative_after_max']
        again = SITE_KNOWN['chat_initiative_reset_after']
        times = SITE_KNOWN['chat_initiative_repeat']
        cap = SITE_KNOWN['chat_initiative_max_day']
        pause = SITE_KNOWN['chat_initiative_send_every']

        assert after['title'] == 'Инициатива, проявить впервые через минут (от)'
        assert upto['title'] == 'Инициатива, проявить впервые через минут (до)'
        assert again['title'] == 'Инициатива, сброс через часов'
        assert times['title'] == 'Инициатива, сколько раз подряд звать молчащего'
        assert cap['title'] == 'Инициатива, не больше этого в сутки на пару'
        assert pause['title'] == 'Инициатива, пауза между отправками, в мин'
        assert times['default'] == '2'
        assert times['kind'] == 'number' and times['min'] == 0
        assert upto['kind'] == 'number' and upto['min'] == 0, \
            'ноль обязан быть допустим — это «без разброса»'
        assert pause['kind'] == 'number' and pause['min'] == 0, \
            'ноль обязан быть допустим — это «без паузы»'
        assert again['kind'] == 'number' and again['min'] == 0, \
            'ноль обязан быть допустим — это «сброса нет»'
        assert all(SITE_KNOWN[key]['group'] == 'invite'
                   for key in INITIATIVE_SETTINGS), \
            'настройки читают вместе — и стоять им рядом'
```

И добавить два теста в тот же класс:

```python
    def test_the_reset_is_off_by_default(self):
        """⚠⚠ Умолчание — ноль, и это не осторожность, а совместимость.

        Настройка меняет **поведение** занятия, а не добавляет новое. Поставь
        умолчанием сутки, и выкладка молча включила бы повторные заходы на всех
        сайтах разом, в том числе там, где человек об этом не просил.
        """
        from src.dating.initiative import INITIATIVE_RESET_AFTER_DEFAULT

        assert SITE_KNOWN['chat_initiative_reset_after']['default'] == '0'
        assert INITIATIVE_RESET_AFTER_DEFAULT == 0, \
            'умолчание кода разошлось с умолчанием реестра'

    def test_the_note_says_what_zero_means(self):
        """⚠ Ноль здесь значит «сброса нет», а не «занятие выключено».

        Без этой строки человек, увидев ноль, читает его как выключатель и
        начинает искать, почему инициатива «выключена», хотя она работает.
        """
        note = SITE_KNOWN['chat_initiative_reset_after']['note']

        assert 'сброса нет' in note
        assert 'Выключателем занятия она не является' in note
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chat_initiative.py::TestTheSettings -v`
Expected: FAIL — `KeyError: 'chat_initiative_reset_after'` в трёх тестах, и `AssertionError` на подписи `chat_initiative_after` (она ещё без слова «впервые»).

- [ ] **Step 3: Добавить настройку в реестр**

В `src/dating/site_setting.py` после блока `'chat_initiative_after_max'` (он кончается строкой `'min': 0,` и `},` на 524–525) вставить:

```python
    # ⚠⚠ **Срок повторного захода, и он отвечает не на тот вопрос, что минутный.**
    # Минутный говорит, через сколько тишины девушка заговаривает **впервые**;
    # этот — через сколько она возвращается снова, если он так и не ответил.
    # Одним сроком на оба случая занятие и встало: при пределе «один подряд» пара
    # получала одну реплику и замирала навсегда, потому что счёт обнуляет только
    # его ответ, а на 72 ушедшие инициативы не ответил никто.
    #
    # ⚠ Второго счётчика рядом нет намеренно: потолком заходов остаётся
    # `chat_initiative_repeat`, и второе число отвечало бы на тот же вопрос.
    'chat_initiative_reset_after': {
        'group': 'invite',
        'sub': 'initiative',
        'title': 'Инициатива, сброс через часов',
        'note': 'через сколько часов тишины девушка возвращается к разговору '
                '**повторно**. Первый заход меряется минутным сроком выше, этот — '
                'все следующие. ⚠ Всего заходов не больше, чем задано в «сколько '
                'раз подряд звать молчащего»: срок отвечает «когда», потолок — '
                '«сколько». ⚠⚠ Его ответ счёт обнуляет, и тогда всё начинается с '
                'первого захода, по минутному сроку. ⚠ Ноль — **сброса нет**: '
                'повторные заходы идут по минутному сроку, как до появления этой '
                'настройки. Выключателем занятия она не является — за него '
                'отвечает галочка «набор инициативы». ⚠ Упёршуюся в потолок пару '
                'можно отпустить руками: «Чат → Диалоги», иконка в шапке разговора',
        'default': '0',
        # ⚠ Число, а не набор: часы человек читает как есть, и соседние сроки
        # занятия — тоже числа. Границы при этом проверяет и чтение
        # (`site_seconds`): форма не единственный вход.
        'kind': 'number',
        'min': 0,
    },
```

- [ ] **Step 4: Уточнить две подписи минутного срока**

В том же файле заменить две строки `'title'`:

```python
        'title': 'Инициатива, проявить впервые через минут (от)',
```

```python
        'title': 'Инициатива, проявить впервые через минут (до)',
```

⚠ Слово «впервые» обязательно: после правки этот срок за промежуток между повторами не отвечает, и подпись без него обещает то, чего не делает.

- [ ] **Step 5: Константы и читалка в домене**

В `src/dating/initiative.py` после блока `INITIATIVE_MAX_DAY_*` (строка 97) добавить:

```python
# Срок тишины для **повторного** захода, в часах. Ноль — «сброса нет»: повторы
# меряются минутным сроком, как до появления настройки.
#
# ⚠ Умолчание ноль, а не сутки: настройка меняет поведение занятия, и выкладка не
# должна включать повторные заходы там, где о них не просили.
INITIATIVE_RESET_AFTER_SETTING = 'chat_initiative_reset_after'
INITIATIVE_RESET_AFTER_DEFAULT = 0
```

И в конец файла, рядом с `_repeat` и `_max_day`:

```python
async def _reset_after(site_id: int) -> int:
    """Срок тишины повторного захода, в **секундах**. Настройка — в часах.

    ⚠ Ноль — законное «сброса нет»: повторные заходы меряются минутным сроком,
    ровно как до появления настройки. Это не выключатель занятия — за него
    отвечает `chat_initiative_on` или ноль в `chat_initiative_repeat`.

    ⚠ Не прочиталось — умолчание, а не ноль наугад: сторона ошибки выбрана так же,
    как у `_max_day`, в пользу прежнего поведения.
    """
    try:
        hours = int(await site_seconds(int(site_id or 0),
                                       INITIATIVE_RESET_AFTER_SETTING))
    except Exception as e:
        logger_info(f'[dating] срок повторного захода не прочитался: {e}')

        hours = INITIATIVE_RESET_AFTER_DEFAULT

    return max(0, hours) * 3600
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_chat_initiative.py::TestTheSettings -v`
Expected: PASS, восемь тестов класса.

- [ ] **Step 7: Добавить тест читалки и прогнать группу**

В `tests/test_initiative_round.py` добавить класс в конец файла:

```python
class TestTheResetTermIsRead:
    """Срок повторного захода: часы настройки → секунды кода."""

    async def test_zero_by_default(self):
        """⚠ У стендового сайта настройка не стоит — значит «сброса нет»."""
        assert await initiative_mod._reset_after(STAND_SITE) == 0

    async def test_hours_become_seconds(self, auth_client):
        """⚠ Настройка в часах, код считает в секундах: 24 → 86400."""
        before = (await auth_client.get(
            f'/api/dating/site/{STAND_SITE}')).json()['result']['settings']
        try:
            await auth_client.put(
                f'/api/dating/site/{STAND_SITE}',
                json={'settings': {**before,
                                   'chat_initiative_reset_after': '24'}})

            assert await initiative_mod._reset_after(STAND_SITE) == 24 * 3600, \
                'код не видит срок, поставленный на странице'
        finally:
            await auth_client.put(f'/api/dating/site/{STAND_SITE}',
                                  json={'settings': before})
```

Run: `pytest -m chat -q`
Expected: PASS — всё зелёное, падений нет.

- [ ] **Step 8: Commit**

```bash
git add src/dating/site_setting.py src/dating/initiative.py \
        tests/test_chat_initiative.py tests/test_initiative_round.py
git commit -m "Инициатива: настройка срока повторного захода заведена в реестре

Пока ни на что не влияет — отбор её не спрашивает (следующая задача). Умолчание
ноль: выкладка не меняет поведения ни одному существующему сайту.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Счёт заходов переезжает в запрос отбора

Структурная правка **без изменения поведения**: сторожем служат десять готовых тестов `TestWhoIsQuiet` и три `TestTheDailyBackstop`. Делается раньше самой работы, чтобы не писать один и тот же код дважды.

**Files:**
- Modify: `src/dating/initiative.py:228-378` (`_quiet` целиком)
- Test: `tests/test_initiative_round.py` (новый класс в конец)

**Interfaces:**
- Consumes: ничего нового.
- Produces: `initiative_his_last_sql(profile: str, uid: str) -> str` и `initiative_in_row_sql(profile: str, uid: str, his_last: str) -> str` — выражения SQL, собираемые из **имён столбцов вызывающего**, без подстановок значений. Их берут задачи 4 и 6. Сигнатура `_quiet` получает шестой довод `reset_sec: int = 0` (задача 3 начнёт его передавать).

- [ ] **Step 1: Write the failing test**

В `tests/test_initiative_round.py` добавить класс:

```python
class TestTheSelectionWindow:
    """⚠⚠ Окно отбора: `LIMIT` применяется **после** отбора, а не до него."""

    async def test_an_old_pair_is_found_behind_a_crowd_of_unfit_ones(self, girl):
        """⚠⚠ Негодные пары не должны съедать окно.

        `_quiet` берёт пары порядком «свежая тишина сверху» и ограничивает их
        числом. Пока счёт заходов стоил запроса на строку, окно наполнялось
        **до** отбора — и годная пара, молчащая дольше всех, в него не попадала,
        если свежих было больше окна. Со суточным сроком повторного захода это
        ломало бы занятие молча: годные при нём — как раз самые старые пары.

        Негодных здесь делаем неотвеченными инвайтами (ни одной входящей): они
        проходят срок, но отбор их не берёт — ровно тот случай, что съедал окно.
        """
        for n in range(30):
            await _said(girl, f'inv-{n}', direction='out', minutes_ago=10)

        await _said(girl, 'u-old', direction='in', minutes_ago=4000)
        await _said(girl, 'u-old', direction='out', minutes_ago=3900)

        got = await initiative_mod._quiet(girl, 300, 300, 2)

        assert [one['uid'] for one in got] == ['u-old'], \
            'годная пара потерялась за толпой негодных — окно наполняется до отбора'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_initiative_round.py::TestTheSelectionWindow -v`
Expected: FAIL — `assert [] == ['u-old']`: тридцать неотвеченных инвайтов заняли окно в двадцать строк.

- [ ] **Step 3: Вынести два выражения SQL**

В `src/dating/initiative.py` перед `_quiet` добавить:

```python
def initiative_his_last_sql(profile: str, uid: str) -> str:
    """Его последнее слово — нижняя граница счёта «подряд», выражением SQL.

    Args:
        profile: как вызывающий называет столбец анкеты (`d.profile_id`).
        uid: как вызывающий называет столбец собеседника (`d.uid`).

    ⚠ Собирается из **имён столбцов**, а не из значений: подстановок внутри нет,
    и порядок доводов запроса от этой функции не зависит. Значения сюда не
    попадают вовсе — склеивать SQL с ними было бы дырой.
    """
    return ('(SELECT MAX(h.created_at) FROM message h'
            f'  WHERE h.profile_id = {profile} AND h.sender_uid = {uid}'
            "    AND h.direction = 'in')")


def initiative_in_row_sql(profile: str, uid: str, his_last: str) -> str:
    """Счёт заходов **подряд** одним выражением SQL.

    Сколько наших инициатив ушло паре после его последней реплики — и после
    ручного сброса, если он был (`chat_initiative.reset_at`, задача 4).

    Args:
        profile: имя столбца анкеты у вызывающего.
        uid: имя столбца собеседника у вызывающего.
        his_last: выражение «его последнее слово» (обычно
            `initiative_his_last_sql(...)` либо уже посчитанный столбец).

    ⚠⚠ **Правило одно на два места.** Зовут его отбор круга (`_quiet`) и строка
    диалога (`chat_dialogs` → `initiative_blocked`). Своя копия в ручке
    разошлась бы с кругом на первой правке, и иконка горела бы не тогда, когда
    круг молчит, — ровно та грабля, что уже была с `initiative_why`.
    """
    return ('(SELECT COALESCE(SUM(i.sent_at > ' + his_last + '), 0)'
            '   FROM chat_initiative i'
            f'  WHERE i.profile_id = {profile} AND i.uid = {uid}'
            "    AND i.status = 'sent')")
```

- [ ] **Step 4: Переписать `_quiet` одним запросом**

Заменить тело `_quiet` (от `async with mysql_get_db_async() as db:` до `return got`) на:

```python
    async with mysql_get_db_async() as db:
        # ⚠⚠ **Отбор целиком в запросе, и это не украшение.** Прежде счёт заходов
        # стоил отдельного запроса на строку, и потому брался **после** окна:
        # двадцать свежайших затихших пар наполнялись до отбора, а негодные
        # (неотвеченные инвайты, ждущие ответа реплики) съедали окно целиком. С
        # суточным сроком повторного захода это ломало бы занятие молча — годные
        # при нём как раз самые старые пары, то есть последние в этом порядке.
        #
        # ⚠ «Последняя реплика» считается по времени, а не по `MAX(id)`: импорт из
        # панели кладёт недельную историю сегодняшними номерами. Та же мерка, что
        # у автоответа и у списка диалогов — три места, и во всех правило одно.
        await db.execute(
            'SELECT q.uid, q.name, q.quiet_sec, q.last_at, cnt.in_row'
            '  FROM (SELECT t.profile_id, t.sender_uid AS uid, t.last_at,'
            '               (SELECT c.name FROM companion c'
            '                 WHERE c.site_id = p.site_id'
            '                   AND c.uid = t.sender_uid) AS name,'
            '               ' + initiative_his_last_sql('t.profile_id',
                                                        't.sender_uid')
            + ' AS his_last,'
            # ⚠ Сторона последней реплики и решение молчать берутся одним
            # порядком: иначе можно поймать метку от позапрошлой.
            '               (SELECT m2.direction FROM message m2'
            '                 WHERE m2.profile_id = t.profile_id'
            '                   AND m2.sender_uid = t.sender_uid'
            '                 ORDER BY m2.created_at DESC, m2.id DESC'
            '                 LIMIT 1) AS last_side,'
            '               (SELECT m3.answer_silent_at FROM message m3'
            '                 WHERE m3.profile_id = t.profile_id'
            '                   AND m3.sender_uid = t.sender_uid'
            '                 ORDER BY m3.created_at DESC, m3.id DESC'
            '                 LIMIT 1) AS last_silent,'
            # Сколько пара молчит — им и проверяется жребий срока. Считает база,
            # а не мы: её `NOW()` и отобрал строку ниже, и мерить одно и то же
            # двумя часами значило бы ловить расхождение в секундах.
            '               TIMESTAMPDIFF(SECOND, t.last_at, NOW()) AS quiet_sec'
            '          FROM (SELECT m.profile_id, m.sender_uid,'
            '                       MAX(m.created_at) AS last_at'
            '                  FROM message m WHERE m.profile_id = %s'
            '                 GROUP BY m.profile_id, m.sender_uid) t'
            '          JOIN profile p ON p.id = t.profile_id'
            '         WHERE t.last_at < NOW() - INTERVAL %s SECOND) q'
            # ⚠ `LATERAL`: счёту нужен `q.his_last`, посчитанный строкой выше.
            # MySQL 8.0.14+; на проде и локально 8.0.46.
            '  JOIN LATERAL (SELECT '
            + initiative_in_row_sql('q.profile_id', 'q.uid', 'q.his_last')
            + ' AS in_row,'
            # ⚠ Сутки скользящие, мерка та же, что у `invite_repeat_max_day`:
            # полночь ничего не значит для того, кто читает сообщения.
            '                       (SELECT COALESCE(SUM(j.sent_at >'
            '                                 NOW() - INTERVAL 1 DAY), 0)'
            '                          FROM chat_initiative j'
            '                         WHERE j.profile_id = q.profile_id'
            '                           AND j.uid = q.uid'
            "                           AND j.status = 'sent') AS in_day"
            '               ) cnt ON TRUE'
            # ⚠ Последним говорил он — берём, **только если решено молчать**.
            # Пока решения нет, реплику ждёт автоответ, и это его работа.
            "  WHERE (q.last_side = 'out' OR q.last_silent IS NOT NULL)"
            # ⚠ Он не сказал ни слова — это неотвеченный инвайт, не наша работа.
            '    AND q.his_last IS NOT NULL'
            # ⚠ «Подряд» — первым: он отсекает несравнимо чаще дневного.
            '    AND cnt.in_row < %s'
            # ⚠⚠ Предохранитель. Сторожит не живого человека, а патологию:
            # зацикленный автоответчик обнуляет счёт «подряд» каждой репликой, и
            # без потолка пара получила бы сорок инициатив за сутки.
            '    AND cnt.in_day < %s'
            # ⚠ Порядок «свежая тишина сверху»: разговор, затихший час назад,
            # ещё живой, и шансов на ответ у него больше. Окно теперь считает
            # **годные** строки, а не все подряд.
            '  ORDER BY q.last_at DESC LIMIT %s',
            (int(profile_id), int(after_sec), int(repeat_max), int(max_day),
             INITIATIVE_PER_PROFILE * 10))
        rows = await db.fetchall() or []

        got = []
        for row in rows:
            # Срок этой пары — жребием между границами. Нижнюю уже проверил SQL;
            # всё, что между границами, решается здесь.
            #
            # ⚠ Жребий остаётся в Python: он выводится из пары (`blake2b` от
            # `profile_id:uid:last_at`), и повторить его в SQL значило бы
            # завести второе правило о том же.
            due = _due_sec(profile_id, str(row['uid']), row.get('last_at'),
                           int(after_sec), int(after_max_sec))
            if int(row.get('quiet_sec') or 0) < due:
                continue

            # ⚠ Номер захода — по счёту **подряд**, а не дневному: «заход 2»
            # значит «второй раз зову в эту тишину», и ровно это читает человек в
            # журнале анкеты.
            got.append({'uid': str(row['uid']), 'name': row.get('name') or '',
                        'try_no': int(row.get('in_row') or 0) + 1})

            if len(got) >= INITIATIVE_PER_PROFILE:
                break

    return got
```

И в объявление функции добавить шестой довод (пока не используется — его начнёт передавать задача 3):

```python
async def _quiet(profile_id: int, after_sec: int, after_max_sec: int,
                 repeat_max: int,
                 max_day: int = INITIATIVE_MAX_DAY_DEFAULT,
                 reset_sec: int = 0) -> list[dict]:
```

⚠ Довод шестым, **после** `max_day`: тринадцать готовых тестов зовут `_quiet` четырьмя позиционными доводами, и вставка в середину сломала бы их молча — на числах, а не на имени.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_initiative_round.py::TestWhoIsQuiet tests/test_initiative_round.py::TestTheDailyBackstop tests/test_initiative_round.py::TestTheSelectionWindow tests/test_initiative_round.py::TestTheTermIsDrawnPerPair -v`
Expected: PASS — четырнадцать прежних тестов зелёные (поведение не изменилось) плюс новый.

- [ ] **Step 6: Прогнать группу целиком**

Run: `pytest -m chat -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/dating/initiative.py tests/test_initiative_round.py
git commit -m "Отбор инициативы: счёт заходов переехал в запрос

Поведение то же — сторожем стоят четырнадцать готовых тестов. Смысл правки в
окне: LIMIT применяется после отбора, а не до него, и негодные пары больше его
не съедают. Заодно ушёл запрос-на-строку, а выражение счёта стало одним на два
места — его возьмёт строка диалога.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Повторный заход ждёт свой срок

**Files:**
- Modify: `src/dating/initiative.py` (`_quiet` — одно условие; `initiative_make` — передать срок; докстринги)
- Modify: `tests/test_initiative_round.py:244-258` (докстринг готового теста)
- Test: `tests/test_initiative_round.py` (новый класс)

**Interfaces:**
- Consumes: `_reset_after(site_id) -> int` (задача 1), `_quiet(..., reset_sec=…)` (задача 2).
- Produces: поведение — повторный заход уходит не раньше `reset_sec` тишины.

- [ ] **Step 1: Write the failing test**

В `tests/test_initiative_round.py` добавить класс:

```python
class TestTheRepeatCallWaitsItsOwnTerm:
    """⚠⚠ Первый заход меряется минутами, повторные — своим сроком в часах."""

    async def test_the_first_call_ignores_the_reset_term(self, girl):
        """⚠ Первому заходу новый срок не указ: его срок — минутный.

        Иначе настройка превратилась бы в «через сколько вообще заговаривать», и
        минутный срок перестал бы значить что-либо.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=200)
        await _said(girl, 'u-1', direction='out', minutes_ago=180)

        got = await initiative_mod._quiet(girl, 3600, 3600, 2,
                                          reset_sec=24 * 3600)

        assert [one['uid'] for one in got] == ['u-1'], \
            'первый заход стал ждать суточный срок'

    async def test_the_second_call_waits_the_hours(self, girl):
        """⚠⚠ Заход уже был, он молчит — ждём суточный срок, а не минутный.

        Тишина здесь три часа: минутный срок (час) вышел, суточный — нет.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _said(girl, 'u-1', direction='out', minutes_ago=180)
        await _card_sent(girl, 'u-1', minutes_ago=180)

        assert await initiative_mod._quiet(girl, 3600, 3600, 2,
                                           reset_sec=24 * 3600) == [], \
            'повторный заход ушёл по минутному сроку'

    async def test_the_second_call_goes_when_the_hours_run_out(self, girl):
        """⚠ Суточный срок вышел — заход уходит, и это заход номер два."""
        await _said(girl, 'u-1', direction='in', minutes_ago=4000)
        await _said(girl, 'u-1', direction='out', minutes_ago=3000)
        await _card_sent(girl, 'u-1', minutes_ago=3000)

        got = await initiative_mod._quiet(girl, 3600, 3600, 2,
                                          reset_sec=24 * 3600)

        assert [one['uid'] for one in got] == ['u-1']
        assert [one['try_no'] for one in got] == [2], \
            'номер захода не вырос — счёт «подряд» не считает прошлый заход'

    async def test_the_ceiling_still_holds(self, girl):
        """⚠⚠ Срок говорит «когда», потолок — «сколько»; второго счётчика нет.

        Два захода при пределе два: суточный срок вышел давно, и всё равно не
        зовём. Иначе сброс стал бы способом обойти потолок.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=9000)
        await _said(girl, 'u-1', direction='out', minutes_ago=8000)
        await _card_sent(girl, 'u-1', minutes_ago=8000)
        await _card_sent(girl, 'u-1', minutes_ago=4000)

        assert await initiative_mod._quiet(girl, 3600, 3600, 2,
                                           reset_sec=24 * 3600) == [], \
            'третий заход при пределе два — потолок перестал держать'

    async def test_zero_keeps_todays_behaviour(self, girl):
        """⚠ Ноль — «сброса нет»: повторы идут по минутному сроку, как прежде."""
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _said(girl, 'u-1', direction='out', minutes_ago=180)
        await _card_sent(girl, 'u-1', minutes_ago=180)

        got = await initiative_mod._quiet(girl, 3600, 3600, 2, reset_sec=0)

        assert [one['uid'] for one in got] == ['u-1'], \
            'ноль в сроке изменил прежнее поведение'

    async def test_his_answer_returns_the_pair_to_the_minute_term(self, girl):
        """⚠⚠ Ответил — счёт обнулён, и следующий заход снова **первый**.

        То есть меряется минутами, а не сутками: разговор жив, и ждать сутки,
        чтобы ответить на затихшую беседу, значило бы его потерять.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _card_sent(girl, 'u-1', minutes_ago=350)
        await _said(girl, 'u-1', direction='in', minutes_ago=300)
        await _said(girl, 'u-1', direction='out', minutes_ago=180)

        got = await initiative_mod._quiet(girl, 3600, 3600, 2,
                                          reset_sec=24 * 3600)

        assert [one['uid'] for one in got] == ['u-1'], \
            'его ответ не вернул пару на минутный срок'
        assert [one['try_no'] for one in got] == [1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_initiative_round.py::TestTheRepeatCallWaitsItsOwnTerm -v`
Expected: FAIL — `test_the_second_call_waits_the_hours` и `test_the_second_call_goes_when_the_hours_run_out` падают (`reset_sec` пока ни на что не влияет: первый вернёт пару, второй даст `try_no` правильный, но срок не проверяется). Остальные четыре проходят и служат сторожами.

- [ ] **Step 3: Проверить срок в отборе**

В `src/dating/initiative.py`, в `_quiet`, добавить условие в `WHERE` **после** `cnt.in_day < %s`:

```python
            # ⚠⚠ Повторный заход ждёт **свой** срок, в часах. Первому он не указ:
            # его срок минутный, с жребием. Ноль в настройке — «сброса нет», и
            # условие тогда не участвует вовсе.
            '    AND (%s = 0 OR cnt.in_row = 0 OR q.quiet_sec >= %s)'
```

и соответственно дописать два довода в кортеж, **перед** `INITIATIVE_PER_PROFILE * 10`:

```python
            (int(profile_id), int(after_sec), int(repeat_max), int(max_day),
             int(reset_sec), int(reset_sec),
             INITIATIVE_PER_PROFILE * 10))
```

- [ ] **Step 4: Передать срок из `initiative_make`**

В `src/dating/initiative.py:126-147` добавить чтение и передачу:

```python
    after, after_max = await _after_range(site_id)
    repeat = await _repeat(site_id)
    max_day = await _max_day(site_id)
    reset_sec = await _reset_after(site_id)
```

```python
        for pair in await _quiet(int(profile['id']), after, after_max, repeat,
                                 max_day, reset_sec):
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_initiative_round.py::TestTheRepeatCallWaitsItsOwnTerm -v`
Expected: PASS, шесть тестов.

- [ ] **Step 6: Поправить докстринг готового теста, который теперь врёт**

В `tests/test_initiative_round.py`, тест `test_an_old_silence_is_still_one_silence` (около строки 244), его докстринг кончается словами «девушка звала бы молчащего каждый день по разу вечно» — а именно это теперь и делается, но **по явной настройке**. Дописать в конец докстринга:

```python
        ⚠⚠ Это по-прежнему верно для счёта самого по себе: окна у него нет, и
        сутки его не обнуляют. «Звать молчащего каждый день по разу» теперь
        возможно, но только когда человек явно задал срок повторного захода
        (`chat_initiative_reset_after`), и не больше, чем разрешает потолок
        `chat_initiative_repeat`. Без настройки — ровно как здесь: не зовём.
```

- [ ] **Step 7: Поправить докстринги домена**

В `src/dating/initiative.py` в заголовочном докстринге модуля таблицу настроек привести к шестёрке:

```
| `chat_initiative_after` | через сколько минут тишины пишем **впервые** — от |
| `chat_initiative_after_max` | тот же срок — **до**; между ними жребий |
| `chat_initiative_reset_after` | через сколько **часов** возвращаемся снова |
| `chat_initiative_repeat` | сколько раз **подряд** возвращаемся, пока он молчит |
| `chat_initiative_max_day` | предохранитель: сколько инициатив паре за сутки |
```

И в докстринг `_quiet` дописать раздел:

```
    ## ⚠⚠ Сроков два, и они о разных заходах

    **Первый заход** меряется минутным сроком с жребием: разговор затих только
    что, и вернуться к нему стоит быстро.

    **Повторный** ждёт `chat_initiative_reset_after` (часы). Одним сроком на оба
    случая занятие и встало: при пределе «один подряд» пара получала одну реплику
    и замирала навсегда — счёт обнуляет только его ответ, а на 72 ушедшие
    инициативы не ответил никто.

    ⚠ Ноль в сроке — «сброса нет»: повторы меряются минутным сроком, как прежде.

    ⚠ Потолком остаётся `chat_initiative_repeat`: срок отвечает «когда», потолок
    — «сколько». Второго счётчика нет намеренно.
```

- [ ] **Step 8: Прогнать группу**

Run: `pytest -m chat -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/dating/initiative.py tests/test_initiative_round.py
git commit -m "Инициатива: повторный заход ждёт свой срок в часах

Первый заход остаётся на минутном сроке с жребием, повторные меряются
chat_initiative_reset_after. Потолком остаётся chat_initiative_repeat: срок
отвечает «когда», потолок — «сколько».

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Отметка ручного сброса

**Files:**
- Create: `migrations/20261005210000_chat_initiative_reset_at.sql`
- Modify: `src/dating/chat_initiative.py` (новая функция в конец)
- Modify: `src/dating/initiative.py` (`initiative_in_row_sql` — нижняя граница)
- Test: `tests/test_initiative_round.py` (новый класс)

**Interfaces:**
- Consumes: `initiative_in_row_sql(profile, uid, his_last)` (задача 2).
- Produces: `async chat_initiative_reset(profile_id: int, uid: str) -> bool` — `True`, если отметка легла; `False`, если у пары нет ушедших карточек. Берёт задача 5.

- [ ] **Step 1: Написать миграцию**

Создать `migrations/20261005210000_chat_initiative_reset_at.sql`:

```sql
-- Ручной сброс счёта заходов инициативы: столбец `reset_at` (2026-10-05)
--
-- ЗАЧЕМ. Счёт заходов «подряд» обнуляет только входящая реплика собеседника. Это
-- верно по смыслу — молчание тоже ответ, — но значит, что пара, упёршаяся в
-- потолок, замирает навсегда, если человек не напишет сам. На проде так и вышло:
-- 72 ушедшие инициативы 33 парам, ни одного ответа, пул стоит пустым.
--
-- Теперь у человека есть щелчок: иконка в шапке разговора на «Чат → Диалоги»
-- отпускает пару, и счёт начинается заново. Отметка ложится на **последнюю
-- ушедшую карточку пары** и значит «счёт заходов считать с этого момента».
--
-- ⚠ СВОЕЙ ТАБЛИЦЫ НЕТ. Сброс — свойство пары, но живёт отметкой на карточке:
-- журнал ушедшего остаётся цел, а сам сброс виден в истории пары. Тем же приёмом
-- обошлись без своей таблицы сбои (`error`, `source` вида `crash:…`).
--
-- ⚠⚠ ПОЭТОМУ `NULL DEFAULT NULL`, А НЕ `NOT NULL DEFAULT CURRENT_TIMESTAMP`.
-- `ALTER` со вторым проставил бы всем накопленным строкам время самой миграции —
-- то есть выкладка молча обнулила бы счёт заходов у всех пар разом, и девушки
-- пошли бы звать по второму кругу тех, кого уже звали. Та же грабля, что у
-- `favorite_mail.updated_at` и у соседнего `chat_initiative.updated_at`.
--
-- ⚠ ОТМЕТКА НЕ ВЫКЛЮЧАЕТ СЧЁТ, А СДВИГАЕТ ЕГО НИЖНЮЮ ГРАНИЦУ: карточки, ушедшие
-- после неё, счёт видит. Иначе сброс стал бы способом отключить потолок навсегда.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
           AND COLUMN_NAME = 'reset_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `status` = 'sent')
         AS ушедших;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
               AND COLUMN_NAME = 'reset_at');

SET @sql = IF(@add,
    "ALTER TABLE `chat_initiative`
        ADD COLUMN `reset_at` DATETIME NULL DEFAULT NULL
            COMMENT 'Счёт заходов обнулён руками после этой карточки; NULL — не обнуляли'
            AFTER `sent_at`",
    'SELECT ''столбец уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
           AND COLUMN_NAME = 'reset_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `reset_at` IS NOT NULL)
         AS сброшенных;
```

- [ ] **Step 2: Применить миграцию локально и проверить**

Run: `PYTHONPATH=py_modules python -m mysql_.migration.mysql_migration_pending`
Expected: в списке к применению — `20261005210000_chat_initiative_reset_at.sql`.

Применить тем же способом, каким проект применяет миграции (`.claude/docs/migration.md`), затем проверить:

Run:
```bash
PYTHONPATH=py_modules python -c "
from mysql_.mysql_query import mysql_query_run
print(mysql_query_run(
    'SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_DEFAULT'
    '  FROM information_schema.COLUMNS'
    \" WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'\"
    \"   AND COLUMN_NAME = 'reset_at'\").get('rows'))"
```
Expected: `[{'COLUMN_NAME': 'reset_at', 'IS_NULLABLE': 'YES', 'COLUMN_DEFAULT': None}]`

- [ ] **Step 3: Write the failing test**

В `tests/test_initiative_round.py` добавить класс:

```python
class TestTheManualReset:
    """⚠⚠ Сброс руками: пара, упёршаяся в потолок, отпускается щелчком."""

    async def test_a_capped_pair_is_released(self, girl):
        """Заход был, он молчит, потолок один — сброс возвращает пару в отбор."""
        from src.dating.chat_initiative import chat_initiative_reset

        await _said(girl, 'u-1', direction='in', minutes_ago=4000)
        await _said(girl, 'u-1', direction='out', minutes_ago=3900)
        await _card_sent(girl, 'u-1', minutes_ago=3800)

        assert await initiative_mod._quiet(girl, 3600, 3600, 1) == [], \
            'пара не упёрлась в потолок — тест проверяет не то, что думает'

        assert await chat_initiative_reset(girl, 'u-1') is True

        got = await initiative_mod._quiet(girl, 3600, 3600, 1)

        assert [one['uid'] for one in got] == ['u-1'], \
            'сброс не вернул пару в отбор'
        assert [one['try_no'] for one in got] == [1], \
            'номер захода не начался заново'

    async def test_a_pair_without_sent_cards_answers_false(self, girl):
        """⚠ Обнулять нечего — это не ошибка, а ответ «нечего»."""
        from src.dating.chat_initiative import chat_initiative_reset

        await _said(girl, 'u-1', direction='in', minutes_ago=400)

        assert await chat_initiative_reset(girl, 'u-1') is False

    async def test_cards_after_the_reset_are_counted_again(self, girl):
        """⚠⚠ Сброс сдвигает границу счёта, а не выключает его.

        Иначе один щелчок снимал бы потолок с пары навсегда, и девушка звала бы
        молчащего без конца.
        """
        from src.dating.chat_initiative import chat_initiative_reset

        await _said(girl, 'u-1', direction='in', minutes_ago=4000)
        await _said(girl, 'u-1', direction='out', minutes_ago=3900)
        await _card_sent(girl, 'u-1', minutes_ago=3800)
        await chat_initiative_reset(girl, 'u-1')

        # После сброса ушла ещё одна карточка — счёт обязан её увидеть.
        await _card_sent(girl, 'u-1', minutes_ago=10)

        assert await initiative_mod._quiet(girl, 3600, 3600, 1) == [], \
            'карточка после сброса не посчитана — потолок снят навсегда'

    async def test_the_daily_backstop_survives_the_reset(self, girl):
        """⚠⚠ Сброс трогает только счёт «подряд», дневной потолок остаётся.

        Тот сторожит не живого человека, а зацикленный автоответчик на той
        стороне; снимать его щелчком значило бы остаться без предохранителя.
        """
        from src.dating.chat_initiative import chat_initiative_reset

        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _said(girl, 'u-1', direction='out', minutes_ago=300)
        for minutes in (200, 150):
            await _card_sent(girl, 'u-1', minutes_ago=minutes)

        await chat_initiative_reset(girl, 'u-1')

        assert await initiative_mod._quiet(girl, 3600, 3600, 5,
                                           max_day=2) == [], \
            'сброс снял дневной потолок'
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_initiative_round.py::TestTheManualReset -v`
Expected: FAIL — `ImportError: cannot import name 'chat_initiative_reset'`.

- [ ] **Step 5: Написать `chat_initiative_reset`**

В конец `src/dating/chat_initiative.py`:

```python
async def chat_initiative_reset(profile_id: int, uid: str) -> bool:
    """Обнуляет счёт заходов пары: отметка на её последней ушедшей карточке.

    Args:
        profile_id: наша анкета.
        uid: собеседник на сайте.

    Returns:
        bool: `True` — отметка легла; `False` — у пары нет ушедших карточек, и
        обнулять нечего. ⚠ Второе не ошибка: кнопку могли нажать на разговоре,
        куда инициатива ещё не уходила.

    ## ⚠⚠ Зачем отметка, а не правка статуса

    Счёт «подряд» считает ушедшие карточки пары после его последней реплики
    (`initiative.initiative_in_row_sql`). Обнулить его, не трогая журнал,
    можно только сдвинув нижнюю границу счёта — это и делает `reset_at`.

    Статус у ушедших карточек при этом не меняется: они часть переписки, и
    переписать историю ради счётчика нельзя.

    ⚠ Отметка **не выключает счёт**: карточки, ушедшие после неё, он видит.
    Иначе щелчок снимал бы потолок с пары навсегда.

    ⚠ Дневного потолка (`chat_initiative_max_day`) отметка не касается: тот
    сторожит зацикленный автоответчик на той стороне, а не живого человека.
    """
    if not (int(profile_id or 0) and str(uid or '').strip()):
        return False

    async with mysql_get_db_async() as db:
        # ⚠ Отметка на **последней** ушедшей карточке, одним запросом: выбери мы
        # её отдельным, между выбором и записью успел бы встать круг отправки.
        await db.execute(
            "UPDATE chat_initiative SET reset_at = NOW()"
            " WHERE profile_id = %s AND uid = %s AND status = 'sent'"
            ' ORDER BY sent_at DESC, id DESC LIMIT 1',
            (int(profile_id), str(uid)))
        done = int(db.rowcount or 0)
        await db.connection.commit()

    return bool(done)
```

- [ ] **Step 6: Учесть отметку в счёте**

В `src/dating/initiative.py` заменить `initiative_in_row_sql` на:

```python
def initiative_in_row_sql(profile: str, uid: str, his_last: str) -> str:
    """Счёт заходов **подряд** одним выражением SQL.

    Сколько наших инициатив ушло паре после его последней реплики — либо после
    ручного сброса, если он был позже (`chat_initiative.reset_at`).

    Args:
        profile: имя столбца анкеты у вызывающего.
        uid: имя столбца собеседника у вызывающего.
        his_last: выражение «его последнее слово».

    ⚠⚠ **Правило одно на два места.** Зовут его отбор круга (`_quiet`) и строка
    диалога (`chat_dialogs` → `initiative_blocked`). Своя копия в ручке
    разошлась бы с кругом на первой правке, и иконка горела бы не тогда, когда
    круг молчит, — ровно та грабля, что уже была с `initiative_why`.

    ⚠ Собирается из **имён столбцов**, а не из значений: подстановок внутри нет,
    и порядок доводов запроса от этой функции не зависит.

    ⚠ `COALESCE` с «началом времён» обязателен: `GREATEST` с одним `NULL` отдаёт
    `NULL`, и у пары без сброса счёт вышел бы пустым — то есть нулём, то есть
    снятым потолком у всех, кого ни разу не отпускали руками.
    """
    return ('(SELECT COALESCE(SUM(i.sent_at > GREATEST(' + his_last + ','
            '      COALESCE((SELECT MAX(r.reset_at) FROM chat_initiative r'
            f'                WHERE r.profile_id = {profile}'
            f'                  AND r.uid = {uid}),'
            "               '1000-01-01 00:00:00'))), 0)"
            '   FROM chat_initiative i'
            f'  WHERE i.profile_id = {profile} AND i.uid = {uid}'
            "    AND i.status = 'sent')")
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_initiative_round.py::TestTheManualReset tests/test_initiative_round.py::TestWhoIsQuiet -v`
Expected: PASS — четыре новых и десять прежних.

- [ ] **Step 8: Прогнать группу**

Run: `pytest -m chat -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add migrations/20261005210000_chat_initiative_reset_at.sql \
        src/dating/chat_initiative.py src/dating/initiative.py \
        tests/test_initiative_round.py
git commit -m "Инициатива: счёт заходов обнуляется руками

Отметка reset_at на последней ушедшей карточке пары сдвигает нижнюю границу
счёта. Журнал ушедшего цел, дневной потолок не тронут, карточки после отметки
счёт видит.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Ручка сброса

**Files:**
- Modify: `src/uvicorn/routers/dating/chat.py:155-186` (рядом с `chat_dialog_read`)
- Test: `tests/test_chat_initiative.py` (новый класс)

**Interfaces:**
- Consumes: `chat_initiative_reset(profile_id, uid) -> bool` (задача 4).
- Produces: `POST /api/dating/chat/dialogs/{profile_id}/{uid}/initiative-reset` → `{"result": {"reset": true|false}}`. Зовёт её страница (задача 7).

- [ ] **Step 1: Write the failing test**

В `tests/test_chat_initiative.py` добавить класс:

```python
class TestTheResetHandle:
    """Ручка сброса: ею страница отпускает упёршуюся пару."""

    async def test_it_resets(self, auth_client, girl):
        await _card(girl, 'u-1', status='sent')

        got = (await auth_client.post(
            f'/api/dating/chat/dialogs/{girl}/u-1/initiative-reset')).json()

        assert got['result'] == {'reset': True}

        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM chat_initiative'
                ' WHERE profile_id = %s AND uid = %s AND reset_at IS NOT NULL',
                (girl, 'u-1'))

            assert int(((await db.fetchone()) or {})['n']) == 1, \
                'отметка не легла'

    async def test_nothing_to_reset_is_not_an_error(self, auth_client, girl):
        """⚠ Инициатива этой паре не уходила — ответ «нечего», а не 404.

        Кнопку видно у любого разговора, и нажатие на том, куда мы не писали, —
        обычное дело, а не поломка.
        """
        got = await auth_client.post(
            f'/api/dating/chat/dialogs/{girl}/u-none/initiative-reset')

        assert got.status_code == 200
        assert got.json()['result'] == {'reset': False}

    async def test_a_stranger_is_turned_away(self, client, girl):
        """⚠ Ручка за входом: сброс меняет работу круга, а не вид страницы."""
        got = await client.post(
            f'/api/dating/chat/dialogs/{girl}/u-1/initiative-reset')

        assert got.status_code in (401, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chat_initiative.py::TestTheResetHandle -v`
Expected: FAIL — 404 или 405 на неизвестный путь.

- [ ] **Step 3: Написать ручку**

В `src/uvicorn/routers/dating/chat.py` после `chat_dialog_read` (строка 186) добавить:

```python
@router.post('/dialogs/{profile_id}/{uid}/initiative-reset',
             include_in_schema=False)
async def chat_dialog_initiative_reset(profile_id: int, uid: str,
                                       user=Depends(auth_required)):
    """Обнуляет счёт заходов инициативы у пары: её зовут снова с первого.

    ## ⚠⚠ Зачем кнопка, если счёт обнуляет ответ

    Счёт «подряд» обнуляет **входящая реплика**, и это верно по смыслу: молчание
    тоже ответ. Но пара, упёршаяся в потолок, замирает тогда навсегда — если
    человек не напишет сам, девушка к нему не вернётся никогда. На проде так и
    вышло: 72 ушедшие инициативы 33 парам, ни одного ответа, пул пуст.

    Кнопка — решение человека, а не кода: он прочитал разговор и решил, что стоит
    попробовать снова.

    ⚠ **По паре, а не по анкете**: отпускают один разговор, который видят.

    ⚠ `{reset: false}` — законный ответ: инициатива этой паре не уходила, и
    обнулять нечего. Ошибкой это делать нельзя — кнопка видна у любого разговора.

    ⚠ Дневного потолка (`chat_initiative_max_day`) кнопка **не** снимает: тот
    сторожит зацикленный автоответчик на той стороне. На деле это видно так:
    нажал, а реплика ушла не сразу, если паре сегодня уже писали сколько можно.
    """
    return {'result': {'reset': await chat_initiative_reset(int(profile_id),
                                                            str(uid))}}
```

Импортов из `chat_initiative` в этом файле пока нет ни одного — добавить строкой, в алфавитном порядке среди прочих `from src.dating…`:

```python
from src.dating.chat_initiative import chat_initiative_reset
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_chat_initiative.py::TestTheResetHandle -v`
Expected: PASS, три теста.

- [ ] **Step 5: Прогнать группу и записать ручку в документацию API**

Run: `pytest -m chat -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/uvicorn/routers/dating/chat.py tests/test_chat_initiative.py
git commit -m "Инициатива: ручка сброса счёта заходов у пары

POST /api/dating/chat/dialogs/{profile_id}/{uid}/initiative-reset. Пара без
ушедших карточек отвечает reset: false — кнопка видна у любого разговора.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Поле `initiative_blocked` в строке диалога

**Files:**
- Modify: `src/dating/initiative.py` (новая функция `initiative_blocked`)
- Modify: `src/uvicorn/routers/dating/chat.py:388` (рядом с `_unread_last_in`, за блоком соединения)
- Test: `tests/test_chat_initiative.py` (новый класс)

**Interfaces:**
- Consumes: `initiative_his_last_sql`, `initiative_in_row_sql` (задачи 2 и 4), `_repeat(site_id)` (есть).
- Produces: `async initiative_blocked(site_id: int, pairs: list[tuple[int, str]]) -> set[tuple[int, str]]` — пары, чей счёт заходов упёрся в потолок. Поле `initiative_blocked: bool` в каждой строке `/chat/dialogs`.

- [ ] **Step 1: Write the failing test**

В `tests/test_chat_initiative.py` добавить класс:

```python
class TestTheCappedMarkInTheRow:
    """⚠⚠ Строка диалога говорит, упёрлась ли пара в потолок заходов."""

    async def test_a_free_pair_is_not_marked(self, auth_client, girl):
        await _said_in(girl, 'u-1')

        rows = (await auth_client.get(
            f'/api/dating/chat/dialogs?site_id={STAND_SITE}'
            f'&profile_id={girl}')).json()['result']['contacts']

        assert [one['initiative_blocked'] for one in rows] == [False]

    async def test_a_capped_pair_is_marked(self, auth_client, girl):
        """⚠ Потолок у стендового сайта умолчанием два — значит две карточки."""
        await _said_in(girl, 'u-1')
        await _card(girl, 'u-1', status='sent')
        await _card(girl, 'u-1', status='sent')

        rows = (await auth_client.get(
            f'/api/dating/chat/dialogs?site_id={STAND_SITE}'
            f'&profile_id={girl}')).json()['result']['contacts']

        assert [one['initiative_blocked'] for one in rows] == [True]

    async def test_the_rule_is_the_one_the_round_uses(self):
        """⚠⚠ Правило одно, и проверяется это чтением кода, а не повторением.

        Своя копия счёта в ручке разошлась бы с кругом на первой правке, и иконка
        горела бы не тогда, когда круг молчит. Ровно так уже устарела подсказка
        `initiative_why`, пережившая появление самого круга на две недели.
        """
        import inspect

        from src.dating import initiative

        assert 'initiative_in_row_sql' in inspect.getsource(
            initiative.initiative_blocked), \
            'ручка считает заходы своим выражением, а не общим'
        assert 'initiative_in_row_sql' in inspect.getsource(initiative._quiet), \
            'отбор круга перестал звать общее выражение'
```

Там же, рядом с готовым помощником `_card`, добавить:

```python
async def _said_in(profile_id: int, uid: str) -> None:
    """Входящая реплика пары: без неё диалога нет вовсе (`chat_dialogs`)."""
    import uuid

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction)'
            " VALUES (%s, %s, 'Brian', 'привет', %s, 'in')",
            (int(profile_id), uid, f'zz-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chat_initiative.py::TestTheCappedMarkInTheRow -v`
Expected: FAIL — `KeyError: 'initiative_blocked'` в двух тестах и `AttributeError: module 'src.dating.initiative' has no attribute 'initiative_blocked'` в третьем.

- [ ] **Step 3: Написать `initiative_blocked`**

В `src/dating/initiative.py` после `initiative_in_row_sql` добавить:

```python
async def initiative_blocked(site_id: int,
                             pairs: list[tuple[int, str]]) -> set:
    """Пары, чей счёт заходов упёрся в потолок сайта.

    Args:
        site_id: сайт-закладка — из него берётся потолок `chat_initiative_repeat`.
        pairs: пары `(номер анкеты, собеседник)`, о которых спрашивают.

    Returns:
        set: подмножество `pairs`, которым инициатива больше не уйдёт, пока он не
        ответит или человек не сбросит счёт руками.

    ## ⚠⚠ Это про потолок, а не про «почему пул пуст»

    Отбор круга спрашивает больше: срок тишины, дневной потолок, сторону
    последней реплики. Здесь ответ на один вопрос — тот, который задаёт иконка в
    шапке разговора: «к этой паре мы больше не вернёмся сами?»

    Смешивать их нельзя: иконка, краснеющая от «сейчас не вышел срок», горела бы
    почти всегда и ничего не значила.

    ⚠ **Одним запросом на всю страницу**, а не по строке: диалогов на странице
    до пятидесяти, и запрос на каждую стоил бы пятидесяти походов в базу ради
    одного значка.

    ⚠ Счёт берётся общим выражением (`initiative_in_row_sql`) — тем же, которым
    отбирает круг.
    """
    pairs = [(int(one), str(two)) for one, two in (pairs or []) if int(one or 0)]
    if not pairs:
        return set()

    repeat = await _repeat(int(site_id or 0))
    # ⚠ Ноль в потолке — занятие выключено, и «упёрлась» бессмысленно: иконка
    # горела бы у всех пар сразу, объясняя не потолок, а снятое занятие.
    if repeat <= 0:
        return set()

    # ⚠ Спрошенные пары приезжают подзапросом `UNION ALL`, а не конструктором
    # `VALUES`: тот есть только с MySQL 8.0.19, и выигрыша от него здесь нет.
    asked = ' UNION ALL '.join(['SELECT %s AS profile_id, %s AS uid']
                               + ['SELECT %s, %s'] * (len(pairs) - 1))
    args = [value for pair in pairs for value in pair]

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT d.profile_id, d.uid FROM (' + asked + ') d WHERE '
            + initiative_in_row_sql(
                'd.profile_id', 'd.uid',
                initiative_his_last_sql('d.profile_id', 'd.uid'))
            + ' >= %s',
            (*args, int(repeat)))
        rows = await db.fetchall() or []

    return {(int(row['profile_id']), str(row['uid'])) for row in rows}
```

- [ ] **Step 4: Run test to verify the helper works**

Run: `pytest tests/test_chat_initiative.py::TestTheCappedMarkInTheRow::test_the_rule_is_the_one_the_round_uses -v`
Expected: PASS.

- [ ] **Step 5: Добавить поле в строки `chat_dialogs`**

В `src/uvicorn/routers/dating/chat.py`, в `chat_dialogs`, **после** блока `async with mysql_get_db_async() as db:` — там, где уже стоит вызов `_unread_last_in`, берущий своё соединение (строка 388):

```python
    # ⚠ Оба числа значка — **до** ответа, одним ходом: ответ уже собран, и
    # запрос после него дал бы список и значок с разных моментов времени.
    unread, last_in_at = await _unread_last_in(int(site_id), int(profile_id))

    # ⚠ Упёрлась ли пара в потолок заходов инициативы — **одним запросом на всю
    # страницу** и тем же правилом, которым отбирает круг
    # (`initiative.initiative_blocked`). Своя копия счёта здесь разошлась бы с
    # кругом на первой правке, и иконка горела бы не тогда, когда круг молчит.
    #
    # ⚠ Спрашивается **за** блоком соединения, рядом с `_unread_last_in`, а не
    # внутри: функция берёт из пула своё соединение, и вложенный вызов держал бы
    # два разом на каждый показ страницы.
    capped = await initiative_blocked(
        int(site_id), [(int(one['profile_id']), str(one['uid']))
                       for one in contacts])
    for contact in contacts:
        contact['initiative_blocked'] = (
            int(contact['profile_id']), str(contact['uid'])) in capped
```

Импорт в начале файла:

```python
from src.dating.initiative import initiative_blocked
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_chat_initiative.py::TestTheCappedMarkInTheRow -v`
Expected: PASS, три теста.

- [ ] **Step 7: Прогнать группу**

Run: `pytest -m chat -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/dating/initiative.py src/uvicorn/routers/dating/chat.py \
        tests/test_chat_initiative.py
git commit -m "Диалоги: строка говорит, упёрлась ли пара в потолок заходов

Считает это то же выражение, которым отбирает круг, и одним запросом на всю
страницу. Поле initiative_blocked читает иконка в шапке разговора.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Иконка в шапке разговора

**Files:**
- Modify: `data/uvicorn/template/admin/dating/profile/site/_base.html:262-268` (колонка «Разговор» в блоке `work_talk`)
- Modify: `data/uvicorn/template/admin/dating/profile.html:12500-12514` (`chatTalkPaint`), `:4890-4892` (привязка щелчка рядом с `initiative-reload`)
- Modify: `data/uvicorn/public/css/dating_page.css` (в конец)
- Test: `tests/test_chat_initiative.py` (новый класс)

**Interfaces:**
- Consumes: поле `initiative_blocked` строки `/chat/dialogs` (задача 6), ручка `…/initiative-reset` (задача 5).
- Produces: разметка `data-slot="initiative-free"`.

- [ ] **Step 1: Write the failing test**

В `tests/test_chat_initiative.py` добавить класс:

```python
class TestTheReleaseIcon:
    """⚠ Иконка сброса — в шапке разговора на «Чат → Диалоги», и только там."""

    def _page(self, name: str) -> str:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]

        return (root / name).read_text(encoding='utf-8')

    def test_the_markup_has_the_icon(self):
        page = self._page(
            'data/uvicorn/template/admin/dating/profile/site/_base.html')

        assert page.count('data-slot="initiative-free"') == 1, \
            'иконка либо не заведена, либо уехала не в одну шапку'

    def test_it_stands_in_the_chat_tab_only(self):
        """⚠⚠ Шапок «Разговор» в шаблоне три, и две чужие.

        769 — «Фавориты → Диалоги», 1203 — «Письма»: у них своё занятие, и
        инициатива к ним отношения не имеет. Копия иконки там предлагала бы
        сбросить то, чего нет.
        """
        page = self._page(
            'data/uvicorn/template/admin/dating/profile/site/_base.html')
        before = page.split('data-slot="initiative-free"')[0]

        assert before.count('{% block work_talk %}') == 1, \
            'иконка стоит до блока «Чат → Диалоги»'
        assert '{% block work_favorite_talks %}' not in before, \
            'иконка уехала в шапку «Фаворитов»'
        assert '{% block work_mail_sent %}' not in before, \
            'иконка уехала в шапку «Писем»'

    def test_the_page_reads_the_server_field(self):
        page = self._page('data/uvicorn/template/admin/dating/profile.html')

        assert 'initiative_blocked' in page, \
            'страница не читает признак потолка с сервера'
        assert 'initiative-reset' in page, \
            'щелчок по иконке не зовёт ручку сброса'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_chat_initiative.py::TestTheReleaseIcon -v`
Expected: FAIL — `assert 0 == 1` в первом тесте, остальные два тоже красные.

- [ ] **Step 3: Разметка**

В `data/uvicorn/template/admin/dating/profile/site/_base.html` заменить колонку «Разговор» внутри блока `work_talk` (строки 262–268) на:

```html
                    <div class="in-col in-col-pool">
                        <div class="in-head">
                            <span>Разговор</span>
                            <span class="in-count" data-slot="chat-talk-who"></span>
                            {# ⚠⚠ Инициатива упёрлась в потолок — значок красный, и
                               щелчок по нему **отпускает пару**: счёт заходов
                               начинается заново.

                               Зачем кнопка: счёт обнуляет входящая реплика, то
                               есть пара, которой мы позвали столько раз, сколько
                               разрешено, замирает навсегда, если человек не
                               напишет сам. Это решение человека, не кода.

                               ⚠ Серый значок — обычное состояние, а не ошибка:
                               «к паре мы ещё вернёмся сами». Прятать его нельзя,
                               иначе появление красного читалось бы как новая
                               кнопка, а не как смена состояния. #}
                            <button type="button" class="talk-free"
                                    data-slot="initiative-free" hidden
                                    title="Инициатива: сбросить счёт заходов">↻</button>
                        </div>
                        <div class="in-list" data-slot="chat-talk-thread"></div>
                    </div>
```

- [ ] **Step 4: Оформление**

В конец `data/uvicorn/public/css/dating_page.css`:

```css
/* Значок сброса счёта заходов инициативы в шапке разговора.
   ⚠ Два состояния, и серое — обычное: «к паре мы ещё вернёмся сами». */
.talk-free {
    margin-left: auto;
    border: 0;
    background: none;
    cursor: pointer;
    font-size: 15px;
    line-height: 1;
    padding: 2px 4px;
    color: var(--muted, #8a8a8a);
}

/* Упёрлась в потолок: сами больше не вернёмся, пока он не ответит. */
.talk-free.capped {
    color: var(--bad, #c0392b);
}

.talk-free:hover { filter: brightness(1.2); }
```

- [ ] **Step 5: Отрисовка состояния**

В `data/uvicorn/template/admin/dating/profile.html`, в конец функции `chatTalkPaint()` (после `fillSlot('chat-talk-who', …)`), добавить:

```javascript
        // ⚠ Значок сброса: красный — инициатива упёрлась в потолок заходов, и
        // сама девушка к паре больше не вернётся. Признак считает сервер тем же
        // правилом, которым отбирает круг (`initiative_blocked`).
        const free = slot('initiative-free');
        if (free) {
            free.hidden = !found.uid;
            free.classList.toggle('capped', !!found.initiative_blocked);
            free.title = found.initiative_blocked
                ? 'Инициатива упёрлась в потолок заходов — сбросить счёт'
                : 'Инициатива: счёт заходов не исчерпан';
        }
```

- [ ] **Step 6: Щелчок**

В `data/uvicorn/template/admin/dating/profile.html` рядом с привязкой `initiative-reload` (строка 4890) добавить:

```javascript
        // Отпустить пару: счёт заходов инициативы начинается заново.
        //
        // ⚠ Перечитываем **список диалогов**, а не только значок: признак потолка
        // приходит в строке, и оставь мы список прежним, значок погас бы, а строка
        // продолжала бы носить старое значение — следующий щелчок по ней вернул бы
        // красный.
        const initiativeFree = workEl.querySelector('[data-slot="initiative-free"]');
        if (initiativeFree) initiativeFree.addEventListener('click', async () => {
            const [profileId, uid] = String(chatTalkOpenKey || '').split('|');
            if (!uid) return;

            initiativeFree.disabled = true;
            try {
                await postData(`${BASE}/api/dating/chat/dialogs/`
                    + `${profileId}/${encodeURIComponent(uid)}/initiative-reset`);
                await chatLoad(true);
                chatTalkPaint();
            } catch (e) {
                showNotification('Счёт заходов не сбросился: ' + e.message,
                                 'error');
            } finally {
                initiativeFree.disabled = false;
            }
        });
```

⚠ Имена не выдуманы: `chatLoad(refresh = false)` объявлена на строке 10875 и перечитывает диалоги, `showNotification(текст, 'error')` — общий показ сообщения страницы (её же зовут отправка ответа на письмо и соседние кнопки). Своих помощников для этого не заводить.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_chat_initiative.py::TestTheReleaseIcon -v`
Expected: PASS, три теста.

- [ ] **Step 8: Проверить глазами в живом приложении**

Поднять приложение и открыть «Профили» → сайт → «Чат» → «Диалоги», выбрать разговор. Значок виден серым; у пары с исчерпанным потолком — красный; щелчок делает его серым и не перезагружает страницу.

Если приложение под рукой не поднято — зовётся skill `run`.

- [ ] **Step 9: Прогнать группу**

Run: `pytest -m chat -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add data/uvicorn/template/admin/dating/profile/site/_base.html \
        data/uvicorn/template/admin/dating/profile.html \
        data/uvicorn/public/css/dating_page.css \
        tests/test_chat_initiative.py
git commit -m "Диалоги: значок сброса счёта заходов в шапке разговора

Красный — инициатива упёрлась в потолок, щелчок отпускает пару. Серое состояние
обычное и не прячется: иначе красный читался бы как новая кнопка.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Документация

**Files:**
- Modify: `.claude/docs/api.md:3765-3820` (раздел инициативы)
- Modify: `CLAUDE.md` (строка «Инициатива: через сколько и сколько раз»)
- Test: прогон сюиты целиком

- [ ] **Step 1: Таблица настроек в `api.md`**

Заменить абзац «Настроек у занятия пять…» и таблицу под ним на:

```markdown
Настроек у занятия шесть, все подразделом «Инициатива» на вкладке «Инвайты и
чат» (§3.5):

| Ключ | Подпись | Умолчание |
|------|---------|-----------|
| `chat_initiative_after` | Инициатива, проявить впервые через минут (от) | 60 |
| `chat_initiative_after_max` | Инициатива, проявить впервые через минут (до) | 120 |
| `chat_initiative_reset_after` | Инициатива, сброс через часов | 0 |
| `chat_initiative_repeat` | Инициатива, сколько раз подряд звать молчащего | 2 |
| `chat_initiative_max_day` | Инициатива, не больше этого в сутки на пару | 5 |
| `chat_initiative_send_every` | Инициатива, пауза между отправками, в мин | 3 |

### ⚠⚠ Сроков два, и они о разных заходах

| Заход | Через сколько тишины |
|-------|----------------------|
| первый | `chat_initiative_after`…`after_max`, минуты, жребием |
| второй и далее | `chat_initiative_reset_after`, часы |
| сверх `chat_initiative_repeat` | не зовём, пока он не ответит |

Одним сроком на оба случая занятие и встало. При `chat_initiative_repeat = 1`
пара получала одну реплику и замирала навсегда: счёт обнуляет только его ответ, а
на 72 ушедшие инициативы 33 парам не ответил никто — пул на LadaDate стоял пустым
с обеда 05.10.

⚠ Ноль в `chat_initiative_reset_after` — «сброса нет»: повторы меряются минутным
сроком, как до появления настройки. Выключателем занятия она не является.

⚠ **Второго счётчика рядом нет намеренно.** Потолком заходов остаётся
`chat_initiative_repeat`: срок отвечает «когда», потолок — «сколько». Пара
настроек «срок сброса плюс потолок сбросов» дала бы три взаимодействующих предела
и скрытое поведение «пачками» — при `repeat = 2` две реплики через минуты, потом
сутки, потом ещё две.

⚠ Жребия у суточного срока нет: разброс даёт сама отправка — карточка лежит в
пуле, пока адресата нет в сети, а поверх стоит пауза `chat_initiative_send_every`
на весь сайт.

### Отпустить пару руками

`POST /api/dating/chat/dialogs/{profile_id}/{uid}/initiative-reset` →
`{reset: true|false}`.

Счёт заходов обнуляется отметкой `chat_initiative.reset_at` на последней ушедшей
карточке пары. Нажимается иконкой в шапке колонки «Разговор» на «Чат → Диалоги»:
красная — инициатива упёрлась в потолок, серая — ещё вернёмся сами. Признак
приходит полем `initiative_blocked` в строке `/chat/dialogs`, и считает его то же
выражение, которым отбирает круг (`initiative.initiative_in_row_sql`).

⚠ `{reset: false}` — законный ответ: инициатива этой паре не уходила.

⚠⚠ Дневного потолка кнопка **не** снимает: тот сторожит не живого человека, а
зацикленный автоответчик на той стороне.
```

- [ ] **Step 2: Строка в `CLAUDE.md`**

В строке «**Инициатива: через сколько и сколько раз**» дописать после слов про интервал «от…до»:

```
⚠⚠ Сроков **два, и они о разных заходах**: минутный `chat_initiative_after`/`after_max` — когда зовём **впервые**, `chat_initiative_reset_after` (часы) — когда возвращаемся **снова**. Одним сроком на оба случая занятие встало наглухо: при потолке «один подряд» пара получала одну реплику и замирала навсегда, потому что счёт обнуляет только его ответ, — 72 инициативы 33 парам, ни одного ответа. ⚠ Ноль в сроке возврата — «сброса нет», прежнее поведение. ⚠ Второго счётчика нет: потолком остаётся `chat_initiative_repeat`. ⚠⚠ Упёршуюся пару человек отпускает **иконкой в шапке разговора** на «Чат → Диалоги» (`chat_initiative.reset_at`); признак потолка считает одно выражение `initiative_in_row_sql` на два места — отбор круга и строку диалога
```

- [ ] **Step 3: Полный прогон перед выкладкой**

Run: `pytest -q`
Expected: PASS целиком.

⚠ Одиночное красное в полном прогоне у круговых тестов регрессией не является (память проекта: `suite-round-flakes`) — перепрогнать упавший файл отдельно, прежде чем искать причину. И не править файлы во время прогона.

- [ ] **Step 4: Commit**

```bash
git add .claude/docs/api.md CLAUDE.md
git commit -m "Документация: два срока инициативы и сброс руками

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## После работы: что сделать на проде руками

Не шаг плана, а памятка — код этих значений не ставит.

1. Выложить ветку обычным порядком (`.claude/docs/deploy.md`); миграция применяется выкладкой.
2. «Настройки сайтов» → LadaDate → «Инвайты и чат» → «Инициатива»:
   `chat_initiative_reset_after` = `24`, `chat_initiative_repeat` = `4`.
3. Через сутки проверить журнал анкеты: должны появиться «заход 2», затем 3 и 4, и ни одного «заход 5».

⚠ Сразу после выкладки пул не наполнится: 33 упёршиеся пары ждут суточного срока
от своей последней реплики. Быстрее — отпустить несколько пар иконкой.
