-- Звезда, погасшая на сайте: состояние `gone` у пары избранных (2026-09-28)
--
-- ЗАЧЕМ. `favorite_sync` до сих пор только добавлял и ничего не убирал: снятая на
-- сайте звезда оставалась у нас навсегда, а отбор писем избранным
-- (`favorite_mail._quiet`) читает НАШУ таблицу, а не сайт.
--
-- ⚠⚠ ЭТО НАЙДЕНО НА ЖИВОМ ПРОДЕ, А НЕ ПРИДУМАНО. В пуле «Фавориты → Приставание»
-- висело письмо `Oksana413636 → John849892`, при том что у Оксаны Джона в
-- избранных нет. Строка у нас:
--
--     favorite id=76  profile_id=48  uid=849892
--     state=already   reason=site_list   created_at=2026-09-13 11:02:14
--
-- То есть 13 сентября он там БЫЛ, звезду потом сняли, а строка осталась. Сверка
-- живых списков в тот день: у тринадцати анкет 22 лишние пары, все с
-- `reason = site_list`, все заведены 13 сентября. Двое висели у многих сразу
-- (`808675` у шести девушек, `157454` у пяти) — такие учётки убирает сам сайт.
--
-- ⚠ ГАСИМ, А НЕ УДАЛЯЕМ. `DELETE` унёс бы и состояние (`tries`, `error`), и повод
-- (`reason`), и историю: журнал доставки делит ответы между «Фаворитами» и
-- «Письмами» по наличию строки. Новое состояние в набор `FAVORITE_STATES`
-- ('done', 'already') не входит, а по нему отбирают все шесть мест — пул писем,
-- лента «Избранные онлайн», счётчики вкладок, — и перестают видеть пару сами.
--
-- ⚠ НАКОПЛЕННОЕ ЭТА МИГРАЦИЯ НЕ ТРОГАЕТ, и не может: какие из 22 пар мертвы, знает
-- только сайт. Их погасит первая же сверка после выкладки — она ходит раз в час
-- (`favorite_sync_every`), и сама же снимет их черновики.
--
-- ⚠ `fail` ОСТАЁТСЯ на месте и в наборе не участвовал: «нажать не вышло» — это не
-- «человек не избранный», у него свой счётчик заходов.
--
-- Идемпотентность: значение добавляется, только если его ещё нет в перечислении.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `favorite`) AS пар,
       (SELECT COUNT(*) FROM `favorite` WHERE `state` = 'already') AS уже_были,
       (SELECT COLUMN_TYPE FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite'
           AND COLUMN_NAME = 'state') AS перечисление;

SET @add = (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite'
               AND COLUMN_NAME = 'state' AND COLUMN_TYPE LIKE '%gone%');

SET @sql = IF(@add,
    "ALTER TABLE `favorite` MODIFY COLUMN `state`
         ENUM('done','already','fail','gone') NOT NULL DEFAULT 'fail'
         COMMENT 'Исход захода: gone — звезда погасла на сайте'",
    'SELECT ''значение уже есть'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `favorite`) AS пар,
       (SELECT COUNT(*) FROM `favorite` WHERE `state` = 'gone') AS погашено,
       (SELECT COLUMN_TYPE FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'favorite'
           AND COLUMN_NAME = 'state') AS перечисление;
