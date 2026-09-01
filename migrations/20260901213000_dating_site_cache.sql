-- Список сайтов переживает недоступность соседа (2026-09-01)
--
-- ЗАЧЕМ: селект сайта стоит в шапке **каждой** страницы раздела, и список для него
-- брали у RhapsodyAi при каждом открытии. Лежит сосед — селект пуст, и панель
-- целиком выглядит сломанной: без выбранного сайта ни анкет, ни профилей, ни
-- журналов. Причём наши-то данные на месте, показать их ничто не мешает.
--
-- ⚠ ЭТО МЕНЯЕТ ПРАВИЛО, ЗАПИСАННОЕ В ПРЕДЫДУЩЕЙ МИГРАЦИИ. Там сказано: «имени и
-- адреса здесь нет и быть не должно». Теперь есть — и это осознанный размен, а не
-- забывчивость:
--
--   * было: копии нет, разойтись не с чем, но лежащий сосед гасит всю панель;
--   * стало: копия есть и может разойтись с оригиналом — ценой пустого селекта.
--
-- Расхождение при этом лечится само: каждый показ списка заводит фоновую сверку с
-- соседом, и переименованный сайт получает новое имя в ту же секунду, когда сосед
-- отвечает. Живёт расхождение ровно столько, сколько сосед недоступен, — то есть
-- ровно то время, которое иначе панель не работала бы вовсе.
--
-- ⚠ ВЕДЁТ СПИСОК ПО-ПРЕЖНЕМУ СОСЕД. Строки здесь — снимок его ответа, а не наш
-- список: сайт, которого у него нет, удаляется из снимка при первой же удачной
-- сверке (`is_gone`), а не живёт своей жизнью. Заводить сайты у себя нельзя —
-- ручки на это нет и не будет.
--
-- `synced_at` — когда снимок последний раз сверяли. Нужен человеку: список
-- недельной давности выглядит как свежий, и без отметки его не отличить.
--
-- ОТКАЗЫ СВЕРКИ — в `dating_error`, а не в журнал анкеты: они не про анкету и не
-- про сайт, а про связь между проектами. Строка одна на источник отказа
-- (`source`), с счётчиком повторов: лежащий сосед иначе залил бы таблицу тысячей
-- одинаковых строк за час.
--
-- Идемпотентность: колонки добавляются проверкой на существование, таблица —
-- `CREATE TABLE IF NOT EXISTS`. Файл можно гонять сколько угодно раз.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
           AND COLUMN_NAME = 'name') AS есть_имя,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_error') AS есть_ошибки;

-- ------------------------------------------- Снимок закладки соседа
SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'name') = 0,
    'ALTER TABLE `dating_site`
       ADD COLUMN `name` VARCHAR(190) NOT NULL DEFAULT ''''
           COMMENT ''Имя сайта у соседа: снимок, обновляется сверкой'',
       ADD COLUMN `url` VARCHAR(255) NOT NULL DEFAULT ''''
           COMMENT ''Адрес сайта у соседа: снимок'',
       ADD COLUMN `tags` VARCHAR(190) NOT NULL DEFAULT ''''
           COMMENT ''Метки закладки: по метке dating сосед и отбирает сайты'',
       ADD COLUMN `is_gone` TINYINT(1) NOT NULL DEFAULT 0
           COMMENT ''У соседа такой закладки больше нет: в селекте не показываем'',
       ADD COLUMN `synced_at` DATETIME NULL DEFAULT NULL
           COMMENT ''Когда снимок последний раз сверяли с соседом''',
    'SELECT ''колонки снимка уже есть'' AS пропуск');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Отказы, которые не про анкету
CREATE TABLE IF NOT EXISTS `dating_error` (
    `id` INT NOT NULL AUTO_INCREMENT,
    -- Откуда отказ. Строкой, а не перечислением: источники заводятся по ходу дела,
    -- и миграция на каждый новый — цена, которой это не стоит.
    `source` VARCHAR(64) NOT NULL COMMENT 'Что не вышло: sites_sync, …',
    `site_id` INT NOT NULL DEFAULT 0 COMMENT '0 — отказ не про конкретный сайт',
    `message` TEXT NOT NULL COMMENT 'Последняя причина словами — её и читает человек',
    -- Сколько раз подряд. Лежащий сосед даёт отказ на каждый показ страницы, и
    -- строка на каждый залила бы таблицу; вместо этого растёт счётчик.
    `hits` INT NOT NULL DEFAULT 1,
    `first_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        COMMENT 'Когда сломалось впервые — по нему видно, идёт это час или неделю',
    `last_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (`id`),
    -- Одна строка на источник и сайт: «что сломано сейчас» важнее истории отказов.
    UNIQUE KEY `uniq_dating_error` (`source`, `site_id`),
    INDEX `idx_dating_error_last` (`last_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
           AND COLUMN_NAME = 'name') AS есть_имя,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_error') AS есть_ошибки,
       (SELECT COUNT(*) FROM `dating_site`) AS сайтов;
