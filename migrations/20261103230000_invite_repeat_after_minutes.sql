-- Пауза повтора инвайта переехала из секунд в минуты (2026-11-03)
--
-- ЗАЧЕМ. Поле было набором со сроками в секундах («5 минут» = `300`), стало
-- числовым инпутом в минутах — по решению владельца. Значение лежит в
-- `site.settings` строкой, и без перевода прежние числа прочитались бы как
-- минуты: `300` стало бы пятью часами, а `86400` — двумя месяцами.
--
-- ⚠⚠⚠ ОШИБКА БЫЛА БЫ МОЛЧАЛИВОЙ. Ни запрос, ни круг не заметят разницы: они
-- получат число и честно подставят его в `INTERVAL`. Увидеть это можно только
-- по поведению — инвайты перестали бы уходить повторно вовсе, и объяснить,
-- почему, стало бы нечем.
--
-- ⚠⚠ ПЕРЕВОДИМ ТОЛЬКО ПОХОЖЕЕ НА СЕКУНДЫ. Прежний набор давал ровно семь
-- значений (300, 3600, 21600, 43200, 86400, 259200, 604800), и все они делятся
-- на 60 без остатка и не меньше 300. Число вне этого ряда — уже выставленное
-- руками в новой единице, и трогать его нельзя: перевод применился бы дважды.
--
-- ⚠ Замер прода 03.11.2026: Ladadate и Step2Love `300`, Goldenbride и Luxee
-- `86400`. После миграции — `5` и `1440`.

SELECT 'ДО' AS этап, `site_id`,
       JSON_UNQUOTE(JSON_EXTRACT(`settings`, '$.invite_repeat_after')) AS было
  FROM `site`
 WHERE JSON_EXTRACT(`settings`, '$.invite_repeat_after') IS NOT NULL;

-- ⚠ `CAST` через `UNSIGNED`: значение лежит строкой, и сравнение строк дало бы
-- `'86400' < '300'` — лексикографически это правда.
--
-- ⚠⚠ Делим через `DIV`, а не `/`: обычное деление в MySQL даёт `DECIMAL`, и в
-- колонку легло бы `'5.0000'`. Поймано репетицией — число осталось бы верным,
-- а инпут на странице показал бы дробь, и первое же сохранение формы
-- переписало бы её обратно в целое, молча меняя значение у тех сайтов, куда
-- никто не заходил.
UPDATE `site`
   SET `settings` = JSON_SET(
         `settings`, '$.invite_repeat_after',
         CAST(CAST(JSON_UNQUOTE(JSON_EXTRACT(`settings`,
                                '$.invite_repeat_after')) AS UNSIGNED) DIV 60
              AS CHAR))
 WHERE JSON_EXTRACT(`settings`, '$.invite_repeat_after') IS NOT NULL
   AND JSON_UNQUOTE(JSON_EXTRACT(`settings`, '$.invite_repeat_after'))
         REGEXP '^[0-9]+$'
   AND CAST(JSON_UNQUOTE(JSON_EXTRACT(`settings`,
                         '$.invite_repeat_after')) AS UNSIGNED) >= 300
   AND CAST(JSON_UNQUOTE(JSON_EXTRACT(`settings`,
                         '$.invite_repeat_after')) AS UNSIGNED) % 60 = 0;

SELECT 'ПОСЛЕ' AS этап, `site_id`,
       JSON_UNQUOTE(JSON_EXTRACT(`settings`, '$.invite_repeat_after')) AS стало
  FROM `site`
 WHERE JSON_EXTRACT(`settings`, '$.invite_repeat_after') IS NOT NULL;
