-- Части имени собеседника: `companion_firstName` и `companion_lastName` (2026-09-11)
--
-- ЗАЧЕМ. Сайт отдаёт имя одной строкой: «Brian Ritter», «JULIO RODRIGO ZAPATA
-- OLIVA», «Kevin». Письмо, обращающееся «Здравствуйте, Brian Ritter», читается
-- как заполненная форма, а не как письмо человека: по имени и фамилии разом не
-- обращаются нигде.
--
-- Отсюда две переменные рядом с `companion_name`. Само `companion_name` остаётся
-- как есть — оно про полное имя, и шаблоны, которые им пользуются, не меняются.
--
-- ⚠ ЗНАЧЕНИЕ ВЫВОДИТСЯ, А НЕ ХРАНИТСЯ. Как и `companion_name`, эти две берутся из
-- карточки собеседника при отправке (`dating_mailing_person_fields`): первое слово
-- до пробела — имя, остальное — фамилия. Своего места в базе у них нет и не надо:
-- части имени это то же имя, посмотренное под другим углом, и отдельные колонки
-- разошлись бы с ним в первую же правку карточки.
--
-- ⚠ НЕТ ПРОБЕЛА — ОБЕ ПУСТЫ. «Kevin» и «Tall_Good_Man» это одно слово: делить
-- нечего. Соблазн отдать «Kevin» первым именем велик, но тогда пропадает разница
-- между «имя известно отдельно» и «у нас просто строка».
--
-- Что делать с пустотой, решает тот, кто пишет текст:
--
--   * промпт агента — через `%if%`: есть первое имя, обращаемся им, нет —
--     полным (`dating_invite_write` у соседа);
--   * шаблон письма — через `default_value` переменной, если человек его задаст.
--
-- ⚠ УМОЛЧАНИЙ НЕ СТАВИМ, и это то же решение, что у `companion_name`: пустое
-- умолчание значит «письмо не уйдёт, пока не заполнят». Умолчание вида «дорогой
-- друг» ушло бы живым людям вместо имени.
--
-- ⚠ ИМЕНА В camelCase — `firstName`, а не `first_name`. Подстановка ищет поле
-- карточки по хвосту после последнего подчёркивания и приводит его к нижнему
-- регистру (`_from_person`): у `companion_firstName` хвост — `firstName` → поле
-- `firstname`, и оно находится. А вот `companion_first_name` дал бы хвост `name`
-- — то есть подставилось бы **полное имя**, молча и всегда.
--
-- Идемпотентность: вставка по условию отсутствия.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(DISTINCT site_id) FROM `dating_profile`) AS сайтов_с_анкетами,
       (SELECT COUNT(*) FROM `dating_mail_var`
         WHERE `name` IN ('companion_firstName', 'companion_lastName')) AS уже_есть;

-- ------------------------------------------- Первое имя
INSERT INTO `dating_mail_var` (`site_id`, `name`, `source`, `title`, `default_value`)
SELECT s.site_id, 'companion_firstName', 'companion',
       'Имя собеседника без фамилии — первое слово. Пусто, если делить нечего', ''
  FROM (SELECT DISTINCT site_id FROM `dating_profile`) s
 WHERE NOT EXISTS (SELECT 1 FROM `dating_mail_var` v
                    WHERE v.site_id = s.site_id AND v.name = 'companion_firstName');

-- ------------------------------------------- Фамилия
INSERT INTO `dating_mail_var` (`site_id`, `name`, `source`, `title`, `default_value`)
SELECT s.site_id, 'companion_lastName', 'companion',
       'Фамилия собеседника — всё после первого пробела. Пусто, если пробела нет', ''
  FROM (SELECT DISTINCT site_id FROM `dating_profile`) s
 WHERE NOT EXISTS (SELECT 1 FROM `dating_mail_var` v
                    WHERE v.site_id = s.site_id AND v.name = 'companion_lastName');

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, `site_id`, `name`, `source`
  FROM `dating_mail_var`
 WHERE `name` IN ('companion_firstName', 'companion_lastName')
 ORDER BY `site_id`, `name`;
