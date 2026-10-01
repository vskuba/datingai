-- Какую именно фотографию разобрала модель (2026-10-01)
--
-- ## ЗАЧЕМ
--
-- Разбор снимка стоит вызова модели, и делается он **один раз на файл**.
-- Повторять его надо ровно тогда, когда снимок на Диске сменился.
--
-- Отличить «тот же файл» от «подменённого» умеет контрольная сумма: Диск отдаёт
-- `md5Checksum` прямо в списке, скачивать ради этого ничего не нужно. Но
-- колонка `md5` держит сумму **нынешнего** содержимого, и её одной мало:
-- сравнивать надо с той суммой, **которую разбирали**. Без второй колонки
-- вопрос «изменилось ли с прошлого разбора» задать нечем.
--
-- ## ⚠⚠ ЧЕМ ЭТО ОПАСНО И ЧЕМ ОПАСНОСТЬ СНЯТА
--
-- Накопленным строкам колонка достаётся пустой, и это **правильно**: у них
-- разбора ещё не было вовсе (таблица заведена сегодня же). Будь иначе — пустая
-- сумма у разобранной строки означала бы «разобрать заново», то есть лишний
-- вызов модели на каждую. Проверять тут нечего: `described_at` у всех `NULL`.
--
-- ## ⚠ Почему не сравнивать по `updated_at`
--
-- Время правки строки двигает **любой** обход: переименовали файл, переложили в
-- другую папку — и отметка свежее разбора, хотя снимок тот же. Сумма меняется
-- только вместе с содержимым, а именно оно модель и занимает.
--
-- Идемпотентность: колонка заводится по `information_schema` и подготовленным
-- запросом — второй прогон её видит и ничего не делает.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
           AND `COLUMN_NAME` = 'described_md5') AS колонка_уже,
       (SELECT COUNT(*) FROM `gallery_file`) AS строк,
       (SELECT COUNT(*) FROM `gallery_file` WHERE `described_at` IS NOT NULL) AS разобрано;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
                AND `COLUMN_NAME` = 'described_md5');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `gallery_file`
     ADD COLUMN `described_md5` varchar(64) NOT NULL DEFAULT ''''
       COMMENT ''Сумма того снимка, который разобрала модель. Разошлась с md5 — разбираем заново''
       AFTER `described_at`',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'gallery_file'
           AND `COLUMN_NAME` = 'described_md5') AS колонка_стала,
       (SELECT COUNT(*) FROM `gallery_file` WHERE `described_md5` <> '') AS с_суммой;
