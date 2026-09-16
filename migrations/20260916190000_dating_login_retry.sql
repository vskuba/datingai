-- Счётчик неудачных входов у анкеты (2026-09-16)
--
-- ЗАЧЕМ. На проде анкета #41 входила каждые 86 секунд без остановки: сценарий
-- проходил форму, сайт кабинет не подтверждал, круг расписания пробовал снова.
-- Девять входов за четырнадцать минут — ровно то, за что сайт закрывает вход по
-- внешнему адресу, и ровно та беда, из-за которой мы уже сутки просидели в
-- блокировке Ladadate.
--
-- ⚠ ЛЕСТНИЦА ОТКАТА ЭТОГО НЕ ЛОВИЛА: её спрашивает круг восстановления, а входил
-- круг расписания — прямым вызовом, минуя правила. Поэтому счётчик кладётся в
-- анкету и проверяется у самой двери (`dating_login_run`), а не в одном из кругов.
--
-- ⚠ ПОЧЕМУ НЕ `relogin_tries`, КОТОРЫЙ УЖЕ ЕСТЬ. Тот про другое: он ведёт паузы
-- автовосстановления, включая **плановые** входы по возрасту сессии, и гаснет по
-- своим правилам. Здесь считаются только отказы сайта, и гасит их лишь удавшийся
-- вход. Свести их в одно поле — это ровно та экономия, из которой в
-- `dating_revive.py` уже выросли две поломки подряд.
--
-- Идемпотентность: колонки заводятся по условию отсутствия.

SELECT 'ДО' AS этап,
       SUM(COLUMN_NAME = 'login_fail_tries') AS есть_счётчик,
       SUM(COLUMN_NAME = 'login_fail_at') AS есть_время
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile';

-- ------------------------------------------- Счётчик неудач
SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
                  AND COLUMN_NAME = 'login_fail_tries') = 0,
              'ALTER TABLE `dating_profile` ADD COLUMN `login_fail_tries` INT NOT NULL DEFAULT 0'
              ' COMMENT ''Неудачных входов подряд; гасит только удавшийся вход''',
              'SELECT ''колонка login_fail_tries уже есть''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ------------------------------------------- Когда была последняя
SET @sql = IF((SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile'
                  AND COLUMN_NAME = 'login_fail_at') = 0,
              'ALTER TABLE `dating_profile` ADD COLUMN `login_fail_at` DATETIME NULL'
              ' COMMENT ''Когда вход не вышел последний раз: от неё отсчитывается пауза''',
              'SELECT ''колонка login_fail_at уже есть''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       SUM(COLUMN_NAME = 'login_fail_tries') AS есть_счётчик,
       SUM(COLUMN_NAME = 'login_fail_at') AS есть_время
  FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_profile';
