-- Luxee: адрес проверки панели и примета кабинета под новую учётку (2026-11-16)
--
-- ЗАЧЕМ. Панель Luxee горела красным при живой сессии, а круг панели каждые
-- пять минут заново входил в неё сценарием (прогоны у соседа «ok», проверка
-- «сайт не признал вход»). Причин две, и обе в этих двух ключах:
--
--   admin_url  `https://luxee.io/profiles` отвечает `301` на `/profiles/` —
--              проверка идёт без переадресации и читает это как увод на вход.
--              Новый адрес — `/profile/` С ЧЕРТОЙ: без неё снова `301`.
--              (Черта в конце пути теперь не срезается — `panel._split`.)
--   admin_mark `StarAgency` — имя прежней учётки агентства. Новая (раздел
--              «Models admin», операторская) его не показывает. Примета —
--              ссылка выхода `/site/logout/`: она есть на любой странице после
--              входа и не зависит от того, чья учётка.
--
-- ⚠ Корень панели от `…/profile/` выходит `…/profile`, поэтому пути разделов
-- у драйвера пишутся от корня сайта, с ведущей чертой (`panel_fetch_address`).
--
-- ⚠ Отбор по адресу сайта, а не по номеру: номер закладки на другой установке
-- свой (тот же довод, что у 20261015130000_site_luxee_settings.sql).

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап, site_id, name,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.admin_url')) AS admin_url,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.admin_mark')) AS admin_mark
  FROM site
 WHERE url LIKE '%luxee.io%';

UPDATE site
   SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),
        '$.admin_url', 'https://luxee.io/profile/',
        '$.admin_mark', '/site/logout/')
 WHERE url LIKE '%luxee.io%';

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, site_id, name,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.admin_url')) AS admin_url,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.admin_mark')) AS admin_mark
  FROM site
 WHERE url LIKE '%luxee.io%';
