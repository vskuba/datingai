-- Откуда берётся переменная: анкета или адресат (2026-09-06)
--
-- ЗАЧЕМ: переменные писем задумывались как сведения об анкете — их заполняет
-- человек или агент, и значение одно на всех адресатов. Но письмо начинается с
-- обращения к получателю: `Hello, [[companion_name]]!` — а имени собеседника у
-- анкеты быть не может, оно своё на каждого.
--
-- Различает их `source`:
--
--   `profile`   — про анкету (`girl_name`): лежит в `dating_profile_var`,
--                 заполняется руками или агентом, одно значение на всех;
--   `companion` — про адресата (`companion_name`): в `dating_profile_var` его
--                 нет вовсе, подставляется при отправке из карточки собеседника.
--
-- ⚠ ПОЧЕМУ ЭТО НЕ ДВЕ ТАБЛИЦЫ. Для письма разницы нет: `[[girl_name]]` и
-- `[[companion_name]]` пишутся одинаково, проверяются одинаково и одинаково
-- ломают рассылку, когда их нечем подставить. Разный только источник значения — а
-- это одно поле, а не вторая сущность со своим списком, своей страницей и своей
-- ручкой.
--
-- ⚠ ПОЧЕМУ У `companion` НЕТ ЗНАЧЕНИЙ У АНКЕТЫ. Их некуда девать: значение
-- зависит от того, кому пишем, и появляется в момент отправки. Строка в
-- `dating_profile_var` для такой переменной означала бы «имя собеседника у
-- анкеты», то есть неправду. Поэтому вкладка «Переменные» у анкеты показывает
-- только `profile`, а `companion` — отдельной справкой.
--
-- ⚠ УМОЛЧАНИЕ `profile`, А НЕ `companion`. Переменных про анкету будет больше:
-- имя, возраст, город, чем занимается. Про адресата известно ровно то, что отдал
-- сайт, — имя, возраст, город, и на этом всё.
--
-- Заодно заводим две переменные, с которых начинают: `girl_name` и
-- `companion_name`. ⚠ Заводим их **на каждый сайт, у которого уже есть свои
-- переменные или шаблоны** — то есть там, где письмами уже занимались. Сайтам, где
-- писем нет вовсе, ничего не подкладываем: реестр ведёт человек, и заводить ему
-- переменные впрок значит решать за него.
--
-- Идемпотентность: колонка добавляется, если её нет; переменные — если их нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mail_var'
           AND COLUMN_NAME = 'source') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_mail_var`) AS переменных;

-- ------------------------------------------- Источник значения
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mail_var'
        AND COLUMN_NAME = 'source') = 0,
    'ALTER TABLE `dating_mail_var`
        ADD COLUMN `source` ENUM(''profile'', ''companion'') NOT NULL DEFAULT ''profile''
            COMMENT ''Откуда значение: profile — у анкеты, companion — у адресата''
            AFTER `name`',
    'SELECT ''колонка source уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Две переменные, с которых начинают
--
-- Только сайтам, где письмами уже занимались: есть шаблон или своя переменная.
-- `INSERT ... SELECT` с проверкой на существование — вставка идемпотентна и без
-- временных таблиц.
INSERT INTO `dating_mail_var` (`site_id`, `name`, `source`, `title`, `default_value`)
SELECT s.site_id, 'girl_name', 'profile', 'Имя девушки', ''
  FROM (SELECT DISTINCT site_id FROM `dating_mail`
        UNION SELECT DISTINCT site_id FROM `dating_mail_var`) s
 WHERE NOT EXISTS (SELECT 1 FROM `dating_mail_var` v
                    WHERE v.site_id = s.site_id AND v.name = 'girl_name');

INSERT INTO `dating_mail_var` (`site_id`, `name`, `source`, `title`, `default_value`)
SELECT s.site_id, 'companion_name', 'companion', 'Имя собеседника', ''
  FROM (SELECT DISTINCT site_id FROM `dating_mail`
        UNION SELECT DISTINCT site_id FROM `dating_mail_var`) s
 WHERE NOT EXISTS (SELECT 1 FROM `dating_mail_var` v
                    WHERE v.site_id = s.site_id AND v.name = 'companion_name');

-- ------------------------------------------- Уборка осиротевших значений
--
-- ⚠ Если `companion`-переменная уже успела обзавестись значением у анкеты (до
-- этой миграции все были `profile`), его надо убрать: оно означало бы «имя
-- собеседника у анкеты» — неправду, которая молча подставилась бы в письмо.
DELETE v FROM `dating_profile_var` v
  JOIN `dating_profile` p ON p.id = v.profile_id
  JOIN `dating_mail_var` m ON m.site_id = p.site_id AND m.name = v.name
 WHERE m.source = 'companion';

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_mail_var` WHERE `source` = 'profile') AS про_анкету,
       (SELECT COUNT(*) FROM `dating_mail_var` WHERE `source` = 'companion') AS про_адресата;
