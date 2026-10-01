-- Выбранная папка галереи у анкеты (2026-10-01)
--
-- ## ЗАЧЕМ (указание человека)
--
-- На вкладке «Галерея» человек привязывает к анкете папку с Диска — **выбирая
-- из списка существующих**, а не вписывая имя руками. Выбранное надо где-то
-- держать: это колонка.
--
-- ## ⚠⚠ Пусто — обычный случай, а не пробел в данных
--
-- Папки девушек на Диске названы **логином анкеты**: проверено на живых
-- данных — `bogiladiana9021@bigmir.net` это `profile.login` анкеты 849.
-- Поэтому пустая колонка значит «как у всех», и показ подставляет логин сам.
-- Вписывают сюда исключение: папку, названную иначе.
--
-- Отсюда и `DEFAULT ''` вместо `NULL`: у «не выбрано» и «выбрано пустое» нет
-- разных смыслов, и различать их значило бы заводить состояние, которого нет.
--
-- ## ⚠ Писать колонку будет одна дверь
--
-- Ручка галереи, и только она: через общий `PUT /api/dating/profile` поле не
-- принимается. Две двери записали бы разные имена, и вкладка показывала бы
-- папку, которую человек уже сменил. Тот же приём, что у карточки собеседника
-- (`companion_card.py`).
--
-- Идемпотентность: колонка заводится по `information_schema` и подготовленным
-- запросом — второй прогон её видит и ничего не делает.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'profile'
           AND `COLUMN_NAME` = 'gallery_folder') AS колонка_уже,
       (SELECT COUNT(*) FROM `profile`) AS анкет;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'profile'
                AND `COLUMN_NAME` = 'gallery_folder');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `profile`
     ADD COLUMN `gallery_folder` varchar(255) NOT NULL DEFAULT ''''
       COMMENT ''Папка девушки на Диске, выбранная из списка. Пусто — берём логин анкеты''',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'profile'
           AND `COLUMN_NAME` = 'gallery_folder') AS колонка_стала,
       (SELECT COUNT(*) FROM `profile` WHERE `gallery_folder` <> '') AS с_выбранной_папкой;
