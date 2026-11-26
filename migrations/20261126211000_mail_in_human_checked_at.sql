-- Письмо проверено на человеческий ответ: отметка на входящем (2026-11-26)
--
-- ЗАЧЕМ. «Ответили ли на письме» (`is_replied`) — признак сайта, и говорит он
-- только «кто-то ответил», а не «кто и что». Пока круг ответов видел такое
-- письмо неотвеченным у **нас**, он сочинял на него второй ответ — и мужчина
-- получал реплику живого человека и реплику агента подряд (решение владельца
-- 26.11.2026). Проверка живёт в `src/dating/mail_human.py`: страница истории
-- панели забирается один раз, письма человека ложатся в `mail_human`.
--
-- ЧТО. `mail_in.human_checked_at` — когда для этого письма страницу истории
-- уже забирали. NULL — не забирали, и пара стоит в отборе следующего захода;
-- отметка ставится **после успешного** забора, сорванная попытка остаётся
-- неотмеченной и повторится.
--
-- ⚠ ОТМЕТКА НАД ПИСЬМОМ, а не у пары: отбор круга идёт по письму
-- (`is_replied = 1` и нет нашего ответа), и решать «проверяли ли уже» по строке,
-- ради которой всё и затевалось, — значит не завести второго правила про
-- «когда проверять».
--
-- ⚠ NULL у всех накопленных строк — законное «не проверяли»: забор ходит к
-- панели только там, где письмо помечено отвеченным без нашего ответа, а таких
-- меньшинство.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
           AND `COLUMN_NAME` = 'human_checked_at') AS колонка_уже,
       (SELECT COUNT(*) FROM `mail_in` `m`
          LEFT JOIN `mail_reply` `r` ON `r`.`mail_in_id` = `m`.`id`
         WHERE `m`.`is_replied` = 1 AND `r`.`id` IS NULL) AS отвеченных_не_нами;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
                AND `COLUMN_NAME` = 'human_checked_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mail_in`
     ADD COLUMN `human_checked_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда ради письма забирали страницу истории панели. NULL — не забирали''
       AFTER `reply_making_at`',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
           AND `COLUMN_NAME` = 'human_checked_at') AS колонка_стала;
