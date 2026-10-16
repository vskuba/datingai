-- Шаблон письма принадлежит анкете (2026-10-16)

-- ЗАЧЕМ (указание человека).
--
-- Прежде шаблоны были общими на сайт: рассылка подставляла случайной анкете любой
-- включённый текст, и по имени шаблона было не понять, к какой он девушке. Теперь
-- круг пишет заготовки **каждой включённой анкете** своей рукой, по числу из
-- настройки, и имя несёт владелицу: `elena_template_1`. Колонка `profile_id` — то,
-- чем владелица записана: по ней список на «Шаблонах» показывает её тексты, а пул
-- рассылки берёт ей одной, а не всем подряд.
--
-- ⚠ `profile_id` ЗДЕСЬ ЗНАЧИТ ТО ЖЕ, ЧТО ВО ВСЕЙ БАЗЕ: НАШ НОМЕР СТРОКИ `profile`.
-- Не номер на сайте (то — `profile.profile_id`, и оно строка); сверка в
-- `20260927130000_profile_site_id_column.sql`.
--
-- ⚠ КОЛОНКА МОЖЕТ БЫТЬ ПУСТОЙ, И НАКОПЛЕННЫЕ СТРОКИ — ПУСТЫЕ. Прежние шаблоны
-- писались на весь сайт, и переписать их на одну анкету нельзя: общий текст не
-- принадлежит ей, и угадать владелицу — значит выдумать её.
-- Такие строки (`profile_id IS NULL`) рассылка больше не берёт: анкета пишет своим,
-- что не принадлежит ей, то не её письмо. Живые тексты круг напишет сам при первом
-- же включённом расписании, а старые уберёт уборка по сроку `template_keep` —
-- никаких данных не потеряно, просто у каждого текста теперь своя хозяйка.
--
-- ⚠ ВНЕШНЕГО КЛЮЧА НЕТ — общее правило таблицы: сайты и анкеты живут у соседа и у
-- нас без своей таблицы; `mailing` связан с `profile` ключом, а шаблон от удаления
-- анкеты переживает её спокойно — журнал писем уже помнит текст целиком.
--
-- Идемпотентность: колонка добавляется, только если её ещё нет.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail'
           AND COLUMN_NAME = 'profile_id') AS колонка_есть,
       (SELECT COUNT(*) FROM `mail`) AS шаблонов;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail'
               AND COLUMN_NAME = 'profile_id');

SET @sql = IF(@add,
    "ALTER TABLE `mail`
        ADD COLUMN `profile_id` INT NULL DEFAULT NULL
            COMMENT 'Чей это шаблон: номер нашей анкеты (`profile.id`); NULL — заведён до правила, когда тексты были общими на сайт'
            AFTER `site_id`",
    'SELECT ''колонка уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail'
           AND COLUMN_NAME = 'profile_id') AS колонка_есть,
       (SELECT COUNT(*) FROM `mail` WHERE `profile_id` IS NULL) AS без_хозяйки,
       (SELECT COUNT(*) FROM `mail`) AS шаблонов;
