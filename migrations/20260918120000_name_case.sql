-- Имена людей в человеческом регистре: каждое слово с большой буквы.
--
-- Сайт отдаёт имена как попало: «VINCENT VEGA», «DirK», «zaoui». В карточке,
-- в пуле писем и в обращении письма это читается как крик или как опечатка, а
-- подставляется имя прямо в текст — «Hi VINCENT» уходит живому человеку.
--
-- С этой выкладки регистр правится **на входе** — у всех семи дверей, которыми
-- имя попадает к нам (`people.people_name_case`). Здесь — разовая уборка того,
-- что уже лежит.
--
-- ## ⚠ Почему правило переписано на SQL, а не позвано из кода
--
-- Миграция — замороженная история: применённую не правят. Позови она
-- `people_name_case`, и завтрашняя правка функции задним числом меняла бы
-- смысл вчерашней миграции — на новой базе она сделала бы не то, что сделала
-- на этой. Правило здесь повторено нарочно и меняться уже не должно.
--
-- ## ⚠ Сравнение — `BINARY`, иначе «кривых» не найдётся вовсе
--
-- Сличение в базе идёт по `utf8mb4_unicode_ci`, а там `BEIJING = Beijing`.
-- Первая версия отбора вернула ноль строк при 69 кривых именах. `BINARY`
-- сравнивает байты — только так видно, что регистр разный.
--
-- ## Что не трогаем
--
-- `mail.name` (название шаблона), `mail_var.name` и `profile_var.name` (имена
-- переменных вроде `firstName`) и `site.name` — это не имена людей. Капитализация
-- имени переменной сломала бы подстановку.

-- Сперва пробелы: два подряд внутри имени и хвост по краям. Отдельным проходом,
-- потому что вписать `TRIM(REGEXP_REPLACE(…))` внутрь разбора по знакам значило
-- бы повторить его в каждом из трёх мест выражения — и в четвёртом, в `WHERE`.
UPDATE block SET name = TRIM(REGEXP_REPLACE(name, '[[:space:]]+', ' ')) WHERE name <> '';
UPDATE companion SET name = TRIM(REGEXP_REPLACE(name, '[[:space:]]+', ' ')) WHERE name <> '';
UPDATE contact SET name = TRIM(REGEXP_REPLACE(name, '[[:space:]]+', ' ')) WHERE name <> '';
UPDATE favorite_mail SET name = TRIM(REGEXP_REPLACE(name, '[[:space:]]+', ' ')) WHERE name <> '';
UPDATE invite SET name = TRIM(REGEXP_REPLACE(name, '[[:space:]]+', ' ')) WHERE name <> '';
UPDATE mail_in SET name = TRIM(REGEXP_REPLACE(name, '[[:space:]]+', ' ')) WHERE name <> '';
UPDATE mail_reply SET name = TRIM(REGEXP_REPLACE(name, '[[:space:]]+', ' ')) WHERE name <> '';
UPDATE mailing SET name = TRIM(REGEXP_REPLACE(name, '[[:space:]]+', ' ')) WHERE name <> '';
UPDATE message SET sender_name = TRIM(REGEXP_REPLACE(sender_name, '[[:space:]]+', ' '))
 WHERE sender_name <> '';

-- Разряды позиции: имена в базе не длиннее 190 знаков.
--
-- Правило то же, что у `people_name_case`: знак становится заглавным, если он
-- первый или стоит сразу за разделителем. Разделителей три — пробел, дефис и
-- апостроф: «anna-maria» это «Anna-Maria», а «o'neill» — «O'Neill».

UPDATE block SET name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(block.name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(block.name, n.i, 1))
      ELSE LOWER(SUBSTRING(block.name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(block.name))
 WHERE name <> '';

UPDATE companion SET name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(companion.name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(companion.name, n.i, 1))
      ELSE LOWER(SUBSTRING(companion.name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(companion.name))
 WHERE name <> '';

UPDATE contact SET name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(contact.name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(contact.name, n.i, 1))
      ELSE LOWER(SUBSTRING(contact.name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(contact.name))
 WHERE name <> '';

UPDATE favorite_mail SET name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(favorite_mail.name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(favorite_mail.name, n.i, 1))
      ELSE LOWER(SUBSTRING(favorite_mail.name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(favorite_mail.name))
 WHERE name <> '';

UPDATE invite SET name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(invite.name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(invite.name, n.i, 1))
      ELSE LOWER(SUBSTRING(invite.name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(invite.name))
 WHERE name <> '';

UPDATE mail_in SET name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(mail_in.name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(mail_in.name, n.i, 1))
      ELSE LOWER(SUBSTRING(mail_in.name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(mail_in.name))
 WHERE name <> '';

UPDATE mail_reply SET name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(mail_reply.name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(mail_reply.name, n.i, 1))
      ELSE LOWER(SUBSTRING(mail_reply.name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(mail_reply.name))
 WHERE name <> '';

UPDATE mailing SET name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(mailing.name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(mailing.name, n.i, 1))
      ELSE LOWER(SUBSTRING(mailing.name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(mailing.name))
 WHERE name <> '';

-- ⚠ Тут столбец называется иначе — `sender_name`, и строк на два порядка больше
-- (13 тысяч против четырёхсот). Дольше остальных, но всё равно секунды.
UPDATE message SET sender_name = (
  WITH RECURSIVE n (i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 190)
  SELECT GROUP_CONCAT(CASE
      WHEN n.i = 1 OR SUBSTRING(message.sender_name, n.i - 1, 1) IN (' ', '-', '''')
      THEN UPPER(SUBSTRING(message.sender_name, n.i, 1))
      ELSE LOWER(SUBSTRING(message.sender_name, n.i, 1)) END
    ORDER BY n.i SEPARATOR '')
    FROM n WHERE n.i <= CHAR_LENGTH(message.sender_name))
 WHERE sender_name <> '';
