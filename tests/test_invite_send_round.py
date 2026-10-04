"""Круг отправки инвайтов: кого берёт, когда молчит и чем ограничен.

⚠ **Ни одна реплика наружу не уходит.** Сама отправка (`invite_send_one`)
подменена: настоящая ушла бы живому человеку в чат. Проверяется здесь **отбор** —
кого круг берёт и почему пропускает; что отправитель делает с ответом сайта,
проверяет `tests/test_invite.py` на кнопке, а дорога у них одна.

⚠ Сайт и анкеты — стендовые (`999_343`): круг ходит по всем сайтам, и живой номер
означал бы работу по настоящим анкетам.
"""

import time
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import invite_send_round
from src.dating.invite_send_round import (INVITE_SEND_EVERY_MIN,
                                                 INVITE_SEND_ROUND_TICK_SEC,
                                                 invite_send_why,
                                                 invite_send_round_run)
from src.dating.site_setting import SITE_KNOWN

pytestmark = pytest.mark.chat

STAND_SITE = 999_343

# Пустой стенд для проверок самого отбора: ни анкет, ни пула.
BARE_SITE = 999_779

# Смена на все сутки: круг сверяет часы, а тест не про часы.
#
# ⚠ **`24:00`, а не `23:45`.** Четверть часа перед полуночью оставалась вне смены,
# и сюита краснела каждый вечер после 23:45 — в трёх соседних файлах разом. Правая
# граница строгая (`from <= minutes < to`), так что 1440 включает последнюю минуту.
ALWAYS = '[{"from": "00:00", "to": "24:00"}]'


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    """Снимает паузу между репликами: она про живой сайт, а не про отбор."""
    invite_send_round._sent_at.clear()
    monkeypatch.setattr(invite_send_round, '_every', _answer(0))

    yield

    invite_send_round._sent_at.clear()


@pytest.fixture
def site(monkeypatch):
    """⚠⚠ Сужает **отбор** круга до стенда — и не трогает флаг в базе.

    Правило общее (`testing.md`): тест, зовущий круг, подменяет его отбор, а не
    только действие. Здесь у него есть вторая, более острая причина: **этот же
    круг крутит живой воркер в соседнем контейнере**, по той же базе. Подними мы
    `invite_send_on` у строки, воркер увидел бы её раньше теста, взял его же
    черновики и попробовал отправить их по-настоящему — а его отправку никакой
    `monkeypatch` не подменяет: это другой процесс.

    Поймано флейком: `test_one_per_pass` падал через раз, потому что одну из трёх
    реплик успевал забрать воркер.

    Отбор возвращаем прямо, минуя базу: строка сайта так и остаётся `is_gone = 1`
    с опущенным флагом, и живому кругу её не видно вовсе.
    """
    async def ours():
        return [{'site_id': STAND_SITE, 'invite_schedule': ALWAYS}]

    monkeypatch.setattr(invite_send_round, '_sites', ours)

    return STAND_SITE


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда, которая **в сети**: без этого круг её не берёт."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'inv-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    await _status(profile_id, 'online')

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM invite WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


@pytest.fixture
def sent(monkeypatch):
    """Перехваченная отправка: список ушедших вместо похода на сайт.

    ⚠⚠ **Заглушка обязана помечать строку ушедшей.** Живая `invite_send_one`
    занимает её условным `UPDATE` до отправки — иначе четыре щелчка послали бы
    четыре одинаковые реплики. Заглушка, которая только запоминает вызов,
    оставляет строку черновиком, и очередь отдаёт её снова: пачка в десять
    «отправила» один и тот же инвайт десять раз. Поймано ровно так.
    """
    got = []

    async def _send(invite):
        got.append(dict(invite))

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE invite SET status = 'sent', sent_at = NOW()"
                             ' WHERE id = %s', (int(invite['id']),))
            await db.connection.commit()

        return {'ok': True, 'error': ''}

    monkeypatch.setattr(invite_send_round, 'invite_send_one', _send)

    return got


class TestPicks:
    """Кого круг берёт."""

    async def test_draft_of_an_online_girl_goes(self, site, girl, sent):
        await _draft(girl)

        got = await invite_send_round_run()

        assert got['sent'] == 1 and len(sent) == 1

    async def test_one_per_pass_by_default(self, site, girl, sent):
        """⚠ По одной за заход — **умолчание**, а не закон.

        Пул, ушедший залпом, читается как робот, поэтому умолчание осталось
        единицей. Но сколько уходит за заход, теперь решает сайт
        (`invite_send_batch`): один терпит десяток подряд, другой закроет
        учётку и на трёх, и выбирать это должен человек.
        """
        for _ in range(3):
            await _draft(girl)

        assert (await invite_send_round_run())['sent'] == 1
        assert len(sent) == 1

    async def test_the_priority_one_jumps_the_queue(self, site, girl, sent,
                                                    monkeypatch):
        """⚠ Отметка на карточке — про очередь **отправки** тоже, а не только набора.

        Попасть в пул и уйти из пула — разные сроки: при паузе в минуту приоритетная
        реплика ждала бы полсотни чужих час, а человек в чате десять минут.
        """
        old = await _draft_uid(girl, 'Старый', '2026-09-01 10:00:00')
        vip = await _draft_uid(girl, 'Важный', '2026-09-05 10:00:00')
        monkeypatch.setattr(invite_send_round, 'online_check',
                            lambda site_id, uid: {'online': True, 'known': True})

        await _priority(vip)
        try:
            await invite_send_round_run()
        finally:
            await _priority_drop(vip)

        assert [one['uid'] for one in sent] == [vip], f'ушёл {old}, а не приоритетный'

    async def test_oldest_goes_first(self, site, girl, sent):
        """Реплика сочинялась под того, кто был в чате тогда: чем дольше лежит,
        тем меньше от неё толку."""
        first = await _draft(girl, when='2026-09-01 10:00:00')
        await _draft(girl, when='2026-09-02 10:00:00')

        await invite_send_round_run()

        assert sent[0]['id'] == first


class TestSkips:
    """Кого не берёт — и это главное, потому что реплика уходит живому человеку."""

    async def test_offline_girl_writes_nothing(self, site, girl, sent):
        """Реплика уходит её сессией, а ответ читать некому, если её нет."""
        await _draft(girl)
        await _status(girl, 'offline')

        assert (await invite_send_round_run())['sent'] == 0
        assert not sent

    async def test_disabled_girl_writes_nothing(self, site, girl, sent):
        """«Не ведём» значит не ведём совсем."""
        await _draft(girl)
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET enabled = 0 WHERE id = %s', (girl,))
            await db.connection.commit()

        assert (await invite_send_round_run())['sent'] == 0

    async def test_row_with_an_error_waits_for_a_human(self, site, girl, sent):
        """⚠ Взявшись за неудачную строку снова, круг зациклился бы на отказе
        сайта («слишком часто») ровно тогда, когда тот просит остановиться."""
        await _draft(girl, error='собеседника нет в чате')

        assert (await invite_send_round_run())['sent'] == 0
        assert not sent

    async def test_outside_the_shift_nothing_goes(self, girl, sent, monkeypatch):
        """Реплика в четыре утра выдаёт робота вернее любого текста.

        ⚠ Смена считается **от текущей минуты**, а не вписана числом: тест с
        постоянными часами зеленел бы или краснел смотря по времени прогона —
        ровно тот флейк, что уже описан у почтовых тестов.
        """
        hour = (time.gmtime().tm_hour + 6) % 24
        away = '[{"from": "%02d:00", "to": "%02d:15"}]' % (hour, hour)

        async def dead_hours():
            return [{'site_id': STAND_SITE, 'invite_schedule': away}]

        monkeypatch.setattr(invite_send_round, '_sites', dead_hours)
        await _draft(girl)

        assert (await invite_send_round_run())['sent'] == 0
        assert not sent

    async def test_day_cap_stops_the_round(self, site, girl, sent, monkeypatch):
        """Потолок считается **по факту отправки**, а не по попыткам."""
        monkeypatch.setattr(invite_send_round, '_max_day', _answer(0))
        await _draft(girl)

        assert (await invite_send_round_run())['sent'] == 0
        assert not sent


class TestSelection:
    """Сам отбор сайтов — на настоящем `_sites`, без подмены.

    ⚠ Стенд здесь **свой и пустой** (`999_779`): у него нет ни пула, ни анкет, и
    живой воркер, увидев поднятый флаг, не найдёт что отправить. Проверять флаг на
    стенде с черновиками значило бы отдать их ему.
    """

    async def test_site_without_the_switch_is_not_taken(self):
        await _flag(BARE_SITE, on=False)

        assert BARE_SITE not in [int(row['site_id'])
                                 for row in await invite_send_round._sites()]

    async def test_site_with_the_switch_is_taken(self):
        """⚠ Поднятый `invite_send_on` — это и есть решение «наружу писать»."""
        try:
            await _flag(BARE_SITE, on=True)

            assert BARE_SITE in [int(row['site_id'])
                                 for row in await invite_send_round._sites()]
        finally:
            await _flag(BARE_SITE, on=False)

    async def test_gone_site_is_not_taken(self):
        """Закладку убрали у соседа — писать с неё некому."""
        try:
            await _flag(BARE_SITE, on=True, gone=True)

            assert BARE_SITE not in [int(row['site_id'])
                                     for row in await invite_send_round._sites()]
        finally:
            await _flag(BARE_SITE, on=False)


class TestPause:
    """Пауза между репликами."""

    async def test_second_pass_in_a_row_waits(self, site, girl, sent, monkeypatch):
        monkeypatch.setattr(invite_send_round, '_every', _answer(600))
        await _draft(girl)
        await _draft(girl)

        assert (await invite_send_round_run())['sent'] == 1
        assert (await invite_send_round_run())['sent'] == 0, \
            'вторая реплика ушла, не дождавшись паузы'

    async def test_the_floor_guards_a_typo_not_a_decision(self):
        """⚠ Предел сторожит `0` и `-1`, вписанные руками, а не выбор заказчика.

        Был двадцать секунд «чтобы смена не превратилась в очередь из сотен
        реплик в час». Довод верен, но частоту выбирает владелец панели: он и
        просил трёхсекундную паузу. Предел остался, чтобы ноль в базе не означал
        отправку без пауз вовсе.
        """
        assert INVITE_SEND_EVERY_MIN >= 1

    def test_the_floor_equals_the_tick(self):
        """⚠⚠ **Тик — потолок частоты.** Пауза короче тика недостижима: круг не
        проснётся вовремя, и страница обещала бы то, чего он не сделает.

        Поймано на правке «добавь три секунды»: настройка добавилась бы, а тик
        остался бы пятнадцатью — то есть три означало бы пятнадцать, и молча.
        """
        assert INVITE_SEND_EVERY_MIN == INVITE_SEND_ROUND_TICK_SEC

    async def test_every_option_of_the_page_is_reachable(self):
        """⚠ Каждый пункт списка на странице обязан быть достижимым.

        Иначе человек выбирает «3 секунды», а круг шлёт раз в пятнадцать — и
        поломкой это не выглядит, потому что реплики-то уходят.
        """
        options = SITE_KNOWN['invite_send_every']['options']
        low = min(int(value) for value, _ in options)

        assert low >= INVITE_SEND_EVERY_MIN, \
            f'на странице предлагают {low} с, а круг не пойдёт чаще ' \
            f'{INVITE_SEND_EVERY_MIN} с'


# ── Вспомогательное ──────────────────────────────────────────────────────────

def _answer(value):
    """Подменённая настройка: всегда одно и то же число.

    ⚠ Принимает **любые** доводы: у подменяемых разные подписи — `_max_day(site_id)`,
    а `_today(site_id, offset_hours)`. Заглушка с одним параметром роняла бы вторую
    `TypeError`, и та, проглоченная защитным `except`, выглядела бы как «повода нет».
    """
    async def _got(*args, **kwargs):
        return value

    return _got


async def _flag(site_id: int, on: bool, gone: bool = False) -> None:
    """Выключатель отправки у стендового сайта."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO site (site_id, name, invite_send_on, invite_schedule, is_gone)'
            ' VALUES (%s, %s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE invite_send_on = VALUES(invite_send_on),'
            '                         is_gone = VALUES(is_gone)',
            (int(site_id), 'Стенд DatingAi: отбор', 1 if on else 0, ALWAYS,
             1 if (gone or not on) else 0))
        await db.connection.commit()


async def _status(profile_id: int, status: str) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET status = %s WHERE id = %s',
                         (status, int(profile_id)))
        await db.connection.commit()


async def _draft(profile_id: int, error: str = '', when: str = '') -> int:
    """Реплика в пуле. Возвращает её номер."""
    async with mysql_get_db_async() as db:
        if when:
            await db.execute(
                'INSERT INTO invite (site_id, profile_id, uid, name, body,'
                " status, error, created_at) VALUES (%s, %s, %s, %s, %s, 'draft', %s, %s)",
                (STAND_SITE, int(profile_id), f'u{uuid.uuid4().hex[:8]}', 'Frank',
                 'Привет!', error, when))
        else:
            await db.execute(
                'INSERT INTO invite (site_id, profile_id, uid, name, body,'
                " status, error) VALUES (%s, %s, %s, %s, %s, 'draft', %s)",
                (STAND_SITE, int(profile_id), f'u{uuid.uuid4().hex[:8]}', 'Frank',
                 'Привет!', error))
        made = db.lastrowid
        await db.connection.commit()

    return int(made)


class TestFeed:
    """Лента событий: «От → кому» видно с любой страницы раздела.

    ⚠ Журнал анкеты этого не заменяет: его открывают у конкретной девушки и
    постфактум, а вопрос «панель сейчас пишет живым людям?» задают о сайте
    целиком и в тот же момент.
    """

    async def test_sent_invite_shows_who_wrote_whom(self, girl, monkeypatch):
        from src.dating import invite_send
        from src.dating.feed import feed_read

        said = []
        monkeypatch.setattr(invite_send, 'feed_write',
                            lambda site_id, kind, text, profile_id=0: said.append(
                                (site_id, kind, text)) or '')
        monkeypatch.setattr(invite_send, 'send_message',
                            _ok_send())
        monkeypatch.setattr(invite_send, 'site_presence', _site())
        await _name(girl, 'Diana')
        invite_id = await _draft(girl)

        got = await invite_send.invite_send_one(
            await _invite_row(invite_id))

        assert got['ok']
        assert said, 'отправка прошла молча — в ленте ничего'
        site_id, kind, text = said[0]
        assert kind == 'invite' and site_id == STAND_SITE
        assert text == 'Инвайт: Diana → Frank', text

    async def test_girl_without_a_name_shows_her_login(self, girl, monkeypatch):
        """Пустая левая половина («Инвайт:  → Frank») читалась бы как сбой."""
        from src.dating import invite_send

        said = []
        monkeypatch.setattr(invite_send, 'feed_write',
                            lambda site_id, kind, text, profile_id=0: said.append(text) or '')
        monkeypatch.setattr(invite_send, 'send_message', _ok_send())
        monkeypatch.setattr(invite_send, 'site_presence', _site())
        invite_id = await _draft(girl)

        await invite_send.invite_send_one(await _invite_row(invite_id))

        assert said and said[0].startswith('Инвайт: inv-'), said

    async def test_failure_is_a_warning_in_the_feed(self, girl, monkeypatch):
        """«Пул полон, а ничего не уходит» иначе выглядит как вставший круг."""
        from src.dating import invite_send

        said = []
        monkeypatch.setattr(invite_send, 'feed_write',
                            lambda site_id, kind, text, profile_id=0: said.append(
                                (kind, text)) or '')
        monkeypatch.setattr(invite_send, 'site_presence', _site())

        # ⚠ Отказ **стойкий**, а не «его нет в сети»: уход из чата состояние, оно
        # проходит само, в ленту не пишется и строку не глушит (см. `TestOffline…`).
        async def refused(profile, presence, uid, body, to_history=True):
            return {'ok': False, 'error': 'слишком часто'}

        monkeypatch.setattr(invite_send, 'send_message', refused)
        invite_id = await _draft(girl)

        got = await invite_send.invite_send_one(await _invite_row(invite_id))

        assert not got['ok']
        kind, text = said[0]
        assert kind == 'warn' and 'слишком часто' in text, text


def _ok_send():
    async def _send(profile, presence, uid, body, to_history=True):
        return {'ok': True}

    return _send


def _site():
    """Описание сайта — то, что отдаёт `site_presence` из нашей базы.

    ⚠ Пустое описание здесь законно: транспорт стенду задаёт настройка сайта, а
    не описание. Прежде заглушка изображала карточку соседа (`{'presence': '{}'}`)
    — за ней ходили ручкой, пока описание не переехало к нам.
    """

    async def _got(site_id):
        return {}

    return _got


async def _name(profile_id: int, name: str) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                         ('{"name": "%s"}' % name, int(profile_id)))
        await db.connection.commit()


async def _invite_row(invite_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, uid, name, body'
                         ' FROM invite WHERE id = %s', (int(invite_id),))
        row = await db.fetchone()

    return dict(row)


class TestOfflineIsNotAFailure:
    """«Его нет в сети» не глушит черновик и не тратит заход впустую.

    ⚠ Пул набирается раз в полчаса, а люди на сайте живут минутами: через час
    половина адресатов ушла. Поймано в первый же час работы круга — пул на 50
    черновиков, в сети из них 27, и круг пошёл заглушать остальные по одной в
    минуту причиной, которая проходит сама.
    """

    async def test_away_leaves_the_row_clean(self, girl, monkeypatch):
        from src.dating import invite_send

        async def away(profile, presence, uid, body, to_history=True):
            return {'ok': False, 'offline': True}

        monkeypatch.setattr(invite_send, 'send_message', away)
        monkeypatch.setattr(invite_send, 'site_presence',
                            _site_answer())
        invite_id = await _draft(girl)

        got = await invite_send.invite_send_one(await _one(invite_id))
        row = await _one(invite_id)

        assert not got['ok'] and 'нет в сети' in got['error']
        assert row['status'] == 'draft'
        assert row['error'] == '', '«нет в сети» записали как отказ — строка заглушена'

    async def test_a_real_refusal_still_stops_it(self, girl, monkeypatch):
        """⚠ Обратное правило остаётся: стойкий отказ строку держит, иначе круг
        зациклится ровно тогда, когда сайт просит остановиться."""
        from src.dating import invite_send

        async def refused(profile, presence, uid, body, to_history=True):
            return {'ok': False, 'error': 'слишком часто'}

        monkeypatch.setattr(invite_send, 'send_message', refused)
        monkeypatch.setattr(invite_send, 'site_presence',
                            _site_answer())
        invite_id = await _draft(girl)

        await invite_send.invite_send_one(await _one(invite_id))
        row = await _one(invite_id)

        assert row['status'] == 'draft' and row['error'] == 'слишком часто'

    async def test_round_skips_those_who_left(self, site, girl, sent, monkeypatch):
        """Круг берёт того, кто в ленте **сейчас**, а не самого старого подряд."""
        from src.dating import invite_send_round as round_

        old_uid = await _draft_uid(girl, 'ушёл', when='2026-09-01 10:00:00')
        new_uid = await _draft_uid(girl, 'на месте', when='2026-09-02 10:00:00')

        monkeypatch.setattr(round_, 'online_check',
                            lambda site_id, uid: {'online': str(uid) == new_uid,
                                                  'known': True, 'age_sec': 1})

        got = await round_._next(STAND_SITE)

        assert got and got['uid'] == new_uid, f'взяли ушедшего: {got}'
        assert old_uid != new_uid


def _site_answer():
    async def _got(site_id):
        return {'id': site_id, 'presence': '{}'}

    return _got


async def _one(invite_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, site_id, profile_id, uid, name, body, status,'
                         ' error FROM invite WHERE id = %s', (int(invite_id),))

        return dict(await db.fetchone())


class TestWhyItStands:
    """⚠⚠ Молчащий пул обязан объяснять себя.

    Поймано живьём и стоило человеку часов: «пул инвайтов 50 из 50», девять анкет
    в сети, смена идёт — и ничего не уходит. Причина была законной (выбрана
    дневная норма), но лежала в журнале воркера, а человек смотрел на страницу.

    ⚠ Ответ считается **теми же вопросами, что решает круг**, поэтому проверяется
    каждая развилка порознь: слитая проверка «строка непустая» зеленела бы и на
    неверном объяснении.
    """

    async def test_the_switch_off_says_so(self):
        """Отбор круга стенд не включает — значит выключатель снят."""
        assert await invite_send_why(STAND_SITE, 0) \
            == 'отправка инвайтов выключена'

    async def test_outside_the_shift_says_so(self, monkeypatch):
        hour = (time.gmtime().tm_hour + 6) % 24
        away = '[{"from": "%02d:00", "to": "%02d:15"}]' % (hour, hour)

        async def dead_hours():
            return [{'site_id': STAND_SITE, 'invite_schedule': away}]

        monkeypatch.setattr(invite_send_round, '_sites', dead_hours)

        assert 'не смена' in await invite_send_why(STAND_SITE, 0)

    async def test_the_day_cap_says_how_much(self, site, monkeypatch):
        """⚠ Числа в строке — те же, по которым круг и остановился."""
        monkeypatch.setattr(invite_send_round, '_max_day', _answer(7))
        monkeypatch.setattr(invite_send_round, '_today', _answer(7))

        said = await invite_send_why(STAND_SITE, 0)

        assert 'норма выбрана' in said and 'ушло 7 из 7' in said

    async def test_an_empty_pool_says_so(self, site, girl):
        assert await invite_send_why(STAND_SITE, 0) \
            == 'пул пуст: отправлять нечего'

    async def test_a_sleeping_girl_says_so(self, site, girl):
        await _draft(girl)
        await _status(girl, 'offline')

        assert 'не в сети' in await invite_send_why(STAND_SITE, 0)

    async def test_drafts_with_errors_say_so(self, site, girl, monkeypatch):
        await _draft(girl, error='слишком часто')
        monkeypatch.setattr(invite_send_round, 'online_check',
                            lambda site_id, uid: {'online': True, 'known': True})

        assert 'с ошибкой' in await invite_send_why(STAND_SITE, 0)

    async def test_nobody_online_says_so(self, site, girl, monkeypatch):
        await _draft(girl)
        monkeypatch.setattr(invite_send_round, 'online_check',
                            lambda site_id, uid: {'online': False, 'known': True})

        assert 'нет сейчас в сети' in await invite_send_why(STAND_SITE, 0)

    async def test_silence_when_all_is_well(self, site, girl, monkeypatch):
        """⚠ Пустая строка — не «нет данных», а «идёт»: её страница не показывает."""
        await _draft(girl)
        monkeypatch.setattr(invite_send_round, 'online_check',
                            lambda site_id, uid: {'online': True, 'known': True})

        assert await invite_send_why(STAND_SITE, 0) == ''


class TestTheDayIsLocal:
    """⚠⚠ Сутки дневной нормы — **местные**, а не серверные.

    Поймано на живом проде и выглядело как поломка панели. База живёт по UTC, смены
    и часы панели — по местному времени (+3). Ночная смена 00:00–05:00 перешла
    границу суток UTC, за час выбрала все сто реплик «нового дня» — и отправка
    встала до следующей полуночи UTC. В пуле при этом лежало полсотни готовых, а
    девять анкет были в сети.
    """

    async def test_a_send_before_local_midnight_is_yesterday(self, girl):
        """Реплика двухчасовой давности при местном часе 01:00 — вчерашняя.

        ⚠ Смещение считается **от текущего часа**, а не вписано числом: тест с
        постоянными часами зеленел бы или краснел смотря по времени прогона.
        """
        before = await invite_send_round._today(STAND_SITE, _offset_to(1))
        await _sent_hours_ago(girl, 2)

        assert await invite_send_round._today(STAND_SITE, _offset_to(1)) == before

    async def test_the_same_send_counts_when_the_local_day_covers_it(self, girl):
        """⚠ Та же самая реплика при местном часе 12:00 — сегодняшняя.

        Две проверки на одной строке и есть доказательство: меняется только
        часовой пояс, а ответ — на противоположный.
        """
        before = await invite_send_round._today(STAND_SITE, _offset_to(12))
        await _sent_hours_ago(girl, 2)

        assert await invite_send_round._today(STAND_SITE,
                                                     _offset_to(12)) == before + 1

    async def test_the_offset_reaches_the_counter(self):
        """Смещение доезжает из круга до счёта суток — иначе всё выше напрасно.

        ⚠ **Путь стал длиннее на звено, и это не ослабление.** Прежде круг сам
        спрашивал `_today(site_id, offset_hours)`; теперь он спрашивает «почему
        стоим» (`invite_send_why`), а сутки считает уже тот — тем же
        вызовом и тем же смещением.

        Сделано ради журнала простоя: причина, по которой круг не пошёл, должна
        быть **одна** и для страницы, и для записи, и для самого решения. Держи
        круг свою копию правил — она разошлась бы с ответом странице на первой
        же правке, и человек читал бы «уходит» там, где не уходит.
        """
        import inspect

        run = inspect.getsource(invite_send_round.invite_send_round_run)
        why = inspect.getsource(invite_send_round.invite_send_why)

        assert 'invite_send_why(site_id, offset_hours)' in run, \
            'круг решает сам, мимо общей причины — правила разойдутся'
        assert '_today(site_id, offset_hours)' in why, \
            'причина снова считает сутки без часового пояса'


class TestReasonDoesNotRepeatTheAnswer:
    """⚠ Ответ сайта в карточке — **один раз**, а не дважды.

    `send` собирает причину как «сайт ответил 200: <тело>», а тело мы
    храним отдельно и показываем своей строкой. Выходило так:

        сайт ответил 200: {"IsSuccess":false,"Reason":"ERROR_MESSAGE_USER_DENY"…}
        {"IsSuccess":false,"Reason":"ERROR_MESSAGE_USER_DENY"…}
    """

    def test_the_body_is_cut_out_of_the_reason(self):
        from src.dating.invite_send import _why_short

        body = '{"IsSuccess":false,"Reason":"ERROR_MESSAGE_USER_DENY"}'

        assert _why_short(f'сайт ответил 200: {body}', body) == 'сайт ответил 200'

    def test_a_reason_without_the_body_is_untouched(self):
        from src.dating.invite_send import _why_short

        assert _why_short('слишком часто', '') == 'слишком часто'
        assert _why_short('слишком часто', '{"ok":1}') == 'слишком часто'

    def test_a_long_answer_is_still_found(self):
        """⚠ В причину тело уезжает обрезанным (200 знаков), а хранится целиком."""
        from src.dating.invite_send import _why_short

        body = '{"Message":"' + 'x' * 400 + '"}'

        assert _why_short(f'сайт ответил 200: {body[:200]}', body) == 'сайт ответил 200'

    def test_nothing_left_means_keep_the_original(self):
        """Молчащая строка отказа хуже повторяющейся."""
        from src.dating.invite_send import _why_short

        body = '{"IsSuccess":false}'

        assert _why_short(body, body) == body


def _offset_to(hour: int) -> int:
    """Смещение, при котором местный час равен заданному. От «сейчас», а не числом."""
    import time

    return (int(hour) - time.gmtime().tm_hour) % 24


async def _sent_hours_ago(profile_id: int, hours: int) -> None:
    """Ушедшая реплика столько-то часов назад — счёт суток проверяется ею."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO invite (site_id, profile_id, uid, name, body, status,'
            ' sent_at) VALUES (%s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s HOUR)',
            (STAND_SITE, int(profile_id), f'u{uuid.uuid4().hex[:8]}', 'Frank',
             'Привет!', 'sent', int(hours)))
        await db.connection.commit()


async def _priority(uid: str) -> None:
    """Карточка собеседника с поднятой отметкой «Приоритет в рассылке»."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO companion (site_id, uid, name, priority)'
            " VALUES (%s, %s, 'Важный', 1)", (STAND_SITE, uid))
        await db.connection.commit()


async def _priority_drop(uid: str) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM companion WHERE site_id = %s AND uid = %s',
                         (STAND_SITE, uid))
        await db.connection.commit()


async def _draft_uid(profile_id: int, tag: str, when: str) -> str:
    uid = f'u{uuid.uuid4().hex[:8]}'
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO invite (site_id, profile_id, uid, name, body,'
            " status, error, created_at) VALUES (%s, %s, %s, %s, %s, 'draft', '', %s)",
            (STAND_SITE, int(profile_id), uid, tag, 'Привет!', when))
        await db.connection.commit()

    return uid


class TestBatch:
    """⚠⚠ Сколько реплик уходит за один заход круга.

    Прежде — ровно одна, и полсотни инвайтов растягивались на полсотни пауз: при
    паузе в минуту это почти час на пул.

    ⚠ **Пачка не ускоряет круг, а укрупняет заход.** Пауза (`invite_send_every`)
    осталась и держит промежутки **между** заходами; внутри пачки реплики уходят
    подряд, без паузы — сайт видит их одним всплеском. За этим и настройка, а не
    константа: один сайт терпит десяток, другой закроет учётку и на трёх.
    """

    @pytest.fixture
    async def batch(self, monkeypatch):
        """Задаёт размер пачки, минуя базу: настройки сайта делит вся сюита.

        ⚠ **И чистит пул стенда перед заходом.** Пачка берёт из пула столько,
        сколько просит, — и черновики, оставленные соседними тестами, уезжают
        вместе с её собственными. Проверяя «ушло ровно два», такой тест ловил бы
        не код, а порядок запуска: у меня он показал «ушло 10».
        """
        async with mysql_get_db_async() as db:
            await db.execute("DELETE FROM invite WHERE site_id = %s AND status = 'draft'",
                             (STAND_SITE,))
            await db.connection.commit()

        def _set(value):
            async def _batch(site_id):
                return int(value)

            monkeypatch.setattr(invite_send_round, '_batch', _batch)

        return _set

    async def test_three_go_at_once(self, site, girl, sent, batch):
        batch(3)
        for _ in range(5):
            await _draft(girl)

        got = await invite_send_round_run()

        assert got['sent'] == 3, f"ушло {got['sent']}, а просили три"
        assert len(sent) == 3

    async def test_a_short_pool_sends_what_it_has(self, site, girl, sent, batch):
        """Пачка больше пула — уходит пул, и это не отказ."""
        batch(10)
        await _draft(girl)
        await _draft(girl)

        assert (await invite_send_round_run())['sent'] == 2

    async def test_the_daily_cap_wins(self, site, girl, sent, batch, monkeypatch):
        """⚠⚠ Пачка не перескакивает дневной потолок.

        До правки за заход уходила одна реплика, и проверки нормы перед заходом
        было довольно. Пачка шлёт несколько — а норма считается **до** захода:
        без оглядки на остаток пачка в десять при остатке две отправила бы
        десять, то есть восемь сверх нормы, которую поставил человек.
        """
        batch(10)
        for _ in range(6):
            await _draft(girl)

        async def _today(site_id, offset_hours=0):
            return 98

        async def _cap(site_id):
            return 100

        monkeypatch.setattr(invite_send_round, '_today', _today)
        monkeypatch.setattr(invite_send_round, '_max_day', _cap)

        got = await invite_send_round_run()

        assert got['sent'] == 2, f"ушло {got['sent']}, а до нормы было две"

    async def test_a_failure_stops_the_batch(self, site, girl, monkeypatch, batch):
        """⚠ Неудача обрывает пачку, а не идёт дальше.

        Причина почти всегда общая для всех реплик захода — сайт отказал, сессия
        протухла. Дослать остальные значило бы получить те же отказы, кормя
        счётчик сайта. Круг вернётся через пятнадцать секунд.
        """
        batch(5)
        for _ in range(5):
            await _draft(girl)

        tried = []

        async def _send(invite):
            tried.append(dict(invite))

            return {'ok': False, 'error': 'сайт отказал'}

        monkeypatch.setattr(invite_send_round, 'invite_send_one', _send)

        got = await invite_send_round_run()

        assert got['failed'] == 1
        assert len(tried) == 1, f'после отказа ушло ещё {len(tried) - 1}'

    async def test_zero_is_not_a_switch(self, site, girl, sent, monkeypatch):
        """⚠ Ноль в настройке даёт единицу, а не «ничего».

        «Не отправлять» — отдельный выключатель (`invite_send_on`); отменять
        отправку размером пачки значило бы два выключателя об одном, и человек
        искал бы, который из них главнее.
        """
        async def _none(site_id, key):
            return 0

        monkeypatch.setattr('src.dating.invite_send_round.site_seconds', _none)
        await _draft(girl)

        assert (await invite_send_round_run())['sent'] == 1

    async def test_the_setting_is_registered(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        one = known['invite_send_batch']

        assert one['default'] == '1'
        # ⚠ Не набор, а число: пачку задаёт тот, кто ведёт сайт, а подобранная
        # нами четвёрка значений однажды оказалась бы уже настоящей нужды.
        assert one['kind'] == 'number'
        assert not one.get('options')
        assert one['min'] == 1, 'нулевая пачка — это выключенная отправка втихую'
        # ⚠ Ключ в ответе есть всегда — смотрим на **значение**: потолка нет.
        # Сколько реплик подряд терпит сайт, знает тот, кто его ведёт, а не код.
        assert one.get('max') is None, 'потолок вернулся'

    async def test_it_lives_with_the_pause(self, auth_client):
        """Пачка и пауза — про один и тот же заход, и стоят рядом в форме."""
        got = (await auth_client.get('/api/dating/site')).json()['result']
        group = {row['key']: row['group'] for row in got['known']}

        assert group['invite_send_batch'] == group['invite_send_every']
