-- Один шаблон рассылки уходит человеку ровно раз — кем бы ни была девушка.
--
-- ЧТО БЫЛО. Ключ стоял на тройке «анкета — адресат — шаблон»: не больше письма
-- от **этой** анкеты **этому** человеку **этим** шаблоном. Разным девушкам он не
-- мешал — тройки-то разные, — и один и тот же текст мог прийти человеку дважды:
-- от Дианы и от Екатерины.
--
-- Внутри одного захода этого не случалось: каждый адресат получает одно письмо за
-- проход. Но заходов много — круг добирает пул по расписанию, — и на следующем
-- человек снова оказывался свободен, только уже для другой анкеты.
--
-- ⚠⚠ СО СТОРОНЫ ПОЛУЧАТЕЛЯ ЭТО ВЫДАННОЕ АГЕНТСТВО: два одинаковых письма от двух
-- незнакомок. Тот же довод, по которому собеседники уникальны в пуле инвайтов.
--
-- ЧТО СТАЛО. Ключ — «сайт — адресат — шаблон». Правило в коде стоит там же
-- (`mailing._pairs`, набор `spent`), а ключ страхует от гонки: кнопку жмут поверх
-- работающего круга.
--
-- ⚠ Разные шаблоны от разных девушек по-прежнему можно: у них разный текст, и
-- второе письмо читается как ещё одно знакомство, а не как копия.
--
-- ⚠ `mail_id` бывает `NULL` — так ключ на `mail` гасит ссылку, когда шаблон
-- удаляют. В уникальном ключе MySQL `NULL` не конфликтует сам с собой, и осиротевшие
-- письма правилу не мешают.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mailing'
           AND INDEX_NAME = 'uq_dating_mailing_once') AS старый_ключ,
       (SELECT COUNT(*) FROM (
          SELECT uid, mail_id FROM mailing WHERE mail_id IS NOT NULL
           GROUP BY site_id, uid, mail_id HAVING COUNT(DISTINCT profile_id) > 1) t)
         AS нарушений_в_данных;

-- ⚠ Нарушения убираем ДО смены ключа, иначе он не создастся. Оставляем самое
-- раннее письмо пары «человек — шаблон»: оно либо уже ушло, либо стоит в пуле
-- первым, и человек увидит именно его.
DELETE d FROM mailing d
  JOIN (SELECT site_id, uid, mail_id, MIN(id) AS keep_id
          FROM mailing WHERE mail_id IS NOT NULL
         GROUP BY site_id, uid, mail_id
        HAVING COUNT(*) > 1) k
    ON k.site_id = d.site_id AND k.uid = d.uid AND k.mail_id = d.mail_id
 WHERE d.id > k.keep_id;

-- ⚠⚠ Сперва заводим индекс под внешний ключ, и только потом снимаем старый.
--
-- `fk_dmailing_profile` держится за `uq_dating_mailing_once`: тот начинается с
-- `profile_id`, и MySQL считает его индексом внешнего ключа. Сними его первым — и
-- `Cannot drop index: needed in a foreign key constraint`. Поймано на первом же
-- прогоне; порядок здесь не косметический.
ALTER TABLE `mailing` ADD KEY `idx_mailing_profile` (`profile_id`);

ALTER TABLE `mailing`
  ADD UNIQUE KEY `uq_mailing_once` (`site_id`, `uid`, `mail_id`);

ALTER TABLE `mailing` DROP INDEX `uq_dating_mailing_once`;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mailing'
           AND INDEX_NAME = 'uq_mailing_once') AS столбцов_в_новом_ключе,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mailing'
           AND INDEX_NAME = 'uq_dating_mailing_once') AS старый_остался;
