-- Таблицы без префикса `dating_` (2026-09-17)
--
-- ЗАЧЕМ. Проект делает ровно одно занятие — анкеты на сайтах знакомств, — и своя
-- база у него одна. Префикс `dating_` в ней не различает ничего: он есть у всех
-- девятнадцати таблиц, то есть не несёт сведений и только удлиняет каждый
-- запрос. `dating_profile` в базе `dating` читается как «dating dating profile».
--
-- ⚠ ТРИ ЧУЖИЕ ТАБЛИЦЫ ОСТАЮТСЯ КАК ЕСТЬ: `setting`, `user`, `user_access_token`
-- префикса и не имели — они из общего слоя (`py_modules/auth_`, `setting_`), и
-- переименовать их значило бы разойтись с соседними проектами.
--
-- ⚠ СЛУЖЕБНЫЕ `_yoyo_*` И `yoyo_lock` НЕ ТРОГАЕМ: их ведёт сам накатчик
-- миграций, и переименование остановило бы его на первом же запуске.
--
-- ⚠⚠ RENAME TABLE, А НЕ CREATE + INSERT + DROP. Переименование в MySQL — правка
-- словаря данных: атомарная, мгновенная, не копирующая ни байта. Внешние ключи,
-- индексы и права переезжают сами. Копирование же на таблице в 42 тысячи строк
-- (`dating_message` на проде) означало бы простой и риск потерять строки,
-- записанные между копией и удалением.
--
-- ⚠ ВСЕ ПЕРЕИМЕНОВАНИЯ ОДНИМ ОПЕРАТОРОМ. `RENAME TABLE a TO b, c TO d`
-- выполняется целиком или не выполняется вовсе. Разбей мы его на девятнадцать
-- команд — сбой на середине оставил бы базу, где часть таблиц с префиксом, а
-- часть без: код не работает ни со старыми именами, ни с новыми.
--
-- ⚠ ВНЕШНИЕ КЛЮЧИ СЛЕДУЮТ ЗА ТАБЛИЦЕЙ САМИ. У `dating_profile` их шесть
-- (`ON DELETE CASCADE` из инвайтов, писем, сообщений); MySQL правит ссылки при
-- переименовании. Отдельных ALTER не нужно, и делать их вредно.
--
-- Идемпотентность: переименовываем, только если старое имя ещё существует.
-- Проверка по **одному** имени — их либо все девятнадцать, либо ни одного,
-- потому что оператор атомарен.

SELECT 'ДО' AS этап,
       SUM(TABLE_NAME LIKE 'dating\_%') AS с_префиксом,
       COUNT(*) AS всего
  FROM information_schema.TABLES
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE';

SET @sql = IF((SELECT COUNT(*) FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'dating_profile') = 1,
              'RENAME TABLE `dating_block` TO `block`,
       `dating_companion` TO `companion`,
       `dating_contact` TO `contact`,
       `dating_error` TO `error`,
       `dating_favorite` TO `favorite`,
       `dating_favorite_mail` TO `favorite_mail`,
       `dating_invite` TO `invite`,
       `dating_log` TO `log`,
       `dating_mail` TO `mail`,
       `dating_mail_in` TO `mail_in`,
       `dating_mail_reply` TO `mail_reply`,
       `dating_mail_var` TO `mail_var`,
       `dating_mailing` TO `mailing`,
       `dating_message` TO `message`,
       `dating_profile` TO `profile`,
       `dating_profile_var` TO `profile_var`,
       `dating_request` TO `request`,
       `dating_scenario_run` TO `scenario_run`,
       `dating_site` TO `site`',
              'SELECT ''таблицы уже без префикса''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       SUM(TABLE_NAME LIKE 'dating\_%') AS с_префиксом,
       COUNT(*) AS всего
  FROM information_schema.TABLES
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE';

-- ⚠ Внешние ключи обязаны смотреть на новые имена: не перенеси их MySQL,
-- каскадное удаление анкеты молча перестало бы чистить её переписку.
SELECT 'КЛЮЧИ' AS этап, COUNT(*) AS смотрят_на_profile
  FROM information_schema.KEY_COLUMN_USAGE
 WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME = 'profile';
