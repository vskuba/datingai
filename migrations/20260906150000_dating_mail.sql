-- Шаблоны писем и журнал рассылки (2026-09-06)
--
-- ЗАЧЕМ: рассылка писем должна брать текст откуда-то и помнить, кому что уже
-- ушло. Первого не было вовсе, второго — тем более.
--
-- Две таблицы, потому что вещи разные и живут по-разному:
--
--   `dating_mail`    — шаблоны: текст с переменными, который правит человек;
--   `dating_mailing` — журнал: что и кому ушло, с уже подставленными значениями.
--
-- ⚠ ПОЧЕМУ ОТРЕНДЕРЕННЫЙ ТЕКСТ ХРАНИТСЯ ЦЕЛИКОМ, А НЕ ССЫЛКОЙ НА ШАБЛОН.
-- Шаблон правят: сегодня в нём одно, завтра другое. Журнал же отвечает на вопрос
-- «что человек получил» — и ответ на него не должен меняться задним числом от
-- правки шаблона. Плюс переменные: `{{her_name}}` в шаблоне ничего не говорит о
-- том, какое имя ушло на самом деле.
--
-- ⚠ ПОЧЕМУ УНИКАЛЬНЫЙ КЛЮЧ (profile_id, uid, mail_id). Это и есть «без
-- дубликатов»: одна анкета не пришлёт одному человеку один и тот же текст
-- дважды. Другой шаблон ему же — можно (это следующее письмо в переписке), тот же
-- шаблон от другой анкеты — тоже (это другая девушка).
--
-- Ключ в базе, а не проверкой в коде: рассылка пойдёт кругом воркера, и проверка
-- «а не слали ли уже» между чтением и записью — это гонка. Уникальный индекс
-- ловит её там, где она случается.
--
-- ⚠ ПОЧЕМУ `mail_id` — ВНЕШНИЙ КЛЮЧ С `SET NULL`, А НЕ `CASCADE`. Шаблон удалят,
-- а журнал должен остаться: он про то, что уже случилось, и удаление шаблона
-- прошлого не отменяет. Тот же приём и та же причина, что у `dating_scenario_run`.
--
-- ⚠ ...И ПОЧЕМУ ТОГДА `uid` ВАЖНЕЕ `mail_id` В КЛЮЧЕ. `SET NULL` делает
-- уникальный ключ бесполезным для строк с погибшим шаблоном: в MySQL NULL не
-- равен NULL, и две записи с `mail_id IS NULL` уникальность не нарушат. Это
-- осознанно: шаблона больше нет, и «то же самое письмо» стало неопределимым —
-- запрещать по нему нечего.
--
-- ⚠ ПОЧЕМУ `site_id` ЕСТЬ И В ЖУРНАЛЕ, ХОТЯ ВЫВОДИТСЯ ЧЕРЕЗ АНКЕТУ. Страница
-- отбирает журнал по сайту, как и всё в разделе; join к `dating_profile` ради
-- одного числа на каждой странице списка — лишняя работа там, где её видно.
-- Анкету при этом могут удалить, а строка журнала остаться — тогда сайт был бы
-- потерян вовсе.
--
-- ⚠ `site_id` БЕЗ ВНЕШНЕГО КЛЮЧА — общее правило проекта: сайты живут у соседа,
-- своей таблицы сайтов здесь нет (`database.md`).

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mail') AS шаблоны_есть,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mailing') AS журнал_есть;

-- ------------------------------------------- Шаблоны
CREATE TABLE IF NOT EXISTS `dating_mail` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `site_id` INT NOT NULL COMMENT 'Сайт-закладка у соседа: шаблоны у сайтов свои',
    `name` VARCHAR(190) NOT NULL COMMENT 'Название для списка в панели, не уходит на сайт',
    `body` TEXT NOT NULL COMMENT 'Текст письма с переменными вида {{her_name}}',
    `enabled` TINYINT(1) NOT NULL DEFAULT 1
        COMMENT 'Участвует ли в рассылке: выключенный остаётся, но не берётся',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    -- Отбор списка на странице: сайт, потом включённость. Порядок — от самой
    -- избирательной колонки.
    INDEX `idx_dating_mail_site` (`site_id`, `enabled`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------- Журнал рассылки
CREATE TABLE IF NOT EXISTS `dating_mailing` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `site_id` INT NOT NULL COMMENT 'Сайт: отбор журнала на странице идёт по нему',
    `profile_id` INT NOT NULL COMMENT 'От какой анкеты ушло письмо',
    `mail_id` INT NULL DEFAULT NULL
        COMMENT 'Каким шаблоном; NULL — шаблон с тех пор удалили',
    `uid` VARCHAR(32) NOT NULL COMMENT 'Кому: номер собеседника на сайте',
    `name` VARCHAR(190) NOT NULL DEFAULT ''
        COMMENT 'Имя адресата на момент отправки: в журнале номер ничего не говорит',
    `body` TEXT NOT NULL COMMENT 'Текст, который ушёл, — с уже подставленными значениями',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    -- ⚠ Тот самый запрет дубликатов. Он же ловит гонку круга рассылки: проверка
    -- «а не слали ли уже» между чтением и записью гарантий не даёт, индекс даёт.
    UNIQUE KEY `uq_dating_mailing_once` (`profile_id`, `uid`, `mail_id`),
    -- Лента журнала по сайту, свежее сверху.
    INDEX `idx_dating_mailing_site` (`site_id`, `created_at`),
    CONSTRAINT `fk_dmailing_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE,
    -- ⚠ `SET NULL`, а не `CASCADE`: шаблон удалят, а журнал останется — он про
    -- то, что уже случилось.
    CONSTRAINT `fk_dmailing_mail` FOREIGN KEY (`mail_id`)
        REFERENCES `dating_mail` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------- После
--
-- Умолчаний не заводим: шаблон письма — текст, который пишет человек под свой
-- сайт, и «письмо по умолчанию» ушло бы живым людям от нашего имени.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_mail`) AS шаблонов,
       (SELECT COUNT(*) FROM `dating_mailing`) AS отправок;
