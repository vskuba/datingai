-- Отметка «реплику доложили в историю агента у соседа» (2026-11-02)
--
-- ЗАЧЕМ. Текст, написанный оператором **руками в панели сайта**, к соседу не
-- попадал ничем. Наши собственные реплики пишет у него та дорога, которая их
-- сочинила (прогон workflow либо `mail_send_mark`), а операторская дорога —
-- это вообще не наш код: человек набрал текст в панели агентства, сайт его
-- отправил, и узнаём мы о нём только заходом импорта. Агент такой реплики не
-- видит и продолжает разговор, будто её не было.
--
-- ⚠⚠ ПОЧЕМУ НЕ ХВАТИЛО «ДОКЛАДЫВАЕМ ТО, ЧТО ЛЕГЛО». Правило, которым уже
-- докладываются входящие, здесь не работает, и это **замерено**, а не
-- предположено. Исходящая строка из панели ложится и тогда, когда она наша:
--
--   * **инвайт.** Он не пишется в `message` при отправке (пока человек молчит,
--     разговора нет), а переносится туда первым ответом — то есть **позже**,
--     чем панельная строка с тем же текстом успевает лечь. На проде за 90 дней
--     таких строк 11, и как минимум одна сошлась с живым черновиком инвайта;
--   * **наша отправка в полёте.** Между «сайт принял кадр» и нашей записью в
--     `message` проходят секунды, и заход импорта помещается в этот промежуток.
--
-- Отличить их в момент вставки нечем: пара появится позже. Поэтому отметка —
-- отдельная колонка, а отбор идёт **выдержкой**: строка докладывается, когда
-- она отлежалась и пары у неё так и не нашлось.
--
-- ⚠⚠⚠ НАКОПЛЕННОЕ МЕТИМ СДЕЛАННЫМ. Колонка заводится пустой, и пустая значит
-- «не докладывали» — то есть все 11 накопленных строк уехали бы к соседу
-- разом, вперемешку со старыми инвайтами, которых там быть не должно.
-- Поэтому вторым шагом `UPDATE` ставит отметку **всему, что уже есть**:
-- история задним числом не восстанавливается, чинится дорога вперёд.
--
-- ⚠ Колонка `NULL` и **без умолчания** — та же грабля, что уже стоила ошибки
-- у `mail_reply.updated_at`: заведённая `NOT NULL DEFAULT CURRENT_TIMESTAMP`,
-- она получила бы от `ALTER` время миграции всем строкам, и отличить
-- «доложено тогда-то» от «доложено миграцией» стало бы нечем. Здесь отметку
-- накопленному ставит **явный** `UPDATE`, и в нём видно, каким временем.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `message`) AS реплик,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'message'
           AND `COLUMN_NAME` = 'history_at') AS колонка_уже;

-- 1. Сама колонка.
SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'message'
                AND `COLUMN_NAME` = 'history_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `message`
     ADD COLUMN `history_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда реплику доложили в историю агента у соседа. NULL — не докладывали''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2. Накопленное — сделанным. ⚠ Только при первом применении: повторный заход
--    не должен трогать строки, которые с тех пор завели и ещё не доложили.
SET @sql = IF(@есть = 0,
  'UPDATE `message` SET `history_at` = `created_at` WHERE `history_at` IS NULL',
  'SELECT ''колонка была — накопленное не трогаем'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 3. Индекс под отбор: «что ещё не доложено». ⚠ Ведущая колонка —
--    `history_at`: отметка стоит почти у всех строк, и `NULL`-ов в индексе
--    единицы — то есть отбор упирается ровно в них, не читая остального.
SET @есть_и = (SELECT COUNT(DISTINCT `INDEX_NAME`) FROM `information_schema`.`STATISTICS`
                WHERE `TABLE_SCHEMA` = DATABASE()
                  AND `TABLE_NAME` = 'message'
                  AND `INDEX_NAME` = 'idx_dating_message_history');

SET @sql = IF(@есть_и = 0,
  'ALTER TABLE `message`
     ADD KEY `idx_dating_message_history` (`history_at`, `direction`, `source`)',
  'SELECT ''индекс уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `message`) AS реплик,
       (SELECT COUNT(*) FROM `message` WHERE `history_at` IS NULL) AS не_доложено,
       (SELECT COUNT(DISTINCT `INDEX_NAME`) FROM `information_schema`.`STATISTICS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'message'
           AND `INDEX_NAME` = 'idx_dating_message_history') AS индексов;
