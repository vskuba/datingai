-- Выключатель анкеты целиком (2026-09-02)
--
-- ЗАЧЕМ: у анкеты был один выключатель — `auto_answer`, и он про одно: отвечать ли
-- агентом. Понадобился второй, шире: **вести анкету или нет вовсе**. Выключенную
-- воркер не трогает — ни автоответ, ни отправка, ни сценарии; получать входящие
-- при этом можно, и это осознанно (переписка продолжает копиться, и включив анкету
-- обратно, человек видит, что за это время написали).
--
-- ⚠ ДВА ВЫКЛЮЧАТЕЛЯ, А НЕ ОДИН, И ЭТО НАМЕРЕННО. `enabled` — «ведём ли анкету»,
-- `auto_answer` — «отвечает ли за неё агент». Их путают, но случаи разные: анкету
-- ведут руками (включена, автоответ снят), и анкету отставляют совсем (выключена).
-- Свести их в один флаг значило бы отнять первый случай.
--
-- ПОЧЕМУ DEFAULT 1: 64 заведённые анкеты работали до этой колонки, и появление
-- выключателя не должно останавливать их разом. Умолчание — «как было».
--
-- Идемпотентность: колонка добавляется только если её нет. Прогонять файл можно
-- сколько угодно раз.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME = 'enabled') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_profile`) AS анкет;

-- ------------------------------------------- Колонка
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'enabled') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `enabled` TINYINT(1) NOT NULL DEFAULT 1
            COMMENT ''Ведём ли анкету: выключенную воркер не трогает вовсе''
            AFTER `auto_answer_only`',
    'SELECT ''колонка enabled уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME = 'enabled') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `enabled` = 1) AS включено,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `enabled` = 0) AS выключено;
