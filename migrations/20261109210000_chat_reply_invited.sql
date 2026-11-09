-- Ответ в чат умеет уйти приглашением: третье состояние строки (2026-11-09)
--
-- ЗАЧЕМ. У Step2Love написать можно только в **открытый** чат, а открывает его
-- принятый запрос. Собеседник, закрывший окно между своей репликой и нашим
-- ответом, оставляет пустой `chatObjectList`: отправка отвечает «у анкеты нет
-- открытого чата», черновик возвращается в пул, круг повторяет через минуту.
--
-- Замер прода 09.11.2026, пара Vironika → Lance: он дописал в 19:49:17, ответ
-- был готов в 19:50:32 — и уже некуда. Та же беда накануне стоила 249 попыток
-- к закрывшейся анкете.
--
-- Лечится тем же, чем у инициативы (`chat_initiative`): реплика уходит
-- **приглашением** с текстом ответа — собеседник читает его в списке запросов,
-- примет галочкой, и чат откроется.
--
-- ⚠⚠ ПОЧЕМУ ТРЕТЬЕ СОСТОЯНИЕ, А НЕ `sent`. «Ушло приглашением» и «доставлено»
-- — разные вещи: текст лежит у него в запросах и ждёт согласия, а сайт стирает
-- запрос по своему сроку (пять минут). Назови это доставкой — агент счёл бы
-- реплику сказанной и продолжил разговор от непрочитанного; назови черновиком
-- — круг разослал бы второе приглашение через минуту, третье через две. У пула
-- нет способа отличить «ещё не звали» от «позвали и ждём».
--
-- ⚠ Имя и смысл — те же, что у `chat_initiative.status`: состояние одно и то
-- же, и второе слово на него заставило бы читателя гадать, чем они разнятся.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `chat_reply`) AS строк,
       (SELECT COLUMN_TYPE FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'chat_reply'
           AND `COLUMN_NAME` = 'status') AS было;

-- ⚠ Расширение набора значений строки не трогает: MySQL дописывает вариант в
-- список, прежние остаются при своих номерах. Поэтому и проверка «уже есть» —
-- по тексту типа, а не по попытке с перехватом.
SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'chat_reply'
                AND `COLUMN_NAME` = 'status'
                AND `COLUMN_TYPE` LIKE '%invited%');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `chat_reply`
     MODIFY COLUMN `status` ENUM(''draft'', ''sent'', ''failed'', ''invited'')
       NOT NULL DEFAULT ''draft''
       COMMENT ''draft — ждёт отправки, sent — ушло, failed — не ушло, invited — ушло приглашением и ждёт согласия''',
  'SELECT ''состояние уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `chat_reply`) AS строк,
       (SELECT COUNT(*) FROM `chat_reply` WHERE `status` = 'draft') AS черновиков,
       (SELECT COLUMN_TYPE FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'chat_reply'
           AND `COLUMN_NAME` = 'status') AS стало;
