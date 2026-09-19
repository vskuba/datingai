"""Круг инициативы: кого берём, чем пишем и когда отправляем.

## Зачем занятие

Разговор кончается не словом «пока», а молчанием: мы ответили, он не ответил.
Автоответ такую пару не видит — он отвечает на входящее, а входящего нет.

## ⚠⚠ Приём выбирает панель, а не модель

Соблазн написать агенту «будь разной» уже проверен на инвайтах: три шаблона из
трёх начинались с «you're trouble». Модель держит один тон и возвращается к нему
всегда. Поэтому приём приходит готовым — жребием по весам, — а шаг у соседа
получает одну задачу.

⚠ **Наружу сюита не ходит** (`testing.md`): агент и отправка подменяются.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import initiative as initiative_mod
from src.dating import initiative_round as round_mod
from src.dating.chat_initiative import chat_initiative_put, chat_initiative_waiting

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'init-round-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                         (profile_id,))
        await db.connection.commit()

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM chat_initiative WHERE profile_id = %s',
                         (profile_id,))
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _said(profile_id: int, uid: str, direction: str = 'out',
                minutes_ago: int = 0) -> int:
    """Реплика в переписке.

    ⚠ Время считает **база** (`NOW() - INTERVAL`): MySQL живёт по UTC, и
    `datetime.now()` положил бы строку в будущее.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, created_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
            (int(profile_id), uid, 'Brian', 'текст',
             f'zz-{uuid.uuid4().hex[:12]}', direction, int(minutes_ago)))
        await db.connection.commit()

        return int(db.lastrowid)


class TestWhoIsQuiet:
    """⚠⚠ Что считается затихшим разговором."""

    async def test_our_last_word_and_long_ago(self, girl):
        """Мы ответили, он молчит дольше срока — берём."""
        await _said(girl, 'u-1', direction='in', minutes_ago=200)
        await _said(girl, 'u-1', direction='out', minutes_ago=180)

        got = await initiative_mod._quiet(girl, 3600, 2)

        assert [one['uid'] for one in got] == ['u-1']

    async def test_his_last_word_is_not_silence(self, girl):
        """⚠⚠ Последним говорил он — это **неотвеченное**, а не тишина.

        Там работает автоответ, и лезть туда инициативе нельзя: вышли бы два наших
        сообщения подряд на одну его реплику.
        """
        await _said(girl, 'u-1', direction='out', minutes_ago=200)
        await _said(girl, 'u-1', direction='in', minutes_ago=180)

        assert await initiative_mod._quiet(girl, 3600, 2) == []

    async def test_fresh_silence_waits(self, girl):
        """Срок не вышел — ждём: час тишины это ещё не конец разговора."""
        await _said(girl, 'u-1', direction='in', minutes_ago=30)
        await _said(girl, 'u-1', direction='out', minutes_ago=20)

        assert await initiative_mod._quiet(girl, 3600, 2) == []

    async def test_a_silent_invite_is_not_ours(self, girl):
        """⚠ Он не сказал ни слова — это неотвеченный инвайт.

        Возвращаться к нему — работа инвайтов (`invite_repeat_after`), и делать это
        вдвоём значило бы звать человека дважды из двух мест.
        """
        await _said(girl, 'u-1', direction='out', minutes_ago=300)

        assert await initiative_mod._quiet(girl, 3600, 2) == []

    async def test_the_repeat_limit_stops_it(self, girl):
        """⚠⚠ Молчание — тоже ответ.

        Три подряд «ты тут?» выдают автомат надёжнее любого текста, поэтому предел
        заходов важнее срока.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _said(girl, 'u-1', direction='out', minutes_ago=300)

        async with mysql_get_db_async() as db:
            for n in (1, 2):
                await db.execute(
                    'INSERT INTO chat_initiative (site_id, profile_id, uid, name,'
                    "  body, try_no, status, sent_at)"
                    " VALUES (%s, %s, 'u-1', 'Brian', 'заход', %s, 'sent',"
                    '  NOW() - INTERVAL %s MINUTE)',
                    (STAND_SITE, girl, n, 200 - n))
            await db.connection.commit()

        assert await initiative_mod._quiet(girl, 3600, 2) == [], \
            'третий заход при пределе два'

    async def test_his_answer_resets_the_count(self, girl):
        """⚠⚠ Заговорил — счёт начинается заново.

        Считаем ушедшие карточки **после его последней реплики**: ответил — граница
        сдвинулась, и прежние заходы больше не в счёт.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO chat_initiative (site_id, profile_id, uid, name, body,'
                " try_no, status, sent_at) VALUES (%s, %s, 'u-1', 'Brian', 'заход',"
                " 1, 'sent', NOW() - INTERVAL 350 MINUTE)", (STAND_SITE, girl))
            await db.connection.commit()

        # Он ответил после того захода, а потом снова замолчал.
        await _said(girl, 'u-1', direction='in', minutes_ago=300)
        await _said(girl, 'u-1', direction='out', minutes_ago=250)

        got = await initiative_mod._quiet(girl, 3600, 1)

        assert [one['try_no'] for one in got] == [1], 'счёт не обнулился'


class TestTheKindIsDrawn:
    """⚠⚠ Приём выбирает панель."""

    def test_a_kind_is_a_task(self):
        """⚠ Формулировка — задача агенту, а не тема: «пошути» он исполнит как
        угодно, узкая задача — одним способом."""
        got = initiative_mod.initiative_kind()

        assert got in [one for one, _ in initiative_mod.INITIATIVE_KINDS]
        assert len(got) > 30, 'приём выродился в одно слово'

    def test_the_draw_is_varied(self):
        """Сто жребиев — не один и тот же приём."""
        got = {initiative_mod.initiative_kind() for _ in range(100)}

        assert len(got) >= 6, f'жребий вырождается: {len(got)} приёмов из ста'

    def test_the_sharp_ones_are_rare(self):
        """⚠ Чёрный юмор хорош раз в десяток реплик и ужасен каждую вторую."""
        веса = dict(initiative_mod.INITIATIVE_KINDS)
        острое = [вес for приём, вес in веса.items()
                  if 'мрачно' in приём or 'глупость' in приём]
        лёгкое = [вес for приём, вес in веса.items() if 'историю из своего дня' in приём]

        assert острое and лёгкое
        assert max(острое) < min(лёгкое), 'острое выпадает не реже безобидного'


class TestItReachesTheAgent:
    """Переменные вызова: инициатива и приём."""

    async def test_the_variables_are_sent(self, monkeypatch, girl):
        from src.dating import answer as answer_mod

        got = {}

        async def _chat_ask(agent_id, question, companion_id=0, variables=None):
            got.update(variables or {})

            return 'ответ'

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_ask', _chat_ask)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile,
                                    initiative='пофлиртуй коротко')

        assert got['make_initiative'] == '1'
        assert got['initiative_kind'] == 'пофлиртуй коротко'

    async def test_without_initiative_the_flag_is_zero(self, monkeypatch, girl):
        """⚠ Строкой `'0'`, а не `False`: у соседа `%if%` ложно для `'0'`, а
        `'False'` было бы истиной."""
        from src.dating import answer as answer_mod

        got = {}

        async def _chat_ask(agent_id, question, companion_id=0, variables=None):
            got.update(variables or {})

            return 'ответ'

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_ask', _chat_ask)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile)

        assert got['make_initiative'] == '0'
        assert got['initiative_kind'] == ''


class TestTheTwoSwitches:
    """⚠⚠ Флага два: набор и отправка."""

    async def test_both_are_off_by_default(self, auth_client):
        got = (await auth_client.get(
            f'/api/dating/site/{STAND_SITE}/chat-initiative')).json()['result']

        assert got['chat_initiative_on'] is False
        assert got['chat_initiative_send_on'] is False

    async def test_they_switch_apart(self, auth_client):
        """Набор можно поднять, не поднимая отправку, — это и есть рабочий порядок."""
        try:
            await auth_client.put(
                f'/api/dating/site/{STAND_SITE}/chat-initiative-on',
                json={'chat_initiative_on': True})

            got = (await auth_client.get(
                f'/api/dating/site/{STAND_SITE}/chat-initiative')).json()['result']

            assert got['chat_initiative_on'] is True
            assert got['chat_initiative_send_on'] is False, \
                'набор потянул за собой отправку — пул некому прочитать'
        finally:
            await auth_client.put(
                f'/api/dating/site/{STAND_SITE}/chat-initiative-on',
                json={'chat_initiative_on': False})

    async def test_a_silent_site_is_skipped(self, monkeypatch):
        """Оба флага сняты — круг про сайт не вспоминает."""
        got = await round_mod.initiative_round_run()

        assert got['made'] == 0 and got['sent'] == 0


class TestStaleCards:
    """⚠⚠ Он ответил — карточка стала вредна."""

    async def test_an_answered_pair_loses_its_card(self, girl):
        """Текст сочинялся под тишину: уйдя после его реплики, он прозвучит мимо
        разговора — он спросил о другом, а девушка будто не прочитала."""
        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'как выходные?')
        # Он ответил уже после того, как карточка легла.
        await _said(girl, 'u-1', direction='in', minutes_ago=0)

        gone = await round_mod._stale_clean(STAND_SITE)

        assert gone >= 1
        assert await chat_initiative_waiting(girl, 'u-1') == {}

    async def test_a_silent_pair_keeps_its_card(self, girl):
        """Молчит — карточка ждёт своего часа."""
        await _said(girl, 'u-1', direction='in', minutes_ago=300)
        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'как выходные?')

        await round_mod._stale_clean(STAND_SITE)

        assert await chat_initiative_waiting(girl, 'u-1'), 'карточку сняли зря'


class TestOfflineGirlLosesHerCards:
    """⚠⚠ Девушка ушла из сети — её карточки уходят из пула.

    То же правило, что у рассылки, инвайтов и приставаний (`pool.py`): черновик от
    анкеты не в сети отправить некому, а место в пуле он занимает.

    ⚠ Инициативе это нужнее прочих: её тексты привязаны ко времени сильнее всего —
    «расскажи про свой день», «спроси про планы на вечер», «у нас третий день
    дождь». Пролежав до завтра, такая карточка врёт не тоном, а содержанием.
    """

    def test_the_pool_is_in_the_cleaner_list(self):
        from src.dating.pool import POOL_TABLES

        assert 'chat_initiative' in POOL_TABLES, \
            'карточки инициативы переживут уход анкеты'

    def test_chat_reply_is_not(self):
        """⚠ А черновик ответа — нет, и это не забывчивость.

        Он отвечает **конкретной реплике** и ждёт собеседника, а не свежести:
        вопрос, заданный вчера, ответа не теряет.
        """
        from src.dating.pool import POOL_TABLES

        assert 'chat_reply' not in POOL_TABLES

    async def test_leaving_drops_the_card(self, girl):
        """Уход по концу смены (`pool_drop`) уносит карточку."""
        from src.dating.pool import pool_drop

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'как выходные?')
        got = await pool_drop(girl, 'смена кончилась')

        assert got.get('chat_initiative') == 1
        assert await chat_initiative_waiting(girl, 'u-1') == {}

    async def test_the_sweeper_catches_the_rest(self, girl):
        """⚠ Страховка на выходы, о которых мы не узнали: сайт выбросил сессию,
        вход не признали, статус сбился в `error`."""
        from src.dating.pool import pool_sweep

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'как выходные?')

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'error' WHERE id = %s",
                             (girl,))
            await db.connection.commit()

        await pool_sweep(STAND_SITE)

        assert await chat_initiative_waiting(girl, 'u-1') == {}

    async def test_sent_cards_survive(self, girl):
        """⚠ Ушедшее — история: по ней считаются заходы. Сотри мы её, вернувшаяся
        анкета начала бы счёт заново и написала бы третий раз при пределе два."""
        from src.dating.pool import pool_drop

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO chat_initiative (site_id, profile_id, uid, name, body,'
                " try_no, status, sent_at) VALUES (%s, %s, 'u-1', 'Brian', 'ушло',"
                " 1, 'sent', NOW())", (STAND_SITE, girl))
            await db.connection.commit()

        await pool_drop(girl, 'смена кончилась')

        async with mysql_get_db_async() as db:
            await db.execute("SELECT COUNT(*) AS n FROM chat_initiative"
                             " WHERE profile_id = %s AND status = 'sent'", (girl,))
            row = await db.fetchone()

        assert int(row['n']) == 1, 'чистка унесла историю заходов'


class TestBothMustBeOnline:
    """⚠⚠ Отправка идёт, только когда в сети **оба**."""

    async def test_a_sleeping_girl_sends_nothing(self, girl):
        """Девушка не в сети — карточку даже не берут в работу.

        Проверка стоит **в запросе** (`chat_initiative_ready`), а не после: иначе
        круг набирал бы карточки спящих и каждый раз откладывал их, не доходя до
        тех, что можно отправить.
        """
        from src.dating.chat_initiative import chat_initiative_ready

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'текст')

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'offline' WHERE id = %s",
                             (girl,))
            await db.connection.commit()

        готовые = await chat_initiative_ready(STAND_SITE)

        assert not [one for one in готовые if int(one['profile_id']) == girl]

    async def test_a_sleeping_companion_keeps_the_card(self, monkeypatch, girl):
        """⚠ Собеседник не в сети — карточка **ждёт**, а не гибнет.

        Он вернётся, и она уйдёт: ровно как черновик ответа. Пометки о неудаче
        тоже не ставим — неудачи не было.
        """
        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'текст')
        monkeypatch.setattr(round_mod, 'online_check',
                            lambda site_id, uid: {'online': False, 'known': True})

        sent = await round_mod._send(STAND_SITE)

        assert sent == 0
        assert await chat_initiative_waiting(girl, 'u-1'), 'карточку потеряли'

    async def test_both_online_sends(self, monkeypatch, girl):
        """Оба в сети — реплика уходит."""
        ушло = []

        async def _send(profile, presence, uid, text):
            ушло.append(text)

            return {'ok': True, 'error': ''}

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'текст')
        monkeypatch.setattr(round_mod, 'online_check',
                            lambda site_id, uid: {'online': True, 'known': True})
        monkeypatch.setattr(round_mod, 'send_message', _send)

        sent = await round_mod._send(STAND_SITE)

        assert sent == 1 and ушло == ['текст']
        assert await chat_initiative_waiting(girl, 'u-1') == {}, 'карточка не ушла'
