-- Контекст намерения: с чем его исполнять (2026-10-29)
--
-- ЗАЧЕМ. Намерение — метка словом (`she_promised_send_photo`), и она отвечает
-- «что решили», но не «с чем это делать». Исполнителю письма нужен текст
-- письма и приметы снимка, звонку — время, покупке — список. Пока этого не
-- было, исполнитель добывал бы контекст сам — то есть заново, по пересказу
-- вместо первоисточника, и уже без разговора под рукой.
--
-- Теперь контекст собирает тот же прогон, что нашёл намерение: подпоток
-- `intention_context` у соседа, пока история разговора ещё в руках.
--
-- ⚠⚠ ПОЧЕМУ JSON, А НЕ КОЛОНКИ. Намерения разные по природе, и набор полей у
-- каждого свой: письму нужен `letter` и `tags`, звонку — `at`, покупке —
-- `items`. Под каждое заводить колонки значило бы миграцию на каждое новое
-- намерение, то есть выкладку ради того, что задаётся в workflow. Та же
-- причина, по которой JSON выбран у `companion.params` (см. миграцию
-- 20261028120000).
--
-- ⚠ Цена размена названа: опечатка в имени поля отсюда не видна и всплывёт у
-- того, кто контекст читает. Поэтому читающий обязан смотреть на своё поле и
-- не падать, не найдя его.
--
-- ⚠ Колонка nullable и без умолчания: «контекст не собран» — законное
-- состояние (намерение помечено, а собирать было нечем), и отличать его от
-- пустого объекта никому не нужно. Умолчание `'{}'` сделало бы «не собран» и
-- «собран пустым» неразличимыми.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'chat_intention'
           AND `COLUMN_NAME` = 'context') AS колонка_уже,
       (SELECT COUNT(*) FROM `chat_intention`) AS строк,
       (SELECT COUNT(*) FROM `chat_intention` WHERE `status` = 'open') AS открытых;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'chat_intention'
                AND `COLUMN_NAME` = 'context');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `chat_intention`
     ADD COLUMN `context` JSON NULL
       COMMENT ''С чем исполнять намерение: текст письма, приметы снимка, время. NULL — не собран''
       AFTER `intention`',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'chat_intention'
           AND `COLUMN_NAME` = 'context') AS колонка_стала,
       (SELECT COUNT(*) FROM `chat_intention` WHERE `context` IS NOT NULL) AS с_контекстом;
