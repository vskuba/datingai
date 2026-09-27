-- Номер анкеты на сайте — своей колонкой (2026-09-27)
--
-- ## ЗАЧЕМ (указание человека)
--
-- Номер девушки на сайте знакомств лежит внутри JSON-столбца `profile`, под ключом
-- `id`. Им ключуются факты у соседа (`girl_id`), по нему же панель агентства
-- подписывает её строки («643694 Diana»), и по нему импорт переписки сводит
-- сообщения с нашими анкетами (`chat_panel.py`).
--
-- Значение, которым ищут и сводят, должно быть колонкой: из JSON его нельзя ни
-- проиндексировать, ни защитить от повтора.
--
-- ## ⚠⚠⚠ ИМЯ КОЛОНКИ СТОЛКНУЛОСЬ С ОБЩЕПРИНЯТЫМ, И ЭТО НАДО ЗНАТЬ
--
-- `profile_id` в этой базе уже значит другое — **номер нашей строки** в таблице
-- `profile`. Он стоит так в **тринадцати** таблицах (`mail_in`, `mail_reply`,
-- `favorite`, `invite`, `mailing`, `block`, …) и встречается в коде 1118 раз, всюду
-- с одним смыслом.
--
-- Здесь же `profile.profile_id` будет значить **номер на сайте** — величину чужую и
-- другого вида (строка, а не `INT`). То есть:
--
--     mail_reply.profile_id  →  profile.id          (наш номер)
--     profile.profile_id     →  номер на сайте      (чужой номер)
--
-- Запрос `JOIN ... ON x.profile_id = p.profile_id` соединит наш номер с сайтовым.
-- Он не упадёт — оба числа, — просто не найдёт ничего или найдёт не то, и молча.
--
-- ⚠ Имя, которым эта величина зовётся в коде сегодня, — **`site_uid`**
-- (`chat_panel.py`, `pool_pair.py`), и переименование колонки стоило бы одной
-- строки, пока ею никто не пользуется. Решение за человеком; миграция делает, как
-- он сказал, а предупреждение остаётся здесь — чтобы следующий читатель не принял
-- одно за другое.
--
-- ## ⚠⚠ Строкой, а не числом
--
-- Так просил человек, и это верно по существу: номер приходит от чужого сайта, и
-- «оно всегда число» — обещание, которого он не давал. У Goldenbride идентификаторы
-- анкет уже не только цифры.
--
-- Длина: 7 знаков на проде, 24 локально (тестовые). `VARCHAR(32)` с запасом.
--
-- ## ⚠⚠ Пусто — значит `NULL`, а не пустая строка
--
-- Уникальность ниже считает пустые строки одинаковыми, а `NULL`-ы — разными. Анкет
-- без снятой карточки в базе 66 локально; будь у них `''`, вторая же не завелась бы.
--
-- ## ⚠ Уникальность парой «сайт + номер», а не одним номером
--
-- Номера у разных сайтов свои и пересекаются; а вот две наши строки на одну анкету
-- сайта — ошибка данных, и ловить её надо при вставке. Проверено: повторов нет ни
-- на проде (15 анкет), ни локально (81).
--
-- Идемпотентность: колонка и ключ добавляются, только если их ещё нет; заполнение
-- трогает лишь пустые.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'profile'
           AND COLUMN_NAME = 'profile_id') AS колонка_есть,
       (SELECT COUNT(*) FROM `profile`) AS анкет,
       (SELECT COUNT(*) FROM `profile`
         WHERE JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')) REGEXP '^[0-9]+$')
         AS с_номером_в_json;

-- ── 1. Колонка ───────────────────────────────────────────────────────────────
SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'profile'
               AND COLUMN_NAME = 'profile_id');

SET @sql = IF(@add,
    "ALTER TABLE `profile`
        ADD COLUMN `profile_id` VARCHAR(32) NULL DEFAULT NULL
            COMMENT 'Номер анкеты НА САЙТЕ (не наш id!): им ключуются факты и сводится панель'
            AFTER `login`",
    'SELECT ''колонка уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- ── 2. Заполнение из JSON ────────────────────────────────────────────────────
--
-- ⚠ Берём **только непустое**: `JSON_UNQUOTE` от отсутствующего ключа даёт строку
-- «null» — четыре буквы, а не пустоту, — и без проверки она легла бы номером.
--
-- ⚠ Трогаем лишь те строки, где колонка ещё пуста: повторный прогон не затрёт то,
-- что к тому времени могли поправить руками.
UPDATE `profile`
   SET `profile_id` = JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id'))
 WHERE `profile_id` IS NULL
   AND JSON_VALID(`profile`)
   AND JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')) IS NOT NULL
   AND JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')) <> ''
   AND JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')) <> 'null';

-- ── 3. Уникальность пары ─────────────────────────────────────────────────────
SET @key = (SELECT COUNT(*) = 0 FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'profile'
               AND INDEX_NAME = 'uk_dating_profile_site_uid');

SET @dupes = (SELECT COUNT(*) FROM (
    SELECT `site_id`, `profile_id` FROM `profile`
     WHERE `profile_id` IS NOT NULL
     GROUP BY `site_id`, `profile_id` HAVING COUNT(*) > 1) one);

-- ⚠⚠ Ключ ставим, **только если повторов нет**. Иначе `ALTER` упал бы, и миграция
-- оборвалась бы на середине — с заполненной колонкой и без ключа, а следующий
-- прогон не сказал бы почему. Лучше честный пропуск с числом в отчёте.
SET @sql = IF(@key AND @dupes = 0,
    'ALTER TABLE `profile`
        ADD UNIQUE KEY `uk_dating_profile_site_uid` (`site_id`, `profile_id`)',
    IF(@dupes > 0,
       'SELECT ''ЕСТЬ ПОВТОРЫ: ключ не поставлен, разберите их руками'' AS пропуск',
       'SELECT ''ключ уже есть'' AS пропуск'));

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `profile` WHERE `profile_id` IS NOT NULL)
         AS с_номером,
       (SELECT COUNT(*) FROM `profile` WHERE `profile_id` IS NULL)
         AS без_номера,
       -- ⚠ `DISTINCT`: ключ из двух колонок даёт в `STATISTICS` две строки, и
       -- «2» в отчёте о наличии одного ключа заставляет читателя остановиться.
       (SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'profile'
           AND INDEX_NAME = 'uk_dating_profile_site_uid') AS ключ_есть,
       @dupes AS повторов_было;

-- ⚠ Сверка по существу: колонка обязана совпасть с тем, что лежит в JSON. «Столько
-- же строк» — ещё не «те же значения».
--
-- ⚠⚠ Сверяем **любой непустой** номер, а не только числовой. Колонка строковая
-- нарочно, и проверка «только цифры» молчала бы ровно о тех записях, ради которых
-- строку и выбрали. Локально таких сорок — тестовые анкеты.
SELECT 'расхождений колонки с JSON' AS проверка, COUNT(*) AS сколько
  FROM `profile`
 WHERE JSON_VALID(`profile`)
   AND JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')) IS NOT NULL
   AND JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')) NOT IN ('', 'null')
   AND (`profile_id` IS NULL
        OR `profile_id` <> JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')));
