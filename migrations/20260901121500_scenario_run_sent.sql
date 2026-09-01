-- Прогон записывается дважды: отправка и ответ (2026-09-01)
--
-- ЗАЧЕМ: до сих пор строка появлялась **только когда прогон кончился**. Пока он
-- шёл — а идёт он десятки секунд, а с очередью и минуты, — в журнале не было
-- ничего. Человек, открывший «Журналы» в этот момент, видел тишину и не мог
-- отличить «ничего не запускалось» от «запущено и ещё работает».
--
-- ⚠ ОСОБЕННО ВАЖНО ДЛЯ ЗАВИСШИХ. Прогон, который не вернётся никогда (сайт не
-- отвечает, вкладка умерла), не оставлял следа вовсе: записи «кончился» не будет,
-- а записи «начался» не было. Теперь такой виден сразу — строка «отправлен» без
-- пары.
--
-- ЧТО МЕНЯЕТСЯ: в `status` добавляется `sent` — «запрос ушёл, ответа ещё нет».
-- Прежние значения на месте, данные не переписываются.
--
-- Идемпотентность: сверка с `information_schema`. Файл можно гонять сколько
-- угодно раз.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COLUMN_TYPE FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_scenario_run'
           AND COLUMN_NAME = 'status') AS тип_status;

-- ------------------------------------------- Новое значение перечисления
--
-- `sent` первым в списке: перечисление хранится номером, и добавь мы его в конец —
-- порядок значений перестал бы совпадать с порядком событий. На сортировку это не
-- влияет (лента сортируется по времени), но `ORDER BY status` в отладочном запросе
-- выдавал бы бессмыслицу.
SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_scenario_run'
        AND COLUMN_NAME = 'status' AND COLUMN_TYPE LIKE '%sent%') = 0,
    'ALTER TABLE `dating_scenario_run`
       MODIFY `status` ENUM(''sent'',''ok'',''error'') NOT NULL DEFAULT ''sent''
       COMMENT ''sent — запрос ушёл, ответа ещё нет''',
    'SELECT ''sent уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COLUMN_TYPE FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_scenario_run'
           AND COLUMN_NAME = 'status') AS тип_status,
       (SELECT COUNT(*) FROM `dating_scenario_run`) AS строк;
