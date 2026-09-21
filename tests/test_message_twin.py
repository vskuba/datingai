"""Одна реплика, записанная двумя дорогами, показывается один раз.

## ⚠⚠ Что было сломано

Наше исходящее сообщение попадает в `message` дважды: своей строкой в момент
отправки (ULID в `message_uid`) и строкой из панели агентства (`panel:52447706`).
Защита импорта от повторов — `INSERT IGNORE` по паре анкета + `message_uid` —
здесь бессильна: **ключи разные**, и своя собственная реплика, вернувшаяся из
журнала сайта, повтором не считается.

Живой случай: Elena → Henry, четыре пары строк за полчаса.

## ⚠ Собеседнику при этом уходит ОДНО сообщение

Проверено на проде: у журнала самого сайта нет ни одного случая, где один наш
текст записан дважды. Двоится только наша таблица — поэтому чиним показ, а не
отправку.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.message_twin import message_twin_skip

pytestmark = pytest.mark.chat


# Сайт-пустышка под тесты. ⚠ Свой, а не живой: строки заводим настоящие, и
# попасть ими в рабочую анкету нельзя.
TWIN_SITE = 999_343


@pytest.fixture
async def pair():
    """Анкета-пустышка и собеседник: строки заводим сами, чтобы не трогать живые.

    ⚠ Анкета **настоящая**, а не выдуманный номер: у `message.profile_id` есть
    внешний ключ на `profile`, и вставка под несуществующую анкету отвергается
    (1452). Убирается она сама — `ON DELETE CASCADE` унесёт и сообщения.
    """
    profile_id = 900_000 + int(uuid.uuid4().int % 90_000)
    uid = str(uuid.uuid4().int % 10 ** 9)

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO profile (id, site_id, login, password, role, status, enabled)'
            " VALUES (%s, %s, %s, 'x', 'lady', 'online', 1)"
            ' ON DUPLICATE KEY UPDATE status = VALUES(status)',
            (profile_id, TWIN_SITE, f'twin{profile_id}@test'))
        await db.connection.commit()

    yield profile_id, uid

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _put(profile_id, uid, text, uid_key, source, at, direction='out'):
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            ' message_uid, direction, is_read, source, created_at)'
            " VALUES (%s, %s, '', %s, %s, %s, 1, %s, %s)",
            (profile_id, uid, text, uid_key, direction, source, at))
        await db.connection.commit()

        return db.lastrowid


async def _seen(profile_id, uid):
    """Что увидит страница — тем же условием, что и она."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, text, source FROM message'
            ' WHERE profile_id = %s AND sender_uid = %s'
            + message_twin_skip() +
            ' ORDER BY created_at, id', (profile_id, uid))

        return [dict(row) for row in (await db.fetchall() or [])]


class TestTheTwinIsHidden:
    """⚠⚠ Главное: две записи одного события — одна строка на экране."""

    async def test_our_row_and_the_panel_row_show_once(self, pair):
        profile_id, uid = pair
        at = '2026-09-21 14:30:30'

        ours = await _put(profile_id, uid, 'glitch lol', '01M32609Q1K2A0MXW21PWYQ58Q',
                          'http', at)
        await _put(profile_id, uid, 'glitch lol', 'panel:52447706', 'panel', at)

        seen = await _seen(profile_id, uid)

        assert len(seen) == 1, f'реплика показана дважды: {seen}'
        assert seen[0]['id'] == ours, 'показали панельную вместо своей'

    async def test_the_earlier_row_wins(self, pair):
        """⚠ Остаётся запись с наименьшим `id` — наша, сделанная при отправке.

        Импорт из панели приходит позже, и его строка всегда старше номером.
        """
        profile_id, uid = pair
        at = '2026-09-21 14:24:11'

        first = await _put(profile_id, uid, 'oh wow', 'ulid-1', 'http', at)
        await _put(profile_id, uid, 'oh wow', 'panel:1', 'panel', at)
        await _put(profile_id, uid, 'oh wow', 'panel:2', 'panel', at)

        seen = await _seen(profile_id, uid)

        assert [one['id'] for one in seen] == [first]


class TestItHidesOnlyTwins:
    """⚠⚠ Обратная сторона, и она важнее: отсев не должен съедать разговор."""

    async def test_the_same_words_at_another_time_stay(self, pair):
        """⚠ Секунда входит в ключ нарочно: без неё «ok» вчера и «ok» сегодня
        слились бы в одну реплику. Люди повторяются, короткими ответами —
        особенно."""
        profile_id, uid = pair

        await _put(profile_id, uid, 'ok', 'ulid-1', 'http', '2026-09-20 10:00:00')
        await _put(profile_id, uid, 'ok', 'ulid-2', 'http', '2026-09-21 10:00:00')

        assert len(await _seen(profile_id, uid)) == 2, 'съели законный повтор'

    async def test_different_directions_stay(self, pair):
        """Он сказал «ok», и мы ответили «ok» — это два разных события."""
        profile_id, uid = pair
        at = '2026-09-21 12:00:00'

        await _put(profile_id, uid, 'ok', 'ulid-in', 'panel', at, direction='in')
        await _put(profile_id, uid, 'ok', 'ulid-out', 'http', at, direction='out')

        assert len(await _seen(profile_id, uid)) == 2

    async def test_other_companions_are_untouched(self, pair):
        """⚠ Один и тот же текст двум людям в одну секунду — это рассылка, а не
        близнец: пишем-то мы разным людям."""
        profile_id, uid = pair
        other = uid + '9'
        at = '2026-09-21 12:00:00'

        await _put(profile_id, uid, 'hi there', 'ulid-1', 'http', at)
        await _put(profile_id, other, 'hi there', 'ulid-2', 'http', at)

        assert len(await _seen(profile_id, uid)) == 1
        assert len(await _seen(profile_id, other)) == 1

    async def test_a_lone_panel_row_stays(self, pair):
        """⚠⚠ Привезённая история остаётся видимой целиком.

        Переписка за прошлые недели существует **только** панельными строками:
        нашей записи у неё нет и быть не может. Отсев обязан оставить их все,
        иначе импорт из панели стал бы бессмысленным.
        """
        profile_id, uid = pair

        await _put(profile_id, uid, 'старое письмо', 'panel:100', 'panel',
                   '2026-09-01 10:00:00')
        await _put(profile_id, uid, 'и ещё одно', 'panel:101', 'panel',
                   '2026-09-01 10:00:05')

        assert len(await _seen(profile_id, uid)) == 2


class TestBothPagesUseIt:
    """⚠⚠ Оба места показа зовут общий отсев, а не свою копию условия."""

    def test_the_dialog_window_uses_it(self):
        import inspect

        import src.uvicorn.routers.dating.profile_view as mod

        assert 'message_twin_skip' in inspect.getsource(mod), \
            'окно переписки снова показывает близнецов'

    def test_the_preview_uses_it(self):
        import inspect

        import src.uvicorn.routers.dating.chat as mod

        assert 'message_twin_skip' in inspect.getsource(mod), \
            'предпросмотр в списке диалогов показывает близнецов'
