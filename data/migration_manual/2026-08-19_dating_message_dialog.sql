-- Индекс диалога: dating_message (girl_id, sender_uid, id) — выпуск 2026-08-19
--
-- Зачем вручную: `migrations/0001.sql` — схема для чистой установки, и yoyo
-- применяет её один раз. На живой базе (локальной и прод) индекса от правки файла
-- не появится, а карточка анкеты его уже ждёт: без него оба её запроса
-- вырождаются в перебор.
--
-- ЧТО МЕНЯЕТСЯ: один индекс в `dating_message`. Данные не трогаются вовсе.
--
-- ЗАЧЕМ ИМЕННО ОН. Карточка собеседниц делает два запроса. Первый группирует
-- переписку по собеседнице (кто, сколько реплик, какая последняя) — по индексу
-- `(girl_id, id)` собеседница не префикс, и база строит временную таблицу на все
-- реплики анкеты. Второй читает последние десять реплик одного диалога — обратным
-- проходом по первичному ключу с отбрасыванием чужих строк. Замер на анкете с 400
-- диалогами и 22 тысячами реплик: 183 мс на группировку и 5.9 мс на каждый из
-- пятидесяти диалогов (4206 строк прочитано ради десяти). С индексом — 12 мс и
-- 0.07 мс.
--
-- ЦЕНА: индекс на трёх коротких полях; на 25 тысячах реплик это меньше мегабайта.
-- Запись в `dating_message` идёт по одной строке на сообщение, лишний индекс на
-- такой нагрузке незаметен.
--
-- Идемпотентность: индекс добавляется по сверке с `information_schema`. Файл можно
-- гонять сколько угодно раз.
--
-- ПЕРЕД ПРОГОНОМ: дамп базы (`.claude/docs/migration.md`, раздел 3). `ALTER TABLE`
-- на живой таблице переписи данных не делает, но времени на большой таблице
-- требует — гнать лучше не в час работы операторов.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_message'
           AND INDEX_NAME = 'idx_dating_message_dialog') AS индекс_есть,
       (SELECT COUNT(*) FROM dating_message) AS всего_реплик;

-- ------------------------------------------- 1. Индекс
SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_message'
        AND INDEX_NAME = 'idx_dating_message_dialog') = 0,
    'ALTER TABLE `dating_message`
       ADD INDEX `idx_dating_message_dialog` (`girl_id`, `sender_uid`, `id`)',
    'SELECT ''idx_dating_message_dialog уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, INDEX_NAME AS индекс, SEQ_IN_INDEX AS место, COLUMN_NAME AS поле
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_message'
   AND INDEX_NAME = 'idx_dating_message_dialog'
 ORDER BY SEQ_IN_INDEX;
