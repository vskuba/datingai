-- Галерея: номер файла уникален в пределах анкеты, а не всей таблицы (2026-11-20)
--
-- ЗАЧЕМ. Одна девушка ведётся на двух сайтах — Olena: Ladadate (#55) и Luxee
-- (#64), — и у обеих анкет одна папка на Диске. Ключ `uniq_file (file_id)`
-- держал номер файла один на всю таблицу, и обход #64 упирался в строки #55:
-- `ON DUPLICATE KEY UPDATE` молча переписывал **чужую** строку, не меняя её
-- владелицу. Ручка честно докладывала «94 files, 94 new», а у #64 не
-- появлялось ни одной плитки — и так при каждом нажатии «Refresh».
--
-- ЧТО. Ключ становится парой `(profile_id, file_id)`: у каждой анкеты своя
-- строка, своя память показов и своя правка руками. Описание двойнику
-- переносит обход (`gallery_index._twin_copy`), чтобы не платить модели
-- зрения второй раз за тот же снимок.
--
-- ⚠ Дублей пары сейчас быть не может: прежний ключ был строже нового.

SET @old = (SELECT COUNT(*) FROM `information_schema`.`STATISTICS`
             WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
               AND `INDEX_NAME` = 'uniq_file');

SET @sql = IF(@old > 0, 'ALTER TABLE `gallery_file` DROP INDEX `uniq_file`', 'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @new = (SELECT COUNT(*) FROM `information_schema`.`STATISTICS`
             WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
               AND `INDEX_NAME` = 'uniq_profile_file');

SET @sql = IF(@new = 0,
    'ALTER TABLE `gallery_file` ADD UNIQUE KEY `uniq_profile_file` (`profile_id`, `file_id`)',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
