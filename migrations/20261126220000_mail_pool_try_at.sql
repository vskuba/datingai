-- Когда письмо из пула пробовали отправить в последний раз (2026-11-26)
--
-- ЗАЧЕМ. Очередь отправки (`send_round._oldest`) брала первой строку с меньшим
-- `id`. Карточка, которая не уходит по причине, не сведённой в паузу (отказ
-- сети, неизвестное «подожди»), оставалась первой **навсегда** и получала
-- попытку каждые несколько секунд, не пуская соседей.
--
-- Замер прода 26.11.2026: `favorite_mail` #2276 — повтор ровно каждые 6 секунд,
-- очередь из остальных писем не двигалась.
--
-- Тот же приём уже чинил ту же беду у реплик: `invite.tried_at`
-- (миграция 20261104090000, замер 04.11.2026 — 106 попыток к одному человеку).
--
-- ⚠ Колонка `NULL` и **без умолчания**: пустая значит «не пробовали», и такие
-- строки идут первыми — как было до правки. Заведи её `NOT NULL DEFAULT
-- CURRENT_TIMESTAMP`, и `ALTER` проставил бы всем накопленным время миграции,
-- то есть «все пробованы только что», — очередь перемешалась бы разом. Та же
-- грабля, что уже стоила ошибки у `mail_reply.updated_at` (CLAUDE.md,
-- «Дата у неудачного письма»).
--
-- ⚠ Правку ставит одна дверь — `mail_send_fail` (круг и все кнопки через неё);
-- письмо, ушедшее, её не видит вовсе.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `mailing` WHERE `status` = 'draft') AS рассылка,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'mailing'
           AND `COLUMN_NAME` = 'try_at') AS колонка_уже;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'mailing'
                AND `COLUMN_NAME` = 'try_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mailing`
     ADD COLUMN `try_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда письмо в последний раз пробовали отправить. NULL — не пробовали''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'mail_reply'
                AND `COLUMN_NAME` = 'try_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mail_reply`
     ADD COLUMN `try_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда письмо в последний раз пробовали отправить. NULL — не пробовали''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'favorite_mail'
                AND `COLUMN_NAME` = 'try_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `favorite_mail`
     ADD COLUMN `try_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда письмо в последний раз пробовали отправить. NULL — не пробовали''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'intention_mail'
                AND `COLUMN_NAME` = 'try_at');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `intention_mail`
     ADD COLUMN `try_at` DATETIME NULL DEFAULT NULL
       COMMENT ''Когда письмо в последний раз пробовали отправить. NULL — не пробовали''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` IN ('mailing', 'mail_reply', 'favorite_mail',
                                'intention_mail')
           AND `COLUMN_NAME` = 'try_at') AS колонок,
       (SELECT COUNT(*) FROM `favorite_mail` WHERE `status` = 'draft'
          AND `try_at` IS NOT NULL) AS уже_пробованы;
