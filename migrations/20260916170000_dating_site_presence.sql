-- Описание сайтов переезжает к нам целиком (2026-09-16)
--
-- ЧТО НЕ ТАК. `browser_site.presence` у соседа — это описание сайта: где его
-- ручки, какими кадрами чат, чем отправлять, на каком языке отвечать. Читают его
-- четыре петли воркера, каждая по разу **на сайт**:
--
--     держатель, presence_loop   раз в 15 секунд    нужен send.transport
--     держатель, главный цикл    раз в 60 секунд    то же
--     держатель, answer_loop     раз в 60 секунд    то же
--     круг восстановления        раз в 60 секунд    НИЧЕГО
--
-- Счёт по журналу запросов прода за час:
--
--     /api/browser/site/343    412 обращений
--     /api/browser/site/19     343 обращения
--
-- У Ladadate это триста байт, не менявшихся с 11 сентября, и читалось из них одно
-- слово — "http". Хуже цены зависимость: лежит сосед — встаёт чат, хотя отправка
-- идёт нашей ручкой по нашим cookie и браузера не касается вовсе.
--
-- ⚠ КЕША ЗДЕСЬ БЫТЬ НЕ ДОЛЖНО — это был первый и неверный ответ на ту же беду.
-- Кеш признаёт, что настройка живёт не там, где её читают. Настройки сайтов у нас
-- и так свои: `dating_site.settings`, страница «Сайты», правка человеком.
--
-- ⚠ ПЕРЕНОСИМ ЦЕЛИКОМ, А НЕ ПО КЛЮЧУ, и это важнее, чем кажется. Присутствие уже
-- уехало отдельным ключом (`heartbeat`, миграция 20260916150000); растащи мы
-- остальное так же — вышло бы третье место, где живёт одно описание. Здесь лежит
-- сырое описание как есть, и читатели берут из него свои ключи.
--
-- ⚠ СОСЕД ОСТАЁТСЯ ЗАПАСНЫМ ПУТЁМ. Новый сайт заводят у него: пока настройку не
-- перенесли, его описание — единственное, чем анкета работает.
--
-- ⚠ ЗНАЧЕНИЕ — СТРОКА С JSON ВНУТРИ, а не объект. Объект здесь уже ронял прод:
-- `_strings` в `dating_site.py` хранит память о дне, когда `heartbeat` у Ladadate
-- записали `JSON_SET`'ом с настоящим объектом, и на сайте перестала сохраняться
-- любая настройка.
--
-- ⚠ СЦЕНАРИИ ВНУТРИ — ЭТО ИМЕНА, А НЕ САМИ СЦЕНАРИИ. Гоняет их по-прежнему сосед:
-- мы храним, как его попросить, а не то, что он делает. Граница не сдвинулась.
--
-- Идемпотентность: пишем только туда, где своего описания ещё нет. Правка
-- человеком на странице «Сайты» повторным накатом не затирается.

SELECT 'ДО' AS этап, site_id, name,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) IS NOT NULL AS своё_описание
  FROM dating_site WHERE site_id IN (19, 343) ORDER BY site_id;

-- ------------------------------------------- #19 (Goldenbride)
UPDATE dating_site
   SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()), '$.presence',
                           '{"base": "https://goldenbride.net", "chat_url": "https://goldenbride.net/chat#!CHAT", "chat_ws_mark": "Red5Chat", "contacts": {"body": "mode=get_result_v2", "path": "/goldenbride/services/chat/v27"}, "dialog_list_scenario": {"lady": "диалоги: список (девушка)", "man": "диалоги: список (мужчина)"}, "dialog_read_scenario": {"lady": "диалог: переписка (девушка)", "man": "диалог: переписка (мужчина)"}, "headers": {"Referer": "https://goldenbride.net/chat"}, "heartbeat": {"body": "mode=setAuthUserOnline", "path": "/goldenbride/services/chat/v5"}, "logout_scenario": {"lady": "выход (девушка)", "man": "выход (мужчина)"}, "profile": {"field": "user", "method": "GET", "path": "/goldenbride/services/userApiV2?requestType=ManStats"}, "profile_scenario": {"lady": "анкета (девушка)"}, "send": {"ack": {"ok_mark": "confirmFromServer"}, "frame": {"content": "{{text}}", "messageIdForConfirm": "{{uid}}", "method": "sendPrivateMessage", "toId": "{{to}}"}, "transport": "ws"}, "status": [{"auth_field": "auth", "body": "mode=update_v2", "path": "/goldenbride/services/chat/v26"}, {"body": "7|0|10|https://goldenbride.net/ladymodule/|6194265CA9BA5095E40F4AEE22413C7F|com.lady.shared.dataaccess.IDataServlet|executeRequest|com.lady.shared.dataaccess.RequestDTO/370062081|java.util.HashMap/1797211028|java.lang.String/2004016611|timeZone|UTC|com.lady.server.requestprocessors.lady.event.LadyEventRequestProcessor|1|2|3|4|1|5|5|6|1|7|8|7|9|10|", "headers": {"Content-Type": "text/x-gwt-rpc; charset=UTF-8", "X-GWT-Module-Base": "https://goldenbride.net/ladymodule/", "X-GWT-Permutation": "8D7AC9E3818039E53F8C64289B32F39E"}, "method": "POST", "ok_mark": "//OK", "path": "/ladymodule/services/rpc"}], "unread": {"field": "unreadMail", "method": "GET", "path": "/goldenbride/services/userApiV2?requestType=LetterStats"}}')
 WHERE site_id = 19
   AND (settings IS NULL
        OR JSON_EXTRACT(settings, '$.presence') IS NULL
        OR JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) = '');

-- ------------------------------------------- #343 (Ladadate)
UPDATE dating_site
   SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()), '$.presence',
                           '{"answer_language": "en", "base": "https://ladadate.com", "send": {"body": "id={{to}}&body={{text}}", "content_type": "application/x-www-form-urlencoded; charset=UTF-8", "encode": "js_escape_form", "ok_mark": "\"IsSuccess\":true", "path": "/chat-send", "transport": "http"}}')
 WHERE site_id = 343
   AND (settings IS NULL
        OR JSON_EXTRACT(settings, '$.presence') IS NULL
        OR JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) = '');

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап, site_id, name,
       JSON_TYPE(JSON_EXTRACT(settings, '$.presence')) AS тип_значения,
       JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence')) LIKE '%"send"%' AS отправка_на_месте,
       CHAR_LENGTH(JSON_UNQUOTE(JSON_EXTRACT(settings, '$.presence'))) AS байт
  FROM dating_site WHERE site_id IN (19, 343) ORDER BY site_id;
