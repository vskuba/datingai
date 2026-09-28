"""Ручка «убрать из фаворитов»: `favorite_forget` и её дверь.

Кнопка на красной строке «Фаворитов → Доставки» (сторож `test_favorite_unfav_button`)
зовёт `DELETE /api/dating/favorite`. Это её тело: сперва гасится звезда **на
сайте** (сценарий «анкета мужчины: из избранных» у соседа), и лишь затем строка
пары уходит из таблицы избранных, черновик приставания — за ней, а журнал
доставки остаётся как был.

## ⚠⚠ Что ручка делает и чего не делает

У сайта своей ручки снимать избранное нет, а `favorite_sync` читает список сайта
и вернул бы строку обратно при следующем обходе — «удаление» держалось бы ровно
до `favorite_sync_every`. Поэтому жмём переключатель и там: шаг сценария
«из избранных» `.favorite__status.favorite__status-ok` (`optional`) тушит
горящую звезду, и только после этого уходит наша строка и черновик. Ушедшее письмо —
журнал, его не трогаем.

⚠ Звезда не погасла на сайте — значит не убрано нигде: строка остаётся, а
человек видит причину. Это отличие от `favorite_mark` намеренное: тот глотает
ошибку — фоновая работа переживёт единичный сбой, а здесь человек нажал кнопку
и ждёт ответа; база не отвечает — пусть страница покажет причиной.
"""

import uuid

import pytest
from fastapi import HTTPException

from mysql_.mysql_ import mysql_get_db_async

from src.dating.favorite import (FAVORITE_FORGET_SCENARIO, favorite_forget,
                                        favorite_settled)
from src.uvicorn.routers.dating.favorite import favorite_drop

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

# Живые классы звёздочки, те же, что у сторожа добавления (`test_favorite.py`),
# слово в слово с пробными прогонами под анкетой 849. Тот же элемент и та же
# разметка — выдуманные классы проверяли бы нашу мысль о вёрстке, а не вёрстку.
LIT = 'favorite__status favorite__status-ok profile_btn-friend'
DARK = 'favorite__status  profile_btn-friend'

# Ключи `extract`, которыми тело сверяет состояние, — как записаны у соседа в
# базе сценария.
EXTRACT_BEFORE, EXTRACT_AFTER = 'до', 'после'


@pytest.fixture
async def pair(auth_client, rhapsody):
    """Анкета с избранным, его черновиком, его ушедшим письмом и чужими строками.

    ⚠ Чужие строки — то есть **не его**: другой избранник той же анкеты и тот
    же человек под другой анкетой. Ручка убирает пару, а не человека и не анкету:
    без этих строк `WHERE` с одним столбцом выглядел бы верно.

    ⚠ Анкете ставим свой пароль (`password_source = 'own'`): прогон входит под
    ней, и выбор пароля обязано видеть, что этот пароль — её собственный.
    """
    girl = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'favfgt-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    tag = uuid.uuid4().hex[:6]
    star, quiet, other_girl = f'star-{tag}', f'quiet-{tag}', girl + 900_000

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET password_source = 'own'"
                         ' WHERE id = %s', (girl,))
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
        await db.execute('SELECT id, site_id, login, password, password_source'
                         ' FROM profile WHERE id = %s', (girl,))
        profile = dict(await db.fetchone())
        await db.connection.commit()

    yield {'girl': girl, 'profile': profile, 'star': star, 'quiet': quiet,
           'other': other_girl}

    async with mysql_get_db_async() as db:
        for table in ('favorite', 'favorite_mail', 'log'):
            await db.execute(f'DELETE FROM {table} WHERE profile_id IN (%s, %s)',
                             (girl, other_girl))
        await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
        await db.connection.commit()


class TestForgetsThePair:
    """Тело ручки: сперва сайт, потом строка — и только пара."""

    async def test_the_star_goes_out_and_the_row_goes(self, pair, rhapsody):
        """⚠ Звезда погасла на сайте, строка ушла у нас — пара выпала из набора
        писем, и `favorite_sync` вернёт её лишь если она и правда горит."""
        _site(rhapsody, LIT, DARK)

        got = await favorite_forget(pair['profile'], pair['star'])

        assert got == {'state': 'done', 'dropped': True}
        settled = await favorite_settled(pair['girl'])
        assert pair['star'] not in settled, 'пара осталась избранной'
        assert pair['quiet'] in settled, \
            'ручка задела не его: другой избранник той же анкеты ушёл'
        assert any(p.endswith('/run') for _, p in rhapsody.calls), \
            'сайт так и не спросили — удаление доживёт лишь до ближайшей сверки'

    async def test_the_star_that_stayed_lit_undoes_nothing(self, pair, rhapsody):
        """⚠⚠ Звезда не погасла на сайте — не убрано нигде: строка на месте,
        а человек видит причину. Удаление наполовину дороже неудачи: круг молча
        вернул бы забытого, и кнопка показалась бы сработавшей."""
        _site(rhapsody, LIT, LIT)

        got = await favorite_forget(pair['profile'], pair['star'])

        assert got['state'] == 'fail' and 'не погасла' in got['error']
        assert pair['star'] in await favorite_settled(pair['girl']), \
            'строка ушла без сайта'

    async def test_the_other_profile_keeps_him(self, pair, rhapsody):
        """⚠ Убирается **пара**, а не человек: у другой анкеты он избран и остаётся."""
        _site(rhapsody, LIT, DARK)
        await favorite_forget(pair['profile'], pair['star'])

        async with mysql_get_db_async() as db:
            await db.execute('SELECT uid FROM favorite WHERE profile_id = %s',
                             (pair['other'],))
            rows = await db.fetchall()

        assert [r['uid'] for r in rows] == [pair['star']], \
            'у чужой анкеты избранность размылась'

    async def test_the_draft_goes_and_the_letter_stays(self, pair, rhapsody):
        """⚠ Черновик выметается вместе с парой — иначе круг уйдёт в отказ, а
        журнал остаётся: уже случившееся не переписывают."""
        _site(rhapsody, LIT, DARK)
        await favorite_forget(pair['profile'], pair['star'])

        async with mysql_get_db_async() as db:
            await db.execute("SELECT status, COUNT(*) AS n FROM favorite_mail"
                             ' WHERE profile_id = %s AND uid = %s'
                             ' GROUP BY status', (pair['girl'], pair['star']))
            rows = {r['status']: int(r['n']) for r in await db.fetchall()}

        assert rows.get('draft', 0) == 0, 'черновик пережил пару'
        assert rows.get('sent', 0) == 1, 'журнал ушедшего письма двинули'

    async def test_second_forget_reports_nothing(self, pair, rhapsody):
        """⚠ Повтор не ошибка: `already`, `dropped: False` — его уже нет ни на
        сайте, ни у нас, а человек хотел именно этого. И нажатие по своей сути
        то же, что первое, — жать ещё раз незачем, но и нельзя наказать."""
        _site(rhapsody, LIT, DARK)
        first = await favorite_forget(pair['profile'], pair['star'])
        assert first == {'state': 'done', 'dropped': True}

        # Теперь звезда не горит и шаг сценария пропущен — классы одинаковы.
        _site(rhapsody, DARK, DARK)
        second = await favorite_forget(pair['profile'], pair['star'])
        assert second == {'state': 'already', 'dropped': False}

    async def test_empty_uid_forgets_nothing(self, pair, rhapsody):
        """⚠⚠ Пустой номер не доходит ни до браузера, ни до `DELETE`: точный
        отказ словами — страница покажет его, а строка анкеты цела."""
        got = await favorite_forget(pair['profile'], '   ')

        assert got['state'] == 'fail' and 'не назван' in got['error']
        assert pair['star'] in await favorite_settled(pair['girl']), \
            'пустой довод что-то да удалил'


class TestTheHandle:
    """Дверь: зовёт тело, просит пару, отказ сайта отдаёт словом."""

    async def test_the_handle_forgets(self, pair, rhapsody):
        _site(rhapsody, LIT, DARK)

        got = await favorite_drop(profile_id=pair['girl'], uid=pair['star'],
                                  user=None)

        assert got == {'result': {'state': 'done', 'dropped': True}}
        assert pair['star'] not in await favorite_settled(pair['girl'])

    async def test_the_site_refusal_becomes_an_error(self, pair, rhapsody):
        """⚠ Сосед встал шагом — 409 с его же словами: страница покажет причину,
        а строка дождётся человека, а не молча исчезнет."""
        rhapsody.scenarios.append({'id': 911, 'site_id': STAND_SITE,
                                   'name': FAVORITE_FORGET_SCENARIO})
        rhapsody.run = {'status': 'error', 'step_no': 11,
                        'error': 'браузер занят', 'extract': {}}

        with pytest.raises(HTTPException) as exc:
            await favorite_drop(profile_id=pair['girl'], uid=pair['star'],
                                user=None)

        assert exc.value.status_code == 409
        assert 'браузер занят' in exc.value.detail
        assert pair['star'] in await favorite_settled(pair['girl']), \
            'дверь всё равно убрала пару при отказе'

    async def test_the_handle_wants_the_pair(self, pair):
        """⚠ Без пары 422: убирать «вообще все» ручка не умеет ни за каким видом.
        И до прогона дойти не успевает — анкету не спрашивает."""
        for kwargs in ({'profile_id': 0, 'uid': pair['star']},
                       {'profile_id': pair['girl'], 'uid': '  '}):
            with pytest.raises(HTTPException) as exc:
                await favorite_drop(user=None, **kwargs)
            assert exc.value.status_code == 422

        assert pair['star'] in await favorite_settled(pair['girl']), \
            'отказ без пары что-то да сделал'


def _site(rhapsody, before: str, after: str) -> None:
    """Сосед отвечает прогоном ровно этого сценария и отдаёт такие классы.

    Сценарий заводится под стендовый сайт сюиты и своим именем: промах по имени
    («нашли «в избранные» вместо «из избранных»») тонет здесь же, где и найден.
    """
    rhapsody.scenarios.append({'id': 911, 'site_id': STAND_SITE,
                               'name': FAVORITE_FORGET_SCENARIO})
    rhapsody.run = {'status': 'success', 'step_no': 0, 'error': '',
                    'extract': {EXTRACT_BEFORE: before, EXTRACT_AFTER: after}}
