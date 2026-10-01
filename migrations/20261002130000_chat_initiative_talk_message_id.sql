-- Карточка инициативы помнит номер обмена разговора (2026-10-02)
--
-- ## ⚠⚠ ЗАЧЕМ
--
-- Инициатива ходит тем же workflow `dating_talk`, что и чат: сосед пишет обмен в
-- `companion_session` **внутри прогона**, до того как мы постучимся к сайту
-- знакомств. Карточку при этом сочиняет один круг (`initiative.py`), а уносит
-- отправкой другой, и позже — когда собеседник окажется в сети
-- (`initiative_round._send`). Номер обмена, полученный при сочинении, до
-- отправки не доживал бы без своего места в строке пула — а значит ушедшая
-- инициатива осталась бы у соседа неотмеченной, и агент не увидел бы собственных
-- сказанных слов.
--
-- ⚠ Тот же приём, что у `chat_reply` (задача 7а, `talk_message_id`) — и то же
-- имя: это намеренно, одно имя в двух пулах чата важнее экономии букв.
--
-- ## ⚠ Почему не `message_id`
--
-- В `chat_initiative` имя `message_id` не занято (в отличие от `chat_reply`,
-- где оно значит повод — нашу реплику, на которую отвечают), но заводить его
-- тут не стали: столбец значит номер обмена **у соседа**, а свободное имя не
-- делает смысл очевиднее. Разные пулы чата с одинаковым именем читаются проще,
-- чем один пул с двумя похожими, но разными «message_id».
--
-- ## ⚠ `NULL` значит «не знаем»
--
-- Строка могла родиться до этой правки либо агент промолчал о номере. Отправка
-- на таком не спотыкается — просто не зовёт отметку доставки (ноль ушёл бы в
-- холостой ход `rhapsody_api_chat_delivered`, а не в никуда, но счёт лучше
-- держать явным).
--
-- Идемпотентность: колонка заводится по условию.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
           AND COLUMN_NAME = 'talk_message_id') AS колонка_есть;

SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
        AND COLUMN_NAME = 'talk_message_id') = 0,
    'ALTER TABLE `chat_initiative`
        ADD COLUMN `talk_message_id` INT NULL DEFAULT NULL
            COMMENT ''Номер обмена разговора у соседа; им помечаем доставку''',
    'SELECT ''колонка уже есть'' AS пропуск');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
           AND COLUMN_NAME = 'talk_message_id') AS колонка_есть;
