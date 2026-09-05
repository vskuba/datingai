-- Флаг рассылки писем (2026-09-05)
--
-- ЗАЧЕМ: в блоке «Настройки анкеты» появляется галочка «Письма рассылка», и её
-- состоянию негде храниться. На «Профилях» вкладка с таким именем уже есть, но
-- её тело — заглушка: механики рассылки в проекте нет.
--
-- ⚠ ИСПОЛНИТЕЛЯ У ФЛАГА НЕТ, И ЭТО НЕ НЕДОДЕЛКА. Круга рассылки не существует —
-- колонка хранит решение человека, чтобы оно не пропадало до того дня, когда
-- рассылку напишут. Никто её не читает: ни воркер, ни отправка.
--
-- ⚠ ПОЧЕМУ УМОЛЧАНИЕ 0, А НЕ 1 КАК У `auto_answer`. Показать включённой галочку у
-- функции, которой нет, значит соврать: человек решит, что рассылка идёт, и не
-- поймёт, почему писем никто не получил.
--
-- ⚠ ПОЧЕМУ БЕЗ ИНДЕКСА. Отбирать по колонке некому — круга нет. Индекс, который
-- никто не читает, платится на каждой записи. Заведёт его та задача, которая
-- напишет круг, — как `idx_dating_profile_sched` завела задача расписания.
--
-- Идемпотентность: `yoyo` её не требует (`.claude/docs/migration.md`, §2), но
-- соседние файлы её держат, и цена нулевая — зато файл можно прогнать руками по
-- прод-туннелю, не боясь второго накатывания.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME = 'mailing_on') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_profile`) AS анкет;

-- ------------------------------------------- Колонка
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'mailing_on') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `mailing_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Рассылка писем: флаг задела, исполнителя пока нет''
            AFTER `auto_answer_only`',
    'SELECT ''колонка mailing_on уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `mailing_on` = 1) AS рассылают,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `mailing_on` = 0) AS не_рассылают;
