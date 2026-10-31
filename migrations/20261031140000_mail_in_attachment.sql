-- Вложение входящего письма: снимок, который прислал он (2026-10-31)
--
-- ЗАЧЕМ. Собеседник присылает фотографию письмом, а мы этого не видели вовсе:
-- забор читал из ответа сайта `Body` и отправителя, а поле `Attachment`
-- выбрасывал. Следствие тяжелее, чем кажется, — **агент тоже его не видел**:
-- человек прислал снимок, а девушка отвечает так, будто ничего не приходило.
-- На проде это уже случилось: 13.10.2026 Mike написал Diana «I send you
-- photo», а следом — «oh no...I didn't get any photo».
--
-- ⚠⚠ ССЫЛКА, КОТОРУЮ ОТДАЁТ САЙТ, НЕ РАБОТАЕТ. `Attachment` приходит в виде
-- `https://ladadate.com/content/attachments/<файл>.jpg`, и такой адрес
-- отвечает `404` — и без cookie, и под сессией получательницы. Настоящий путь
-- несёт **номер отправителя**:
-- `/content/attachments/<его uid>/<файл>.jpg`. Проверено на живом письме
-- (Jann Kränzlin → STELA, 29.10): 404 против 200 и 37 КБ JPEG.
--
-- Поэтому в колонке лежит адрес **исправленный** — тот, по которому файл
-- вправду отдаётся. Хранить присланный значило бы хранить заведомо неживую
-- ссылку и чинить её у каждого читателя заново.
--
-- ⚠ Описание лежит рядом и по-английски, как `companion_about` и описания
-- галереи: его читает агент в промпте, и перевод в этом месте был бы вторым
-- состоянием одного текста.
--
-- ⚠ `attachment_at` — когда описали, а не когда получили: по нему видно, что
-- снимок уже проходил через модель. NULL значит «ещё не описан», и это
-- законное состояние — забор кладёт письмо сразу, а описание делается следом.
-- Умолчания у него нет намеренно: `DEFAULT CURRENT_TIMESTAMP` проставил бы
-- всем накопленным строкам время миграции, то есть соврал бы «описаны».

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
           AND `COLUMN_NAME` = 'attachment') AS колонка_уже,
       (SELECT COUNT(*) FROM `mail_in`) AS писем;

SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
                AND `COLUMN_NAME` = 'attachment');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mail_in`
     ADD COLUMN `attachment` VARCHAR(500) NOT NULL DEFAULT ''''
       COMMENT ''Адрес снимка, который он прислал — УЖЕ с номером отправителя в пути. Пусто — вложения не было''
       AFTER `body`,
     ADD COLUMN `attachment_about` TEXT NULL
       COMMENT ''Что на снимке — словами, по-английски: это читает агент в промпте''
       AFTER `attachment`,
     ADD COLUMN `attachment_at` DATETIME NULL
       COMMENT ''Когда снимок описали. NULL — ещё не описан''
       AFTER `attachment_about`',
  'SELECT ''колонки уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'mail_in'
           AND `COLUMN_NAME` IN ('attachment', 'attachment_about',
                                 'attachment_at')) AS колонок_стало,
       (SELECT COUNT(*) FROM `mail_in` WHERE `attachment` <> '') AS со_снимком;
