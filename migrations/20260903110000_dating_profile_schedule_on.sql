-- Выключатель расписания (2026-09-03)
--
-- ЗАЧЕМ: расписание хранилось и показывалось, но исполнять его было нельзя — не
-- было способа сказать «этой анкетой веди по сменам». Пустое расписание значит
-- «ведут руками», но у анкеты с заведёнными сменами два разных случая: смены
-- набросали на будущее и смены надо исполнять. Различить их нечем.
--
-- Теперь различает `schedule_on`: чекбокс в правом верхнем углу блока расписания.
--
-- ⚠ ПОЧЕМУ ОТДЕЛЬНЫЙ ФЛАГ, А НЕ «ЕСТЬ СМЕНЫ = ИСПОЛНЯЕМ». Расписание правят
-- ползунком, и промежуточные состояния законны: человек двигает ручку, смены на
-- секунду налезают, потом расходятся. Исполняй мы «по факту наличия», воркер
-- входил бы и выходил на каждое движение — то есть гонял браузер по чужой правке.
--
-- ⚠ ПОЧЕМУ УМОЛЧАНИЕ 0. Расписание уже стоит у 64 анкет — его проставила миграция
-- умолчанием, а не человек. Умолчание 1 означало бы, что воркер немедленно начнёт
-- входить и выходить у всех, о чём никто не просил: вход это действие наружу,
-- самое заметное из всех.
--
-- Идемпотентность: колонка добавляется только если её нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME = 'schedule_on') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_profile`
         WHERE `schedule` IS NOT NULL AND JSON_LENGTH(`schedule`) > 0) AS со_сменами;

-- ------------------------------------------- Колонка
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'schedule_on') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `schedule_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Исполнять расписание: воркер входит и выходит по сменам''
            AFTER `schedule`',
    'SELECT ''колонка schedule_on уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Индекс под отбор круга
--
-- Круг расписания спрашивает «кого вести по сменам» раз в минуту. Порядок в
-- индексе — от самой избирательной колонки: с включённым расписанием единицы,
-- выключенных анкет мало.
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND INDEX_NAME = 'idx_dating_profile_sched') = 0,
    'ALTER TABLE `dating_profile`
        ADD INDEX `idx_dating_profile_sched` (`schedule_on`, `enabled`)',
    'SELECT ''индекс idx_dating_profile_sched уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `schedule_on` = 1) AS исполняется,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `schedule_on` = 0) AS не_исполняется;
