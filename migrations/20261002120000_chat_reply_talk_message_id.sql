-- Черновик чата помнит номер обмена разговора (2026-10-02)
--
-- ## ⚠⚠ ЗАЧЕМ
--
-- Ответ в чате уходит сразу не всегда: собеседника нет в сети — текст ложится
-- черновиком и уходит следующим кругом. Номер обмена живёт в памяти вызова и до
-- следующего круга не доживает, а значит ушедшая реплика остаётся у соседа
-- неотмеченной — и агент не видит собственных сказанных слов.
--
-- ## ⚠⚠ Почему не `message_id`
--
-- Это имя в `chat_reply` **занято и значит другое**: номер реплики в нашей
-- таблице `message`, на которую отвечаем. На нём стоит `UNIQUE`-ключ, которым
-- даром получаются протухание черновика и защита от дублей круга. Второе
-- значение у того же имени развело бы два смысла в одном столбце.
--
-- `talk_message_id` — то же имя, под которым номер живёт переменной у соседа
-- (`talk_message_id` в workflow `dating_talk`).
--
-- ## ⚠ `NULL` значит «не знаем»
--
-- Строка могла родиться до этой правки либо агент промолчал. Отправка на таком
-- не спотыкается — просто не зовёт отметку.
--
-- Идемпотентность: колонка заводится по условию.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_reply'
           AND COLUMN_NAME = 'talk_message_id') AS колонка_есть;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_reply'
        AND COLUMN_NAME = 'talk_message_id') = 0,
    'ALTER TABLE `chat_reply`
        ADD COLUMN `talk_message_id` INT NULL DEFAULT NULL
            COMMENT ''Номер обмена разговора у соседа; им помечаем доставку''',
    'SELECT ''колонка уже есть'' AS пропуск');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_reply'
           AND COLUMN_NAME = 'talk_message_id') AS колонка_есть;
