-- Снимок у письма рассылки (2026-11-04)
--
-- ЗАЧЕМ. Рассылка — единственный пул писем, который не носил фотографий:
-- `mail_reply`, `favorite_mail` и `intention_mail` колонку имеют давно, а
-- `mailing` уходила текстом. Решением владельца снимок прикладывается к
-- **каждому** письму рассылки, если у анкеты есть неотправленный этой паре
-- кадр.
--
-- ⚠⚠ ЖРЕБИЯ ЗДЕСЬ НЕТ, и это отличие от ответов с приставанием. Там долю
-- бросает шаг `dice` у соседа и кладёт `img_attach` в `extra` прогона — потому
-- что письмо сочиняется на пару и вызов агента всё равно идёт. Рассылка же
-- собирается одним шаблоном на всех, агента на письмо не зовёт, и второе место
-- с долей разошлось бы с первым молча. Правило простое: есть свободный
-- снимок — прикладываем, нет — письмо уходит текстом.
--
-- ⚠ Колонка `NOT NULL DEFAULT 0`, как у трёх соседних: ноль значит «без
-- снимка», и это самое частое состояние накопленных строк. `NULL` завёл бы
-- второй вид пустоты на тот же вопрос.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `mailing`) AS писем,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'mailing'
           AND `COLUMN_NAME` = 'gallery_file_id') AS колонка_уже;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'mailing'
                AND `COLUMN_NAME` = 'gallery_file_id');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mailing`
     ADD COLUMN `gallery_file_id` INT NOT NULL DEFAULT 0
       COMMENT ''Снимок письма: строка gallery_file. 0 — письмо без фотографии''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `mailing`) AS писем,
       (SELECT COUNT(*) FROM `mailing` WHERE `gallery_file_id` = 0) AS без_снимка,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'mailing'
           AND `COLUMN_NAME` = 'gallery_file_id') AS колонка;
