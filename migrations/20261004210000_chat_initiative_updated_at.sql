-- Дата у неудачной карточки инициативы: столбец `updated_at` (2026-10-04)
--
-- ЗАЧЕМ. У красной строки в пуле инициативы нет времени исхода. `sent_at` у
-- неудачной карточки пуст — отметку снимает сама неудача, — и показ
-- (`_sentWhen`: `sent_at → updated_at → created_at`) доходит до `created_at`,
-- то есть до времени **сочинения**. Между сочинением и отказом проходят часы:
-- карточку готовит круг набора, уносит её круг отправки по своему сроку.
--
-- Человек видит в красной строке время, когда текст написали, и читает его как
-- время, когда писать не вышло. Ровно та же беда, что уже была у писем
-- избранным и у ответов на письма; там она чинилась двумя миграциями подряд,
-- потому что первая сделала столбец `NOT NULL DEFAULT CURRENT_TIMESTAMP`.
--
-- ⚠⚠ ПОЭТОМУ `NULL DEFAULT NULL`, А НЕ `NOT NULL DEFAULT CURRENT_TIMESTAMP`.
-- `ALTER` со вторым проставил бы **всем накопленным строкам время самой
-- миграции**: письмо, не ушедшее сутки назад, показалось бы сегодняшним. На
-- проде это стоило ложного обвинения собственному коду — красная строка со
-- свежей датой у запрещённой пары читается одним способом: «код опять пишет
-- тому, кому нельзя», — и часа на разбор по журналу запросов
-- (`20260922140000_favorite_mail_updated_at_fix.sql`).
--
-- Неизвестно — значит `NULL`, и показ сам возьмёт `created_at`. Записать туда
-- `created_at` прямо значило бы соврать в самом столбце: он про **исход**, а не
-- про сочинение, и следующий запрос, читающий его без `COALESCE`, получил бы
-- ответ на другой вопрос.
--
-- ⚠ ЧЕГО СТОЛБЕЦ НЕ ЗНАЧИТ. `ON UPDATE` срабатывает на любую правку строки,
-- то есть строго это «когда карточку последний раз меняли». Так же устроено у
-- инвайтов, писем избранным и ответов. Разводить «исход» и «правку» вторым
-- столбцом значило бы отвечать на вопрос, которого никто не задаёт.
--
-- ⚠ У `mailing` такого столбца нет и не заводится: там неудачная рассылка
-- показывает время сочинения намеренно (`CLAUDE.md`, «Дата у неудачного
-- письма»). Пулов с датой исхода теперь четыре из пяти, и пятый — исключение с
-- доводом, а не забытый.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
           AND COLUMN_NAME = 'updated_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `sent_at` IS NULL)
         AS строк_без_даты,
       (SELECT COUNT(*) FROM `chat_initiative`) AS всего;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
               AND COLUMN_NAME = 'updated_at');

SET @sql = IF(@add,
    "ALTER TABLE `chat_initiative`
        ADD COLUMN `updated_at` DATETIME NULL DEFAULT NULL
            ON UPDATE CURRENT_TIMESTAMP
            COMMENT 'Когда записали исход; NULL — неизвестно, показ возьмёт created_at'
            AFTER `created_at`",
    'SELECT ''столбец уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
           AND COLUMN_NAME = 'updated_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `updated_at` IS NOT NULL)
         AS строк_с_датой;
