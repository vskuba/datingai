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

from src.dating.chat_panel import chat_panel_save
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
    """⚠ Оба места показа зовут общий отсев, а не свою копию условия.

    ⚠⚠ **Этого сторожа мало, и он уже промолчал.** Он смотрит, встречается ли имя
    `message_twin_skip` **в модуле**, а запросов в модуле несколько: правило
    стояло у показа реплик и не стояло у счётчика на карточке — сторож был
    зелёным, а значок говорил 61 при шестидесяти видимых репликах. Поведение
    стережёт `TestTheCounterAgreesWithTheWindow` ниже; эти два остаются дешёвой
    проверкой «имя не выпало из файла целиком».
    """

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


class TestTheCounterAgreesWithTheWindow:
    """⚠⚠ Значок на карточке диалога и число видимых реплик — одно число.

    Окно прятало близнеца, а `COUNT(*)` считал его: на проде у пары Diana ↔ Bruce
    карточка говорила **61** при шестидесяти видимых строках. Разойтись они могут
    на сколько угодно — близнецы копятся, пока идёт импорт из панели, — и человек
    не может ни проверить число, ни объяснить разницу.

    ⚠ Счёт здесь сверяется **с показом**, а не с ожидаемым числом: разъехаться
    они могут в обе стороны, и тест, знающий только «должно быть два», не заметил
    бы, если спрячется лишнее.
    """

    async def _said_with_a_twin(self, profile_id: str, uid: str) -> None:
        """Входящая, наш ответ и его отражение из панели — то же, что на проде."""
        await _put(profile_id, uid, 'how are you?', 'panel:900001', 'panel',
                   '2026-09-21 14:30:00', direction='in')
        await _put(profile_id, uid, 'glitch lol', '01M32609Q1K2A0MXW21PWYQ58Q',
                   'http', '2026-09-21 14:30:30')
        await _put(profile_id, uid, 'glitch lol', 'panel:900002', 'panel',
                   '2026-09-21 14:30:30')

    async def test_the_profile_card_counts_what_is_visible(self, pair):
        from src.uvicorn.routers.dating.profile_view import profile_view_card

        profile_id, uid = pair
        await self._said_with_a_twin(profile_id, uid)

        card = (await profile_view_card(profile_id, user=None))['result']
        row = [one for one in card['contacts'] if one['uid'] == uid]

        assert row, 'пара пропала из карточки анкеты'
        assert row[0]['messages'] == len(await _seen(profile_id, uid)), \
            'значок на карточке считает не то, что видно в окне'
        assert row[0]['messages'] == 2, 'близнец сосчитан отдельной репликой'

    async def test_the_site_dialogs_count_what_is_visible(self, pair):
        """⚠ Второй список — по всему сайту, и запрос у него свой."""
        from src.uvicorn.routers.dating.chat import chat_dialogs

        profile_id, uid = pair
        await self._said_with_a_twin(profile_id, uid)

        got = await chat_dialogs(site_id=TWIN_SITE, profile_id=profile_id,
                                        user=None)
        row = [one for one in got['result']['contacts'] if one['uid'] == uid]

        assert row, 'пара пропала из диалогов сайта'
        assert row[0]['messages'] == len(await _seen(profile_id, uid)), \
            'значок в диалогах сайта считает не то, что видно в окне'
        assert row[0]['messages'] == 2, 'близнец сосчитан отдельной репликой'

    async def test_the_last_message_is_not_the_hidden_twin(self, pair):
        """⚠⚠ «Последняя реплика» тоже обязана быть видимой.

        Отсева в группировке для этого мало: время у близнеца и у нашей строки
        совпадает секунда в секунду сплошь и рядом, а `MAX(id)` выбирает из них
        больший — то есть привезённый из панели позже. Строка списка указывала бы
        на реплику, которой в окне нет; поймано этим тестом при первой редакции
        правки.
        """
        from src.uvicorn.routers.dating.chat import chat_dialogs
        from src.uvicorn.routers.dating.profile_view import profile_view_card

        profile_id, uid = pair
        await self._said_with_a_twin(profile_id, uid)
        seen = await _seen(profile_id, uid)

        got = await chat_dialogs(site_id=TWIN_SITE, profile_id=profile_id,
                                        user=None)
        site_row = [one for one in got['result']['contacts'] if one['uid'] == uid][0]

        card = (await profile_view_card(profile_id, user=None))['result']
        card_row = [one for one in card['contacts'] if one['uid'] == uid][0]

        assert site_row['last_id'] == seen[-1]['id'], \
            'строка диалогов сайта указывает на спрятанную реплику'
        assert card_row['last_id'] == seen[-1]['id'], \
            'строка карточки анкеты указывает на спрятанную реплику'


def _row(profile_id, uid, text, uid_key, at, direction='out', name='Henry'):
    """Запись в том виде, в каком её отдаёт разбор панели."""
    return {'profile_id': profile_id, 'sender_uid': uid, 'sender_name': name,
            'text': text, 'message_uid': uid_key, 'direction': direction,
            'sent_at': at}


async def _all(profile_id, uid):
    """Что реально лежит в таблице — без отсева показа."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, text, source, message_uid FROM message'
            ' WHERE profile_id = %s AND sender_uid = %s ORDER BY id',
            (profile_id, uid))

        return [dict(row) for row in (await db.fetchall() or [])]


class TestTheImportDoesNotCreateTwins:
    """⚠⚠ Чиним причину, а не только показ: таблица не должна расти мусором."""

    async def test_our_sent_message_is_not_imported_again(self, pair):
        """Главный случай: панель возвращает реплику, которую мы сами отправили."""
        profile_id, uid = pair
        at = '2026-09-21 14:30:30'

        await _put(profile_id, uid, 'glitch lol', '01M32609Q1K2A0MXW21PWYQ58Q',
                   'http', at)

        got = await chat_panel_save([_row(profile_id, uid, 'glitch lol',
                                          'panel:52447706', at)])

        assert got['added'] == 0, 'импорт снова завёл близнеца'
        assert len(await _all(profile_id, uid)) == 1

    async def test_a_message_we_never_saw_is_imported(self, pair):
        """⚠ Обратная сторона: привезённая история обязана доехать целиком."""
        profile_id, uid = pair

        got = await chat_panel_save([
            _row(profile_id, uid, 'старое письмо', 'panel:100',
                 '2026-09-01 10:00:00', direction='in'),
            _row(profile_id, uid, 'и ещё одно', 'panel:101',
                 '2026-09-01 10:00:05', direction='in')])

        assert got['added'] == 2
        assert len(await _all(profile_id, uid)) == 2

    async def test_the_same_panel_row_twice_is_still_ignored(self, pair):
        """⚠ Прежняя защита цела: тот же номер панели из пересекающихся
        диапазонов по-прежнему пропускается молча."""
        profile_id, uid = pair
        row = _row(profile_id, uid, 'привет', 'panel:7', '2026-09-10 12:00:00',
                   direction='in')

        first = await chat_panel_save([row])
        second = await chat_panel_save([row])

        assert first['added'] == 1
        assert second['added'] == 0
        assert len(await _all(profile_id, uid)) == 1

    async def test_the_same_words_at_another_time_are_imported(self, pair):
        """⚠ Он написал «ok» вчера и «ok» сегодня — это две реплики."""
        profile_id, uid = pair

        got = await chat_panel_save([
            _row(profile_id, uid, 'ok', 'panel:1', '2026-09-20 10:00:00',
                 direction='in'),
            _row(profile_id, uid, 'ok', 'panel:2', '2026-09-21 10:00:00',
                 direction='in')])

        assert got['added'] == 2

    async def test_his_reply_is_not_eaten_by_our_message(self, pair):
        """⚠⚠ Наше «ok» не должно съедать его «ok» той же секунды: направление
        входит в ключ."""
        profile_id, uid = pair
        at = '2026-09-21 13:00:00'

        await _put(profile_id, uid, 'ok', 'ulid-out', 'http', at, direction='out')

        got = await chat_panel_save([_row(profile_id, uid, 'ok', 'panel:9', at,
                                          direction='in')])

        assert got['added'] == 1, 'входящую приняли за нашу'
        assert len(await _all(profile_id, uid)) == 2


class TestBothSidesShareOneRule:
    """⚠⚠ Показ и импорт понимают близнеца одинаково.

    Разойдись эти правила — импорт считал бы строку новой, а страница прятала бы
    её как двойника, и реплика пропала бы с экрана совсем.
    """

    def test_the_import_uses_the_shared_condition(self):
        import inspect

        import src.dating.chat_panel as mod

        assert 'message_twin_absent' in inspect.getsource(mod), \
            'импорт снова судит о дублях по-своему'

    def test_both_conditions_name_the_same_fields(self):
        from src.dating.message_twin import (message_twin_absent,
                                             message_twin_skip)

        show, put = message_twin_skip(), message_twin_absent('out')

        for field in ('profile_id', 'sender_uid', 'text', 'created_at'):
            assert f'twin.{field}' in show, field
            assert f'twin.{field}' in put, field

    def test_both_keep_our_row_not_the_panel_one(self):
        """⚠⚠ Источник в правиле у обеих сторон: прячется отражение, а не
        «строка с бо́льшим номером»."""
        from src.dating.message_twin import (message_twin_absent,
                                             message_twin_skip)

        for one in (message_twin_skip(), message_twin_absent('out')):
            assert "twin.source <> 'panel'" in one

    def test_the_window_matches_on_both_sides(self):
        """⚠ Допуск по времени одинаков у показа и у импорта."""
        from src.dating.message_twin import (MESSAGE_TWIN_WINDOW_OUT,
                                             message_twin_absent,
                                             message_twin_skip)

        assert f'{MESSAGE_TWIN_WINDOW_OUT}' in message_twin_skip()
        assert (f'INTERVAL {MESSAGE_TWIN_WINDOW_OUT} SECOND'
                in message_twin_absent('out'))


class TestTheClocksMayDisagree:
    """⚠⚠ Время у строк разное по природе, и точная секунда как ключ не годится.

    Своё мы ставим по нашим часам в момент отправки, панель отдаёт отметку сайта.
    Замерено на проде: расхождение 0 секунд у одиннадцати пар, 1 секунда у одной
    и **7 секунд** у одной. Первая редакция требовала совпадения секунда в
    секунду, и дубли Diana → Henry пережили починку именно поэтому.
    """

    async def test_a_seven_second_gap_is_still_a_twin(self, pair):
        profile_id, uid = pair

        ours = await _put(profile_id, uid, 'sounds tempting', 'ulid-1', 'http',
                          '2026-09-21 14:00:08')
        await _put(profile_id, uid, 'sounds tempting', 'panel:9', 'panel',
                   '2026-09-21 14:00:01')

        seen = await _seen(profile_id, uid)

        assert len(seen) == 1, f'пара с расхождением в 7 секунд не узналась: {seen}'
        assert seen[0]['id'] == ours, 'спрятали свою строку вместо панельной'

    async def test_a_far_apart_repeat_stays(self, pair):
        """⚠ Допуск не бесконечен: тот же текст через час — другая реплика."""
        profile_id, uid = pair

        await _put(profile_id, uid, 'ok', 'ulid-1', 'http', '2026-09-21 10:00:00')
        await _put(profile_id, uid, 'ok', 'panel:1', 'panel', '2026-09-21 11:00:00')

        assert len(await _seen(profile_id, uid)) == 2

    async def test_incoming_keeps_the_strict_key(self, pair):
        """⚠⚠ У входящих допуска нет: человек пишет очередью, и реплики в
        соседние секунды — настоящие разные. Съесть чужую реплику хуже, чем
        показать лишнюю строку."""
        profile_id, uid = pair

        await _put(profile_id, uid, 'ok', 'sock-1', 'socket', '2026-09-21 12:00:00',
                   direction='in')
        await _put(profile_id, uid, 'ok', 'panel:1', 'panel', '2026-09-21 12:00:03',
                   direction='in')

        assert len(await _seen(profile_id, uid)) == 2, 'съели входящую реплику'


class TestThePanelCutsLongText:
    """⚠⚠ Журнал панели режет текст на 256 знаках, и обрезок — тот же близнец.

    Замер на проде 02.11.2026: у всех 512 панельных строк `CHAR_LENGTH(text)`
    не больше 256, тогда как у наших доходит до 1532. Равенство текстов на таких
    парах не срабатывало, и на экране висели две строки одной реплики — вторая
    оборванная на полуслове (Yekaterina → Frank, 02.11 12:18). Таких пар
    накопилось шесть, и все шесть — отражения наших же отправок.
    """

    # Живой текст той самой реплики: 278 знаков, панель привезла 256.
    LONG = ('I like your straightforwardness, Frank. I would choose Vienna, because '
            'it is calm and beautiful. But let us chat a bit more first. What is '
            'your favorite book to read while walking? I prefer quiet places where '
            'we can actually hear each other speak without distractions around us.')

    # Текст, у которого 256-й знак — пробел: 'Vienna, ' кратно восьми.
    SPACED = 'Vienna, ' * 40

    async def test_a_truncated_panel_row_is_still_a_twin(self, pair):
        profile_id, uid = pair
        at = '2026-11-02 10:18:16'

        ours = await _put(profile_id, uid, self.LONG, '01M6DW8MD3ECV67Q1SX0JYZWFX',
                          'http', at)
        await _put(profile_id, uid, self.LONG[:256], 'panel:80287559', 'panel', at)

        seen = await _seen(profile_id, uid)

        assert len(seen) == 1, f'обрезанный близнец показан отдельной строкой: {seen}'
        assert seen[0]['id'] == ours, 'спрятали свою строку вместо обрезка'
        assert seen[0]['text'] == self.LONG, 'на экране осталась обрезанная реплика'

    async def test_the_cut_landing_on_a_space_is_still_a_twin(self, pair):
        """⚠⚠ Обрезок бывает и **255** знаков, а не ровно 256.

        Разбор панели зовёт `.strip()` (`chat_panel._text`), и когда срез попал на
        пробел, тот пропадает. Живой случай — строка 684180 на проде: 255 знаков
        при нашей в 355. Правило «длина ровно 256» такую пару не узнало бы.
        """
        profile_id, uid = pair
        at = '2026-10-30 11:39:54'
        cut = self.SPACED[:256].strip()

        assert len(cut) == 255, 'текст подобран неверно: срез не попал на пробел'

        ours = await _put(profile_id, uid, self.SPACED, 'ulid-1', 'invite', at)
        await _put(profile_id, uid, cut, 'panel:684180', 'panel', at)

        seen = await _seen(profile_id, uid)

        assert len(seen) == 1, f'обрезок с потерянным пробелом не узнался: {seen}'
        assert seen[0]['id'] == ours

    async def test_the_import_skips_the_truncated_reflection(self, pair):
        """⚠ Чиним причину, а не только показ: обрезок не должен заводиться."""
        profile_id, uid = pair
        at = '2026-11-02 10:18:16'

        await _put(profile_id, uid, self.LONG, '01M6DW8MD3ECV67Q1SX0JYZWFX',
                   'http', at)

        got = await chat_panel_save([_row(profile_id, uid, self.LONG[:256],
                                          'panel:80287559', at)])

        assert got['added'] == 0, 'импорт снова завёл обрезанного близнеца'
        assert len(await _all(profile_id, uid)) == 1

    async def test_a_short_panel_row_does_not_eat_our_longer_one(self, pair):
        """⚠⚠ Главная опасность правила: начало — это не «та же реплика».

        «Ok» из панели и наше «Ok, let us meet tomorrow» в ту же секунду — два
        разных события, и отсев обязан оставить оба. Поэтому сравнивается не любой
        префикс, а ровно срез панели: начало длиной `MESSAGE_TWIN_PANEL_CUT`.
        """
        profile_id, uid = pair
        at = '2026-11-02 10:18:16'

        await _put(profile_id, uid, 'Ok, let us meet tomorrow', 'ulid-1', 'http', at)
        await _put(profile_id, uid, 'Ok', 'panel:1', 'panel', at)

        assert len(await _seen(profile_id, uid)) == 2, 'съели короткую реплику'

    async def test_another_long_message_stays(self, pair):
        """⚠ Длина у обоих за срез, а текст с самого начала разный — две реплики."""
        profile_id, uid = pair
        at = '2026-11-02 10:18:16'

        await _put(profile_id, uid, self.LONG, 'ulid-1', 'http', at)
        await _put(profile_id, uid, ('So tell me, ' + self.LONG)[:256], 'panel:2',
                   'panel', at)

        assert len(await _seen(profile_id, uid)) == 2, 'съели чужую длинную реплику'

    def test_both_sides_cut_at_the_same_length(self):
        """⚠ Срез панели — одно число на показ и на импорт."""
        from src.dating.message_twin import (MESSAGE_TWIN_PANEL_CUT,
                                             message_twin_absent,
                                             message_twin_skip)

        assert f'LEFT(twin.text, {MESSAGE_TWIN_PANEL_CUT})' in message_twin_skip()
        assert (f'LEFT(twin.text, {MESSAGE_TWIN_PANEL_CUT})'
                in message_twin_absent('out'))


class TestTwoPanelRowsDoNotEatEachOther:
    """⚠⚠ Прячется отражение, а не «строка с бо́льшим номером».

    Первая редакция оставляла наименьший `id`, и человек, написавший одно и то же
    дважды через сайт агентства, увидел бы одну реплику вместо двух: обе строки
    панельные, своей записи у них нет.
    """

    async def test_a_human_double_send_survives(self, pair):
        profile_id, uid = pair

        await _put(profile_id, uid, 'hello', 'panel:1', 'panel',
                   '2026-09-21 12:00:00')
        await _put(profile_id, uid, 'hello', 'panel:2', 'panel',
                   '2026-09-21 12:00:01')

        assert len(await _seen(profile_id, uid)) == 2, 'съели вторую реплику человека'

    async def test_our_own_two_rows_survive(self, pair):
        """Две наши записи подряд друг друга не прячут: панельной среди них нет."""
        profile_id, uid = pair

        await _put(profile_id, uid, 'hi', 'ulid-1', 'http', '2026-09-21 12:00:00')
        await _put(profile_id, uid, 'hi', 'ulid-2', 'http', '2026-09-21 12:00:02')

        assert len(await _seen(profile_id, uid)) == 2


class TestTheSiteEatsCharacters:
    """⚠⚠⚠ Сайт съедает символы, и равенство текстов этого не переживает.

    Живой случай 03.11.2026, Step2Love, пара Anastasiya → Semen. Мы отправили
    63 знака с типографским апострофом, сайт записал 62 — апостроф пропал **у
    него**: кадр его переживает, в сыром HTML панели уже `Hows`, и ровно это
    видит собеседник. Близнец не узнался ни показом, ни вставкой, и в окне
    повисли две реплики одной отправки.

    ⚠ Это второй случай той же беды — первым были эмодзи. Там чинилось на
    входе, потому что символ **искажался**; здесь его **удаляют**, и на входе
    чинить нечего: `Hows` обратно в `How’s` не превратить.
    """

    OURS = 'Hi again! How’s your Tuesday going? What brings you here today?'
    THEIRS = 'Hi again! Hows your Tuesday going? What brings you here today?'

    async def test_the_eaten_apostrophe_is_still_a_twin(self, pair):
        profile_id, uid = pair
        at = '2026-11-03 11:52:24'

        assert len(self.OURS) == 63 and len(self.THEIRS) == 62, \
            'тексты подобраны неверно: съеден должен быть ровно один знак'

        ours = await _put(profile_id, uid, self.OURS, 'ulid-s2l', 'socket', at)
        await _put(profile_id, uid, self.THEIRS, 'panel:769777', 'panel',
                   '2026-11-03 11:52:23')

        seen = await _seen(profile_id, uid)

        assert len(seen) == 1, f'реплика двоится из-за съеденного знака: {seen}'
        assert seen[0]['id'] == ours, 'спрятали свою строку вместо отражения'
        assert seen[0]['text'] == self.OURS, 'на экране осталась порченая реплика'

    async def test_the_import_does_not_create_it(self, pair):
        """⚠ Главное — не завести двойника, а не спрятать заведённого."""
        profile_id, uid = pair

        await _put(profile_id, uid, self.OURS, 'ulid-s2l-2', 'socket',
                   '2026-11-03 11:52:24')

        got = await chat_panel_save([_row(profile_id, uid, self.THEIRS,
                                          'panel:769777',
                                          '2026-11-03 11:52:23')])

        assert got['added'] == 0, 'импорт завёл близнеца с порченым текстом'
        assert len(await _all(profile_id, uid)) == 1

    async def test_an_eaten_emoji_is_a_twin_too(self, pair):
        """⚠ Правило закрывает и прежнюю грабли: смайлик в скелет не входит."""
        profile_id, uid = pair
        at = '2026-11-03 12:00:00'

        ours = await _put(profile_id, uid, 'see you tomorrow 😉', 'ulid-emo',
                          'socket', at)
        await _put(profile_id, uid, 'see you tomorrow', 'panel:1', 'panel', at)

        seen = await _seen(profile_id, uid)

        assert len(seen) == 1 and seen[0]['id'] == ours

    async def test_two_emoji_only_replies_are_not_twins(self, pair):
        """⚠⚠ Защита обратной стороны: у текста из одних смайликов скелет пуст,
        и без проверки на пустоту он совпал бы с любым другим таким же."""
        profile_id, uid = pair
        at = '2026-11-03 12:05:00'

        await _put(profile_id, uid, '😉', 'ulid-wink', 'socket', at)
        await _put(profile_id, uid, '👍', 'panel:2', 'panel', at)

        seen = await _seen(profile_id, uid)

        assert len(seen) == 2, f'разные смайлики слились в один: {seen}'

    async def test_a_short_panel_row_is_not_a_prefix_of_ours(self, pair):
        """⚠⚠ Скелет не превращает правило в «любой префикс».

        Панельное «Ok» не должно прятаться под нашим «Ok, let us meet
        tomorrow»: панель не резала (текст короче среза), значит совпасть
        обязано целиком.
        """
        profile_id, uid = pair
        at = '2026-11-03 12:10:00'

        await _put(profile_id, uid, 'Ok, let us meet tomorrow', 'ulid-long',
                   'socket', at)
        await _put(profile_id, uid, 'Ok', 'panel:3', 'panel', at)

        seen = await _seen(profile_id, uid)

        assert len(seen) == 2, f'короткая панельная строка съедена: {seen}'

    async def test_cyrillic_survives_the_skeleton(self, pair):
        """⚠ Кириллица — буква и для MySQL: русская реплика не схлопывается.

        Проверка не теоретическая: выпади кириллица из `[:alnum:]`, скелет
        любого русского текста стал бы пустым, и две разные реплики слились бы
        в одну.
        """
        profile_id, uid = pair
        at = '2026-11-03 12:15:00'

        await _put(profile_id, uid, 'Привет, как дела?', 'ulid-ru', 'socket', at)
        await _put(profile_id, uid, 'Добрый день', 'panel:4', 'panel', at)

        seen = await _seen(profile_id, uid)

        assert len(seen) == 2, f'разные русские реплики слились: {seen}'


class TestTheValuesComeFromTheRule:
    """⚠⚠ Счёт значений живёт в правиле, а не глазами у вызывающего.

    Текст уходит в условие несколько раз, и при первой же правке условия счёт
    у вызывающего сдвинулся бы молча — запрос упал бы не на тесте, а на проде.
    """

    def test_the_count_matches(self):
        from src.dating.message_twin import (message_twin_absent,
                                             message_twin_absent_args)

        for direction in ('out', 'in'):
            sql = message_twin_absent(direction)
            args = message_twin_absent_args(1, 'u', 'текст', '2026-11-03 00:00:00')

            assert sql.count('%s') == len(args), \
                f'{direction}: условие ждёт {sql.count("%s")}, правило даёт {len(args)}'

    def test_the_import_does_not_count_them_itself(self):
        import inspect

        import src.dating.chat_panel as mod

        assert 'message_twin_absent_args' in inspect.getsource(mod), \
            'импорт снова перечисляет значения условия сам'
