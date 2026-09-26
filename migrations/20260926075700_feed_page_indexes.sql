-- Индексы страниц ленты: request (ok, site_id, created_at), scenario_run (site_id, created_at)
--
-- ЧТО МЕНЯЕТСЯ: два индекса под страницы лент («Ошибки» на «Журналах» и общая
-- лента). Данные не трогаются вовсе.
--
-- ЗАЧЕМ. Лента отказов считает каждого поставщика отказов своим запросом и берёт
-- верхние строки тоже из каждой половины (log.py, `feed_page`). Счёт половин и
-- выборка верхних строк без этих индексов вырождались в перебор всей таблицы:
-- походы наружу читаются только неудачные (906 строк из 145 513 — а перебор
-- доставал все), у прогонов отбор шёл по сайту поверх любого порядка.
--
-- ЗАМЕР (запрос страницы ленты, 145 тысяч строк походов): счёт половины без
-- индекса — 145 тысяч прочитанных строк, ~100 мс; с индексом — 906 записей
-- счёта. Разметка правки видна на вкладке «Ошибки»: 0.3 с → доли.
--
-- ЦЕНА: два узких вторичных индекса; запись в таблицы идёт по одной строке на
-- событие, на такой нагрузке правка индекса незаметна.
--
-- Идемпотентность: индексы добавляются по сверке с `information_schema`.
-- Файл можно гонять сколько угодно раз.
--
-- ⚠ ГНАТЬ РУКАМИ — только с `--default-character-set=utf8mb4`: клиент MySQL
-- иначе разбирает русские заголовки этого файла как испорченные байты. Через
-- yoyo на старте сервера этой грабли нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'request'
           AND INDEX_NAME = 'idx_dating_request_outcome') AS просит_исход_есть,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'scenario_run'
           AND INDEX_NAME = 'idx_dsr_site_when') AS прогоны_когда_есть;

-- ------------------------------------------- 1. Неудача по сайту: счёт и верх ленты отказов
SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'request'
        AND INDEX_NAME = 'idx_dating_request_outcome') = 0,
    'ALTER TABLE `request`
       ADD INDEX `idx_dating_request_outcome` (`ok`, `site_id`, `created_at`)',
    'SELECT ''idx_dating_request_outcome уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

-- ------------------------------------------- 2. Прогон по сайту: время сверху
SET @sql := IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'scenario_run'
        AND INDEX_NAME = 'idx_dsr_site_when') = 0,
    'ALTER TABLE `scenario_run`
       ADD INDEX `idx_dsr_site_when` (`site_id`, `created_at`)',
    'SELECT ''idx_dsr_site_when уже на месте'' AS шаг');
PREPARE это FROM @sql; EXECUTE это; DEALLOCATE PREPARE это;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, TABLE_NAME AS таблица, INDEX_NAME AS индекс,
       SEQ_IN_INDEX AS место, COLUMN_NAME AS поле
  FROM information_schema.STATISTICS
 WHERE TABLE_SCHEMA = DATABASE()
   AND INDEX_NAME IN ('idx_dating_request_outcome', 'idx_dsr_site_when')
 ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;
