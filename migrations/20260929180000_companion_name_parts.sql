-- Части имени собеседника — своими колонками (2026-09-29)
--
-- ## ЗАЧЕМ (указание человека)
--
-- `companion_first_name` пустовал у большинства адресатов, и письма из-за этого не
-- уходили: пустая переменная останавливает отправку. Причина была в разборе —
-- `people_name_parts` отвечала пустотой на имя **без пробела**, а имя одним словом
-- на сайте обычное дело.
--
-- Разбор починен той же правкой (первое слово есть всегда, когда есть имя), а
-- человек решил вдобавок **хранить части колонками и заполнять их при заведении
-- карточки**. Миграция заводит колонки и заполняет накопленное.
--
-- ## ⚠⚠⚠ ЧЕМ ЭТО ОПАСНО И ЧЕМ ОПАСНОСТЬ СНЯТА
--
-- Прежнее решение — «выводим, а не храним» — было записано в двух докстрингах
-- (`mailing_person_fields`, `mailing_companion_vars`) с прямым доводом: заведи мы
-- колонки, они разошлись бы с именем в первую же правку карточки, и письмо ушло бы
-- с прежним обращением.
--
-- Довод верен, и снимается он не обещанием, а устройством: `name` в карточку
-- пишется **одной дверью** (`companion_save`), и та пересчитывает части **тем же
-- вызовом**, которым меняет имя. Разойтись им негде — нет второго места, где имя
-- меняется в одиночку. Сторож на это стоит в сюите.
--
-- ⚠ **Выводимые части при этом никуда не делись, и убирать их нельзя.** Набор пула
-- подставляет переменные из **ленты «Все онлайн»**, а не из карточки
-- (`mailing._people` → кэш `people.py`), и колонок в тех строках не будет никогда.
-- Колонки — ускорение и опора для страницы карточки; правило подстановки
-- по-прежнему «своё поле карточки сильнее выводимого» (`mailing_person_fields`).
--
-- ## ⚠⚠ Чужая письменность не фильтруется здесь
--
-- Части пишутся как есть, даже у имени иероглифами. Решает «подставлять ли»
-- по-прежнему `people_name_usable` — одно место на панель, и на чтении. Фильтруй
-- мы ещё и на записи, правило жило бы в двух местах и разошлось бы.
--
-- ## ⚠ Заполнение считается SQL, и это здесь безопасно
--
-- В отличие от переноса ключей собеседников (там `agent_key` с транслитом SQL не
-- повторить), разбор имени — это «первое слово и остаток»: `SUBSTRING_INDEX` даёт
-- ровно то же, что `str.partition(' ')`. Лишние пробелы внутри снимает `TRIM`.
--
-- Идемпотентность: колонки заводятся по `information_schema`, заполнение идёт
-- только там, где части ещё пусты.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'companion'
           AND `COLUMN_NAME` IN ('firstname', 'lastname')) AS колонок_уже,
       (SELECT COUNT(*) FROM `companion`) AS карточек,
       (SELECT COUNT(*) FROM `companion` WHERE TRIM(`name`) <> '') AS с_именем;

-- ── Колонки ─────────────────────────────────────────────────────────────────
--
-- ⚠ `ADD COLUMN IF NOT EXISTS` MySQL не умеет, поэтому через `information_schema`
-- и подготовленный запрос: второй прогон видит колонки и ничего не делает.
SET @есть = (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
              WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'companion'
                AND `COLUMN_NAME` = 'firstname');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `companion`
     ADD COLUMN `firstname` varchar(190) NOT NULL DEFAULT ''''
       COMMENT ''Имя без фамилии: первое слово name. Пишет companion_save''
       AFTER `name`,
     ADD COLUMN `lastname` varchar(190) NOT NULL DEFAULT ''''
       COMMENT ''Фамилия: остаток name после первого пробела. Пусто — не знаем''
       AFTER `firstname`',
  'SELECT ''колонки уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- ── Заполнение накопленного ─────────────────────────────────────────────────
--
-- ⚠ Только там, где части пусты: заполнение разовое, а дальше их ведёт
-- `companion_save`. Затирай мы заполненное, миграция спорила бы с кодом.
UPDATE `companion`
   SET `firstname` = SUBSTRING_INDEX(TRIM(`name`), ' ', 1),
       `lastname` = IF(LOCATE(' ', TRIM(`name`)) = 0, '',
                       TRIM(SUBSTRING(TRIM(`name`),
                                      LOCATE(' ', TRIM(`name`)) + 1)))
 WHERE TRIM(`name`) <> '' AND `firstname` = '' AND `lastname` = '';

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `information_schema`.`COLUMNS`
         WHERE `TABLE_SCHEMA` = DATABASE() AND `TABLE_NAME` = 'companion'
           AND `COLUMN_NAME` IN ('firstname', 'lastname')) AS колонок_стало,
       (SELECT COUNT(*) FROM `companion`
         WHERE TRIM(`name`) <> '' AND `firstname` <> '') AS имя_заполнено,
       -- ⚠⚠ Главная примета: у карточки с именем первое слово обязано быть. Ноль —
       -- значит заполнение не сработало, и письма снова не пойдут.
       (SELECT COUNT(*) FROM `companion`
         WHERE TRIM(`name`) <> '' AND `firstname` = '') AS осталось_без_имени,
       -- ⚠ Фамилия пуста законно: у имени одним словом её нет.
       (SELECT COUNT(*) FROM `companion` WHERE `lastname` <> '') AS с_фамилией;

-- ⚠ Десяток примеров глазами: первое слово и остаток обязаны читаться как имя и
-- фамилия, а не как обрубки.
SELECT `id`, `name`, `firstname`, `lastname` FROM `companion`
 WHERE TRIM(`name`) <> '' ORDER BY `id` DESC LIMIT 10;
