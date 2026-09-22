-- Расписание писем избранным: когда круг набирает пул (2026-09-22)
--
-- ЗАЧЕМ. На «Фаворитах → Приставание» стоял блок «Возобновление разговора» с
-- кнопкой «Генерировать письма». Им не пользовались: пул набирает круг, а
-- человеку остаётся вычитать готовое. На его месте — расписание, и круг теперь
-- ходит по сменам, как ходят рассылка и инвайты.
--
-- Это ровно тот же путь, каким прошли инвайты: у них кнопку «Генерировать
-- инвайты» тоже убрали, оставив расписание. Ручка набора при этом жива — ею
-- ходят тесты и круг.
--
-- ⚠ ВТОРОГО ВЫКЛЮЧАТЕЛЯ НЕТ, И ЭТО РЕШЕНИЕ. Роль «работает ли занятие» уже
-- играет `favorite_mail_make_on` — та самая галочка «генерация писем» в полосе
-- подвкладок. У рассылки её зовут `mailing_schedule_on`, у инвайтов
-- `invite_schedule_on`; имя другое, смысл тот же. Заведи мы рядом ещё
-- `favorite_mail_schedule_on`, две галочки отвечали бы на один вопрос, и человек
-- гадал бы, какая из них главная.
--
-- ⚠⚠ ПОЧЕМУ НАКОПЛЕННЫМ СТАВИМ СУТКИ ЦЕЛИКОМ. По правилу проекта пустое
-- расписание значит «не работаем», а не «работаем всегда» (`mailing_round`,
-- `invite_round`). Оставь мы столбец пустым, накат миграции **остановил бы
-- генерацию** у сайта, где она включена, — и молча: человек ничего не менял, а
-- письма перестали появляться.
--
-- Поэтому сайтам с поднятой галочкой проставляется смена `00:00–24:00`: это в
-- точности нынешнее поведение, записанное словами расписания. Дальше человек
-- сузит её сам, уже видя ленту.
--
-- ⚠ `24:00` — законный конец суток: правая граница смены строгая
-- (`from <= минуты < to`), так что 1440 включает последнюю минуту.
--
-- ⚠ Сайтам с опущенной галочкой расписание не заводится вовсе: они и так не
-- работают, а придуманные за человека смены он увидел бы как чужое решение.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет; смены
-- проставляются только там, где столбец пуст.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'site'
           AND COLUMN_NAME = 'favorite_mail_schedule') AS столбец_есть,
       (SELECT COUNT(*) FROM `site` WHERE `favorite_mail_make_on` = 1)
         AS сайтов_с_генерацией;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'site'
               AND COLUMN_NAME = 'favorite_mail_schedule');

SET @sql = IF(@add,
    "ALTER TABLE `site`
        ADD COLUMN `favorite_mail_schedule` JSON NULL DEFAULT NULL
            COMMENT 'Смены суток: когда круг набирает пул писем избранным'
            AFTER `favorite_mail_make_on`",
    'SELECT ''столбец уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- Нынешнее поведение словами расписания: круглые сутки — тем, у кого включено.
UPDATE `site`
   SET `favorite_mail_schedule` = CAST('[{"from": "00:00", "to": "24:00"}]' AS JSON)
 WHERE `favorite_mail_make_on` = 1
   AND (`favorite_mail_schedule` IS NULL
        OR JSON_LENGTH(`favorite_mail_schedule`) = 0);

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'site'
           AND COLUMN_NAME = 'favorite_mail_schedule') AS столбец_есть,
       (SELECT COUNT(*) FROM `site`
         WHERE JSON_LENGTH(`favorite_mail_schedule`) > 0) AS сайтов_со_сменами;
