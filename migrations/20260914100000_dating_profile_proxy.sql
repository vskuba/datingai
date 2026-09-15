-- Свой выход у анкеты: номер прокси в реестре соседа и его адрес (2026-09-14)
--
-- ЗАЧЕМ. Сайт знакомств считает учётки **по внешнему IP**: с одного адреса
-- пускают несколько девушек, дальше вход отвергается. Значит каждой анкете нужен
-- свой выход — и один и тот же во всех дверях: вход браузером у соседа,
-- `POST /auth/login` нашим HTTP, присутствие, вебсокет чата, отправка писем.
-- Разные адреса у одной учётки для сайта хуже, чем один общий: это выглядит как
-- угон, а не как несколько девушек.
--
-- ⚠ ПОЧЕМУ У НАС ТОЛЬКО НОМЕР И КЭШ, А РЕЕСТР — У СОСЕДА. Выходы заводят,
-- проверяют и раздают в RhapsodyAi (`/api/browser/proxy`): через тот же прокси
-- ходит его Chromium, и второй реестр здесь означал бы, что одна сторона не
-- знает, чем занята другая, — а лимит сайта у них общий.
--
-- ⚠ ПОЧЕМУ АДРЕС ВСЁ-ТАКИ ЛЕЖИТ ЗДЕСЬ, ХОТЯ ЭТО КОПИЯ. Присутствие бьёт каждые
-- полминуты, сокет живёт часами, круги идут непрерывно — спрашивать адрес у
-- соседа на каждый запрос значит поставить всю работу в зависимость от его
-- доступности. Его перезапуск гасил бы присутствие всех анкет разом. Поэтому
-- адрес кэшируется здесь, а хозяин у него по-прежнему один.
--
-- Цена размена: правка прокси у соседа доезжает сюда не мгновенно — кэш
-- перечитывается по сроку (`dating_proxy.py`). Для смены пароля канала это
-- минуты, и это приемлемо; для «выход умер» есть проверка на его стороне.
--
-- ЧТО ЗАВОДИТСЯ:
--
--   `proxy_id`  — номер в реестре соседа. 0 — выход ещё не закреплён;
--   `proxy_url` — он же строкой, с логином и паролем: тем, чем ходят клиенты;
--   `proxy_ip`  — каким адресом нас видят. Для глаз: по нему видно, что две
--                 анкеты сидят за одним IP, даже если прокси у них разные;
--   `proxy_at`  — когда адрес обновлялся последний раз.
--
-- ⚠ ПАРОЛЬ В `proxy_url` ЛЕЖИТ ОТКРЫТЫМ, как и пароль анкеты от сайта в соседней
-- колонке: им авторизуется машина. Наружу (`/api/dating/profile`) он не отдаётся
-- — страница получает `proxy_ip` и имя выхода, а не строку подключения.
--
-- Идемпотентность: каждая колонка заводится по условию отсутствия.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME LIKE 'proxy%') AS колонок_есть;

-- ------------------------------------------- Номер выхода
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'proxy_id') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `proxy_id` INT NOT NULL DEFAULT 0
            COMMENT ''Выход в реестре RhapsodyAi; 0 — не закреплён''
            AFTER `ws_url`',
    'SELECT ''dating_profile.proxy_id уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Адрес выхода
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'proxy_url') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `proxy_url` VARCHAR(500) NOT NULL DEFAULT ''''
            COMMENT ''Кэш адреса с паролем: им ходят наши клиенты. Хозяин — реестр соседа''
            AFTER `proxy_id`',
    'SELECT ''dating_profile.proxy_url уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Увиденный адрес
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'proxy_ip') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `proxy_ip` VARCHAR(45) NOT NULL DEFAULT ''''
            COMMENT ''Каким адресом нас видит сайт — для глаз и для разбора''
            AFTER `proxy_url`',
    'SELECT ''dating_profile.proxy_ip уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Когда обновляли
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
        AND COLUMN_NAME = 'proxy_at') = 0,
    'ALTER TABLE `dating_profile`
        ADD COLUMN `proxy_at` DATETIME NULL
            COMMENT ''Когда адрес выхода читался у соседа последний раз''
            AFTER `proxy_ip`',
    'SELECT ''dating_profile.proxy_at уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
           AND COLUMN_NAME LIKE 'proxy%') AS колонок_есть,
       (SELECT COUNT(*) FROM `dating_profile` WHERE `proxy_id` > 0) AS анкет_с_выходом;
