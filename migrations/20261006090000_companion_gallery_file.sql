-- Что из галереи уже ушло этому собеседнику: `companion_gallery_file` (2026-10-06)
--
-- ЗАЧЕМ. Снимок, посланный человеку дважды, выдаёт агентство вернее любого
-- текста: живая девушка помнит, что показывала. Помнить за неё должен код —
-- отправок бывают сотни, и держать это в голове оператора нельзя.
--
-- ⚠⚠ ТАБЛИЦА ЗАВОДИТСЯ РАНЬШЕ САМОЙ ОТПРАВКИ, И ЭТО ОСОЗНАННО. Отправки фото в
-- чат сейчас нет вовсе: галерея — картотека (обход Диска, индекс, описание,
-- показ в панели). Запись о том, что ушло, нужна в тот же миг, когда отправка
-- появится; заводить её потом значило бы иметь первые сотни отправок без
-- памяти, то есть ровно те дубли, ради которых таблица и затеяна.
--
-- ⚠ КЛЮЧ ПАРЫ — `(site_id, uid)`, КАК ВЕЗДЕ В РАЗДЕЛЕ. Номер человека уникален
-- в пределах сайта, а не вообще: два сайта вправе выдать один и тот же. Без
-- `site_id` в ключе отметка с одного сайта глушила бы снимок на другом.
--
-- ⚠⚠ `profile_id` ЗДЕСЬ ХРАНИТСЯ, ХОТЯ ВЫВОДИМ ИЗ ФАЙЛА. Галерея принадлежит
-- анкете, и строго говоря довольно `gallery_file_id`. Но вопрос «что эта
-- девушка показывала этому человеку» задаётся чаще прочих — и без колонки он
-- требовал бы присоединения `gallery_file` на каждый показ строки диалога.
--
-- ⚠ ВНЕШНИЙ КЛЮЧ НА ФАЙЛ С КАСКАДОМ: строка индекса пропавшего файла живёт
-- (метится `is_gone`), но если её однажды снесут руками, отметки об отправке
-- этого файла бессмысленны — показывать нечего.
--
-- ⚠⚠ У АНКЕТЫ КЛЮЧА НЕТ, как и везде в разделе: `profile_id` тут для чтения.
-- Уборка анкеты уносит её `gallery_file` каскадом, а с ними — и эти строки.
--
-- Идемпотентность: `CREATE TABLE IF NOT EXISTS`.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'companion_gallery_file') AS таблица_есть;

CREATE TABLE IF NOT EXISTS `companion_gallery_file` (
    `id` INT NOT NULL AUTO_INCREMENT,

    `profile_id` INT NOT NULL
        COMMENT 'От чьего имени ушло; выводится из файла, хранится ради чтения',
    `site_id` INT NOT NULL
        COMMENT 'Сайт собеседника: номер человека уникален в пределах сайта',
    `uid` VARCHAR(32) NOT NULL
        COMMENT 'Кому ушло: номер собеседника на сайте',

    `gallery_file_id` INT NOT NULL COMMENT 'Какой снимок ушёл (`gallery_file.id`)',

    `sent_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        COMMENT 'Когда ушло',

    PRIMARY KEY (`id`),

    -- ⚠⚠ Один снимок одному человеку — **один раз**. Ключ, а не проверка в
    -- коде: два круга могут решать одновременно, и проверка «уже слали?»
    -- отдельным запросом разошлась бы со вставкой между ними.
    UNIQUE KEY `uq_companion_file` (`site_id`, `uid`, `gallery_file_id`),

    -- Под вопрос «что эта девушка слала этому человеку».
    KEY `idx_pair` (`profile_id`, `uid`),
    -- Под отбор «чего этому человеку ещё не слали».
    KEY `idx_file` (`gallery_file_id`),

    CONSTRAINT `fk_companion_gallery_file` FOREIGN KEY (`gallery_file_id`)
        REFERENCES `gallery_file` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Какие снимки галереи уже ушли какому собеседнику';

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'companion_gallery_file') AS таблица_есть,
       (SELECT COUNT(*) FROM `companion_gallery_file`) AS строк;
