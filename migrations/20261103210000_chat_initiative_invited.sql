-- Состояние «ушло приглашением, доставки ещё нет» (2026-11-03)
--
-- ЗАЧЕМ. У сайта, где разговор открывается приглашением (Step2Love), между
-- «отправили» и «прочитал» стоит третье состояние: текст лежит у собеседника
-- в списке запросов и ждёт, пока он нажмёт галочку. Не нажал — сайт стирает
-- запрос по своему сроку, и реплику не читал никто.
--
-- Прежде такая строка закрывалась как `sent`, то есть «ушло» выдавалось за
-- «дошло». Замер 03.11.2026: инициатива Anastasiya → Semen ушла приглашением
-- в 17:01:28; к 17:30 запроса не было ни в списке анкеты, ни в панели, чат не
-- открылся — а строка стояла `sent`, и обмен #4730 у соседа значился
-- доставленным. Пара при этом считалась «позванной»: круг к ней не
-- возвращался, то есть одно непринятое приглашение гасило разговор навсегда.
--
-- ⚠⚠ ПОЧЕМУ ОТДЕЛЬНОЕ СОСТОЯНИЕ, А НЕ `draft`. Оставь строку черновиком — и
-- круг разошлёт второе приглашение через минуту, третье через две: у пула нет
-- способа отличить «ещё не звали» от «позвали и ждём ответа». А `failed` врёт
-- в другую сторону: ничего не сломалось, приглашение принято сайтом.
--
-- ⚠ Накопленному ничего не меняем: `sent` у прежних строк остаётся как есть.
-- История задним числом не восстанавливается — чинится дорога вперёд. Одна
-- ложная строка (#3077) убрана руками вместе с обменом соседа.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `chat_initiative`) AS строк,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `status` = 'sent') AS ушедших,
       (SELECT `COLUMN_TYPE` FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'chat_initiative'
           AND `COLUMN_NAME` = 'status') AS было;

-- Само состояние. ⚠ `MODIFY` переписывает набор целиком, поэтому прежние три
-- значения перечислены полностью: забудь одно — и строки с ним станут
-- пустыми, молча.
SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'chat_initiative'
                AND `COLUMN_NAME` = 'status'
                AND `COLUMN_TYPE` LIKE '%invited%');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `chat_initiative`
     MODIFY COLUMN `status`
       ENUM(''draft'', ''sent'', ''failed'', ''invited'')
       NOT NULL DEFAULT ''draft''
       COMMENT ''draft — черновик, sent — доставлено, failed — отказ, invited — ушло приглашением и ждёт согласия''',
  'SELECT ''состояние уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `chat_initiative`) AS строк,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `status` = 'sent') AS ушедших,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `status` = 'invited') AS ждущих,
       (SELECT `COLUMN_TYPE` FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'chat_initiative'
           AND `COLUMN_NAME` = 'status') AS стало;
