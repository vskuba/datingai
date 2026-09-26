-- Время исхода у ответа на письмо (2026-09-26)
--
-- ЗАЧЕМ. Ответы приехали в «Фавориты → Доставка» и стоят там рядом с письмами
-- избранным. У письма время неудачи есть (`updated_at`), у ответа нет: в `mail_reply`
-- столбец один — `sent_at`, «когда ушло», — и у неудачи его нет и быть не может,
-- отметку снимает сама неудача.
--
-- Показ падает до `created_at`, то есть до времени, когда черновик **сочинили**.
-- Между сочинением и сорвавшейся отправкой бывают часы: пул набирает один круг,
-- уносит письма другой по своему расписанию. Две строки подряд, одинаковые по виду,
-- и у одной дата отвечает на другой вопрос — молча.
--
-- ⚠ ТАК УЖЕ СДЕЛАНО У ИНВАЙТОВ И У ПИСЕМ ИЗБРАННЫМ. Столбец нарочно того же имени:
-- две таблицы с разными именами под одно понятие расходятся в первом же запросе,
-- который пишут по памяти.
--
-- ⚠ `ON UPDATE CURRENT_TIMESTAMP` ставит отметку сам, при любой правке строки.
-- Отдельной записи в коде не нужно: исход пишет `mail_send_mark` одним `UPDATE` на
-- три таблицы, и добавь мы столбец в SQL — забыли бы в одной из трёх ветвей (ушло,
-- окончательный отказ, «осталось в пуле»), и дата пропадала бы через раз.
--
-- ⚠⚠ ГЛАВНОЕ: `NULL DEFAULT NULL`, А НЕ `NOT NULL DEFAULT CURRENT_TIMESTAMP`.
-- Ровно на этом уже обожглись четыре дня назад — `20260922100000` плюс починка
-- `20260922140000`. Заведённый с умолчанием, столбец получает от `ALTER` **время
-- самой миграции всем накопленным строкам**: письмо Дианы к René, не ушедшее
-- 21 сентября в 16:02, показалось как «22 сентября 08:47». Пара к тому моменту уже
-- лежала в «Не пишем», и красная строка со свежей датой читается ровно одним
-- способом: «код опять пишет тому, кому запрещено». Цена ошибки — не «некрасивая
-- дата», а ложное обвинение своему коду и час на разбор по журналу запросов.
--
-- Здесь этого шага нет вовсе: неизвестно — значит `NULL`, а показ (`_sentWhen`,
-- `COALESCE(sent_at, updated_at, created_at)`) сам возьмёт `created_at`.
--
-- ⚠ ПОЧЕМУ НЕ ЗАПИСАТЬ `created_at` ПРЯМО. Записав, мы соврали бы в самом столбце —
-- он про **исход**, а не про сочинение, — и следующий запрос, читающий `updated_at`
-- без `COALESCE`, получил бы ответ на другой вопрос.
--
-- ⚠ ЧЕГО СТОЛБЕЦ НЕ ЗНАЧИТ. `ON UPDATE` срабатывает и на правку текста человеком
-- (`PUT /api/dating/mail_reply/{id}`), то есть строго это «когда строку последний раз
-- меняли». Так же устроено у писем избранным и у инвайтов, и в журнале это видно
-- только у застрявшего черновика, который после отказа ещё и правили руками. Развести
-- «исход» и «правку» значило бы завести второй столбец под вопрос, которого никто не
-- задаёт.
--
-- Идемпотентность: столбец добавляется, только если его ещё нет.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
           AND COLUMN_NAME = 'updated_at') AS столбец_есть,
       (SELECT COUNT(*) FROM `mail_reply` WHERE `sent_at` IS NULL)
         AS строк_без_даты,
       (SELECT COUNT(*) FROM `mail_reply`) AS всего;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
               AND COLUMN_NAME = 'updated_at');

SET @sql = IF(@add,
    "ALTER TABLE `mail_reply`
        ADD COLUMN `updated_at` DATETIME NULL DEFAULT NULL
            ON UPDATE CURRENT_TIMESTAMP
            COMMENT 'Когда записали исход; NULL — неизвестно, показ возьмёт created_at'
            AFTER `created_at`",
    'SELECT ''столбец уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- ⚠ Сверка «ПОСЛЕ» смотрит не только на наличие столбца, но и на его **вид**: та же
-- беда, что чинили 22-го, прошла бы проверку «столбец есть» не заметив ничего.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
           AND COLUMN_NAME = 'updated_at') AS столбец_есть,
       (SELECT IS_NULLABLE FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
           AND COLUMN_NAME = 'updated_at') AS допускает_null,
       (SELECT COALESCE(COLUMN_DEFAULT, 'нет') FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mail_reply'
           AND COLUMN_NAME = 'updated_at') AS умолчание,
       (SELECT COUNT(*) FROM `mail_reply` WHERE `updated_at` IS NOT NULL)
         AS с_отметкой_сразу,
       (SELECT COUNT(*) FROM `mail_reply`) AS всего;
