-- Когда реплику пробовали отправить в последний раз (2026-11-04)
--
-- ЗАЧЕМ. Строка, у которой собеседник вышел, возвращается в пул **чистой**:
-- уход проходит сам через минуты, и глушить готовую реплику причиной, которая
-- рассосётся, нельзя. Но очередь отправки стоит на `created_at`, а он не
-- меняется — и отвергнутая строка остаётся первой **навсегда**.
--
-- Замер прода 04.11.2026: **106 попыток к одному человеку** за десять минут,
-- ноль удач, пул из 531 черновика не двигался вовсе.
--
-- ⚠⚠ ПОЧЕМУ НЕ `updated_at`. Та меняется от любой правки строки и получает
-- `CURRENT_TIMESTAMP` уже **при вставке** — то есть у свежей строки она
-- «сегодня», даже если `created_at` задан прошлым годом. Отсортируй очередь по
-- ней, и порядок по возрасту сломался бы у всего пула. Поймано сторожем.
--
-- ⚠ Колонка `NULL` и **без умолчания**: пустая значит «не пробовали», и такие
-- строки идут первыми, между собой по возрасту — как было до правки. Заведи её
-- `NOT NULL DEFAULT CURRENT_TIMESTAMP`, и `ALTER` проставил бы всем накопленным
-- время миграции, то есть «все пробованы только что», — очередь перемешалась бы
-- разом. Та же грабля, что уже стоила ошибки у `mail_reply.updated_at`.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `invite`) AS строк,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'invite'
           AND `COLUMN_NAME` = 'tried_at') AS колонка_уже;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'invite'
                AND `COLUMN_NAME` = 'tried_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `invite`
     ADD COLUMN `tried_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда реплику в последний раз пробовали отправить. NULL — не пробовали''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- Индекс под очередь. ⚠ Порядок колонок — тот же, что в `ORDER BY` отбора:
-- сперва сайт и состояние (они в `WHERE`), затем ключ сортировки.
SET @есть_и = (SELECT COUNT(DISTINCT `INDEX_NAME`) FROM `information_schema`.`STATISTICS`
                WHERE `TABLE_SCHEMA` = DATABASE()
                  AND `TABLE_NAME` = 'invite'
                  AND `INDEX_NAME` = 'idx_dating_invite_queue');

SET @sql = IF(@есть_и = 0,
  'ALTER TABLE `invite`
     ADD KEY `idx_dating_invite_queue` (`site_id`, `status`, `tried_at`)',
  'SELECT ''индекс уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `invite`) AS строк,
       (SELECT COUNT(*) FROM `invite` WHERE `tried_at` IS NULL) AS не_пробованы,
       (SELECT COUNT(DISTINCT `INDEX_NAME`) FROM `information_schema`.`STATISTICS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'invite'
           AND `INDEX_NAME` = 'idx_dating_invite_queue') AS индексов;
