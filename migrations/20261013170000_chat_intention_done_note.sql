-- Чем намерение исполнили: `chat_intention.done_note` (2026-10-13)
--
-- ЗАЧЕМ. В строке есть «исполнено ли» (`status`) и «когда» (`done_at`), но нет
-- самого главного для человека — **что сделали**. Вкладка «Намерения и действия»
-- показывает исполненное отдельной колонкой, и без этого столбца ей нечего в неё
-- положить: она отвечала бы «обещание закрыто» и молчала о том, чем.
--
-- ⚠ ОДИН СТОЛБЕЦ, А НЕ ТАБЛИЦА ДЕЙСТВИЙ. Отдельная `chat_intention_action` дала
-- бы историю из нескольких действий на одно намерение. Но исполнителя ещё нет, и
-- формы его работы мы не знаем: заведённая сейчас таблица подогнала бы его под
-- догадку. Одно намерение — одна строка — один исход; понадобится история, её
-- заведут тогда, когда будет известно, из чего она состоит.
--
-- ⚠⚠ `NOT NULL DEFAULT ''` ЗДЕСЬ ЗАКОННО, в отличие от грабли с `updated_at` у
-- `favorite_mail`. Там `DEFAULT CURRENT_TIMESTAMP` приписал всем накопленным
-- строкам время наката, и письмо суточной давности показалось сегодняшним — то
-- есть умолчание **врало**. Пустая строка не врёт: «не записали» — правда для
-- всех старых строк, их никто не исполнял.
--
-- ⚠ Длина 500, как у `error` в пулах: это строка для глаза, а не метка.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'chat_intention'
           AND COLUMN_NAME = 'done_note') AS столбец_есть,
       (SELECT COUNT(*) FROM `chat_intention`) AS строк;

-- ------------------------------------------------- Чем исполнили
SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'chat_intention'
                  AND COLUMN_NAME = 'done_note') = 0,
              'ALTER TABLE `chat_intention`
                   ADD COLUMN `done_note` VARCHAR(500) NOT NULL DEFAULT ''''
                       COMMENT ''Что сделали по намерению: словами, для вкладки''
                       AFTER `done_at`',
              'SELECT ''столбец уже есть''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE()
           AND TABLE_NAME = 'chat_intention'
           AND COLUMN_NAME = 'done_note') AS столбец_есть;
