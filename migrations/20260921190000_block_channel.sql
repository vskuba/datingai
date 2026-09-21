-- Запрет знает, какого канала он касается (2026-09-21)
--
-- ЗАЧЕМ. Отказы сайта, по которым пара попадает в «не пишем», бывают двух разных
-- свойств, и до сих пор мы считали их одинаковыми:
--
--   ERROR_MESSAGE_USER_DENY     «User ignore messages from you»
--       Он закрылся от девушки. Это про неё саму, а не про способ доставки:
--       ни реплики в чат, ни письма.
--
--   ERROR_MESSAGE_AGE_CRITERIA  «You do not match the criteria of age…»
--       Он выставил, от женщин какого возраста готов получать ПИСЬМА. К чату это
--       отношения не имеет.
--
-- ⚠⚠ ЭТО ПОДТВЕРЖДЕНО ЖУРНАЛОМ, А НЕ ДОГАДКА. За всё время отказы ни разу не
-- пересеклись по каналам: «по возрасту» приходил только из почты (1 раз), «он
-- закрылся» — только из чата (6 раз).
--
-- ⚠ БЕЗ ЭТОГО СТОЛБЦА ОТКАЗ ПО ВОЗРАСТУ ГАСИЛ БЫ ИНВАЙТЫ. Набор инвайтов зовёт
-- `block_pairs` и до сих пор получал все строки подряд; добавив примету возраста
-- в общий список, мы молча запретили бы чат тем, кто всего лишь не хочет писем от
-- женщины этого возраста. Регрессия внесена сегодня же и этим столбцом чинится.
--
-- ⚠ `all` УМОЛЧАНИЕМ, и это верная сторона ошибки: накопленные 637 строк — все
-- из чата, по коду «он закрылся», и он действительно про оба канала. Новый столбец
-- их не меняет.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `block`) AS запретов,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'block'
           AND COLUMN_NAME = 'channel') AS столбец_есть;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'block'
               AND COLUMN_NAME = 'channel');

SET @sql = IF(@add,
    "ALTER TABLE `block` ADD COLUMN `channel` ENUM('all','mail') NOT NULL
         DEFAULT 'all'
         COMMENT 'Чего касается запрет: всего или только писем'
         AFTER `source`",
    'SELECT ''столбец уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- ⚠ Уже накопленные строки с приметой возраста — их канал почтовый. Сегодняшний
-- заход мог успеть завести такие до этой миграции.
UPDATE `block` SET `channel` = 'mail'
 WHERE `reason` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%' AND `channel` <> 'mail';

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `block`) AS запретов,
       (SELECT COUNT(*) FROM `block` WHERE `channel` = 'all') AS про_оба_канала,
       (SELECT COUNT(*) FROM `block` WHERE `channel` = 'mail') AS только_письма;
