-- Переменные писем: реестр по сайту и значения у анкет (2026-09-06)
--
-- ЗАЧЕМ: шаблон письма подставляет переменные, но какие именно — до сих пор
-- решал код (жёсткий список `his_name`, `her_name`, …). Это неверно: какие
-- сведения об анкете нужны письму, знает человек, ведущий сайт, а не код.
--
-- Теперь переменные **заводит человек** — на вкладке «Переменные» страницы
-- «Письма». Заведённая переменная появляется у каждой анкеты сайта пустой, и
-- дальше её наполняют: сейчас руками, потом агентом у соседа, которому отдадут
-- сведения об анкете и попросят вернуть значения.
--
-- Две таблицы, потому что вещи разные:
--
--   `dating_mail_var`    — какие переменные бывают на этом сайте (реестр);
--   `dating_profile_var` — чему они равны у конкретной анкеты (значения).
--
-- ⚠ ПОЧЕМУ ЗНАЧЕНИЕ У АНКЕТЫ, А НЕ У ПАРЫ «АНКЕТА + СОБЕСЕДНИК». Переменные
-- описывают саму анкету: имя, фамилия, что о ней известно. Одно и то же значение,
-- кому бы она ни писала. Держи мы их на пару, агента пришлось бы звать на каждого
-- адресата — сотни вызовов на анкету вместо одного.
--
-- ⚠ ПОЧЕМУ РЕЕСТР ПО САЙТУ. Шаблоны писем уже по сайту (`dating_mail.site_id`), и
-- переменные — их словарь: письмо ссылается на имена из него. Общий реестр на
-- панель означал бы, что переменная, заведённая под один сайт, требует значения у
-- анкет всех остальных.
--
-- ⚠ ПОЧЕМУ ЗНАЧЕНИЯ СТРОКАМИ, А НЕ JSON-КОЛОНКОЙ В `dating_profile`. Их
-- спрашивают по имени («какие анкеты не имеют `firstName`»), проверяют перед
-- каждой отправкой и наполняют по одной. JSON пришлось бы разбирать целиком ради
-- одного ключа, а отбор «у кого пусто» стал бы перебором в Python. Это ровно
-- обратный случай расписанию (`dating_profile.schedule`), которое читают и
-- заменяют целиком.
--
-- ⚠ ПОЧЕМУ `name` ДУБЛИРУЕТСЯ В ЗНАЧЕНИЯХ, А НЕ ССЫЛАЕТСЯ НА `dating_mail_var.id`.
-- По имени и идёт вся работа: письмо пишет `{{firstName}}`, агент возвращает
-- `firstName`, страница показывает `firstName`. Ссылка номером означала бы join
-- на каждую проверку письма ради строки, которая и так под рукой. Целостность при
-- этом держит не внешний ключ, а уборка при удалении переменной — она в коде
-- ручки, и это записано в `api.md`.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mail_var') AS реестр_есть,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'dating_profile_var') AS значения_есть,
       (SELECT COUNT(*) FROM `dating_mail`) AS шаблонов;

-- ------------------------------------------- Реестр переменных сайта
CREATE TABLE IF NOT EXISTS `dating_mail_var` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `site_id` INT NOT NULL COMMENT 'Сайт-закладка у соседа: реестр у сайтов свой',
    `name` VARCHAR(64) NOT NULL
        COMMENT 'Имя переменной латиницей: firstName, lastName',
    `title` VARCHAR(190) NOT NULL DEFAULT ''
        COMMENT 'Подпись для человека и подсказка агенту, что искать',
    `default_value` VARCHAR(500) NOT NULL DEFAULT ''
        COMMENT 'Чем подставлять, если у анкеты своё значение пусто',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    -- ⚠ Имя уникально **внутри сайта**: письмо ссылается на переменную по имени, и
    -- две одноимённые сделали бы ссылку неоднозначной.
    UNIQUE KEY `uq_dating_mail_var_name` (`site_id`, `name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------- Значения у анкеты
CREATE TABLE IF NOT EXISTS `dating_profile_var` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `profile_id` INT NOT NULL COMMENT 'Чья переменная',
    `name` VARCHAR(64) NOT NULL COMMENT 'Имя из реестра сайта этой анкеты',
    `value` VARCHAR(500) NOT NULL DEFAULT ''
        COMMENT 'Значение. Пусто — переменная не заполнена, письмо с ней не уйдёт',
    `filled_by` ENUM('hand', 'agent') NOT NULL DEFAULT 'hand'
        COMMENT 'Кто заполнил: человек или агент у соседа',
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    -- Одно значение на пару «анкета + имя». Он же делает запись идемпотентной:
    -- агент вернёт значение второй раз — обновится, а не задвоится.
    UNIQUE KEY `uq_dating_profile_var` (`profile_id`, `name`),
    CONSTRAINT `fk_dpv_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------- После
--
-- ⚠ Умолчаний не заводим. Прежние семь встроенных (`his_name`, `her_name`, …)
-- отсюда не переносятся: они были решением кода, а не человека, и половина из них
-- (поля адресата) при значении «у анкеты» не выражается вовсе. Сайт заводит свои
-- переменные сам — до этого письма с переменными просто не сохраняются.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_mail_var`) AS переменных,
       (SELECT COUNT(*) FROM `dating_profile_var`) AS значений;
