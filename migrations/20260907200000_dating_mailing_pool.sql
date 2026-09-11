-- Пул писем: подготовленные, но ещё не отправленные (2026-09-07)
--
-- ЗАЧЕМ: письма теперь готовятся заранее — человек выбирает шаблоны, жмёт
-- «Генерировать», и получает пул карточек: кто, кому, с каким текстом. Их можно
-- прочитать, поправить и только потом отправить.
--
-- `dating_mailing` задумывался как журнал отправленного. Пул — это тот же набор
-- полей, но до отправки: анкета, адресат, шаблон, готовый текст. Заводить под
-- него вторую таблицу значило бы держать две одинаковые и перекладывать строки
-- между ними при отправке.
--
-- ⚠ ПОЭТОМУ `status`, А НЕ ВТОРАЯ ТАБЛИЦА. Отправка меняет одно поле, а не
-- переносит запись. Заодно журнал и пул смотрятся одним запросом: «что этой
-- анкете уже готовилось» — вопрос про обоих.
--
-- ⚠ `sent_at` ОТДЕЛЬНО ОТ `created_at`. Первое — когда письмо ушло, второе —
-- когда его сделали. Между ними человек читает и правит, и разница бывает в часы.
-- Без обоих не ответить, сколько письмо пролежало в пуле.
--
-- ⚠ ПРО УНИКАЛЬНЫЙ КЛЮЧ. Он остаётся прежним — `(profile_id, uid, mail_id)`: одна
-- анкета не пришлёт одному человеку один и тот же **шаблон** дважды. Правило
-- генерации строже — не больше одного письма от анкеты адресату **любым**
-- шаблоном, — но оно про то, как набирается пул, а не про то, что допустимо в
-- истории. Держит его код генерации: ключ, включающий `mail_id`, такого запрета
-- выразить не может.
--
-- Идемпотентность: колонки добавляются, если их нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mailing'
           AND COLUMN_NAME = 'status') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_mailing`) AS строк;

-- ------------------------------------------- Состояние письма
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mailing'
        AND COLUMN_NAME = 'status') = 0,
    'ALTER TABLE `dating_mailing`
        ADD COLUMN `status` ENUM(''draft'', ''sent'', ''failed'') NOT NULL DEFAULT ''draft''
            COMMENT ''draft — лежит в пуле, sent — ушло, failed — не ушло''
            AFTER `body`,
        ADD COLUMN `error` VARCHAR(500) NOT NULL DEFAULT ''''
            COMMENT ''Почему не ушло: причина словами, для страницы'',
        ADD COLUMN `sent_at` DATETIME NULL DEFAULT NULL
            COMMENT ''Когда ушло. NULL — ещё в пуле''',
    'SELECT ''колонка status уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Индекс под пул
--
-- Страница спрашивает «покажи пул этого сайта, свежее сверху» на каждое открытие
-- вкладки. Порядок в индексе — от самой избирательной колонки: сайтов десятки,
-- состояний три.
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_mailing'
        AND INDEX_NAME = 'idx_dating_mailing_pool') = 0,
    'ALTER TABLE `dating_mailing`
        ADD INDEX `idx_dating_mailing_pool` (`site_id`, `status`, `id`)',
    'SELECT ''индекс idx_dating_mailing_pool уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Выключатель отправки
--
-- ⚠ Хранится у сайта, а не в памяти страницы: решение «рассылка идёт» переживает
-- перезагрузку и видно всем, кто открыл панель. Умолчание — выключено: письма
-- уходят живым людям, и включать это должен человек, а не накат миграции.
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_site'
        AND COLUMN_NAME = 'mailing_send_on') = 0,
    'ALTER TABLE `dating_site`
        ADD COLUMN `mailing_send_on` TINYINT(1) NOT NULL DEFAULT 0
            COMMENT ''Отправлять ли письма из пула''
            AFTER `mailing_schedule_on`',
    'SELECT ''колонка mailing_send_on уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_mailing` WHERE `status` = 'draft') AS в_пуле,
       (SELECT COUNT(*) FROM `dating_mailing` WHERE `status` = 'sent') AS отправлено,
       (SELECT COUNT(*) FROM `dating_site` WHERE `mailing_send_on` = 1) AS сайтов_с_отправкой;
