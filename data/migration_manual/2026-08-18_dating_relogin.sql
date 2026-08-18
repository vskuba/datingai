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
