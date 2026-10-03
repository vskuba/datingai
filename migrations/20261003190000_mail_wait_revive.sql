-- Письма, похороненные часовым пределом получателя (2026-10-03)
--
-- ЗАЧЕМ. Сайт отвечает на `check-send`:
--
--   403 {"Error":"The user's hourly incoming message limit has been exceeded",
--        "Reason":"ERROR_MESSAGE_USER_HOUR_IN_LIMIT"}
--
-- Это состояние ЕГО ящика, и через час оно проходит. Панель же понимала отказ как
-- вечный: причина ложилась в `error`, а строки с причиной круг отправки не берёт
-- НИКОГДА (`send_round._oldest`) — письмо оставалось лежать мёртвым.
--
-- ⚠⚠ И держало место в пуле. Замер прода 03.10.2026: 100 черновиков рассылки при
-- потолке 100, живых 17, остальные 83 — вот эти. Набор видел пул полным и не
-- готовил ничего, отправка точила остаток. Рассылка стояла сутки.
--
-- Код это больше не делает: отказ стал остудой (`src/dating/mail_wait.py`), письмо
-- остаётся чистым. Но накопленное он не разберёт — черновики так и лежат с
-- причиной, и круг к ним не вернётся. Их и оживляем.
--
-- ЧТО ДЕЛАЕМ. Стираем `error` у черновиков, чьей причиной был ВРЕМЕННЫЙ отказ:
-- часовой предел ящика и суточный предел писем новому человеку. Письмо целое,
-- текст на месте — круг унесёт его в свой срок, спросив сайт заново.
--
-- ⚠ ТОЛЬКО ВРЕМЕННЫЕ ПРИМЕТЫ И ТОЛЬКО ЧЕРНОВИКИ. Отказ по возрасту
-- (`ERROR_MESSAGE_AGE_CRITERIA`) вечен, и его письма похоронены правильно — их
-- подбирала своя миграция (`20260921200000_block_age_backfill.sql`). Сотри мы
-- причину и у них, круг пошёл бы писать тем, кто писем не получит, по письму в
-- минуту.
--
-- ⚠ ОТПРАВЛЕННОЕ НЕ ТРОГАЕМ ВОВСЕ: у ушедшего письма `error` пуст, а `sent` —
-- это факт, и переписывать его нечем.
--
-- Идемпотентность: условие `error LIKE` само себя гасит — стёртая причина второму
-- прогону не покажется.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `mailing`
         WHERE `status` = 'draft' AND `error` = '') AS живых_рассылки,
       (SELECT COUNT(*) FROM `mailing`
         WHERE `status` = 'draft'
           AND (`error` LIKE '%ERROR_MESSAGE_USER_HOUR_IN_LIMIT%'
                OR `error` LIKE '%ERROR_MESSAGE_NEW_USER_DAILY_LIMIT%'))
         AS оживим_рассылки,
       (SELECT COUNT(*) FROM `favorite_mail`
         WHERE `status` = 'draft'
           AND (`error` LIKE '%ERROR_MESSAGE_USER_HOUR_IN_LIMIT%'
                OR `error` LIKE '%ERROR_MESSAGE_NEW_USER_DAILY_LIMIT%'))
         AS оживим_избранным,
       (SELECT COUNT(*) FROM `mail_reply`
         WHERE `status` = 'draft'
           AND (`error` LIKE '%ERROR_MESSAGE_USER_HOUR_IN_LIMIT%'
                OR `error` LIKE '%ERROR_MESSAGE_NEW_USER_DAILY_LIMIT%'))
         AS оживим_ответов;

-- ── Оживляем черновики ──────────────────────────────────────────────────────
--
-- ⚠ Три пула порознь, а не одним запросом: имя таблицы в SQL не параметризуется,
-- и склеить их можно было бы только `UNION`-ом на чтение, а здесь запись.
UPDATE `mailing` SET `error` = ''
 WHERE `status` = 'draft'
   AND (`error` LIKE '%ERROR_MESSAGE_USER_HOUR_IN_LIMIT%'
        OR `error` LIKE '%ERROR_MESSAGE_NEW_USER_DAILY_LIMIT%');

UPDATE `favorite_mail` SET `error` = ''
 WHERE `status` = 'draft'
   AND (`error` LIKE '%ERROR_MESSAGE_USER_HOUR_IN_LIMIT%'
        OR `error` LIKE '%ERROR_MESSAGE_NEW_USER_DAILY_LIMIT%');

UPDATE `mail_reply` SET `error` = ''
 WHERE `status` = 'draft'
   AND (`error` LIKE '%ERROR_MESSAGE_USER_HOUR_IN_LIMIT%'
        OR `error` LIKE '%ERROR_MESSAGE_NEW_USER_DAILY_LIMIT%');

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `mailing`
         WHERE `status` = 'draft' AND `error` = '') AS живых_рассылки,
       (SELECT COUNT(*) FROM `mailing`
         WHERE `status` = 'draft' AND `error` <> '') AS осталось_с_причиной,
       (SELECT COUNT(*) FROM `favorite_mail`
         WHERE `status` = 'draft' AND `error` = '') AS живых_избранным,
       (SELECT COUNT(*) FROM `mail_reply`
         WHERE `status` = 'draft' AND `error` = '') AS живых_ответов;
