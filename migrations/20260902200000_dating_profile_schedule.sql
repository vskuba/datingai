-- Расписание анкеты: когда ей быть в сети (2026-09-02)
--
-- ЗАЧЕМ: блок расписания на странице был **только видом** — смены жили в памяти
-- страницы до перезагрузки. В коде так и стояло: «чтобы никто не решил, будто
-- расписание уже работает». Теперь оно хранится.
--
-- ⚠ ПОЧЕМУ JSON-КОЛОНКА, А НЕ СВОЯ ТАБЛИЦА. Смен две-четыре, читают их только
-- вместе с анкетой и заменяют целиком — по одной смене их никто не спрашивает и не
-- правит. Отдельная таблица дала бы join на каждый показ карточки и порядок
-- вставки, который пришлось бы поддерживать; выигрыш — нормализация, которой здесь
-- нечего нормализовать. Тот же приём, что у `dating_site.settings`.
--
-- РАСПИСАНИЕ ПО УМОЛЧАНИЮ — три смены: 07:00–11:00, 14:00–18:00, 19:00–23:00.
-- Между ними перерывы, ночь пустая: анкета спит, как спит живой человек, и
-- круглосуточное присутствие не выдаёт её механикой.
--
-- ⚠ ТОЛЬКО ДЕВУШКАМ (`role = 'lady'`). Мужская анкета в этом проекте — рабочий
-- инструмент агентства (ею смотрят чужие анкеты), а не собеседница: держать её «в
-- сети по расписанию» незачем.
--
-- ⚠ УМОЛЧАНИЕ СТАВИТСЯ ТОЛЬКО ТАМ, ГДЕ КОЛОНКА ПУСТА. Прогон файла второй раз не
-- затрёт расписание, которое человек к тому времени поправил.
--
-- Идемпотентность: колонка добавляется, если её нет; заполнение — только пустых.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME = 'schedule') AS колонка_есть,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `role` = 'lady') AS девушек;

-- ------------------------------------------- Колонка
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'schedule') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `schedule` JSON NULL DEFAULT NULL
            COMMENT ''Смены суток: [{"from":"07:00","to":"11:00"}]; NULL — ведут руками''
            AFTER `work_reason`',
    'SELECT ''колонка schedule уже есть'' AS пропуск');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Умолчание девушкам
UPDATE `dating_profile`
   SET `schedule` = '[{"from": "07:00", "to": "11:00"},
                      {"from": "14:00", "to": "18:00"},
                      {"from": "19:00", "to": "23:00"}]'
 WHERE `role` = 'lady'
   AND (`schedule` IS NULL OR JSON_LENGTH(`schedule`) = 0);

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `dating_profile`
         WHERE `schedule` IS NOT NULL AND JSON_LENGTH(`schedule`) = 3) AS с_расписанием,
       (SELECT COUNT(*) FROM `dating_profile`
         WHERE `role` = 'lady' AND `schedule` IS NULL) AS девушек_без,
       (SELECT COUNT(*) FROM `dating_profile`
         WHERE `role` <> 'lady' AND `schedule` IS NOT NULL) AS мужских_задето;
