-- Дата у накопленных писем избранным: снять отметку миграции (2026-09-22)
--
-- ЗАЧЕМ. Миграция `20260922100000_favorite_mail_updated_at` завела столбец
-- `updated_at`, чтобы у неудачного письма в «Доставке» была дата. Столбец вышел
-- `NOT NULL DEFAULT CURRENT_TIMESTAMP`, и `ALTER` проставил **всем тридцати
-- накопленным строкам время самой миграции**.
--
-- Своя же приписка в той миграции обещала другое: «у накопленных строк отметка
-- станет временем миграции, и это честнее нуля: `NULL` показу не мешает —
-- `COALESCE` возьмёт `created_at`». `NOT NULL DEFAULT` этого `NULL` не оставил,
-- и `COALESCE(sent_at, updated_at, created_at)` до `created_at` не доходит
-- никогда.
--
-- ЧТО ИЗ ЭТОГО ВЫШЛО. Письмо Дианы к René, не ушедшее **21 сентября в 16:02**,
-- стало показываться как «22 сентября 08:47» — время миграции. Пара к тому
-- моменту уже лежала в «Не пишем», и красная строка со свежей датой читается
-- ровно одним способом: «код опять пишет тому, кому запрещено». Проверка по
-- журналу запросов показала обратное — после запрета к нему не ходили ни разу,
-- а оба письменных похода были до него.
--
-- То есть цена ошибки здесь не «некрасивая дата», а ложное обвинение
-- собственному коду и потерянный на разбор час.
--
-- ⚠ ПОЧЕМУ НЕ `created_at` ВМЕСТО `NULL`. Разница видна на показе: `NULL`
-- отдаёт решение `COALESCE`, а тот и так возьмёт `created_at`. Записав его
-- прямо, мы соврали бы в самом столбце — он про **исход**, а не про сочинение,
-- и следующий запрос, читающий `updated_at` без `COALESCE`, получил бы ответ на
-- другой вопрос.
--
-- ⚠⚠ КАК ОТЛИЧИТЬ ОТМЕТКУ МИГРАЦИИ ОТ НАСТОЯЩЕЙ. Признак — пачка: `ALTER`
-- проставил один и тот же миг **всем** строкам разом. Настоящую отметку ставит
-- отправка, а круг уносит не больше письма в минуту, поэтому пять строк с
-- точностью до секунды работой не объясняются.
--
-- Второе условие — `created_at` старше этого мига на минуту. Набор пула кладёт
-- черновики подряд, и несколько штук попадают в одну секунду; их отметка равна
-- их же `created_at`, так что снимать её незачем и вредно.
--
-- Идемпотентность: повторный заход не найдёт пачки (отметки уже сняты) и
-- ничего не тронет.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `favorite_mail` WHERE `updated_at` IS NULL)
         AS без_отметки,
       (SELECT COUNT(*) FROM `favorite_mail`) AS всего;

-- Столбец обязан уметь `NULL`: без этого снять ложную отметку нечем.
SET @nullable = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
                  WHERE TABLE_SCHEMA = DATABASE()
                    AND TABLE_NAME = 'favorite_mail'
                    AND COLUMN_NAME = 'updated_at'
                    AND IS_NULLABLE = 'YES');

SET @sql = IF(@nullable,
    "ALTER TABLE `favorite_mail`
        MODIFY COLUMN `updated_at` DATETIME NULL DEFAULT NULL
            ON UPDATE CURRENT_TIMESTAMP
            COMMENT 'Когда записали исход; NULL — неизвестно, показ возьмёт created_at'",
    'SELECT ''столбец уже допускает NULL'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- Миг, которым `ALTER` пометил накопленное: самая многолюдная секунда.
SET @stamp = (SELECT `updated_at` FROM `favorite_mail`
               WHERE `updated_at` IS NOT NULL
               GROUP BY `updated_at`
              HAVING COUNT(*) >= 5
               ORDER BY COUNT(*) DESC
               LIMIT 1);

UPDATE `favorite_mail`
   SET `updated_at` = NULL
 WHERE @stamp IS NOT NULL
   AND `updated_at` = @stamp
   AND `created_at` < @stamp - INTERVAL 1 MINUTE;

SELECT 'ПОСЛЕ' AS этап,
       @stamp AS отметка_миграции,
       (SELECT COUNT(*) FROM `favorite_mail` WHERE `updated_at` IS NULL)
         AS без_отметки,
       (SELECT COUNT(*) FROM `favorite_mail`) AS всего;
