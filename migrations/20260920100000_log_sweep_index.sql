-- Индекс под уборку журнала анкет (2026-09-20)
--
-- ЗАЧЕМ. Журнал чистится по возрасту — `created_at < NOW() - INTERVAL ? HOUR`, — а
-- индексов у него было два, и оба начинаются с `profile_id`. Удаление по времени
-- шло бы **полным сканом**: сейчас это 283 тысячи строк, и каждый заход уборки
-- держал бы таблицу, в которую пишут все круги разом.
--
-- ⚠ Тот же индекс, что у журнала запросов (`idx_dating_request_when`), и по той же
-- причине: там он появился, когда таблица доросла до полутора миллионов строк.
--
-- ⚠⚠ ПОЧЕМУ ЖУРНАЛ ВООБЩЕ РАСТЁТ. 112 тысяч строк в сутки, из них 92% — уровень
-- `ok`, и почти всё это «Инвайт ушёл: …»: каждая отправка пишет строку. Журнал —
-- прямое следствие объёма инвайтов, и с ростом отправок он растёт линейно. За
-- месяц выходило около 3,3 миллиона строк, за год — под 3 ГБ.
--
-- Идемпотентность: проверка `information_schema` перед `ALTER`.

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`statistics`
              WHERE `table_schema` = DATABASE() AND `table_name` = 'log'
                AND `index_name` = 'idx_log_when');

SET @sql = IF(@есть = 0,
    'ALTER TABLE `log` ADD KEY `idx_log_when` (`created_at`)',
    'SELECT "индекс уже есть" AS этап');

PREPARE шаг FROM @sql;
EXECUTE шаг;
DEALLOCATE PREPARE шаг;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`statistics`
         WHERE `table_schema` = DATABASE() AND `table_name` = 'log'
           AND `index_name` = 'idx_log_when') AS индекс_есть,
       (SELECT COUNT(*) FROM `log`) AS строк_сейчас;
