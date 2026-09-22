-- Время исхода у письма избранному (2026-09-22)
--
-- ЗАЧЕМ. На «Фаворитах → Доставка» у неудачных писем нет даты, а у ушедших есть.
-- Причина в том, что столбец там один — `sent_at`, «когда ушло», — и у неудачи
-- его нет и быть не может: отметку снимает сама неудача.
--
-- Человек видит две строки подряд, одинаковые по виду, и у красной пусто там, где
-- у зелёной время. Читается это как «данных не показали», хотя показывать нечего:
-- времени исхода мы не храним вовсе.
--
-- ⚠ ТАК УЖЕ СДЕЛАНО У ИНВАЙТОВ. У `invite` столбец `updated_at` есть, и журнал
-- доставки берёт `COALESCE(sent_at, updated_at)` — у ушедшей минуту отправки, у
-- неудачной минуту, когда исход записали. Здесь заводится то же самое, и нарочно
-- тем же именем: две таблицы с разными именами под одно понятие расходятся в
-- первом же запросе, который пишут по памяти.
--
-- ⚠⚠ ПОЧЕМУ НЕ ПРОСТО ПОКАЗАТЬ `created_at`. Он говорит, когда черновик СОЧИНИЛИ,
-- а не когда отправка сорвалась. Между ними бывают часы: пул набирается кругом,
-- уносит письма другой круг по своему расписанию. Показав время сочинения под
-- видом времени ошибки, мы отвечали бы не на тот вопрос — и молча.
--
-- ⚠ `ON UPDATE CURRENT_TIMESTAMP` ставит отметку сам, при любой правке строки.
-- Отдельной записи в коде не нужно: её забыли бы в одном из путей отправки, и
-- дата пропадала бы через раз — хуже, чем не быть вовсе.
--
-- ⚠ У накопленных строк отметка станет временем миграции, и это честнее нуля:
-- «когда исход записали» для них неизвестно, а `NULL` показу не мешает —
-- `COALESCE` возьмёт `created_at`.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
           AND COLUMN_NAME = 'updated_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `favorite_mail` WHERE `sent_at` IS NULL) AS строк_без_даты;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
               AND COLUMN_NAME = 'updated_at');

SET @sql = IF(@add,
    "ALTER TABLE `favorite_mail`
        ADD COLUMN `updated_at` DATETIME NOT NULL
            DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            COMMENT 'Когда записали исход: у неудачи это и есть время ошибки'
            AFTER `created_at`",
    'SELECT ''столбец уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite_mail'
           AND COLUMN_NAME = 'updated_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `favorite_mail`
         WHERE COALESCE(`sent_at`, `updated_at`, `created_at`) IS NOT NULL)
         AS строк_с_датой,
       (SELECT COUNT(*) FROM `favorite_mail`) AS всего;
