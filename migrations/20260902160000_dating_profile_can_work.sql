-- Признак «панель может работать с анкетой» (2026-09-02)
--
-- ЗАЧЕМ: воркер отбирал анкеты условием «снят вход» (`storage_state IS NOT NULL`).
-- Для сокетного сайта это верно — без входа нет адреса сокета. Но у Ladadate сокета
-- нет вовсе: там пишут сценарием, который входит сам, каждый раз заново, и
-- `storage_state` ему не нужен.
--
-- Итог: из 24 анкет Ladadate воркер видел НОЛЬ. Автоответ до них не доходил, и
-- понять это по панели было нельзя — анкета выглядела нормальной.
--
-- ⚠ ТРИ ПРИЗНАКА, И ПУТАТЬ ИХ ДОРОГО:
--   * `enabled`   — ведём ли анкету вовсе (ставит человек галочкой);
--   * `can_work`  — можно ли с ней работать (ставит код, по обстоятельствам);
--   * `status`    — в сети ли прямо сейчас (ставит опрос присутствия).
-- Свести их значило бы потерять состояния «ведём, но нечем войти» и «готова, но
-- сейчас не в сети», а по ним человек и понимает, что делать.
--
-- `work_reason` — почему нельзя, словами. ⚠ Не украшение: без неё панель говорит
-- «не работает», а человек ищет поломку в коде вместо того, чтобы вписать пароль.
--
-- ПОЧЕМУ DEFAULT 0: «готова» — вывод из обстоятельств, а не состояние по умолчанию.
-- Проставит его первый же круг воркера (он пересчитывает всех, кого обходит) и
-- любая проверка учётки. Умолчание 1 означало бы «готова, пока не доказано
-- обратное» — то есть воркер полез бы работать с анкетами без пароля и входа.
--
-- Идемпотентность: обе колонки добавляются только если их нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME = 'can_work') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_profile`) AS анкет,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `storage_state` IS NOT NULL)
           AS со_снятым_входом;

-- ------------------------------------------- Колонки
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'can_work') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `can_work` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Панель может работать с анкетой: условие зависит от транспорта сайта''
            AFTER `enabled`,
        ADD COLUMN `work_reason` VARCHAR(200) NOT NULL DEFAULT ''''
            COMMENT ''Почему работать нельзя — словами для панели''
            AFTER `can_work`',
    'SELECT ''колонки can_work и work_reason уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Индекс под отбор воркера
--
-- Воркер спрашивает «кого можно вести» раз в минуту, и отбор идёт по трём
-- колонкам сразу. Порядок в индексе — от самой избирательной: выключенных единицы,
-- неготовых бывает половина.
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND INDEX_NAME = 'idx_dating_profile_work') = 0,
    'ALTER TABLE `dating_profile`
        ADD INDEX `idx_dating_profile_work` (`enabled`, `can_work`, `site_id`)',
    'SELECT ''индекс idx_dating_profile_work уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME IN ('can_work', 'work_reason')) AS колонок,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND INDEX_NAME = 'idx_dating_profile_work') AS частей_индекса;
