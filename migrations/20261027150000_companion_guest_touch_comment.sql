-- Комментарий у `companion_guest.created_at`: подтверждение, а не первая встреча
--
-- ЗАЧЕМ. В тот же день, что и таблица, дверь записи (`guests_seen`) научилась
-- двигать `created_at` при повторном sighting: гость, которого сегодня снова
-- видно на странице, подтверждён сегодня — и по этой дате лента отвечает
-- возрастом. А комментарий у колонки остался «когда гость увиден впервые» —
-- и следующий, кто откроет `SHOW CREATE TABLE`, решит, что дверь гонит строки
-- ошибкой. Коммент в базе читают руками чаще, чем докстринг двери.
--
-- ⚠ КОММЕНТАРИЙ — НЕ СХЕМА. Тип, default и `CURRENT_TIMESTAMP` ровно те же,
-- что в `20261027120000_companion_guest.sql`; правится одно слово COMMENT.
-- Отдельная миграция, а не правка прежней: та уже применена и не редактируется.
--
-- Идемпотентность: `MODIFY COLUMN` повторяем сам по себе.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап, COLUMN_COMMENT AS комментарий
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'companion_guest' AND COLUMN_NAME = 'created_at';

-- ---------------------------------------------------------------- Правка
ALTER TABLE `companion_guest`
    MODIFY COLUMN `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        COMMENT 'Когда гость в последний раз подтверждён съёмом; первая встреча — время вставки';

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, COLUMN_COMMENT AS комментарий
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE()
   AND TABLE_NAME = 'companion_guest' AND COLUMN_NAME = 'created_at';
