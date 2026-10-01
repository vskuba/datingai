-- Индекс файлов галереи с Google Диска (2026-10-01)
--
-- ## ЗАЧЕМ (указание человека)
--
-- Вкладка «Галерея» показывает фото девушки с Диска, и у каждого снимка должны
-- быть **описание и теги**: их пишет vision-модель, а человек правит руками.
--
-- Отсюда таблица. Первая редакция спеки утверждала «список фото не лежит нигде,
-- только снимок в Redis на 15 минут» — это отменено, и отменено по делу: за
-- описанием стоит вызов модели и, возможно, правка человеком. Терять такое с
-- истечением кэша нельзя, а добывать заново при каждом открытии вкладки — это
-- оплачивать модель за то, что уже знаем.
--
-- Байты фотографий по-прежнему не хранятся нигде: здесь имена, размеры,
-- контрольные суммы и смысл — знание о снимках, а не снимки.
--
-- ## ⚠⚠⚠ ЧЕМ ЭТО ОПАСНО И ЧЕМ ОПАСНОСТЬ СНЯТА
--
-- **`described_at` и `edited_at` обязаны уметь NULL и не иметь умолчания.**
-- Эта грабля в проекте уже случалась — с `updated_at` у писем: заведённый
-- `NOT NULL DEFAULT CURRENT_TIMESTAMP`, он раздал время миграции всем
-- накопленным строкам, и письмо, не ушедшее сутки назад, показалось
-- сегодняшним. Здесь цена та же: неразобранные снимки выглядели бы
-- разобранными сегодня, и круг разбора обошёл бы их стороной — навсегда.
-- Неизвестно — значит NULL.
--
-- ## ⚠⚠ Ключ строки — `file_id`, а не путь
--
-- Файл, перенесённый человеком из `public` в `intim`, остаётся тем же файлом:
-- у него меняется `path`, но описание и теги обязаны переехать с ним. Будь
-- ключом путь, перенос выглядел бы как «старый пропал, новый появился», и за
-- описание мы заплатили бы модели второй раз.
--
-- ## ⚠⚠ Пропавший файл метится, а не удаляется
--
-- `is_gone = 1` вместо `DELETE`: пропасть файл может от одного моргания Диска
-- или снятого доступа, а за строкой стоит вызов модели и правка руками.
-- Вернулся с тем же `file_id` — зажигается обратно вместе с описанием. Тот же
-- приём уже работает у `favorite` (`state = 'gone'`) и у `site.is_gone`.
--
-- ## ⚠ Внешнего ключа на `profile` нет
--
-- Как и у прочих таблиц этого раздела (`database.md`): `site_id` и `profile_id`
-- живут без FK. Чистка идёт каскадом из кода, а не из схемы.
--
-- Идемпотентность: таблица заводится через `information_schema` и
-- подготовленный запрос — второй прогон видит её и ничего не делает.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`TABLES`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file') AS таблица_уже,
       (SELECT COUNT(*) FROM `profile`) AS анкет;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`TABLES`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file');

SET @sql = IF(@есть = 0,
  'CREATE TABLE `gallery_file` (
     `id` int NOT NULL AUTO_INCREMENT,
     `profile_id` int NOT NULL COMMENT ''Чья галерея; без FK, как везде в разделе'',
     `file_id` varchar(128) NOT NULL
       COMMENT ''Номер файла на Диске — ключ строки: имя и путь меняются, он нет'',
     `path` varchar(1000) NOT NULL DEFAULT ''''
       COMMENT ''Путь от папки девушки (photo/public/diana.png); им и группируем показ'',
     `name` varchar(255) NOT NULL DEFAULT '''',
     `mime` varchar(128) NOT NULL DEFAULT '''',
     `kind` enum(''image'',''video'',''other'') NOT NULL DEFAULT ''other'',
     `size` bigint NOT NULL DEFAULT 0,
     `md5` varchar(64) NOT NULL DEFAULT ''''
       COMMENT ''Контрольная сумма Диска: «тот ли файл» без скачивания'',
     `width` int NOT NULL DEFAULT 0,
     `height` int NOT NULL DEFAULT 0,
     `description` text NULL
       COMMENT ''Что на снимке. Пишет модель, правит человек'',
     `tags` json NULL COMMENT ''Список коротких тегов от модели'',
     `described_at` datetime NULL
       COMMENT ''Когда разобрано моделью. ⚠ NULL = не разобрано, умолчания быть не должно'',
     `described_error` varchar(500) NOT NULL DEFAULT ''''
       COMMENT ''Чем кончился последний разбор; удача стирает'',
     `edited_at` datetime NULL
       COMMENT ''⚠ Правлено руками: непустое — разбор эту строку не трогает'',
     `is_gone` tinyint(1) NOT NULL DEFAULT 0
       COMMENT ''Файла на Диске больше нет. Не удаляем: описание дороже строки'',
     `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
     `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
     PRIMARY KEY (`id`),
     UNIQUE KEY `uniq_file` (`file_id`),
     KEY `idx_show` (`profile_id`, `is_gone`),
     KEY `idx_todo` (`is_gone`, `described_at`)
   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
     COMMENT=''Индекс файлов галереи с Google Диска: что лежит и что на снимке''',
  'SELECT ''таблица уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`TABLES`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file') AS таблица_стала,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file') AS колонок,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
           AND `COLUMN_NAME` IN ('described_at', 'edited_at')
           AND `IS_NULLABLE` = 'YES' AND `COLUMN_DEFAULT` IS NULL) AS отметок_без_умолчания;
