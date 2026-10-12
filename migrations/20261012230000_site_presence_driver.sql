-- Сайт называет свой драйвер: `presence.driver` (2026-10-12)
--
-- ЗАЧЕМ. Драйверы сайта выбираются по ключу `driver` в описании
-- (`site_registry.py`), но **ни у одного живого сайта он не проставлен**. Чат и
-- анкета это переживали: у них стоит переходное умолчание `goldenbride`, и
-- заведено оно ровно потому, что поле пустует. У чтения панели такого умолчания
-- нет и быть не может — путь страницы анкеты у каждой панели свой, и чужой дал бы
-- не отказ, а пустые параметры, неотличимые от «анкета не заполнена».
--
-- Итог был виден в интерфейсе: кнопка «Обновить с сайта» на вкладке «Информация»
-- отвечала «панель этого сайта читать нечем» у единственного сайта с панелью.
--
-- ⚠⚠ ЧТО ЭТО МЕНЯЕТ, КРОМЕ ПАНЕЛИ. Ключ один на четыре реестра, и проверено
-- прямым вызовом до правки:
--
--   чат                  DatingSiteChatGoldenbride    → не меняется (умолчание)
--   анкета девушки       DatingSiteProfileGoldenbride → не меняется (умолчание)
--   страница собеседника DatingSiteAbout              → DatingSiteAboutLadadate
--   панель               DatingSitePanel (пустой)     → DatingSitePanelLadadate
--
-- Третья строка — смена класса без смены поведения: `DatingSiteAboutLadadate`
-- наследует базовый и своего разбора не имеет (так и написано в его докстринге).
-- Сверено на живом куске разметки: оба драйвера дают посимвольно одинаковый
-- разбор.
--
-- ⚠ Отбор по адресу сайта, а не по номеру: `site_id` — номер закладки у соседа, и
-- на другой установке за ним стоит другой сайт. Адрес же принадлежит самому сайту.
--
-- ⚠ Goldenbride (19) сюда не попадает намеренно: у него описание пустое и панели
-- нет вовсе, а умолчание чата и так ведёт его в свой драйвер.
--
-- ⚠⚠ `presence` — строка JSON **внутри** JSON-колонки, а не вложенный объект.
-- Отсюда тройное преобразование: достать строку, разобрать её как JSON, дописать
-- ключ, положить обратно строкой. Запиши мы объектом — читалка
-- (`json_column_load`) получила бы словарь там, где ждёт строку, и описание сайта
-- стало бы пустым у всех его анкет разом.
--
-- ⚠ Таблица зовётся `site`, без приставки: её сняли миграцией
-- `20260917020000_tables_without_prefix.sql`. Соседние файлы старше и пишут
-- `dating_site` — копировать их имя нельзя.
--
-- Идемпотентность: пишем только туда, где `driver` ещё не задан.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап, site_id, name,
       IFNULL(JSON_UNQUOTE(JSON_EXTRACT(
           CAST(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) AS JSON),
           '$.driver')), '(пусто)') AS драйвер
  FROM site
 WHERE is_gone = 0 AND JSON_EXTRACT(settings, '$.presence') IS NOT NULL
 ORDER BY site_id;

-- ------------------------------------------------------- Ladadate
UPDATE site
   SET settings = JSON_SET(settings, '$.presence',
        CAST(JSON_SET(
            CAST(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) AS JSON),
            '$.driver', 'ladadate') AS CHAR))
 WHERE is_gone = 0
   AND JSON_EXTRACT(settings, '$.presence') IS NOT NULL
   AND JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) LIKE '%ladadate.com%'
   AND JSON_EXTRACT(
         CAST(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) AS JSON),
         '$.driver') IS NULL;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, site_id, name,
       IFNULL(JSON_UNQUOTE(JSON_EXTRACT(
           CAST(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) AS JSON),
           '$.driver')), '(пусто)') AS драйвер
  FROM site
 WHERE is_gone = 0 AND JSON_EXTRACT(settings, '$.presence') IS NOT NULL
 ORDER BY site_id;
