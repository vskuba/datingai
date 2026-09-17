-- Инвайты уходят из переписки: приглашение — не разговор (2026-09-17)
--
-- ЧТО НЕ ТАК БЫЛО. Отправка инвайта шла общей дверью (`dating_send_message`), а
-- та пишет ушедшее в переписку. Выходило, что приглашение попадало в диалог
-- сразу, ещё до того как человек хоть слово ответил.
--
-- Цена видна на живых данных: из 15 832 исходящих строк **15 765 были
-- инвайтами**. Настоящих реплик — 67. Одному человеку пишут в разные дни, до
-- двадцати приглашений на пару, и диалог выглядел монологом из двадцати наших
-- фраз без единой его — то есть портил ровно то, ради чего инвайты и хранят:
-- понять, **на что** человек ответил.
--
-- КАК СТАЛО. Инвайт живёт в своей таблице (`invite`), а в переписку последние из
-- приглашений переносятся в момент **первого ответа** — столько, сколько просит
-- настройка сайта `invite_keep` (`dating_chat._invites_to_history`).
--
-- ⚠ ПЕРЕНОС СТАВИТ ВРЕМЯ ОТПРАВКИ, А НЕ ТЕКУЩЕЕ. Иначе приглашение оказалось бы
-- новее ответа на него, и диалог читался бы задом наперёд: разбор истории идёт
-- по времени, а не по `id`.
--
-- ЧТО ДЕЛАЕТ ЭТА МИГРАЦИЯ.
--
--   1. Учит `source` значению `invite` — им помечается перенесённое. Без этого
--      вставка падает: столбец `enum`, а не строка.
--   2. Убирает из переписки прежние инвайты — те исходящие, чей текст совпадает
--      с телом приглашения этой же пары.
--
-- ⚠ КРИТЕРИЙ УДАЛЕНИЯ ТРОЙНОЙ: пара (анкета + собеседник), совпадение текста и
-- `source = 'http'`. Одного совпадения текста мало — девушка могла отправить ту
-- же фразу руками; `http` же означает «ушло ручкой сайта», то есть тем самым
-- путём, которым уходят инвайты. Проверено на проде: критерий отбирает 15 765
-- строк из 15 832, и остаток (67) — настоящие реплики.
--
-- ⚠ УДАЛЯЕМ ТОЛЬКО ИСХОДЯЩИЕ. Входящие — слова живых людей, и их не касается
-- ничто: `direction = 'in'` в условии нет вовсе.
--
-- ⚠ ПЕРЕНОС ЗАНОВО НЕ ДЕЛАЕМ. Пары, где человек ответил, восстановят историю
-- сами — при следующем его сообщении. Разовая запись в переписку 1 678 пар
-- поставила бы приглашения задним числом тем, кто, может, уже месяц не пишет.
--
-- ⚠ БЭКАП ДО НАКАТА ОБЯЗАТЕЛЕН (`deploy.md`, §4.5): миграция удаляет строки, и
-- вернуть их можно только из дампа.
--
-- Идемпотентность: `enum` расширяется по условию, удаление повторно находит ноль
-- строк.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM message WHERE direction = 'out') AS исходящих,
       (SELECT COUNT(*) FROM message WHERE direction = 'in') AS входящих,
       (SELECT COLUMN_TYPE LIKE '%invite%' FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message'
           AND COLUMN_NAME = 'source') AS source_знает_invite;

-- ------------------------------------------- 1. `source` учится `invite`
SET @sql = IF((SELECT COLUMN_TYPE NOT LIKE '%invite%' FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message'
                  AND COLUMN_NAME = 'source') = 1,
              'ALTER TABLE `message` MODIFY COLUMN `source`'
              ' ENUM(''socket'', ''http'', ''panel'', ''scenario'', ''invite'')'
              ' COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT ''socket'''
              ' COMMENT ''Каким путём реплика попала в переписку; invite — перенос приглашения''',
              'SELECT ''source уже знает invite''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ------------------------------------------- 2. Прежние инвайты — из переписки
--
-- Через производную таблицу: MySQL не даёт удалять из таблицы, к которой в том
-- же запросе есть подзапрос напрямую.
DELETE FROM message
 WHERE id IN (SELECT id FROM (
         SELECT m.id
           FROM message m
           JOIN invite i ON i.profile_id = m.profile_id
                        AND i.uid = m.sender_uid
                        AND i.body = m.text
          WHERE m.direction = 'out' AND m.source = 'http') t);

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM message WHERE direction = 'out') AS исходящих,
       (SELECT COUNT(*) FROM message WHERE direction = 'in') AS входящих,
       (SELECT COLUMN_TYPE LIKE '%invite%' FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message'
           AND COLUMN_NAME = 'source') AS source_знает_invite;
