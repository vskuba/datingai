-- Пул ответов на входящие письма (2026-09-09)
--
-- Ответы, написанные агентом и ждущие человека. Устроено как пул рассылки
-- (`dating_mailing`) и по той же причине: между «панель написала» и «письмо ушло»
-- должно быть место, где текст читают.
--
-- ⚠ ПУЛ ОБЩИЙ НА САЙТ, а не на анкету. Письма приходят разным девушкам, но читает
-- ответы один человек и подряд: раздели мы пул по анкетам, ему пришлось бы обойти
-- сорок списков, чтобы понять, есть ли работа. Отсюда `site_id` в отборе и
-- `profile_id` — просто колонка «от кого».
--
-- ⚠ ОДИН ОТВЕТ НА ПИСЬМО. Уникальный ключ по `mail_in_id`: повторный заход круга не
-- должен класть второй ответ на то же письмо, а два ответа подряд одному человеку
-- выглядят поломкой — ею и являются.
--
-- ⚠ `mail_in_id` С КАСКАДОМ: удалили входящее — ушёл и ответ на него. Ответ без
-- письма, на которое он отвечает, прочесть нельзя: не с чем сверить.

CREATE TABLE IF NOT EXISTS `dating_mail_reply` (
    `id`         INT NOT NULL AUTO_INCREMENT,
    `site_id`    INT NOT NULL COMMENT 'Сайт: пул отбирается по нему',
    `profile_id` INT NOT NULL COMMENT 'От чьего имени отвечаем',
    `mail_in_id` INT NOT NULL COMMENT 'На какое входящее письмо это ответ',

    `uid`  VARCHAR(32)  NOT NULL DEFAULT '' COMMENT 'Кому: номер собеседника на сайте',
    `name` VARCHAR(190) NOT NULL DEFAULT '' COMMENT 'Его имя на момент ответа',

    `body` TEXT NOT NULL COMMENT 'Текст ответа — то, что уйдёт',

    -- ⚠ Те же три состояния, что у пула рассылки, и то же правило: неудача
    -- оставляет ответ в пуле (`draft` с причиной в `error`), чтобы можно было
    -- повторить. `failed` его бы похоронил.
    `status` ENUM('draft', 'sent', 'failed') NOT NULL DEFAULT 'draft'
        COMMENT 'draft — лежит в пуле, sent — ушло, failed — не ушло',
    `error`   VARCHAR(500) NOT NULL DEFAULT '' COMMENT 'Почему не ушло: словами',
    `sent_at` DATETIME NULL COMMENT 'Когда ушло. NULL — ещё в пуле',

    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_dating_mail_reply` (`mail_in_id`),
    -- Под список пула: свежие сверху, по сайту.
    KEY `idx_dating_mail_reply_site` (`site_id`, `status`, `id`),
    KEY `fk_dreply_profile` (`profile_id`),
    CONSTRAINT `fk_dreply_in` FOREIGN KEY (`mail_in_id`)
        REFERENCES `dating_mail_in` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_dreply_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Пул ответов на входящие письма: написаны агентом, ждут человека';
