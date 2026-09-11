-- Переписка с избранными: письмо, возобновляющее затихший разговор (2026-09-11)
--
-- ЗАЧЕМ. Цепочка почты замкнулась на ответах: мы разослали, он ответил, мы
-- ответили — и ждём. Он не пишет. Разговор затихает, и сам собой не оживает: у
-- нас нет повода написать, потому что все наши поводы — реакция на его письмо.
--
-- Этот пул и есть тот повод. Письмо **пишем первыми** тем, кто уже отвечал (то
-- есть избранным), — не знакомство, а продолжение прерванного разговора.
--
-- ⚠ СВОЯ ТАБЛИЦА, А НЕ ТРЕТИЙ ЖАНР В `dating_mailing`. Там уникальный ключ
-- `(profile_id, uid, mail_id)`: «одна анкета не пришлёт одному человеку один и
-- тот же шаблон дважды». Здесь ровно наоборот — одному и тому же человеку мы
-- пишем **много раз**, потому и заведено: разговор возобновляют не однажды.
-- Уникального ключа на пару здесь нет и быть не может; частоту держат не ключи, а
-- две настройки (см. ниже).
--
-- ⚠ ЧАСТОТА — ПРО ЧЕЛОВЕКА, А НЕ ПРО САЙТ. Настройки сайта:
--
--   `favorite_mail_per_day` — сколько писем в сутки **одному человеку** (2);
--   `favorite_mail_gap`     — сколько ждать между письмами **ему же** (4 часа).
--
-- Сделай мы их про сайт, тринадцать анкет делили бы два письма на всех, и
-- занятие не работало бы вовсе. А для человека это и есть тот предел, за которым
-- письмо от девушки читается как автоответчик.
--
-- ⚠ СЧИТАЕТСЯ ВСЁ УШЕДШЕЕ ЕМУ, А НЕ ТОЛЬКО ЭТОТ ПУЛ. Письма считает получатель, а
-- не наши таблицы: рассылка, ответ и возобновление приходят ему в одну почту. Тот
-- же довод, по которому `send_max_day` общий на рассылку и ответы.
--
-- ⚠ ВЫКЛЮЧАТЕЛЕЙ ДВА, И ОБА СНЯТЫ ПО УМОЛЧАНИЮ:
--
--   `favorite_mail_make_on` — «генерация»: круг набирает пул сам;
--   `favorite_mail_send_on` — «отправка»: письма из пула уходят кругом отправки.
--
-- Разводить обязательно, и это то же решение, что у рассылки, ответов и инвайтов:
-- «готовим» не значит «шлём». Человек включает генерацию, чтобы прочитать, что
-- выйдет, — а не чтобы письма ушли, пока он читает.
--
-- ⚠ РАСПИСАНИЯ СВОЕГО НЕТ, И ЭТО НЕ ПРОПУСК. Набор ничего не отправляет, а
-- отправляет круг отправки — в часы `mailing_schedule`, общие на всю почту сайта.
-- Второе расписание отвечало бы на тот же вопрос вторым голосом.
--
-- Идемпотентность: таблица и колонки заводятся по условию отсутствия.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'dating_favorite_mail') AS таблица_есть,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
           AND COLUMN_NAME = 'favorite_mail_make_on') AS выключатель_есть;

-- ------------------------------------------------- Пул писем избранным
CREATE TABLE IF NOT EXISTS `dating_favorite_mail` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `site_id` INT NOT NULL COMMENT 'Сайт-закладка: по нему отбирают круг и страница',
    `profile_id` INT NOT NULL COMMENT 'От какой девушки уйдёт письмо',
    `uid` VARCHAR(32) NOT NULL COMMENT 'Кому: номер собеседника на сайте',
    `name` VARCHAR(190) NOT NULL DEFAULT ''
        COMMENT 'Имя адресата на момент письма: в пуле номер ничего не говорит',
    `photo` VARCHAR(500) NOT NULL DEFAULT ''
        COMMENT 'Лицо для карточки пула: список читают лицами',
    `body` TEXT NOT NULL COMMENT 'Готовый текст. Правят его на странице перед отправкой',
    `status` ENUM('draft', 'sent', 'failed') NOT NULL DEFAULT 'draft'
        COMMENT 'draft — лежит в пуле, sent — ушло, failed — не ушло',
    `error` VARCHAR(500) NOT NULL DEFAULT ''
        COMMENT 'Почему не ушло: причина словами, для страницы',
    `sent_at` DATETIME DEFAULT NULL COMMENT 'Когда ушло. NULL — ещё в пуле',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    -- ⚠ Уникального ключа на пару **нет намеренно**: одному человеку пишут много
    -- раз, в том и смысл занятия. Повторы держат настройки частоты, а не схема.
    KEY `idx_dating_favorite_mail_site` (`site_id`, `status`, `id`),
    -- По этому ключу считают «сколько ему уже ушло и когда последнее»: вопрос
    -- задаётся на каждого кандидата при каждом наборе пула.
    KEY `idx_dating_favorite_mail_who` (`profile_id`, `uid`, `sent_at`),
    CONSTRAINT `fk_dfav_mail_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci
  COMMENT = 'Пул писем избранным: возобновление затихшего разговора';

-- ------------------------------------------------- Выключатель «генерация»
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'favorite_mail_make_on') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `favorite_mail_make_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Набирать ли пул писем избранным: круг пишет сам''
            AFTER `invite_send_on`',
    'SELECT ''колонка favorite_mail_make_on уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------------- Выключатель «отправка»
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'favorite_mail_send_on') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `favorite_mail_send_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Отправлять ли письма избранным: уходят живым людям''
            AFTER `favorite_mail_make_on`',
    'SELECT ''колонка favorite_mail_send_on уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'dating_favorite_mail') AS таблица_есть,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
           AND COLUMN_NAME LIKE 'favorite_mail%') AS выключателей;
