-- Когда реплика легла к нам — своей колонкой, время сайта остаётся при своём (2026-11-24)
--
-- ЗАЧЕМ. `message.created_at` хранит **время сайта**: импорт из панели кладёт
-- туда `sent_at` из журнала агентства (`chat_panel.py`,
-- `COALESCE(NULLIF(sent_at, ''), NOW())`), а не минуту, когда реплику привезли.
-- Так и должно быть — ленту разговора читают по ней. Но быстрый ответ мерил
-- паузу этим же полем, и для него оно врёт: заход импорта привозит вчерашний
-- хвост, и реплика, написанная сутки назад, лежит у нас свежее трёх минут —
-- «собеседник молчит ровно столько, сколько надо», шаблон уходит в мёртвый
-- разговор.
--
-- ЧТО. `message.received_at` — когда реплика легла к нам. Пишет её сама база
-- (`DEFAULT CURRENT_TIMESTAMP`): ни одна вставка колонку не перечисляет, и
-- править их не нужно — новые строки помечаются сами. `NULL` — строка старше
-- этой миграции, о минуте её приёма судить нечем.
--
-- ⚠⚠ ДВА ШАГА, И ПОРЯДОК В НИХ ВАЖНЕЕ САМОГО ALTER. Заведи мы колонку сразу с
-- `DEFAULT CURRENT_TIMESTAMP` — и `ALTER` проставил бы **всем накопленным
-- строкам время миграции**: вчерашние молчалки стали бы «три минуты тишины», и
-- первый же круг после выкладки осыпал бы шаблонами каждую старую переписку.
-- Замерено на этой базе: `ADD COLUMN ... DEFAULT CURRENT_TIMESTAMP` по строкам
-- двухдневной давности поставил в новой колонке сегодняшние секунды.
--
-- Поэтому: (а) колонка — `NULL` и **без умолчания**, накопленное остаётся
-- `NULL` («не знаем»), отбор такие строки не берёт; (б) уже после — `SET
-- DEFAULT`, он меняет только описание таблицы, строки не трогает (проверено на
-- 100 000 строк: `NULL` остались `NULL`, `ALTER` — десятые доли секунды).
--
-- ⚠ ФОРМА СО СКОБКАМИ. `ALTER TABLE ... ALTER COLUMN c SET DEFAULT
-- CURRENT_TIMESTAMP` — синтаксическая ошибка (MySQL 8.0.46, 1064): здесь
-- грамматика ждёт литерал или **выражение в скобках**. Пишем `(CURRENT_TIMESTAMP)`;
-- в `SHOW CREATE TABLE` это `DEFAULT (now())`, поведение то же — секунды при
-- вставке.
--
-- ⚠ КОЛОНКУ НАКОПЛЕННОГО НЕ ПЕРЕОСМЫСЛЯЕМ. Проставить старым строкам их
-- `created_at` — была бы ровно та же беда: у привезённой вчера реплики он вчерашний.
-- `NULL` здесь читается честно: «было до нас».

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS stage,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'message'
           AND `COLUMN_NAME` = 'received_at') AS has_column,
       (SELECT COUNT(*) FROM `message`) AS rows_total;

-- 1. Колонка. ⚠ Без умолчания — иначе накопленное получит время миграции.
SET @has_column = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
                    WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'message'
                      AND `COLUMN_NAME` = 'received_at');

SET @sql = IF(@has_column = 0,
    'ALTER TABLE `message` ADD COLUMN `received_at` DATETIME NULL
       COMMENT ''когда реплика легла к нам (не время сайта); NULL — строка старше миграции''',
    'SELECT ''колонка уже есть — пропуск'' AS skip');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2. Умолчание для новых вставок. Только правка описания: строки остаются
--    такими, какими были. ⚠ Условие на `COLUMN_DEFAULT` — чтобы повторный заход
--    не делал второй `ALTER` и не переставлял умолчание, поставленное позже.
SET @has_default = (SELECT `COLUMN_DEFAULT` FROM `information_schema`.`COLUMNS`
                     WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'message'
                       AND `COLUMN_NAME` = 'received_at');

SET @sql = IF(@has_default IS NULL,
    'ALTER TABLE `message` ALTER COLUMN `received_at` SET DEFAULT (CURRENT_TIMESTAMP)',
    'SELECT ''умолчание уже стоит — пропуск'' AS skip');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
-- ⚠ `stamped_now` обязано быть нулём: любое другое число — значит шаг 1 ушёл с
--   умолчанием, и старый хвост выглядит тишиной.
SELECT 'ПОСЛЕ' AS stage,
       (SELECT `COLUMN_DEFAULT` FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'message'
           AND `COLUMN_NAME` = 'received_at') AS new_default,
       (SELECT COUNT(*) FROM `message`) AS rows_total,
       (SELECT COUNT(*) FROM `message` WHERE `received_at` IS NULL) AS rows_before,
       (SELECT COUNT(*) FROM `message`
         WHERE `received_at` >= NOW() - INTERVAL 10 MINUTE) AS stamped_now;
