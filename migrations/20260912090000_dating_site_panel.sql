-- Админ-панель сайта: своя сессия у сайта, а не у анкеты (2026-09-12)
--
-- ЗАЧЕМ. У каждого сайта знакомств есть панель агентства — та, где видны все наши
-- анкеты, переписка и письма разом. Сейчас туда **входит каждый сценарий заново**:
-- «чат: сообщения из панели» начинается с `goto` → `fill login` → `fill password` →
-- `click`, и так на каждый прогон, раз в `chat_check_every`. Вход занимает половину
-- из сорока секунд прогона, и о том, жива ли панель вообще, панель не знает ничего:
-- сломался пароль — узнаём по упавшему сценарию, а не по красной точке в шапке.
--
-- ЧТО ЗАВОДИМ. Сессия панели становится состоянием **сайта**: вошли один раз
-- сценарием, сняли cookie, дальше держим их живыми HTTP-ударом по таймеру — тем же
-- движком, что держит «в сети» анкеты (`py_modules/http_/http_heartbeat.py`).
--
-- ⚠ **Колонки у `dating_site`, хотя это снимок соседа.** Строку ведёт сверка
-- (`_snapshot_write`), но трогает она четыре поля — имя, адрес, метки, время, — а
-- прочие оставляет как есть: там уже живут наши расписания и выключатели. Заводить
-- ради сессии отдельную таблицу значило бы связывать её с сайтом внешним ключом,
-- которого у `site_id` нет и не будет (`database.md`).
--
-- ⚠ `panel_state` — ТО ЖЕ, ЧТО `dating_profile.storage_state`, и хранится так же:
-- JSON от соседа целиком, без разбора. Разбирать его умеет один
-- `http_heartbeat_cookies` — он же отбирает cookie нужного хоста.
--
-- ⚠ **Пароля панели здесь нет и не будет.** Логин и пароль лежат переменными сайта
-- **у соседа** (`panel_login`, `panel_password`) — их знает сценарий входа, а мы
-- зовём сценарий по имени. Копия пароля здесь означала бы второе место, где он
-- протухает.
--
-- ⚠ `panel_status` СТРОКОЙ, А НЕ `ENUM`. Значений три и они те же, что у проверки
-- присутствия (`online` | `offline` | `error`), но `ENUM` пришлось бы менять
-- миграцией на каждое новое состояние, а состояние здесь — пересказ чужого ответа.
-- Пусто значит «ещё не спрашивали», и это не то же, что `offline`.
--
-- Идемпотентность: каждая колонка заводится по условию отсутствия.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
           AND COLUMN_NAME LIKE 'panel\_%') AS колонок_панели,
       (SELECT COUNT(*) FROM `dating_site` WHERE `is_gone` = 0) AS сайтов;

-- ------------------------------------------- Сессия панели
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'panel_state') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `panel_state` JSON NULL
            COMMENT ''storage_state сессии админ-панели: снят сценарием входа''
            AFTER `chat_answer_on`',
    'SELECT ''колонка panel_state уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Что показываем в шапке
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'panel_status') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `panel_status` VARCHAR(16) NOT NULL DEFAULT ''''
            COMMENT ''online | offline | error; пусто — ещё не проверяли''
            AFTER `panel_state`',
    'SELECT ''колонка panel_status уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Когда проверяли и когда входили
--
-- ⚠ Две метки, а не одна. `panel_checked_at` отвечает «свежий ли перед вами
-- ответ» — по ней страница решает, не протухла ли точка; `panel_login_at` — «когда
-- сессии начали», по ней видно, живёт панель сутки или переваливается каждые
-- десять минут. Слитые в одну, они отвечали бы на первый вопрос и молчали о втором.
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'panel_checked_at') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `panel_checked_at` DATETIME NULL
            COMMENT ''когда последний раз спрашивали панель''
            AFTER `panel_status`',
    'SELECT ''колонка panel_checked_at уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'panel_login_at') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `panel_login_at` DATETIME NULL
            COMMENT ''когда последний раз входили сценарием''
            AFTER `panel_checked_at`',
    'SELECT ''колонка panel_login_at уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Чем кончилось, если не вышло
--
-- ⚠ Строка живёт до следующей удачи и стирается ею. Пустая строка и значит
-- «сейчас всё хорошо»: отдельного флага «была ошибка» не нужно.
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'panel_error') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `panel_error` VARCHAR(500) NOT NULL DEFAULT ''''
            COMMENT ''почему панель не отвечает; пусто — всё хорошо''
            AFTER `panel_login_at`',
    'SELECT ''колонка panel_error уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
           AND COLUMN_NAME LIKE 'panel\_%') AS колонок_панели,
       (SELECT COUNT(*) FROM `dating_site` WHERE `panel_status` <> '') AS панелей_с_ответом;
