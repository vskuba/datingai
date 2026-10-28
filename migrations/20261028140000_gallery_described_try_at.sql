-- Когда снимок последний раз пробовали разобрать (2026-10-28)
--
-- ЗАЧЕМ. Очередь разбора вставала намертво. Отбор шёл
-- `ORDER BY described_at IS NOT NULL, id`, а неудача `described_at` не ставит —
-- и пачка каждый заход брала **одни и те же** первые строки по номеру. Пока
-- причина неудачи жила (у соседа зрение ходило на мёртвый адрес), очередь
-- повторяла их вечно.
--
-- Измерено на проде 28.10.2026: 123 плитки, разобрана 1, у 5 ошибка, и эти
-- пять повторялись час за часом — 80 прогонов подряд. Остальные 117 снимков не
-- пробовали **ни разу**.
--
-- ⚠⚠ ПОЧЕМУ НЕ ХВАТИЛО ПОРЯДКА. Первая мысль — «строки с ошибкой в конец»
-- (`ORDER BY described_error <> ''`). Это лечит ровно до того мгновения, пока
-- неудача не случится у всех: тогда ключ перестаёт различать строки, и порядок
-- снова упирается в `id` — то есть в те же первые пять. Нужна не метка «была
-- ошибка», а **время попытки**: по нему очередь проворачивается.
--
-- ⚠⚠ ПОЧЕМУ НЕ `updated_at`. Он есть и обновляется сам
-- (`ON UPDATE CURRENT_TIMESTAMP`), но для этого строка должна **измениться**.
-- Неудача пишет ту же самую причину той же строке — MySQL видит, что значения
-- совпали, и не трогает ни строку, ни отметку. Очередь так и стояла бы, а
-- выглядело бы это как «починили, но не работает». Отдельная колонка
-- проставляется явно и от совпадения значений не зависит.
--
-- ⚠ NULL значит «не пробовали ни разу», и такие строки идут **первыми**: в
-- MySQL `ORDER BY` по возрастанию ставит NULL в начало, и это ровно то, что
-- нужно — свежий снимок важнее повтора неудачи.
--
-- ⚠ Колонка не `NOT NULL DEFAULT CURRENT_TIMESTAMP`: заведи её такой, все
-- накопленные строки получили бы время миграции, то есть «мы их только что
-- пробовали», и свежие снимки ушли бы в конец очереди. Та же грабля, что уже
-- стоила неверных дат в «Доставке» (`api.md`, §3.7.5).

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
           AND `COLUMN_NAME` = 'described_try_at') AS колонка_уже,
       (SELECT COUNT(*) FROM `gallery_file`) AS строк,
       (SELECT COUNT(*) FROM `gallery_file` WHERE `described_error` <> '') AS с_ошибкой;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
                AND `COLUMN_NAME` = 'described_try_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `gallery_file`
     ADD COLUMN `described_try_at` datetime NULL
       COMMENT ''Когда последний раз пробовали разобрать. NULL — ни разу; по нему проворачивается очередь''
       AFTER `described_error`',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
           AND `COLUMN_NAME` = 'described_try_at') AS колонка_стала,
       (SELECT COUNT(*) FROM `gallery_file` WHERE `described_try_at` IS NOT NULL) AS с_попыткой;
