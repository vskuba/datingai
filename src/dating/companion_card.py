"""Единственная дверь записи карточки собеседника.

## ⚠⚠ Зачем дверь одна

Карточку писали три места, и каждое по-своему: ручка ленты слала полный набор
колонок, забор избранных — свой, а `companion_touch` правил только названное.
Расходились они не в мелочах:

* `people_name_case` звала ручка и забор, а `companion_touch` — **нет**, и имя
  «VINCENT» доезжало до письма криком;
* `_ensure` заводил карточку **без имени вовсе**, и до первого её обновления
  подстановка не находила ни `companion_name`, ни `companion_first_name`.

## ⚠⚠⚠ И зачем она нужна теперь особенно: части имени стали колонками

`firstname` и `lastname` лежат в карточке (`20260929180000_companion_name_parts`).
Прежнее решение — «выводим, а не храним» — возражало против этого прямо: колонки
разойдутся с именем в первую же правку, и письмо уйдёт с прежним обращением.

Возражение снимается не обещанием, а тем, что **имя и его части пишутся одним
вызовом**: `companion_save` пересчитывает части всякий раз, когда меняет `name`,
и второго места, где `name` меняется в одиночку, нет. Заведи такое — и возражение
снова станет верным, поэтому в сюите стоит сторож на единственность двери.

⚠ **Выводимые части при этом остаются** (`mailing_person_fields`): набор пула
подставляет переменные из ленты «Все онлайн», а не из карточки, и колонок в тех
строках не будет никогда.

## ⚠ Пишем только названное

Сведения о человеке приходят из разных мест: имя и фото — из ленты, описание и
город — со страницы анкеты. Пиши мы строку целиком, каждый источник затирал бы
чужое своими пустотами. Поэтому «пустое значит не знаю» решает **вызывающий** —
он просто не передаёт того, чего не знает.

⚠⚠ Внутрь это правило не перенести: `about_error=''` — законная запись, ею
ошибку **стирают** после удачного захода. Отфильтруй мы пустые здесь, ошибка
осталась бы на карточке навсегда.

⚠ Своего `INSERT`-сборщика общий слой под это не даёт: `sql_build_insert` клеит
готовый текст для миграций и переносов, а здесь значения приходят с чужого сайта
и обязаны уехать **подстановкой**, а не в текст запроса.
"""

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.dating.people import people_name_case, people_name_parts

# Колонки, которые эта дверь умеет писать. ⚠ Список закрытый: имя колонки уезжает
# в SQL текстом, и принимать его от вызывающего как есть нельзя.
#
# ⚠ `firstname` и `lastname` здесь **нет намеренно** — их не передают, а считают
# из `name` (ниже). Дай мы их писать снаружи, появился бы способ разойтись с именем.
COMPANION_CARD_COLUMNS = ('name', 'photo', 'age', 'city', 'country', 'email',
                          'about', 'about_state', 'about_error', 'params')


def companion_card_name(name) -> dict:
    """Имя и его части — то, что ложится в карточку вместе.

    Args:
        name: имя, как его отдал сайт («VINCENT», «Brian Ritter»).

    Returns:
        dict: `name`, `firstname`, `lastname` — готовые к записи.

    ⚠ Регистр правится **до** разбора: «VINCENT PITCHEN» должен дать «Vincent»,
    а не «VINCENT». Разбери мы сначала, крик уехал бы в первое слово.
    """
    said = people_name_case(str(name or ''))
    parts = people_name_parts(said)

    return {'name': said,
            'firstname': parts['firstname'],
            'lastname': parts['lastname']}


async def companion_save(site_id, uid, **fields) -> bool:
    """Заводит или дописывает карточку. Возвращает, дошло ли до базы.

    Args:
        site_id: сайт-закладка — человек принадлежит сайту, а не анкете.
        uid: номер человека на сайте.
        **fields: любые из `COMPANION_CARD_COLUMNS`. Чего не назвали — не трогаем.
            Имя нормализуется и тянет за собой `firstname` с `lastname`.

    Returns:
        bool: `False` — база не ответила (причина в журнал). Карточка не то место,
        ради которого стоит валить круг рассылки.

    ⚠ Незнакомые ключи **молча отбрасываются**: опечатка в имени поля не должна
    ни падать, ни писать мусор.
    """
    uid = str(uid or '').strip()
    if not uid:
        return False

    known = {name: value for name, value in fields.items()
             if name in COMPANION_CARD_COLUMNS}

    # ⚠⚠ Имя и части — одной связкой. Здесь и только здесь, и потому расходиться
    # им негде: `name` без пересчёта частей отсюда не уходит.
    if 'name' in known:
        known.update(companion_card_name(known.pop('name')))

    if 'age' in known:
        known['age'] = int(known['age'] or 0)

    cols = list(known)
    # ⚠⚠ Время ответа сайта — вместе с состоянием: по нему видно, давно ли
    # спрашивали, и оно же отвечает на «не пора ли ещё раз».
    #
    # ⚠⚠ Идёт **и во вставку, и в обновление**, а не только во второе. Сперва
    # стояло только в `ON DUPLICATE KEY UPDATE` — и у карточки, заведённой этим
    # же вызовом (а таких большинство: описание собирают для тех, кого в лентах
    # не видели), срабатывала ветка вставки, где `about_at` не было вовсе. Дата
    # оставалась `NULL`, то есть «никогда не спрашивали» про только что
    # спрошенное — и круг пошёл бы за описанием снова. Поймано сюитой.
    stamp = 'about_state' in known
    raw = ['about_at'] if stamp else []

    if cols or raw:
        names = cols + raw
        holes = ['%s'] * len(cols) + ['NOW()'] * len(raw)
        sql = ('INSERT INTO companion (site_id, uid, ' + ', '.join(names) + ')'
               ' VALUES (%s, %s, ' + ', '.join(holes) + ')'
               ' ON DUPLICATE KEY UPDATE '
               + ', '.join([f'{one} = VALUES({one})' for one in cols]
                           + [f'{one} = NOW()' for one in raw]))
        args = (int(site_id or 0), uid, *[known[one] for one in cols])
    else:
        # ⚠ Пустая карточка — законный случай: описание собирают и для того, кого
        # в лентах ещё не видели. `INSERT IGNORE` ловит гонку двух кругов.
        sql = ('INSERT IGNORE INTO companion (site_id, uid, name)'
               " VALUES (%s, %s, '')")
        args = (int(site_id or 0), uid)

    try:
        async with mysql_get_db_async() as db:
            await db.execute(sql, args)
            await db.connection.commit()
    except Exception as e:
        logger_info(f'[dating] карточка {uid} сайта #{site_id} не легла: {e}')

        return False

    return True
