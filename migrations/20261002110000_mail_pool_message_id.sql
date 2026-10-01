-- Пулы писем помнят номер обмена разговора (2026-10-02)
--
-- ## ⚠⚠ ЗАЧЕМ
--
-- Чат отправляет ответ сразу, и номер обмена живёт в памяти вызова. Письма
-- ложатся в пул и ждут круга отправки, иногда часами, — между сочинением и
-- отправкой номер теряется, и сказать соседу «эта реплика дошла» нечем.
--
-- Пулов два, потому что через `dating_talk` ходят оба: ответ на письмо
-- (`mail_reply`) и письмо избранному (`favorite_mail`). Рассылка (`mailing`) и
-- инвайты идут своими workflow и в `companion_session` не пишут вовсе —
-- колонки им не нужно.
--
-- ## ⚠ `NULL` значит «не знаем»
--
-- Строка могла родиться до этой правки либо агент промолчал. Круг отправки на
-- таком не спотыкается — просто не зовёт отметку.
--
-- Идемпотентность: обе колонки заводятся по условию.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
           AND COLUMN_NAME = 'message_id') AS у_ответов,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
           AND COLUMN_NAME = 'message_id') AS у_избранных;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
        AND COLUMN_NAME = 'message_id') = 0,
    'ALTER TABLE `mail_reply`
        ADD COLUMN `message_id` INT NULL DEFAULT NULL
            COMMENT ''Номер обмена разговора у соседа; им помечаем доставку''',
    'SELECT ''у mail_reply колонка уже есть'' AS пропуск');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
        AND COLUMN_NAME = 'message_id') = 0,
    'ALTER TABLE `favorite_mail`
        ADD COLUMN `message_id` INT NULL DEFAULT NULL
            COMMENT ''Номер обмена разговора у соседа; им помечаем доставку''',
    'SELECT ''у favorite_mail колонка уже есть'' AS пропуск');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
           AND COLUMN_NAME = 'message_id') AS у_ответов,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
           AND COLUMN_NAME = 'message_id') AS у_избранных;
