-- Два оставшихся счётчика вкладки получают свой индекс — на вырост (2026-10-31)
--
-- ЗАЧЕМ. `20261031200000_message_live_index.sql` снял перебор у третьего
-- счётчика вкладки (`site_live._chat`): 44,6 мс → 1,7 мс на проде. Два
-- соседних — письма (`_mail`) и обещания агента (`_intention`) — отбирают
-- **тем же способом**, по `created_at`, и индекса под него не имеют тоже.
--
-- ⚠⚠ СЕГОДНЯ ЭТИ ИНДЕКСЫ НЕ ДАЮТ ВЫИГРЫША, и это сказано честно: в `mail_in`
-- двадцать семь строк, в `chat_intention` — семь, оба запроса и так стоят по
-- полторы миллисекунды — то есть одну дорогу до базы, а не чтение строк.
--
-- ⚠ Оптимизатор при этом берёт их **сам**, не дожидаясь роста: индексы
-- покрывающие, и пройти по ним дешевле, чем читать строки, даже когда строк
-- семь (проверено `EXPLAIN` без `FORCE INDEX` — `type: range`,
-- `Using index`). Здесь стояло обратное утверждение, и замер его опроверг.
--
-- Это **заготовка от роста**, а не починка замеренной беды. Сравнение с тем,
-- что чинили у `message`, здесь уместно и показывает разницу:
--
--     message          43 782 строки   ← перебор стоил 44,6 мс, чинили
--     mail_in              27 строк    ← 11 за неделю, ~1,5 в сутки
--     chat_intention        7 строк    ← занятие заведено 30.10, счёт идёт
--
-- При таком темпе `mail_in` наберёт пять тысяч строк годам к десяти. То есть
-- беды может не случиться вовсе — и если через год станет видно, что индексы
-- простаивают, убрать их будет правильным решением, а не признанием ошибки.
--
-- ⚠ ВЕДУЩАЯ КОЛОНКА — `created_at`, по тому же доводу, что у `message`: срок
-- отсекает почти всё, а `status <> 'waiting'` не отсекает ничего (сейчас
-- таких строк семь из семи) да ещё и неравенство, которым индекс не ведут.
--
-- ⚠ Оба индекса **покрывающие**: кроме `created_at` в них лежит всё, что
-- запрос спрашивает (`site_id`, а у намерений ещё `status`). Значит обращений
-- к самим строкам не будет вовсе — ни одной лишней страницы с диска.
--
-- ⚠ Имя кончается на `_live` — как `idx_dating_message_live`. Суффикс говорит,
-- кто этот индекс заказал: все три читает `src/dating/site_live.py`, и
-- трогающий его увидит связь, не разбирая планы запросов.
--
-- ⚠⚠ Оба **не уникальные**: отказать по накопленным повторам не могут по
-- устройству. После аварии 31.10 это проверяется заставой выкладки
-- (`unique-on-defaulted-column`), но сказать стоит и здесь.

SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM `mail_in`) AS писем,
       (SELECT COUNT(*) FROM `chat_intention`) AS намерений,
       (SELECT COUNT(DISTINCT `INDEX_NAME`) FROM `information_schema`.`STATISTICS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `INDEX_NAME` IN ('idx_dating_mail_in_live',
                                'idx_chat_intention_live')) AS индексов_уже;

-- 1. Письма: срок плюс сайт — покрывает отбор и группировку целиком.
SET @есть = (SELECT COUNT(DISTINCT `INDEX_NAME`) FROM `information_schema`.`STATISTICS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'mail_in'
                AND `INDEX_NAME` = 'idx_dating_mail_in_live');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `mail_in`
     ADD KEY `idx_dating_mail_in_live` (`created_at`, `site_id`)',
  'SELECT ''индекс писем уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2. Намерения: срок, состояние, сайт. ⚠ `status` вторым, а не первым —
--    неравенством индекс не ведут, а в покрытии он нужен.
SET @есть = (SELECT COUNT(DISTINCT `INDEX_NAME`) FROM `information_schema`.`STATISTICS`
              WHERE `TABLE_SCHEMA` = DATABASE()
                AND `TABLE_NAME` = 'chat_intention'
                AND `INDEX_NAME` = 'idx_chat_intention_live');

SET @sql = IF(@есть = 0,
  'ALTER TABLE `chat_intention`
     ADD KEY `idx_chat_intention_live` (`created_at`, `status`, `site_id`)',
  'SELECT ''индекс намерений уже есть — пропуск'' AS пропуск');

PREPARE st FROM @sql;
EXECUTE st;
DEALLOCATE PREPARE st;

SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(DISTINCT `INDEX_NAME`) FROM `information_schema`.`STATISTICS`
         WHERE `TABLE_SCHEMA` = DATABASE()
           AND `INDEX_NAME` IN ('idx_dating_mail_in_live',
                                'idx_chat_intention_live')) AS индексов_стало,
       (SELECT COUNT(*) FROM `mail_in`) AS писем_осталось,
       (SELECT COUNT(*) FROM `chat_intention`) AS намерений_осталось;
