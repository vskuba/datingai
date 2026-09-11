-- Переменные писем: одно правило имени на все (2026-09-12)
--
-- ЧТО БЫЛО. В реестре сайта жила помесь: `companion_name`, `girl_name`,
-- `question_to_companion` — snake_case, и рядом `companion_firstName`,
-- `companion_lastName` — camelCase. Человек видит их в одном списке и не может
-- объяснить разницу, потому что её нет: обе про поле карточки.
--
-- ПОЧЕМУ ТАК ВЫШЛО, и это не небрежность. Подстановка искала поле карточки по
-- **последнему куску** имени после подчёркивания: у `companion_firstName` хвост
-- `firstName` → поле `firstname`, и оно находилось. А `companion_first_name` дал бы
-- хвост `name` — то есть в обращение молча уехало бы **полное имя**. camelCase был
-- обходом ограничения разбора, а не выбором стиля.
--
-- ЧТО ИЗМЕНИЛОСЬ. Разбор научился пробовать хвосты от длинного к короткому и
-- склеивать подчёркивания (`dating_mailing_person_key`): `companion_first_name` →
-- `first_name` → `firstname`. Обход больше не нужен, и имена приводятся к общему
-- виду.
--
-- ⚠ СТАРЫЕ ИМЕНА ПРОДОЛЖАЮТ РАБОТАТЬ. Разбор понимает обе формы, и это не
-- обратная совместимость ради неё самой: имена переменных пишет человек — руками в
-- реестре и в текстах шаблонов, — и сайт, заведённый в другой инсталляции, может
-- держать camelCase до сих пор. Отказать ему значило бы остановить его рассылку
-- накатом.
--
-- ⚠ ТЕКСТЫ ШАБЛОНОВ НЕ ТРОГАЕМ. Проверено перед накатом: ни один шаблон не
-- ссылается на `companion_firstName` — все девять зовут `companion_name`. Правь мы
-- тексты вслепую, письмо адресату с именем одним словом («Kevin») перестало бы
-- уходить вовсе: `firstname` у него пуст намеренно, а нехватка переменной
-- останавливает отправку.
--
-- Идемпотентность: переименование идёт по условию существования старого имени и
-- отсутствия нового.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       SUM(`name` = 'companion_firstName') AS camel_first,
       SUM(`name` = 'companion_lastName') AS camel_last,
       SUM(`name` IN ('companion_first_name', 'companion_last_name')) AS snake_уже
  FROM `dating_mail_var`;

-- ------------------------------------------- Имя без фамилии
UPDATE `dating_mail_var` v
   SET v.`name` = 'companion_first_name'
 WHERE v.`name` = 'companion_firstName'
   AND NOT EXISTS (SELECT 1 FROM (SELECT `site_id` FROM `dating_mail_var`
                                   WHERE `name` = 'companion_first_name') AS уже
                    WHERE уже.`site_id` = v.`site_id`);

-- ------------------------------------------- Фамилия
UPDATE `dating_mail_var` v
   SET v.`name` = 'companion_last_name'
 WHERE v.`name` = 'companion_lastName'
   AND NOT EXISTS (SELECT 1 FROM (SELECT `site_id` FROM `dating_mail_var`
                                   WHERE `name` = 'companion_last_name') AS уже
                    WHERE уже.`site_id` = v.`site_id`);

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       SUM(`name` = 'companion_firstName') AS camel_остался,
       SUM(`name` = 'companion_lastName') AS camel_остался_фамилия,
       SUM(`name` IN ('companion_first_name', 'companion_last_name')) AS snake_стало
  FROM `dating_mail_var`;
