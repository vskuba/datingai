-- Реплика агента в переписке — своим признаком: кто сказал, агент или человек (2026-11-20)
--
-- ЗАЧЕМ. В окне разговора наши реплики выглядели одинаково, кто бы их ни
-- написал: агент (ответ, инициатива, инвайт), оператор кнопкой «Отправить» в
-- панели или живая девушка прямо в панели агентства. Анкету ведут параллельно
-- человек и агент (Luxee, 19.11.2026), и по виду ленты нельзя было понять,
-- чьи слова сбили разговор. По `source` это не различить: кнопка оператора
-- шлёт той же дорогой, что агент.
--
-- ЧТО. `message.by_agent` — 1 у реплики, которую сочинил агент. Ставят его
-- дороги агента при записи (`send_message(by_agent=True)`, перенос инвайтов в
-- историю); у реплики из панели и у кнопки оператора — 0.
--
-- ⚠ Прошлое помечается по пулам: в `chat_reply` и `chat_initiative` лежат
-- тексты, которые агент отправил (`status = 'sent'`), инвайты узнаются по
-- `source = 'invite'`. Строка из панели (`panel`) не метится никогда — её
-- агентская копия, если была, лежит рядом своей строкой и метится сама.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'message'
           AND `COLUMN_NAME` = 'by_agent') AS столбец;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'message'
                AND `COLUMN_NAME` = 'by_agent');

SET @sql = IF(@есть = 0,
    'ALTER TABLE `message` ADD COLUMN `by_agent` TINYINT NOT NULL DEFAULT 0 COMMENT ''1 — реплику сочинил агент, 0 — человек или входящая''',
    'SELECT 1');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE `message` SET `by_agent` = 1
 WHERE `direction` = 'out' AND `source` = 'invite' AND `by_agent` = 0;

UPDATE `message` m
  JOIN `chat_reply` r ON r.`profile_id` = m.`profile_id` AND r.`uid` = m.`sender_uid`
                     AND r.`body` = m.`text` AND r.`status` = 'sent'
   SET m.`by_agent` = 1
 WHERE m.`direction` = 'out' AND m.`source` <> 'panel' AND m.`by_agent` = 0;

UPDATE `message` m
  JOIN `chat_initiative` i ON i.`profile_id` = m.`profile_id` AND i.`uid` = m.`sender_uid`
                          AND i.`body` = m.`text` AND i.`status` = 'sent'
   SET m.`by_agent` = 1
 WHERE m.`direction` = 'out' AND m.`source` <> 'panel' AND m.`by_agent` = 0;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `message` WHERE `by_agent` = 1) AS агента,
       (SELECT COUNT(*) FROM `message` WHERE `direction` = 'out' AND `by_agent` = 0) AS исходящих_человека;
