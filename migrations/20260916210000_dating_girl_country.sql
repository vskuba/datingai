-- Страна девушек: Украина у обоих сайтов (2026-09-16)
--
-- ЗАЧЕМ. Настройка `girl_country` уезжает агенту инвайтов рядом с текущим
-- временем (`20260916200000_wf_dating_invite_now` у соседа). Время без места
-- половинчато: чужой праздник или чужой сезон выдают робота так же, как
-- перепутанный вечер. До сих пор настройка стояла пустой, и агент про место
-- молчал — законное поведение, но небесплатное.
--
-- ⚠ ЗНАЧЕНИЕ НАЗВАЛ ЗАКАЗЧИК. Выдумывать его было нельзя: приписать девушке
-- чужую страну хуже, чем не упоминать место вовсе.
--
-- ⚠ HEX-ЛИТЕРАЛОМ, А НЕ СТРОКОЙ В КАВЫЧКАХ. Здесь нет ни слэшей, ни кавычек, и
-- строка прошла бы — но правило (`migration.md`) заведено не под них: значение
-- едет через оболочку и `docker exec`, где кириллица зависит от локали обеих
-- сторон. Шестнадцатеричная запись не зависит ни от чего.
--
-- Идемпотентность: пишем только туда, где своего значения ещё нет. Правка
-- человеком на странице «Сайты» повторным накатом не затирается.

SELECT 'ДО' AS этап, site_id, name,
       IFNULL(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.girl_country')), '(пусто)') AS страна
  FROM dating_site WHERE is_gone = 0 ORDER BY site_id;

-- ------------------------------------------- Страна
UPDATE dating_site
   SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()), '$.girl_country',
                           CONVERT(X'd0a3d0bad180d0b0d0b8d0bdd0b0' USING utf8mb4))
 WHERE is_gone = 0
   AND (settings IS NULL
        OR JSON_EXTRACT(settings, '$.girl_country') IS NULL
        OR JSON_UNQUOTE(JSON_EXTRACT(settings, '$.girl_country')) = '');

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, site_id, name,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.girl_country')) AS страна
  FROM dating_site WHERE is_gone = 0 ORDER BY site_id;
