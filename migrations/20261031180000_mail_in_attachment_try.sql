-- Попытки описать присланный снимок: счётчик (2026-10-31)
--
-- ЗАЧЕМ. Ответ на письмо теперь **ждёт**, пока снимок не описан: агент должен
-- видеть, что ему показали, а не отвечать на голый текст. Поймано живьём
-- 31.10.2026: письмо легло в 15:40:53, круг ответов собрал вопрос в 15:41:07,
-- описание легло в 15:41:20 — на тринадцать секунд позже. Агент ответил,
-- описав снимок из **прошлого** письма: синюю футболку вместо клетчатой
-- рубашки.
--
-- ⚠⚠ НО ЖДАТЬ ВЕЧНО НЕЛЬЗЯ. Описание может не выйти никогда: файл пропал с
-- сайта, зрение отказало, кадр не читается как изображение. Без счётчика такое
-- письмо осталось бы без ответа навсегда — и молча: ни ошибки, ни строки в
-- журнале анкеты, просто собеседник не дождался.
--
-- Счётчик решает обе беды разом:
--
--   * очередь описания идёт **по нему**, и один негодный снимок не съедает
--     пачку заход за заходом — ровно та грабля, что уже случилась у галереи
--     (`described_try_at`, 80 заходов подряд на одних и тех же строках);
--   * исчерпав попытки, письмо уходит в ответ **без** описания: отклик без
--     снимка лучше молчания.
--
-- ⚠ `NOT NULL DEFAULT 0` — ноль значит «не пробовали», и это состояние всех
-- накопленных строк. NULL тут не нужен: «сколько раз пробовали» всегда
-- известно.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
           AND `COLUMN_NAME` = 'attachment_try') AS колонка_уже,
       (SELECT COUNT(*) FROM `mail_in` WHERE `attachment` <> ''
          AND `attachment_at` IS NULL) AS ждут_описания;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
                AND `COLUMN_NAME` = 'attachment_try');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mail_in`
     ADD COLUMN `attachment_try` TINYINT NOT NULL DEFAULT 0
       COMMENT ''Сколько раз пробовали описать снимок. Исчерпав попытки, письмо отвечается без описания''
       AFTER `attachment_at`',
  'SELECT ''колонка уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
           AND `COLUMN_NAME` = 'attachment_try') AS колонка_стала;
