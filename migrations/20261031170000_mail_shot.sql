-- Снимок к письму: ответам и приставанию — жребием (2026-10-31)
--
-- ЗАЧЕМ. Снимок к письму умел прикладывать ровно один пул — письма-исполнения
-- обещаний (`intention_mail`): там агент попросил фотографию, и она уходит
-- вложением. Во всех прочих письмах девушка писала только текстом, и это
-- заметно со стороны: живой человек время от времени прикладывает к письму
-- снимок без всякой просьбы.
--
-- Теперь это делает жребий — редко, долей настройки сайта (умолчание 5%).
--
-- ⚠⚠ ПОЧЕМУ КОЛОНКА, А НЕ ОТБОР ПРИ ОТПРАВКЕ. Между сочинением письма и его
-- уходом проходят минуты и часы: держат пауза отправительницы, остуда ящика
-- получателя, расписание. Считай мы снимок в момент отправки, человек видел бы
-- в пуле одно, а уходило бы другое — и память «этот кадр он уже видел»
-- расходилась бы с тем, что показано. Ровно по этой причине номер снимка
-- хранится и у `intention_mail`.
--
-- ⚠ Ноль — законное и САМОЕ ЧАСТОЕ состояние: девяносто пять писем из ста
-- уходят текстом. Поэтому `NOT NULL DEFAULT 0`, а не `NULL`: «снимка нет» —
-- это ответ, а не отсутствие ответа.
--
-- ⚠ Внешнего ключа на `gallery_file` нет — как и у `intention_mail`
-- (`database.md`): индекс галереи перестраивается обходом, пропавший файл в
-- нём гасится, а не удаляется, и каскад уносил бы письма вместе со снимками.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_reply'
           AND `COLUMN_NAME` = 'gallery_file_id') AS у_ответов,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'favorite_mail'
           AND `COLUMN_NAME` = 'gallery_file_id') AS у_приставания;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_reply'
                AND `COLUMN_NAME` = 'gallery_file_id');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mail_reply`
     ADD COLUMN `gallery_file_id` INT NOT NULL DEFAULT 0
       COMMENT ''Какой снимок уйдёт с письмом: строка gallery_file. Ноль — письмо текстом''
       AFTER `body`',
  'SELECT ''у ответов колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'favorite_mail'
                AND `COLUMN_NAME` = 'gallery_file_id');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `favorite_mail`
     ADD COLUMN `gallery_file_id` INT NOT NULL DEFAULT 0
       COMMENT ''Какой снимок уйдёт с письмом: строка gallery_file. Ноль — письмо текстом''
       AFTER `body`',
  'SELECT ''у приставания колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` IN ('mail_reply', 'favorite_mail')
           AND `COLUMN_NAME` = 'gallery_file_id') AS колонок_стало;
