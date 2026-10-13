-- Намерения разговора: что агент пообещал и исполнено ли это (2026-10-13)
--
-- ЗАЧЕМ. Агент `dating_talk` по ходу разговора принимает решения, которых нет в
-- тексте ответа: «она обещала прислать фото», «он просил не писать до вечера».
-- Сосед такое уже помечает — инструмент `chat_intention_add` кладёт метку словом
-- и отдаёт её списком в ключе `intentions` ответа `/chat/ask`.
--
-- Панель этот ключ выбрасывала: `rhapsody_api_chat_talk` читала только `result`
-- и `message_id`. Обещания девушки не жили нигде, и исполнить их было нечему.
--
-- ⚠ ЭТО НЕ КОПИЯ `companion_intention` У СОСЕДА. Та решает другую задачу:
-- отметкой времени режет историю надвое, чтобы одно обещание не приезжало на
-- каждом следующем ответе. Там дедупликация внутри разговора, здесь очередь
-- работы; подменить одно другим нельзя.
--
-- ⚠⚠ ТАБЛИЦА ЗАВОДИТСЯ РАНЬШЕ ИСПОЛНИТЕЛЯ, И ЭТО ОСОЗНАННО. Круга, который
-- берёт открытые намерения и делает дело, пока нет. Память нужна в тот же миг,
-- когда исполнение появится: заводить её потом значило бы иметь первые сотни
-- обещаний без следа. Тем же доводом живёт `companion_gallery_file`.
--
-- ⚠ КЛЮЧ ПАРЫ — `(site_id, uid)`, КАК ВЕЗДЕ В РАЗДЕЛЕ. Номер человека уникален
-- в пределах сайта, а не вообще: два сайта вправе выдать один и тот же.
--
-- ⚠⚠ КЛЮЧ ДЕРЖАТ ТОЛЬКО ОТКРЫТЫЕ. `open_intention` гаснет в `NULL` у
-- выполненной, а `NULL` в MySQL уникальности не нарушает. История обещаний
-- остаётся, повтор открытой строки не заводится. Простой ключ по
-- `(site_id, uid, profile_id, intention)` запретил бы ВТОРОЕ обещание фото
-- когда бы то ни было — а оно законно и ожидаемо: «oh no… I didn't get any
-- photo» → «i can send it again if you want».
--
-- ⚠⚠ `profile_id` ВХОДИТ В КЛЮЧ, НО НЕ В ВЫЧИСЛЕНИЕ, и это не стиль. MySQL
-- запрещает `ON DELETE CASCADE` у внешнего ключа, если от его столбца зависит
-- генерируемый (`Cannot add foreign key constraint`, 1215) — таблица не
-- создалась бы вовсе. Тем же приёмом живут `chat_initiative` и `invite`.
--
-- ⚠ ДВА СОСТОЯНИЯ, НЕ ТРИ. `failed` добавит миграцией тот, кто заведёт
-- исполнителя: сегодня падать нечему.
--
-- ⚠ РЕГИСТР МЕТКИ СРАВНИВАТЬ НЕ НУЖНО: при `utf8mb4_unicode_ci` ключ считает
-- `Promised_Photo` и `promised_photo` одним.
--
-- Идемпотентность: `CREATE TABLE IF NOT EXISTS`.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'chat_intention') AS таблица_есть;

-- -------------------------------------------- Намерения разговора
CREATE TABLE IF NOT EXISTS `chat_intention` (
    `id`         INT NOT NULL AUTO_INCREMENT,

    `site_id`    INT NOT NULL
        COMMENT 'Сайт собеседника: номер человека уникален в его пределах',
    `profile_id` INT NOT NULL
        COMMENT 'Чьё намерение: от чьего имени шёл разговор',
    `uid`  VARCHAR(32) NOT NULL
        COMMENT 'Кому обещано: номер собеседника на сайте',

    `intention` VARCHAR(100) NOT NULL
        COMMENT 'Метка словом: she_promised_send_photo',

    `status`  ENUM('open', 'done') NOT NULL DEFAULT 'open'
        COMMENT 'open — ждёт исполнения, done — исполнено',
    `done_at` DATETIME NULL DEFAULT NULL
        COMMENT 'Когда исполнили. NULL — ещё открыто',

    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        COMMENT 'Когда намерение приехало от соседа',

    -- ⚠ Приём `chat_initiative`: ключ держат только открытые строки.
    `open_intention` VARCHAR(100) GENERATED ALWAYS AS
        (IF(`status` = 'open', `intention`, NULL)) STORED
        COMMENT 'Метка, пока намерение открыто. NULL у выполненных',

    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_chat_intention_open`
        (`site_id`, `uid`, `profile_id`, `open_intention`),
    -- Под отбор исполнителя: открытые этого сайта, старые первыми.
    KEY `idx_chat_intention_open` (`status`, `site_id`, `id`),
    KEY `fk_chat_intention_profile` (`profile_id`),
    CONSTRAINT `fk_chat_intention_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `profile` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Намерения разговора: что агент пообещал и исполнено ли это';

-- ---------------------------------------------------------------- После
--
-- ⚠ Пусто, и это правильно: строки появятся на первом же прогоне `dating_talk`,
-- который вернёт непустой `intentions`.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'chat_intention') AS таблица_есть,
       (SELECT COUNT(*) FROM `chat_intention`) AS строк;
