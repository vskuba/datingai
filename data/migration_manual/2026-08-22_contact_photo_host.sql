-- Фотографии собеседниц: дописать хост сайта (выпуск 2026-08-22)
--
-- Зачем вручную: это правка ДАННЫХ, а не схемы. Код с этого выпуска пишет
-- фотографию собеседницы через `_photo()` — то есть уже с хостом, как это давно
-- делалось для фотографии самой анкеты. Строки, записанные до правки, остались
-- с путём от корня сайта, и новый код их не трогает: он чинит запись, а не
-- прошлое.
--
-- ЧТО БЫЛО: `dating_contact.photo` вида `/images/designNew/Default-image-07-Man.png`.
-- Страница подставляет это в `<img src>` как есть, браузер достраивает путь до
-- адреса панели — и просит картинку у неё. Её там нет: 404 и пустой кружок
-- вместо аватарки.
--
-- ЧТО МЕНЯЕТСЯ: у таких строк спереди появляется адрес сайта. Полные адреса
-- (`http…`, обычно CDN) и пустые значения не трогаются.
--
-- ХОСТ НЕ УГАДЫВАЕТСЯ. Он берётся из `presence.base` закладки сайта в
-- RhapsodyAi — там же, откуда его берёт код:
--
--   docker exec dating_uvicorn python3 -c "
--   import asyncio, json
--   from src.rhapsody.rhapsody_api import rhapsody_api_site_get
--   async def main():
--       site = await rhapsody_api_site_get(19)
--       p = site.get('presence') or {}
--       print(json.loads(p).get('base') if isinstance(p, str) else p.get('base'))
--   asyncio.run(main())"
--
-- Значения ниже сняты этой командой на проде 2026-08-22. Сайтов с битыми
-- строками был один; если появится второй — скопировать блок, поменяв пару
-- `@site_id` / `@base`.
--
-- Идемпотентность: условие отбирает только строки без `http` в начале, а после
-- прогона таких не остаётся. Файл можно гонять сколько угодно раз.
--
-- ПЕРЕД ПРОГОНОМ: дамп базы (`.claude/docs/migration.md`, раздел 3).

-- Сайт 19 — Goldenbride.
SET @site_id := 19;
SET @base    := 'https://goldenbride.net';

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM dating_contact c JOIN dating_girl g ON g.id = c.girl_id
         WHERE g.site_id = @site_id AND c.photo <> '' AND c.photo NOT LIKE 'http%')
           AS без_хоста,
       (SELECT COUNT(*) FROM dating_contact WHERE photo LIKE 'http%') AS с_хостом,
       (SELECT COUNT(*) FROM dating_contact WHERE photo = '') AS без_фото;

-- ------------------------------------------- Дописываем хост
--
-- Склейка повторяет `_photo()` буква в букву: у базы снимается косая справа, у
-- пути — слева. Иначе на стыке вышло бы `//`, а это для браузера не путь, а
-- начало адреса другого хоста.
UPDATE dating_contact c
  JOIN dating_girl g ON g.id = c.girl_id
   SET c.photo = CONCAT(TRIM(TRAILING '/' FROM @base), '/',
                        TRIM(LEADING '/' FROM c.photo))
 WHERE g.site_id = @site_id
   AND c.photo <> ''
   AND c.photo NOT LIKE 'http%';

-- ---------------------------------------------------------------- После
--
-- Смотреть глазами, а не по коду возврата: `без_хоста` обязан стать нулём, а
-- `пример` — начинаться с адреса сайта.
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM dating_contact c JOIN dating_girl g ON g.id = c.girl_id
         WHERE g.site_id = @site_id AND c.photo <> '' AND c.photo NOT LIKE 'http%')
           AS без_хоста,
       (SELECT COUNT(*) FROM dating_contact WHERE photo LIKE 'http%') AS с_хостом,
       (SELECT COUNT(*) FROM dating_contact WHERE photo = '') AS без_фото;

-- Отбор идёт по `site_id`, а не по `photo LIKE @base`: пользовательская
-- переменная приходит в коллации соединения (`utf8mb4_0900_ai_ci`), колонка
-- живёт в `utf8mb4_unicode_ci`, и сравнение этих двух — `ERROR 1267`. В `CONCAT`
-- выше переменная безопасна: там она не сравнивается, а склеивается.
SELECT 'пример' AS этап, c.photo
  FROM dating_contact c
  JOIN dating_girl g ON g.id = c.girl_id
 WHERE g.site_id = @site_id AND c.photo <> ''
 LIMIT 1;
