-- Письмо занято сочинением ответа: отметка на время прогона (2026-11-03)
--
-- ЗАЧЕМ. «Уже отвечали» до сих пор значило «в пуле есть строка» (`_to_answer`:
-- `LEFT JOIN mail_reply … AND r.id IS NULL`). Строка же появляется **после**
-- того, как агент ответил, — а он отвечает минутами. Всё это время письмо для
-- любого следующего захода выглядит нетронутым.
--
-- Поймано живьём 03.11.2026, пара Natalia → Kazimierz. Два обращения к соседу с
-- побайтово одинаковым телом: 12:58:09 и 13:00:23. Первое шло 240 секунд и
-- кончилось отказом по сроку (звавшего к тому времени уже не было — ни строки в
-- журнале запросов, ни строки доступа у соседа), второе уложилось в 121 и легло
-- в пул. Прогон первого при этом доработал до конца: 498 секунд модели оплачены
-- и выброшены.
--
-- ⚠⚠ ОТМЕТКА ВРЕМЕНЕМ, А НЕ ФЛАГОМ. Ровно тот случай, что и породил беду,
-- оставляет занятие висеть: процесс умер посреди вызова, снять флаг некому, и
-- письмо не ответится **никогда** — молча, как и всё в этой дороге. Время
-- протухает само: `reply_making_at` старше `MAIL_REPLY_CLAIM_SEC` отбор не
-- держит.
--
-- ⚠ Колонка у `mail_in`, а не новое состояние у `mail_reply`. Статус пула
-- читают восемь мест — журналы доставки, счётчики, круг отправки, страница, —
-- и новое значение перечисления просочилось бы в половину из них. Занятие же
-- про **письмо**, а не про ответ, и лежать ему рядом с `attachment_at`.
--
-- ⚠ NULL значит «не занято»; это состояние всех накопленных строк, и умолчание
-- выбрано им.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
           AND `COLUMN_NAME` = 'reply_making_at') AS колонка_уже,
       (SELECT COUNT(*) FROM `mail_in` `m`
          LEFT JOIN `mail_reply` `r` ON `r`.`mail_in_id` = `m`.`id`
         WHERE `r`.`id` IS NULL AND `m`.`body` <> '') AS писем_без_ответа;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
                AND `COLUMN_NAME` = 'reply_making_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mail_in`
     ADD COLUMN `reply_making_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда письмо взяли сочинять ответ. NULL — не занято; протухшее занятие отбор отпускает сам''
       AFTER `is_replied`',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
           AND `COLUMN_NAME` = 'reply_making_at') AS колонка_стала;
