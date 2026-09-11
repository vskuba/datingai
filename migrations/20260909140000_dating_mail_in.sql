-- Входящие письма анкет (2026-09-09)
--
-- Письма, которые пишут **нашим девушкам**. Забираются через API сайта
-- (`GET /messages`), лежат здесь и здесь же помечаются просмотренными.
--
-- ⚠ ПОЧЕМУ ХРАНИМ У СЕБЯ, А НЕ СПРАШИВАЕМ КАЖДЫЙ РАЗ. Две причины, и обе решающие.
--
-- Первая: **у сайта нет признака «прочитано»**. В его ответе есть `IsReplied` —
-- «ответили ли», а это другой вопрос: письмо можно прочесть и не ответить, и такое
-- письмо для нас уже не новое. Свой флаг хранить негде, кроме как у себя.
--
-- Вторая: **текст письма приходит только поштучно**. Список (`GET /messages`) отдаёт
-- метаданные без тела, тело — `GET /messages/{id}`. Сорок писем это сорок запросов;
-- спрашивай мы их на каждое открытие вкладки, страница открывалась бы полминуты.
-- Забрали раз — читаем сколько угодно.
--
-- ⚠ `message_id` — НОМЕР САЙТА, и уникальность по паре с анкетой. У разных анкет
-- номера свои: это счётчик в чужой системе, и совпадения между учётками законны.
--
-- ⚠ `profile_id` с каскадом: удалили анкету — её переписка уходит с ней. То же
-- правило, что у чата и у пула писем.

CREATE TABLE IF NOT EXISTS `dating_mail_in` (
    `id`         INT NOT NULL AUTO_INCREMENT,
    `site_id`    INT NOT NULL COMMENT 'Сайт: отбор на странице идёт по нему',
    `profile_id` INT NOT NULL COMMENT 'Кому написали: наша анкета',
    `message_id` BIGINT NOT NULL COMMENT 'Номер письма на сайте',

    `uid`     VARCHAR(32)  NOT NULL DEFAULT '' COMMENT 'Кто написал: номер на сайте',
    `name`    VARCHAR(190) NOT NULL DEFAULT '' COMMENT 'Имя отправителя на момент письма',
    `photo`   VARCHAR(500) NOT NULL DEFAULT '' COMMENT 'Его фотография: список читают лицами',
    `age`     INT          NOT NULL DEFAULT 0,
    `city`    VARCHAR(190) NOT NULL DEFAULT '',
    `country` VARCHAR(190) NOT NULL DEFAULT '',

    -- ⚠ Пустое тело — законное состояние: список пришёл, а за телом ещё не сходили
    -- или сайт его не отдал. Письмо при этом уже видно в списке, и это лучше, чем
    -- прятать его до полной загрузки.
    `body` TEXT NOT NULL COMMENT 'Текст письма; пусто — тело ещё не забрали',

    `date_post`  DATETIME NULL COMMENT 'Когда написано, по часам сайта (UTC)',
    `is_replied` TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Ответили ли на сайте: признак сайта',

    -- ⚠ Наш собственный признак, которого у сайта нет. Ноль — письмо новое, и оно
    -- считается в значке на карточке анкеты.
    `seen` TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Просмотрено человеком в панели',

    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_dating_mail_in` (`profile_id`, `message_id`),
    -- Под значок непрочитанных: считается по анкете и флагу.
    KEY `idx_dating_mail_in_new` (`profile_id`, `seen`),
    -- Под список вкладки: свежие сверху, по сайту.
    KEY `idx_dating_mail_in_site` (`site_id`, `date_post`),
    CONSTRAINT `fk_dmail_in_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Входящие письма анкет, забранные через API сайта';
