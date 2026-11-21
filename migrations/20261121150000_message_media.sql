-- Реплика со снимком: превью и полный размер — своей колонкой (2026-11-21)
--
-- ЗАЧЕМ. Девушка Luxee шлёт в чат снимки (подарок и обещанное фото), а окно
-- разговора видело только подпись и слово «[photo]» — что именно ушло, по
-- панели было не понять. Владелец: «фото, которое было отослано, нужно
-- отображать в диалоге — превью с открытием».
--
-- ЧТО. `message.media` — JSON-список `[{"thumb": адрес, "src": адрес}]`.
-- Пишут его наша отправка (`send_message_remember`) и импорт из панели
-- (`site_import_luxee.py`); `NULL` — снимков нет, у всех прежних строк так и
-- остаётся.

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'message'
                AND `COLUMN_NAME` = 'media');

SET @sql = IF(@есть = 0,
    'ALTER TABLE `message` ADD COLUMN `media` JSON NULL COMMENT ''снимки реплики: [{thumb, src}]''',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
