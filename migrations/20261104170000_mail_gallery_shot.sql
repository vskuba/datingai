-- Снимок принадлежит шаблону (2026-11-04)
--
-- ЗАЧЕМ. Кадр рассылки выбирался получателю («первый, которого он ещё не
-- видел»), и все новые адресаты получали один и тот же первый снимок галереи:
-- на странице разных шаблонов стояла одна и та же миниатюра. Правило владельца
-- от 04.11.2026: **у каждого живого шаблона свой кадр** — тогда одному
-- человеку, которому за день достались два разных шаблона, уйдут два разных
-- кадра.
--
-- Две колонки, потому что память здесь двух видов:
--
--   `mail.gallery_file_id`       — привязка: какой кадр принадлежит этому
--     шаблону. Живёт, пока жив шаблон: чистка `mail_round._clean` удалит
--     строку — кадр освободится и снова будет годен.
--   `gallery_file.last_shot_at`  — свежесть: когда кадр последний раз
--     выбирался шаблону. Переживает смерть шаблонов намеренно: без неё каждый
--     день выбирался бы один и тот же свежий кадр (привязки-то освобождаются),
--     а это ровно то, что правило запрещает.
--
-- ⚠ UNIQUE НА (profile_id, gallery_file_id) НЕ ВОДИМО. При сбросе круга
-- повтор законен («все кадры использованы — начинаем круг заново, выбор в
-- случайном порядке»), а «без кадра» (`0`) было бы у всех анкет с пустой
-- галереей разом. Уникальность живых шаблонов — правило отбора, одна дверь
-- `mail_shot_for_template`, а не индекс.
--
-- ⚠ КОЛОНКА `gallery_file` переживает переезд индексатора: тот пишет
-- `INSERT … ON DUPLICATE KEY UPDATE` списком колонок и новых не касается, а
-- оживление (`is_gone = 0`) возвращает строку с её историей показаний честно:
-- файл на Диске тот же.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'mail'
           AND `COLUMN_NAME` = 'gallery_file_id') AS привязка_есть,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'gallery_file'
           AND `COLUMN_NAME` = 'last_shot_at') AS свежесть_есть;

-- ------------------------------------------- Привязка: шаблон ↔ кадр
SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'mail'
                AND `COLUMN_NAME` = 'gallery_file_id');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mail`
     ADD COLUMN `gallery_file_id` INT NOT NULL DEFAULT 0
       COMMENT ''Кадр шаблона: строка gallery_file. 0 — шаблон без снимка. Занятость считают только включённые шаблоны''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- ------------------------------------------- Свежесть: когда кадр выбирали
SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'gallery_file'
                AND `COLUMN_NAME` = 'last_shot_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `gallery_file`
     ADD COLUMN `last_shot_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда кадр последний раз выбирался шаблону рассылки. NULL — никогда; сброс круга обнуляет колонку по анкете''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- ---------------------------------------------------------------- После
-- Индексов не заводим: отбор ходами по анкете считается в таблицах на
-- десятки строк, а не на тысячи.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'mail'
           AND `COLUMN_NAME` = 'gallery_file_id') AS привязка,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'gallery_file'
           AND `COLUMN_NAME` = 'last_shot_at') AS свежесть,
       (SELECT COUNT(*) FROM `mail` WHERE `gallery_file_id` = 0) AS шаблонов_без_кадра;
