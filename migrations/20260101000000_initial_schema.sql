-- НАЧАЛЬНАЯ схема DatingAi. Файл заморожен: он история, а не текущее состояние.
--
-- Править его нельзя. yoyo применяет каждый файл ровно один раз и помнит это в
-- `_yoyo_migration`, поэтому дописанный сюда `ALTER TABLE` не выполнится никогда —
-- ни на проде, ни на локальной базе с данными. Изменение схемы — **новый файл**
-- рядом, с меткой времени в имени (`YYYYMMDDHHMMSS_<что делаем>.sql`).
--
-- Отсюда следствие, которое надо знать: текущая схема — это сумма файлов в
-- `migrations/` по порядку имён, а не этот файл. Быстрый ответ на «как оно
-- сейчас» даёт база: `SHOW CREATE TABLE`. Разменяли осознанно — взамен правка
-- схемы доезжает до прода сама, при старте, а не одноразовым файлом по туннелю.
-- Подробнее — `.claude/docs/migration.md`.
--
-- В базе лежит ровно то, чем этот проект владеет: учётки анкет, их переписка,
-- журнал работы и собеседницы. Всего остального — сайтов-закладок, сценариев
-- браузера, агентов, workflow и фактов — здесь нет намеренно: это хозяйство
-- RhapsodyAi, и вторая копия разошлась бы с первой в первый же день.
--
-- Отсюда и `site_id` без внешнего ключа: сайт живёт у соседа, проверить его
-- базой невозможно, и проверяет его API-клиент (`rhapsody_api_site_get`).
--
-- Имени базы здесь нет намеренно. Раньше стояло `USE `dating``, и прогон файла в
-- тестовую базу на проверку молча переключался на рабочую — а ниже по файлу
-- `DROP TABLE` по всем таблицам. Имя задаёт подключение: yoyo берёт его из
-- `MYSQL_DATABASE`, ручной прогон получает аргументом `mysql`.

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `dating_contact`;
DROP TABLE IF EXISTS `dating_log`;
DROP TABLE IF EXISTS `dating_message`;
DROP TABLE IF EXISTS `dating_girl`;
DROP TABLE IF EXISTS `user_access_token`;
DROP TABLE IF EXISTS `user`;

SET FOREIGN_KEY_CHECKS = 1;

-- Люди, которые заходят в панель.
--
-- Ролей ровно две, и это не матрица прав, а один вопрос: пускать ли к устройству
-- установки. `dating` — тот, кто работает с анкетами: список, чат, ответы, то
-- есть всё, ради чего панель и открывают. `admin` — он же плюс seed, бэкапы и
-- веб-морда к базе, то есть операции, которые правят или выносят базу целиком.
--
-- Дробить дальше («смотреть» и «править» внутри анкет) не надо: работа с анкетой
-- неделима — кто видит переписку, тот на неё и отвечает.
--
-- По умолчанию `dating`: новая учётка не должна получать доступ к бэкапам от
-- того, что о роли забыли подумать.
CREATE TABLE `user` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `username` VARCHAR(50) NOT NULL,
    `password_hash` VARCHAR(100) NOT NULL COMMENT 'bcrypt; открытый пароль не хранится',
    `role` VARCHAR(20) NOT NULL DEFAULT 'dating' COMMENT 'dating — анкеты; admin — плюс установка',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Токены доступа: ими ходят скрипты и проверки, у которых нет и не может быть
-- сессии. Токенов у пользователя может быть несколько — по одному на потребителя,
-- чтобы отзыв одного не ронял остальных.
CREATE TABLE `user_access_token` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `user_id` INT NOT NULL,
    `token` VARCHAR(64) NOT NULL,
    `note` VARCHAR(190) NOT NULL DEFAULT '' COMMENT 'Кому выдан: имя потребителя',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_access_token` (`token`),
    INDEX `idx_user_access_token_user` (`user_id`),
    CONSTRAINT `fk_uat_user` FOREIGN KEY (`user_id`) REFERENCES `user` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Учётки девушек на сайте знакомств: под ними браузер входит и работает.
--
-- Пароль лежит рядом с логином и в открытом виде: под этой учёткой заходит не
-- человек, а браузер, и подставить хеш вместо пароля в форму входа нельзя.
-- Наружу (`/api/dating/girl`) он не отдаётся — только признак «пароль задан»:
-- страница им не пользуется, а утечь через журнал или чужой глаз он не должен.
--
-- Учётка принадлежит сайту: одна и та же почта на разных сайтах — разные учётки,
-- и удаление закладки уносит их с собой (`ON DELETE CASCADE`).
CREATE TABLE `dating_girl` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `site_id` INT NOT NULL COMMENT 'Сайт-закладка в RhapsodyAi: своей таблицы сайтов здесь нет',
    `login` VARCHAR(190) NOT NULL COMMENT 'Логин или почта, которыми входят на сайт',
    `password` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Пароль для входа; наружу не отдаётся',
    `note` VARCHAR(255) NOT NULL DEFAULT '' COMMENT 'Пометка человека: чья анкета, для чего',
    -- Роль на сайте. Мужской и женский кабинеты — разные приложения на одном
    -- домене, и сценарии у них не совпадают: выход, анкета, открытие чата. Без
    -- этого поля учётке доставался чужой сценарий, который честно падал по
    -- таймауту, потратив минуту.
    `role` ENUM('lady','man') NOT NULL DEFAULT 'lady' COMMENT 'Чей кабинет: женский или мужской',
    -- Отвечать ли на новые сообщения самостоятельно. Включено по умолчанию: анкету
    -- заводят, чтобы она разговаривала, а не чтобы за ней ходили с кнопкой. Кнопка
    -- «Ответить» остаётся запасным путём — ответить сейчас и при себе.
    `auto_answer` TINYINT(1) NOT NULL DEFAULT 1 COMMENT 'Автоответ агентом на новые сообщения',
    -- Кому отвечать автоматически: номера собеседников через запятую. Пусто — всем.
    -- Ограничение нужно, потому что каждый ответ стоит денег дважды: прогон LLM и
    -- платное сообщение на сайте. У анкеты с сотнями диалогов «отвечать всем» —
    -- это рассылка, а не разговор.
    `auto_answer_only` VARCHAR(500) NOT NULL DEFAULT '' COMMENT 'Кому отвечать: список uid через запятую; пусто — всем',
    -- Вход, снятый после логина. С ним присутствие держит обычный HTTP-клиент, без
    -- вкладки Chromium: замер на живом сайте — вкладка ~143 МБ, клиент с cookie
    -- единицы килобайт. Логин нужен раз в сутки, а не раз в 15 секунд.
    `storage_state` JSON NULL COMMENT 'Cookie и localStorage; обновляется входом',
    -- Адрес чат-сокета с токенами сессии. В разметке его нет — собирает JS страницы,
    -- поэтому снимается браузером один раз (`browser_ws`), а держит сокет потом
    -- обычный клиент.
    `ws_url` VARCHAR(1000) NOT NULL DEFAULT '' COMMENT 'Адрес чат-сокета с токенами сессии',
    -- Статус для индикатора: `online` — сайт подтвердил вход последним опросом,
    -- `error` — опрос не прошёл, `offline` — входа не было либо он протух.
    `status` ENUM('offline','online','error') NOT NULL DEFAULT 'offline',
    `unread` INT NOT NULL DEFAULT 0 COMMENT 'Непрочитанных писем на момент опроса',
    `last_seen_at` DATETIME NULL COMMENT 'Когда сайт последний раз подтвердил вход',
    `last_error` VARCHAR(500) NOT NULL DEFAULT '' COMMENT 'Чем кончился неудачный опрос',
    -- Анкета должна быть в сети: ставит успешный вход, снимает «Отключить». По
    -- этой пометке смена держателя восстанавливает вход сама. Отдельным полем, а
    -- не `storage_state IS NOT NULL`: неудачная проверка входа состояние стирает,
    -- и анкета выпадала бы из-под присмотра ровно тогда, когда её надо чинить.
    `keep_online` TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Держать анкету в сети автоматически',
    -- Когда вход сняли последний раз. Нужен профилактике: сессия сайта живёт
    -- около суток, и обновлять её лучше заранее, а не в середине разговора.
    `logged_in_at` DATETIME NULL COMMENT 'Когда последний раз снят storage_state',
    -- Череда неудачных попыток входа. Пусто — всё хорошо. Живёт в базе, а не в
    -- памяти смены: смена длится шесть часов, а череда тянется до суток.
    `relogin_since` DATETIME NULL COMMENT 'Начало череды неудачных входов',
    `relogin_tries` INT NOT NULL DEFAULT 0 COMMENT 'Сколько попыток сделано в череде',
    `relogin_next_at` DATETIME NULL COMMENT 'Когда следующая попытка входа',
    -- Профиль с сайта: имя, фотография, описание. Кэш, а не источник правды —
    -- сайт остаётся хозяином, но страница не должна ходить туда на каждый показ.
    `profile` JSON NULL COMMENT 'Имя, фото, ID и описание анкеты с сайта',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_dating_girl_site_login` (`site_id`, `login`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Сообщения живого чата, пойманные вебсокетом.
--
-- Приходят кадрами `receivePrivateMessage`: отправитель, текст, идентификатор для
-- подтверждения доставки. Складываем их здесь, потому что сокет держит воркер, а
-- смотрит человек страницей — общая память между ними только база.
--
-- `message_uid` — идентификатор сайта (`messageIdForConfirm`), по нему же ловятся
-- повторы: сокет переподключается, и сайт шлёт неподтверждённое заново.
CREATE TABLE `dating_message` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `girl_id` INT NOT NULL,
    `sender_uid` VARCHAR(32) NOT NULL COMMENT 'Кто написал — идентификатор на сайте',
    `sender_name` VARCHAR(190) NOT NULL DEFAULT '' COMMENT 'Имя, если сайт его прислал',
    `text` TEXT NOT NULL,
    `message_uid` VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'Идентификатор сайта; ловит повторы',
    `is_read` TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Прочитано человеком на странице',
    -- Ответили ли на это сообщение: пометка «отвечено» в списке диалога. Значит
    -- именно «мы отправили ответ», а не «мы про него знаем»: иначе в панели
    -- появлялась ложь — диалог помечен отвеченным, а ответа в нём нет.
    `is_answered` TINYINT(1) NOT NULL DEFAULT 0,
    -- Когда последний раз спрашивали агента про это сообщение. Нужно отдельно от
    -- `is_answered`, потому что агент имеет право промолчать: пометить такое
    -- «отвеченным» — обмануть оператора, а спрашивать заново каждую минуту —
    -- сжигать деньги на LLM. Отметка попытки решает и то, и другое.
    `answer_tried_at` DATETIME NULL COMMENT 'Когда агента спрашивали про это сообщение',
    -- `in` — написала собеседница, `out` — отправили мы.
    `direction` ENUM('in','out') NOT NULL DEFAULT 'in',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_dating_message_uid` (`girl_id`, `message_uid`),
    INDEX `idx_dating_message_girl` (`girl_id`, `id`),
    -- Индекс диалога: по нему карточка и раскладывает собеседниц. Без него оба её
    -- запроса вырождаются в перебор — группировка по собеседнице идёт через
    -- временную таблицу, а «последние десять реплик одной» читается обратным
    -- проходом по первичному ключу (замер на 400 диалогах: 4206 строк прочитано
    -- ради десяти). Порядок полей именно такой: `girl_id` и `sender_uid` берутся
    -- равенством, `id` остаётся отсортированным — по нему и свежесть диалога, и
    -- десяток последних реплик без сортировки.
    INDEX `idx_dating_message_dialog` (`girl_id`, `sender_uid`, `id`),
    CONSTRAINT `fk_dm_girl` FOREIGN KEY (`girl_id`) REFERENCES `dating_girl` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Журнал работы по учётке: что делали и чем кончилось.
--
-- Пишется на каждый шаг проверки и на каждое сообщение — входящее и исходящее.
-- Раздельный по учёткам: на странице человек смотрит одну девушку, и общая лента
-- по сотне анкет ему ничего не скажет.
CREATE TABLE `dating_log` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `girl_id` INT NOT NULL,
    -- `in`/`out` — сообщения чата, остальное — ход работы. Разделены, потому что
    -- на странице они разного цвета и читаются по-разному.
    `level` ENUM('ok','fail','info','in','out') NOT NULL DEFAULT 'info',
    `text` TEXT NOT NULL,
    `created_at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (`id`),
    INDEX `idx_dating_log_girl` (`girl_id`, `id`),
    CONSTRAINT `fk_dl_girl` FOREIGN KEY (`girl_id`) REFERENCES `dating_girl` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Собеседницы: кто пишет учётке. Профиль тянется с сайта и кэшируется здесь —
-- имя и фотография нужны на каждой перерисовке страницы, а сайт отдаёт их
-- ответом в сотни килобайт, и дёргать его ради подписи к строке незачем.
CREATE TABLE `dating_contact` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `girl_id` INT NOT NULL,
    `uid` VARCHAR(32) NOT NULL COMMENT 'Идентификатор собеседницы на сайте',
    `name` VARCHAR(190) NOT NULL DEFAULT '',
    `photo` VARCHAR(500) NOT NULL DEFAULT '',
    `age` INT NOT NULL DEFAULT 0,
    `city` VARCHAR(190) NOT NULL DEFAULT '',
    `is_online` TINYINT(1) NOT NULL DEFAULT 0,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_dating_contact` (`girl_id`, `uid`),
    CONSTRAINT `fk_dc_girl` FOREIGN KEY (`girl_id`) REFERENCES `dating_girl` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
