-- Диалоги чата и фаворитов — снимок прода (2026-09-25)
--
-- ## ⚠⚠ ЭТО НЕ МИГРАЦИЯ, а разовая загрузка данных
--
-- Лежит в `migrations/local/`, и подпапку `yoyo` не обходит — проверено опытом.
-- Это предохранитель: ниже стоят `DELETE`, и попади файл в общую очередь, первая
-- же выкладка снесла бы переписку прода.
--
-- ## Зачем
--
-- «Чат → Диалоги» и «Фавориты → Диалоги» локально показывали тестовый мусор: 25
-- тысяч реплик от сюиты и одну запись избранного. Проверять на них вид страницы
-- нельзя — ни длинных разговоров, ни переписки письмами, ни отказов.
--
-- Теперь там то же, что на проде: 42 289 реплик, 147 избранных, 47 писем.
--
-- ## ⚠⚠ Номера свои, тексты прода
--
-- Анкеты сопоставляются **по логину**, а не по номеру: у прода Diana — это
-- `id = 40`, локально она же `849`. Перенос переводит `profile_id` на лету, и
-- локальные номера остаются своими.
--
--     прод 40  bogiladiana9021@bigmir.net  →  локально 849
--     прод 43  zliznukolenavlad89@…        →  локально 852
--     …тринадцать анкет Ladadate
--
-- ⚠ Двух анкет локально нет вовсе, и их надо **завести**: `1430354` (Renata,
-- Goldenbride) и `1439074` (мужская учётка). Без них 42 141 реплика из 42 289
-- никуда не легла бы — а это девять десятых всей переписки.
--
-- ## ⚠ Собеседник узнаётся по паре «сайт + номер»
--
-- `companion` и `contact` ключуются не номером строки, а `site_id` + `uid`.
-- Поэтому переносятся `INSERT … ON DUPLICATE KEY UPDATE`: локальная запись того
-- же человека обновляется прод-данными, чужая остаётся.
--
-- ## ⚠⚠ Чего этот файл НЕ трогает
--
--     стенд сюиты      site_id = 999343 — свои анкеты и 19 реплик
--     посев для тестов  login LIKE 'seed-%' — 40 анкет на сайте 19
--
-- ⚠⚠ **Про посев я узнал не сразу, и это стоило шести красных тестов.** Первая
-- правка чистила по сайту целиком — а `seed-000-big@example.com` живёт на сайте
-- **19**, рядом с боевыми, и на его 600 собеседницах стоит `test_contacts_sort`.
-- Снёс их — и шесть тестов упали на пустых страницах.
--
-- Отсюда правило: отбор по сайту **недостаточен**, нужен ещё признак посева. На
-- боевом сайте живут и настоящие анкеты, и заготовки сюиты.

SET FOREIGN_KEY_CHECKS = 0;

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `message`) AS реплик,
       (SELECT COUNT(*) FROM `favorite`) AS избранных,
       (SELECT COUNT(*) FROM `favorite_mail`) AS писем_избранным,
       (SELECT COUNT(*) FROM `companion`) AS собеседников;

-- ── Две анкеты, которых локально нет ────────────────────────────────────────
--
-- ⚠ Номер отдаёт база: свой, не прод-овый. Дальше он находится по логину, как и
-- у остальных тринадцати, — то есть разницы между «заведённой» и «сопоставленной»
-- анкетой ниже уже нет.
--
-- ⚠⚠ `enabled = 0` и `status = 'offline'` намеренно: это данные для **просмотра**
-- страницы, а не для работы. Включённая анкета попала бы в круги воркера, и тот
-- пошёл бы входить на живой сайт под чужой учёткой.
INSERT INTO `profile` (`site_id`, `login`, `password`, `role`, `enabled`,
                       `can_work`, `status`, `profile`, `note`)
SELECT p.`site_id`, p.`login`, '', p.`role`, 0, 0, 'offline', p.`profile`,
       'снимок прода: только для просмотра диалогов'
  FROM `dating_prod`.`profile` p
 WHERE NOT EXISTS (SELECT 1 FROM (SELECT * FROM `profile`) l
                    WHERE l.`login` = p.`login`);

-- ── Чистим диалоги боевых сайтов ────────────────────────────────────────────
--
-- ⚠⚠ Только 19 и 343. Стенд сюиты (`999343`) не трогаем: на его анкетах стоят
-- тесты, и снос уронил бы прогон целиком.
DELETE m FROM `message` m
  JOIN `profile` p ON p.`id` = m.`profile_id`
 WHERE p.`site_id` IN (19, 343) AND p.`login` NOT LIKE 'seed-%';

DELETE x FROM `favorite` x
  JOIN `profile` p ON p.`id` = x.`profile_id`
 WHERE x.`site_id` IN (19, 343) AND p.`login` NOT LIKE 'seed-%';
DELETE x FROM `favorite_mail` x
  JOIN `profile` p ON p.`id` = x.`profile_id`
 WHERE x.`site_id` IN (19, 343) AND p.`login` NOT LIKE 'seed-%';
DELETE x FROM `mail_reply` x
  JOIN `profile` p ON p.`id` = x.`profile_id`
 WHERE x.`site_id` IN (19, 343) AND p.`login` NOT LIKE 'seed-%';
DELETE x FROM `mail_in` x
  JOIN `profile` p ON p.`id` = x.`profile_id`
 WHERE x.`site_id` IN (19, 343) AND p.`login` NOT LIKE 'seed-%';
DELETE x FROM `mailing` x
  JOIN `profile` p ON p.`id` = x.`profile_id`
 WHERE x.`site_id` IN (19, 343) AND p.`login` NOT LIKE 'seed-%';

DELETE c FROM `contact` c
  JOIN `profile` p ON p.`id` = c.`profile_id`
 WHERE p.`site_id` IN (19, 343) AND p.`login` NOT LIKE 'seed-%';

-- ── Реплики ─────────────────────────────────────────────────────────────────
--
-- ⚠ `profile_id` переводится подзапросом по логину. Не сойдись логин — строка
-- просто не поедет (`JOIN`), и это видно по счёту в отчёте.
--
-- ⚠⚠ `id` не переносим вовсе: у локальной таблицы своя нумерация, а уникальность
-- реплики держит пара `profile_id` + `message_uid`.
INSERT INTO `message` (`profile_id`, `sender_uid`, `sender_name`, `text`,
                       `message_uid`, `is_read`, `is_answered`, `answer_tried_at`,
                       `answer_silent_at`, `direction`, `created_at`, `source`)
SELECT l.`id`, m.`sender_uid`, m.`sender_name`, m.`text`, m.`message_uid`,
       m.`is_read`, m.`is_answered`, m.`answer_tried_at`, m.`answer_silent_at`,
       m.`direction`, m.`created_at`, m.`source`
  FROM `dating_prod`.`message` m
  JOIN `dating_prod`.`profile` pp ON pp.`id` = m.`profile_id`
  JOIN `profile` l ON l.`login` = pp.`login`;

-- ── Избранные и письма им ───────────────────────────────────────────────────
INSERT INTO `favorite` (`site_id`, `profile_id`, `uid`, `state`, `error`, `tries`,
                        `reason`, `created_at`, `updated_at`)
SELECT f.`site_id`, l.`id`, f.`uid`, f.`state`, f.`error`, f.`tries`, f.`reason`,
       f.`created_at`, f.`updated_at`
  FROM `dating_prod`.`favorite` f
  JOIN `dating_prod`.`profile` pp ON pp.`id` = f.`profile_id`
  JOIN `profile` l ON l.`login` = pp.`login`;

INSERT INTO `favorite_mail` (`site_id`, `profile_id`, `uid`, `name`, `photo`,
                             `body`, `status`, `error`, `answer`, `sent_at`,
                             `created_at`, `updated_at`)
SELECT f.`site_id`, l.`id`, f.`uid`, f.`name`, f.`photo`, f.`body`, f.`status`,
       f.`error`, f.`answer`, f.`sent_at`, f.`created_at`, f.`updated_at`
  FROM `dating_prod`.`favorite_mail` f
  JOIN `dating_prod`.`profile` pp ON pp.`id` = f.`profile_id`
  JOIN `profile` l ON l.`login` = pp.`login`;

-- ── Почта ───────────────────────────────────────────────────────────────────
--
-- ⚠⚠ Порядок: `mail_in` раньше `mail_reply`. Ответ ведёт на входящее письмо
-- внешним ключом, и его новый номер берётся по паре `profile_id` + `message_id`
-- — та же уникальность, что держит таблицу.
INSERT INTO `mail_in` (`site_id`, `profile_id`, `message_id`, `uid`, `name`,
                       `photo`, `age`, `city`, `country`, `body`, `date_post`,
                       `is_replied`, `seen`, `created_at`)
SELECT i.`site_id`, l.`id`, i.`message_id`, i.`uid`, i.`name`, i.`photo`, i.`age`,
       i.`city`, i.`country`, i.`body`, i.`date_post`, i.`is_replied`, i.`seen`,
       i.`created_at`
  FROM `dating_prod`.`mail_in` i
  JOIN `dating_prod`.`profile` pp ON pp.`id` = i.`profile_id`
  JOIN `profile` l ON l.`login` = pp.`login`;

INSERT INTO `mail_reply` (`site_id`, `profile_id`, `mail_in_id`, `uid`, `name`,
                          `body`, `status`, `error`, `answer`, `sent_at`,
                          `created_at`)
SELECT r.`site_id`, l.`id`, li.`id`, r.`uid`, r.`name`, r.`body`, r.`status`,
       r.`error`, r.`answer`, r.`sent_at`, r.`created_at`
  FROM `dating_prod`.`mail_reply` r
  JOIN `dating_prod`.`profile` pp ON pp.`id` = r.`profile_id`
  JOIN `profile` l ON l.`login` = pp.`login`
  JOIN `dating_prod`.`mail_in` pi ON pi.`id` = r.`mail_in_id`
  JOIN `mail_in` li ON li.`profile_id` = l.`id`
                   AND li.`message_id` = pi.`message_id`;

INSERT INTO `mailing` (`site_id`, `profile_id`, `mail_id`, `uid`, `name`, `body`,
                       `status`, `created_at`, `error`, `answer`, `sent_at`)
SELECT g.`site_id`, l.`id`, g.`mail_id`, g.`uid`, g.`name`, g.`body`, g.`status`,
       g.`created_at`, g.`error`, g.`answer`, g.`sent_at`
  FROM `dating_prod`.`mailing` g
  JOIN `dating_prod`.`profile` pp ON pp.`id` = g.`profile_id`
  JOIN `profile` l ON l.`login` = pp.`login`;

-- ── Люди: карточки и лента ──────────────────────────────────────────────────
--
-- ⚠ Собеседник узнаётся по паре «сайт + номер», а не по номеру строки: имя и
-- фото в диалоге берутся оттуда. Своего человека обновляем прод-данными, чужих
-- (стенд сюиты) оставляем.
INSERT INTO `companion` (`site_id`, `uid`, `name`, `photo`, `age`, `city`,
                         `country`, `email`, `priority`, `about`, `about_state`,
                         `about_error`, `about_at`, `created_at`, `updated_at`)
SELECT c.`site_id`, c.`uid`, c.`name`, c.`photo`, c.`age`, c.`city`, c.`country`,
       c.`email`, c.`priority`, c.`about`, c.`about_state`, c.`about_error`,
       c.`about_at`, c.`created_at`, c.`updated_at`
  FROM `dating_prod`.`companion` c
    ON DUPLICATE KEY UPDATE
       `name` = VALUES(`name`), `photo` = VALUES(`photo`), `age` = VALUES(`age`),
       `city` = VALUES(`city`), `country` = VALUES(`country`),
       `email` = VALUES(`email`), `about` = VALUES(`about`),
       `about_state` = VALUES(`about_state`), `about_at` = VALUES(`about_at`);

INSERT INTO `contact` (`profile_id`, `uid`, `name`, `photo`, `age`, `city`,
                       `is_online`, `updated_at`)
SELECT l.`id`, c.`uid`, c.`name`, c.`photo`, c.`age`, c.`city`, c.`is_online`,
       c.`updated_at`
  FROM `dating_prod`.`contact` c
  JOIN `dating_prod`.`profile` pp ON pp.`id` = c.`profile_id`
  JOIN `profile` l ON l.`login` = pp.`login`
    ON DUPLICATE KEY UPDATE
       `name` = VALUES(`name`), `photo` = VALUES(`photo`), `age` = VALUES(`age`),
       `city` = VALUES(`city`), `is_online` = VALUES(`is_online`);

SET FOREIGN_KEY_CHECKS = 1;

SELECT 'ПОСЛЕ' AS этап,
       -- ⚠⚠ Посев обязан уцелеть: на его собеседницах стоит `test_contacts_sort`,
       -- и пустой список ронял шесть тестов на пустых страницах.
       (SELECT COUNT(*) FROM `contact` c
          JOIN `profile` p ON p.`id` = c.`profile_id`
         WHERE p.`login` LIKE 'seed-%') AS собеседниц_у_посева,
       (SELECT COUNT(*) FROM `message`) AS реплик,
       (SELECT COUNT(*) FROM `favorite`) AS избранных,
       (SELECT COUNT(*) FROM `favorite_mail`) AS писем_избранным,
       (SELECT COUNT(*) FROM `companion`) AS собеседников;

-- ⚠⚠ Главная проверка: столько же реплик, сколько на проде, и **ни одна не
-- потерялась на переводе номера**. Разойдись счёт — значит логин не сошёлся, и
-- чей-то диалог не доехал.
SELECT 'СВЕРКА' AS этап,
       (SELECT COUNT(*) FROM `dating_prod`.`message`) AS на_проде,
       (SELECT COUNT(*) FROM `message` m JOIN `profile` p ON p.`id` = m.`profile_id`
         WHERE p.`site_id` IN (19, 343)) AS перенесено,
       (SELECT COUNT(*) FROM `message` m JOIN `profile` p ON p.`id` = m.`profile_id`
         WHERE p.`site_id` = 999343) AS осталось_у_стенда,
       -- ⚠ Пар «анкета + собеседник» столько же: это и есть строки списка диалогов.
       (SELECT COUNT(*) FROM (SELECT 1 FROM `dating_prod`.`message`
                               GROUP BY `profile_id`, `sender_uid`) d) AS пар_на_проде,
       (SELECT COUNT(*) FROM (
          SELECT 1 FROM `message` m JOIN `profile` p ON p.`id` = m.`profile_id`
           WHERE p.`site_id` IN (19, 343)
           GROUP BY m.`profile_id`, m.`sender_uid`) d) AS пар_локально;
