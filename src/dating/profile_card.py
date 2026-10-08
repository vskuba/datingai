"""Карточка анкеты: что в ней правит человек и что это защищает от перезаписи.

Карточка (`profile.profile`, JSON) — **кэш того, что отдал сайт**, и писателей у
неё двое: импорт анкет (`import_.py`) и чтение страницы кабинета
(`profile_page.py`). Человек стал третьим: окно правки на вкладке «Общая
информация» даёт вписать то, чего сайт не отдал или отдал неверно.

## ⚠⚠ Правило одно, а мест применения два

Правленое руками сайт перезаписывать не вправе — таково правило проекта («не
трогаем то, что завёл человек», `import_.py`). Проверять это приходится в **двух**
писателях, и своя копия правила в каждом разошлась бы на первой правке: одна
половина системы уважала бы ручную правку, другая затирала. Поэтому правило живёт
здесь одной функцией `profile_card_keep`, а оба писателя её зовут.

## ⚠ Почему отметка выбрасывает поле, а не перекрывает его

`profile_card_keep` возвращает пришедшее с сайта **без** правленых ключей, а не
подменяет их прежними значениями. Так проще и так вернее: у обоих вызывающих
прежняя карточка под рукой (`{**before, **fresh}`), и класть её значения второй раз
означало бы два источника правды в одном выражении.

## ⚠ Номер анкеты — поле карточки, хотя живёт ещё и колонкой

Ключ `id` в списке правимых полей есть, и это не описка: номер записан дважды —
колонкой `profile.profile_id` и ключом карточки, — потому что читают их разные
места (ключ фактов агента берёт колонку, сведение переписки из панели и ссылка на
сайт берут карточку). Отметка защищает обе записи сразу: пока она стоит, импорт не
трогает ключ, а колонку он не пишет вовсе.

⚠ Почты (`login`) среди полей нет намеренно. Это колонка учётки, и импорт у
существующей анкеты её не трогает (`_profile_update` пишет только `profile`).
Отметка защищает от перезаписи; защищать почту не от кого.
"""

import json

from mysql_.mysql_ import mysql_get_db_async

from src.dating.json_column import json_column_list, json_column_load

# Поля карточки, которые правят руками. ⚠ Имена — ключи самой карточки, а не
# подписи окна: по ним же ложится отметка, и второе написание развело бы их.
PROFILE_CARD_FIELDS = ('id', 'name', 'age', 'city', 'about')

# Чем «пусто» у каждого поля. ⚠ Нужно потому, что карточку собирают разные
# писатели и ключа может не быть вовсе: без этого «возраст 0 там, где ключа нет»
# читалось бы как правка, и окно, открытое поглядеть и сохранённое без изменений,
# заморозило бы возраст навсегда.
PROFILE_CARD_EMPTY = {'id': '', 'name': '', 'age': 0, 'city': '', 'about': ''}


def profile_card_edited(value) -> list[str]:
    """Отметки из колонки `profile_edited` — списком известных имён.

    Args:
        value: значение колонки: список, строка JSON или `None`.

    Returns:
        list: имена правленых полей в порядке объявления `PROFILE_CARD_FIELDS`.
        Незнакомые имена и испорченный JSON дают пустой список.

    ⚠ Чужие имена отсеиваются, а не переносятся как есть: строка, попавшая в
    колонку мимо нашей двери, иначе отняла бы у сайта поле, которого в карточке
    нет вовсе.

    ⚠ Порядок — наш, а не как лежало: он уезжает в ответ ручки и в окно, и
    «то же самое, но в другом порядке» выглядело бы изменением.
    """
    said = {str(one) for one in json_column_list(value)}

    return [name for name in PROFILE_CARD_FIELDS if name in said]


def profile_card_keep(fresh: dict, edited) -> dict:
    """Пришедшее с сайта без полей, которые правил человек.

    Args:
        fresh: карточка или её часть, как её отдал сайт.
        edited: значение колонки `profile_edited` — список, строка JSON или `None`.

    Returns:
        dict: копия `fresh` без правленых ключей. Отметок нет — копия целиком.
    """
    skip = set(profile_card_edited(edited))

    return {name: value for name, value in (fresh or {}).items() if name not in skip}


# Имена уникальных ключей таблицы. ⚠ Нужны **оба и порознь**: по ним причина
# отказа узнаётся точно, а «просто дубль» отправил бы человека править не то поле.
PROFILE_CARD_KEY_LOGIN = 'uk_dating_profile_site_login'
PROFILE_CARD_KEY_UID = 'uk_dating_profile_site_uid'


async def profile_card_save(profile_id: int, values: dict) -> dict:
    """Записать правку руками: карточка, две колонки и отметки одним заходом.

    Args:
        profile_id: наш номер анкеты.
        values: что пришло из окна. `login` — почта; остальные ключи — имена из
            `PROFILE_CARD_FIELDS`. Отсутствующий ключ значит «не трогать».

    Returns:
        dict: `login`, `card` и `edited` — что в итоге легло.

    Raises:
        LookupError: анкеты с таким номером нет.
        ValueError: почта или номер на сайте уже заняты — причина словами.

    ## ⚠⚠ Номер уезжает в оба написания

    Колонка `profile_id` и ключ `id` карточки держат одну и ту же величину, и
    читают их разные места: ключ фактов агента — колонку (`agent_site_uid`),
    сведение переписки из панели и ссылка на сайт — карточку. Пишем их **одним
    `UPDATE`**: разойдясь, они не уронят ни одной проверки — половина кода просто
    увидит номер, а половина пустоту.

    ## ⚠⚠ Метится изменённое, а не присланное

    Поле попадает в отметки, только если значение разошлось с тем, что лежит.
    Иначе человек, открывший окно поглядеть и нажавший «Сохранить», заморозил бы
    все пять полей разом — и сайт больше не обновил бы ни одного.

    ⚠ Стирание при этом — правка: вписанное пустым значит «неправда, не знаем», и
    без отметки импорт вернул бы убранное.

    ⚠ Совпало с тем, что лежит, — отметка **не ставится и не снимается**. Снять её
    можно только «вернуть сайту» (`profile_card_forget`): это решение человека, и
    угадывать его по совпадению значений нельзя — девушку могли переименовать в то
    же самое.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT login, profile, profile_edited FROM profile '
                         ' WHERE id = %s', (int(profile_id),))
        row = await db.fetchone()

    if not row:
        raise LookupError(f'анкеты #{int(profile_id)} нет')

    before = json_column_load(row['profile'])
    card = dict(before)
    edited = set(profile_card_edited(row['profile_edited']))

    for field in PROFILE_CARD_FIELDS:
        if field not in values:
            continue

        if _same(field, before.get(field), values[field]):
            continue

        card[field] = values[field]
        edited.add(field)

    login = str(values.get('login') or '').strip() or str(row['login'] or '')
    # ⚠ Пусто ложится `NULL`, а не `''`: уникальный ключ считает пустые строки
    # одинаковыми, а `NULL`-ы — разными, и второй анкеты без номера не было бы.
    uid = str(card.get('id') or '').strip() or None
    marks = profile_card_edited(sorted(edited))

    try:
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE profile SET login = %s, profile_id = %s, profile = %s, '
                '       profile_edited = %s WHERE id = %s',
                (login, uid, json.dumps(card, ensure_ascii=False),
                 json.dumps(marks) if marks else None, int(profile_id)))
            await db.connection.commit()
    except Exception as error:
        raise ValueError(profile_card_why(error, login, str(uid or ''))) from error

    return {'login': login, 'card': card, 'edited': marks}


async def profile_card_forget(profile_id: int, field: str) -> list[str]:
    """Снять отметку с поля — «вернуть сайту».

    Args:
        profile_id: наш номер анкеты.
        field: имя поля карточки из `PROFILE_CARD_FIELDS`.

    Returns:
        list: отметки, которые остались.

    Raises:
        ValueError: такого поля среди правимых нет.
        LookupError: анкеты нет.

    ⚠ **Значение остаётся на месте.** Стереть его значило бы выбросить то, что
    человек видел в поле; обновит его следующий импорт, когда сайт отдаст своё.

    ⚠ Отметка нужна человеку именно снимаемой: без этого правило «сайт правленое
    не трогает» становится ловушкой — поправивший имя однажды отрезал бы его от
    сайта навсегда и узнал об этом только тем, что имя больше не обновляется.
    """
    if field not in PROFILE_CARD_FIELDS:
        raise ValueError(f'поля «{field}» в карточке анкеты нет')

    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile_edited FROM profile WHERE id = %s',
                         (int(profile_id),))
        row = await db.fetchone()
        if not row:
            raise LookupError(f'анкеты #{int(profile_id)} нет')

        left = [one for one in profile_card_edited(row['profile_edited'])
                if one != field]
        # ⚠ Пустые отметки ложатся `NULL`, а не `'[]'`: «отметок нет» должно иметь
        # одно написание — то же правило, что в миграции колонки.
        await db.execute('UPDATE profile SET profile_edited = %s WHERE id = %s',
                         (json.dumps(left) if left else None, int(profile_id)))
        await db.connection.commit()

    return left


def profile_card_why(error: Exception, login: str, uid: str) -> str:
    """Причина отказа словами вместо текста драйвера.

    Args:
        error: исключение драйвера.
        login: почта, которую пытались записать.
        uid: номер на сайте, который пытались записать.

    Returns:
        str: предложение для человека.

    ⚠ **Ключи различаются порознь, и общий признак дубля стоит последним.** У
    таблицы два уникальных ключа, и «почта занята» вместо «номер занят» отправило
    бы человека править не то поле.
    """
    text = str(error)

    if PROFILE_CARD_KEY_UID in text:
        return f'номер {uid} на этом сайте уже у другой анкеты'

    if PROFILE_CARD_KEY_LOGIN in text or '1062' in text or 'Duplicate entry' in text:
        return f'учётка «{login}» на этом сайте уже заведена'

    return f'не удалось сохранить анкету: {text}'


def _same(field: str, before, after) -> bool:
    """Совпало ли значение с тем, что лежит.

    ⚠ Отсутствующий ключ приравнивается к «пусто» своего вида
    (`PROFILE_CARD_EMPTY`): без этого присланный возраст `0` там, где ключа нет
    вовсе, читался бы как правка.
    """
    empty = PROFILE_CARD_EMPTY[field]
    was = empty if before is None else before
    now = empty if after is None else after

    return str(was) == str(now)
