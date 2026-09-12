"""Круг расписания: воркер водит анкету по сменам.

⚠ **Вход и выход подменены.** Настоящий вход гонит браузер у соседа минутами;
проверяется расписание — кого и когда круг трогает, а не работа сценария.

## ⚠ Главное правило, и оно исправляет настоящую ошибку

Круг ведёт **только анкеты с включённым расписанием**. Сперва он брал всех, у кого
жив сокет, — чтобы «выключенное расписание рвало соединение сразу». Получилось, что
ручной вход становится невозможен: нажали «Войти», через минуту круг вышел, потому
что галочки нет. Проверено на живой базе — круг сбросил вход у семи анкет,
залогиненных руками, и восстановить `storage_state` было нечем.

Обрыв при снятии галочки делает **ручка**: она знает, что переход случился.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async
from src.dating import dating_schedule_round as rnd
from src.dating.dating_schedule_round import (dating_schedule_round_drop,
                                              dating_schedule_round_run)

pytestmark = pytest.mark.profile

SHIFT = '[{"from": "07:00", "to": "11:00"}]'


@pytest.fixture
async def probe(monkeypatch):
    """Пробная анкета с расписанием. ⚠ Своя: круг меняет вход и статус, и трогать
    живые значило бы выкидывать анкеты из работы.

    ⚠⚠ **Своей анкеты мало — кругу сужается и список.** `_rows` берёт из базы
    **все** анкеты с включённым расписанием, и подменённый вход (`door`) писал
    поддельный `ws_url` каждой из них: у живой анкеты сессия при этом теряется —
    ровно та беда, от которой этот файл и защищает.

    Так и вышло: человек включил расписание у работающей анкеты, и очередной
    прогон сюиты записал ей `wss://x?t=1` вместо настоящего адреса. Заодно ломался
    сам тест — `checked` считает всех, кого круг взял, и живая анкета сбивала
    счёт с нуля на единицу.

    Тот же приём, что у круга шаблонов (`test_mail_round.py`): подменяем не
    запрос, а **отбор** — и получаем круг, который физически не видит чужого.
    """
    login = f'zz-sched-{uuid.uuid4().hex[:8]}@test'

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_profile (site_id, login, password, password_source,'
            ' role, schedule, schedule_on, enabled, status)'
            " VALUES (343, %s, 'x', 'own', 'lady', %s, 1, 1, 'offline')",
            (login, SHIFT))
        await db.connection.commit()
        await db.execute('SELECT id FROM dating_profile WHERE login = %s', (login,))
        profile_id = int((await db.fetchone())['id'])

    original = rnd._rows

    async def _mine():
        return [row for row in await original() if int(row['id']) == profile_id]

    monkeypatch.setattr(rnd, '_rows', _mine)

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


@pytest.fixture
def clock(monkeypatch):
    """Подменяет «сколько сейчас»: ждать 07:00 в тесте нельзя."""
    state = {'hour': 9}
    monkeypatch.setattr(rnd, '_minutes_now', lambda offset: state['hour'] * 60)

    return state


@pytest.fixture
def door(monkeypatch):
    """Подменяет вход и выход. Возвращает, кого куда водили."""
    seen = {'entered': [], 'left': []}

    async def _enter(profile_id):
        seen['entered'].append(int(profile_id))
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE dating_profile SET status = 'online',"
                             " storage_state = '{}', ws_url = 'wss://x?t=1'"
                             ' WHERE id = %s', (int(profile_id),))
            await db.connection.commit()

        return True

    async def _leave(profile_id, why):
        seen['left'].append((int(profile_id), why))

        return await rnd._state_clear(int(profile_id))

    monkeypatch.setattr(rnd, '_enter', _enter)
    monkeypatch.setattr(rnd, '_leave', _leave)

    return seen


class TestRoundFollowsTheSchedule:
    """Кого и когда круг трогает."""

    async def test_enters_when_the_shift_starts(self, probe, clock, door):
        clock['hour'] = 9

        result = await dating_schedule_round_run(0)

        assert door['entered'] == [probe]
        assert result['entered'] == 1

    async def test_does_nothing_when_already_online(self, probe, clock, door):
        """Второй круг внутри смены ничего не делает: анкета уже в сети.

        Без этой проверки круг входил бы каждую минуту — то есть гонял браузер у
        соседа шестьдесят раз в час вместо одного.
        """
        clock['hour'] = 9
        await dating_schedule_round_run(0)
        door['entered'].clear()

        await dating_schedule_round_run(0)

        assert door['entered'] == []

    async def test_leaves_when_the_shift_ends(self, probe, clock, door):
        clock['hour'] = 9
        await dating_schedule_round_run(0)

        clock['hour'] = 12
        result = await dating_schedule_round_run(0)

        assert [uid for uid, _ in door['left']] == [probe]
        assert result['left'] == 1

    async def test_does_not_enter_outside_the_shift(self, probe, clock, door):
        clock['hour'] = 3

        await dating_schedule_round_run(0)

        assert door['entered'] == []

    async def test_socket_url_is_cleared_on_leave(self, probe, clock, door):
        """⚠ Выход гасит адрес сокета: держатель отпускает соединение по пустому
        адресу, и это единственное, что делает выход немедленным."""
        clock['hour'] = 9
        await dating_schedule_round_run(0)

        clock['hour'] = 12
        await dating_schedule_round_run(0)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT ws_url, status, storage_state FROM dating_profile'
                             ' WHERE id = %s', (probe,))
            row = await db.fetchone()

        assert row['ws_url'] == ''
        assert row['status'] == 'offline'
        assert row['storage_state'] is None


class TestRoundDoesNotTouchManualLogins:
    """⚠ Настоящая ошибка, найденная на живой базе, и защита от её возврата."""

    async def test_profile_without_schedule_on_is_ignored(self, probe, clock, door):
        """Расписание выключено — круг анкету не трогает, даже если она в сети.

        Так залогинились **вручную**: нажали «Войти» на странице. Круг, отпускающий
        всё без галочки, выкинул бы такую анкету через минуту — и выкинул, семь
        штук, пока правило не исправили.
        """
        clock['hour'] = 9
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE dating_profile SET schedule_on = 0,"
                             " status = 'online', storage_state = '{}',"
                             " ws_url = 'wss://x?t=1' WHERE id = %s", (probe,))
            await db.connection.commit()

        result = await dating_schedule_round_run(0)

        assert result['checked'] == 0, 'круг взял анкету без включённого расписания'
        assert door['left'] == [], 'ручной вход сброшен кругом'

        async with mysql_get_db_async() as db:
            await db.execute('SELECT status, ws_url FROM dating_profile WHERE id = %s',
                             (probe,))
            row = await db.fetchone()

        assert row['status'] == 'online' and row['ws_url'], 'состояние затёрто'

    async def test_disabled_profile_is_released(self, probe, clock, door):
        """А вот выключенную анкету круг отпускает: «не ведём» значит не ведём
        совсем — даже с включённым расписанием."""
        clock['hour'] = 9
        await dating_schedule_round_run(0)

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_profile SET enabled = 0 WHERE id = %s',
                             (probe,))
            await db.connection.commit()

        result = await dating_schedule_round_run(0)

        assert result['dropped'] == 1
        assert door['left'] and 'выключена' in door['left'][-1][1]


class TestDropIsImmediate:
    """Снятие галочки рвёт сокет сразу — это делает ручка, а не круг."""

    async def test_drop_clears_the_socket(self, probe, door):
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE dating_profile SET status = 'online',"
                             " storage_state = '{}', ws_url = 'wss://x?t=1'"
                             ' WHERE id = %s', (probe,))
            await db.connection.commit()

        assert await dating_schedule_round_drop(probe) is True

        async with mysql_get_db_async() as db:
            await db.execute('SELECT status, ws_url FROM dating_profile WHERE id = %s',
                             (probe,))
            row = await db.fetchone()

        assert row['status'] == 'offline' and row['ws_url'] == ''

    async def test_drop_of_offline_profile_says_nothing_happened(self, probe):
        """⚠ `False`, когда рвать нечего: говорить человеку «сокет снят» у анкеты,
        которой и не было в сети, — неправда."""
        assert await dating_schedule_round_drop(probe) is False

    async def test_drop_of_missing_profile_is_quiet(self):
        """Удалённая анкета — не повод падать: ручка могла прийти по устаревшей
        странице."""
        assert await dating_schedule_round_drop(99_999_999) is False


class TestLocalTime:
    """⚠ Расписание задано местным временем, а сервер живёт по UTC."""

    def test_offset_shifts_the_clock(self, monkeypatch):
        """«Войти в 7 утра» человек понимает буквально: смены сверяют с часами на
        стене, а не с UTC."""
        import time

        monkeypatch.setattr(time, 'time', lambda: 0.0)   # 00:00 UTC

        assert rnd._minutes_now(0) == 0
        assert rnd._minutes_now(3) == 3 * 60


class TestSessionIsReleasedToo:
    """⚠ Живая сессия — тоже «есть что отпускать», и это находка живого прогона.

    Присутствие держится на `storage_state`, а не на статусе и не на сокете: пока
    он лежит, анкета остаётся вошедшей на сайте. Прежде конец смены смотрел только
    на `status` и `ws_url` — и у анкеты, чей статус сбился в `error`, выход не
    срабатывал вовсе: сессия жила дальше, а точка в панели не гасла.

    Поймано на Ladadate: сайт без сокета, `ws_url` пуст всегда, а статус увела в
    `error` соседняя беда. Смена кончилась, удары прекратились — но выхода не было.
    """

    async def test_shift_end_clears_a_live_session(self, probe, clock, door):
        """Смена кончилась — сессия снята, даже когда ни статуса, ни сокета."""
        clock['hour'] = 12          # вне смены 07:00–11:00

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE dating_profile SET status = 'error', ws_url = '',"
                             " storage_state = '{}' WHERE id = %s", (probe,))
            await db.connection.commit()

        result = await dating_schedule_round_run(0)

        assert result['left'] == 1, 'выход не сработал при живой сессии'

        async with mysql_get_db_async() as db:
            await db.execute('SELECT status, storage_state FROM dating_profile'
                             ' WHERE id = %s', (probe,))
            row = await db.fetchone()

        assert row['storage_state'] is None, 'сессия осталась жить после смены'
        assert row['status'] == 'offline', 'точка в панели не погасла'

    async def test_disabled_profile_loses_its_session(self, probe, clock, door):
        """Выключенную отпускаем так же: «не ведём» значит не ведём совсем."""
        clock['hour'] = 9           # внутри смены — и всё равно отпускаем

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE dating_profile SET enabled = 0, status = 'error',"
                             " ws_url = '', storage_state = '{}' WHERE id = %s", (probe,))
            await db.connection.commit()

        assert (await dating_schedule_round_run(0))['dropped'] == 1

    async def test_nothing_to_release_is_not_a_leave(self, probe, clock, door):
        """⚠ Пустая анкета вне смены выхода **не** считает.

        Иначе круг «выходил» бы из каждой неработающей анкеты каждую минуту, и
        счётчик `left` перестал бы что-либо значить.
        """
        clock['hour'] = 12

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE dating_profile SET status = 'offline', ws_url = '',"
                             ' storage_state = NULL WHERE id = %s', (probe,))
            await db.connection.commit()

        assert (await dating_schedule_round_run(0))['left'] == 0
