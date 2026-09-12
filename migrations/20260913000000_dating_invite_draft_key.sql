-- Инвайт можно повторить: ключ держит пул, а не историю (2026-09-13)
--
-- ЧТО БЫЛО. `UNIQUE (profile_id, uid)` — «одна анкета зовёт одного человека один
-- раз». Ключ писался, когда правило и было таким: позвали — и всё.
--
-- ПОЧЕМУ МЕНЯЕМ. Правило с тех пор изменилось, а ключ нет. Инвайт заканчивается
-- **ответом**, а не отправкой: пока человек молчит, звать его можно снова — не
-- чаще `invite_repeat_after` и не больше `invite_repeat_max_day` раз в сутки. Код
-- это уже умеет (`_invited` в `dating_invite.py`), а база — нет.
--
-- ⚠ И отказывала она **молча**. Кладёт инвайт `INSERT IGNORE`, поставленный там
-- против гонки двух заходов: наткнувшись на ключ, он ничего не вставляет и
-- ничего не говорит. Круг при этом уже сходил к агенту за текстом и засчитал
-- строку сделанной — то есть каждый заход тратил вызов LLM ни на что.
--
-- Поймано живьём и потому чинится. Semen Gorbunkov отмечен приоритетом, стоит в
-- ленте первым, свободен для Соломии — и не попадал в пул больше часа: строка
-- `sent` от 21:16 держала за него ключ. Анкета на сайте активна одна, так что
-- «позовёт другая девушка» его не спасало.
--
-- ЧТО СТАНОВИТСЯ. Ключ переезжает с истории на **пул**: одна анкета держит для
-- одного человека **один черновик**, а сколько ушедших строк лежит рядом — база
-- больше не спрашивает. Ровно то, чего от неё и хотят: «в пуле всегда один инвайт
-- к одному собеседнику, и он ждёт своего времени».
--
-- КАК. Столбец-выражение `draft_uid` равен `uid` у черновика и NULL у всего
-- прочего, а уникальный индекс ставится на него. NULL в уникальном индексе MySQL
-- не считает повтором — отсюда «сколько угодно ушедших, но один черновик». Без
-- него пришлось бы сторожить это кодом, то есть чтением перед записью, то есть
-- гонкой — тем самым, от чего ключ здесь и стоит.
--
-- ⚠ ЧТО ЭТО НЕ ОТМЕНЯЕТ. «Один инвайт на человека, а не на пару» — правило набора
-- (`_pairs`), и оно остаётся в коде: две наши анкеты, позвавшие одного мужчину,
-- выдают агентство. База про это не знает и знать не должна — там пара.
--
-- Идемпотентность: и столбец, и оба индекса меняются по условию.

-- ---------------------------------------------------------------- До
SELECT 'ДО' AS этап,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
           AND INDEX_NAME = 'uq_dating_invite') AS старый_ключ,
       (SELECT COUNT(*) FROM `dating_invite` WHERE `status` = 'draft') AS черновиков,
       (SELECT COUNT(*) FROM `dating_invite` WHERE `status` = 'sent') AS ушедших;

-- ------------------------------------------- Столбец-выражение под черновики
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
        AND COLUMN_NAME = 'draft_uid') = 0,
    'ALTER TABLE `dating_invite`
        ADD COLUMN `draft_uid` VARCHAR(32)
            GENERATED ALWAYS AS (IF(`status` = ''draft'', `uid`, NULL)) STORED
            COMMENT ''Номер собеседника, пока строка в пуле. NULL у ушедших — их ключ не держит''
            AFTER `uid`',
    'SELECT ''столбец draft_uid уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Новый ключ: один черновик на пару
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
        AND INDEX_NAME = 'uq_dating_invite_draft') = 0,
    'ALTER TABLE `dating_invite`
        ADD UNIQUE KEY `uq_dating_invite_draft` (`profile_id`, `draft_uid`)',
    'SELECT ''ключ uq_dating_invite_draft уже есть'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ------------------------------------------- Старый ключ уходит
--
-- ⚠ Порядок именно такой: новый ключ заводится **до** снятия старого. Между двумя
-- ALTER'ами таблица живёт под работающим воркером, и мгновение без единого ключа
-- означало бы два черновика одной паре — ровно то, от чего ключ и стоит.
SET @sql = IF(
    (SELECT COUNT(*) FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
        AND INDEX_NAME = 'uq_dating_invite') > 0,
    'ALTER TABLE `dating_invite` DROP INDEX `uq_dating_invite`',
    'SELECT ''старого ключа уже нет'' AS пропуск');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- ---------------------------------------------------------------- После
SELECT 'ПОСЛЕ' AS этап,
       (SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
           AND INDEX_NAME = 'uq_dating_invite_draft') AS новый_ключ,
       (SELECT COUNT(*) FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dating_invite'
           AND INDEX_NAME = 'uq_dating_invite') AS старый_ключ,
       (SELECT COUNT(*) FROM `dating_invite` WHERE `draft_uid` IS NOT NULL) AS черновиков;
