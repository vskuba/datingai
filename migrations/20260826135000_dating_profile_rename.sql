-- `dating_girl` → `dating_profile`, `girl_id` → `profile_id`.
--
-- Учётка на сайте знакомств — это анкета, профиль. «Девушка» была словом из
-- первых дней проекта, когда учётки заводили только женские; сейчас у таблицы
-- есть колонка `role`, и мужская анкета в ней такая же законная строка.
--
-- Переименование сплошное: таблица, три ссылающиеся колонки, внешние ключи и
-- индексы, где старое слово стояло в имени. Половинчатое — хуже обоих вариантов:
-- `dating_profile.girl_id` заставляет помнить, что это одно и то же.
--
-- ПОРЯДОК ОБЯЗАТЕЛЕН. Колонку под внешним ключом MySQL переименовать не даст,
-- поэтому ключи сначала снимаются, потом ставятся заново — уже с новыми именами.
-- `RENAME COLUMN` и `RENAME INDEX` требуют MySQL 8.0; он и стоит (образ
-- `mysql:8.0` локально, сервер RhapsodyAi на проде).
--
-- Составные ключи, где слова `girl` в имени нет (`uk_dating_message_uid`,
-- `idx_dating_message_dialog`, `uk_dating_contact`), не трогаем: их состав
-- переезжает вместе с колонкой сам.
--
-- Данные не теряются: это переименование, а не пересоздание.

ALTER TABLE `dating_message` DROP FOREIGN KEY `fk_dm_girl`;
ALTER TABLE `dating_log`     DROP FOREIGN KEY `fk_dl_girl`;
ALTER TABLE `dating_contact` DROP FOREIGN KEY `fk_dc_girl`;

RENAME TABLE `dating_girl` TO `dating_profile`;

ALTER TABLE `dating_profile`
    RENAME INDEX `uk_dating_girl_site_login` TO `uk_dating_profile_site_login`;

ALTER TABLE `dating_message`
    RENAME COLUMN `girl_id` TO `profile_id`,
    RENAME INDEX  `idx_dating_message_girl` TO `idx_dating_message_profile`;

ALTER TABLE `dating_log`
    RENAME COLUMN `girl_id` TO `profile_id`,
    RENAME INDEX  `idx_dating_log_girl` TO `idx_dating_log_profile`;

ALTER TABLE `dating_contact`
    RENAME COLUMN `girl_id` TO `profile_id`;

ALTER TABLE `dating_message`
    ADD CONSTRAINT `fk_dm_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE;

ALTER TABLE `dating_log`
    ADD CONSTRAINT `fk_dl_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE;

ALTER TABLE `dating_contact`
    ADD CONSTRAINT `fk_dc_profile` FOREIGN KEY (`profile_id`)
        REFERENCES `dating_profile` (`id`) ON DELETE CASCADE;
