-- Источник сообщения: добавляем `http` (2026-09-11)
--
-- ЗАЧЕМ. `dating_message.source` отвечает на вопрос «каким путём это ушло», и
-- нужен он ровно при разборе: сообщение не дошло — первым делом смотрят, чем его
-- отправляли. Значений было три (`socket`, `panel`, `scenario`), а путей отправки
-- в коде четыре: сокет, сценарий, HTTP-ручка сайта и импорт из панели.
--
-- Пока HTTP-путь никем не был настроен, расхождение не проявлялось. Сегодня
-- LadaDate переведён на него (`presence.send.transport: http`, `send_transport:
-- presence`), и **каждое исходящее сообщение стало писаться как `socket`** — у
-- сайта, у которого сокета нет вовсе.
--
-- ⚠ ЭТО РОВНО ТА ПУТАНИЦА, ОТ КОТОРОЙ СТОЛБЕЦ И ЗАВОДИЛСЯ. В `dating_send.py` у
-- `_message_save` записано: «у Ladadate сокета нет вовсе, и запись со `socket`
-- заставила бы искать поломку там, где её нет». Тогда это относилось к сценарию —
-- и ради этого появилось значение `scenario`. Теперь тот же довод про HTTP.
--
-- ⚠ ПОЧЕМУ НЕ СВЕСТИ К ДВУМ («сами» / «привезли»). Потому что вопрос при разборе
-- именно такой: чем шло. Сокет молчит иначе, чем отказывает ручка, а сценарий
-- падает на шаге вёрстки — три разных места, куда идти смотреть. Столбец, не
-- различающий их, отвечает «неизвестно».
--
-- Прежние строки не трогаем: они действительно ушли сокетом или сценарием, и
-- переписывать историю задним числом значило бы врать журналу.
--
-- Идемпотентность: значение добавляется, если его ещё нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COLUMN_TYPE FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_message'
           AND COLUMN_NAME = 'source') AS было,
       (SELECT COUNT(*) FROM `dating_message` WHERE `source` = 'socket') AS сокетом;

SET @sql = IF(
    (SELECT COLUMN_TYPE LIKE '%http%' FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_message'
        AND COLUMN_NAME = 'source') = 0,
    'ALTER TABLE `dating_message`
        MODIFY COLUMN `source` ENUM(''socket'', ''http'', ''panel'', ''scenario'')
            NOT NULL DEFAULT ''socket''
            COMMENT ''Каким путём ушло или пришло: кадром сокета, ручкой сайта, из панели агентства, сценарием''',
    'SELECT ''значение http уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COLUMN_TYPE FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_message'
           AND COLUMN_NAME = 'source') AS стало;
