-- Карточка анкеты: номер одним ключом, без дубля (2026-09-27)
--
-- ## ЗАЧЕМ (указание человека)
--
-- В JSON-столбце `profile.profile` номер анкеты на сайте лежал **дважды**, под
-- ключами `id` и `site_uid`, с одним и тем же значением:
--
--     ["id", "age", "city", "name", "about", "photo", "site_uid"]
--
-- Дубль завёлся не замыслом, а тем, что карточку пишут двое: вход
-- (`site_profile.card_parse`) кладёт один `id`, импорт анкет клал оба. Читатели
-- разбрелись — десять мест, и каждое выбрало своё: `answer.py` спрашивал
-- `site_uid or id`, `pool_pair.py` только `site_uid`, `agent.py` только `id`.
--
-- ## ⚠⚠ Чем это опасно, если не трогать
--
-- Два имени одной величины живут мирно ровно до дня, когда их **разведут по
-- смыслу**, не заметив. Достаточно одного запроса, заполнившего `id`, но не
-- `site_uid`, — и половина кода увидит номер, а половина пустоту. Ни одна проверка
-- не упадёт: обе величины строки, обе бывают пустыми законно.
--
-- ⚠ Это уже началось: `id` пишут оба писателя, `site_uid` — только импорт. Анкета,
-- заведённая входом, а не импортом, дубля не имеет вовсе, и `pool_pair.py`
-- показывал бы у неё пустой номер.
--
-- ## Что делает миграция
--
-- Убирает ключ `site_uid` из карточек. Значение не теряется: оно и так лежит под
-- `id`, и миграция трогает **только те** карточки, где они совпадают.
--
-- ⚠⚠ Расхождение — не наш случай, и молчать о нём нельзя. Если у какой-то анкеты
-- `id` и `site_uid` разные, ключ остаётся на месте, а отчёт говорит сколько таких:
-- удалить в этом случае значило бы выбросить величину, о которой мы ничего не
-- знаем. Проверено до правки: расхождений нет ни на проде (15 анкет), ни локально.
--
-- ⚠ Колонки `site_uid` не существует и не существовало — ни в одной таблице. Номер
-- живёт колонкой `profile.profile_id` (заведена `20260927130000`) и ключом `id` в
-- карточке; здесь убирается третье, лишнее его написание.
--
-- Идемпотентность: `JSON_REMOVE` отсутствующего ключа ничего не делает.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `profile`
         WHERE JSON_VALID(`profile`)
           AND JSON_CONTAINS_PATH(`profile`, 'one', '$.site_uid')) AS с_дублем,
       (SELECT COUNT(*) FROM `profile`
         WHERE JSON_VALID(`profile`)
           AND JSON_CONTAINS_PATH(`profile`, 'one', '$.site_uid')
           AND JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.site_uid'))
               <> COALESCE(JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')), ''))
         AS расходятся,
       (SELECT COUNT(*) FROM `profile`) AS всего;

-- ⚠ Условие `id = site_uid` — не перестраховка, а отказ трогать непонятное:
-- совпали — дубль, не совпали — две разные величины, и решать это не миграции.
UPDATE `profile`
   SET `profile` = JSON_REMOVE(`profile`, '$.site_uid')
 WHERE JSON_VALID(`profile`)
   AND JSON_CONTAINS_PATH(`profile`, 'one', '$.site_uid')
   AND JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.site_uid'))
       = COALESCE(JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')), '');

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(*) FROM `profile`
         WHERE JSON_VALID(`profile`)
           AND JSON_CONTAINS_PATH(`profile`, 'one', '$.site_uid')) AS с_дублем,
       (SELECT COUNT(*) FROM `profile`
         WHERE JSON_VALID(`profile`)
           AND JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')) NOT IN ('', 'null'))
         AS с_номером;

-- ⚠⚠ Сверка по существу: номер обязан уцелеть. «Дубля нет» без этой строки
-- означало бы и «номера нет» тоже — а это две очень разные новости.
SELECT 'анкет, где номер пропал' AS проверка, COUNT(*) AS сколько
  FROM `profile`
 WHERE `profile_id` IS NOT NULL
   AND JSON_VALID(`profile`)
   AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(`profile`, '$.id')), '') <> `profile_id`;
