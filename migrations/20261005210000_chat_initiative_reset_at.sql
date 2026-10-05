-- Ручной сброс счёта заходов инициативы: столбец `reset_at` (2026-10-05)
--
-- ЗАЧЕМ. Счёт заходов «подряд» обнуляет только входящая реплика собеседника. Это
-- верно по смыслу — молчание тоже ответ, — но значит, что пара, упёршаяся в
-- потолок, замирает навсегда, если человек не напишет сам. На проде так и вышло:
-- 72 ушедшие инициативы 33 парам, ни одного ответа, пул стоит пустым.
--
-- Теперь у человека есть щелчок: иконка в шапке разговора на «Чат → Диалоги»
-- отпускает пару, и счёт начинается заново. Отметка ложится на ПОСЛЕДНЮЮ
-- УШЕДШУЮ КАРТОЧКУ ПАРЫ и значит «счёт заходов считать с этого момента».
--
-- ⚠ СВОЕЙ ТАБЛИЦЫ НЕТ. Сброс — свойство пары, но живёт отметкой на карточке:
-- журнал ушедшего остаётся цел, а сам сброс виден в истории пары. Тем же приёмом
-- обошлись без своей таблицы сбои (`error`, `source` вида `crash:…`).
--
-- ⚠⚠ ПОЭТОМУ `NULL DEFAULT NULL`, А НЕ `NOT NULL DEFAULT CURRENT_TIMESTAMP`.
-- `ALTER` со вторым проставил бы всем накопленным строкам время самой миграции —
-- то есть выкладка молча обнулила бы счёт заходов у всех пар разом, и девушки
-- пошли бы звать по второму кругу тех, кого уже звали. Та же грабля, что у
-- `favorite_mail.updated_at` и у соседнего `chat_initiative.updated_at`.
--
-- ⚠ ОТМЕТКА НЕ ВЫКЛЮЧАЕТ СЧЁТ, А СДВИГАЕТ ЕГО НИЖНЮЮ ГРАНИЦУ: карточки, ушедшие
-- после неё, счёт видит. Иначе сброс стал бы способом отключить потолок навсегда.
--
-- ⚠ ДНЕВНОГО ПОТОЛКА ОТМЕТКА НЕ КАСАЕТСЯ (`chat_initiative_max_day`): тот
-- сторожит не живого человека, а зацикленный автоответчик на той стороне.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
           AND COLUMN_NAME = 'reset_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `status` = 'sent')
         AS ушедших;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
               AND COLUMN_NAME = 'reset_at');

SET @sql = IF(@add,
    "ALTER TABLE `chat_initiative`
        ADD COLUMN `reset_at` DATETIME NULL DEFAULT NULL
            COMMENT 'Счёт заходов обнулён руками после этой карточки; NULL — не обнуляли'
            AFTER `sent_at`",
    'SELECT ''столбец уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_initiative'
           AND COLUMN_NAME = 'reset_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `chat_initiative` WHERE `reset_at` IS NOT NULL)
         AS сброшенных;
