-- Присутствие Goldenbride переезжает к нам (2026-09-16)
--
-- ЧТО НЕ ТАК. У Ladadate настройка присутствия своя (`heartbeat` на странице
-- «Сайты»), у Goldenbride её нет — и `dating_heartbeat_config` каждый раз уходит
-- к соседу за карточкой сайта, чтобы перевести её `presence` в формат движка.
--
-- Цена, снятая с журнала запросов прода за час:
--
--     /api/browser/site/19    343 запроса в час
--
-- Это 2 КБ настроек, которые не менялись месяцами, и спрашиваются они по анкете
-- на каждом круге опроса. Сосед лежит — присутствие Goldenbride гаснет целиком,
-- хотя к браузеру и сценариям оно отношения не имеет: удар шлём мы сами, по
-- своим cookie, своим HTTP-клиентом.
--
-- ⚠ КЕША ЗДЕСЬ БЫТЬ НЕ ДОЛЖНО, и это был первый (неверный) ответ на ту же беду.
-- Кеш — это признание, что настройка живёт не там, где её читают. Настройки
-- сайтов у нас **и так свои**: `dating_site.settings`, страница «Сайты», правка
-- человеком. Присутствие Goldenbride было единственным, что там не лежало.
--
-- ЧТО СТАВИМ. Ту же настройку, что сосед отдаёт сегодня, переведённую в формат
-- движка **тем самым переводчиком**, что работает в бою (`_from_neighbour` в
-- `dating_heartbeat.py`), а не руками:
--
--   base     — корень сайта;
--   headers  — общий `Referer` (сайт без него отвечает отказом);
--   beat     — `mode=setAuthUserOnline`, тот самый удар, что шлёт страница;
--   alive    — две проверки живого входа, включая GWT-RPC в `ladymodule`;
--   unread   — счётчик непрочитанных писем.
--
-- ⚠ `beat` И `alive` РАЗНЫЕ, и это проверено перед записью. Совпади они —
-- присутствия бы не было вовсе, была бы проверка сессии, а колонка «В сети»
-- врала бы (`heartbeat.md`, §4).
--
-- ⚠ ВЫХОД (`logout`) СЮДА НЕ ПЕРЕЕЗЖАЕТ, и это не забывчивость. У Goldenbride
-- ручки выхода нет — выходят сценарием (`logout_scenario` в карточке соседа), и
-- `dating_disconnect` именно так и поступает, когда в настройке шага нет.
--
-- ⚠ ЗНАЧЕНИЕ — СТРОКА С JSON ВНУТРИ, а не объект JSON. Объект здесь уже ломал
-- прод: `_strings` в `dating_site.py` хранит память о том дне, когда `heartbeat`
-- у Ladadate записали `JSON_SET`'ом с настоящим объектом, и на сайте перестала
-- сохраняться **любая** настройка.
--
-- Идемпотентность: пишем только там, где своего значения ещё нет. Правка
-- человеком на странице «Сайты» повторным накатом не затирается.

SELECT 'ДО' AS этап, site_id, name,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.heartbeat')) IS NOT NULL AS своя_настройка
  FROM dating_site WHERE site_id = 19;

-- ------------------------------------------- Настройка присутствия
UPDATE dating_site
   SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()), '$.heartbeat',
                           '{"alive": [{"auth_field": "auth", "body": "mode=update_v2", "path": "/goldenbride/services/chat/v26"}, {"body": "7|0|10|https://goldenbride.net/ladymodule/|6194265CA9BA5095E40F4AEE22413C7F|com.lady.shared.dataaccess.IDataServlet|executeRequest|com.lady.shared.dataaccess.RequestDTO/370062081|java.util.HashMap/1797211028|java.lang.String/2004016611|timeZone|UTC|com.lady.server.requestprocessors.lady.event.LadyEventRequestProcessor|1|2|3|4|1|5|5|6|1|7|8|7|9|10|", "headers": {"Content-Type": "text/x-gwt-rpc; charset=UTF-8", "X-GWT-Module-Base": "https://goldenbride.net/ladymodule/", "X-GWT-Permutation": "8D7AC9E3818039E53F8C64289B32F39E"}, "method": "POST", "ok_mark": "//OK", "path": "/ladymodule/services/rpc"}], "base": "https://goldenbride.net", "beat": {"body": "mode=setAuthUserOnline", "path": "/goldenbride/services/chat/v5"}, "headers": {"Referer": "https://goldenbride.net/chat"}, "unread": {"field": "unreadMail", "method": "GET", "path": "/goldenbride/services/userApiV2?requestType=LetterStats"}}')
 WHERE site_id = 19
   AND (settings IS NULL
        OR JSON_EXTRACT(settings, '$.heartbeat') IS NULL
        OR JSON_UNQUOTE(JSON_EXTRACT(settings, '$.heartbeat')) = '');

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, site_id, name,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.heartbeat')) LIKE '%setAuthUserOnline%' AS удар_на_месте,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.heartbeat')) LIKE '%ladymodule%' AS проверка_на_месте,
       JSON_TYPE(JSON_EXTRACT(settings, '$.heartbeat')) AS тип_значения
  FROM dating_site WHERE site_id = 19;
