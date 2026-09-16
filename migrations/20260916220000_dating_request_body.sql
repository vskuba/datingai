-- Журнал запросов: размер ответа и его хвост при отказе (2026-09-16)
--
-- ЗАЧЕМ. Разбор живого журнала прода показал беду, которую нечем расследовать:
-- **156 инвайтов застряли с причиной «сайт ответил 200»**. Причина называет код
-- и молчит о главном — что было в теле. А ответ лежал ровно там: у Ladadate
-- признак удачи `"IsSuccess":true`, и его отсутствие при коде 200 и есть отказ.
--
-- Это самый частый тихий отказ у сайтов знакомств: код удачи, тело отказа.
-- Восстановить, почему полторы сотни сообщений не ушли живым людям, сейчас
-- нечем — журнал этих данных не сохранил.
--
-- ЧТО ДОБАВЛЯЕМ.
--
--   `bytes` — длина ответа. Показывает «200, 43 байта» рядом с «200, 8 КБ»:
--             пустой ответ виден глазом, без разбирательства.
--   `body`  — первые 500 знаков тела, **только у отказов**. Отвечает на вопрос
--             «а что он вообще сказал», на который сейчас ответить нечем.
--
-- ⚠ ХВОСТ ТОЛЬКО У ОТКАЗОВ, и это не экономия места. Тело удачного ответа — это
-- переписка живых людей, списки собеседников, почта: 52 тысячи строк в сутки с
-- личными данными внутри превратили бы журнал в копию базы сайта. У отказа же
-- тела как такового нет — есть сообщение об ошибке.
--
-- ⚠ СЕКРЕТЫ ВЫРЕЗАЮТСЯ ПЕРЕД ЗАПИСЬЮ (`dating_request.py`). Тело отказа входа
-- вполне может содержать эхо отправленного логина, а у нас уже был день, когда
-- пароли уезжали в журнал открытым текстом.
--
-- Идемпотентность: колонки заводятся по условию отсутствия.

SELECT 'ДО' AS этап,
       SUM(COLUMN_NAME = 'bytes') AS есть_размер,
       SUM(COLUMN_NAME = 'body') AS есть_тело
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_request';

-- ------------------------------------------- Размер ответа
SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_request'
                  AND COLUMN_NAME = 'bytes') = 0,
              'ALTER TABLE `dating_request` ADD COLUMN `bytes` INT NOT NULL DEFAULT 0'
              ' COMMENT ''Длина ответа: пустое тело при коде 200 — частый тихий отказ''',
              'SELECT ''колонка bytes уже есть''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Хвост тела у отказа
SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_request'
                  AND COLUMN_NAME = 'body') = 0,
              'ALTER TABLE `dating_request` ADD COLUMN `body` VARCHAR(500) NOT NULL DEFAULT '''''
              ' COMMENT ''Начало тела — только у отказов, с вырезанными секретами''',
              'SELECT ''колонка body уже есть''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       SUM(COLUMN_NAME = 'bytes') AS есть_размер,
       SUM(COLUMN_NAME = 'body') AS есть_тело
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_request';
