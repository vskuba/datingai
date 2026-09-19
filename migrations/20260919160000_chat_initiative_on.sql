-- Выключатель круга инициативы (2026-09-19)
--
-- ЗАЧЕМ. Круг инициативы пишет живым людям **первым** — не в ответ, а после
-- молчания. Это самое заметное из всего, что панель делает наружу: ответ человек
-- ждёт, а неожиданное «как прошли выходные?» через час тишины он не ждал.
--
-- ⚠⚠ УМОЛЧАНИЕ — СНЯТО, и это не осторожность ради осторожности. Накат миграции,
-- сам разрешивший писать живым людям, был бы худшим из сюрпризов: круг пошёл бы
-- по всем затихшим парам сайта разом, а их на проде тысячи.
--
-- ⚠⚠ ФЛАГОВ ДВА, И ЭТО НЕ ИЗБЫТОЧНОСТЬ. `chat_initiative_on` разрешает
-- **набирать** пул, `chat_initiative_send_on` — **отправлять** из него. Ровно как
-- у инвайтов и у рассылки, и по той же причине: пул сперва читают глазами.
--
-- Инициатива этого требует сильнее прочего. Текст сочиняется под тишину, приёмом
-- из жребия — флирт, подколка, чёрный юмор, — и увидеть, что именно агент понял
-- под «подколи по-доброму», надо до того, как оно ушло живому человеку. Один флаг
-- означал бы «включаю набор и сразу верю результату».
--
-- Идемпотентность: проверка `information_schema` перед `ALTER`.

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`columns`
              WHERE `table_schema` = DATABASE() AND `table_name` = 'site'
                AND `column_name` = 'chat_initiative_on');

SET @sql = IF(@есть = 0,
    'ALTER TABLE `site`
       ADD COLUMN `chat_initiative_on` TINYINT(1) NOT NULL DEFAULT 0
           COMMENT "Набор пула инициативы: карточки сочиняются",
       ADD COLUMN `chat_initiative_send_on` TINYINT(1) NOT NULL DEFAULT 0
           COMMENT "Отправка инициативы: карточки уходят живым людям"',
    'SELECT "колонки уже есть" AS этап');

PREPARE шаг FROM @sql;
EXECUTE шаг;
DEALLOCATE PREPARE шаг;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`columns`
         WHERE `table_schema` = DATABASE() AND `table_name` = 'site'
           AND `column_name` LIKE 'chat_initiative%on') AS колонок,
       (SELECT COUNT(*) FROM `site` WHERE `chat_initiative_on` = 1) AS набор_включён,
       (SELECT COUNT(*) FROM `site` WHERE `chat_initiative_send_on` = 1) AS отправка_включена;
