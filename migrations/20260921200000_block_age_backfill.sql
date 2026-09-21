-- Отказы по возрасту, случившиеся до появления приметы (2026-09-21)
--
-- ЗАЧЕМ. Примета «вы не подходите ему по возрасту» заведена сегодня, а отказы по
-- ней сайт давал и раньше. Для тех писем код ещё не знал этого слова: письмо
-- осталось черновиком в пуле, запрет не завёлся, и вкладка «Фавориты → Не пишем»
-- стояла пустой при живом отказе в «Доставке».
--
-- ⚠⚠ ХУЖЕ ТОГО, ЧЕРНОВИК ОСТАЛСЯ РАБОЧИМ. Круг отправки берёт его каждый заход,
-- получает тот же отказ и снова кладёт обратно. Письмо, которое сайт не примет
-- никогда, стоит нам вызова LLM при каждом наборе пула.
--
-- ЧТО ДЕЛАЕМ, И ПОЧЕМУ ИМЕННО В ЭТОМ ПОРЯДКЕ:
--
--   1. хороним письмо (`failed`) — и только поэтому отказ остаётся видно в
--      «Доставке». Заведи мы запрет первым, чистка пулов снесла бы черновик, и
--      причина исчезла бы вместе с ним;
--   2. заводим запрет с каналом `mail`: отказ про письма, чат он не трогает.
--
-- ⚠ ТОЛЬКО ПО ВОЗРАСТУ И НИЧЕГО БОЛЬШЕ. Остальные красные строки доставки — это
-- часовой лимит получателя (`ERROR_MESSAGE_USER_HOUR_IN_LIMIT`), и он ВРЕМЕННЫЙ:
-- пять входящих в час, считая письма чужих агентств. Занеси мы их в «не пишем» —
-- потеряли бы людей навсегда из-за чужой активности, а лимит отпускает через час.
-- Их черновики намеренно остаются в пуле: следующий заход пройдёт.
--
-- Идемпотентность: `INSERT … WHERE NOT EXISTS` и `status = 'draft'` в условии.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `favorite_mail`
         WHERE `error` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%' AND `status` = 'draft')
         AS черновиков_по_возрасту,
       (SELECT COUNT(*) FROM `block` WHERE `channel` = 'mail') AS запретов_почты;

-- ── 1. Хороним письма ───────────────────────────────────────────────────────
UPDATE `favorite_mail` SET `status` = 'failed'
 WHERE `error` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%' AND `status` = 'draft';

UPDATE `mailing` SET `status` = 'failed'
 WHERE `error` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%' AND `status` = 'draft';

UPDATE `mail_reply` SET `status` = 'failed'
 WHERE `error` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%' AND `status` = 'draft';

-- ── 2. Заводим запреты ──────────────────────────────────────────────────────
--
-- ⚠ Три пула одним заходом: отказ приходит на любой из них, а запрет один.
INSERT INTO `block` (`site_id`, `profile_id`, `uid`, `name`, `reason`, `source`,
                     `channel`)
SELECT m.`site_id`, m.`profile_id`, m.`uid`, m.`name`, m.`error`, 'mail', 'mail'
  FROM (
        SELECT `site_id`, `profile_id`, `uid`, `name`, `error` FROM `favorite_mail`
         WHERE `error` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%'
        UNION
        SELECT `site_id`, `profile_id`, `uid`, `name`, `error` FROM `mailing`
         WHERE `error` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%'
        UNION
        SELECT `site_id`, `profile_id`, `uid`, `name`, `error` FROM `mail_reply`
         WHERE `error` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%'
       ) AS m
 WHERE NOT EXISTS (SELECT 1 FROM `block` b
                    WHERE b.`profile_id` = m.`profile_id` AND b.`uid` = m.`uid`);

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `favorite_mail`
         WHERE `error` LIKE '%ERROR_MESSAGE_AGE_CRITERIA%' AND `status` = 'draft')
         AS черновиков_осталось,
       (SELECT COUNT(*) FROM `block` WHERE `channel` = 'mail') AS запретов_почты,
       (SELECT COUNT(*) FROM `block` WHERE `channel` = 'all')  AS запретов_общих;
