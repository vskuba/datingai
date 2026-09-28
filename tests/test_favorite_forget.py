"""Ручка «убрать из фаворитов»: `favorite_forget` и её дверь.

Кнопка на красной строке «Фаворитов → Доставки» (сторож `test_favorite_unfav_button`)
зовёт `DELETE /api/dating/favorite`. Это её тело: строка пары уходит из таблицы
избранных, черновик приставания — за ней, а журнал доставки остаётся как был.

## ⚠⚠ Что ручка делает и чего не делает

Унося строку `favorite`, пара выпадает из набора писем избранным (`FAVORITE_STATES`
в наборе приставания) — в этом и смысл кнопки: ей перестают писать. Ответы пары
переезжают в журнал «Письма» (делят их журналы через `JOIN` избранных), а строки
доставки — уже случившееся, их не трогаем.

⚠ **Звезду на сайте ручка не снимает**: у сайта такой ручки нет, и наш сценарий
зажжённую звезду не жмёт вовсе (`favorite_add` — переключатель). Сидит человек на
сайте со звёздочкой — `favorite_sync` вернёт строку `already` при следующем обходе:
про избранных истина там. Кнопка стоит на красной строке ровно потому, что там
писем по-прежнему быть не должно.

⚠ Отличие от `favorite_mark` намеренное: тот глотает ошибку — фоновая работа
переживёт единичный сбой, а здесь человек нажал кнопку и ждёт ответа; база
не отвечает — пусть страница покажет причину.
"""

import uuid

import pytest
from fastapi import HTTPException

from mysql_.mysql_ import mysql_get_db_async

from src.dating.favorite import favorite_forget, favorite_settled
from src.uvicorn.routers.dating.favorite import favorite_drop

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


@pytest.fixture
async def pair(auth_client):
    """Анкета с избранным, его черновиком, его ушедшим письмом и чужими строками.

    ⚠ Чужие строки — то есть **не его**: другой избранник той же анкеты и тот же
    человек под другой анкетой. Ручка убирает пару, а не человека и не анкету:
    без этих строк `WHERE` с одним столбцом выглядел бы верно.
    """
    girl = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'favfgt-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    tag = uuid.uuid4().hex[:6]
    star, quiet, other_girl = f'star-{tag}', f'quiet-{tag}', girl + 900_000

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            " VALUES (%s, %s, %s, 'done')", (STAND_SITE, girl, star))
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            " VALUES (%s, %s, %s, 'done')", (STAND_SITE, girl, quiet))
        # Тот же человек у другой анкеты — её избранность, не наша.
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            " VALUES (%s, %s, %s, 'done')", (STAND_SITE, other_girl, star))

        # Черновик приставания — уйдёт с парой; ушедшее письмо — журнал, его не
        # трогаем ни в каком виде.
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status, error) VALUES (%s, %s, %s, 'Star', 'пристаю', 'draft',"
            " 'not favorite')", (STAND_SITE, girl, star))
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status, sent_at) VALUES (%s, %s, %s, 'Star', 'приставалось',"
            " 'sent', NOW() - INTERVAL 10 MINUTE)", (STAND_SITE, girl, star))
        await db.connection.commit()

    yield {'girl': girl, 'star': star, 'quiet': quiet, 'other': other_girl}

    async with mysql_get_db_async() as db:
        for table in ('favorite', 'favorite_mail', 'log'):
            await db.execute(f'DELETE FROM {table} WHERE profile_id IN (%s, %s)',
                             (girl, other_girl))
        await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
        await db.connection.commit()


class TestForgetsThePair:
    """Тело ручки: пара, и только она."""

    async def test_the_pair_row_goes(self, pair):
        """⚠ Строка избранных уходит — с ней пара выпадает из приставания."""
        assert await favorite_forget(pair['girl'], pair['star']) is True

        settled = await favorite_settled(pair['girl'])
        assert pair['star'] not in settled, 'пара осталась избранной'
        assert pair['quiet'] in settled, \
            'ручка задела не его: другой избранник той же анкеты ушёл'

    async def test_the_other_profile_keeps_him(self, pair):
        """⚠ Убирается **пара**, а не человек: у другой анкеты он избран и остаётся."""
        await favorite_forget(pair['girl'], pair['star'])

        async with mysql_get_db_async() as db:
            await db.execute('SELECT uid FROM favorite WHERE profile_id = %s',
                             (pair['other'],))
            rows = await db.fetchall()

        assert [r['uid'] for r in rows] == [pair['star']], \
            'у чужой анкеты избранность размылась'

    async def test_the_draft_goes_and_the_letter_stays(self, pair):
        """⚠ Черновик выметается вместе с парой — иначе круг уйдёт в отказ, а
        журнал остаётся: уже случившееся не переписывают."""
        await favorite_forget(pair['girl'], pair['star'])

        async with mysql_get_db_async() as db:
            await db.execute("SELECT status, COUNT(*) AS n FROM favorite_mail"
                             ' WHERE profile_id = %s AND uid = %s'
                             ' GROUP BY status', (pair['girl'], pair['star']))
            rows = {r['status']: int(r['n']) for r in await db.fetchall()}

        assert rows.get('draft', 0) == 0, 'черновик пережил пару'
        assert rows.get('sent', 0) == 1, 'журнал ушедшего письма двинули'

    async def test_second_forget_reports_nothing(self, pair):
        """⚠ Повтор не ошибка: `False` — строки уже нет, а человек хотел именно
        этого. Страница скажет то же самое."""
        assert await favorite_forget(pair['girl'], pair['star']) is True
        assert await favorite_forget(pair['girl'], pair['star']) is False

    async def test_empty_pair_forgets_nothing(self, pair):
        """⚠⚠ Пустая пара не имеет права превратиться в `DELETE FROM favorite`
        без условия — зачистку анкеты ручкой делать нечего."""
        assert await favorite_forget(0, pair['star']) is False
        assert await favorite_forget(pair['girl'], '   ') is False
        assert pair['star'] in await favorite_settled(pair['girl']), \
            'пустые доводы что-то да удалили'


class TestTheHandle:
    """Дверь: зовёт тело, просит пару."""

    async def test_the_handle_forgets(self, pair):
        got = await favorite_drop(profile_id=pair['girl'], uid=pair['star'],
                                  user=None)

        assert got == {'result': {'dropped': True}}
        assert pair['star'] not in await favorite_settled(pair['girl'])

    async def test_the_handle_wants_the_pair(self, pair):
        """⚠ Без пары 422: убирать «вообще все» ручка не умеет ни за каким видом."""
        for kwargs in ({'profile_id': 0, 'uid': pair['star']},
                       {'profile_id': pair['girl'], 'uid': '  '}):
            with pytest.raises(HTTPException) as exc:
                await favorite_drop(user=None, **kwargs)
            assert exc.value.status_code == 422

        assert pair['star'] in await favorite_settled(pair['girl']), \
            'отказ без пары что-то да сделал'
