-- Письма-исполнения намерений: девушка обещала фото — вот оно (2026-10-30)
--
-- ЗАЧЕМ. Агент помечает обещания (`chat_intention`), контекст к ним собирает
-- подпоток соседа: текст письма и приметы снимка. Исполнять их было нечем —
-- занятия не существовало вовсе, и пул рос, а колонка «Сделано» была пуста
-- всегда. Эта таблица — пул того занятия: черновик письма со снимком,
-- выбранным по приметам, ждёт отправки.
--
-- ⚠⚠ ПОЧЕМУ СВОЯ ТАБЛИЦА, А НЕ ЧУЖОЙ ПУЛ. Оба соседних не подошли, и по
-- разным причинам:
--
--   * `mail_reply` — ключ `mail_in_id` UNIQUE NOT NULL: ответ привязан к
--     входящему письму, а обещание в чате входящего не создаёт;
--   * `favorite_mail` — пул **холодных** писем (`MAIL_SEND_COLD_TABLES`), и
--     его письма считает дневной предел получателя. Письмо по обещанию
--     холодным не является: о нём просили. Положи мы его туда, оно съедало бы
--     предел настоящего приставания — та же беда, из-за которой `mail_reply`
--     в холодные не входит.
--
-- ⚠⚠ ЗАТО В `MAIL_SEND_TABLES` ВОЙТИ ОБЯЗАНО. Этот список отвечает не на
-- вопрос «куда писать исход», а на «что ушло живому человеку»: по нему
-- считаются пауза отправительницы и «сколько мы ему написали». Не войди
-- таблица туда — девушка слала бы письмо по обещанию поверх только что
-- ушедшего, и оба предела разошлись бы молча.
--
-- ⚠ Ключ `uq_intention_mail_once` — ОДИН черновик на намерение. Круг
-- просыпается каждую минуту, и без ключа он сочинял бы письмо на каждом тике,
-- пока человек не нажмёт «отправить».
--
-- ⚠ `gallery_file_id` хранится, хотя отбор считается заново: между сочинением
-- и отправкой проходят минуты, и человек должен видеть на карточке **тот**
-- снимок, который уйдёт, а не свежий ответ отбора.
--
-- ⚠ Внешних ключей нет — как и у соседних пулов (`database.md`): анкеты и
-- сайты живут своей жизнью, а каскад уносил бы переписку вместе с ними.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`TABLES`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'intention_mail') AS таблица_уже,
       (SELECT COUNT(*) FROM `chat_intention` WHERE `status` = 'open') AS открытых;

CREATE TABLE IF NOT EXISTS `intention_mail` (
  `id`              INT NOT NULL AUTO_INCREMENT,
  `site_id`         INT NOT NULL,
  `profile_id`      INT NOT NULL,
  `intention_id`    INT NOT NULL
      COMMENT 'Какое обещание исполняем — строка chat_intention',
  `uid`             VARCHAR(32) NOT NULL,
  `name`            VARCHAR(190) NOT NULL DEFAULT '',
  `photo`           VARCHAR(500) NOT NULL DEFAULT ''
      COMMENT 'Лицо собеседника для показа на карточке',
  `gallery_file_id` INT NOT NULL DEFAULT 0
      COMMENT 'Какой снимок уйдёт: строка gallery_file. Ноль — письмо без снимка',
  `body`            TEXT NOT NULL,
  `status`          ENUM('draft','sent','failed') NOT NULL DEFAULT 'draft',
  `error`           VARCHAR(500) NOT NULL DEFAULT '',
  `answer`          VARCHAR(500) NOT NULL DEFAULT ''
      COMMENT 'Что ответил сайт — как есть, нашим выводом это не подменяется',
  `sent_at`         DATETIME NULL,
  `created_at`      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  -- ⚠ Умолчания нет и быть не должно: у неудачного письма это время отказа, и
  -- `DEFAULT CURRENT_TIMESTAMP` проставил бы накопленным строкам время
  -- миграции — «Доставка» показала бы вчерашнюю неудачу сегодняшней.
  `updated_at`      DATETIME NULL ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_intention_mail_once` (`intention_id`),
  KEY `ix_intention_mail_site` (`site_id`, `status`),
  KEY `ix_intention_mail_profile` (`profile_id`, `uid`),
  KEY `ix_intention_mail_sent` (`sent_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Пул писем-исполнений: снимок, обещанный агентом в разговоре';

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`TABLES`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `TABLE_NAME` = 'intention_mail') AS таблица_стала,
       (SELECT COUNT(*) FROM `intention_mail`) AS строк;
