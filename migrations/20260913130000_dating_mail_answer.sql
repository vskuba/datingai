-- Ответ сайта хранится и у писем, а не только у инвайтов (2026-09-13)
--
-- ПОЧЕМУ. У инвайтов колонка `answer` появилась по живой беде: панель числила
-- реплики ушедшими, а под учёткой собеседника в чате было пусто — и отличить
-- «сайт принял» от «человек получил» оказалось нечем. Помог ответ сайта, как он
-- есть.
--
-- У писем ровно тот же вопрос и тот же разрыв: мы пишем «доставлено», а сайт
-- говорил нам `{"Status":"OK"}` — или что-то иное, чего мы не сохранили. В
-- «Доставке» у фаворитов при этом уже есть куда его положить: подсказка строки.
--
-- ⚠ ТРЁМ ПУЛАМ СРАЗУ, а не одному. Отправитель у них один
-- (`dating_mail_send_one` с именем таблицы), и колонка в одной из трёх означала
-- бы ветку «а этому пулу не пишем» в общем коде — то есть место, где однажды
-- разойдутся.
--
-- ⚠ 500 ЗНАКОВ, как у `error` и у `dating_invite.answer`. Ответ бывает страницей
-- целиком, а вопрос к нему один: что сказал сайт. Начало отвечает, хвост — это
-- вес таблицы на каждое письмо.
--
-- Идемпотентность: каждая колонка заводится по условию отсутствия.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'answer'
           AND TABLE_NAME IN ('dating_mailing', 'dating_mail_reply',
                              'dating_favorite_mail')) AS колонок_есть;

-- ------------------------------------------- Рассылка
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mailing'
        AND COLUMN_NAME = 'answer') = 0,
    'ALTER TABLE `dating_mailing`
        ADD COLUMN `answer` VARCHAR(500) NOT NULL DEFAULT ''''
            COMMENT ''Что ответил сайт на отправку — как есть''
            AFTER `error`',
    'SELECT ''dating_mailing.answer уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Ответы на письма
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mail_reply'
        AND COLUMN_NAME = 'answer') = 0,
    'ALTER TABLE `dating_mail_reply`
        ADD COLUMN `answer` VARCHAR(500) NOT NULL DEFAULT ''''
            COMMENT ''Что ответил сайт на отправку — как есть''
            AFTER `error`',
    'SELECT ''dating_mail_reply.answer уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Письма избранным
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_favorite_mail'
        AND COLUMN_NAME = 'answer') = 0,
    'ALTER TABLE `dating_favorite_mail`
        ADD COLUMN `answer` VARCHAR(500) NOT NULL DEFAULT ''''
            COMMENT ''Что ответил сайт на отправку — как есть''
            AFTER `error`',
    'SELECT ''dating_favorite_mail.answer уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'answer'
           AND TABLE_NAME IN ('dating_mailing', 'dating_mail_reply',
                              'dating_favorite_mail')) AS колонок_есть;
