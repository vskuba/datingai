-- Индекс по паре «девушка — собеседник» у инвайтов (2026-09-17)
--
-- ЗАЧЕМ. Появилась чистка истории пары: после каждой вставки инвайта лишние
-- строки этой пары удаляются, остаётся `invite_keep` последних (по умолчанию
-- два). Оба запроса чистки отбирают по `profile_id` + `uid`, а такого индекса у
-- таблицы не было: есть `uq_dating_invite_draft` (profile_id, draft_uid) — но
-- `draft_uid` виртуальный и равен `uid` **только у черновика**, то есть для
-- отправленных он `NULL` и отбор по нему не работает.
--
-- Цена без индекса: полный скан на каждую вставку. На проде в таблице уже 16
-- тысяч строк, а пул набирается десятками карточек за заход — это десятки
-- сканов подряд.
--
-- ⚠ ПОРЯДОК СТОЛБЦОВ ИМЕННО ТАКОЙ. `profile_id` первым: он же стоит первым в
-- `WHERE` обоих запросов, и по нему одному индекс тоже пригодится — «все
-- инвайты этой девушки» спрашивают и страница, и круг. Обратный порядок сузил
-- бы пользу до одной чистки.
--
-- ⚠ `id` ТРЕТЬИМ СТОЛБЦОМ НЕ ДЕЛАЕМ. В InnoDB вторичный индекс и так несёт
-- первичный ключ, а `ORDER BY id DESC LIMIT 1 OFFSET N` читает его из листа
-- индекса. Явное добавление ничего не даёт, кроме лишних байт на строку.
--
-- Идемпотентность: индекс заводится по условию отсутствия.

SELECT 'ДО' AS этап, COUNT(*) AS есть_индекс
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
   AND INDEX_NAME = 'idx_dating_invite_pair';

SET @sql = IF((SELECT COUNT(*) FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
                  AND INDEX_NAME = 'idx_dating_invite_pair') = 0,
              'ALTER TABLE `dating_invite`'
              ' ADD INDEX `idx_dating_invite_pair` (`profile_id`, `uid`)',
              'SELECT ''индекс idx_dating_invite_pair уже есть''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SELECT 'ПОСЛЕ' AS этап, COUNT(*) AS есть_индекс
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
   AND INDEX_NAME = 'idx_dating_invite_pair';
