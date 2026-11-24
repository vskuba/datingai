-- Быстрый ответ: шаблоны, чем закрыть длинную тишину (2026-11-22)
--
-- ЗАЧЕМ. Собеседник пишет, а ответ агента собирается минуты: судья, LLM,
-- задержка по длине — и всё это время он смотрит в пустой чат. Живая девушка
-- на такую паузу отвечает хотя бы «hi». Мы — молчанием, и часть разговоров
-- умирает в этой паузе, не дожив до ответа.
--
-- ЧТО. `chat_quick` — список шаблонов сайта (смайлик, «hi», что угодно одной
-- строкой). Круг (`chat_quick_round.py`) через `chat_quick_after` секунд
-- тишины шлёт один из них **случайный**, а агент тем временем сочиняет
-- настоящий ответ: два ответа на одну реплику — штатный исход, а не поломка.
--
-- ⚠ ЗАПИСЕЙ МОЖЕТ МНОГО, ОДНА НЕОБЯЗАТЕЛЬНА. Ответ выбирается жребием из
-- всех: одна и та же «привет-привет-привет» через месяц читается роботом,
-- а разброс шаблонов — нет. Пустой список — занятие выключено, даже при
-- поднятом интервале: без шаблонов слать нечего.
--
-- ⚠ СВОЯ ТАБЛИЦА, а не настройка `settings`. Шаблонов много, их добавляют и
-- удаляют по одному, и каждое изменение settings значило бы правку JSON
-- целиком под замком формы. Ровно так же устроены переменные писем
-- (`mail_var`) против самой настройки.
--
-- ⚠ `site_id`, а не `profile_id`: ответ — слово сайта, а не одной анкеты, и
-- заводить двадцать копий «hi» на анкеты значило бы править их двадцать раз.
--
-- ⚠ `source='quick'` в `message` — этим строкам потом обещано молчание: их
-- не считает агент (он пишет свой ответ параллельно), их не прячут близнецы
-- как панельный шум, их не видят счётчики инициативы. Значение здесь —
-- единственное место, где оно заводится; правка реестра колонок без этой
-- строки роняет вставку молча (`enum`, а не строка).
--
-- Идемпотентность: таблица по условию отсутствия, enum — по признаку значения.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_quick') AS таблица_есть,
       (SELECT COLUMN_TYPE LIKE '%quick%' FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message'
           AND COLUMN_NAME = 'source') AS source_знает_quick;

-- ------------------------------------------- Шаблоны сайта
CREATE TABLE IF NOT EXISTS `chat_quick` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `site_id` INT NOT NULL COMMENT 'Сайт-закладка: ответ — слово сайта, по нему и отбирают',
    `body` TEXT NOT NULL COMMENT 'Что уходит собеседнику; одна строка, жребий выбирает из всех',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    -- Круг спрашивает «что есть у этого сайта» целиком, списком, и спит по
    -- пять секунд: индекс по сайту — весь план доступа.
    INDEX `idx_chat_quick_site` (`site_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Быстрый ответ: шаблоны, которыми закрываем тишину, пока агент сочиняет';

-- ------------------------------------------- `source` учится `quick`
SET @sql = IF((SELECT COLUMN_TYPE NOT LIKE '%quick%' FROM information_schema.COLUMNS
                 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message'
                   AND COLUMN_NAME = 'source') = 1,
              'ALTER TABLE `message` MODIFY COLUMN `source`'
              ' ENUM(''socket'', ''http'', ''panel'', ''scenario'', ''invite'', ''quick'')'
              ' COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT ''socket'''
              ' COMMENT ''Каким путём реплика попала в переписку; quick — быстрый ответ по шаблону''',
              'SELECT ''source уже знает quick''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_quick') AS таблица_есть,
       (SELECT COUNT(*) FROM `chat_quick`) AS шаблонов,
       (SELECT COLUMN_TYPE LIKE '%quick%' FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message'
           AND COLUMN_NAME = 'source') AS source_знает_quick;
