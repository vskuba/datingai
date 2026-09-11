-- Инвайты: первая реплика в чат тому, с кем разговора не было (2026-09-10)
--
-- ЗАЧЕМ. У панели есть письма — и есть чат. Письмо человек открывает в почте
-- сайта; инвайт всплывает у него в окне чата одной репликой. Это разные жанры и
-- разный повод: письмо продолжает знакомство, инвайт его начинает.
--
-- ⚠ ПОЧЕМУ СВОЯ ТАБЛИЦА, А НЕ `dating_mailing` СО ВТОРЫМ ЖАНРОМ. У письма есть
-- шаблон (`mail_id`), и на нём держится уникальный ключ пула: «одна анкета не
-- пришлёт одному человеку один и тот же шаблон дважды». У инвайта шаблона нет
-- вовсе — текст сочиняет агент под конкретного человека, — и правило другое:
-- **одна анкета зовёт одного человека один раз, точка**. Ключ, включающий
-- шаблон, такого запрета выразить не может, а `mail_id NULL` сделал бы его
-- неработающим (в MySQL NULL не равен NULL — на этом уже спотыкался журнал
-- рассылки).
--
-- ⚠ `status` ТОТ ЖЕ, ЧТО У ПИСЕМ, И ЭТО НАМЕРЕННО. Отправка меняет одно поле, а
-- не переносит запись: пул и журнал ушедшего смотрятся одним запросом. Тот же
-- довод, по которому пул писем не стал второй таблицей.
--
-- ⚠ `name` ХРАНИМ РЯДОМ С `uid`. Имя приходит из снимка ленты онлайна, а тот
-- живёт неделю и обновляется; карточка `dating_companion` есть не у всех. Без
-- имени в строке пул через месяц читался бы как список номеров — а имя ещё и
-- стоит внутри самого текста, и расхождение между ними было бы видно человеку.
--
-- ⚠ РАСПИСАНИЕ И ВЫКЛЮЧАТЕЛИ — КОЛОНКАМИ САЙТА, как у рассылки. Смены правят
-- лентой на странице, а лента шлёт весь набор целиком; ключа в JSON-настройках
-- она не знает. Выключателей два, и оба по умолчанию сняты:
--
--   `invite_schedule_on` — «генерация инвайтов»: круг набирает пул по сменам;
--   `invite_send_on`     — «отправка инвайтов»: разрешает кнопку на карточке.
--
-- Разводить их обязательно. Инвайт уходит живому человеку в чат, и «расписание
-- включено» не значит «разрешаю писать»: человек включает смены, чтобы к утру был
-- готов пул, который он прочтёт. Тот же довод и та же пара, что у писем
-- (`mailing_schedule_on` против `mailing_send_on`).
--
-- ⚠ ВТОРОЙ ВЫКЛЮЧАТЕЛЬ ЗАПИРАЕТ КНОПКУ, А НЕ КРУГ. Кругового отправителя у
-- инвайтов нет: отправляет человек кнопкой на карточке. Флаг всё равно нужен —
-- он отвечает на вопрос «можно ли сейчас писать наружу с этого сайта», и запертая
-- кнопка честнее кнопки, которая пишет живому человеку по неосторожному щелчку.
--
-- Идемпотентность: таблица и колонки заводятся по условию отсутствия.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'dating_invite') AS таблица_есть,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
           AND COLUMN_NAME = 'invite_schedule') AS расписание_есть;

-- ------------------------------------------------- Пул инвайтов
CREATE TABLE IF NOT EXISTS `dating_invite` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `site_id` INT NOT NULL COMMENT 'Сайт-закладка: по нему отбирает круг и страница',
    `profile_id` INT NOT NULL COMMENT 'От какой девушки уйдёт реплика',
    `uid` VARCHAR(32) NOT NULL COMMENT 'Номер собеседника на сайте (AccountId)',
    `name` VARCHAR(190) NOT NULL DEFAULT ''
        COMMENT 'Как его зовут. ⚠ Хранится: имя стоит внутри текста, и расхождение видно человеку',
    `photo` VARCHAR(500) NOT NULL DEFAULT ''
        COMMENT 'Лицо для карточки пула. Из снимка ленты: та живёт неделю',
    `body` TEXT NOT NULL COMMENT 'Готовая реплика. Правят её на странице перед отправкой',
    `status` ENUM('draft', 'sent', 'failed') NOT NULL DEFAULT 'draft'
        COMMENT 'draft — лежит в пуле, sent — ушло, failed — не ушло',
    `error` VARCHAR(500) NOT NULL DEFAULT ''
        COMMENT 'Почему не ушло: причина словами, для страницы',
    `sent_at` DATETIME NULL DEFAULT NULL COMMENT 'Когда ушло. NULL — ещё в пуле',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    -- ⚠ **Одна анкета зовёт одного человека один раз.** Ключ в базе, а не проверка
    -- в коде: пул набирает круг воркера, и проверка между чтением и записью — это
    -- гонка. Другая анкета того же человека позвать может — это её знакомство.
    UNIQUE KEY `uq_dating_invite` (`profile_id`, `uid`),
    -- Отбор страницы: «покажи пул этого сайта, свежее сверху» на каждое открытие
    -- подвкладки. Порядок в индексе — от избирательного: сайтов десятки,
    -- состояний три.
    INDEX `idx_dating_invite_site` (`site_id`, `status`, `created_at`),
    -- ⚠ Внешний ключ на анкету с каскадом: удалили учётку — её неотправленные
    -- приглашения уходят вместе с ней. Отправленные тоже: инвайт без автора это
    -- строка, по которой не ответить «от кого он был».
    CONSTRAINT `fk_dating_invite_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------- Расписание инвайтов у сайта
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'invite_schedule') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `invite_schedule` JSON NULL DEFAULT NULL
            COMMENT ''Смены суток, в которые круг набирает пул инвайтов''',
    'SELECT ''колонка invite_schedule уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Выключатель генерации
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'invite_schedule_on') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `invite_schedule_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Исполнять расписание инвайтов: круг набирает пул сам''
            AFTER `invite_schedule`',
    'SELECT ''колонка invite_schedule_on уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Выключатель отправки
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'invite_send_on') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `invite_send_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Разрешить отправку инвайтов: без него кнопка на карточке заперта''
            AFTER `invite_schedule_on`',
    'SELECT ''колонка invite_send_on уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
--
-- ⚠ Пул пуст, и это правильно: наполняет его круг по сменам, а смены задаёт
-- человек. Заливать сюда при накате нечего.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_invite`) AS инвайтов,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
           AND COLUMN_NAME LIKE 'invite_%') AS колонок_сайта;
